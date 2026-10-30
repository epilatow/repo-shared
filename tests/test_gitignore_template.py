# This is AI generated code
"""Behavior of the shipped ``.gitignore`` template under real git."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import git_in, git_init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "shared" / "dottemplates" / "gitignore"

# Directories a checkout on slow storage may keep elsewhere.
RELOCATABLE = (
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "htmlcov",
    ".wt",
    "tmp",
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with the template committed as its ``.gitignore``."""
    path = tmp_path / "repo"
    path.mkdir()
    git_init_repo(path)
    shutil.copyfile(TEMPLATE, path / ".gitignore")
    git_in(path, "add", ".gitignore")
    git_in(path, "commit", "-m", "baseline")
    return path


@pytest.mark.parametrize("name", RELOCATABLE)
def test_relocated_directory_symlink_is_ignored(
    repo: Path, tmp_path: Path, name: str
) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (repo / name).symlink_to(target, target_is_directory=True)
    assert git_in(repo, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize("name", RELOCATABLE)
def test_real_directory_is_ignored(repo: Path, name: str) -> None:
    (repo / name).mkdir()
    (repo / name / "file").write_text("state\n")
    assert git_in(repo, "status", "--porcelain").stdout == ""


def test_nested_tmp_directory_is_ignored(repo: Path) -> None:
    (repo / "src" / "tmp").mkdir(parents=True)
    (repo / "src" / "tmp" / "file").write_text("state\n")
    assert git_in(repo, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize("name", ["build", "dist", "scripts/tmp"])
def test_ordinary_file_is_not_ignored(repo: Path, name: str) -> None:
    (repo / name).parent.mkdir(exist_ok=True)
    (repo / name).write_text("file\n")
    status = git_in(repo, "status", "--porcelain", "--untracked-files=all")
    assert status.stdout == f"?? {name}\n"
