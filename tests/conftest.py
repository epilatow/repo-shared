# This is AI generated code
"""Shared test infrastructure for repo-shared's own suite."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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
