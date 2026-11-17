# Security Policy

`hermes-skmemory` is a Hermes Agent memory provider. It runs inside the Hermes process and
starts the `skmemory-mcp` server as a **child process**, talking to it over stdio. It has
no network listener and no CLI. Its security relevance is in four places: it decides **who
a turn is for** (owner or a scoped reader), it decides **what environment the child gets**,
it may **pass a database password** to the child, and in linked mode it can **write into
a real, shared memory store**.

**Maturity tier:** T0, N/A (no key material). **Canonical home:** this file.

## Reporting a vulnerability

Report privately. Do **not** open a public issue for a security bug.

- **Primary:** GitHub private vulnerability reporting for this repo
  (`Security ▸ Report a vulnerability`).
- **Secondary:** a PGP-encrypted report to the SKWorld security contact via the CapAuth
  identity, or the smilinTux / SKWorld maintainers through the SKCapstone coordination
  channel.

Include the affected commit, a reproduction, and the impact observed.

**Acknowledgement SLA: within 72 hours.** Silence past that window means the report did
not reach us, not that it was dismissed. Please chase it.

## Scope

**In scope**

- `__init__.py`, `plugin.yaml`, and anything else in this repo.
- The owner/reader decision (`_session_reader`, `assume_owner`) and the write gates
  (`read_only`, `agent_context`, owner turn).
- The environment handed to the child (`_child_env`), and how `dsn` is resolved and passed.
- The MCP client (`McpClient`): framing, timeouts, restart, process cleanup.
- Mode selection: isolated versus linked.
- Anything this plugin can do to the Hermes process it is loaded into.

**Out of scope**

- Hermes Agent core: the plugin loader, the memory subsystem, `agent.memory_provider`,
  `tools.session_scope`. Report upstream.
