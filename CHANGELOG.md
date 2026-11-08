# Changelog

All notable changes to `hermes-skmemory` (the plugin Hermes loads as `skmemory`).

Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
refer to the `version:` field in `plugin.yaml`, which is the only version this repo has:
there is no `pyproject.toml`, no tag series, and nothing published to a package index.

This file was reconstructed from git history on 2026-08-14. Entries before that date are
derived from commit messages and the diffs they carry, not from a log kept at the time.

## [Unreleased]

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
