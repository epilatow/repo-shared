# Development Guide

Repo-specific dev conventions for repo-shared.

The companion [DEVELOPMENT_SHARED.md](DEVELOPMENT_SHARED.md) holds the
cross-repo conventions; this file layers repo-shared specifics on top. **This
file takes precedence over `DEVELOPMENT_SHARED.md` on conflict.**

## Repo layout

- `shared/` is the upstream content vendored into consumers at
  `_repo_shared/<kind>/`. `vendor.py` maps each kind to its consumer paths,
  with a leading dot on the first path segment for the `dot` kinds:
  - `shared/files/` and `shared/dotfiles/` -> canonical-path symlinks at the
    consumer root. Agent Skills are ordinary `dotfiles` entries.
  - `shared/templates/` and `shared/dottemplates/` -> real-file copies, for the
    reasons in README's [Docs and configs](README.md#docs-and-configs).
  - `shared/tests/` -> no canonical path. Consumers reach the delivered gates
    through an injected `testpaths` entry, which keeps them off the consumer's
    `tests/conftest.py` chain.
- `src/epilatow_repo_shared/` is the Python package. It ships `shared/` as
  package data for the CLI and the delivered gates.

This repo uses the same layout on itself: its canonical symlinks point into
`shared/`, and its `CLAUDE.md` and `.gitignore` are committed copies that the
delivered `test_in_sync.py` checks against `shared/`, so editing either
template means editing its root copy too. `README.md` and this file are
repo-local. `.repo-shared-ignore` lists `DEVELOPMENT.md` because its shared
upstream is only a placeholder.

## Testing

```bash
uv run --extra test pytest shared/tests     # development: shared gates
uv run --extra test pytest tests/test_vendor.py # focused example
uv run --extra test pytest                  # final full run before landing
```

[Testing](DEVELOPMENT_SHARED.md#testing) says which to run when. `testpaths`
covers both layers:

- The repo's own unit, integration, and end-to-end tests under `tests/`.
- The delivered gates under `shared/tests/`, the same files consumers run, with
  no `[tool.repo-shared.*]` overrides, so this repo exercises the defaults a
  fresh consumer gets. A regression in the delivered test surface shows up here
  first. The drift and tool-pin gates skip here because this repo is the
  source.

Consumer `run-tests` and default upgrade validation run the delivered gates
isolated from consumer pytest startup; explicit upgrade test commands and
ordinary pytest runs do not. Cover both routes when changing the shared runner
or upgrade test dispatch.

Consumers under test run on the Python in `.python-version`, the oldest one a
real consumer runs. `test_delivered_tests_e2e.py` also runs the delivered gates
and a wrapper `upgrade` in consumers on `_NEWEST_CONSUMER_PYTHON`; raise that
when consumers move to a newer release.

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

`upgrade-tools` commits the bump in a worktree under
`<repo-shared>/.wt/repo-shared-tool-bump-<hash>`, named for the target versions
so a rerun after a red test resumes it, and runs `shared/tests` with `--locked`
so what is tested is exactly what was committed. A red run leaves the committed
worktree for debugging. `--push` lands and pushes only a green bump and checks
`git push --dry-run` first.

That test run covers `shared/tests` -- the gates that exercise the bumped tools
-- rather than the full suite, because each CLI integration test under `tests/`
spawns a nested upgrade with its own venv and dogfood run: redundant for a tool
bump, and slow enough under contention to hit the subprocess timeout.

If the bumps won't all lock together, conflicting pins are skipped and reported
and the rest land. If none can land, the run exits cleanly without a commit:
nothing can be done until the upstream catches up, so nightly automation
shouldn't page.

`upgrade-tools` is hidden from consumers' `--help` and refuses to run outside a
repo-shared clone.

## Where docs go

Apply [Update relevant docs](DEVELOPMENT_SHARED_AGENT.md#update-relevant-docs)
with these readers in mind:

- `README.md` -- consumers: what repo-shared delivers and how to onboard,
  upgrade, and override it.
- `DEVELOPMENT.md` -- repo-shared maintainers.
- `shared/` -- vendored into every consumer. A doc line there reaches every
  consumer on its next upgrade, so it holds conventions and procedures, not the
  behavior of repo-shared's own tools.