- **skmemory itself**, including `skmemory-mcp`, its reader/audience rules, the sensitivity
  filter and its storage tiers. Report in the
  [skmemory repo](https://github.com/smilinTux/skmemory).
- Postgres, pgvector, and any embedding server skmemory talks to.
- Findings that need an attacker to already have the operator's shell. At that point
  `$HERMES_HOME/skmemory.json` and the store files are readable directly and this plugin is
  not the weakest link.

## Supported versions

| Version | Status | Fixes |
|---|---|---|
| `master` (currently `0.2.0` per `plugin.yaml`) | Active, incubating | Yes |
| `0.1.x` (in-process skmemory) | Unsupported | Update to `master` |

There are no published artifacts, no tag series, and nothing on PyPI for this plugin, so
"supported" means the tip of `master`. Per VERSION_LIFECYCLE this repo is **Incubating**
and expected to break.

## Credential handling

**This repo stores no secret and must never store one.** It may, however, **pass** one.

A Postgres DSN (with password) reaches the child as `SKMEMORY_PG_DSN` in two ways: config
`dsn` in `$HERMES_HOME/skmemory.json` (any mode), or, in linked mode only, Hermes's own
`SKMEMORY_PG_DSN` passed through. Isolated mode with no `dsn` passes none, and strips any
inherited one.

- **A DSN in `skmemory.json` is at rest in a plain JSON file**, protected only by filesystem
  permissions. Check them: `ls -l "$HERMES_HOME/skmemory.json"`. Prefer the environment
  route for linked mode.
- **Never commit a real DSN to this repo.** Every example must stay a placeholder.
- **The plugin does not log its config or the child env.** The child's stderr is discarded,
  so skmemory's own error output does not reach the Hermes log through this plugin.
- If a credential is ever exposed, rotate the Postgres role first, prove the new
  credential works, and only then revoke the old one.

`.github/workflows/secret-scan.yml` runs the **gitleaks binary** over the full history on
every push and pull request and fails the build on a finding. The binary rather than
`gitleaks-action`, because that wrapper needs a paid licence for organization-owned repos
and exits before scanning anything: a check that cannot scan is worse than no check, since
a permanently red gate gets ignored. Never allowlist a real finding.

## The child process

- **It runs with the user's privileges.** `skmemory-mcp` is whatever binary the lookup finds:
  config `mcp_command`, env `SKMEMORY_MCP_BIN`, `PATH`, then `~/.local/bin` and
  `~/.skenv/bin`. Anyone who can write to `skmemory.json`, the Hermes environment, a `PATH`
  directory or those two fallback paths can make Hermes run their code. That is the same
  trust boundary as Hermes's own `PATH`, but it is real: on a shared machine, set an
  absolute `mcp_command` owned by the user.
- **It inherits a filtered environment.** Every inherited `SKMEMORY_*`, `SKCAPSTONE_*`,
  `SKAGENT*` and `SK_DEFAULT_AGENT` variable is removed, then the plugin sets its own agent
  selectors and, in isolated mode, an explicit `SKMEMORY_HOME`. This is what stops a Hermes
  running on an skcapstone node from opening that node's store in isolated mode. **All other
  variables are inherited unchanged**, including API keys and tokens in Hermes's
  environment. skmemory is trusted with them the same way Hermes is.
- **It is bounded in time, not in resources.** Each call has a timeout (default 20 s); on
  timeout or exit the child is closed and restarted on the next call. There is no memory or
  CPU limit on the child.
- **It is stopped with the provider.** `shutdown()` closes stdin and terminates the child,
  killing it after 5 s.

## Owner turns, readers and writes

A turn is an owner turn only when Hermes says so. On sk Hermes builds the reader comes from
`tools.session_scope.session_reader()`. On stock Hermes the CLI is the owner and a gateway
process (`_HERMES_GATEWAY=1`) is `@public`, unless `assume_owner` is set. Any error fails
closed to `@public`.

- Non-owner turns pass their `reader` to the server, which enforces the audience. They
  never write: not per-turn, not through `skmemory_store`.
- **`assume_owner: true` turns that safety off.** On a stock Hermes gateway it makes every
  sender the owner, with full recall and write access. Use it only on a single-user install
  where you are the only person who can reach the agent.
- In owner turns the server drops sensitive-tagged rows from auto-injected context;
  an explicit `skmemory_search` can still return them.

## The linked plus writable risk (read this before deploying)

Linked mode with `read_only: false` means:

- The model can call `skmemory_store` and place arbitrary content into that agent's store.
- Every completed primary-context owner turn is written there automatically, both sides of
  the conversation, truncated at 4000 characters.
- **Anyone the plugin treats as the owner can therefore write into the store**, indirectly
  via conversation and directly by asking the agent to remember something.
- That makes memory a **prompt-injection surface with persistence**. Text that gets stored
  is read back into future sessions by `prefetch`, and content from tools and web pages the
  agent read during an owner turn can end up in a per-turn write.

The write path is gated to `primary` contexts (cron and subagents cannot write) and to owner
turns. If recall is what you want and writes are not, set `"read_only": true` and restart:
`get_tool_schemas()` then withholds the store tool and `handle_tool_call` refuses it.

## Threat model

| Asset | Threat | Control |
|---|---|---|
| Someone else's memory store | An isolated Hermes on an skcapstone node opening the node's store via inherited env | `_child_env` strips `SKMEMORY_*`, `SKCAPSTONE_*`, `SKAGENT*`, `SK_DEFAULT_AGENT` and sets an explicit `SKMEMORY_HOME`. Tested (`test_isolated_mode_child_env`). |
| The memory store | Unwanted or hostile writes in linked mode | `read_only: true` withholds the tool and blocks writes. Off by default; a deliberate choice. |
| The memory store | A non-owner (group chat, public DM) writing as the owner | Writes only in owner turns; scope errors fail closed to `@public`. Tested. Weakened by `assume_owner`. |
| Owner-only memories | Leaking into a shared room via recall | Non-owner turns search under their reader; the server enforces it. Sensitive tags are not auto-injected even for the owner. |
| The memory store | A cron job or subagent writing its system prompt into memory | `sync_turn` writes only when `agent_context == "primary"`. Tested. |
| Database credential | Disclosure at rest or in transit to the child | Filesystem permissions on `skmemory.json`; env-only route available. Passed to the child by environment, not argv. No vault integration. |
| Hermes process | A malicious or swapped `skmemory-mcp` binary | Lookup order is documented; pin an absolute `mcp_command`. The child runs as the user. Not otherwise controlled. |
| Hermes availability | A hung or crashing server | Per-call timeout, restart on next call, every failure returned as a JSON error. `is_available()` only looks for the binary. |
| The isolated store | Loss | Lives under `$HERMES_HOME/.skmemory`, so `hermes backup` covers it. |

## What this repo does NOT claim

- No cryptography of any kind. No signing, no encryption, no key material.
- The test suite (`tests/`) covers the gates above against a fake server, plus one
  end-to-end test against a real `skmemory-mcp` that is skipped in CI. It does not prove
  skmemory's own reader enforcement; that is skmemory's job and its test suite.
- No independent security review. Sovereign, self-built software.
- Memory content is **not** authenticated or attributed beyond a `source: hermes` tag and
  a session id. A stored memory does not prove who caused it to be stored.
