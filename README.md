# skmemory (Hermes memory plugin)

Use **[skmemory](https://github.com/smilinTux/skmemory)** as a [Hermes Agent](https://github.com/NousResearch/hermes-agent)
memory backend. **SQLite out-of-the-box — zero infrastructure**, with an opt-in
**pgvector** toggle for semantic/RAG search.

A self-contained on-ramp into the SKWorld ecosystem: one `pip install skmemory`,
no Docker, no embedding server, no rest of the SK stack required.

## Tiers

| Tier | `vector_backend` | Deps | Search |
|---|---|---|---|
| **Lite (default, OOTB)** | `sqlite` | stdlib only | keyword |
| **Full RAG** *(opt-in)* | `pgvector` | a Postgres+pgvector store + embeddings | semantic / hybrid |

The default needs nothing but `pip install skmemory`. skmemory's source of truth
is flat files + a rebuildable index, so upgrading lite→full is just a config flip
+ reindex — no data migration.

## Install

```bash
# the memory wizard installs the pip dep and activates it:
hermes plugins install /home/cbrd21/clawd/skcapstone-repos/hermes-skmemory   # → ~/.hermes/plugins/skmemory
hermes memory setup        # pick "skmemory"; auto-installs `skmemory`
# (or set memory.provider: skmemory in ~/.hermes/config.yaml)
```

## Config — `~/.hermes/skmemory.json` (all optional)

```json
{
  "agent": "hermes",
  "vector_backend": "sqlite",
  "dsn": "postgresql://user:pass@host:5432/skmemory",
  "embed_url": "http://192.168.0.100:11434/api/embed",
  "embed_model": "mxbai-embed-large"
}
```

`vector_backend: "pgvector"` switches to semantic search; `dsn`/`embed_*` point at
your Postgres + embedding server. Env equivalents: `SKMEMORY_VECTOR_BACKEND`,
`SKMEMORY_DSN`, `SKMEMORY_EMBED_URL`, `SKMEMORY_EMBED_MODEL`.

## Two modes

**Isolated (default, OOTB)** — the plugin creates a Hermes-scoped store under
`~/.hermes/skmemory/agents/<agent>/`. It does **not** touch any shared
`~/.skcapstone` store; a Hermes user's memories stay self-contained.

**Linked / shim** — point Hermes at an *existing* skcapstone store (e.g. your own
`~/.skcapstone` with `agent: lumina`) so the agent recalls your real memories.
Set `skcapstone_home` + `agent` in config. **Strongly pair with `read_only: true`**
so Hermes can search/recall but never writes into your precious store (the
`skmemory_store` tool is withheld and refused). In linked mode the plugin creates
and overwrites **nothing** in the target store.

```json
{ "skcapstone_home": "/home/cbrd21/.skcapstone", "agent": "lumina",
  "read_only": true, "vector_backend": "pgvector",
  "dsn": "postgresql://…", "embed_url": "http://192.168.0.100:11434/api/embed",
  "embed_model": "mxbai-embed-large" }
```

Env equivalents: `SKMEMORY_LINK_HOME`, `SKMEMORY_READ_ONLY`.

## Tools exposed to the model

- `skmemory_search(query, limit)` — recall facts/preferences/context.
- `skmemory_store(content, title?, tags?)` — persist something durable.

v0.1 persists explicitly via the tool (no write-on-every-turn, to avoid bloat);
automatic turn extraction can be added later (`on_session_end` / `on_pre_compress`).

## Status

v0.1 — core integration **empirically validated** against skmemory 0.9.x
(scaffold bootstrap + SQLite store + snapshot/search). Pending: live `hermes
memory setup` selection test in a clean Hermes venv, and the pgvector path against
a real Postgres.
