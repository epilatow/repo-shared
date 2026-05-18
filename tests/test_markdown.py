# This is AI generated code
"""Unit tests for ``MdformatCheckBase`` / ``MarkdownlintCheckBase``."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import git_init_repo

from epilatow_repo_shared.markdown import (
    MarkdownlintCheckBase,
    MdformatCheckBase,
    _walk_markdown,
)


def test_walk_markdown_basic(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("# b\n")
    found = _walk_markdown(tmp_path, exclude_dirs=())
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in found)
    assert rels == ["a.md", "sub/b.md"]


def test_walk_markdown_prunes_top_level_dir(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.md").write_text("# skip\n")
    found = _walk_markdown(tmp_path, exclude_dirs=("node_modules",))
    rels = [p.relative_to(tmp_path).as_posix() for p in found]
    assert rels == ["a.md"]


def test_walk_markdown_prunes_nested_dir_by_name(tmp_path: Path) -> None:
    """A bare directory name prunes the dir anywhere in the tree.

    The old prefix-based shape needed a separate ``**/tmp/`` pattern
    to handle nested instances; the new os.walk + name-pruned shape
    handles top-level and nested instances with one entry.
    """
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "tmp").mkdir()
    (tmp_path / "deep" / "tmp" / "skip.md").write_text("# skip\n")
    found = _walk_markdown(tmp_path, exclude_dirs=("tmp",))
    rels = [p.relative_to(tmp_path).as_posix() for p in found]
    assert rels == ["a.md"]


def test_walk_markdown_strips_legacy_trailing_slash(tmp_path: Path) -> None:
    """Trailing ``/`` in an entry is tolerated for backward compat."""
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "skip.md").write_text("# skip\n")
    found = _walk_markdown(tmp_path, exclude_dirs=("build/",))
    rels = [p.relative_to(tmp_path).as_posix() for p in found]
    assert rels == ["a.md"]


def test_walk_markdown_strips_legacy_glob_prefix(tmp_path: Path) -> None:
    """Legacy ``**/<dir>/`` entries collapse to bare directory names."""
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "cache").mkdir()
    (tmp_path / "deep" / "cache" / "skip.md").write_text("# skip\n")
    found = _walk_markdown(tmp_path, exclude_dirs=("**/cache/",))
    rels = [p.relative_to(tmp_path).as_posix() for p in found]
    assert rels == ["a.md"]


def test_walk_markdown_rejects_multi_segment_entry(tmp_path: Path) -> None:
    """Multi-segment entries like ``docs/_build`` are rejected loudly.

    The old prefix-from-root semantic supported them; the new
    directory-name semantic doesn't, and silently changing behaviour
    would mask consumer misconfiguration. Raise so the operator sees
    the issue before the test run drifts.
    """
    with pytest.raises(ValueError, match="contains '/'"):
        _walk_markdown(tmp_path, exclude_dirs=("docs/_build",))


def test_walk_markdown_honors_gitignore(tmp_path: Path) -> None:
    """When ``root`` is a git working tree, ``.gitignore`` controls
    what's enumerated -- a sibling worktree under ``.wt/`` or any
    other ignored content is excluded automatically."""
    git_init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".wt/\nbuild/\n")
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.md").write_text("# skip\n")
    (tmp_path / ".wt" / "branch").mkdir(parents=True)
    (tmp_path / ".wt" / "branch" / "sibling.md").write_text("# skip\n")

    found = _walk_markdown(tmp_path, exclude_dirs=())
    rels = [p.relative_to(tmp_path).as_posix() for p in found]
    assert rels == ["a.md"]


def _ensure_mdformat() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import mdformat"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("mdformat not importable in test env")


def test_mdformat_check_passes_on_clean_tree(tmp_path: Path) -> None:
    _ensure_mdformat()
    md = tmp_path / "a.md"
    md.write_text("# Title\n\nA paragraph that fits within 79 chars.\n")
    canonicalize = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdformat",
            "--wrap=79",
            "--number",
            str(md),
        ],
        capture_output=True,
        check=False,
    )
    assert canonicalize.returncode == 0

    class _Check(MdformatCheckBase):
        repo_root = tmp_path

    _Check().test_mdformat_check_clean()


def test_mdformat_check_fails_on_dirty_tree(tmp_path: Path) -> None:
    _ensure_mdformat()
    (tmp_path / "a.md").write_text("Some text without trailing newline")

    class _Check(MdformatCheckBase):
        repo_root = tmp_path

    with pytest.raises(AssertionError):
        _Check().test_mdformat_check_clean()


def test_markdownlint_fails_without_npx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing ``npx`` must fail loudly, not silently skip.

    Markdownlint gates rules ``mdformat`` cannot see (broken anchor
    links, the custom ``no-squashed-file-references`` rule, ...); a
    silent skip on Node-less envs would leave those rules unenforced.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    class _Check(MarkdownlintCheckBase):
        repo_root = tmp_path

    with pytest.raises(AssertionError) as excinfo:
        _Check().test_markdownlint_clean()
    msg = str(excinfo.value)
    assert "npx" in msg
    assert "Install Node" in msg
