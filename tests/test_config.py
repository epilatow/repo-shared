# This is AI generated code
"""Unit tests for ``[tool.repo-shared.<section>]`` loaders."""

from __future__ import annotations

from pathlib import Path

from epilatow_repo_shared.config import (
    CodeQualityOverrides,
    MarkdownOverrides,
    code_quality_overrides,
    markdown_overrides,
)


def _write_pyproject(root: Path, body: str) -> None:
    (root / "pyproject.toml").write_text(body)


def test_code_quality_defaults_when_no_pyproject(tmp_path: Path) -> None:
    assert code_quality_overrides(repo_root=tmp_path) == CodeQualityOverrides(
        additional_targets=[],
        mypy_extra_deps=[],
        mypy_python_version=None,
    )


def test_code_quality_defaults_when_section_absent(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "[project]\nname = 'demo'\n")
    o = code_quality_overrides(repo_root=tmp_path)
    assert o.additional_targets == []
    assert o.mypy_extra_deps == []
    assert o.mypy_python_version is None


def test_code_quality_walks_up_to_find_pyproject(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        '[tool.repo-shared.code-quality]\npython-targets = ["bin/foo"]\n',
    )
    nested = tmp_path / "tests" / "repo-shared"
    nested.mkdir(parents=True)
    o = code_quality_overrides(repo_root=nested)
    assert o.additional_targets == ["bin/foo"]


def test_code_quality_ignores_malformed_python_targets(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        '[tool.repo-shared.code-quality]\npython-targets = "not-a-list"\n',
    )
    o = code_quality_overrides(repo_root=tmp_path)
    assert o.additional_targets == []


def test_code_quality_reads_all_keys(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        "[tool.repo-shared.code-quality]\n"
        'python-targets = ["bin/foo", "bin/bar"]\n'
        'extra-exclude-dirs = ["vendor/", "generated/"]\n'
        'mypy-extra-deps = ["voluptuous"]\n'
        'mypy-python-version = "3.12"\n',
    )
    o = code_quality_overrides(repo_root=tmp_path)
    assert o.additional_targets == ["bin/foo", "bin/bar"]
    assert o.extra_exclude_dirs == ("vendor/", "generated/")
    assert o.mypy_extra_deps == ["voluptuous"]
    assert o.mypy_python_version == "3.12"


def test_code_quality_extra_exclude_dirs_defaults_empty(
    tmp_path: Path,
) -> None:
    _write_pyproject(tmp_path, "[project]\nname = 'demo'\n")
    o = code_quality_overrides(repo_root=tmp_path)
    assert o.extra_exclude_dirs == ()


def test_code_quality_extra_exclude_dirs_malformed_falls_back(
    tmp_path: Path,
) -> None:
    _write_pyproject(
        tmp_path,
        '[tool.repo-shared.code-quality]\nextra-exclude-dirs = "not-a-list"\n',
    )
    o = code_quality_overrides(repo_root=tmp_path)
    assert o.extra_exclude_dirs == ()


def test_code_quality_blank_python_version_is_none(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        '[tool.repo-shared.code-quality]\nmypy-python-version = ""\n',
    )
    assert (
        code_quality_overrides(repo_root=tmp_path).mypy_python_version is None
    )


def test_markdown_defaults(tmp_path: Path) -> None:
    assert markdown_overrides(repo_root=tmp_path) == MarkdownOverrides(
        wrap=79, extra_exclude_dirs=()
    )


def test_markdown_reads_wrap_and_extras(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        "[tool.repo-shared.markdown]\n"
        "wrap = 78\n"
        'extra-exclude-dirs = ["build/", "vendor/"]\n',
    )
    o = markdown_overrides(repo_root=tmp_path)
    assert o.wrap == 78
    assert o.extra_exclude_dirs == ("build/", "vendor/")


def test_markdown_falls_back_on_malformed_wrap(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path, '[tool.repo-shared.markdown]\nwrap = "not-int"\n'
    )
    assert markdown_overrides(repo_root=tmp_path).wrap == 79


def test_malformed_pyproject_falls_back(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, "this is not valid toml [")
    assert code_quality_overrides(repo_root=tmp_path) == CodeQualityOverrides()
