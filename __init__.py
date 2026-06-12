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
        self._agent = str(self._cfg.get("agent") or kwargs.get("agent_identity") or "hermes")

        # Bootstrap an ISOLATED skcapstone scaffold so `import skmemory` succeeds
        # without touching any shared ~/.skcapstone agent store.
        skcap_home = hermes_home / "skmemory"
        agent_dir = skcap_home / "agents" / self._agent
        for sub in ("config", "memory", "seeds"):
            (agent_dir / sub).mkdir(parents=True, exist_ok=True)
        cfg_file = agent_dir / "config" / "skmemory.yaml"
        if not cfg_file.exists():
            cfg_file.write_text("{}\n", encoding="utf-8")
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
            self.store = MemoryStore(vector=vector)  # SQLite primary + optional pg vector
            mode = "pgvector" if vector is not None else "sqlite"
            logger.info(
                "skmemory: initialized (agent=%s, backend=%s, home=%s)",
                self._agent, mode, skcap_home,
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
                "falling back to SQLite keyword search", e,
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
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self.store is None or not query.strip():
            return ""
        try:
            results = self.store.search(query, limit=5)
        except Exception as e:
            logger.debug("skmemory prefetch failed: %s", e)
            return ""
        return self._format_memories(results, header="Relevant memories")

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages: Optional[List[Dict[str, Any]]] = None) -> None:
        # v0.1: no automatic write-on-every-turn (avoids store bloat). Persistence
        # is explicit via the skmemory_store tool. Auto-extraction can be added
        # later (e.g. on_session_end / on_pre_compress) following mem0's pattern.
        return

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, STORE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if self.store is None:
            return _err("skmemory store not initialized")
        try:
            if tool_name == "skmemory_search":
                q = (args or {}).get("query", "")
                limit = int((args or {}).get("limit", 5) or 5)
                results = self.store.search(q, limit=limit)
                return json.dumps({"results": self._memories_to_dicts(results)})
            if tool_name == "skmemory_store":
                content = (args or {}).get("content", "")
                if not content.strip():
                    return _err("content is required")
                title = (args or {}).get("title") or content[:60]
                tags = (args or {}).get("tags") or None
                mem = self.store.snapshot(
                    title=title, content=content, tags=tags, source="hermes",
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
            out.append({
                "title": getattr(m, "title", ""),
                "content": (getattr(m, "content", "") or "")[:500],
                "id": getattr(m, "id", None),
            })
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
