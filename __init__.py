"""skmemory memory plugin: a Hermes MemoryProvider that talks to skmemory over MCP.

skmemory stays OUTSIDE Hermes. This plugin never imports it: it starts the
``skmemory-mcp`` stdio server as a child process (one per provider instance, so
one per Hermes profile) and calls its tools over JSON-RPC. That keeps the Hermes
venv free of skmemory's dependencies, keeps memory access under skmemory's own
reader rules, and lets the same plugin serve two setups:

* **isolated** (default, the "sidekick" setup): a private store under
  ``$HERMES_HOME/.skmemory`` on skmemory's local tier (flat JSON files plus
  SQLite/FTS5). No Postgres, no embedding server, works offline.
* **linked** (the "enterprise" setup): point ``skcapstone_home`` + ``agent`` at an
  existing skcapstone agent, and the child serves that store with whatever tiers
  the node has (pgvector, BM25, graph).

Install skmemory where Hermes can run it, not into Hermes's venv, at the tested
version (``TESTED_SKMEMORY``)::

    uv tool install skmemory==0.11.173   # or: pipx install skmemory==0.11.173

Config: ``$HERMES_HOME/skmemory.json`` (all optional), env vars override nothing
that the file sets::

    {
      "agent": "hermes",              # store name (isolation key)
      "skcapstone_home": "",          # linked mode: an existing skcapstone home
      "read_only": false,             # recall only, never write
      "mcp_command": "",              # path to skmemory-mcp (default: found on PATH)
      "assume_owner": false,          # see "Owner turns" below
      "timeout": 20                   # seconds per MCP call
    }

Linked mode only: the Postgres DSN comes from Hermes's secret store
(``SKMEMORY_PG_DSN`` via ``agent.secret_scope.get_secret``), never from this file.

Owner turns: a turn is an owner turn only when Hermes says so. Upstream Hermes has
no public per-turn reader scope yet, so the local CLI counts as the owner and a
messaging gateway counts as ``@public`` (fail closed: recall under ``@public``, no
writes), unless ``assume_owner`` is set for a single-user install. Hermes builds
that carry a session-scope module (``tools.session_scope``, not in upstream) supply
the reader instead. Non-owner turns search under their reader, get no sensitive
memories injected, and never write.

The child gets an allowlisted environment only (see ``_child_env``): no Hermes
provider keys or other secrets reach ``skmemory-mcp``.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT = 20.0
PLUGIN_VERSION = "0.2.2"
# The child's stderr goes here (rotated once at this size), so a startup crash is visible.
STDERR_LOG_MAX_BYTES = 5 * 1024 * 1024
_PUBLIC = "@public"
_TRUE = ("1", "true", "yes", "on")
# The only variables the child inherits from Hermes. Everything else (provider API
# keys, tokens, and any SK* selector that could point an isolated store at someone
# else's memory) stays out. The SK variables the child needs are set explicitly.
_ENV_ALLOW = frozenset((
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TMP", "TEMP",
    # Windows basics, so Python can start there at all.
    "SYSTEMROOT", "SYSTEMDRIVE", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PATHEXT", "COMSPEC",
))
_ENV_ALLOW_PREFIXES = ("LC_",)

# skmemory versions this plugin is tested against (serverInfo.version of skmemory-mcp).
TESTED_SKMEMORY = "0.11.173"
_SUPPORTED_MIN = (0, 11, 173)
_SUPPORTED_BELOW = (0, 12, 0)

_STOPWORDS = frozenset(
    """a an and are as at be but by can could did do does for from had has have how i if in
    into is it its just me my no not of on or our please so than that the their them then
    there these they this to too us was we were what when where which who why will with
    would you your yes ok okay hey hi hello thanks thank""".split()
)


# -- per-turn scope ---------------------------------------------------------


def _session_reader(assume_owner: bool = False):
    """None for an owner turn, else a reader string. Never raises; unknown is public."""
    try:
        from tools.session_scope import session_reader  # sk Hermes builds

        return session_reader()
    except ImportError:
        if assume_owner:
            return None
        # No session scope: only a process without a messaging gateway is the operator.
        return _PUBLIC if os.environ.get("_HERMES_GATEWAY") == "1" else None
    except Exception as e:  # fail closed
        logger.debug("skmemory: session scope failed (%s); treating as public", e)
        return _PUBLIC


def _version_tuple(v: str):
    try:
        return tuple(int(x) for x in str(v).split("+")[0].split(".")[:3])
    except ValueError:
        return None


def _check_server_version(info: Dict[str, Any]) -> None:
    """Warn when skmemory-mcp is not a tested version. Never raises."""
    version = str((info or {}).get("version") or "")
    vt = _version_tuple(version)
    if (info or {}).get("name") != "skmemory" or vt is None or not (
            _SUPPORTED_MIN <= vt < _SUPPORTED_BELOW):
        logger.warning(
            "skmemory: skmemory-mcp reports %s %s; this plugin is tested with skmemory %s "
            "(supported >=%s,<%s). Install it with `uv tool install skmemory==%s`.",
            (info or {}).get("name") or "?", version or "?", TESTED_SKMEMORY,
            ".".join(map(str, _SUPPORTED_MIN)), ".".join(map(str, _SUPPORTED_BELOW)),
            TESTED_SKMEMORY)


def _secret(name: str) -> Optional[str]:
    """A credential from Hermes's profile secret scope. Never raises; None when absent."""
    try:
        from agent.secret_scope import get_secret

        return get_secret(name) or None
    except Exception as e:  # no scope bound, or an old Hermes: no secret
        logger.debug("skmemory: secret %s unavailable (%s)", name, e)
        return None


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes"))


