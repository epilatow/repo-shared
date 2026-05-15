# This is AI generated code
"""Shared test infrastructure for repo-shared's own suite."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_pycache_tmpdir = tempfile.mkdtemp(prefix="pytest_pycache_repo_shared_")
sys.pycache_prefix = _pycache_tmpdir
sys.dont_write_bytecode = True


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    shutil.rmtree(_pycache_tmpdir, ignore_errors=True)
    repo_root = Path(__file__).resolve().parent.parent
    for cache in repo_root.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
    for mypy_cache in repo_root.rglob(".mypy_cache"):
        if mypy_cache.is_dir():
            shutil.rmtree(mypy_cache, ignore_errors=True)
