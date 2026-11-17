# Changelog

All notable changes to `hermes-skmemory` (the plugin Hermes loads as `skmemory`).

Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
refer to the `version:` field in `plugin.yaml`, which is the only version this repo has:
there is no `pyproject.toml`, no tag series, and nothing published to a package index.
skmemory itself is versioned and published separately.

This file was reconstructed from git history on 2026-08-14. Entries before that date are
derived from commit messages and the diffs they carry, not from a log kept at the time.

## [Unreleased]

Nothing yet.

## [0.2.0] - 2026-11-17

Breaking: the plugin no longer runs skmemory inside Hermes. Install skmemory as its own
tool (`uv tool install skmemory` or `pipx install skmemory`) before upgrading. The removed
config keys below are ignored if still present.

### Changed

- **Transport.** The provider starts the `skmemory-mcp` stdio server as a child process,
  one per provider instance (so one per Hermes profile), and speaks MCP JSON-RPC to it
  (`McpClient`). It never imports skmemory. The server is found via config `mcp_command`,
  env `SKMEMORY_MCP_BIN`, `PATH`, then `~/.local/bin` and `~/.skenv/bin`.
- **Recall** uses the server's `memory_auto_context` with keywords extracted from the user
  message (token budget 800). The sensitivity filter that v0.1 ran in-process
  (`skmemory.hooks.sensitivity`) is now applied by the server, which drops sensitive-tagged
  rows from auto-injected context in owner turns.
- `skmemory_search` calls `memory_search` (limit capped at 20, result rows now include
  `tags`); `skmemory_store` and the per-turn `sync_turn` write call `memory_store`
  (per-turn: `layer: short-term`, tags `hermes` and `session:<id>`, `source: hermes`).
- **Isolated mode** gives the child an explicit `SKMEMORY_HOME` for
  `$HERMES_HOME/.skmemory/agents/<agent>/` and runs on skmemory's local tier only (flat
  JSON plus SQLite/FTS5, offline). **Linked mode** (`skcapstone_home` + `agent`) serves
  an existing skcapstone agent with the node's tiers; Hermes's `SKMEMORY_PG_DSN` is passed
  through, or config `dsn` is used.
- `is_available()` now checks that `skmemory-mcp` can be found, instead of
  `find_spec("skmemory")`.
- The plugin no longer sets process-wide `SKAGENT` / `SKCAPSTONE_HOME` in Hermes's own
  environment; those go to the child only.
- `plugin.yaml`: `version: 0.2.0`, `requires_hermes: ">=0.21.6"`, `provides_tools: []`
  (provider tools come through `get_tool_schemas()`), `optional_env` lists the four env
  fallbacks.
- Docs (`README.md`, `SOP.md`, `SECURITY.md`, `CONTRIBUTING.md`) rewritten for v0.2, with
  personal hostnames and paths removed.

### Added

- `queue_prefetch`: recall runs in a background thread and is consumed once by `prefetch`.
- `get_config_schema` / `save_config` for `hermes memory setup`.
- `backup_paths()` (returns nothing extra: the isolated store is already under
  `$HERMES_HOME`, so `hermes backup` covers it).
- Config keys `mcp_command`, `assume_owner` (env `SKMEMORY_ASSUME_OWNER`) and `timeout`
  (default 20 s per MCP call).
- A per-call timeout that returns a tool error, and restart of a dead child on the next call.
- Tests: `tests/test_provider.py` (20 tests against `tests/fake_skmemory_mcp.py`) and
  `tests/test_integration.py` (real `skmemory-mcp`, skipped when absent). These replace
  `tests/test_scope.py`.
- CI: a pytest step, and the AST contract check now also requires `queue_prefetch`,
  `get_config_schema`, `save_config` and `backup_paths` and fails on any `import skmemory`.

### Removed