def _keywords(text: str, limit: int = 8) -> str:
    """Distinct content words of ``text``, longest first, for memory_auto_context."""
    words = []
    for w in re.findall(r"[\w][\w'-]{2,}", (text or "").lower()):
        w = w.strip("'-")
        if len(w) > 2 and w not in _STOPWORDS and w not in words:
            words.append(w)
    words.sort(key=len, reverse=True)
    return " ".join(words[:limit])


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


# -- minimal MCP stdio client -----------------------------------------------


class McpError(RuntimeError):
    pass


class McpClient:
    """Newline-delimited JSON-RPC 2.0 over a child's stdio (MCP stdio transport).

    Thread-safe: one request in flight at a time. A dead child is restarted once
    on the next call.
    """

    def __init__(self, argv: List[str], env: Dict[str, str], cwd: str, timeout: float,
                 stderr_log: Optional[Path] = None):
        self._argv = argv
        self._stderr_log = stderr_log
        self._env = env
        self._cwd = cwd
        self._timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self.server_info: Dict[str, Any] = {}

    def _open_stderr_log(self):
        """Append handle for the child's stderr, or DEVNULL if the log cannot be opened."""
        path = self._stderr_log
        if path is None:
            return subprocess.DEVNULL
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > STDERR_LOG_MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            fh = os.fdopen(fd, "a", encoding="utf-8")
            fh.write(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S%z')} starting {self._argv[0]}\n")
            fh.flush()
            return fh
        except OSError as e:
            logger.warning("skmemory: cannot open %s for skmemory-mcp stderr (%s)", path, e)
            return subprocess.DEVNULL

    def _start(self) -> None:
        self._lines = queue.Queue()
        err = self._open_stderr_log()
        try:
            self._proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=err,
                env=self._env,
                cwd=self._cwd,
                text=True,
                bufsize=1,
            )
        finally:
            if err is not subprocess.DEVNULL:
                err.close()  # the child holds its own copy of the descriptor
        threading.Thread(target=self._pump, args=(self._proc, self._lines),
                         name="skmemory-mcp-reader", daemon=True).start()
        init = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "hermes-skmemory", "version": PLUGIN_VERSION},
        })
        self.server_info = init.get("serverInfo") or {}
        _check_server_version(self.server_info)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @staticmethod
    def _pump(proc: subprocess.Popen, lines: "queue.Queue[Optional[str]]") -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.put(line)
        finally:
            lines.put(None)

    def _send(self, msg: Dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                line = self._lines.get(timeout=self._timeout)
            except queue.Empty:
                self.close()
                raise McpError(f"skmemory-mcp timed out after {self._timeout:g}s on {method}")
            if line is None:
                self.close()
                where = f"; its stderr is in {self._stderr_log}" if self._stderr_log else ""
                raise McpError(f"skmemory-mcp exited{where}")
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # not a JSON-RPC frame
            if msg.get("id") != rid:
                continue  # a notification or a stale reply
            if "error" in msg:
                raise McpError(str(msg["error"].get("message") or msg["error"]))
            return msg.get("result") or {}

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool; returns its JSON payload (the server's first text block, parsed)."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = "".join(c.get("text", "") for c in result.get("content") or []
                       if isinstance(c, dict) and c.get("type") == "text")
        try:
            payload = json.loads(text) if text else {}
        except ValueError:
            payload = {"text": text}
        if result.get("isError") or (isinstance(payload, dict) and payload.get("error")):
            raise McpError(str(payload.get("error") if isinstance(payload, dict) else text))
        return payload

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# -- tool schemas -----------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "skmemory_search",
    "description": (
        "Search your persistent skmemory store by meaning/keywords for facts, "
        "preferences, and past context. Use when you need to recall something."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to recall."},
            "limit": {"type": "integer", "description": "Max results (default 5)."},
        },
        "required": ["query"],
    },
}

