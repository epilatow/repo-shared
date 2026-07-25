# This is AI generated code
"""repo-shared CLI: init, upgrade, status, run-tests, upgrade-tools.

Modeled after the utils/bin idioms: ``cli()`` is the argparse entry
point that returns an ``int``, ``main()`` does the per-subcommand
work, exit codes use the ``ExitCode`` enum.

The wrapper script at ``shared/files/_repo_shared/repo-shared`` (which
gets installed at ``<consumer>/_repo_shared/repo-shared`` via the
vendor mechanism) invokes this CLI via ``uv run --project <root>
repo-shared``, so the running version is whatever the consumer's
``uv.lock`` pins -- not whatever the wrapper script was bundled with.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any

from epilatow_repo_shared import sp
from epilatow_repo_shared.exit_codes import ExitCode
from epilatow_repo_shared.vendor import (
    VENDOR_DIRNAME,
    _is_repo_shared_source_root,
    _is_vendor_runtime_artifact,
    check_in_sync,
    cleanup_stale_vendored,
    consumer_paths,
    iter_shared,
    package_shared_root,
    vendor,
)

_DEFAULT_SOURCE = "git+https://github.com/epilatow/repo-shared"


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _resolve_consumer_root(path_arg: str | None) -> Path:
    return Path(path_arg or Path.cwd()).resolve()


def _git_is_clean(repo_root: Path) -> bool:
    result = sp.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() == ""


def _git_repo(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def _can_push(repo_root: Path, branch: str) -> bool:
    """Probe whether ``git push origin <branch>`` would succeed.

    Runs ``git push --dry-run origin <branch>:<branch>`` so the
    upgrade flow can fail fast on auth / fast-forward / permission
    rejection before doing expensive worktree + test work that is
    going to be discarded when the eventual real push fails. The
    ``--dry-run`` flag tells git to negotiate the push fully but
    not actually transfer or update refs.
    """
    result = sp.run(
        ["git", "push", "--dry-run", "origin", f"{branch}:{branch}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    return result.returncode == 0


_TESTPATHS_ENTRY = "_repo_shared/tests"


def _project_name(repo_root: Path) -> str:
    """Best-effort project name for ``uv init --bare --name <X>``.

    ``repo_root.name`` is the basename of the path ``init`` was
    invoked against. For a main checkout (e.g. ``~/utils``) that
    matches the project name. For a worktree (``~/utils.wt/branch``
    or ``~/utils/.wt/branch``) the basename is the worktree's
    directory name, not the project's -- silently writing
    ``name = "branch"`` to a fresh ``pyproject.toml`` would bake the
    wrong name into ``uv.lock`` (and into any wheel the consumer
    later builds).

    Discriminator: ``git rev-parse --git-dir`` and
    ``--git-common-dir`` return the same path in a main checkout and
    diverge in a worktree (the per-worktree git-dir vs the shared
    common-dir). When they diverge, the common-dir's parent is the
    canonical project directory; use its basename. Falls back to
    ``repo_root.name`` on any git error so onboarding pre-git-init
    repos still produces a reasonable default.
    """
    git_dir = sp.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    common = sp.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if git_dir.returncode or common.returncode:
        return repo_root.name
    git_dir_path = (repo_root / git_dir.stdout.strip()).resolve()
    common_path = (repo_root / common.stdout.strip()).resolve()
    if git_dir_path == common_path:
        # Main checkout (or submodule). repo_root is the project root.
        return repo_root.name
    # Worktree -- common-dir's parent is the canonical project root.
    return common_path.parent.name


def _has_explicit_ref(source: str) -> bool:
    """True when ``source`` ends with an explicit ``@<ref>`` pin.

    Distinguishes a trailing ref like ``...@deadbeef`` from an
    intra-authority ``@`` such as ``git+ssh://git@github.com/...``
    by checking whether the substring after the last ``@`` contains
    a ``/`` (intra-URL) or not (trailing ref).
    """
    return "@" in source and "/" not in source.rsplit("@", 1)[-1]


def _ensure_repo_shared_dep(
    repo_root: Path,
    *,
    source_url: str,
) -> int:
    """Ensure ``pyproject.toml`` declares ``epilatow-repo-shared``.

    Three pyproject edits land here:

    - ``uv init --bare`` creates the file if absent.
    - ``uv add`` inserts the dep into ``[project] dependencies`` and
      the source spec into ``[tool.uv.sources]``. When ``source_url``
      doesn't already carry an explicit ``@<ref>`` pin, the upstream
      ``HEAD`` is resolved to a concrete SHA first and the explicit
      pin is passed through to ``uv add``. Without the explicit pin,
      ``uv add`` against an already-satisfied ``uv.lock`` skips the
      git-fetch, so the recovery re-run of ``init`` on an
      out-of-sync consumer would freeze the SHA at whatever first
      onboarded it; resolving ``HEAD`` here pins the repair to the
      current upstream.
    - ``_inject_shared_testpaths`` (tomlkit round-trip) appends
      ``_repo_shared/tests`` to
      ``[tool.pytest.ini_options] testpaths`` so a bare ``pytest``
      finds the shared tests at their vendored path. The vendored
      path lives outside ``tests/``, so the consumer's
      ``tests/conftest.py`` is not in the conftest walk chain --
      shared tests don't get the consumer's module-load imports
      leaking in.

    Returns the subprocess exit code (``0`` on success).
    """
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        init_result = sp.run(
            [
                "uv",
                "init",
                "--bare",
                "--vcs",
                "none",
                "--no-readme",
                "--no-pin-python",
                "--name",
                _project_name(repo_root),
            ],
            cwd=repo_root,
            check=False,
        )
        if init_result.returncode != 0:
            return init_result.returncode

    effective_source = source_url
    if not _has_explicit_ref(source_url):
        resolved = _resolve_upstream_head(source_url)
        if resolved is not None:
            effective_source = f"{source_url}@{resolved}"

    add_result = sp.run(
        ["uv", "add", f"epilatow-repo-shared @ {effective_source}", "pytest"],
        cwd=repo_root,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if add_result.returncode != 0:
        return add_result.returncode
    _inject_shared_testpaths(pyproject)
    return 0


def _inject_shared_testpaths(pyproject: Path) -> None:
    """Append the shared-tests path to ``[tool.pytest.ini_options]``.

    Idempotent: if the entry is already present, leaves the file
    alone. Uses ``tomlkit`` for a round-trip that preserves the
    consumer's existing comments, key ordering, and whitespace.
    """
    import tomlkit  # Deferred so CLI startup stays cheap.

    text = pyproject.read_text(encoding="utf-8")
    doc = tomlkit.parse(text)
    tool_raw = doc.get("tool")
    if isinstance(tool_raw, tomlkit.items.Table):
        tool = tool_raw
    else:
        tool = tomlkit.table()
        doc["tool"] = tool
    pytest_section_raw = tool.get("pytest")
    if isinstance(pytest_section_raw, tomlkit.items.Table):
        pytest_section = pytest_section_raw
    else:
        pytest_section = tomlkit.table()
        tool["pytest"] = pytest_section
    ini_raw = pytest_section.get("ini_options")
    if isinstance(ini_raw, tomlkit.items.Table):
        ini_options = ini_raw
    else:
        ini_options = tomlkit.table()
        pytest_section["ini_options"] = ini_options
    paths_raw = ini_options.get("testpaths")
    if isinstance(paths_raw, tomlkit.items.Array):
        paths = paths_raw
    else:
        paths = tomlkit.array()
        ini_options["testpaths"] = paths
    if _TESTPATHS_ENTRY not in [str(p) for p in paths]:
        paths.append(_TESTPATHS_ENTRY)
        pyproject.write_text(tomlkit.dumps(doc), encoding="utf-8")


_LOCKED_SHA_PATTERN = re.compile(
    r"^epilatow-repo-shared @ git\+\S+@([0-9a-fA-F]{7,40})$"
)


def _read_locked_sha(repo_root: Path) -> str | None:
    """Resolve the SHA epilatow-repo-shared is pinned to in this consumer.

    Shells out to ``uv export --format requirements-txt --no-hashes``
    rather than parsing ``uv.lock`` ourselves. The lockfile's internal
    schema drifts across uv releases (``resolved-reference`` vs
    ``rev`` vs URL ``#<sha>`` fragment vs ``?rev=<sha>`` query); the
    export output is a stable requirements.txt-style line of the form
    ``<name> @ git+<url>@<sha>`` regardless of how the lock represents
    the pin internally. That's the stable surface to depend on.

    Returns ``None`` when:

    - the consumer has no ``uv.lock`` (not onboarded),
    - ``uv export`` fails (uv not on PATH, broken pyproject, etc.), or
    - no ``epilatow-repo-shared @ git+...@<sha>`` line is found in the
      export (the dep isn't declared, or it's pinned to a non-git
      source like a wheel or local path).
    """
    if not (repo_root / "uv.lock").is_file():
        return None
    result = sp.run(
        ["uv", "export", "--format", "requirements-txt", "--no-hashes"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = _LOCKED_SHA_PATTERN.match(line.rstrip())
        if match:
            return match.group(1)
    return None


def _git_commit_upgrade(
    repo_root: Path,
    *,
    old_sha: str,
    new_sha: str,
) -> int:
    """Commit the SHA bump with the canonical upgrade-subject format.

    ``old_sha`` is required: ``upgrade`` refuses earlier when the
    consumer has no existing pin (an unonboarded repo runs ``init``,
    not ``upgrade``), so the "no prior pin" case never reaches here.
    """
    message = f"- repo-shared: upgrade from {old_sha[:7]} to {new_sha[:7]}.\n"
    # ``git add -A`` so the commit captures every file the bump
    # touched -- pyproject (uv add + testpaths injection),
    # ``uv.lock``, the vendored ``_repo_shared/`` tree, AND any
    # canonical-path symlinks created or removed (whose names
    # differ across layout migrations). The worktree is required
    # clean before the upgrade runs, so this only stages
    # upgrade-driven changes.
    add_result = sp.run(
        ["git", "add", "-A"],
        cwd=repo_root,
        check=False,
    )
    if add_result.returncode != 0:
        return add_result.returncode
    commit_result = sp.run(
        ["git", "commit", "-m", message],
        cwd=repo_root,
        check=False,
    )
    return commit_result.returncode


def _running_from_local_repo_shared() -> Path | None:
    """If the CLI source lives inside a clone of repo-shared, return it."""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists() and (
            candidate / "shared" / "files"
        ).is_dir():
            return candidate
    return None


class RepoState(Enum):
    """What a repo is, from the CLI's point of view.

    Drives both ``--help`` subcommand visibility (against the cwd)
    and the per-command runtime refusal (against the resolved
    target). ``SOURCE`` and ``ONBOARDED`` are positively detected;
    ``PLAIN`` is the fallthrough for anything else, including a
    not-yet-onboarded repo.
    """

    SOURCE = "source"
    ONBOARDED = "onboarded"
    PLAIN = "plain"


def _classify_repo(path: Path) -> RepoState:
    """Classify ``path`` as repo-shared source, onboarded, or plain."""
    if _is_repo_shared_source_root(path):
        return RepoState.SOURCE
    if (path / VENDOR_DIRNAME).is_dir():
        return RepoState.ONBOARDED
    return RepoState.PLAIN


def _refuse_init_target(repo_root: Path) -> ExitCode | None:
    """Return ``USAGE`` unless ``init`` has work to do on ``repo_root``.

    ``init`` onboards a not-yet-onboarded repo or repairs one whose
    vendored content is out of sync with the upstream. Two targets are
    a usage error:

    - repo-shared's own source -- it *is* the upstream, never a
      consumer. The default target is the cwd, so running ``init``
      with no path from a clone lands here; the message steers the
      caller to pass the path of the repo they mean to onboard.
    - an already-onboarded consumer that is fully in sync -- there is
      nothing to do; ``upgrade`` bumps the pin and ``status`` inspects
      drift. An onboarded-but-out-of-sync target is *not* refused: a
      prior ``init`` that aborted on a sync violation leaves
      ``_repo_shared/`` in place, and re-running ``init`` after
      clearing the violation is the documented recovery.
    """
    state = _classify_repo(repo_root)
    if state is RepoState.SOURCE:
        _eprint(
            f"init onboards a plain repo; {repo_root} is repo-shared's "
            "own source. Pass the path to the repo you want to onboard."
        )
        return ExitCode.USAGE
    if state is RepoState.ONBOARDED and not check_in_sync(repo_root):
        _eprint(
            f"{repo_root} is already onboarded; use `upgrade` to bump "
            "the pinned SHA or `status` to inspect drift."
        )
        return ExitCode.USAGE
    return None


def _refuse_when_target_is_source(
    subcommand: str, repo_root: Path
) -> ExitCode | None:
    """Return ``USAGE`` when ``repo_root`` is repo-shared's own source.

    ``upgrade`` / ``status`` operate on an onboarded consumer's
    vendored ``_repo_shared/``; repo-shared itself is the source and
    has no vendored copy, so pointing them at it is a usage error.
    Any other target -- including from a maintainer's clone via an
    explicit path -- carries on (a plain, never-onboarded target is
    handled by each command's own "not onboarded" path).
    """
    if _classify_repo(repo_root) is not RepoState.SOURCE:
        return None
    _eprint(
        f"`{subcommand}` operates on an onboarded consumer; {repo_root} "
        "is repo-shared's own source. Point it at an onboarded repo."
    )
    return ExitCode.USAGE


def _refuse_run_tests_from_clone() -> ExitCode | None:
    """Return ``USAGE`` if ``run-tests`` is invoked from a repo-shared clone.

    ``run-tests`` runs the delivered suite through the *consumer's*
    pinned repo-shared version. Driving it from a clone would run it
    through the clone's (potentially different) version instead, so
    it is consumer-only; maintainers dogfood via ``uv run pytest
    shared/tests`` from the source tree.
    """
    if _running_from_local_repo_shared() is None:
        return None
    _eprint(
        "`run-tests` runs a consumer's delivered suite against its own "
        "pinned repo-shared; run it from the onboarded repo (via "
        "`_repo_shared/repo-shared run-tests`), not from a clone. "
        "Maintainers use `uv run pytest shared/tests`."
    )
    return ExitCode.USAGE


_PIN_LINE_RE = re.compile(
    r'^\s*"(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[0-9][0-9A-Za-z.+-]*)"'
)


def _read_pinned_deps(pyproject_text: str) -> list[tuple[str, str]]:
    """Parse ``[project] dependencies`` for exact-pinned entries.

    Returns ``(name, version)`` tuples for lines matching
    ``"pkg==X.Y.Z"`` inside the ``dependencies = [...]`` list.
    Loose constraints (``>=``, ``~=``, no operator) and the
    self-dep ``epilatow-repo-shared`` (the repo never pins itself
    here) are skipped. Line-based parse, not full TOML, so the
    update routine can preserve formatting + comments around the
    pin in-place.
    """
    pinned: list[tuple[str, str]] = []
    in_deps = False
    for line in pyproject_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies"):
            if stripped.endswith("["):
                in_deps = True
            continue
        if in_deps:
            if stripped == "]":
                break
            match = _PIN_LINE_RE.match(stripped)
            if match:
                pinned.append((match.group("name"), match.group("version")))
    return pinned


def _query_pypi_latest(pkg: str) -> str | None:
    """Return PyPI's ``info.version`` for ``pkg``, or ``None`` on failure.

    ``info.version`` is the latest stable release (PyPI's JSON API
    already excludes pre-releases). Short timeout so a slow / down
    PyPI fails fast instead of hanging the test command.
    """
    url = f"https://pypi.org/pypi/{pkg}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    version = (
        data.get("info", {}).get("version") if isinstance(data, dict) else None
    )
    return version if isinstance(version, str) and version else None


def _tool_bump_hash(bumps: list[tuple[str, str, str]]) -> str:
    """Short deterministic digest of the target version set.

    The hash is over ``sorted([(name, new), ...])`` so the same set
    of target versions always maps to the same branch name --
    enabling resume-on-failure across nightly runs, and ensuring a
    new bump set gets a distinct branch instead of overwriting an
    in-progress one.
    """
    target_pairs = sorted((name, new) for name, _old, new in bumps)
    blob = ",".join(f"{n}={v}" for n, v in target_pairs).encode()
    return hashlib.sha256(blob).hexdigest()[:7]


def _worktree_has_expected_bumps(
    wt_path: Path, bumps: list[tuple[str, str, str]]
) -> bool:
    """True iff ``wt_path``'s ``pyproject.toml`` is a valid bump state.

    Used by the resume path: when a worktree from a prior run still
    exists at the same deterministic branch name AND its pyproject
    pins each candidate at either its ``old`` (skipped on a prior
    conflict-fallback sweep) or ``new`` (accepted) value, the bump
    work is already done -- we just need to re-run tests + push
    (whichever failed last time). At least one bump must be at its
    ``new`` to count as resumable, so a worktree that bailed out
    with zero accepted bumps gets rebuilt instead of resumed.

    Any other value indicates a stale or foreign worktree state
    (different bump set, hand-edit) and triggers a rebuild.
    """
    pyproject = wt_path / "pyproject.toml"
    if not pyproject.is_file():
        return False
    actual = dict(_read_pinned_deps(pyproject.read_text()))
    accepted_any = False
    for name, old, new in bumps:
        pinned = actual.get(name)
        if pinned == new:
            accepted_any = True
        elif pinned != old:
            return False
    return accepted_any


def _ensure_tool_bump_worktree(
    *,
    repo_root: Path,
    branch: str,
    wt_path: Path,
    upstream_ref: str,
    bumps: list[tuple[str, str, str]],
    force_retry: bool,
) -> ExitCode | str:
    """Create or resume the tool-bump worktree.

    Returns ``"needs-work"`` if the caller should apply bumps + lock
    + commit, ``"resume"`` if the existing worktree already pins
    every target version cleanly, or an ``ExitCode`` on failure.
    Mirrors ``_ensure_update_worktree``'s shape for the consumer
    upgrade flow.
    """
    branch_exists = _git_branch_exists(repo_root, branch)
    worktree_exists = wt_path.is_dir() and (wt_path / ".git").exists()

    if branch_exists and worktree_exists:
        if not _git_is_clean(wt_path):
            if not force_retry:
                _eprint(
                    f"existing worktree {wt_path} has uncommitted "
                    "changes; refusing to recreate. Commit / stash "
                    "/ discard there, or pass --force-retry."
                )
                return ExitCode.DIRTY
            _remove_worktree(repo_root, wt_path)
            _delete_branch(repo_root, branch)
        else:
            ff = _git_branch_ff_mergeable(repo_root, branch, upstream_ref)
            if _worktree_has_expected_bumps(wt_path, bumps) and ff:
                return "resume"
            # Stale: targets diverged or the default branch moved
            # past the branch's parent. Drop and recreate.
            _remove_worktree(repo_root, wt_path)
            _delete_branch(repo_root, branch)
    elif branch_exists and not worktree_exists:
        _delete_branch(repo_root, branch)
    elif worktree_exists and not branch_exists:
        _remove_worktree(repo_root, wt_path)

    wt_path.parent.mkdir(parents=True, exist_ok=True)
    create = sp.run(
        [
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(wt_path),
            upstream_ref,
        ],
        cwd=repo_root,
        check=False,
        timeout=sp.SHORT_TIMEOUT_SECONDS,
    )
    if create.returncode != 0:
        _eprint(f"git worktree add failed for {wt_path}; resolve and rerun.")
        return ExitCode.SUBPROCESS
    return "needs-work"


def _compose_bumped_pyproject(
    original_text: str, bumps: list[tuple[str, str, str]]
) -> str | None:
    """Return ``original_text`` with each ``(name, old, new)`` pin rewritten.

    Returns ``None`` if any ``"name==old"`` literal can't be found in
    the source -- a pre-flight check the caller uses to fail fast
    when the pyproject shape moved out of band between PyPI query
    and rewrite. Replacement is two-pass (validate all, then mutate)
    so a partial rewrite never lands on disk.
    """
    for name, old, _new in bumps:
        if f'"{name}=={old}"' not in original_text:
            return None
    text = original_text
    for name, old, new in bumps:
        text = text.replace(f'"{name}=={old}"', f'"{name}=={new}"')
    return text


def _try_bump_set(
    *,
    wt_path: Path,
    wt_pyproject: Path,
    original_text: str,
    bumps: list[tuple[str, str, str]],
) -> bool:
    """Write ``bumps`` into ``wt_pyproject`` and run ``uv lock``.

    Returns ``True`` iff lock succeeds. On failure the pyproject is
    left in the attempted state -- callers iterating across sets are
    expected to call again with a different set, which overwrites
    pyproject before the next lock.
    """
    text = _compose_bumped_pyproject(original_text, bumps)
    if text is None:
        return False
    wt_pyproject.write_text(text)
    result = sp.run(
        ["uv", "lock"],
        cwd=wt_path,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    return result.returncode == 0


def _resolve_compatible_bumps(
    *,
    wt_path: Path,
    wt_pyproject: Path,
    original_text: str,
    bumps: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Return ``(accepted, skipped)`` after resolving lock conflicts.

    Fast path: apply every bump and run ``uv lock`` once; if that
    resolves, every bump is accepted. Otherwise fall back to a
    greedy per-pin sweep in the caller's input order -- a bump is
    kept iff it locks together with the already-accepted set, and
    dropped otherwise. Worst case is N+2 lock runs (the failed
    fast-path attempt, N per-pin attempts, plus a final flush when
    the last iteration was a reject or every candidate was
    rejected). When the per-pin sweep ends on an accept, pyproject
    + lockfile already reflect ``accepted`` and the flush is
    skipped.

    Handles the realistic conflict shape: a tool's new major
    release lands on PyPI before its plugin ecosystem catches up
    (e.g. ``mdformat 1.0`` while ``mdformat-tables`` still caps
    ``mdformat<0.8``). The non-conflicting bumps still land instead
    of the whole transaction failing.
    """
    if _try_bump_set(
        wt_path=wt_path,
        wt_pyproject=wt_pyproject,
        original_text=original_text,
        bumps=bumps,
    ):
        return list(bumps), []

    accepted: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str, str]] = []
    last_was_accept = False
    for bump in bumps:
        if _try_bump_set(
            wt_path=wt_path,
            wt_pyproject=wt_pyproject,
            original_text=original_text,
            bumps=accepted + [bump],
        ):
            accepted.append(bump)
            last_was_accept = True
        else:
            skipped.append(bump)
            last_was_accept = False
    if accepted and not last_was_accept:
        # The last iteration rejected its candidate, so pyproject +
        # lockfile reflect ``accepted + [rejected]``. Flush to the
        # accepted-only state so commit / push / tests see a
        # consistent tree. When the last iteration was an accept,
        # pyproject + lockfile already reflect ``accepted`` and the
        # flush is skipped.
        _try_bump_set(
            wt_path=wt_path,
            wt_pyproject=wt_pyproject,
            original_text=original_text,
            bumps=accepted,
        )
    elif not accepted:
        wt_pyproject.write_text(original_text)
        sp.run(
            ["uv", "lock"],
            cwd=wt_path,
            check=False,
            timeout=sp.LONG_TIMEOUT_SECONDS,
        )
    return accepted, skipped


