# This is AI generated code
"""Pytest base keeping consumers off the packages repo-shared pins.

repo-shared pins its tools and libraries exactly, and those pins bind
every consumer's resolver. A consumer's own constraint, source, or URL
for one of them resolves only while it admits repo-shared's pin, so it
can sit unnoticed until a bump makes ``upgrade`` unresolvable. An
override goes further and replaces the pin, so the consumer stops running
the version its repo-shared SHA promises. ``ToolPinsBase`` fails each of
these when it is written, before either problem can show.

Requirements in a PEP 723 block or ``build-system.requires`` are out of
scope: they resolve in environments of their own, so they can't conflict
with the consumer's project.

Markers are deliberately not evaluated. The gate could only answer for
the host running it, so a pin guarded by one would pass here and block
``upgrade`` elsewhere.

Subclass per consumer test file::

    from epilatow_repo_shared.tool_pins import ToolPinsBase

    class TestToolPins(ToolPinsBase):
        pass
"""

from __future__ import annotations

import glob
import os
import re
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from importlib.metadata import requires
from pathlib import Path
from typing import ClassVar

import pytest
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier
from packaging.utils import canonicalize_name
from packaging.version import Version

from epilatow_repo_shared.vendor import _is_repo_shared_source_root

# Operators that cap the versions a requirement admits. ``!=`` caps
# nothing, but can still exclude a release; ``_can_reject_a_bump``
# compares it to the pin.
_CAPPING_OPERATORS = frozenset({"==", "===", "~=", "<", "<="})

# The name that opens a PEP 508 requirement. Read this way so a
# requirement only uv accepts still names its package.
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


@dataclass(frozen=True)
class PinConflict:
    """A consumer entry that can block or replace a repo-shared pin."""

    section: str
    entry: str
    pin: str


def repo_shared_tool_pins() -> dict[str, str]:
    """Map each exactly pinned package's canonical name to its pin."""
    pins: dict[str, str] = {}
    for raw in requires("epilatow-repo-shared") or []:
        requirement = Requirement(raw)
        if any(spec.operator == "==" for spec in requirement.specifier):
            pins[canonicalize_name(requirement.name)] = str(requirement)
    return pins


def _table(parent: Mapping[str, object], key: str) -> dict[str, object]:
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    """Return an array's strings; a group's includes are tables."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _declared_requirements(
    pyproject: Mapping[str, object],
) -> Iterator[tuple[str, str]]:
    """Yield ``(section, requirement)`` for each one uv must satisfy."""
    project = _table(pyproject, "project")
    for raw in _strings(project.get("dependencies")):
        yield "project.dependencies", raw
    extras = _table(project, "optional-dependencies")
    for extra, value in extras.items():
        for raw in _strings(value):
            yield f"project.optional-dependencies.{extra}", raw
    for group, value in _table(pyproject, "dependency-groups").items():
        for raw in _strings(value):
            yield f"dependency-groups.{group}", raw
    uv = _table(_table(pyproject, "tool"), "uv")
    for key in ("dev-dependencies", "constraint-dependencies"):
        for raw in _strings(uv.get(key)):
            yield f"tool.uv.{key}", raw


def _pin_for(raw: str, pins: Mapping[str, str]) -> str | None:
    """Return the pin on the package a requirement names, if any."""
    name = _REQUIREMENT_NAME.match(raw)
    return pins.get(canonicalize_name(name.group())) if name else None


def _can_reject_a_bump(specifier: Specifier, pinned: Version) -> bool:
    if specifier.operator == "!=":
        return Version(specifier.version.removesuffix(".*")) >= pinned
    return specifier.operator in _CAPPING_OPERATORS


def _can_block_a_bump(raw: str, pin: str) -> bool:
    """Say whether a requirement on a pinned package could stop a bump.

    One ``packaging`` can't parse counts: uv may still accept it, and
    nothing here can show it has no cap.
    """
    try:
        requirement = Requirement(raw)
    except InvalidRequirement:
        return True
    if requirement.url:
        return True
    pinned = next(
        Version(spec.version.removesuffix(".*"))
        for spec in Requirement(pin).specifier
        if spec.operator == "=="
    )
    return any(
        _can_reject_a_bump(spec, pinned) for spec in requirement.specifier
    )


def find_pin_conflicts(
    pyproject: Mapping[str, object],
    pins: Mapping[str, str],
) -> list[PinConflict]:
    """Return each entry that can block or replace a pinned package."""
    conflicts: list[PinConflict] = []
    for section, raw in _declared_requirements(pyproject):
        pin = _pin_for(raw, pins)
        if pin is not None and _can_block_a_bump(raw, pin):
            conflicts.append(PinConflict(section, raw, pin))
    uv = _table(_table(pyproject, "tool"), "uv")
    for raw in _strings(uv.get("override-dependencies")):
        pin = _pin_for(raw, pins)
        if pin is not None:
            conflicts.append(
                PinConflict("tool.uv.override-dependencies", raw, pin)
            )
    for name in _table(uv, "sources"):
        pin = _pin_for(name, pins)
        if pin is not None:
            conflicts.append(PinConflict("tool.uv.sources", name, pin))
    return conflicts


def _load(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _expand(root: Path, patterns: object) -> set[Path]:
    """Resolve workspace patterns, which may be plain or absolute paths."""
    return {
        (root / match).resolve()
        for pattern in _strings(patterns)
        for match in glob.glob(pattern, root_dir=root)
    }


def _workspace_members(
    root: Path, pyproject: Mapping[str, object]
) -> Iterator[Path]:
    """Yield each workspace member directory other than ``root``."""
    workspace = _table(_table(_table(pyproject, "tool"), "uv"), "workspace")
    members = _expand(root, workspace.get("members"))
    for member in sorted(members - _expand(root, workspace.get("exclude"))):
        if member != root.resolve() and (member / "pyproject.toml").is_file():
            yield member


def find_consumer_pin_conflicts(
    root: Path, pins: Mapping[str, str]
) -> list[PinConflict]:
    """Check the consumer's project and every workspace member."""
    pyproject = _load(root / "pyproject.toml")
    conflicts = find_pin_conflicts(pyproject, pins)
    for member in _workspace_members(root, pyproject):
        name = Path(os.path.relpath(member, root.resolve())).as_posix()
        conflicts += [
            replace(conflict, section=f"{name}: {conflict.section}")
            for conflict in find_pin_conflicts(
                _load(member / "pyproject.toml"), pins
            )
        ]
    return conflicts


class ToolPinsBase:
    """Assert the consumer leaves repo-shared's pins free to move."""

    consumer_root: ClassVar[Path] = Path.cwd()

    def test_pinned_tools_are_left_unconstrained(self) -> None:
        if _is_repo_shared_source_root(self.consumer_root):
            pytest.skip("running from repo-shared source; these are its pins")
        conflicts = find_consumer_pin_conflicts(
            self.consumer_root, repo_shared_tool_pins()
        )
        assert not conflicts, (
            "repo-shared pins these packages exactly, and these entries "
            "can block a future ``upgrade`` or replace the pin:\n"
            + "\n".join(
                f"  - {conflict.section}: {conflict.entry} "
                f"(repo-shared: {conflict.pin})"
                for conflict in conflicts
            )
            + "\n\nDeclare the package without a version, or with a lower "
            "bound, and repo-shared's pin sets the version. Remove any "
            "override, source, or URL for it. To notice a tool change, set "
            "``[tool.repo-shared] test-command`` and upgrade with "
            "``--run-tests``."
        )
