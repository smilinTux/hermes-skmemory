# Contributing to hermes-skmemory

One file, one class, one job: put skmemory behind Hermes' `MemoryProvider` interface. The
bar is **not breaking a live memory store** and **not shipping a doc that lies**.

Read `SOP.md` first, especially section 2 (why `hermes plugins list` says "not enabled")
and section 4 (there are no tests, and what that means for you).

## Ground rules

1. **Memory logic belongs to skmemory, not here.** If you are implementing storage,
   search ranking, consolidation, or an embedding call, it belongs in skmemory. This repo
   constructs a `MemoryStore` and calls `search()` and `snapshot()`.
2. **Never import `skmemory` at module scope, and never in `is_available()`.** Its package
   import calls `get_agent_paths()` and hard-raises when no scaffold exists.
   `is_available()` uses `find_spec` for exactly that reason (`__init__.py:96-98`), and
   `initialize()` imports only after the scaffold and env vars are in place.
3. **Never name a directory `skmemory` under `$HERMES_HOME`.** The gateway's CWD is
   `$HERMES_HOME` and lands on `sys.path[0]`, so such a directory shadows the installed
   package and `from skmemory import MemoryStore` fails with "unknown location". The
   scaffold is `.skmemory`, dotted, because a dotted name can never be a module name. This
   killed out-of-the-box mode once already (`a1a9143`).
4. **Treat the write gates as load-bearing.** `read_only` and the `primary`-only
   `agent_context` check are the only things standing between a Hermes session and someone's
   real memory store. If you touch them, say so loudly in the PR.
5. **Never commit a real DSN.** Every example must stay a placeholder. See `SECURITY.md`.
6. **Fail soft at runtime, and say so.** Every failure path here is a `logger.warning` plus
   a degraded provider, never an exception. Keep it that way, and keep the warning message
   specific enough to grep for.
7. **No em dashes or en dashes** in any file, commit message, or PR body. Fleet style rule.
   Regular hyphens are fine.

## Development setup

No build, no virtualenv of its own, and nothing to run locally end to end without a
Hermes install:

```bash
git clone git@github.com:smilinTux/hermes-skmemory.git
cd hermes-skmemory
python -m compileall -q .          # what CI runs
ruff check .                       # what CI runs; currently clean on the default rules
```

`__init__.py` **cannot be imported outside the gateway process**: line 35 does
`from agent.memory_provider import MemoryProvider`, and `agent` is Hermes core, which is
not on PyPI. That is why CI inspects the module with `ast` instead of importing it.

To exercise it for real you need a Hermes node with `memory.provider: skmemory`. Test
against an **isolated** store, never against a linked `~/.skcapstone`:

```json
{ "agent": "devtest", "vector_backend": "sqlite" }
```

Then `systemctl --user restart hermes-gateway` and confirm
`journalctl --user -u hermes-gateway | grep 'skmemory: initialized'`.

Never develop in `~/.hermes/plugins/skmemory/`. That is the **deployed** copy, it is a
separate clone, and edits there are lost on the next pull. Use a worktree under
`~/skworld-worktrees/`.

## Tests are the standing invitation

This repo has **no tests at all**. If you are looking for the highest-value contribution,
it is this. `SOP.md` section 4 lists six concrete unit tests in priority order, all of them
pure and none needing Postgres, an embed endpoint, or Hermes core beyond a stubbed
`MemoryProvider` base. Start with the `read_only` gate and the `.skmemory` scaffold name;
those are the two silent regressions that would hurt most.

If you add a `tests/` directory, add a pytest job to `.github/workflows/ci.yml` in the same
PR, and only after you have watched it pass.

## Before you open a PR

```bash
python -m compileall -q .
ruff check .
python3 <path-to>/docs_check.py --repo . --tier 1 --tier 3
```

The last one executes the `docs-evidence` block at the end of `SOP.md`. If you renamed an
entry point, moved the config path, changed a tool name, or bumped `version:` in
`plugin.yaml`, it goes red and the SOP needs updating in the same PR. That is the point of
it.

- Update `CHANGELOG.md` for anything a user or operator would notice.
- If you change an entry point, a path, or the manifest, update the matching
  `docs-evidence` check and the AST contract check in `ci.yml`.

## Commit and PR conventions

- Conventional-commit prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `ci:`, `test:`.
- Branch from `master` (this repo's default branch is `master`, not `main`).
- One logical change per PR. Docs-only changes stay docs-only.
- **Never push a tag** from a working session, and never push directly to `master`.
- Say what you verified and what you did not. An honest "unverified" list is worth more
  than a confident guess, and in a repo with no tests it is the only assurance there is.

## Deploying a merged change

A merge does not deploy anything, and there are **two** copies on the live node:

```bash
cd ~/.hermes/plugins/skmemory
git log --oneline -1 && git status --short   # confirm what is actually there first
git checkout master && git pull --ff-only
systemctl --user restart hermes-gateway
journalctl --user -u hermes-gateway -n 200 | grep 'skmemory: initialized'
```

The second copy is `~/.hermes/profiles/opus/plugins/skmemory/`, a plain non-git copy for
the `hermes-gateway-opus` profile (currently disabled). A `git pull` will not touch it.

## Reporting security issues

Do not open a public issue. See `SECURITY.md`, and note the linked-plus-writable section
before you deploy anything.

## Code of conduct

See `CODE_OF_CONDUCT.md`.
