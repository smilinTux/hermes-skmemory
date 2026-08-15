# Security Policy

`hermes-skmemory` is a Hermes Agent memory provider loaded **in-process** by
`hermes-gateway.service`. It has no network listener and no CLI. Its security relevance is
in three places: it runs inside the gateway's process, it **reads a database password**
from configuration, and in linked mode it can **write into a real, shared memory store**.

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
- How this plugin resolves, holds, and passes the `dsn` credential.
- The mode selection and write-gating logic: `read_only`, `agent_context`,
  isolated versus linked.
- The scaffold bootstrap and its effect on `sys.path` and on process-wide environment.
- Anything this plugin can do to the gateway process it is loaded into.

**Out of scope**

- Hermes Agent core: the plugin loader, the memory subsystem, `agent.memory_provider`.
  Report upstream.
- **skmemory itself**, including `MemoryStore` and `PGVectorBackend`. Both live faults
  documented in `SOP.md` section 8 are raised by skmemory modules. Report them in the
  skmemory repo; we will track and bump.
- Postgres, pgvector, and the embedding server. This repo passes a URL and a DSN through;
  it does not operate those services.
- Findings that need an attacker to already have the operator's shell on the node. At that
  point `~/.hermes/skmemory.json` is readable directly and this plugin is not the weakest
  link.

## Supported versions

| Version | Status | Fixes |
|---|---|---|
| `master` (currently `0.1.0` per `plugin.yaml`) | Active, incubating | Yes |
| Any earlier commit | Unsupported | Update to `master` |

There are no published artifacts, no tag series, and nothing on PyPI, so "supported" means
the tip of `master`. Per VERSION_LIFECYCLE this repo is **Incubating** and expected to
break.

Note that `a1a9143` (2026-07-02) fixed a total failure of out-of-the-box isolated mode. Any
checkout before it should be treated as broken, not merely old.

## Credential handling

**This repo stores no secret and must never store one.** It does, however, **read** one.

`cfg["dsn"]` is a full Postgres connection string including a password. It is resolved
from `SKMEMORY_DSN` (`__init__.py:112`) or from `$HERMES_HOME/skmemory.json`
(`__init__.py:116`), and passed to skmemory's `PGVectorBackend` at `__init__.py:198`. The
live node holds a real DSN in `~/.hermes/skmemory.json`.

Consequences to be aware of:

- **The credential is at rest in a plain JSON file** in the operator's home directory,
  protected only by filesystem permissions. Check them: `ls -l ~/.hermes/skmemory.json`.
  It is not sourced from `skvault` and it is not encrypted.
- **Never commit a real DSN to this repo.** The examples in `README.md` and `SOP.md` are
  placeholders and must stay placeholders. `SOP.md` deliberately prints the live config
  with the credential elided.
- **The DSN can reach the logs.** This repo does not log `cfg`, but skmemory's own error
  paths have printed connection details: the `password authentication failed for user
  "postgres"` warnings in the journal name the user and the host. Treat the gateway
  journal as containing connection metadata.
- If that credential is ever exposed, rotate the Postgres role first, prove the new
  credential works, and only then revoke the old one.

`.github/workflows/secret-scan.yml` runs the **gitleaks binary** over the full history on
every push and pull request and fails the build on a finding. The binary rather than
`gitleaks-action`, because that wrapper needs a paid licence for organization-owned repos
and exits before scanning anything: a check that cannot scan is worse than no check, since
a permanently red gate gets ignored. Never allowlist a real finding.

## The linked plus writable risk (read this before deploying)

`README.md` recommends pairing linked mode with `read_only: true`. **The live node does the
opposite.** As of 2026-08-14, `~/.hermes/skmemory.json` on `noroc2027` is:

```json
{ "skcapstone_home": "/home/cbrd21/.skcapstone", "agent": "lumina", "read_only": false, ... }
```

That is linked directly at the real sovereign memory store, **writable**. What it means
concretely:

- The model can call `skmemory_store` and place arbitrary content into Chef's live
  `lumina` store.
- Every completed primary-session turn is snapshotted into that store automatically
  (`__init__.py:232-256`), carrying both sides of the conversation, truncated at 4000
  characters.
- **Anyone who can talk to the agent can therefore write into the memory store**, indirectly
  via conversation and directly by asking it to remember something. Content arriving over
  Telegram or any other Hermes platform reaches the same path.
- That makes memory a **prompt-injection surface with persistence**. Text a third party
  gets the agent to store is read back into future sessions by `prefetch`.

This is an operator decision, not a defect, and the write path is gated to `primary`
contexts so cron and subagent runs cannot pollute the store. But it should be a **conscious**
decision. If recall is what you want and writes are not, set `"read_only": true` and
restart the gateway. `get_tool_schemas()` then withholds the store tool entirely and
`handle_tool_call` refuses it (`__init__.py:260`, `:274`).

## Threat model

| Asset | Threat | Control |
|---|---|---|
| The sovereign memory store | Unwanted or hostile writes in linked mode | `read_only: true` withholds the tool and blocks writes. **Currently off on the live node**; see above. |
| The sovereign memory store | A cron job or subagent writing its system prompt into memory | `sync_turn` writes only when `agent_context == "primary"` (`__init__.py:131-135`). |
| The sovereign memory store | Isolated mode accidentally touching a shared store | Isolated mode writes only under `$HERMES_HOME/.skmemory`. Linked mode creates nothing and warns on a missing agent dir (`__init__.py:144-148`). |
| Database credential | Disclosure at rest | Filesystem permissions on `~/.hermes/skmemory.json` only. No vault integration. Documented, not solved. |
| Gateway process integrity | A memory provider running with full gateway privileges | Activation is explicit: `memory.provider: skmemory` in `config.yaml`. Installing and selecting it is a deliberate operator trust decision. |
| Gateway availability | The provider crashing the gateway | Every path is defensive. `initialize()` warns and leaves `self.store = None` rather than raising, `handle_tool_call` returns a JSON error instead of throwing, and `is_available()` uses `find_spec` and never imports skmemory (whose package import hard-raises without a scaffold). |
| Search quality, silently | A backend failure degrading recall without any visible break | **Not controlled, and this is the real risk.** skmemory catches a vector-search failure and falls back to text search with a warning. Recall gets worse and nothing looks broken. It ran that way for six days in August 2026. Watch the journal, not the UI. |
| Process-wide environment | `SKCAPSTONE_HOME` and `SKAGENT` set globally at `__init__.py:169-170` | Single provider per process by design (`kind: exclusive`). Two would fight. |

## What this repo does NOT claim

- No cryptography of any kind. No signing, no encryption, no key material.
- **No tests exist in this repo.** Not a thin suite: none. Nothing here is proven by an
  automated behavioural check, including the `read_only` gate this document leans on. CI
  runs structural checks only. See `SOP.md` section 4.
- No independent security review. Sovereign, self-built software.
- Memory content is **not** authenticated or attributed beyond a `source="hermes"` tag and
  a session id. A stored memory does not prove who caused it to be stored.
