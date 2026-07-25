# Development Guide

Repo-specific dev conventions for repo-shared.

The companion [DEVELOPMENT_SHARED.md](DEVELOPMENT_SHARED.md) holds the
cross-repo conventions; this file layers repo-shared specifics on top. **This
file takes precedence over `DEVELOPMENT_SHARED.md` on conflict.**

## Repo layout

Two top-level concerns:

- `shared/` is the upstream content that gets vendored into consumer repos. It
  has different kinds, each handled by `vendor.py`'s `iter_shared` /
  `consumer_paths`:
  - `shared/files/` -> vendored at `_repo_shared/files/` PLUS canonical-path
    symlinks at the consumer root (`DEVELOPMENT_SHARED.md`,
    `_repo_shared/repo-shared`, ...).
  - `shared/dotfiles/` -> vendored at `_repo_shared/dotfiles/` PLUS
    dot-prefixed canonical-path symlinks (`.markdownlint.json`,
    `.markdownlint-cli2.jsonc`, ...).
  - `shared/templates/` and `shared/dottemplates/` -> vendored at
    `_repo_shared/<kind>/` PLUS a canonical-path *copy* (not a symlink) that
    `vendor()` seeds when the consumer has no file there. On a later `upgrade`
    the copy is refreshed to the new upstream only while it still byte-matches
    the *old* upstream (the previous vendored content) -- so a repo that never
    customizes the file keeps getting the latest version, while a customized
    copy is left untouched. Used for files the consumer must own a real copy
    of: `gitignore` (`git` won't follow a symlinked `.gitignore`) and
    `CLAUDE.md` (Claude resolves `@`-includes relative to the file's real
    on-disk location, so a symlinked `CLAUDE.md` would resolve its includes
    against the vendor dir). The delivered `test_in_sync.py` gates each copy
    against its upstream so a customized copy can't silently fall behind
    unnoticed; `init` / `upgrade` enforce the same invariant up front via
    `check_in_sync` and abort with ERROR on any out-of-sync entry. A consumer
    that wants its own version lists the path in `.repo-shared-ignore`, which
    exempts it from the seed, the auto-update, and the sync check. The vendored
    copy under `_repo_shared/<kind>/` still backs the drift gate.
  - `shared/tests/` -> vendored at `_repo_shared/tests/` with NO canonical-path
    symlinks. The delivered test files (`test_code_quality.py`,
    `test_markdown_format.py`, `test_markdownlint.py`,
    `test_repo_shared_drift.py`, `test_in_sync.py`) live solely at their
    vendored path; pytest finds them via the `testpaths` entry that
    `_inject_shared_testpaths` appends to the consumer's
    `[tool.pytest.ini_options]`. This puts the shared tests on a separate
    ancestor chain from the consumer's `tests/conftest.py`, so a heavy consumer
    conftest can't leak into the delivered tests.
- `src/epilatow_repo_shared/` is the Python package. It ships `shared/` as
  package data so the CLI's vendor logic and the drift tests can read the
  canonical content directly via `importlib.resources`.

The repo's own root-level files dogfood the same mechanism: the symlinked kinds
(`DEVELOPMENT_SHARED.md`, `.markdownlint.json`, ...) are symlinks into
`shared/`, while `CLAUDE.md` and `.gitignore` are real committed copies of
their `shared/templates/` / `shared/dottemplates/` upstreams. Editing a
symlinked upstream under `shared/` updates the repo-local view as a side
effect; the template copies are independent committed files, gated against
their upstreams by the delivered `test_in_sync.py` -- `InSyncBase` calls
`check_in_sync` against `package_shared_root()`, which resolves to the live
`shared/` here, so repo-shared dogfoods the same check every consumer runs.

Repo-local files that are NOT shared (`README.md`, this file) are real files at
the root. `DEVELOPMENT.md` is also a real file -- the `shared/files/` upstream
is a placeholder template -- so the repo's own `.repo-shared-ignore` lists it
to opt the canonical path out of the in-sync gate. `DEVELOPMENT_AGENT.md` keeps
the symlink to the shared placeholder since this repo has no agent-only
specifics worth promoting to a real file.

## Testing

```bash
uv run --extra test pytest                  # full suite (unit + dogfood)
uv run --extra test pytest shared/tests     # dogfood subset only
```

The suite covers two layers, both picked up by `uv run pytest` via
`[tool.pytest.ini_options] testpaths = ["tests", "shared/tests"]` in this
repo's `pyproject.toml`:

- Unit tests under `tests/test_<module>.py` for each Python module.

- The delivered tests at `shared/tests/test_*.py` -- the same files every
  consumer runs (vendored into the consumer at `_repo_shared/tests/` by
  `vendor()`). The repo's own targets for those tests are declared in
  `[tool.repo-shared.code-quality]` in `pyproject.toml`, exactly the same
  mechanism consumers use. A regression in the delivered test surface (e.g. a
  removed class attribute, a misbehaving `[tool.repo-shared.code-quality]`
  loader) shows up here first.

  `test_repo_shared_drift.py` is included in the run alongside the others but
  `VendorDriftBase` calls `_is_repo_shared_source_root(consumer_root)` and
  `pytest.skip`s when that's true -- repo-shared itself has no vendored
  `_repo_shared/` (it *is* the source), so the byte-compare would fail
  spuriously here. A misbehaving consumer that's missing `_repo_shared/` is not
  the source repo and still fails the check.

Both layers run from `uv run --extra test pytest`. No global pip installs.

## Bumping pinned tool deps (ruff / mypy / mdformat)

The shared test bases shell out to `python -m ruff` / `mypy` / `mdformat` from
the project venv, so the version every consumer runs is whatever uv resolved at
`uv lock` time. To keep that deterministic, those tools are pinned with `==` in
`pyproject.toml`'s `[project] dependencies`.

To bump the pins:

```bash
bin/repo-shared upgrade-tools             # bump every pin to PyPI's latest
bin/repo-shared upgrade-tools --only ruff # bump just one
bin/repo-shared upgrade-tools --push      # bump + commit; test + push on green
```

`upgrade-tools` queries PyPI for each `==`-pinned dep, then does the bump work
in a worktree at `<repo-shared>/.wt/repo-shared-tool-bump-<hash>` (mirroring
the consumer-side `upgrade`). `<hash>` is a sha256 prefix of the target version
set, so the same set of target versions deterministically lands on the same
branch -- a re-run after a red test resumes the existing worktree instead of
rebuilding. The worktree branch is `repo-shared/tool-bump-<hash>` off
`origin/<default>`, applies all available bumps to `pyproject.toml`, runs
`uv lock`, commits the bump, and runs the dogfood subset
(`uv run --locked --extra test pytest shared/tests`). The locked run refuses to
update `uv.lock`, so the committed candidate is exactly what dogfood tests. On
a red test run the clean, committed worktree is left in place so the maintainer
can `cd` in and debug, and the next run can test it again. With `--push`, only
a green bump branch ff-merges into the maintainer's default branch and pushes;
`--keep-worktree` retains the worktree after push. `git push --dry-run` runs
before any bump work so an upstream rejection fails fast.

The dogfood is scoped to `shared/tests` -- the quality gates that actually
exercise the bumped ruff / mypy / mdformat against every tracked file -- rather
than the full `uv run --extra test pytest`. The full suite additionally drives
the CLI integration tests under `tests/`, each of which spawns a nested
`upgrade` / `upgrade-tools` that builds a venv and runs its own dogfood; that
nesting is redundant for validating a tool bump and slow enough under
contention to exhaust the subprocess timeout.

When `uv lock` rejects the full bump set -- the common shape is a tool's new
major release landing on PyPI before its plugin ecosystem catches up, e.g.
`mdformat 1.0` while `mdformat-tables` still caps `mdformat<0.8` -- the
resolver falls back to a per-pin sweep in pyproject order. Each bump is kept
iff it locks together with the already-accepted set, and dropped otherwise.
Dropped pins are reported as `skipped (conflict): <name> <old> -> <new>` and
the non-conflicting bumps still land in the commit. If every candidate
conflicts, the worktree + branch are removed and the run exits cleanly --
there's nothing the maintainer can do until the blocking upstream catches up
(in the canonical shape above, until `mdformat-tables` drops its cap), and
nightly automation should shrug at the situation rather than page.

The subcommand is hidden from the consumer-shipped `--help` output (the same
CLI module ships in every consumer's venv, but the subcommand only makes sense
from a repo-shared maintainer's clone). A runtime guard in `_cmd_upgrade_tools`
rejects invocation from outside a repo-shared clone even if a consumer somehow
types the subcommand name.

## Doc-sync

Every change that adds, removes, or renames a CLI flag, public test base
attribute, or shared-content path also updates the doc(s) that mention it.
`README.md` documents onboarding + upgrade;
`shared/files/DEVELOPMENT_SHARED.md` is consumed by every consumer (so a stale
reference there propagates everywhere on the next bump).
