# This is AI generated code
"""Pytest bases for mdformat and markdownlint checks.

Both bases discover their targets the same way: ``_walk_markdown``
returns the real-file ``*.md`` under ``repo_root`` that ``git
ls-files`` reports -- tracked or untracked-but-not-ignored, so
``.gitignore`` is honored -- dropping symlinks and a configurable set
of directory names, and the base shells the explicit file list to its
tool. This deliberately avoids glob expansion: a tree walk over a repo
carrying a large ``.venv`` / ``.cache`` is both slow and wrong (those
files are not ours to lint), so neither gate ever lets the tool expand
a ``**`` glob.

``MdformatCheckBase`` runs ``python -m mdformat --wrap=N --number
--check`` against the discovered files. ``MarkdownlintCheckBase``
shells out to ``npx markdownlint-cli2 --no-globs`` with the discovered
files as ``:``-prefixed literal paths -- ``--no-globs`` drops the
config's ``globs`` so markdownlint-cli2 walks nothing, while the
config's lint rules and custom rules still apply. It **fails** if
``npx`` is missing -- markdownlint gates rules ``mdformat`` cannot see
(broken anchor links, duplicate headings, the custom
``no-squashed-file-references`` rule, ...), so silently skipping when
Node isn't installed leaves those rules unenforced. The consumer's
Requirements section in the README lists ``npx`` as mandatory.

Both bases route the ``exclude_dirs`` knob through
``_normalize_exclude_dirs`` so consumers see the same accept / reject
rules across the two markdown gates.

Subclass per consumer test file::

    from epilatow_repo_shared.markdown import MdformatCheckBase

    class TestMarkdownFormat(MdformatCheckBase):
        pass  # repo_root and wrap default to repo containing tests/

The default ``repo_root`` is two directories up from ``__file__``,
which matches the conventional ``tests/test_*.py`` placement. Override
``repo_root`` / ``wrap`` / ``exclude_dirs`` on the subclass to
customise.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from epilatow_repo_shared import sp
from epilatow_repo_shared.python_quality import (
    _git_tracked_files,
    _normalize_exclude_dirs,
    _path_has_excluded_dir_segment,
)

# ``_repo_shared`` is tracked, but the canonical-path symlinks
# expose its content at a second path, so the markdown gates would
# otherwise check it twice.
_DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = ("_repo_shared",)


def _discover_repo_root() -> Path:
    """Best-effort default ``repo_root`` for a subclass test file.

    Walks up from ``cwd`` looking for a ``.git`` marker; falls back to
    cwd itself. Subclasses that need a different root override the
    class attribute directly.
    """
    here = Path.cwd()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return here


def _walk_markdown(
    root: Path,
    exclude_dirs: Sequence[str],
) -> list[Path]:
    """Yield tracked real-file ``*.md`` under ``root``.

    Symlinks are dropped so canonical-path aliases over the
    vendored ``_repo_shared/`` tree don't produce duplicate hits.
    ``exclude_dirs`` is an additional post-filter; see
    ``_normalize_exclude_dirs`` and
    ``_path_has_excluded_dir_segment``.

    Falls back to ``os.walk`` when ``root`` is not a git working
    tree -- the unit-test case using ``tmp_path``.
    """
    skip = _normalize_exclude_dirs(exclude_dirs)
    tracked = _git_tracked_files(root, ".md")
    if tracked is not None:
        targets: list[Path] = []
        for rel in tracked:
            if _path_has_excluded_dir_segment(rel, skip):
                continue
            full = root / rel
            if full.is_symlink() or not full.is_file():
                continue
            targets.append(full)
        return sorted(targets)
    targets = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue
            targets.append(full)
    return sorted(targets)


class MdformatCheckBase:
    """Assert ``mdformat --check`` is clean across the repo's ``*.md``."""

    repo_root: ClassVar[Path] = _discover_repo_root()
    wrap: ClassVar[int] = 79
    exclude_dirs: ClassVar[tuple[str, ...]] = _DEFAULT_EXCLUDE_DIRS

    def test_mdformat_check_clean(self) -> None:
        targets = _walk_markdown(self.repo_root, self.exclude_dirs)
        if not targets:
            pytest.skip("no *.md files under repo_root")
        result = sp.run(
            [
                sys.executable,
                "-m",
                "mdformat",
                f"--wrap={self.wrap}",
                "--number",
                "--check",
                *(str(p) for p in targets),
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=sp.LONG_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise AssertionError(
                "mdformat reported drift. To canonicalise, run from "
                "the repo root: uvx --with mdformat-gfm --with "
                f"mdformat-tables mdformat --wrap={self.wrap} --number "
                "<path>\n\n"
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )


class MarkdownlintCheckBase:
    """Assert ``markdownlint-cli2`` is clean across the repo.

    Targets come from ``_walk_markdown`` -- the ``*.md`` ``git
    ls-files`` reports under ``repo_root`` (honoring ``.gitignore``),
    minus ``exclude_dirs`` -- and are passed to ``markdownlint-cli2
    --no-globs`` as ``:``-prefixed literal paths. ``--no-globs``
    suppresses the config's ``globs`` so the tool walks nothing (no
    ``.venv`` / ``.cache`` traversal, no ignored files linted); the
    config's lint rules and custom rules still apply because
    ``--no-globs`` only drops ``globs``.

    The subclass wires the consumer's
    ``[tool.repo-shared.markdown] extra-exclude-dirs`` into
    ``exclude_dirs``, sharing the ``_normalize_exclude_dirs`` accept /
    reject rules with ``MdformatCheckBase`` so both markdown gates
    discover the same files and honor the same knob.
    """

    repo_root: ClassVar[Path] = _discover_repo_root()
    exclude_dirs: ClassVar[tuple[str, ...]] = _DEFAULT_EXCLUDE_DIRS

    def test_markdownlint_clean(self) -> None:
        if shutil.which("npx") is None:
            raise AssertionError(
                "``npx`` is not on PATH; markdownlint cannot run. "
                "Install Node (e.g. ``brew install node`` on macOS, "
                "``apt install nodejs npm`` on Debian / Ubuntu) so the "
                "consumer's markdown is gated against rules ``mdformat`` "
                "can't see (broken anchor links, duplicate headings, the "
                "custom ``no-squashed-file-references`` rule, ...). The "
                "Node requirement is documented under Requirements in "
                "repo-shared's README."
            )
        targets = _walk_markdown(self.repo_root, self.exclude_dirs)
        if not targets:
            pytest.skip("no *.md files under repo_root")
        literal_paths = [
            f":{p.relative_to(self.repo_root).as_posix()}" for p in targets
        ]
        result = sp.run(
            [
                "npx",
                "--yes",
                "markdownlint-cli2",
                "--no-globs",
                *literal_paths,
            ],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=sp.LONG_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise AssertionError(
                "markdownlint-cli2 reported violations. Run "
                "`npx markdownlint-cli2 --fix` from the repo root to "
                "auto-fix what's fixable, then resolve the rest "
                "manually.\n\n"
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )
