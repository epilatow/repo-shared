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
from pathlib import Path
from typing import Any

from epilatow_repo_shared import sp
from epilatow_repo_shared.exit_codes import ExitCode
from epilatow_repo_shared.vendor import (
    VENDOR_DIRNAME,
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
      a re-run of ``init`` against an already-onboarded consumer is
      a no-op for resolution (``uv add`` sees the dep already
      satisfied by ``uv.lock`` and skips the git-fetch), so the SHA
      never advances past whatever first onboarded the consumer. With
      the explicit pin, every ``init`` re-run matches an ``upgrade``
      to current HEAD.
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
        timeout=sp.TESTS_TIMEOUT_SECONDS,
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
    import tomlkit  # noqa: PLC0415  (deferred so CLI startup stays cheap)

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


def _refuse_when_repo_shared(subcommand: str) -> ExitCode | None:
    """Return ``USAGE`` exit if invoked from a repo-shared clone.

    ``init`` / ``upgrade`` / ``status`` manipulate a consumer's
    vendored ``_repo_shared/`` content; they have no meaning in
    the source repo. The parser also hides these from ``--help``
    when running from repo-shared, but a user could still type the
    name. Returning ``None`` means "carry on".
    """
    if _running_from_local_repo_shared() is None:
        return None
    _eprint(
        f"``{subcommand}`` is a consumer-side subcommand and only "
        "makes sense from a repo that pins epilatow-repo-shared. "
        "Run it from a consumer instead."
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
    """True iff ``wt_path``'s ``pyproject.toml`` carries the targets.

    Used by the resume path: when a worktree from a prior run
    still exists at the same deterministic branch name AND its
    pyproject already pins every target version, the bump work is
    already done -- we just need to re-run tests + push (whichever
    failed last time).
    """
    pyproject = wt_path / "pyproject.toml"
    if not pyproject.is_file():
        return False
    actual = dict(_read_pinned_deps(pyproject.read_text()))
    return all(actual.get(name) == new for name, _old, new in bumps)


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
            # Stale: targets diverged or master moved past the
            # branch's parent. Drop and recreate.
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
        timeout=sp.GENERAL_TIMEOUT_SECONDS,
    )
    if create.returncode != 0:
        _eprint(f"git worktree add failed for {wt_path}; resolve and rerun.")
        return ExitCode.SUBPROCESS
    return "needs-work"


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
    state-change. Safe for nightly automation.

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
    if not _git_is_clean(repo_root):
        _eprint(
            "working tree has uncommitted changes; upgrade-tools "
            "must start clean (the worktree spawns from the "
            "current branch tip)."
        )
        return ExitCode.DIRTY

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
        timeout=sp.GENERAL_TIMEOUT_SECONDS,
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
        wt_text = wt_pyproject.read_text()
        for name, old, new in bumps:
            old_pin = f'"{name}=={old}"'
            new_pin = f'"{name}=={new}"'
            if old_pin not in wt_text:
                _eprint(
                    f"could not find {old_pin} for in-place "
                    f"rewrite in {wt_pyproject}; worktree left "
                    f"at {wt_path}."
                )
                return ExitCode.ERROR
            wt_text = wt_text.replace(old_pin, new_pin)
        wt_pyproject.write_text(wt_text)

        lock_result = sp.run(
            ["uv", "lock"],
            cwd=wt_path,
            check=False,
            timeout=sp.GENERAL_TIMEOUT_SECONDS,
        )
        if lock_result.returncode != 0:
            _eprint(
                f"uv lock failed after bump; worktree {wt_path} "
                "kept for inspection."
            )
            return ExitCode.SUBPROCESS
    else:
        print(f"resuming existing bump worktree at {wt_path}.")

    print("running dogfood suite against the bumped pins...")
    test_result = sp.run(
        ["uv", "run", "--extra", "test", "pytest"],
        cwd=wt_path,
        check=False,
        timeout=sp.TESTS_TIMEOUT_SECONDS,
    )
    if test_result.returncode != 0:
        _eprint(
            f"dogfood suite failed; worktree {wt_path} kept for "
            "inspection. Fix + commit in the worktree or remove "
            "it and rerun ``upgrade-tools``."
        )
        return ExitCode.ERROR

    # Only commit if there's something to commit; on resume the
    # bump commit already exists from the prior run.
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
            timeout=sp.GENERAL_TIMEOUT_SECONDS,
        )
        if add_result.returncode != 0:
            _eprint(f"git add failed in {wt_path}; worktree kept.")
            return ExitCode.ERROR
        commit_result = sp.run(
            ["git", "commit", "-m", message],
            cwd=wt_path,
            check=False,
            timeout=sp.GENERAL_TIMEOUT_SECONDS,
        )
        if commit_result.returncode != 0:
            _eprint(f"git commit failed in {wt_path}; worktree kept.")
            return ExitCode.ERROR
        print(f"committed {len(bumps)} bump(s) in {wt_path}")

    if not args.push:
        print(
            f"\nbump staged at {wt_path}; pass --push to ff-merge "
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
    )