STORE_SCHEMA = {
    "name": "skmemory_store",
    "description": (
        "Save a durable fact/preference/context to your skmemory store so it "
        "persists across sessions. Use for things worth remembering long-term."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short label."},
            "content": {"type": "string", "description": "The fact to remember."},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["content"],
    },
}


def _find_mcp_command(configured: str = "") -> Optional[str]:
    for cand in (configured, os.environ.get("SKMEMORY_MCP_BIN", "")):
        if cand:
            path = shutil.which(os.path.expanduser(cand))
            if path:
                return path
    found = shutil.which("skmemory-mcp")
    if found:
        return found
    for cand in ("~/.local/bin/skmemory-mcp", "~/.skenv/bin/skmemory-mcp"):
        p = Path(os.path.expanduser(cand))
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


class SkmemoryProvider(MemoryProvider):
    """Hermes memory provider backed by an ``skmemory-mcp`` child process."""

    def __init__(self) -> None:
        self.client: Optional[McpClient] = None
        self._cfg: Dict[str, Any] = {}
        self._agent = "hermes"
        self._read_only = False
        self._writable = False
        self._assume_owner = False
        self._mode = "isolated"
        self._home: Optional[Path] = None
        self._prefetched: Dict[str, str] = {}
        self._failing: set = set()
        self._prefetch_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "skmemory"

    # -- availability (config and binaries only, no process, no network) ----
    def is_available(self) -> bool:
        return _find_mcp_command(os.environ.get("SKMEMORY_MCP_BIN", "")) is not None

    def unavailable_reason(self) -> str:
        return ("skmemory-mcp not found. Install skmemory outside Hermes "
                "(`uv tool install skmemory` or `pipx install skmemory`) or set "
                "mcp_command in $HERMES_HOME/skmemory.json.")

    # -- config -------------------------------------------------------------
    @staticmethod
    def _load_config(hermes_home: Path) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {
            "agent": "hermes",
            "skcapstone_home": os.environ.get("SKMEMORY_LINK_HOME", ""),
            "read_only": str(os.environ.get("SKMEMORY_READ_ONLY", "")).lower() in _TRUE,
            "mcp_command": os.environ.get("SKMEMORY_MCP_BIN", ""),
            "assume_owner": str(os.environ.get("SKMEMORY_ASSUME_OWNER", "")).lower() in _TRUE,
            "timeout": DEFAULT_TIMEOUT,
        }
        path = hermes_home / "skmemory.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if "dsn" in data:
                    logger.warning("skmemory: ignoring `dsn` in skmemory.json; set "
                                   "SKMEMORY_PG_DSN as a Hermes secret instead")
                cfg.update({k: v for k, v in data.items() if v not in (None, "") and k != "dsn"})
            except Exception as e:
                logger.warning("skmemory: bad skmemory.json (%s); using defaults", e)
        return cfg

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "agent", "description": "Store name (isolated mode) or agent name (linked mode)",
             "default": "hermes"},
            {"key": "skcapstone_home", "description":
             "Linked mode: an existing skcapstone home to serve (empty = private store in HERMES_HOME)",
             "default": ""},
            {"key": "read_only", "description": "Recall only, never write", "type": "boolean",
             "default": False},
            {"key": "assume_owner", "description":
             "Single-user install on a Hermes without session scopes: treat every turn as the owner's",
             "type": "boolean", "default": False},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        path = Path(hermes_home) / "skmemory.json"
        current: Dict[str, Any] = {}
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
        current.update({k: v for k, v in values.items() if k in
                        ("agent", "skcapstone_home", "read_only", "assume_owner", "mcp_command", "timeout")})
        path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")

    # -- lifecycle ----------------------------------------------------------
    def _child_env(self, skcap_home: Path, agent_home: Optional[Path]) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items()
               if k in _ENV_ALLOW or k.startswith(_ENV_ALLOW_PREFIXES)}
        env.update({
            "SKCAPSTONE_HOME": str(skcap_home),
            "SKAGENT": self._agent,
            "SKMEMORY_AGENT": self._agent,
            "SKCAPSTONE_AGENT": self._agent,
        })
        if agent_home is not None:
            # Isolated store: an explicit home is an operator instruction to skmemory,
            # so it opens this store without a profile registry.
            env["SKMEMORY_HOME"] = str(agent_home)
        if self._mode == "linked":
            dsn = _secret("SKMEMORY_PG_DSN")
            if dsn:
                env["SKMEMORY_PG_DSN"] = dsn
        return env

    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = Path(kwargs.get("hermes_home") or _hermes_home())
        self._cfg = self._load_config(hermes_home)
        self._agent = str(self._cfg.get("agent") or "hermes")
        self._read_only = bool(self._cfg.get("read_only"))
        self._assume_owner = bool(self._cfg.get("assume_owner"))
        agent_context = str(kwargs.get("agent_context") or "primary")
        self._writable = agent_context == "primary" and not self._read_only

        link = str(self._cfg.get("skcapstone_home") or "").strip()
        if link:
            self._mode = "linked"
            skcap_home = Path(os.path.expanduser(link))
            agent_home = None
            if not (skcap_home / "agents" / self._agent).is_dir():
                logger.warning("skmemory: linked agent %s not found under %s", self._agent, skcap_home)
            self._home = None
        else:
            self._mode = "isolated"
            # Not named "skmemory": $HERMES_HOME can be on sys.path, and a dir of that
            # name would shadow the package for anything that does import it.
            skcap_home = hermes_home / ".skmemory"
            agent_home = skcap_home / "agents" / self._agent
            (agent_home / "config").mkdir(parents=True, exist_ok=True)
            cfg_file = agent_home / "config" / "skmemory.yaml"
            if not cfg_file.exists():
                cfg_file.write_text("{}\n", encoding="utf-8")
            self._home = skcap_home

        command = _find_mcp_command(str(self._cfg.get("mcp_command") or ""))
        if not command:
            logger.warning("skmemory: %s", self.unavailable_reason())
            self.client = None
            return
        try:
            timeout = float(self._cfg.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        # Under HERMES_HOME in both modes: linked mode must not create files in the target store.
        stderr_log = hermes_home / "logs" / f"skmemory-mcp-{self._agent}.log"
        self.client = McpClient([command], self._child_env(skcap_home, agent_home),
                                cwd=str(hermes_home), timeout=timeout, stderr_log=stderr_log)
        logger.info("skmemory: ready (mode=%s, agent=%s, read_only=%s, server=%s)",
                    self._mode, self._agent, self._read_only, command)

    def shutdown(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            client.close()

    def backup_paths(self) -> List[str]:
        # The isolated store lives inside HERMES_HOME, so `hermes backup` already has it.
        return []

    def system_prompt_block(self) -> str:
        if self.client is None:
            return ""
        if self._read_only:
            return "You have a persistent skmemory store. Use `skmemory_search` to recall facts and context."
        return ("You have a persistent skmemory store. Use `skmemory_search` to recall "
                "facts/preferences and `skmemory_store` to save things worth keeping.")

    # -- failure reporting --------------------------------------------------
    def _failed(self, what: str, e: Exception) -> None:
        """Warn on the first failure of each kind, then debug until it succeeds again."""
        if what in self._failing:
            logger.debug("skmemory %s failed again: %s", what, e)
        else:
            self._failing.add(what)
            logger.warning("skmemory %s failed: %s", what, e)

    def _ok(self, what: str) -> None:
        if what in self._failing:
            self._failing.discard(what)
            logger.info("skmemory %s works again", what)

    # -- recall -------------------------------------------------------------
    def _reader(self):
        return _session_reader(self._assume_owner)

    def _owner_turn(self) -> bool:
        try:
            return self._reader() is None
        except Exception:
            return False

    def _recall(self, query: str) -> str:
        if self.client is None:
            return ""
        keywords = _keywords(query)
        if not keywords:
            return ""
        args: Dict[str, Any] = {"keywords": keywords, "token_budget": 800}
        reader = self._reader()
        if reader is not None:
            args["reader"] = reader
        try:
            payload = self.client.call_tool("memory_auto_context", args)
        except Exception as e:
            self._failed("recall", e)
            return ""
        self._ok("recall")
        rows = payload.get("results") if isinstance(payload, dict) else None
        lines = []
        for r in rows or []:
            title = r.get("title") or "(untitled)"
            summary = (r.get("summary") or "").strip()
            lines.append(f"- **{title}**: {summary[:500]}")
        return "## Relevant memories\n" + "\n".join(lines) if lines else ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if self.client is None or not (query or "").strip():
            return

        def run() -> None:
            text = self._recall(query)
            with self._prefetch_lock:
                self._prefetched[session_id] = text

        threading.Thread(target=run, name="skmemory-prefetch", daemon=True).start()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        with self._prefetch_lock:
            cached = self._prefetched.pop(session_id, None)
        if cached is not None:
            return cached
        return self._recall(query) if (query or "").strip() else ""

    # -- writes -------------------------------------------------------------
    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: Optional[List[Dict[str, Any]]] = None, **kwargs) -> None:
        if self.client is None or not self._writable:
            return
        if not (user_content or "").strip() and not (assistant_content or "").strip():
            return
        if not self._owner_turn():
            return  # group chat / non-owner DM: never written as owner memory
        title = ((user_content or assistant_content or "").strip()[:60]) or "turn"
        speaker = (self._agent or "assistant").capitalize()
        content = f"User: {user_content}\n\n{speaker}: {assistant_content}".strip()[:4000]
        tags = ["hermes"] + ([f"session:{session_id}"] if session_id else [])
        try:
            self.client.call_tool("memory_store", {
                "title": title, "content": content, "tags": tags,
                "source": "hermes", "layer": "short-term",
            })
        except Exception as e:
            self._failed("turn write", e)
            return
        self._ok("turn write")

    # -- tools --------------------------------------------------------------
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA] if self._read_only else [SEARCH_SCHEMA, STORE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if self.client is None:
            return _err("skmemory is not running")
        args = args or {}
        try:
            if tool_name == "skmemory_search":
                query = str(args.get("query") or "")
                if not query.strip():
                    return _err("query is required")
                call: Dict[str, Any] = {"query": query, "limit": max(1, min(int(args.get("limit") or 5), 20))}
                reader = self._reader()
                if reader is not None:
                    call["reader"] = reader
                rows = self.client.call_tool("memory_search", call)
                self._ok(f"tool {tool_name}")
                if isinstance(rows, dict):
                    rows = rows.get("results") or []
                out = [{"id": r.get("id"), "title": r.get("title", ""),
                        "content": (r.get("content") or r.get("summary") or "")[:500],
                        "tags": (r.get("tags") or [])[:8]} for r in rows or [] if isinstance(r, dict)]
                return json.dumps({"results": out})
            if tool_name == "skmemory_store":
                if self._read_only:
                    return _err("skmemory is read-only; writes are disabled")
                if not self._owner_turn():
                    return _err("skmemory writes are disabled in a non-owner session")
                content = str(args.get("content") or "")
                if not content.strip():
                    return _err("content is required")
                tags = [str(t) for t in (args.get("tags") or [])][:16]
                payload = self.client.call_tool("memory_store", {
                    "title": str(args.get("title") or content[:60]), "content": content,
                    "tags": tags, "source": "hermes",
                })
                self._ok(f"tool {tool_name}")
                mid = payload.get("memory_id") if isinstance(payload, dict) else None
                return json.dumps({"stored": True, "id": mid})
        except Exception as e:
            self._failed(f"tool {tool_name}", e)
            return _err(f"{tool_name} failed: {e}")
        return _err(f"unknown tool {tool_name}")


def register(ctx) -> None:
    """Plugin entry point: one provider instance per load (per Hermes profile)."""
    ctx.register_memory_provider(SkmemoryProvider())
