# This is AI generated code
"""Consumer entries that block or replace repo-shared's pins."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from conftest import REPO_ROOT

from epilatow_repo_shared.tool_pins import (
    PinConflict,
    ToolPinsBase,
    find_consumer_pin_conflicts,
    find_pin_conflicts,
    repo_shared_tool_pins,
)

_PINS = {"mypy": "mypy==2.4.1", "mdformat-gfm": "mdformat-gfm==1.0.0"}


class _Gate(ToolPinsBase):
    """A gate each test points at its own consumer."""


def _dev_group(requirement: str) -> dict[str, object]:
    return {"dependency-groups": {"dev": [requirement]}}


def test_tool_pins_come_from_the_package_requirements() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    declared = {
        raw for raw in pyproject["project"]["dependencies"] if "==" in raw
    }
    assert set(repo_shared_tool_pins().values()) == declared


@pytest.mark.parametrize(
    "requirement",
    [
        "mypy==2.4.0",
        "mypy==2.4.1",
        "mypy===2.4.1",
        "mypy~=2.4",
        "mypy<3",
        "mypy<=2.4.1",
        "mypy!=2.4.1",
        "mypy!=2.4.2",
        "mypy!=3.*",
        "mypy>=2.4,<3",
        "mypy==2.4.1; python_version < '3.15'",
        "mypy @ https://example.invalid/mypy-2.4.1.whl",
        "MyPy==2.4.0",
        "mdformat_gfm==1.0.0",
        # uv accepts this; packaging rejects the wildcard lower bound.
        "mypy>=1.*,<3",
    ],
)
def test_requirement_that_can_block_a_bump_conflicts(
    requirement: str,
) -> None:
    assert len(find_pin_conflicts(_dev_group(requirement), _PINS)) == 1


@pytest.mark.parametrize(
    "requirement",
    [
        "mypy",
        "mypy>=2.4",
        "mypy>2",
        "mypy!=0.5",
        "pydantic==2.14.0",
        "pytest<10",
        "other @ https://example.invalid/other-1.0.whl",
    ],
)
def test_requirement_that_cannot_block_a_bump_is_allowed(
    requirement: str,
) -> None:
    assert not find_pin_conflicts(_dev_group(requirement), _PINS)


def test_requirement_only_uv_accepts_is_left_to_uv() -> None:
    assert not find_pin_conflicts(_dev_group("other>=1.*"), _PINS)


def test_wildcard_pin_is_read_as_its_version() -> None:
    pins = {"mypy": "mypy==2.4.*"}
    assert find_pin_conflicts(_dev_group("mypy!=3.*"), pins)
    assert not find_pin_conflicts(_dev_group("mypy>=2"), pins)


@pytest.mark.parametrize(
    ("pyproject", "section"),
    [
        (
            {"project": {"dependencies": ["mypy==2.4.0"]}},
            "project.dependencies",
        ),
        (
            {"project": {"optional-dependencies": {"lint": ["mypy==2.4.0"]}}},
            "project.optional-dependencies.lint",
        ),
        (
            {"dependency-groups": {"dev": ["mypy==2.4.0"]}},
            "dependency-groups.dev",
        ),
        (
            {"tool": {"uv": {"dev-dependencies": ["mypy==2.4.0"]}}},
            "tool.uv.dev-dependencies",
        ),
        (
            {"tool": {"uv": {"constraint-dependencies": ["mypy==2.4.0"]}}},
            "tool.uv.constraint-dependencies",
        ),
    ],
)
def test_every_requirement_section_is_checked(
    pyproject: dict[str, object], section: str
) -> None:
    assert find_pin_conflicts(pyproject, _PINS) == [
        PinConflict(section, "mypy==2.4.0", "mypy==2.4.1")
    ]


@pytest.mark.parametrize("requirement", ["mypy>=2.4", "mypy==2.4.0", "mypy"])
def test_any_override_of_a_pinned_package_conflicts(requirement: str) -> None:
    pyproject = {"tool": {"uv": {"override-dependencies": [requirement]}}}
    assert find_pin_conflicts(pyproject, _PINS) == [
        PinConflict(
            "tool.uv.override-dependencies", requirement, "mypy==2.4.1"
        )
    ]


def test_override_of_another_package_is_allowed() -> None:
    pyproject = {"tool": {"uv": {"override-dependencies": ["other==1.0"]}}}
    assert not find_pin_conflicts(pyproject, _PINS)


def test_source_for_a_pinned_package_conflicts() -> None:
    pyproject = {
        "tool": {
            "uv": {
                "sources": {
                    "mdformat_gfm": {"path": "../mdformat-gfm"},
                    "epilatow-repo-shared": {"git": "https://example.invalid"},
                }
            }
        }
    }
    assert find_pin_conflicts(pyproject, _PINS) == [
        PinConflict("tool.uv.sources", "mdformat_gfm", "mdformat-gfm==1.0.0")
    ]


def test_group_includes_are_not_requirements() -> None:
    pyproject: dict[str, object] = {
        "dependency-groups": {
            "lint": ["mypy"],
            "dev": [{"include-group": "lint"}],
        }
    }
    assert not find_pin_conflicts(pyproject, _PINS)


def _write_member(root: Path, name: str, requirement: str) -> None:
    member = root / name
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        f'[dependency-groups]\ndev = ["{requirement}"]\n'
    )


def test_workspace_members_are_checked(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv.workspace]\n"
        'members = ["packages/*"]\n'
        'exclude = ["packages/excluded"]\n'
    )
    _write_member(tmp_path, "packages/pinned", "mypy==2.4.0")
    _write_member(tmp_path, "packages/free", "mypy")
    _write_member(tmp_path, "packages/excluded", "mypy==2.4.0")
    (tmp_path / "packages" / "not-a-project").mkdir()

    assert find_consumer_pin_conflicts(tmp_path, _PINS) == [
        PinConflict(
            "packages/pinned: dependency-groups.dev",
            "mypy==2.4.0",
            "mypy==2.4.1",
        )
    ]


@pytest.mark.parametrize(
    "members",
    [
        '[".", "packages/*"]',
        '["./", "packages/*"]',
        '["packages/pinned"]',
    ],
)
def test_workspace_accepts_the_member_forms_uv_does(
    tmp_path: Path, members: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.uv.workspace]\nmembers = {members}\n"
    )
    _write_member(tmp_path, "packages/pinned", "mypy==2.4.0")

    conflicts = find_consumer_pin_conflicts(tmp_path, _PINS)

    assert [conflict.section for conflict in conflicts] == [
        "packages/pinned: dependency-groups.dev"
    ]


def test_workspace_accepts_an_absolute_member_and_a_root_exclude(
    tmp_path: Path,
) -> None:
    _write_member(tmp_path, "packages/pinned", "mypy==2.4.0")
    member = tmp_path / "packages" / "pinned"
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.uv.workspace]\nmembers = ["{member}"]\nexclude = ["."]\n'
    )

    conflicts = find_consumer_pin_conflicts(tmp_path, _PINS)

    assert [conflict.section for conflict in conflicts] == [
        "packages/pinned: dependency-groups.dev"
    ]


def test_gate_names_the_section_and_both_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[dependency-groups]\ndev = ["mypy==0.0.0"]\n'
    )
    monkeypatch.setattr(_Gate, "consumer_root", tmp_path)

    with pytest.raises(AssertionError) as failure:
        _Gate().test_pinned_tools_are_left_unconstrained()

    report = str(failure.value)
    assert "dependency-groups.dev: mypy==0.0.0" in report
    assert repo_shared_tool_pins()["mypy"] in report
    assert "--run-tests" in report


def test_gate_passes_an_unconstrained_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[dependency-groups]\ndev = ["mypy", "ruff>=0.1"]\n'
    )
    monkeypatch.setattr(_Gate, "consumer_root", tmp_path)

    _Gate().test_pinned_tools_are_left_unconstrained()


def test_gate_skips_the_repo_shared_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_Gate, "consumer_root", REPO_ROOT)

    with pytest.raises(pytest.skip.Exception):
        _Gate().test_pinned_tools_are_left_unconstrained()
