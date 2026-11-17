# Contributing to hermes-skmemory

One module, one job: put skmemory behind Hermes' `MemoryProvider` interface by running
`skmemory-mcp` as a child and speaking MCP to it. The bar is **not breaking someone's
memory store**, **not leaking memory across readers**, and **not shipping a doc that lies**.

Read `SOP.md` first, especially section 2 (why `hermes plugins list` says "not enabled",
and what environment the child gets) and section 4 (the tests).

## Ground rules

1. **Memory logic belongs to skmemory, not here.** Storage, search ranking, audience
   enforcement, sensitivity rules, embeddings and tiers all live in skmemory. This repo
   starts the server, picks a mode, decides who the turn is for, and calls
   `memory_auto_context`, `memory_search` and `memory_store`. If you need a new capability,
   add a server tool in skmemory first.
2. **Never import `skmemory`.** Not at module scope, not lazily, not in a test helper the
   plugin uses. No `pip_dependencies` in `plugin.yaml` either. CI fails on both.
3. **Keep the child env filter.** `_child_env` strips inherited `SKMEMORY_*`,
   `SKCAPSTONE_*`, `SKAGENT*` and `SK_DEFAULT_AGENT` before setting its own. Without that an
   isolated Hermes on an skcapstone node would open the node's store. If you add a new SK
   variable the child must not inherit, add its prefix to `_SK_ENV_PREFIXES` and a test.
4. **Treat the write gates as load-bearing.** `read_only`, the `primary`-only
   `agent_context` check and the owner-turn check are what stand between a Hermes session
   and someone's real memory store. Scope errors must keep failing closed to `@public`. If
   you touch any of them, say so loudly in the PR and add a test.
5. **Keep the isolated store at `$HERMES_HOME/.skmemory`.** The dotted name cannot shadow
   the package for anything that does import it, and existing stores live there.
6. **Never commit a real DSN.** Every example must stay a placeholder. See `SECURITY.md`.
7. **Fail soft at runtime, and say so.** A missing server is a `logger.warning` and a
   provider with no client; a failed call is a JSON error from the tool or an empty recall,
   never an exception into Hermes. Keep messages specific enough to grep for.
8. **No em dashes or en dashes** in any file, commit message, or PR body. Fleet style rule.
   Regular hyphens are fine.

## Development setup

No build and no packaging. You need Python 3.11+ and pytest; Hermes core is not required,
because `conftest.py` stubs `agent.memory_provider` when it is not importable.

```bash
git clone https://github.com/smilinTux/hermes-skmemory.git
cd hermes-skmemory
python -m pip install pytest 'ruff==0.15.4'
python -m compileall -q .
ruff check .
python -m pytest -q
```

`tests/test_provider.py` runs the provider against `tests/fake_skmemory_mcp.py`, a fake
server that logs every call and its startup env to a JSON-lines file, so tests assert on
exactly what was sent. `FAKE_MCP_MODE=hang` and `die` exercise the timeout and restart
paths. Add a test there for any behaviour change.

`tests/test_integration.py` runs the real `skmemory-mcp` in isolated mode against a temp
`HERMES_HOME`. It is skipped unless the server is found (`SKMEMORY_MCP_BIN`, `PATH`, or
`~/.skenv/bin`). To run it, install skmemory as a tool first:

```bash
uv tool install skmemory          # or: pipx install skmemory
python -m pytest -q tests/test_integration.py
```

To try it inside a real Hermes, use a throwaway profile or `HERMES_HOME` and an **isolated**
store, never a linked store you care about:

```json
{ "agent": "devtest" }
```

Then restart Hermes and look for `skmemory: ready (mode=isolated, agent=devtest, ...)` in
the log.

Never develop in `$HERMES_HOME/plugins/skmemory/`. That is the installed copy, and edits
there are lost on the next update. Work in a separate clone or worktree.

## Before you open a PR

```bash
python -m compileall -q .
ruff check .
python -m pytest -q
python3 <path-to-sk-standards>/scripts/docs_check.py --repo . --tier 1 --tier 3
```

The last one executes the `docs-evidence` block at the end of `SOP.md`. If you renamed an
entry point, moved the config path, changed a tool name, changed the env filter or the
default timeout, or bumped `version:` in `plugin.yaml`, it goes red and the SOP needs
updating in the same PR. That is the point of it.

- Update `CHANGELOG.md` for anything a user or operator would notice.
- If you change an entry point, a provider method, a path, or the manifest, update the
  matching `docs-evidence` check and the AST contract and manifest checks in `ci.yml`.
- On a version bump, change both `plugin.yaml` `version:` and `clientInfo.version` in
  `McpClient._start`.

## Commit and PR conventions

- Conventional-commit prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `ci:`, `test:`.
- A `Co-Authored-By` trailer names whoever actually wrote the change, human or agent.
  Never add one you cannot evidence.
- Branch from `master` (this repo's default branch is `master`, not `main`).
- One logical change per PR. Docs-only changes stay docs-only.
- **Never push a tag** from a working session, and never push directly to `master`.
- Say what you verified and what you did not. An honest "unverified" list is worth more
  than a confident guess.

## Deploying a merged change

A merge does not deploy anything. Each Hermes profile has its own installed copy:

```bash
cd "$HERMES_HOME/plugins/skmemory"
git log --oneline -1 && git status --short   # confirm what is actually there first
git checkout master && git pull --ff-only
# restart Hermes, then:
journalctl --user -u hermes-gateway -n 200 | grep 'skmemory: ready'
```

Repeat for every profile that uses the plugin.

## Reporting security issues

Do not open a public issue. See `SECURITY.md`, and read the linked-plus-writable and
`assume_owner` sections before you deploy anything.

## Code of conduct

See `CODE_OF_CONDUCT.md`.
