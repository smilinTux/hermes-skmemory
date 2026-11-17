# hermes-skmemory

**[skmemory](https://github.com/smilinTux/skmemory) as a
[Hermes Agent](https://github.com/NousResearch/hermes-agent) memory provider, with skmemory
kept outside Hermes.** **Maturity tier: T0, N/A (no key material).**

The plugin starts the `skmemory-mcp` stdio server as a child process and talks to it over
MCP. It never imports skmemory, binds no port and has no CLI. Not a crypto component; in
linked mode it can pass a Postgres DSN (which contains a password) through to the child.
**License:** GPL-3.0-or-later. **Operational contract:** [`SOP.md`](./SOP.md).

The default needs no infrastructure: a private store inside `$HERMES_HOME` on skmemory's
local tier (flat JSON files plus SQLite/FTS5), offline, no Postgres, no embedding server.
Linked mode serves an existing skcapstone agent with whatever tiers that node has.

Where this fits: the SK estate itself runs the "enterprise" edition, where skmemory is its
own service that agents reach over MCP or the CLI. This plugin is the "inside Hermes" path,
used by the per-user sksidekick edition and by anyone installing from the Hermes plugin
catalog.

> ### Two things that will confuse you first
>
> **1. `hermes plugins list` reports this plugin as `not enabled` even while it is loaded
> and serving every turn.** That is expected. `kind: exclusive` plugins are skipped by the
> general plugin scanner and loaded by the memory subsystem instead. The lever that
> actually activates it is `memory.provider: skmemory` in `$HERMES_HOME/config.yaml`,
> **not** `plugins.enabled`. See [`SOP.md`](./SOP.md) section 2.
>
> **2. The repo is `hermes-skmemory`; the thing Hermes loads is `skmemory`.** The manifest
> name and the installed directory are both `skmemory`, and that is the name
> `memory.provider` refers to. It is also the name of the separate skmemory package the
> plugin runs as a child, which is a different thing.

## Install

skmemory is installed **outside** Hermes's venv, as its own tool, so `skmemory-mcp` ends up
on `PATH`. The plugin declares no `pip_dependencies` on purpose.

```bash
uv tool install skmemory            # or: pipx install skmemory
hermes plugins install https://github.com/smilinTux/hermes-skmemory
# lands at $HERMES_HOME/plugins/skmemory (directory name = manifest name)
hermes memory setup                 # pick "skmemory", answer the config prompts
# or set memory.provider: skmemory in $HERMES_HOME/config.yaml by hand
```

Then restart Hermes (the gateway service, or a new CLI session). Requires Hermes
`>=0.21.6` (`requires_hermes` in `plugin.yaml`).

The server is looked up in this order: config `mcp_command`, env `SKMEMORY_MCP_BIN`, `PATH`,
then `~/.local/bin/skmemory-mcp` and `~/.skenv/bin/skmemory-mcp`.

### Confirm it came up

Look for this line in the Hermes log (for the gateway, `journalctl --user -u
hermes-gateway`):

```
skmemory: ready (mode=isolated, agent=hermes, read_only=False, server=/path/to/skmemory-mcp)
```

It is the only positive confirmation. If `skmemory-mcp` cannot be found, `initialize()`
logs `skmemory: skmemory-mcp not found. ...` as a warning and Hermes runs on with no memory,
so silence means broken, not fine. The child itself starts lazily on the first memory call.

## Two modes

**Isolated (default).** A private store at `$HERMES_HOME/.skmemory/agents/<agent>/`. The
child gets an explicit `SKMEMORY_HOME` for that store, and every inherited `SKMEMORY_*`,
`SKCAPSTONE_*`, `SKAGENT*` and `SK_DEFAULT_AGENT` variable is stripped from its environment,
so a Hermes running on an skcapstone node can never open someone else's store. Local tier
only: flat JSON plus SQLite/FTS5, offline. The store lives inside `$HERMES_HOME`, so
`hermes backup` already covers it. (The leading dot is deliberate: `$HERMES_HOME` can be on
`sys.path`, and a directory named `skmemory` there would shadow the package for anything
that does import it.)

**Linked.** Set `skcapstone_home` and `agent` to serve an existing skcapstone agent, with
the tiers that node has (pgvector, BM25, graph). `SKMEMORY_PG_DSN` from Hermes's
environment is passed through to the child, or set `dsn` in the config file. The plugin
creates nothing in the target store; it warns if the agent directory is missing.

```json
{ "skcapstone_home": "~/.skcapstone", "agent": "myagent", "read_only": true }
```

> **Pair linked mode with `read_only: true` unless you mean it.** Without it, the model can
> write into that store with `skmemory_store`, and every completed owner turn is written
> into it automatically. [`SECURITY.md`](./SECURITY.md) explains what that exposes,
> including memory as a persistent prompt-injection surface.

## Config: `$HERMES_HOME/skmemory.json` (all keys optional)

| Key | Env fallback | Default | Meaning |
|---|---|---|---|
| `agent` | none | `hermes` | store name (isolated) or agent name (linked) |
| `skcapstone_home` | `SKMEMORY_LINK_HOME` | `""` | non-empty selects **linked** mode |
| `read_only` | `SKMEMORY_READ_ONLY` | `false` | recall only: no store tool, no per-turn writes |
| `mcp_command` | `SKMEMORY_MCP_BIN` | found on `PATH` | path to `skmemory-mcp` |
| `dsn` | none (linked mode passes `SKMEMORY_PG_DSN` through) | `""` | Postgres DSN for the child. **Contains a password.** |
| `assume_owner` | `SKMEMORY_ASSUME_OWNER` | `false` | single-user install on stock Hermes: every turn is the owner's |
| `timeout` | none | `20` | seconds per MCP call |

File values win over env values; an empty string or `null` in the file is ignored.
`hermes memory setup` writes `agent`, `skcapstone_home`, `read_only` and `assume_owner`
through the provider's `get_config_schema`/`save_config`. **Never commit a real DSN.**

Removed in 0.2.0: `vector_backend`, `embed_url`, `embed_model` and the env vars
`SKMEMORY_VECTOR_BACKEND`, `SKMEMORY_DSN`, `SKMEMORY_EMBED_*`. Search tiers are now
whatever the skmemory server has; the plugin does not configure them.

## Tools exposed to the model

- `skmemory_search(query, limit)`: recall facts, preferences, context (server tool
  `memory_search`, limit capped at 20).
- `skmemory_store(content, title?, tags?)`: persist something durable (server tool
  `memory_store`). **Withheld entirely when `read_only` is true.**

**Recall before each turn.** Keywords are extracted from the user message and sent to the
server's `memory_auto_context`; the results are injected as "Relevant memories".
`queue_prefetch` runs this in the background so the next turn does not wait.

**Sessions write to memory.** Each completed primary-context owner turn is stored as a
short-term memory tagged `hermes` and `session:<id>`, with `source: hermes`. Cron and
subagent contexts never write.

## Owner and reader rules

A turn is an owner turn only when Hermes says so:

- On Hermes builds with session scopes (`tools.session_scope.session_reader`, sk builds),
  the reader comes from there.
- Without it, the local CLI is the owner and a messaging gateway is `@public`, unless
  `assume_owner: true` is set for a single-user install.
- Any error resolving the scope fails closed to `@public`.

Non-owner turns search under their reader (passed to the server as `reader`) and never
write, neither per-turn nor through `skmemory_store`. In owner turns the server drops
sensitive-tagged rows from auto-injected context, which covers sacred and owner-only
memories; per the server they remain reachable by an explicit `skmemory_search`.

## Status

**v0.2.0, tested, not currently deployed on the SK estate.** `tests/` holds 20 provider
tests against a fake MCP server and one end-to-end test against a real `skmemory-mcp`
(skipped when it is not installed). CI runs compileall, ruff, an AST contract check, a
manifest check and pytest. Incubating: expect breaking changes.

## Docs

[`SOP.md`](./SOP.md) · [`SECURITY.md`](./SECURITY.md) · [`CONTRIBUTING.md`](./CONTRIBUTING.md) ·
[`CHANGELOG.md`](./CHANGELOG.md) · [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md)

## Related projects / See also

- **Depends on:** [skmemory](https://github.com/smilinTux/skmemory), the memory system and
  the `skmemory-mcp` server this plugin runs as a child.
- **Depends on:** [Hermes Agent](https://github.com/NousResearch/hermes-agent), whose
  `MemoryProvider` interface this implements.
- **Sibling:** [skcapstone](https://github.com/smilinTux/skcapstone), the agent framework
  whose agent homes linked mode serves.
- **Standards:** [sk-standards](https://github.com/smilinTux/sk-standards), the doc/SOP and
  version standards this repo follows.
