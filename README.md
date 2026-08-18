# repo-shared

Shared dev conventions, configs, and pytest gates for sibling epilatow repos.
Onboard a repo with `repo-shared init` and you get a set of canonical
conventions, plus a pytest suite that enforces them on every test run.

## What this repo gives you

### Docs and configs

A canonical set of cross-repo conventions, dropped into the consumer's repo
root. The upstream content lives under `shared/` here and is vendored into the
consumer at `_repo_shared/<kind>/`. Most consumer-visible paths are symlinks
into the vendored tree, so the canonical content stays updateable in one place.
A couple of paths -- `CLAUDE.md` and `.gitignore` -- ship as real-file copies
instead: `git` won't follow a symlinked `.gitignore`, and Claude resolves
`CLAUDE.md`'s `@`-includes relative to the file's real on-disk location.
`upgrade` auto-carries a upstream update into any template copy you haven't
customized.

Every canonical path is expected to be in sync with its upstream -- a correct
symlink, or a byte-matching template copy. `init` and `upgrade` enumerate every
out-of-sync entry and abort with `ERROR` (exit code 4) listing them all, and
the delivered `test_in_sync.py` gates the same invariant on every test run. To
carry your own version of a canonical path, list it in `.repo-shared-ignore` --
see [Override mechanisms](#override-mechanisms) below.

What lands in your repo:

- `CLAUDE.md` -- Claude Code entrypoint that loads the development guidance.
- `AGENTS.md` -- Codex entrypoint that requires the same development guidance
  to be read before work begins.
- `opencode.json` -- opencode config whose `instructions` array injects the
  development guidance files into the agent's system context at session start,
  so the agent never has to opt into reading them. A consumer that already has
  its own `opencode.json` should add it to `.repo-shared-ignore` and merge the
  `instructions` entries by hand.
- `DEVELOPMENT_SHARED.md` -- cross-repo conventions for humans + agents.
- `DEVELOPMENT_SHARED_AGENT.md` -- agent-specific conventions layered on top.
- `DEVELOPMENT.md` and `DEVELOPMENT_AGENT.md` -- per-repo placeholder templates
  that pair with the `_SHARED` files above. Replace either symlink with a real
  file when you accumulate repo-specific conventions worth writing down.
- `.markdownlint.json` and `.markdownlint-cli2.jsonc` -- markdownlint rule
  config and custom-rule registration for the linter test below.
- `.gitignore` -- baseline Python / editor / OS ignores plus the `.wt/`
  worktree dir `upgrade` uses.

### Python and markdown quality gates

A pytest suite that runs as part of `uv run pytest` and enforces a consistent
quality bar across every consumer: Python lint + format + strict type-check,
markdown format + lint. Two smaller gates cover integration sanity -- that the
consumer's vendored copy hasn't been hand-edited, and that the canonical paths
still resolve to their upstreams.

The delivered test files live under the consumer's vendored
`_repo_shared/tests/` directory; init injects a `testpaths` entry into
pyproject so pytest auto-discovers them. The vendored path is outside the
consumer's `tests/` tree, so the consumer's `tests/conftest.py` doesn't leak
into these tests:

- **`test_code_quality.py`** -- per-`.py` parametrize covering:

  - **`ruff check`** -- lint.
  - **`ruff format --check`** -- format.
  - **`mypy --strict`** -- type check, with per-file `--with` deps resolved
    from each file's PEP 723 `# /// script` block when present.

  Discovers every tracked `.py` under the repo, plus a tracked-but-skip
  post-filter that defaults to `_repo_shared/`. Adding a new module / test file
  works without a pyproject edit -- discovery finds it on the next run.

  `ruff check` / `ruff format --check` default to `--line-length=79` (matching
  the `mdformat` prose wrap below) when the consumer hasn't pinned a value in
  `ruff.toml` or `[tool.ruff]` of `pyproject.toml`. A consumer's explicit
  setting always wins; the flag is suppressed in that case. No file is ever
  written to the consumer to enforce this -- the delivered ruff invocation
  passes the flag at runtime only when needed.

  `ruff check` likewise injects the canonical lint categories
  `["E", "F", "W", "I", "B", "UP", "ARG"]` via `--extend-select` when the
  consumer hasn't declared its own `select`. The resulting baseline is the
  pinned Ruff version's curated defaults plus those categories, including `ARG`
  (flake8-unused-arguments) for unused function / method / lambda parameters. A
  consumer that pins its own `select` takes full control and the injection is
  suppressed; a consumer's `extend-select` adds further rules on top. A
  config-file `ignore` / `extend-ignore` does not drop an injected rule (a
  command-line `--extend-select` overrides config ignores), so a single
  injected rule is silenced only with a per-line `noqa` or by pinning `select`.
  As with line-length, nothing is written to the consumer -- the flag is passed
  at runtime only when needed.

- **`test_markdown_format.py`** -- `mdformat --check --wrap=79 --number` (with
  the GFM + tables plugins) across every markdown file in the repo. Catches
  drift in line wrap, table alignment, ordered-list numbering, bullet markers,
  blank-line spacing, ...

- **`test_markdownlint.py`** -- `markdownlint-cli2` across the repo's markdown.
  Catches the rules `mdformat` can't see: required fence languages, broken
  anchor links, duplicate headings, missing image alt text, plus a custom rule
  (`no-squashed-file-references`) that flags multiple Claude `@<path>` file
  imports squashed onto one line -- the imports still expand when reflowed, but
  the source goes opaque and any loader-specific dialect built on top
  (line-by-line parsing) breaks outright. Like the other gates it discovers
  files via `git ls-files` and feeds the explicit list to
  `markdownlint-cli2 --no-globs`, so it never walks the tree (no `.venv` /
  `.cache` traversal) and lints only the files git tracks or doesn't ignore
  (`.gitignore` is honored). The
  `[tool.repo-shared.markdown] extra-exclude-dirs` knob (the same knob the
  mdformat gate reads) drops further directories from that list.
  `.markdownlint.json` still supplies the lint rules and the custom rule. Fails
  -- not skips -- when `npx` is missing, so the gate stays enforced everywhere;
  install Node per the Requirements section.

Plus the two integration sanity checks:

- **`test_repo_shared_drift.py`** -- catches local edits to the vendored
  `_repo_shared/` tree by comparing it against the SHA-pinned package version.
- **`test_in_sync.py`** -- verifies every canonical path matches its upstream.
  An out-of-sync entry is either a symlink-kind path shadowed by a local file,
  or a template-kind copy that's drifted from the upstream. `init` and
  `upgrade` enforce the same invariant up front and abort with `ERROR` before
  they touch your tree.

The ruff, mypy, and mdformat versions are pinned in repo-shared's own
`pyproject.toml` and ride along when the consumer pins repo-shared by SHA.
Every consumer at a given repo-shared SHA runs the same versions of those
tools.

### A CLI that wires it together

`repo-shared init` / `upgrade` / `status` (run via `uvx --from git+...` for the
first invocation, then via `_repo_shared/repo-shared` from inside the consumer
afterwards) handles onboarding, bumping the pinned SHA, and surfacing drift.

## Requirements

A consumer needs the following installed on the dev / CI environment that runs
the test suite:

- **Python >= 3.11** + **`uv`**: the test suite is `uv run pytest`. ruff, mypy,
  and mdformat are `==`-pinned in repo-shared's own pyproject and uv resolves
  them into the consumer's `uv.lock`.
- **Node.js / `npx`**: required for `markdownlint-cli2`, which the
  `test_markdownlint.py` gate shells out to. Install via your package manager
  (`brew install node`, `apt install nodejs npm`, etc.). The gate fails -- not
  skips -- when `npx` is missing, so a Node-less CI surfaces the gap
  immediately rather than silently leaving markdownlint unenforced.
- **Git**: `upgrade` does its work in a worktree under
  `<consumer>/.wt/repo-shared-update-<short>` and `git push`-es from there on
  `--push`.

## Onboarding a new repo

```bash
cd ~/some-other-repo
uvx --from "git+https://github.com/epilatow/repo-shared" \
    repo-shared init
```

Or from a local clone of repo-shared (e.g. for offline work), run its wrapper
from inside the repo you want to onboard:

```bash
cd ~/some-other-repo
~/src/github.com/epilatow/repo-shared/bin/repo-shared init
```

After init, your repo has:

- `pyproject.toml` and `uv.lock` declaring `epilatow-repo-shared` as a dep
  pinned to a SHA, with `_repo_shared/tests` added to
  `[tool.pytest.ini_options] testpaths` so pytest picks up the shared tests on
  the next `uv run pytest`. `init` creates `pyproject.toml` if you don't
  already have one.
- `_repo_shared/` holding the vendored shared content (`files/`, `dotfiles/`,
  `templates/`, `dottemplates/`, `tests/`) plus the `_repo_shared/repo-shared`
  wrapper script you'll use for subsequent operations.
- The consumer-visible canonical paths from the file list above -- symlinks for
  the symlink kinds, seeded real-file copies for the template kinds.

A bare `uv run pytest` runs both your own tests and the delivered shared suite
(the injected `testpaths` entry covers both). To tune knobs (ruff / mypy
targets, mdformat wrap, etc.), or to carry your own version of a canonical
path, see [Override mechanisms](#override-mechanisms) below.

## Updating a consumer

Use your vendored wrapper for subsequent operations:

```bash
cd ~/some-other-repo
_repo_shared/repo-shared upgrade            # bump to default-branch HEAD
_repo_shared/repo-shared upgrade <sha>      # pin to a specific SHA
_repo_shared/repo-shared status             # show pinned SHA + any drift
_repo_shared/repo-shared run-tests          # run just the delivered shared tests
```

`status` ignores Python bytecode and tool cache directories under
`_repo_shared/`; those are runtime artifacts, not vendored content drift.
Unexpected non-cache files still surface as issues.

`run-tests` is a shortcut for
`uv run --project <consumer> pytest _repo_shared/tests` -- run it to verify the
delivered gates pass against your consumer without the rest of your suite. Pass
`-v` for verbose pytest output.

`upgrade` refuses on a dirty working tree, then does its work in a worktree at
`<consumer>/.wt/repo-shared-update-<short>` on a branch
`repo-shared/update-<short>` (deterministic per target SHA, so a re-run against
the same target resumes a prior failed attempt rather than rebuilding). The
resulting commit subject is
`- repo-shared: upgrade from <prev-short> to <new-short>.`

Useful flags:

- `--run-tests` runs the configured test command in the worktree after the
  bump. A non-zero exit aborts the upgrade and leaves the worktree for
  inspection. The test command comes from `[tool.repo-shared] test-command` in
  `pyproject.toml`; the default is `uv run pytest _repo_shared/tests`, which
  runs just the shared tests at their vendored path. To exercise the consumer's
  own tests too:

  ```toml
  [tool.repo-shared]
  test-command = "uv run pytest"
  ```

  Bare `uv run pytest` (no path) reads `testpaths` from your pyproject -- which
  init already populated with both your own tests dir and `_repo_shared/tests`
  -- so the full suite runs.

- `--push` implies `--run-tests`. On green, ff-merges the update branch into
  your default branch and runs `git push`. Default cleanup deletes the worktree
  and update branch after the push lands; pass `--keep-worktree` to retain
  both. Before doing any upgrade work, `--push` probes the eventual push
  outcome with `git push --dry-run` and bails if origin would reject -- a
  missing credential or branch-protection rule fails immediately instead of
  after a full setup + test cycle. A local-only consumer (no `origin` remote)
  has nowhere to push, so `--push` there lands the ff-merge on your local
  default branch and skips both the dry-run probe and the push.

- `--force-retry` drops a prior update worktree that carries uncommitted
  changes and rebuilds fresh. Without it, dirty worktrees block the upgrade so
  debug edits aren't silently dropped.

- `--base <ref>` builds the update worktree on top of `<ref>` instead of the
  default base -- `origin/<default-branch>`, or the local `<default-branch>`
  when there is no origin. Use it to base the upgrade on local work that isn't
  pushed yet -- e.g. `--base main` (or `--base HEAD`) carries your unpushed
  commits into the worktree, so the upgrade lands on top of them rather than on
  the stale origin tip. The push target on `--push` stays your local default
  branch either way (and with no origin there is no push -- see `--push`).

## Override mechanisms

repo-shared retains ownership of every file it delivers. Consumer customization
happens through one of three mechanisms, in increasing order of escape-hatch
invasiveness.

### `pyproject.toml` knob overrides (preferred)

The delivered quality-gate tests read consumer-configured knobs from
`[tool.repo-shared.<section>]` blocks in your `pyproject.toml`, so future
repo-shared updates flow through without you having to touch the tests.

The delivered `test_code_quality.py` discovers every tracked `.py` file under
the repo, with a tracked-but-skip post-filter that defaults to `_repo_shared/`,
and parametrizes ruff lint + ruff format + `mypy --strict` per file. New files
are picked up with no pyproject edit. Optional knobs cover the cases discovery
alone can't:

```toml
[tool.repo-shared.code-quality]
# Additive to auto-discovery: list explicit file paths for extension-less
# shebang scripts (``bin/foo``, ``bin/bar``) that auto-discovery cannot
# find on its own. Regular ``.py`` files do NOT need to be listed here.
python-targets = ["bin/foo"]            # default []

# Appended to the base discovery exclude set. Each entry is a
# directory NAME pruned anywhere in the tree (not a path prefix).
# Use for tracked-but-skip dirs that don't want lint /
# type-checking -- code-gen output, vendored bundles you committed,
# etc.
extra-exclude-dirs = ["htmlcov", "_build"]    # default []

# Project-wide fallback for files WITHOUT their own PEP 723 ``# /// script``
# block. A file with a PEP 723 block uses its own ``dependencies`` /
# ``requires-python`` instead.
mypy-extra-deps = ["voluptuous"]        # default []  (installed via uvx --with)
mypy-python-version = "3.12"            # default unset; pins uvx --python

[tool.repo-shared.markdown]
wrap = 78                               # default 79
# Appended to the base exclude set of BOTH markdown gates (mdformat
# and markdownlint). Each entry is a directory NAME pruned anywhere
# in the tree, not a path prefix.
extra-exclude-dirs = ["build"]          # default []
```

For files that need their own mypy environment (e.g. an HA-coupled module file
that needs `pytest-homeassistant-custom-component` resolvable for real
`homeassistant.*` types), add a PEP 723 `# /// script` block as a top-level
comment -- the test resolver reads it and spawns
`uvx --python <X> --with <dep>... mypy --strict <file>` for just that file. The
block may sit anywhere a top-level comment can (after the module docstring or
imports, or in a shebang prelude), matching how uv locates it; right under the
docstring is the convention here:

```python
"""HA-coupled module."""

# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pytest-homeassistant-custom-component==0.13.324",
# ]
# ///

from __future__ import annotations
```

PEP 723 is designed for shebang scripts but the block is just TOML inside a
comment, so Python's import machinery ignores it at runtime; only mypy's
per-file env reads it via the test resolver.

`extra-exclude-dirs` (in both sections) always *appends* to the base default --
never replaces -- so the shared baseline keeps applying after a repo-shared
upgrade that grows the default list. Missing sections, missing keys, and
malformed values all fall back to the documented defaults so a fresh consumer
with no `[tool.repo-shared.*]` blocks behaves as documented.

#### Tool configs that aren't repo-shared knobs

A handful of tool settings live in your own config rather than under
`[tool.repo-shared.*]`:

- **ruff** rules + Python line-length: set `[tool.ruff]` in your own
  `pyproject.toml` (e.g. `[tool.ruff] line-length = 79`,
  `[tool.ruff.lint] select = ["E", "F", ...]`). ruff reads these natively. When
  you don't pin `select`, the delivered gate keeps Ruff's pinned-version
  defaults and injects the canonical categories
  `["E", "F", "W", "I", "B", "UP", "ARG"]` via `--extend-select`; pinning your
  own `select` suppresses the injection and you own the rule set entirely (add
  `ARG` back if you want to keep unused-argument checking). `extend-select`
  adds rules on top of that combined baseline; a config-file `ignore` does not
  remove an injected rule (use a per-line `noqa`, or pin `select`).
- **mypy** strictness toggles + module overrides: set `[tool.mypy]` and
  `[[tool.mypy.overrides]]` in your own `pyproject.toml`. The delivered test
  runs `mypy --strict` which respects this config.
- **markdownlint** rules: configured via `.markdownlint.json` /
  `.markdownlint-cli2.jsonc`. To override, opt the path out via
  `.repo-shared-ignore` (below) and write your own.

### `.repo-shared-ignore` -- carry your own version of a canonical path

When you want a hand-edited agent entrypoint, your own `DEVELOPMENT.md`, or a
custom `.markdownlint.json`, list the path in `.repo-shared-ignore` at the repo
root:

```text
# .repo-shared-ignore
CLAUDE.md
AGENTS.md
.markdownlint.json
```

Listed paths are skipped by `init` / `upgrade` and by the in-sync gate, so your
own version stays in place. Paths are repo-relative (`CLAUDE.md`, not
`_repo_shared/templates/CLAUDE.md`). Comments (`#`) and blank lines are
ignored.
