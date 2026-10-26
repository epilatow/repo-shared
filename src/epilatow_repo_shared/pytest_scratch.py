"""Locate pytest's numbered temporary runs outside system temporary storage."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from epilatow_repo_shared import sp
from epilatow_repo_shared.runtime_environment import (
    prepare_scratch_parent,
    temporary_environment,
)

_TEMPROOT = "PYTEST_DEBUG_TEMPROOT"
_INHERITED_TEMPROOT = "REPO_SHARED_PYTEST_TEMPROOT"


def _main_checkout(root: Path) -> Path | None:
    """Resolve linked and separate-git-dir checkouts through Git's registry."""
    if not any((p / ".git").exists() for p in (root, *root.parents)):
        return None
    result = sp.run(
        ["git", "-C", str(root), "worktree", "list", "--porcelain", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    first = result.stdout.split("\0", 1)[0]
    if not first.startswith("worktree "):
        raise OSError("Git did not report a main checkout for pytest scratch")
    main = Path(first.removeprefix("worktree ")).resolve()
    if (main / ".git").exists():
        return main
    # Separate metadata may have no registry backlink to the main checkout.
    common = sp.run(
        [
            "git",
            "-C",
            str(root),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    common_dir = Path(common.stdout.rstrip("\n")).resolve()
    for candidate in (root, *root.parents):
        if not (candidate / ".git").exists():
            continue
        git_dir = sp.run(
            ["git", "-C", str(candidate), "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            check=True,
        )
        if Path(git_dir.stdout.rstrip("\n")).resolve() == common_dir:
            return candidate.resolve()
    raise ValueError(
        "Git cannot identify the main checkout for pytest scratch; "
        "set PYTEST_DEBUG_TEMPROOT to a stable directory outside worktrees"
    )


@contextmanager
def pytest_scratch(root: Path, invocation_dir: Path) -> Iterator[None]:
    """Preserve explicit overrides and rebind inherited defaults per repo.

    Pytest owns numbering, locks, retention, and any explicit --basetemp.
    The marker distinguishes our inherited parent from a caller's override.
    """
    current = os.environ.get(_TEMPROOT)
    inherited = os.environ.get(_INHERITED_TEMPROOT)
    if current is not None and current != inherited:
        yield
        return
    parent = default_pytest_temproot(root, invocation_dir)
    prepare_scratch_parent(parent)
    with temporary_environment(
        {_TEMPROOT: str(parent), _INHERITED_TEMPROOT: str(parent)}
    ):
        yield


def default_pytest_temproot(root: Path, invocation_dir: Path) -> Path:
    """Use the main checkout, or the invocation directory outside Git."""
    root = root.resolve()
    invocation_dir = invocation_dir.resolve()
    candidates = (
        (invocation_dir, root)
        if invocation_dir.is_relative_to(root)
        else (root, invocation_dir)
    )
    checkout = _main_checkout(candidates[0]) or _main_checkout(candidates[1])
    return (checkout or invocation_dir.resolve()) / "tmp" / "pytest"