def _cmd_upgrade_tools(args: argparse.Namespace) -> ExitCode:
    """Bump pinned tool deps in repo-shared's pyproject (worktree).

    Detects exact-pinned ``"pkg==X.Y.Z"`` entries in
    ``[project] dependencies``, queries PyPI for the latest stable
    release of each, and applies the bumps in one commit. All work
    happens in a worktree at
    ``<repo-shared>/.wt/repo-shared-tool-bump-<hash>`` on a branch
    ``repo-shared/tool-bump-<hash>`` where ``<hash>`` is derived
    from the set of target versions -- deterministic per bump set
    so two runs with the same set of newly-available versions hit
    the same branch (enabling resume-on-failure).

    Cheap no-op: if PyPI has no bumps available for any pinned
    tool, the command exits early with no worktree, branch, or
    state-change. The conflict-only case (every candidate bump
    blocked by an existing pin's constraint -- see
    ``_resolve_compatible_bumps``) is a second cheap no-op with
    the same external shape: worktree + branch removed, no commit
    landed, ``ExitCode.SUCCESS``. Both shapes are safe for nightly
    automation.

    Maintainer-only -- a runtime guard via
    ``_running_from_local_repo_shared()`` rejects invocation from
    a consumer. Failed test runs leave the worktree in place so
    the maintainer can ``cd`` in and debug; ``--force-retry``
    overrides the dirty-worktree refusal on the next run.
    """
    repo_root = _running_from_local_repo_shared()
    if repo_root is None:
        _eprint(
            "upgrade-tools is a repo-shared-maintainer-only "
            "subcommand; run it from a repo-shared clone, not "
            "from a consumer."
        )
        return ExitCode.USAGE

    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        _eprint(f"missing {pyproject}.")
        return ExitCode.CONFIG

    default_branch = _default_branch(repo_root)
    if default_branch is None:
        _eprint(
            "could not resolve origin/HEAD's branch in repo-shared; "
            "ensure ``origin`` is configured."
        )
        return ExitCode.CONFIG
    upstream_ref = f"origin/{default_branch}"

    fetch_result = sp.run(
        ["git", "fetch", "origin"],
        cwd=repo_root,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if fetch_result.returncode != 0:
        _eprint("git fetch origin failed.")
        return ExitCode.SUBPROCESS

    if args.push and not _can_push(repo_root, default_branch):
        _eprint(
            "git push --dry-run failed; aborting before any bump "
            "work. Investigate (auth, branch protection, "
            "upstream divergence) and re-run."
        )
        return ExitCode.ERROR

    text = pyproject.read_text()
    pinned = _read_pinned_deps(text)
    only = set(args.only or ())
    if only:
        pinned = [(n, v) for n, v in pinned if n in only]
        if not pinned:
            _eprint(
                f"--only {sorted(only)} excluded every pinned "
                "tool; nothing to do."
            )
            return ExitCode.CONFIG
    if not pinned:
        print("no exact-pinned tool deps found in pyproject.toml.")
        return ExitCode.SUCCESS

    bumps: list[tuple[str, str, str]] = []
    for name, current in pinned:
        latest = _query_pypi_latest(name)
        if latest is None:
            _eprint(f"  {name}: could not query PyPI; skipping.")
            continue
        if latest == current:
            print(f"  {name}: up to date ({current}).")
        else:
            print(f"  {name}: {current} -> {latest}")
            bumps.append((name, current, latest))

    if not bumps:
        # Cheap exit: every pin is up to date, no worktree spun
        # up, no branch created. Nightly automation that wakes up
        # to a quiet PyPI leaves no state behind.
        print("every pinned tool is up to date.")
        return ExitCode.SUCCESS

    if not _git_is_clean(repo_root):
        _eprint(
            "working tree has uncommitted changes; upgrade-tools "
            "must start clean. Commit or stash, then re-run."
        )
        return ExitCode.DIRTY

    bump_hash = _tool_bump_hash(bumps)
    branch = f"repo-shared/tool-bump-{bump_hash}"
    wt_path = repo_root / ".wt" / f"repo-shared-tool-bump-{bump_hash}"

    setup = _ensure_tool_bump_worktree(
        repo_root=repo_root,
        branch=branch,
        wt_path=wt_path,
        upstream_ref=upstream_ref,
        bumps=bumps,
        force_retry=args.force_retry,
    )
    if isinstance(setup, ExitCode):
        return setup

    if setup == "needs-work":
        wt_pyproject = wt_path / "pyproject.toml"
        original_text = wt_pyproject.read_text()
        if _compose_bumped_pyproject(original_text, bumps) is None:
            _eprint(
                "could not locate a bump pin for in-place rewrite "
                f"in {wt_pyproject}; worktree left at {wt_path}."
            )
            return ExitCode.ERROR

        accepted, skipped = _resolve_compatible_bumps(
            wt_path=wt_path,
            wt_pyproject=wt_pyproject,
            original_text=original_text,
            bumps=bumps,
        )
        for name, old, new in skipped:
            print(f"  skipped (conflict): {name} {old} -> {new}")
        if not accepted:
            # No accepted bumps means pyproject is back at its
            # pre-bump state with nothing for the maintainer to
            # inspect, so the worktree + branch are removed and the
            # run exits SUCCESS for nightly automation's sake. The
            # next run re-queries PyPI and picks up any release that
            # has since become installable against the existing pins.
            print(
                "no upstream-compatible bumps available this run "
                "(every candidate conflicts with the existing pin "
                "set); re-run after the blocking upstream catches up."
            )
            _remove_worktree(repo_root, wt_path)
            _delete_branch(repo_root, branch)
            return ExitCode.SUCCESS
        bumps = accepted
    else:
        print(f"resuming existing bump worktree at {wt_path}.")

    # Commit before testing so a red dogfood run leaves a clean,
    # inspectable branch that the next invocation can resume. On
    # resume the bump commit already exists.
    if not _git_is_clean(wt_path):
        message = (
            "\n".join(
                f"- deps: bump {name} from {old} to {new}."
                for name, old, new in bumps
            )
            + "\n"
        )
        add_result = sp.run(
            ["git", "add", "pyproject.toml", "uv.lock"],
            cwd=wt_path,
            check=False,
            timeout=sp.SHORT_TIMEOUT_SECONDS,
        )
        if add_result.returncode != 0:
            _eprint(f"git add failed in {wt_path}; worktree kept.")
            return ExitCode.ERROR
        commit_result = sp.run(
            ["git", "commit", "-m", message],
            cwd=wt_path,
            check=False,
            timeout=sp.SHORT_TIMEOUT_SECONDS,
        )
        if commit_result.returncode != 0:
            _eprint(f"git commit failed in {wt_path}; worktree kept.")
            return ExitCode.ERROR
        print(f"committed {len(bumps)} bump(s) in {wt_path}")

    print("running dogfood suite against the bumped pins...")
    # Scope to the quality-gate subset (``shared/tests``) rather than
    # the full suite. A tool-version bump only changes what ruff /
    # mypy / mdformat report, and ``shared/tests`` runs them against
    # every tracked file -- the exact signal a bump needs. The full
    # suite additionally drives the CLI integration tests, each of
    # which spawns a nested ``upgrade(-tools)`` that builds a venv and
    # runs its own dogfood; that nesting is redundant for a tool bump
    # and slow enough under contention to exhaust the timeout.
    test_result = sp.run(
        [
            "uv",
            "run",
            "--locked",
            "--extra",
            "test",
            "pytest",
            "shared/tests",
        ],
        cwd=wt_path,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if test_result.returncode != 0:
        _eprint(
            f"dogfood suite failed; worktree {wt_path} kept for "
            "inspection. Fix and commit in the worktree, or rerun "
            "``upgrade-tools`` to test the committed bump again."
        )
        return ExitCode.ERROR

    if not args.push:
        print(
            f"\nbump committed at {wt_path}; pass --push to ff-merge "
            f"into {default_branch} and push, or merge by hand."
        )
        return ExitCode.SUCCESS

    return _ff_merge_and_push_then_cleanup(
        consumer_root=repo_root,
        wt_path=wt_path,
        branch=branch,
        upstream_ref=upstream_ref,
        default_branch=default_branch,
        keep_worktree=args.keep_worktree,
        has_origin=_has_origin(repo_root),
    )


def _cmd_init(args: argparse.Namespace) -> ExitCode:
    repo_root = _resolve_consumer_root(args.path)
    refusal = _refuse_init_target(repo_root)
    if refusal is not None:
        return refusal
    if not _git_repo(repo_root):
        _eprint(f"not a git repo: {repo_root}")
        return ExitCode.ERROR

    source_url = args.source or _DEFAULT_SOURCE
    if _ensure_repo_shared_dep(repo_root, source_url=source_url) != 0:
        _eprint(
            "uv could not add epilatow-repo-shared to "
            f"{repo_root / 'pyproject.toml'}; resolve and re-run."
        )
        return ExitCode.ERROR

    # Cleanup first, then vendor: a stale canonical-path symlink left
    # by an older repo-shared (e.g. a file that has since moved to a
    # template kind) must be cleared before vendor() decides whether
    # to seed a template copy at that path.
    cleanup_stale_vendored(repo_root)
    result = vendor(repo_root)

    print(f"init complete in {repo_root}")
    print("  vendored shared/ -> _repo_shared/")
    print(f"  symlinks created: {len(result.installed)}")
    if result.seeded:
        print(f"  templates seeded: {len(result.seeded)}")
        for p in result.seeded:
            print(f"    {p.relative_to(repo_root)}")
    if result.updated:
        print(
            f"  templates updated to the new upstream: {len(result.updated)}"
        )
        for p in result.updated:
            print(f"    {p.relative_to(repo_root)}")
    if result.skipped_ignored:
        print(
            f"  canonical paths opted out via .repo-shared-ignore: "
            f"{len(result.skipped_ignored)}"
        )
    if result.out_of_sync:
        print(
            f"  canonical paths out of sync with the upstream: "
            f"{len(result.out_of_sync)}"
        )
        for path, reason in result.out_of_sync:
            print(f"    {path.relative_to(repo_root)}: {reason}")
        print(
            "  -- align each entry above with the upstream (delete a "
            "shadowing local file then re-run ``init`` for a symlink "
            "kind; copy the upstream from ``_repo_shared/<kind>/<rel>`` "
            "over your copy for a template kind), or list the path "
            "in ``.repo-shared-ignore`` to keep your own version."
        )
        return ExitCode.ERROR
    return ExitCode.SUCCESS


def _cmd_upgrade(args: argparse.Namespace) -> ExitCode:
    """Sync the consumer to a target repo-shared SHA via a worktree.

    Always orchestrates a worktree at
    ``<consumer>/.wt/repo-shared-update-<short-sha>`` on a branch
    ``repo-shared/update-<short-sha>``. The lock-bump + re-vendor +
    commit happen there. Tests run in the worktree (when
    ``--run-tests`` or ``--push`` is set). On ``--push`` + green,
    the worktree branch ff-merges into the consumer's default
    branch in the main checkout, gets pushed, and is pruned.

    Failed test runs leave the worktree in place for debugging --
    the next ``upgrade`` invocation auto-detects and either
    resumes (target SHA + ff-mergeable) or recreates fresh
    (the default branch moved on, ff-merge no longer clean).
    """
    consumer_root = _resolve_consumer_root(args.path)
    refusal = _refuse_when_target_is_source("upgrade", consumer_root)
    if refusal is not None:
        return refusal
    if not _git_repo(consumer_root):
        _eprint(f"not a git repo: {consumer_root}")
        return ExitCode.ERROR

    source = args.source or _DEFAULT_SOURCE
    target_sha = args.sha or _resolve_upstream_head(source)
    if target_sha is None:
        _eprint(
            f"could not resolve a target SHA from {source}. "
            "Pass an explicit <sha> argument."
        )
        return ExitCode.ERROR

    current_sha = _read_locked_sha(consumer_root)
    if current_sha is None:
        _eprint(
            "no epilatow-repo-shared pin found in uv.lock -- this "
            "consumer hasn't been onboarded. Run `repo-shared init` "
            "first."
        )
        return ExitCode.CONFIG
    short = target_sha[:7]
    if current_sha == target_sha:
        print(f"already at {short}; nothing to do.")
        return ExitCode.SUCCESS

    if not _git_is_clean(consumer_root):
        _eprint(
            "working tree has uncommitted changes; refusing to "
            "upgrade. Commit or stash, then re-run."
        )
        return ExitCode.DIRTY

    has_origin = _has_origin(consumer_root)
    if (
        has_origin
        and sp.run(
            ["git", "fetch", "origin"],
            cwd=consumer_root,
            check=False,
            timeout=sp.LONG_TIMEOUT_SECONDS,
        ).returncode
        != 0
    ):
        _eprint("git fetch origin failed.")
        return ExitCode.ERROR

    default_branch = _default_branch(consumer_root)
    if default_branch is None:
        _eprint(
            "could not determine the default branch; ensure the "
            "consumer has a local ``main`` / ``master`` branch, or an "
            "``origin`` remote with HEAD set."
        )
        return ExitCode.ERROR
    # The worktree (and the staleness ff-check) build on this ref. With
    # an ``origin`` it is the fetched remote tip; a local-only consumer
    # has none, so it is the local branch. ``--base`` overrides either
    # so an upgrade can sit on top of local work that isn't pushed yet.
    upstream_ref = args.base or (
        f"origin/{default_branch}" if has_origin else default_branch
    )

    # A local-only consumer has nowhere to push, so --push means "land
    # the ff-merge locally"; the pre-flight push check only applies when
    # there is an origin to push to.
    if (
        has_origin
        and args.push
        and not _can_push(consumer_root, default_branch)
    ):
        _eprint(
            f"git push --dry-run origin {default_branch} failed; "
            "refusing to do upgrade work that will fail to push at "
            "the end. Resolve the upstream-permission / fast-forward "
            "issue and rerun."
        )
        return ExitCode.ERROR

    branch = f"repo-shared/update-{short}"
    wt_path = consumer_root / ".wt" / f"repo-shared-update-{short}"

    setup = _ensure_update_worktree(
        consumer_root=consumer_root,
        branch=branch,
        wt_path=wt_path,
        upstream_ref=upstream_ref,
        target_sha=target_sha,
        force_retry=args.force_retry,
    )
    if isinstance(setup, ExitCode):
        return setup

    if setup == "needs-work":
        if (
            _do_inner_upgrade(
                wt_path=wt_path,
                source=source,
                target_sha=target_sha,
                old_sha=current_sha,
            )
            != 0
        ):
            _eprint(
                f"worktree at {wt_path} carries the partial state. "
                "cd in to debug; rerun ``upgrade`` to retry."
            )
            return ExitCode.ERROR
    else:
        print(f"resuming existing update worktree at {wt_path}.")

    if not (args.run_tests or args.push):
        print(
            f"upgrade staged at {wt_path}; pass --run-tests to validate "
            "or --push to validate + ff-merge + push."
        )
        return ExitCode.SUCCESS

    cmd = _read_test_command(wt_path)
    print(f"running tests: {' '.join(cmd)}")
    test_result = sp.run(
        cmd,
        cwd=wt_path,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if test_result.returncode != 0:
        _eprint(
            f"tests failed; worktree {wt_path} kept for debug. "
            "Fix the issue + commit in the worktree, then rerun "
            "``upgrade`` to retry."
        )
        return ExitCode.ERROR
    print("tests passed.")

    if not args.push:
        return ExitCode.SUCCESS

    return _ff_merge_and_push_then_cleanup(
        consumer_root=consumer_root,
        wt_path=wt_path,
        branch=branch,
        upstream_ref=upstream_ref,
        default_branch=default_branch,
        keep_worktree=args.keep_worktree,
        has_origin=has_origin,
    )


def _resolve_upstream_head(source: str) -> str | None:
    """Resolve the SHA at HEAD of ``source``'s default branch."""
    url = source.removeprefix("git+")
    result = sp.run(
        ["git", "ls-remote", url, "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split()[0]


def _has_origin(repo_root: Path) -> bool:
    """True when the repo has an ``origin`` remote configured.

    A local-only consumer (e.g. a ``git.local`` repo never pushed to a
    forge) has none, so the origin-dependent steps of an upgrade --
    the fetch, ``origin/HEAD`` discovery, and the push -- are skipped.
    """
    return (
        sp.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _default_branch(repo_root: Path) -> str | None:
    """Return the repo's default branch, or ``None`` if undeterminable.

    Prefers ``origin/HEAD``'s target. A local-only repo has no origin,
    so fall back to a local ``main`` / ``master``, else the current
    branch.
    """
    result = sp.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        ref = result.stdout.strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    for candidate in ("main", "master"):
        if _git_branch_exists(repo_root, candidate):
            return candidate
    return _current_branch(repo_root)


def _git_branch_exists(repo_root: Path, branch: str) -> bool:
    return (
        sp.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
            cwd=repo_root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _git_branch_ff_mergeable(
    repo_root: Path, branch: str, upstream_ref: str
) -> bool:
    """True iff merge-base(branch, upstream_ref) == upstream_ref's tip."""
    base = sp.run(
        ["git", "merge-base", branch, upstream_ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    tip = sp.run(
        ["git", "rev-parse", upstream_ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if base.returncode != 0 or tip.returncode != 0:
        return False
    return base.stdout.strip() == tip.stdout.strip()


def _remove_worktree(consumer_root: Path, wt_path: Path) -> None:
    if wt_path.exists():
        sp.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=consumer_root,
            check=False,
        )


def _delete_branch(consumer_root: Path, branch: str) -> None:
    sp.run(
        ["git", "branch", "-D", branch],
        cwd=consumer_root,
        capture_output=True,
        check=False,
    )


def _ensure_update_worktree(
    *,
    consumer_root: Path,
    branch: str,
    wt_path: Path,
    upstream_ref: str,
    target_sha: str,
    force_retry: bool,
) -> ExitCode | str:
    """Create or resume the update worktree.

    Returns ``"needs-work"`` if the caller should run the lock /
    re-vendor / commit steps, ``"resume"`` if the existing worktree
    already pins the target SHA and ff-merges cleanly, or an
    ``ExitCode`` on failure.
    """
    branch_exists = _git_branch_exists(consumer_root, branch)
    worktree_exists = wt_path.is_dir() and (wt_path / ".git").exists()

    if branch_exists and worktree_exists:
        if not _git_is_clean(wt_path):
            if not force_retry:
                _eprint(
                    f"existing worktree {wt_path} has uncommitted "
                    "changes; refusing to recreate. Commit / stash / "
                    "discard there, or pass --force-retry."
                )
                return ExitCode.DIRTY
            _remove_worktree(consumer_root, wt_path)
            _delete_branch(consumer_root, branch)
        else:
            wt_sha = _read_locked_sha(wt_path)
            ff = _git_branch_ff_mergeable(consumer_root, branch, upstream_ref)
            if wt_sha == target_sha and ff:
                return "resume"
            # Stale: target SHA mismatch or the default branch moved
            # past the branch's parent. Drop and recreate.
            _remove_worktree(consumer_root, wt_path)
            _delete_branch(consumer_root, branch)
    elif branch_exists and not worktree_exists:
        _delete_branch(consumer_root, branch)
    elif worktree_exists and not branch_exists:
        # Stray worktree dir without a tracked branch; clean up.
        _remove_worktree(consumer_root, wt_path)

    wt_path.parent.mkdir(parents=True, exist_ok=True)
    create = sp.run(
        [
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(wt_path),
            upstream_ref,
        ],
        cwd=consumer_root,
        check=False,
    )
    if create.returncode != 0:
        _eprint(f"git worktree add failed for {wt_path}; resolve and rerun.")
        return ExitCode.ERROR
    return "needs-work"


def _do_inner_upgrade(
    *,
    wt_path: Path,
    source: str,
    target_sha: str,
    old_sha: str,
) -> int:
    """Bump uv.lock + re-vendor + commit inside ``wt_path``.

    Spawns ``uv run --project <wt_path>`` for the re-vendor step so
    the freshly-locked package version is what supplies the shared
    content. Returns the subprocess exit code.
    """
    add_cmd = [
        "uv",
        "add",
        "--frozen",
        f"epilatow-repo-shared @ {source}@{target_sha}",
    ]
    if (
        sp.run(
            add_cmd,
            cwd=wt_path,
            check=False,
            timeout=sp.LONG_TIMEOUT_SECONDS,
        ).returncode
        != 0
    ):
        _eprint("uv add (lock bump) failed in worktree.")
        return 1

    new_sha = _read_locked_sha(wt_path)
    if new_sha is None:
        _eprint("could not read SHA from worktree's uv.lock.")
        return 1

    revendor = sp.run(
        [
            "uv",
            "run",
            "--project",
            str(wt_path),
            "repo-shared",
            "_revendor",
            str(wt_path),
        ],
        cwd=wt_path,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if revendor.returncode != 0:
        _eprint("re-vendor failed in worktree.")
        return 1

    if _git_is_clean(wt_path):
        # The lock + re-vendor combination produced byte-identical
        # files (target SHA was already pinned, or vendored content
        # at the new SHA happens to match what was already in
        # ``_repo_shared/``). Nothing to commit; the upgrade is a
        # no-op against this consumer.
        print(f"upgraded to {new_sha[:7]}; no file changes.")
        return 0

    return _git_commit_upgrade(wt_path, old_sha=old_sha, new_sha=new_sha)


def _current_branch(repo_root: Path) -> str | None:
    """Return the current branch name in ``repo_root``, or None.

    ``None`` covers the detached-HEAD case where there isn't a
    branch to capture; the caller treats that the same as "on the
    default branch already" -- no warning, no restore.
    """
    result = sp.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    if not name or name == "HEAD":
        return None
    return name


def _ff_merge_and_push_then_cleanup(
    *,
    consumer_root: Path,
    wt_path: Path,
    branch: str,
    upstream_ref: str,
    default_branch: str,
    keep_worktree: bool,
    has_origin: bool,
) -> ExitCode:
    """ff-merge worktree branch into default branch, push, cleanup.

    With an ``origin`` the merged default branch is pushed; a
    local-only consumer has none, so the merge lands locally and
    nothing is pushed.

    If the main checkout is on a non-default branch (e.g. a
    feature branch the maintainer is mid-work on), this function
    needs to switch to the default branch to do the ff-merge.
    Capture the prior branch and restore it after the merge lands
    so the user comes back to where they were -- with an
    informational note so the temporary switch isn't silent.
    """
    # Re-fetch in case origin moved while we tested (no-op with no origin).
    if has_origin:
        sp.run(
            ["git", "fetch", "origin"],
            cwd=consumer_root,
            check=False,
            timeout=sp.LONG_TIMEOUT_SECONDS,
        )
    if not _git_branch_ff_mergeable(consumer_root, branch, upstream_ref):
        _eprint(
            f"{branch} no longer ff-merges into {upstream_ref} "
            "(the default branch moved while we tested). Worktree "
            "kept; rerun ``upgrade`` to redo on the new tip."
        )
        return ExitCode.ERROR

    prior_branch = _current_branch(consumer_root)
    if prior_branch is not None and prior_branch != default_branch:
        print(
            f"note: {consumer_root.name} was on {prior_branch}; "
            f"temporarily switching to {default_branch} to land "
            f"the ff-merge, then restoring."
        )

    checkout = sp.run(
        ["git", "checkout", default_branch],
        cwd=consumer_root,
        check=False,
    )
    if checkout.returncode != 0:
        _eprint(f"could not check out {default_branch}; aborting push.")
        return ExitCode.ERROR

    merge = sp.run(
        ["git", "merge", "--ff-only", branch],
        cwd=consumer_root,
        check=False,
    )
    if merge.returncode != 0:
        _eprint("ff-merge failed; aborting push.")
        return ExitCode.ERROR

    if has_origin:
        push = sp.run(
            ["git", "push", "origin", default_branch],
            cwd=consumer_root,
            check=False,
            timeout=sp.LONG_TIMEOUT_SECONDS,
        )
        if push.returncode != 0:
            _eprint(
                "git push failed. The merge is in place locally; resolve "
                "the upstream rejection and push manually."
            )
            return ExitCode.ERROR
        print(f"pushed {default_branch}.")
    else:
        print(
            f"ff-merged into {default_branch}; no origin remote, so "
            "nothing to push."
        )

    if not keep_worktree:
        _remove_worktree(consumer_root, wt_path)
        _delete_branch(consumer_root, branch)
        print(f"cleaned up worktree {wt_path} and branch {branch}.")

    if prior_branch is not None and prior_branch != default_branch:
        restore = sp.run(
            ["git", "checkout", prior_branch],
            cwd=consumer_root,
            check=False,
        )
        if restore.returncode != 0:
            _eprint(
                f"could not restore {prior_branch}; main checkout "
                f"is on {default_branch}. ``git checkout "
                f"{prior_branch}`` manually."
            )
            return ExitCode.ERROR
        print(f"restored {consumer_root.name} to {prior_branch}.")
    return ExitCode.SUCCESS


def _read_test_command(repo_root: Path) -> list[str]:
    """Return the consumer-configured test command, or the default.

    Reads ``[tool.repo-shared] test-command`` from ``pyproject.toml``
    -- a string is split on whitespace, a list is taken as-is. Falls
    back to running the shared tests at their vendored path
    (``_repo_shared/tests``); pytest's conftest walk-up
    from there never reaches the consumer's ``tests/conftest.py`` so
    no ``--confcutdir`` flag is needed for isolation.
    """
    pyproject = repo_root / "pyproject.toml"
    default = [
        "uv",
        "run",
        "pytest",
        "_repo_shared/tests",
    ]
    if not pyproject.is_file():
        return default
    try:
        import tomllib
    except ImportError:
        return default
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return default
    raw = data.get("tool", {}).get("repo-shared", {}).get("test-command")
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.split()
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        return list(raw)
    _eprint(
        "[tool.repo-shared] test-command must be a string or a list of "
        "strings; falling back to the default."
    )
    return default


def _snapshot_vendored_paths(repo_root: Path) -> set[Path]:
    """Return the set of real-file paths under ``_repo_shared/`` now."""
    snapshot: set[Path] = set()
    vendor_dir = repo_root / VENDOR_DIRNAME
    if not vendor_dir.is_dir():
        return snapshot
    for path in vendor_dir.rglob("*"):
        if path.is_file() and not path.is_symlink():
            snapshot.add(path)
    return snapshot


def _cmd_revendor(args: argparse.Namespace) -> ExitCode:
    """Internal: re-vendor only (no lock update, no commit).

    Spawned by ``upgrade`` after the lock update so the re-vendor runs
    in a fresh ``uv run`` that picks up the just-installed new package
    version. Not for direct human use.

    Returns ERROR if any canonical-path entry is out of sync with the
    upstream (a symlink shadowed by a local file, a template copy that
    has drifted, ...) so the upgrade aborts on a divergence instead of
    leaving the consumer's tree silently broken. Every violation is
    surfaced at once -- the consumer fixes them all (sync to the
    upstream or list in ``.repo-shared-ignore``) before re-running
    ``upgrade``.
    """
    repo_root = _resolve_consumer_root(args.path)

    # Cleanup before vendor so a stale canonical-path symlink from an
    # older repo-shared is cleared before vendor() decides whether to
    # seed a template copy at that path.
    cleanup_stale_vendored(repo_root)
    result = vendor(repo_root)
    if result.out_of_sync:
        for path, reason in result.out_of_sync:
            _eprint(f"  {path.relative_to(repo_root)}: {reason}")
        _eprint(
            "Aborting upgrade: align each path above with the upstream, "
            "or list it in .repo-shared-ignore."
        )
        return ExitCode.ERROR

    # Self-heal runs only on the clean path: re-inject testpaths so a
    # consumer that manually edited it away gets it back. Deferred
    # until after the out-of-sync check so an aborting upgrade does
    # not mutate the consumer's pyproject.
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        _inject_shared_testpaths(pyproject)
    return ExitCode.SUCCESS


def _cmd_status(args: argparse.Namespace) -> ExitCode:
    repo_root = _resolve_consumer_root(args.path)
    refusal = _refuse_when_target_is_source("status", repo_root)
    if refusal is not None:
        return refusal
    if not _git_repo(repo_root):
        _eprint(f"not a git repo: {repo_root}")
        return ExitCode.ERROR
    sha = _read_locked_sha(repo_root)
    if sha is None:
        print("no epilatow-repo-shared pin found in uv.lock")
        return ExitCode.SUCCESS
    print(f"pinned: {sha}")

    shared_root = package_shared_root()
    drift: list[str] = []
    extras: list[str] = []
    broken: list[str] = []
    expected: set[Path] = set()
    for kind, src, rel in iter_shared(shared_root):
        vendor_path, _link = consumer_paths(repo_root, kind, rel)
        expected.add(vendor_path)
        if not vendor_path.exists():
            drift.append(f"missing: _repo_shared/{kind}/{rel}")
            continue
        if src.read_bytes() != vendor_path.read_bytes():
            drift.append(f"drift: _repo_shared/{kind}/{rel}")
    vendor_dir = repo_root / VENDOR_DIRNAME
    if vendor_dir.is_dir():
        for path in vendor_dir.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            if _is_vendor_runtime_artifact(path, vendor_dir):
                continue
            if path not in expected:
                extras.append(f"extra: {path.relative_to(repo_root)}")
        try:
            vendor_resolved = vendor_dir.resolve(strict=False)
        except OSError:
            vendor_resolved = vendor_dir
        for link in _iter_canonical_symlinks(repo_root, vendor_resolved):
            # ``_iter_canonical_symlinks`` yields every symlink under
            # the consumer that resolves into the vendor dir. Two
            # shapes show up: (a) canonical-path symlinks that ``init``
            # / ``upgrade`` placed at known consumer-visible locations,
            # and (b) user-placed extras (e.g. a ``claude/<file>``
            # symlink the consumer added to expose shared content in a
            # subdirectory). Both are legitimate as long as the target
            # vendor file still exists; only flag symlinks whose target
            # is gone (a vendor file removed by a prior upgrade that
            # ``cleanup_stale_vendored`` should have caught -- belt and
            # braces).
            if link.resolve(strict=False).exists():
                continue
            broken.append(f"broken symlink: {link.relative_to(repo_root)}")
    issues = drift + extras + broken
    if issues:
        _eprint("issues:")
        for line in issues:
            _eprint(f"  {line}")
        return ExitCode.ERROR
    print("vendor in sync.")
    return ExitCode.SUCCESS


def _cmd_run_tests(args: argparse.Namespace) -> ExitCode:
    """Run the delivered shared tests at ``_repo_shared/tests``.

    Convenience wrapper for ``uv run pytest _repo_shared/tests`` in
    the consumer's project venv. ``uv run`` is used (rather than
    ``sys.executable -m pytest``) so the consumer's venv is the
    runtime regardless of how the CLI was invoked (wrapper script,
    ``uv tool``, or ``uvx``) -- the ephemeral venvs from the latter
    two don't carry the consumer's other deps and would either lack
    ``pytest`` or run the gates against the wrong file set.

    Refuses when invoked from a repo-shared clone (the delivered
    tests dogfood via the source-tree ``testpaths`` entry there;
    ``uv run pytest shared/tests`` is the equivalent for
    maintainers). Returncode mapping mirrors ``pytest``'s own:
    0 -> ``SUCCESS``, 1 -> ``WARNING`` (test failures), anything
    else -> ``ERROR`` (collection failure, interrupt, etc.).
    """
    refusal = _refuse_run_tests_from_clone()
    if refusal is not None:
        return refusal
    repo_root = _resolve_consumer_root(args.path)
    shared_tests = repo_root / VENDOR_DIRNAME / "tests"
    if not shared_tests.is_dir():
        _eprint(
            f"no {VENDOR_DIRNAME}/tests under {repo_root}; run "
            "`repo-shared init` first to onboard this repo."
        )
        return ExitCode.CONFIG
    cmd = [
        "uv",
        "run",
        "--project",
        str(repo_root),
        "pytest",
        f"{VENDOR_DIRNAME}/tests",
    ]
    if args.verbose:
        cmd.append("-v")
    result = sp.run(
        cmd,
        cwd=repo_root,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    if result.returncode in (0, 1):
        return ExitCode(result.returncode)
    return ExitCode.ERROR


def _iter_canonical_symlinks(
    repo_root: Path, vendor_resolved: Path
) -> Iterator[Path]:
    """Yield symlinks under ``repo_root`` that point into the vendor dir.

    Skips anything inside ``_repo_shared/`` itself (those symlinks
    are internal to the vendor layout). Best-effort directory walk
    that doesn't descend into symlinked directories to avoid loops.
    """
    for child in repo_root.iterdir():
        if child.name == VENDOR_DIRNAME:
            continue
        yield from _walk_for_vendor_links(child, vendor_resolved)


def _walk_for_vendor_links(
    path: Path, vendor_resolved: Path
) -> Iterator[Path]:
    if path.is_symlink():
        try:
            target = path.resolve(strict=False)
        except OSError:
            return
        try:
            target.relative_to(vendor_resolved)
        except ValueError:
            return
        yield path
        return
    if path.is_dir():
        for child in path.iterdir():
            yield from _walk_for_vendor_links(child, vendor_resolved)


def args_parser() -> argparse.ArgumentParser:
    """Build the CLI parser with context-aware subcommand visibility.

    The same module ships from a repo-shared clone and from every
    consumer's installed package, but which subcommands are usable
    depends on the state of the repo the CLI is invoked in (the cwd,
    classified by ``_classify_repo``):

    - ``SOURCE`` (a repo-shared clone) -- ``init`` / ``upgrade`` /
      ``status`` operate on another repo passed by path, plus the
      maintainer-only ``upgrade-tools``. ``run-tests`` is hidden: it
      must run through the consumer's own pinned version.
    - ``ONBOARDED`` (a consumer) -- ``upgrade`` / ``status`` /
      ``run-tests`` against the implicit cwd target. ``init`` is
      hidden (already onboarded); ``upgrade-tools`` is hidden
      (maintainer-only).
    - ``PLAIN`` (not yet onboarded) -- only ``init`` makes sense.

    ``_revendor`` is always hidden (subprocess-only entry point
    called by ``_cmd_upgrade``).

    ``help=argparse.SUPPRESS`` on ``add_parser`` doesn't actually
    suppress entries in the subparser listing in current argparse
    (the literal ``==SUPPRESS==`` string prints through), so
    visibility is enforced by omitting ``help=`` entirely on
    hidden commands and by passing a custom ``metavar`` to
    ``add_subparsers`` that lists only the visible ones.
    """
    cwd_state = _classify_repo(Path.cwd())
    if cwd_state is RepoState.SOURCE:
        visible = ["init", "upgrade", "status", "upgrade-tools"]
    elif cwd_state is RepoState.ONBOARDED:
        visible = ["upgrade", "status", "run-tests"]
    else:
        visible = ["init"]

    parser = argparse.ArgumentParser(
        prog="repo-shared",
        description="Manage shared dev conventions vendored from "
        "epilatow/repo-shared into a consumer repo.",
        epilog=ExitCode.epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(
        dest="command",
        metavar="{" + ",".join(visible) + "}",
    )

    def _help_for(name: str, text: str) -> dict[str, Any]:
        """Return ``help=text`` kwarg only when ``name`` is visible.

        Omitting ``help=`` (rather than setting it to
        ``argparse.SUPPRESS``) is what actually hides the
        subcommand's description line; the metavar above handles
        the top-of-help choices listing.
        """
        return {"help": text} if name in visible else {}

    def _add_repo_arg(parser: argparse.ArgumentParser, help_text: str) -> None:
        """Add the ``--repo`` target option.

        The target repo is an option, not a positional, so it never
        collides with ``upgrade``'s optional ``sha`` positional --
        two optional positionals bind ambiguously (a lone argument
        fills the first), which would misread ``upgrade <path>`` as
        a SHA.
        """
        parser.add_argument(
            "--repo",
            dest="path",
            default=None,
            metavar="PATH",
            help=help_text,
        )

    p_init = sub.add_parser(
        "init", **_help_for("init", "onboard a repo for the first time")
    )
    _add_repo_arg(
        p_init, "repo to onboard (default: cwd; required from a clone)"
    )
    p_init.add_argument(
        "--source",
        default=None,
        help=f"git URL for epilatow-repo-shared (default: {_DEFAULT_SOURCE})",
    )

    p_up = sub.add_parser(
        "upgrade",
        **_help_for("upgrade", "bump pinned SHA and re-vendor"),
    )
    p_up.add_argument(
        "sha",
        nargs="?",
        default=None,
        help="specific SHA to pin (default: default branch HEAD)",
    )
    _add_repo_arg(p_up, "consumer repo root (default: cwd)")
    p_up.add_argument(
        "--source",
        default=None,
        help=f"git URL for epilatow-repo-shared (default: {_DEFAULT_SOURCE})",
    )
    p_up.add_argument(
        "--run-tests",
        action="store_true",
        help=(
            "after the upgrade commit, run the consumer's test command "
            "(``[tool.repo-shared] test-command`` in pyproject.toml; "
            "defaults to ``uv run pytest _repo_shared/tests``). "
            "Non-zero exit if tests fail; the commit stays in place "
            "for inspection."
        ),
    )
    p_up.add_argument(
        "--push",
        action="store_true",
        help=(
            "implies --run-tests; on green, ff-merge the update "
            "branch into the consumer's default branch and "
            "``git push``. Use to fully automate updates."
        ),
    )
    p_up.add_argument(
        "--force-retry",
        action="store_true",
        help=(
            "if an existing update worktree carries uncommitted "
            "changes, drop it anyway and recreate fresh on the upgrade "
            "base. Without this, dirty worktrees block the upgrade so "
            "debug edits aren't silently dropped."
        ),
    )
    p_up.add_argument(
        "--keep-worktree",
        action="store_true",
        help=(
            "after a successful --push, leave the update worktree "
            "and branch in place. Default is to prune both."
        ),
    )
    p_up.add_argument(
        "--base",
        default=None,
        metavar="REF",
        help=(
            "git ref to build the update worktree on top of "
            "(default: origin's default branch). Use to base the "
            "upgrade on local, possibly-unpushed work -- e.g. "
            "``--base main`` or ``--base HEAD``."
        ),
    )

    p_status = sub.add_parser(
        "status",
        **_help_for("status", "show pinned SHA and any vendor drift"),
    )
    _add_repo_arg(p_status, "consumer repo root (default: cwd)")

    p_run_tests = sub.add_parser(
        "run-tests",
        description=(
            "Run the delivered shared tests (code-quality, mdformat, "
            "markdownlint, drift, in-sync) against the consumer's "
            "repo. Equivalent to running `uv run pytest "
            "_repo_shared/tests` by hand from the consumer root -- "
            "use this form directly to pass additional pytest flags "
            "(e.g. `-k`, `--lf`) that ``run-tests`` doesn't forward."
        ),
        **_help_for(
            "run-tests",
            "run the delivered shared tests "
            "(== `uv run pytest _repo_shared/tests`)",
        ),
    )
    _add_repo_arg(p_run_tests, "consumer repo root (default: cwd)")
    p_run_tests.add_argument(
        "-v", "--verbose", action="store_true", help="verbose pytest output"
    )

    # Internal subcommand spawned by ``upgrade`` to re-vendor in a
    # fresh ``uv run`` after the lock update. Always hidden.
    p_revendor = sub.add_parser("_revendor")
    p_revendor.add_argument("path", nargs="?", default=None)

    # upgrade-tools: maintainer-only bump of pinned tool deps
    # (ruff / mypy / mdformat) in repo-shared's own pyproject. The
    # ``visible`` list excludes it from the consumer-side --help.
    # A runtime guard in _cmd_upgrade_tools rejects invocation
    # from a consumer even if a user types the name anyway.
    p_upgrade_tools = sub.add_parser(
        "upgrade-tools",
        **_help_for(
            "upgrade-tools",
            "bump pinned ruff / mypy / mdformat in repo-shared's "
            "pyproject.toml",
        ),
    )
    p_upgrade_tools.add_argument(
        "--only",
        action="append",
        metavar="PKG",
        help=(
            "limit the bump to the named pinned package; repeat to "
            "include multiple. Default: bump every exact-pinned dep."
        ),
    )
    p_upgrade_tools.add_argument(
        "--push",
        action="store_true",
        help=(
            "after the committed bump passes dogfood in the worktree, "
            "ff-merge its branch into repo-shared's default branch "
            "and push to origin. Probes with "
            "``git push --dry-run`` before doing any bump work "
            "so an upstream rejection fails fast."
        ),
    )
    p_upgrade_tools.add_argument(
        "--keep-worktree",
        action="store_true",
        help=(
            "after a successful --push, leave the bump worktree "
            "and branch in place. Default is to prune both."
        ),
    )
    p_upgrade_tools.add_argument(
        "--force-retry",
        action="store_true",
        help=(
            "if an existing bump worktree carries uncommitted "
            "changes, drop it anyway and recreate fresh on "
            "origin's default branch. Without this, dirty "
            "worktrees block the bump so debug edits aren't "
            "silently dropped."
        ),
    )

    return parser


def main(args: argparse.Namespace) -> ExitCode:
    handlers = {
        "init": _cmd_init,
        "upgrade": _cmd_upgrade,
        "status": _cmd_status,
        "run-tests": _cmd_run_tests,
        "_revendor": _cmd_revendor,
        "upgrade-tools": _cmd_upgrade_tools,
    }
    handler = handlers.get(args.command)
    if handler is None:
        return ExitCode.USAGE
    try:
        return handler(args)
    except subprocess.TimeoutExpired as err:
        _eprint(
            f"subprocess timed out after {err.timeout}s: {err.cmd!r}",
        )
        return ExitCode.TIMEOUT


def cli() -> int:
    parser = args_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return int(ExitCode.USAGE)
    return int(main(args))


if __name__ == "__main__":
    sys.exit(cli())
