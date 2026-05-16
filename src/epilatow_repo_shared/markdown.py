# This is AI generated code
"""Pytest bases for mdformat and markdownlint checks.

Both bases walk a configured repo root for ``*.md`` files (excluding a
configurable set of directory prefixes) and shell out to the relevant
tool. ``MdformatCheckBase`` runs ``python -m mdformat --wrap=N --number
--check`` against the discovered files; ``MarkdownlintCheckBase`` runs
``npx markdownlint-cli2`` and **fails** if ``npx`` is missing -- markdownlint
gates rules ``mdformat`` cannot see (broken anchor links, duplicate
headings, the custom ``no-squashed-file-references`` rule, ...), so
silently skipping when Node isn't installed leaves those rules unenforced.
The consumer's Requirements section in the README lists ``npx`` as
mandatory.

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
from epilatow_repo_shared.python_quality import _normalize_exclude_dirs

_DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    "node_modules",
    ".venv",
    ".pytest_cache",
    ".git",
    ".claude",
    "tmp",
    ".cache",
    "_repo_shared",
)


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
    """Yield ``*.md`` real files under ``root``, pruning excluded dirs.

    Uses ``os.walk`` with in-place ``dirnames`` mutation so a
    populated ``.venv/`` / ``_repo_shared/`` is never enumerated.
    Each entry in ``exclude_dirs`` is a directory name pruned
    anywhere in the tree; see ``_normalize_exclude_dirs``.
    """
    skip = _normalize_exclude_dirs(exclude_dirs)
    targets: list[Path] = []
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
    """Assert ``markdownlint-cli2`` is clean across the repo."""

    repo_root: ClassVar[Path] = _discover_repo_root()

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
        result = sp.run(
            ["npx", "--yes", "markdownlint-cli2"],
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
