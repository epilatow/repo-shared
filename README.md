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
file symlink, or a byte-matching template copy. `init` and `upgrade` report
every out-of-sync or unsafe path and exit 4 (`ERROR`), possibly after
installing the other files. The delivered `test_in_sync.py` gates canonical
paths on every test run. To carry your own version of a canonical leaf path,
list it in `.repo-shared-ignore` -- see
[Override mechanisms](#override-mechanisms) below.

### What lands in your repo

- `CLAUDE.md` -- Claude Code entrypoint that loads the development guidance.
- `AGENTS.md` -- Codex entrypoint that requires the same development guidance
  to be read before work begins.
- `.agents/skills/<name>/` -- bundled Agent Skills, linked file by file.
- `.claude/commands/<name>.md` -- thin Claude Code discovery adapters.
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
  worktree dir `upgrade` uses. Its `.venv`, `.pytest_cache`, `.ruff_cache`,
  `.mypy_cache`, `htmlcov`, `.wt`, and root `tmp` entries also match symlinks,
  so a checkout on slow storage can link them to existing directories
  elsewhere.

### Agent Skills

The bundled skills are portable
[Agent Skills](https://agentskills.io/specification). The Claude command
adapters prefer the repository-local skill, then a home-directory copy where
repository policy permits.

Bundled skills:

- `repo-shared-independent-code-review` -- zero-context review of a tested
  commit.
- `repo-shared-rewrite-local-commit-stack` -- safe local history rewrites.
- `repo-shared-audit-committed-change` -- authored-context completeness audit
  of one commit.
- `repo-shared-run-commit-gates` -- shared and focused development checks, then
  a final full-suite landing gate for an exact candidate, with isolated-failure
  analysis.
- `repo-shared-write-commit-message` -- draft or validate a repo-compliant
  commit message.
- `repo-shared-development-status-report` -- format requested reports on active
  efforts and unmerged commit stacks with stable numbering and per-commit
  state.

### Python and markdown quality gates

A pytest suite that runs as part of `uv run pytest` and enforces a consistent
quality bar across every consumer: Python lint + format + strict type-check,
markdown format + lint. Two smaller gates cover integration sanity -- that the
consumer's vendored copy hasn't been hand-edited, and that the canonical paths
still resolve to their upstreams.

The delivered tests live in `_repo_shared/tests/`, outside your `tests/` tree,
so your `tests/conftest.py` doesn't apply to them:

- **`test_code_quality.py`** -- `ruff check`, `ruff format --check`, and
  `mypy --strict` on every tracked `.py` file outside `_repo_shared/`. New
  files are picked up without a pyproject edit. See
  [Tool configs that aren't repo-shared knobs](#tool-configs-that-arent-repo-shared-knobs)
  for the ruff defaults.

- **`test_markdown_format.py`** -- `mdformat --check --wrap=79 --number` (with
  the GFM and frontmatter plugins) across tracked and unignored markdown: line
  wrap, table alignment, list numbering, bullet markers, blank-line spacing.

- **`test_markdownlint.py`** -- `markdownlint-cli2` across the same files, for
  the rules `mdformat` can't see: required fence languages, broken anchor
  links, duplicate headings, missing image alt text, and a custom
  `no-squashed-file-references` rule against several Claude `@<path>` imports
  on one line. Rules come from `.markdownlint.json`.

Plus the two integration sanity checks:

- **`test_repo_shared_drift.py`** -- catches local edits to the vendored
  `_repo_shared/` tree by comparing it against the SHA-pinned package version.
- **`test_in_sync.py`** -- checks that every canonical path still matches its
  upstream.

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

- **`uv`**: the test suite is `uv run pytest`.
- **Node.js / `npx`**: required for `markdownlint-cli2`, which the
  `test_markdownlint.py` gate shells out to. Install via your package manager
  (`brew install node`, `apt install nodejs npm`, etc.). The gate fails -- not
  skips -- when `npx` is missing, so a Node-less CI surfaces the gap
  immediately rather than silently leaving markdownlint unenforced.
- **Git**: `upgrade` works in a worktree under `<consumer>/.wt/`.

repo-shared supports Python `>=3.15,<3.16`. Before onboarding or upgrading an
existing project, adjust its `requires-python` to a compatible range; these
commands preserve existing requirements and fail if they are incompatible.

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
  pinned to a SHA, with `_repo_shared/tests` appended to
  `[tool.pytest.ini_options] testpaths`. If you had no `testpaths`, add your
  own test directories to it, or `uv run pytest` collects only the shared
  suite. `init` creates `pyproject.toml` if you don't already have one. A new
  project adopts repo-shared's Python requirement; existing project
  requirements are preserved.
- `_repo_shared/` holding the vendored shared content (`files/`, `dotfiles/`,
  `templates/`, `dottemplates/`, `tests/`) plus the `_repo_shared/repo-shared`
  wrapper script you'll use for subsequent operations.
- The consumer-visible canonical paths from the file list above -- symlinks for
  the symlink kinds, seeded real-file copies for the template kinds.

`init` stages the canonical symlinks in Git, including links beneath a
gitignored directory. Add the other generated files when committing the
onboarding change.

A bare `uv run pytest` runs the delivered shared suite first and runs the rest
of `testpaths` only if it passes. To tune knobs (ruff / mypy targets, mdformat
wrap, etc.), or to carry your own version of a canonical path, see
[Override mechanisms](#override-mechanisms) below.

## Updating a consumer

Use your vendored wrapper for subsequent operations:

```bash
cd ~/some-other-repo
_repo_shared/repo-shared upgrade            # bump to default-branch HEAD
_repo_shared/repo-shared upgrade <sha>      # pin to a specific SHA
_repo_shared/repo-shared status             # show pinned SHA + any drift
_repo_shared/repo-shared run-tests          # run just the delivered shared tests
```

`run-tests` runs only the delivered gates, isolated from your pytest
configuration, plugins, and `conftest.py` files; your `[tool.repo-shared.*]`
knobs and ruff / mypy configuration still apply. Pass `-v` for verbose output.

With repo-shared installed, every pytest session -- yours included -- keeps its
scratch in `<main-checkout>/tmp/pytest/`, also from linked worktrees
(`./tmp/pytest/` outside Git), rather than system temporary storage. In your
own pytest runs, `PYTEST_DEBUG_TEMPROOT`, `--basetemp`, and pytest's
`tmp_path_retention_count` / `tmp_path_retention_policy` settings work as
usual. Those sessions and repo-shared's tools also run with
`NODE_DISABLE_COMPILE_CACHE=1`, overriding your environment, so disposable
worktrees don't accumulate Node compilation caches.

`upgrade` refuses to run on a dirty working tree, which includes a symlink your
current `.gitignore` doesn't ignore; list such a link in `.git/info/exclude`
first, with no trailing slash (`/.wt`). It commits the bump on branch
`repo-shared/update-<short>` (`<short>` is the target's short SHA) in a
worktree at `<consumer>/.wt/repo-shared-update-<short>`, so rerunning against
the same target resumes a failed attempt. Without `--push`, merge that branch
yourself.

Useful flags:

- `--run-tests` runs the configured test command in the worktree after the
  bump. A non-zero exit aborts the upgrade and leaves the worktree for
  inspection. The command comes from `[tool.repo-shared] test-command` in
  `pyproject.toml` and defaults to the same shared-only run as `run-tests`. An
  explicit command runs in your normal environment, so install whatever it
  needs in the update worktree. To run your own tests too:

  ```toml
  [tool.repo-shared]
  test-command = "uv run pytest"
  ```

- `--push` implies `--run-tests`. On green it fast-forwards your default branch
  to the update, pushes it, and removes the worktree and branch
  (`--keep-worktree` keeps them). It runs `git push --dry-run` before any
  upgrade work, so a rejected push fails fast. Without an `origin` remote it
  fast-forwards your local default branch and skips the push.

- `--force-retry` drops a prior update worktree that carries uncommitted
  changes and rebuilds fresh. Without it, dirty worktrees block the upgrade so
  debug edits aren't silently dropped.

- `--base <ref>` builds the update on `<ref>` instead of
  `origin/<default-branch>` (or the local default branch when there is no
  origin) -- e.g. `--base main` to land the upgrade on top of unpushed local
  commits. `--push` still targets your local default branch.

## Override mechanisms

repo-shared retains ownership of every file it delivers. Consumer customization
happens through one of three mechanisms, in increasing order of escape-hatch
invasiveness.

### `pyproject.toml` knob overrides (preferred)

The delivered quality-gate tests read consumer-configured knobs from
`[tool.repo-shared.<section>]` blocks in your `pyproject.toml`, so future
repo-shared updates flow through without you having to touch the tests.

```toml
[tool.repo-shared.code-quality]
# Extension-less Python scripts (``bin/foo``) that discovery can't find.
# Regular ``.py`` files do NOT need to be listed here.
python-targets = ["bin/foo"]            # default []

# Tracked paths to skip -- code-gen output, committed vendored bundles.
# .gitignore patterns relative to the repo root; "!" is rejected.
extra-exclude-dirs = ["htmlcov", "docs/_build"]    # default []

# Project-wide fallback for files WITHOUT their own PEP 723 ``# /// script``
# block. A file with a PEP 723 block uses its own ``dependencies`` /
# ``requires-python`` instead.
mypy-extra-deps = ["voluptuous"]        # default []  (installed via uvx --with)
mypy-python-version = "3.12"            # default unset; pins uvx --python

[tool.repo-shared.markdown]
wrap = 78                               # default 79
# Paths both markdown gates skip; same pattern syntax as above.
extra-exclude-dirs = ["build", "docs/generated.md"]    # default []
```

`extra-exclude-dirs` adds to the shared default excludes; it never replaces
them. Missing or wrong-typed settings fall back to the defaults; invalid
patterns fail the gate.

For a file that needs its own mypy environment (e.g. an HA-coupled module that
needs `pytest-homeassistant-custom-component` for real `homeassistant.*`
types), give it a PEP 723 `# /// script` block. The gate type-checks that file
with the block's `requires-python` and `dependencies`. The block must be a
top-level comment, and importing the module ignores it. By convention it sits
right under the module docstring:

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

#### Tool configs that aren't repo-shared knobs

A handful of tool settings live in your own config rather than under
`[tool.repo-shared.*]`:

- **ruff** rules + Python line-length: set them in `[tool.ruff]` of
  `pyproject.toml`, or in a `ruff.toml`, which replaces the pyproject settings;
  ruff reads these natively. Unset values get repo-shared defaults on the
  command line, with nothing written to your repo: `--line-length=79`, and,
  unless you pin `select`, Ruff's defaults plus
  `["E", "F", "W", "I", "B", "UP", "ARG"]` via `--extend-select`. Pinning
  `select` hands you the whole rule set (add `ARG` back to keep unused-argument
  checking); `extend-select` adds on top. A config-file `ignore` cannot remove
  an injected rule -- use a per-line `noqa` or pin `select`.
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
ignored. Skills use the same exact-path override mechanism. To suppress a
skill's standard and Claude entrypoints, list their paths:

```text
.agents/skills/<name>/SKILL.md
.claude/commands/<name>.md
```

Vendored files remain drift-checked. Suppressing a required skill's entrypoints
does not waive its workflow: agents stop at that step unless higher-precedence
repository instructions provide an authorized replacement.
