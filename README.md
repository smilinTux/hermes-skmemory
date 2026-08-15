# hermes-skmemory

**[skmemory](https://github.com/smilinTux/skmemory) as a
[Hermes Agent](https://github.com/NousResearch/hermes-agent) memory backend.** A memory
**provider** plugin, loaded in-process by `hermes-gateway.service`. It binds no port and
has no CLI.

**Maturity tier: T0, N/A (no key material).** Not a crypto component, but it reads a
Postgres DSN containing a password. **License:** GPL-3.0-or-later.
**Operational contract:** [`SOP.md`](./SOP.md).

SQLite out of the box, zero infrastructure, with an opt-in **pgvector** toggle for
semantic search. A self-contained on-ramp into the SKWorld ecosystem: one
`pip install skmemory`, no Docker, no embedding server, none of the rest of the SK stack.

> ### Two things that will confuse you first
>
> **1. `hermes plugins list` reports this plugin as `not enabled` even while it is loaded
> and serving every turn.** That is expected. `kind: exclusive` plugins are skipped by the
> general plugin scanner and loaded by the memory subsystem instead. The lever that
> actually activates it is `memory.provider: skmemory` in `~/.hermes/config.yaml`, **not**
> `plugins.enabled`. See [`SOP.md`](./SOP.md) section 2.
>
> **2. The repo is `hermes-skmemory`; the thing Hermes loads is `skmemory`.** The manifest
> name and the installed directory are both `skmemory`, and that is the name
> `memory.provider` refers to.

## Tiers

| Tier | `vector_backend` | Deps | Search |
|---|---|---|---|
| **Lite (default, out of the box)** | `sqlite` | stdlib only | keyword |
| **Full RAG (opt-in)** | `pgvector` | a Postgres + pgvector store and an embedding endpoint | semantic / hybrid |

skmemory's source of truth is flat files plus a rebuildable index, so lite to full is a
config flip and a reindex, with no data migration.

## Install

```bash
hermes plugins install /home/cbrd21/clawd/skcapstone-repos/hermes-skmemory   # lands at ~/.hermes/plugins/skmemory
hermes memory setup        # pick "skmemory"; installs the `skmemory` pip dep
# or set memory.provider: skmemory in ~/.hermes/config.yaml by hand
systemctl --user restart hermes-gateway
journalctl --user -u hermes-gateway -n 200 | grep 'skmemory: initialized'
```

That last line is the only positive confirmation the provider came up. Every failure path
in `initialize()` is a warning that leaves the gateway running normally, so silence means
broken, not fine.

## Config: `~/.hermes/skmemory.json` (all keys optional)

```json
{
  "agent": "hermes",
  "vector_backend": "sqlite",
  "dsn": "postgresql://user:password@host:5432/skmemory",
  "embed_url": "http://embed-host:11434/api/embed",
  "embed_model": "mxbai-embed-large"
}
```

Env equivalents: `SKMEMORY_VECTOR_BACKEND`, `SKMEMORY_DSN`, `SKMEMORY_EMBED_URL`,
`SKMEMORY_EMBED_MODEL`, `SKMEMORY_LINK_HOME`, `SKMEMORY_READ_ONLY`. File values win over
env values. **Never commit a real DSN**; the example above is a placeholder and must stay
one.

## Two modes

**Isolated (default).** The plugin creates a Hermes-scoped store under
`~/.hermes/.skmemory/agents/<agent>/`. Note the **leading dot**: a directory named
`skmemory` in `$HERMES_HOME` shadows the installed `skmemory` package (the gateway's CWD
is `$HERMES_HOME`, which lands on `sys.path[0]`) and the store never initialises. That was
a real outage, fixed in `a1a9143`. It does not touch any shared `~/.skcapstone` store.

**Linked / shim.** Point Hermes at an *existing* skcapstone store, for example your own
`~/.skcapstone` with `agent: lumina`, so the agent recalls your real memories. Set
`skcapstone_home` and `agent`. The plugin creates and overwrites nothing in the target
store.

```json
{ "skcapstone_home": "/home/cbrd21/.skcapstone", "agent": "lumina",
  "read_only": true, "vector_backend": "pgvector" }
```

> ⚠️ **Pair linked mode with `read_only: true`.** Without it, the model can write into your
> real store with `skmemory_store`, and every completed primary-session turn is snapshotted
> into it automatically. The live node on `noroc2027` currently runs linked **and writable**
> against the real `~/.skcapstone`. That is a deliberate operator choice, and
> [`SECURITY.md`](./SECURITY.md) explains what it exposes, including memory as a persistent
> prompt-injection surface.

## Tools exposed to the model

- `skmemory_search(query, limit)`: recall facts, preferences, context.
- `skmemory_store(content, title?, tags?)`: persist something durable. **Withheld entirely
  when `read_only` is true.**

**Sessions write to memory.** Each completed primary-session turn is captured as a
short-term snapshot tagged `hermes` and `session:<id>` with `source: hermes`. skmemory's
own synthesis (skwhisper, `memory_synthesize_*`) consolidates them over time. Cron and
subagent contexts are skipped so their system prompts cannot corrupt memory.

## Status

**Live on `noroc2027`, incubating.** Running in linked mode against `~/.skcapstone` as
`agent: lumina` with `vector_backend: pgvector`.

**There are no tests in this repo. None.** Everything documented here is backed by reading
the code and observing the live node, not by an automated check. CI
(`.github/workflows/ci.yml`) runs structural gates only: `compileall`, `ruff`, an AST
contract check over the `MemoryProvider` methods, and a manifest check. `SOP.md` section 4
says what real tests would need to cover, and it is the single most valuable contribution
available here.

Two live faults are **documented but not confirmed resolved**: a Postgres auth failure that
made vector search fall back to text search silently (490 log lines, 2026-08-06 to
2026-08-12) and an unreachable embed endpoint (36 lines, 2026-08-13 to 2026-08-14). Both
are in `SOP.md` sections 8 and Unverified. The pgvector path has never been validated end
to end against a healthy Postgres and a healthy embed endpoint at the same time.

## Docs

[`SOP.md`](./SOP.md) · [`SECURITY.md`](./SECURITY.md) · [`CONTRIBUTING.md`](./CONTRIBUTING.md) ·
[`CHANGELOG.md`](./CHANGELOG.md) · [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md)
