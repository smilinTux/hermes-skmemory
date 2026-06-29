# SOP — hermes-skmemory

## What it is
A **[Hermes Agent](https://github.com/NousResearch/hermes-agent) memory plugin** that wires
**[skmemory](https://github.com/smilinTux/skmemory)** in as Hermes' memory backend. The point
is a **zero-infrastructure on-ramp** into the SKWorld ecosystem: `pip install skmemory`, no
Docker, no embedding server, no rest of the SK stack required — with an opt-in upgrade to full
semantic RAG when you want it.

Part of the **SKWorld host-plugin pattern**: thin per-host (Hermes) adapter, owned here,
outside the Hermes checkout.

## Tiers
| Tier | `vector_backend` | Deps | Search |
|---|---|---|---|
| **Lite (default, OOTB)** | `sqlite` | stdlib only | keyword |
| **Full RAG** *(opt-in)* | `pgvector` | Postgres+pgvector + embeddings | semantic / hybrid |

skmemory's source of truth is flat files + a rebuildable index, so **lite → full is just a
config flip + reindex** — no data migration.

## Operate
- **Install**: `pip install skmemory`; register the plugin via `plugin.yaml`.
- **Default**: nothing else — SQLite keyword memory works out of the box.
- **Upgrade to RAG**: set `vector_backend: pgvector`, point at a Postgres+pgvector store
  (the fleet's `skmem-pg` on .158) + an embedding endpoint (mxbai), then reindex.

## Status / strategy
Strategic: the lowest-friction doorway into SKWorld memory for any Hermes user. Correctly a
**separate repo** per the host-plugin pattern. Mirror: smilinTux (private).