- `pip_dependencies: [skmemory]`. skmemory is not installed into Hermes's venv.
- In-process use of `MemoryStore`, `PGVectorBackend` and `skmemory.hooks.sensitivity`.
- Config keys `vector_backend`, `embed_url`, `embed_model`, and env `SKMEMORY_VECTOR_BACKEND`,
  `SKMEMORY_DSN`, `SKMEMORY_EMBED_URL`, `SKMEMORY_EMBED_MODEL`. Search tiers are configured
  in skmemory. (`dsn` remains, now as the child's `SKMEMORY_PG_DSN`.)

### Security

- The child's environment is filtered: inherited `SKMEMORY_*`, `SKCAPSTONE_*`, `SKAGENT*`
  and `SK_DEFAULT_AGENT` are stripped before the plugin sets its own, so an isolated store
  can never open another agent's store on an skcapstone node.
- Owner and reader rules carried over: reader from `tools.session_scope.session_reader`;
  without it the CLI is the owner and a gateway is `@public`, unless `assume_owner` is set;
  scope errors fail closed to `@public`. Non-owner turns search under their reader and never
  write.
- New in `SECURITY.md`: the server runs with the user's privileges, the binary lookup order
  is a trust boundary, and `assume_owner` turns off the gateway fail-closed default.

## 0.1.x on master, never versioned (2026-08-14 to 2026-11-08)

These landed on `master` after 0.1.0 while `plugin.yaml` still said `0.1.0`. They describe
the in-process v0.1 design that 0.2.0 replaced.

### Security

- `prefetch()` and the `skmemory_search` tool now search under the Hermes session reader
  (`tools.session_scope.session_reader`, which honours `HERMES_MEMORY_OWNER` and
  `HERMES_MEMORY_OWNER_CHATS`) instead of always running an owner search. Auto-injected
  results drop memories flagged by `skmemory.hooks.sensitivity` (sacred, owner-only). Fails
  closed: no session scope inside a gateway means `@public`, an old skmemory without `reader`
  support returns nothing for a non-owner, and a missing sensitivity module injects nothing
  (SKMEM-P0-23).
- Write side of the same rule: `sync_turn()` and the `skmemory_store` tool only write in an
  owner session (session reader `None`). A group chat or non-owner DM is no longer snapshotted
  into the owner store. Fails closed on any scope error. There is no opt-in: skmemory's
  `snapshot()` cannot set an audience (`context_tag`) at write time, so a scoped non-owner
  write is not possible yet (SKMEM-P0-23).
- `conftest.py` stubs `agent.memory_provider` whenever Hermes core is not importable, so
  `pytest` runs without hermes-agent on the path.
- Tests in `tests/test_scope.py` (Hermes core stubbed), run by `pytest` from the repo root.
  (Replaced by `tests/test_provider.py` in 0.2.0.)

### Added

- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, and `LICENSE`
  (GPL-3.0-or-later), bringing the repo to SK_REPO_DOC_STANDARD.
- `.github/workflows/docs-check.yml`, the DOCS_FRESHNESS_STANDARD gate (tiers 1 and 2).
- `.github/workflows/ci.yml`: `compileall`, `ruff` on the full default rule set (pinned,
  currently clean), an **AST contract check** over the `MemoryProvider` implementation, and
  a `plugin.yaml` manifest check. Structural only. There is still no test suite, and the
  workflow says so.
- A `docs-evidence` block at the end of `SOP.md` pinning the entry point, the provider base
  class, the manifest fields, the config path, the dotted scaffold name, the tool names,
  and the version.

### Changed

- `SOP.md` rewritten to the canonical nine sections with an architecture diagram. The
  previous file was a 30 line stub with none of the required sections and no diagram: it
  passed a presence check while certifying nothing.
- `README.md` rewritten for accuracy and to state the maturity tier up front.

### Fixed (documentation)

- `README.md` said isolated mode creates a store under `~/.hermes/skmemory/agents/<agent>/`.
  It creates `~/.hermes/.skmemory/agents/<agent>/`, with a leading dot, and the dot is
  the whole point: a directory named `skmemory` in `$HERMES_HOME` shadows the installed
  package and breaks the plugin. That is what `a1a9143` fixed, and the README was never
  updated to match.
- Neither doc explained that `hermes plugins list` reports this plugin as **`not enabled`**
  while it is loaded and serving, because `kind: exclusive` plugins are skipped by the
  general scanner and loaded by the memory subsystem instead. Anyone debugging would
  misread that listing.
- Neither doc recorded that the plugin directory and manifest name is `skmemory`, not
  `hermes-skmemory`, nor that `memory.provider` rather than `plugins.enabled` is the
  activation lever.
- `README.md` claimed the core integration was "empirically validated" without saying it is
  validated by reading and observation only. There are no tests. `SOP.md` section 4 now
  states that plainly.

## [0.1.0]

### 2026-07-02, `a1a9143`

- **Fixed: out-of-the-box isolated mode was completely dead.** The bootstrap created a
  scaffold directory named `skmemory` under `$HERMES_HOME`. Because the gateway runs as
  `python -m hermes_cli.main` with `WorkingDirectory=$HERMES_HOME`, that directory landed
  on `sys.path[0]` and shadowed the installed `skmemory` package as a namespace package, so
  `from skmemory import MemoryStore` failed with "unknown location" and the store never
  initialised. Renamed to `.skmemory`, which cannot be a module name and therefore cannot
  shadow. Any checkout before this commit should be treated as broken, not merely old.

### 2026-06-12, `a147e7f`

- Read-write by default, and sessions write to memory: each completed turn is snapshotted
  as short-term memory tagged `hermes` and `session:<id>`. Gated to `primary` contexts so
  cron and subagent runs cannot pollute the store, and disabled entirely by `read_only`.
- The assistant turn is labelled with the configured agent name rather than a hardcoded
  one, since the plugin ships to any Hermes user.

### 2026-06-12, `0dacd18`

- Added **linked / shim mode**: `skcapstone_home` points at an existing skcapstone store
  and the plugin uses it as is, creating and overwriting nothing.
- Added `read_only`, which withholds the `skmemory_store` tool from the model entirely and
  refuses the call if it arrives anyway.

### 2026-06-12, `35ee863`

- Initial release. `SkmemoryProvider` over skmemory's `MemoryStore`, SQLite by default with
  an opt-in pgvector backend, the `skmemory_search` and `skmemory_store` tools, and config
  from `$HERMES_HOME/skmemory.json` plus `SKMEMORY_*` env vars.