def _cmd_init(args: argparse.Namespace) -> ExitCode:
    refusal = _refuse_when_repo_shared("init")
    if refusal is not None:
        return refusal
    repo_root = _resolve_consumer_root(args.path)
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
        print(f"  templates updated to the new master: {len(result.updated)}")
        for p in result.updated:
            print(f"    {p.relative_to(repo_root)}")
    if result.skipped_ignored:
        print(
            f"  canonical paths opted out via .repo-shared-ignore: "
            f"{len(result.skipped_ignored)}"
        )
    if result.out_of_sync:
        print(
            f"  canonical paths out of sync with the master: "
            f"{len(result.out_of_sync)}"
        )
        for path, reason in result.out_of_sync:
            print(f"    {path.relative_to(repo_root)}: {reason}")
        print(
            "  -- align each entry above with the master (delete a "
            "shadowing local file then re-run ``init`` for a symlink "
            "kind; copy the master from ``_repo_shared/<kind>/<rel>`` "
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
    (master moved on, ff-merge no longer clean).
    """
    refusal = _refuse_when_repo_shared("upgrade")
    if refusal is not None:
        return refusal
    consumer_root = _resolve_consumer_root(args.path)
    if not _git_repo(consumer_root):
        _eprint(f"not a git repo: {consumer_root}")
        return ExitCode.ERROR
    if not _git_is_clean(consumer_root):
        _eprint(
            "working tree has uncommitted changes; refusing to "
            "upgrade. Commit or stash, then re-run."
        )
        return ExitCode.DIRTY

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

    if (
        sp.run(
            ["git", "fetch", "origin"], cwd=consumer_root, check=False
        ).returncode
        != 0
    ):
        _eprint("git fetch origin failed.")
        return ExitCode.ERROR

    default_branch = _default_branch(consumer_root)
    if default_branch is None:
        _eprint(
            "could not determine origin's default branch; ensure the "
            "consumer has an ``origin`` remote with HEAD set."
        )
        return ExitCode.ERROR
    upstream_ref = f"origin/{default_branch}"

    if args.push and not _can_push(consumer_root, default_branch):
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
        timeout=sp.TESTS_TIMEOUT_SECONDS,
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
    )


def _resolve_upstream_head(source: str) -> str | None:
    """Resolve the SHA at HEAD of ``source``'s default branch."""
    url = source[len("git+") :] if source.startswith("git+") else source
    result = sp.run(
        ["git", "ls-remote", url, "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split()[0]


def _default_branch(repo_root: Path) -> str | None:
    """Return ``origin/HEAD``'s branch name (e.g. ``master`` or ``main``)."""
    result = sp.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    prefix = "refs/remotes/origin/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None


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
            # Stale: target SHA mismatch or master moved past the
            # branch's parent. Drop and recreate.
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
    if sp.run(add_cmd, cwd=wt_path, check=False).returncode != 0:
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
) -> ExitCode:
    """ff-merge worktree branch into default branch, push, cleanup.

    If the main checkout is on a non-default branch (e.g. a
    feature branch the maintainer is mid-work on), this function
    needs to switch to the default branch to do the ff-merge.
    Capture the prior branch and restore it after the push lands
    so the user comes back to where they were -- with an
    informational note so the temporary switch isn't silent.
    """
    # Re-fetch in case origin moved while we tested.
    sp.run(["git", "fetch", "origin"], cwd=consumer_root, check=False)
    if not _git_branch_ff_mergeable(consumer_root, branch, upstream_ref):
        _eprint(
            f"{branch} no longer ff-merges into {upstream_ref} "
            "(master moved while we tested). Worktree kept; rerun "
            "``upgrade`` to redo on the new master."
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

    push = sp.run(
        ["git", "push", "origin", default_branch],
        cwd=consumer_root,
        check=False,
    )
    if push.returncode != 0:
        _eprint(
            "git push failed. The merge is in place locally; resolve "
            "the upstream rejection and push manually."
        )
        return ExitCode.ERROR
    print(f"pushed {default_branch}.")

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
        import tomllib  # noqa: PLC0415
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
    master (a symlink shadowed by a local file, a template copy that
    has drifted, ...) so the upgrade aborts on a divergence instead of
    leaving the consumer's tree silently broken. Every violation is
    surfaced at once -- the consumer fixes them all (sync to the
    master or list in ``.repo-shared-ignore``) before re-running
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
            "Aborting upgrade: align each path above with the master, "
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
    refusal = _refuse_when_repo_shared("status")
    if refusal is not None:
        return refusal
    repo_root = _resolve_consumer_root(args.path)
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

    Refuses when invoked from a repo-shared clone (the four
    delivered tests dogfood via the source-tree ``testpaths`` entry
    there; ``uv run pytest shared/tests`` is the equivalent for
    maintainers). Returncode mapping mirrors ``pytest``'s own:
    0 -> ``SUCCESS``, 1 -> ``WARNING`` (test failures), anything
    else -> ``ERROR`` (collection failure, interrupt, etc.).
    """
    refusal = _refuse_when_repo_shared("run-tests")
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
        timeout=sp.TESTS_TIMEOUT_SECONDS,
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

    The same module ships from a repo-shared clone (maintainer)
    and from every consumer's installed package, but only a subset
    of subcommands is meaningful in each context:

    - ``init`` / ``upgrade`` / ``status`` / ``run-tests`` -- only in a
      consumer.
    - ``upgrade-tools`` -- only in a repo-shared clone (maintainer-side
      dev shortcut for bumping the pinned tool deps).
    - ``_revendor`` -- always hidden (subprocess-only entry point
      called by ``_cmd_upgrade``).

    ``help=argparse.SUPPRESS`` on ``add_parser`` doesn't actually
    suppress entries in the subparser listing in current argparse
    (the literal ``==SUPPRESS==`` string prints through), so
    visibility is enforced by omitting ``help=`` entirely on
    hidden commands and by passing a custom ``metavar`` to
    ``add_subparsers`` that lists only the visible ones.
    """
    is_repo_shared = _running_from_local_repo_shared() is not None
    if is_repo_shared:
        visible = ["upgrade-tools"]
    else:
        visible = ["init", "upgrade", "status", "run-tests"]

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

    p_init = sub.add_parser(
        "init", **_help_for("init", "onboard a repo for the first time")
    )
    p_init.add_argument(
        "path",
        nargs="?",
        default=None,
        help="consumer repo root (default: cwd)",
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
    p_up.add_argument(
        "path",
        nargs="?",
        default=None,
        help="consumer repo root (default: cwd)",
    )
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
            "changes, drop it anyway and recreate fresh on origin's "
            "default branch. Without this, dirty worktrees block "
            "the upgrade so debug edits aren't silently dropped."
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

    p_status = sub.add_parser(
        "status",
        **_help_for("status", "show pinned SHA and any vendor drift"),
    )
    p_status.add_argument(
        "path",
        nargs="?",
        default=None,
        help="consumer repo root (default: cwd)",
    )

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
    p_run_tests.add_argument(
        "path",
        nargs="?",
        default=None,
        help="consumer repo root (default: cwd)",
    )
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
            "after a green dogfood run + commit in the worktree, "
            "ff-merge the bump branch into repo-shared's default "
            "branch and push to origin. Probes with "
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
