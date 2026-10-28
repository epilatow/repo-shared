# This is AI generated code
"""Shared test infrastructure for repo-shared's own suite."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from epilatow_repo_shared.exit_codes import ExitCode

# Programs the tests write out and run: probes, conftest files, and
# stand-in suites, kept as real files, where the quality gates check
# the Python ones.
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def git_init_repo(repo: Path) -> None:
    """``git init`` ``repo`` with a stable user identity.

    Discovery helpers honor ``.gitignore`` via ``git ls-files``, so
    tests that exercise the gitignore-driven path need a real git
    repo under ``tmp_path``. Identity is pinned so the init doesn't
    fall through to the developer's global ``user.email`` /
    ``user.name`` (or fail if neither is set).
    """
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"], check=True
    )


def git_clone(source: Path, dest: Path, *, bare: bool = False) -> None:
    """``git clone`` the local repo ``source`` into ``dest``.

    A clone from a local path copies the source's ``objects/``
    directory file by file, so anything writing there at the same
    time can rename or delete a file mid-copy and fail the clone: a
    fetch or push into the source, or the automatic maintenance that
    a commit, fetch, or incoming push in the source or any of its
    worktrees starts in the background. ``--no-local`` fetches
    through Git's transport instead, which reads objects through the
    object database and keeps working while packs are written or
    replaced.
    """
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-local",
            *(["--bare"] if bare else []),
            str(source),
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


def git_in(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo`` with a pinned test identity."""
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test Author",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def run_cli(argv: list[str]) -> ExitCode:
    """Run the repo-shared CLI in-process on ``argv``."""
    # Imported here rather than at the top so the CLI's modules load
    # after this file's bytecode settings below.
    from epilatow_repo_shared import cli

    parser = cli.args_parser()
    args = parser.parse_args(argv)
    return cli.main(args)


def advance_origin_to_diverge(origin: Path, tmp_path: Path) -> None:
    """Plant a commit directly on ``origin``'s main so it diverges.

    Used to simulate "non-fast-forward push rejected" scenarios:
    after this, any consumer / maintainer-clone tied to ``origin``
    will have its local ``main`` strictly behind ``origin/main``, and
    ``git push --dry-run origin main:main`` rejects.
    """
    workspace = tmp_path / f"{origin.stem}-divergent-workspace"
    git_clone(origin, workspace)
    (workspace / "ORIGIN_DIVERGENCE_MARKER").write_text("plant\n")
    git_in(workspace, "add", "ORIGIN_DIVERGENCE_MARKER")
    git_in(workspace, "commit", "-m", "test: divergent commit on origin")
    git_in(workspace, "push", "--quiet", "origin", "main")


_pycache_tmpdir = tempfile.mkdtemp(prefix="pytest_pycache_repo_shared_")
sys.pycache_prefix = _pycache_tmpdir
sys.dont_write_bytecode = True


def pytest_sessionfinish() -> None:
    shutil.rmtree(_pycache_tmpdir, ignore_errors=True)
    repo_root = Path(__file__).resolve().parent.parent
    for cache in repo_root.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
    for mypy_cache in repo_root.rglob(".mypy_cache"):
        if mypy_cache.is_dir():
            shutil.rmtree(mypy_cache, ignore_errors=True)
