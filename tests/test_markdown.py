# This is AI generated code
"""Unit tests for ``MdformatCheckBase`` / ``MarkdownlintCheckBase``."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import git_init_repo

from epilatow_repo_shared import sp
from epilatow_repo_shared.markdown import (
    MarkdownlintCheckBase,
    MdformatCheckBase,
    _walk_markdown,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOTFILES = _REPO_ROOT / "shared" / "dotfiles"


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


def _stub_npx(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Pretend ``npx`` exists and capture the markdownlint argv.

    Returns a dict the caller reads ``["cmd"]`` from after invoking
    the check; ``sp.run`` is stubbed to a clean (returncode 0) result
    so the gate passes without a real lint run.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/npx")
    captured: dict[str, list[str]] = {}

    def fake_run(
        cmd: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(sp, "run", fake_run)
    return captured


def test_markdownlint_feeds_no_globs_literal_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovered files become ``--no-globs`` ``:``-prefixed paths.

    No positional glob reaches ``markdownlint-cli2`` -- the discovered
    file list is passed as literal paths so the tool walks nothing.
    Paths are repo-relative POSIX (the run's cwd is the repo root) and
    sorted by ``_walk_markdown``. (Discovery here uses the non-git
    ``os.walk`` fallback so the ``sp.run`` stub only intercepts the
    ``npx`` call; git-tracked discovery is exercised by the real-``npx``
    test below.)
    """
    captured = _stub_npx(monkeypatch)
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "b.md").write_text("# b\n")

    class _Check(MarkdownlintCheckBase):
        repo_root = tmp_path

    _Check().test_markdownlint_clean()
    assert captured["cmd"] == [
        "npx",
        "--yes",
        "markdownlint-cli2",
        "--no-globs",
        ":a.md",
        ":docs/b.md",
    ]


def test_markdownlint_exclude_dirs_drop_from_path_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An excluded directory is omitted from the literal-path list."""
    captured = _stub_npx(monkeypatch)
    (tmp_path / "a.md").write_text("# a\n")
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "c.md").write_text("# c\n")

    class _Check(MarkdownlintCheckBase):
        repo_root = tmp_path
        exclude_dirs = ("gen",)

    _Check().test_markdownlint_clean()
    assert captured["cmd"] == [
        "npx",
        "--yes",
        "markdownlint-cli2",
        "--no-globs",
        ":a.md",
    ]


def test_markdownlint_skips_when_no_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No discovered ``*.md`` -> skip, never an empty markdownlint run."""
    _stub_npx(monkeypatch)

    class _Check(MarkdownlintCheckBase):
        repo_root = tmp_path

    with pytest.raises(pytest.skip.Exception):
        _Check().test_markdownlint_clean()


def test_markdownlint_rejects_multi_segment_exclude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A multi-segment exclude entry fails loudly, as in mdformat.

    Both markdown gates share ``_normalize_exclude_dirs``, so a
    path-prefix entry like ``docs/_build`` is rejected before any
    lint run rather than silently changing scope.
    """
    _stub_npx(monkeypatch)

    class _Check(MarkdownlintCheckBase):
        repo_root = tmp_path
        exclude_dirs = ("docs/_build",)

    with pytest.raises(ValueError, match="contains '/'"):
        _Check().test_markdownlint_clean()


def _seed_markdownlint_consumer(tmp_path: Path) -> None:
    """Init a git repo under ``tmp_path`` with the canonical config.

    Copies the three markdownlint dotfiles (rule config, cli2 entry,
    custom rule) to their dot-prefixed consumer paths so the real tool
    runs against the same rules a consumer would. Skips the test when
    ``npx`` is unavailable.
    """
    if shutil.which("npx") is None:
        pytest.skip("npx not available")
    git_init_repo(tmp_path)
    for name in (
        "markdownlint-cli2.jsonc",
        "markdownlint.json",
        "markdownlint-rule-no-squashed-file-references.mjs",
    ):
        shutil.copyfile(_DOTFILES / name, tmp_path / f".{name}")


def test_markdownlint_exclude_dirs_drop_real_dir(tmp_path: Path) -> None:
    """Against real ``markdownlint-cli2``, the right files are linted.

    Drives the real tool over the canonical config to confirm the
    explicit-path + ``--no-globs`` wiring works end to end: a
    lint-violating file under the excluded dir fails the gate by
    default and passes once the dir is excluded, a sibling violation
    outside it keeps failing, and a gitignored ``.venv`` markdown file
    is never linted (the gate feeds only the paths ``git ls-files``
    reports, so the tool never walks into ignored dirs).
    """
    _seed_markdownlint_consumer(tmp_path)
    (tmp_path / ".gitignore").write_text(".venv/\n")
    (tmp_path / "ok.md").write_text("# Title\n\nClean prose.\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "bad.md").write_text("no heading here\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.md").write_text("no heading here\n")

    class _Default(MarkdownlintCheckBase):
        repo_root = tmp_path

    class _Excluded(MarkdownlintCheckBase):
        repo_root = tmp_path
        exclude_dirs = ("generated",)

    with pytest.raises(AssertionError):
        _Default().test_markdownlint_clean()
    _Excluded().test_markdownlint_clean()

    (tmp_path / "also_bad.md").write_text("no heading here\n")
    with pytest.raises(AssertionError):
        _Excluded().test_markdownlint_clean()


def test_markdownlint_custom_rule_fires_under_no_globs(
    tmp_path: Path,
) -> None:
    """The custom rule still applies with ``--no-globs`` + literal paths.

    ``--no-globs`` only drops the config's ``globs``; ``customRules``
    registration must survive it, else markdownlint would silently stop
    enforcing ``no-squashed-file-references``. Feed a file that trips
    only that rule and assert the gate fails naming it.
    """
    _seed_markdownlint_consumer(tmp_path)
    (tmp_path / "squashed.md").write_text("# T\n\n@a.md @b.md @c.md\n")

    class _Check(MarkdownlintCheckBase):
        repo_root = tmp_path

    with pytest.raises(AssertionError, match="no-squashed-file-references"):
        _Check().test_markdownlint_clean()
