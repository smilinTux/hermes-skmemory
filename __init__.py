"""skmemory memory plugin — Hermes MemoryProvider over skmemory.

Wraps skmemory's `MemoryStore` so a Hermes user gets persistent recall with
**zero infrastructure**: the default backend is pure-SQLite (stdlib only), with
an opt-in **pgvector** toggle for semantic/RAG search against a Postgres store.

Design notes (verified against skmemory 0.9.x):
  * skmemory's package import (`seeds.py`) calls ``get_agent_paths()`` at module
    load and HARD-RAISES if no ``$SKCAPSTONE_HOME/agents/<agent>/`` scaffold
    exists. So ``initialize()`` bootstraps an ISOLATED, Hermes-scoped scaffold
    under ``$HERMES_HOME/skmemory`` (NOT the shared ~/.skcapstone) and sets
    ``SKCAPSTONE_HOME``/``SKAGENT`` BEFORE importing skmemory. ``is_available()``
    therefore checks installed-ness via ``find_spec`` only — it never imports.
  * Backend selection mirrors skmemory's own MCP server: ``vector_backend:
    pgvector`` in ``$HERMES_HOME/skmemory.json`` → ``PGVectorBackend`` passed as
    the vector backend; otherwise SQLite primary with keyword-fallback search.

Config — ``$HERMES_HOME/skmemory.json`` (all optional):
    {
      "agent": "hermes",                 # scaffold/agent name (isolation key)
      "vector_backend": "sqlite",        # "sqlite" (default) | "pgvector"
      "dsn": "postgresql://...",         # pgvector only
      "embed_url": "http://host:11434/api/embed",
      "embed_model": "mxbai-embed-large"
    }
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)


# -- per-chat scope (SKMEM-P0-23) -------------------------------------------
# prefetch() injects memories into EVERY turn. Without a reader it ran an owner
# search, so sacred / owner-only memories could land in a group chat. The reader
# comes from Hermes' session scope (None = owner session, else "channel:<id>" or
# "@public"; honours HERMES_MEMORY_OWNER / HERMES_MEMORY_OWNER_CHATS). Fail closed.
_PUBLIC = "@public"


def _session_reader():
    """Return None (owner), or a reader string. Never raises; unknown means public."""
    try:
        from tools.session_scope import session_reader  # Hermes core

        return session_reader()
    except ImportError:
        # Hermes without session scope: only a process with no messaging gateway
        # (local CLI) can be treated as the operator.
        return _PUBLIC if os.environ.get("_HERMES_GATEWAY") == "1" else None
    except Exception as e:  # fail closed
        logger.debug("skmemory: session scope failed (%s); treating as public", e)
        return _PUBLIC


def _owner_may_write() -> bool:
    """True only for an owner session (reader None). Fails closed on any error.

    The write side of the same rule as the read side. skmemory's ``snapshot()`` has
    no audience field (``context_tag`` defaults to owner-only and cannot be set at
    write time), so a non-owner turn cannot be stored with a scoped audience. It is
    therefore not stored at all: no opt-in exists.
    """
    try:
        return _session_reader() is None
    except Exception as e:
        logger.debug("skmemory: write scope check failed (%s); not writing", e)
        return False


def _drop_sensitive(memories):
    """Remove memories whose tags are sensitive or owner-only (auto-injection must never carry them)."""
    try:
        from skmemory.hooks.sensitivity import is_owner_only, is_sensitive
    except Exception as e:  # cannot classify, so inject nothing
        logger.debug("skmemory: sensitivity filter unavailable (%s)", e)
        return []
    return [
        m
        for m in memories or []
        if not (
            is_sensitive(getattr(m, "tags", None))
            or is_owner_only(getattr(m, "tags", None))
        )
    ]


def _err(msg: str) -> str:
    """JSON tool-error (inlined to avoid a hard tools.registry dependency)."""
    return json.dumps({"error": msg})


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


class SkmemoryProvider(MemoryProvider):
    """Hermes memory provider backed by skmemory (SQLite default, pgvector opt-in)."""

    def __init__(self) -> None:
        self.store = None
        self._cfg: Dict[str, Any] = {}
        self._agent = "hermes"
        self._read_only = False
        self._writable = False
        self._mode = "isolated"

    # -- identity -----------------------------------------------------------
    @property
    def name(self) -> str:
        return "skmemory"

    # -- availability (must NOT import skmemory — that can hard-raise) -------
    def is_available(self) -> bool:
        import importlib.util

        return importlib.util.find_spec("skmemory") is not None

    # -- config -------------------------------------------------------------
    def _load_config(self, hermes_home: Path) -> Dict[str, Any]:
        cfg = {
            "agent": "hermes",
            # Linked/shim mode: point at an EXISTING skcapstone store (e.g.
            # ~/.skcapstone with agent=lumina) instead of an isolated one.
            "skcapstone_home": os.environ.get("SKMEMORY_LINK_HOME", ""),
            # Read-only: expose search/recall but disable the write tool, so a
            # linked precious store is never modified by Hermes.
            "read_only": str(os.environ.get("SKMEMORY_READ_ONLY", "")).lower()
            in ("1", "true", "yes"),
            "vector_backend": os.environ.get("SKMEMORY_VECTOR_BACKEND", "sqlite"),
            "dsn": os.environ.get("SKMEMORY_DSN", ""),
            "embed_url": os.environ.get("SKMEMORY_EMBED_URL", ""),
            "embed_model": os.environ.get("SKMEMORY_EMBED_MODEL", ""),
        }
        cfg_path = hermes_home / "skmemory.json"
        if cfg_path.exists():
            try:
                file_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg.update({k: v for k, v in file_cfg.items() if v not in (None, "")})
            except Exception as e:
                logger.warning("skmemory: bad skmemory.json (%s); using defaults", e)
        return cfg

    # -- lifecycle ----------------------------------------------------------
    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = Path(kwargs.get("hermes_home") or os.path.expanduser("~/.hermes"))
        self._cfg = self._load_config(hermes_home)
        self._agent = str(
            self._cfg.get("agent") or kwargs.get("agent_identity") or "hermes"
        )
        self._read_only = bool(self._cfg.get("read_only"))
        # Writes are enabled for primary sessions only. Cron/subagent contexts
        # are skipped (their system prompts would corrupt the user's memory), and
        # read_only disables all writes regardless.
        agent_context = str(kwargs.get("agent_context", "primary") or "primary")
        self._writable = (agent_context == "primary") and not self._read_only

        link_home = str(self._cfg.get("skcapstone_home") or "").strip()
        if link_home:
            # LINKED / SHIM mode — use an EXISTING skcapstone store as-is. Do NOT
            # create or overwrite anything in the user's store. Recommended with
            # read_only=true so Hermes can recall but never modify it.
            skcap_home = Path(os.path.expanduser(link_home))
            agent_dir = skcap_home / "agents" / self._agent
            if not agent_dir.is_dir():
                logger.warning(
                    "skmemory: linked skcapstone agent dir not found: %s — recall "
                    "may be empty (not creating it in linked mode)",
                    agent_dir,
                )
            self._mode = "linked"
        else:
            # ISOLATED / OOTB mode — bootstrap a Hermes-scoped scaffold so
            # `import skmemory` succeeds without touching any shared ~/.skcapstone.
            #
            # The scaffold dir must NOT be named "skmemory": the gateway runs as
            # `python -m hermes_cli...`, so its CWD ($HERMES_HOME) is on
            # sys.path[0]. A dir named "skmemory" there shadows the installed
            # `skmemory` package as a namespace package → `from skmemory import
            # MemoryStore` fails with "unknown location" and the store never
            # initializes. A dotted name (".skmemory") is not a valid module
            # name, so it can never shadow the package.
            skcap_home = hermes_home / ".skmemory"
            agent_dir = skcap_home / "agents" / self._agent
            for sub in ("config", "memory", "seeds"):
                (agent_dir / sub).mkdir(parents=True, exist_ok=True)
            cfg_file = agent_dir / "config" / "skmemory.yaml"
            if not cfg_file.exists():
                cfg_file.write_text("{}\n", encoding="utf-8")
            self._mode = "isolated"
        os.environ["SKCAPSTONE_HOME"] = str(skcap_home)
        os.environ["SKAGENT"] = self._agent

        try:
            from skmemory import MemoryStore
        except Exception as e:
            logger.warning("skmemory: import failed after scaffold bootstrap: %s", e)
            self.store = None
            return

        vector = self._build_vector_backend()
        try:
            self.store = MemoryStore(
                vector=vector
            )  # SQLite primary + optional pg vector
            backend = "pgvector" if vector is not None else "sqlite"
            logger.info(
                "skmemory: initialized (mode=%s, agent=%s, backend=%s, read_only=%s, home=%s)",
                self._mode,
                self._agent,
                backend,
                self._read_only,
                skcap_home,
            )
        except Exception as e:
            logger.warning("skmemory: MemoryStore init failed: %s", e)
            self.store = None

    def _build_vector_backend(self):
        if str(self._cfg.get("vector_backend", "sqlite")).lower() != "pgvector":
            return None
        try:
            from skmemory.backends.pgvector_backend import PGVectorBackend

            kw: Dict[str, Any] = {"agent": self._agent}
            if self._cfg.get("dsn"):
                kw["dsn"] = self._cfg["dsn"]
            if self._cfg.get("embed_url"):
                kw["embed_url"] = self._cfg["embed_url"]
            if self._cfg.get("embed_model"):
                kw["embed_model"] = self._cfg["embed_model"]
            backend = PGVectorBackend(**kw)
            logger.info("skmemory: pgvector backend enabled")
            return backend
        except Exception as e:
            logger.warning(
                "skmemory: pgvector requested but unavailable (%s); "
                "falling back to SQLite keyword search",
                e,
            )
            return None

    def system_prompt_block(self) -> str:
        if self.store is None:
            return ""
        return (
            "You have a persistent skmemory store. Use `skmemory_search` to recall "
            "facts/preferences and `skmemory_store` to save things worth keeping."
        )

    # -- recall / write -----------------------------------------------------
    def _scoped_search(self, query: str, limit: int, reader):
        """store.search under the caller's reader. A non-owner reader on a skmemory that
        cannot take ``reader`` returns nothing rather than an unscoped owner search."""
        if reader is None:
            return self.store.search(query, limit=limit)
        try:
            return self.store.search(query, limit=limit, reader=reader)
        except TypeError:
            logger.warning(
                "skmemory: store.search has no reader support; refusing non-owner search"
            )
            return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self.store is None or not query.strip():
            return ""
        try:
            results = _drop_sensitive(self._scoped_search(query, 5, _session_reader()))
        except Exception as e:
            logger.debug("skmemory prefetch failed: %s", e)
            return ""
        return self._format_memories(results, header="Relevant memories")

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        # Sessions write to memory: each completed turn is captured as a
        # SHORT-term snapshot tagged with the session. skmemory's own
        # synthesis/consolidation (skwhisper, memory_synthesize_*) rolls these
        # up over time. Gated on writable (primary context, not read-only).
        if self.store is None or not self._writable:
            return
        if not (user_content or "").strip() and not (assistant_content or "").strip():
            return
        if not _owner_may_write():
            return  # group chat / non-owner DM: never written as owner memory
        try:
            title = ((user_content or assistant_content or "").strip()[:60]) or "turn"
            # Label the assistant turn with THIS agent's name, not a hardcoded
            # one — the plugin ships to any Hermes user, not just Lumina.
            speaker = (self._agent or "assistant").capitalize()
            content = f"User: {user_content}\n\n{speaker}: {assistant_content}".strip()[
                :4000
            ]
            tags = ["hermes"]
            if session_id:
                tags.append(f"session:{session_id}")
            self.store.snapshot(
                title=title,
                content=content,
                tags=tags,
                source="hermes",
                source_ref=session_id or "",
            )
        except Exception as e:
            logger.debug("skmemory sync_turn write failed: %s", e)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        # read-only (e.g. linked to a precious store) → recall only, no writes.
        if self._read_only:
            return [SEARCH_SCHEMA]
        return [SEARCH_SCHEMA, STORE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if self.store is None:
            return _err("skmemory store not initialized")
        try:
            if tool_name == "skmemory_search":
                q = (args or {}).get("query", "")
                limit = int((args or {}).get("limit", 5) or 5)
                reader = _session_reader()
                results = self._scoped_search(q, limit, reader)
                if reader is not None:
                    results = _drop_sensitive(results)
                return json.dumps({"results": self._memories_to_dicts(results)})
            if tool_name == "skmemory_store":
                if self._read_only:
                    return _err("skmemory is read-only (linked store); writes disabled")
                if not _owner_may_write():
                    return _err("skmemory writes are disabled in a non-owner session")
                content = (args or {}).get("content", "")
                if not content.strip():
                    return _err("content is required")
                title = (args or {}).get("title") or content[:60]
                tags = (args or {}).get("tags") or None
                mem = self.store.snapshot(
                    title=title,
                    content=content,
                    tags=tags,
                    source="hermes",
                )
                return json.dumps({"stored": True, "id": getattr(mem, "id", None)})
        except Exception as e:
            logger.debug("skmemory tool %s failed: %s", tool_name, e)
            return _err(f"{tool_name} failed: {e}")
        return _err(f"unknown tool {tool_name}")

    def shutdown(self) -> None:
        self.store = None

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _memories_to_dicts(memories) -> List[Dict[str, Any]]:
        out = []
        for m in memories or []:
            out.append(
                {
                    "title": getattr(m, "title", ""),
                    "content": (getattr(m, "content", "") or "")[:500],
                    "id": getattr(m, "id", None),
                }
            )
        return out

    def _format_memories(self, memories, *, header: str) -> str:
        dicts = self._memories_to_dicts(memories)
        if not dicts:
            return ""
        lines = [f"## {header}"]
        for d in dicts:
            t = d["title"] or "(untitled)"
            lines.append(f"- **{t}**: {d['content']}")
        return "\n".join(lines)


def register(ctx) -> None:
    """Plugin entry point — register the skmemory provider instance."""
    ctx.register_memory_provider(SkmemoryProvider())
