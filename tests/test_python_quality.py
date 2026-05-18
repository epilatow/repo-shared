# This is AI generated code
"""Unit tests for the python_quality helpers.

The helpers back the delivered ``test_code_quality.py`` symlink. They
are not consumer-facing -- consumers customize via pyproject knobs
+ PEP 723 -- but the unit tests live here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import git_init_repo

from epilatow_repo_shared import sp
from epilatow_repo_shared.python_quality import (
    DEFAULT_RUFF_LINE_LENGTH,
    Pep723Metadata,
    ResolvedFile,
    _consumer_has_ruff_line_length,
    _ruff_line_length_args,
    discover_python_files,
    extract_pep723_metadata,
    resolve_files,
    run_mypy_strict,
    run_ruff_format_check,
    run_ruff_lint,
    validate_additional_targets,
)

_CLEAN_PY = '''"""Clean module."""


def add(a: int, b: int) -> int:
    return a + b
'''


_DIRTY_PY_WITH_BAD_IMPORT = """import os, sys
"""


def test_run_ruff_lint_passes_on_clean(tmp_path: Path) -> None:
    src = tmp_path / "clean.py"
    src.write_text(_CLEAN_PY)
    run_ruff_lint(["clean.py"], cwd=tmp_path)


def test_run_ruff_lint_fails_on_dirty(tmp_path: Path) -> None:
    src = tmp_path / "dirty.py"
    src.write_text(_DIRTY_PY_WITH_BAD_IMPORT)
    with pytest.raises(AssertionError):
        run_ruff_lint(["dirty.py"], cwd=tmp_path)


def test_run_ruff_skipped_without_targets(tmp_path: Path) -> None:
    with pytest.raises(pytest.skip.Exception):
        run_ruff_lint([], cwd=tmp_path)


def test_run_mypy_skipped_without_targets(tmp_path: Path) -> None:
    with pytest.raises(pytest.skip.Exception):
        run_mypy_strict([], cwd=tmp_path)


_MYPY_INTERNAL_ERROR_STDERR = (
    "some/file.py: error: INTERNAL ERROR -- "
    "Please try using mypy master on GitHub:\n"
    "version: 2.1.0\n"
)


def _patch_sp_run(
    monkeypatch: pytest.MonkeyPatch,
    results: list[subprocess.CompletedProcess[str]],
) -> list[list[str]]:
    """Replace ``sp.run`` with a scripted sequence; return calls list.

    ``results`` is consumed in order. The returned ``calls`` list
    accumulates the ``cmd`` argument from each invocation so tests can
    assert the right flags were passed.
    """
    calls: list[list[str]] = []
    iterator = iter(results)

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return next(iterator)

    monkeypatch.setattr(sp, "run", fake_run)
    return calls


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["mypy"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_run_mypy_strict_passes_show_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_sp_run(monkeypatch, [_completed(0)])
    run_mypy_strict(["x.py"], cwd=tmp_path)
    assert "--show-traceback" in calls[0]


def test_run_mypy_strict_retries_on_internal_error_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_sp_run(
        monkeypatch,
        [
            _completed(2, stderr=_MYPY_INTERNAL_ERROR_STDERR),
            _completed(0),
        ],
    )
    run_mypy_strict(["x.py"], cwd=tmp_path)
    assert len(calls) == 2


def test_run_mypy_strict_retries_three_times_then_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_sp_run(
        monkeypatch,
        [_completed(2, stderr=_MYPY_INTERNAL_ERROR_STDERR)] * 3,
    )
    with pytest.raises(AssertionError) as exc_info:
        run_mypy_strict(["x.py"], cwd=tmp_path)
    assert len(calls) == 3
    assert "INTERNAL ERROR after 3 attempts" in str(exc_info.value)


def test_run_mypy_strict_does_not_retry_real_type_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_sp_run(
        monkeypatch,
        [_completed(1, stdout='x.py:1: error: Name "y" is not defined\n')],
    )
    with pytest.raises(AssertionError):
        run_mypy_strict(["x.py"], cwd=tmp_path)
    assert len(calls) == 1


def test_run_mypy_strict_surfaces_real_error_after_transient_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry that uncovers a real type error reports it cleanly.

    The retry path is justified by *transient* mypyc crashes; if the
    retry exposes a genuine type error, the failure message must not
    be tagged with the ``INTERNAL ERROR after N attempts`` prefix
    (which would mislead the consumer into chasing a phantom crash).
    """
    calls = _patch_sp_run(
        monkeypatch,
        [
            _completed(2, stderr=_MYPY_INTERNAL_ERROR_STDERR),
            _completed(1, stdout='x.py:1: error: Name "y" is not defined\n'),
        ],
    )
    with pytest.raises(AssertionError) as exc_info:
        run_mypy_strict(["x.py"], cwd=tmp_path)
    assert len(calls) == 2
    message = str(exc_info.value)
    assert "INTERNAL ERROR" not in message
    assert 'Name "y" is not defined' in message


def test_run_ruff_format_check_passes_on_clean(tmp_path: Path) -> None:
    src = tmp_path / "clean.py"
    src.write_text(_CLEAN_PY)
    run_ruff_format_check(["clean.py"], cwd=tmp_path)


_PEP723_SCRIPT = '''\
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "voluptuous",
#     "PyYAML",
# ]
# ///
"""A script."""


def main() -> None:
    pass
'''


_PEP723_DEPS_ONLY = '''\
# /// script
# dependencies = ["packaging"]
# ///
"""Deps but no requires-python."""
'''


_PEP723_PYTHON_ONLY = '''\
# /// script
# requires-python = ">=3.12"
# ///
"""requires-python but no deps."""
'''


_NO_PEP723 = '''\
"""Plain module, no PEP 723."""


def add(a: int, b: int) -> int:
    return a + b
'''


_PEP723_MALFORMED_TOML = """\
# /// script
# dependencies = ["unterminated
# ///
"""


def test_discover_python_files_honors_gitignore(tmp_path: Path) -> None:
    """``.gitignore`` is the source of truth for excluded paths.

    Anything the consumer chose not to track -- virtualenvs, build
    outputs, sibling worktrees under ``.wt/``, etc. -- is excluded
    automatically without per-directory hardcoding in repo-shared.
    """
    git_init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".venv/\n__pycache__/\n.wt/\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pkg.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "vendored.py").write_text("")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("")
    (tmp_path / ".wt" / "branch").mkdir(parents=True)
    (tmp_path / ".wt" / "branch" / "sibling.py").write_text("")

    found = discover_python_files(tmp_path)
    assert found == ["src/pkg.py", "tests/test_x.py"]


def test_discover_python_files_skips_vendored_repo_shared(
    tmp_path: Path,
) -> None:
    """``_repo_shared/`` is tracked vendored content; the in-tree
    symlinks expose its files at the canonical path, so the default
    excludes it to avoid linting the same content twice."""
    git_init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pkg.py").write_text("")
    (tmp_path / "_repo_shared" / "files").mkdir(parents=True)
    (tmp_path / "_repo_shared" / "files" / "shadow.py").write_text("")

    found = discover_python_files(tmp_path)
    assert found == ["src/pkg.py"]


def test_discover_python_files_honours_custom_excludes(
    tmp_path: Path,
) -> None:
    git_init_repo(tmp_path)
    (tmp_path / "a.py").write_text("")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "v.py").write_text("")
    found = discover_python_files(tmp_path, exclude_dirs=["vendor/"])
    assert found == ["a.py"]


def test_discover_python_files_prunes_nested_dir_by_name(
    tmp_path: Path,
) -> None:
    """A bare directory name prunes that directory anywhere in the tree.

    Both the gitignore-driven path and the os.walk fallback honor
    the same semantic so a consumer's ``extra-exclude-dirs`` entry
    applies whether or not the dir is gitignored.
    """
    git_init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pkg.py").write_text("")
    (tmp_path / "src" / ".venv").mkdir()
    (tmp_path / "src" / ".venv" / "vendored.py").write_text("")
    found = discover_python_files(tmp_path, exclude_dirs=[".venv"])
    assert found == ["src/pkg.py"]


def test_discover_python_files_exclude_does_not_match_filename(
    tmp_path: Path,
) -> None:
    """``exclude_dirs`` matches DIRECTORY components, not filenames.

    Regression guard: the ``os.walk`` semantic only ever pruned
    ``dirnames``, never the basename. A file literally named
    ``htmlcov.py`` at the top level stays in the discovered set even
    if a sibling ``htmlcov/`` directory would be excluded.
    """
    git_init_repo(tmp_path)
    (tmp_path / "htmlcov.py").write_text("")
    (tmp_path / "htmlcov").mkdir()
    (tmp_path / "htmlcov" / "report.py").write_text("")
    found = discover_python_files(tmp_path, exclude_dirs=["htmlcov"])
    assert found == ["htmlcov.py"]


def test_discover_python_files_falls_back_to_walk_without_git(
    tmp_path: Path,
) -> None:
    """Without a ``.git`` entry the discovery falls back to os.walk.

    The fallback only excludes the post-filter ``exclude_dirs``, so
    callers that explicitly pass ``[".venv"]`` still get the dir
    pruned even outside a git repo.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pkg.py").write_text("")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "v.py").write_text("")
    found = discover_python_files(tmp_path, exclude_dirs=[".venv"])
    assert found == ["src/pkg.py"]


def test_discover_python_files_rejects_multi_segment_exclude(
    tmp_path: Path,
) -> None:
    """``docs/_build``-style entries raise rather than silently misbehave.

    The old prefix-from-root semantic supported them; the new
    directory-name semantic does not. Surfacing the rejection at
    discovery time tells the operator before ruff / mypy emit
    confusing "file not found" output.
    """
    with pytest.raises(ValueError, match="contains '/'"):
        discover_python_files(tmp_path, exclude_dirs=["docs/_build"])


def test_extract_pep723_metadata_parses_deps_and_requires_python(
    tmp_path: Path,
) -> None:
    target = tmp_path / "script.py"
    target.write_text(_PEP723_SCRIPT)
    meta = extract_pep723_metadata(target)
    assert meta == Pep723Metadata(
        deps=("voluptuous", "PyYAML"), python_version="3.14"
    )


def test_extract_pep723_metadata_deps_only(tmp_path: Path) -> None:
    target = tmp_path / "deps_only.py"
    target.write_text(_PEP723_DEPS_ONLY)
    assert extract_pep723_metadata(target) == Pep723Metadata(
        deps=("packaging",), python_version=None
    )


def test_extract_pep723_metadata_python_version_only(tmp_path: Path) -> None:
    target = tmp_path / "py_only.py"
    target.write_text(_PEP723_PYTHON_ONLY)
    assert extract_pep723_metadata(target) == Pep723Metadata(
        deps=(), python_version="3.12"
    )


def test_extract_pep723_metadata_returns_none_without_block(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plain.py"
    target.write_text(_NO_PEP723)
    assert extract_pep723_metadata(target) is None


def test_extract_pep723_metadata_returns_none_on_malformed_toml(
    tmp_path: Path,
) -> None:
    target = tmp_path / "broken.py"
    target.write_text(_PEP723_MALFORMED_TOML)
    assert extract_pep723_metadata(target) is None


def test_extract_pep723_metadata_ignores_opener_inside_string_literal(
    tmp_path: Path,
) -> None:
    """An opener inside Python code (after a docstring / def) is ignored.

    PEP 723 places the block at the top of the file (after the
    shebang). Once the extractor sees a non-comment line, it stops
    scanning for the opener. Without this guard, a test file that
    embeds a ``# /// script`` fixture inside a triple-quoted string
    would be misidentified as an actual PEP 723 script -- mypy
    would then run with the fixture's deps instead of the project
    env, producing spurious "module not found" errors.
    """
    target = tmp_path / "test_with_fixture.py"
    target.write_text(
        '"""Test module."""\n\n'
        "import pytest\n\n"
        "_FIXTURE = '''\\\n"
        "# /// script\n"
        '# requires-python = ">=3.14"\n'
        "# dependencies = [\n"
        '#     "voluptuous",\n'
        "# ]\n"
        "# ///\n"
        "'''\n"
    )
    assert extract_pep723_metadata(target) is None


def test_resolve_files_uses_pep723_when_present(tmp_path: Path) -> None:
    (tmp_path / "with_pep723.py").write_text(_PEP723_SCRIPT)
    (tmp_path / "plain.py").write_text(_NO_PEP723)
    resolved = resolve_files(
        tmp_path,
        discovered=["plain.py", "with_pep723.py"],
        default_deps=("fallback",),
        default_python_version="3.10",
    )
    assert resolved == [
        ResolvedFile(
            path="plain.py",
            deps=("fallback",),
            python_version="3.10",
            source="default",
        ),
        ResolvedFile(
            path="with_pep723.py",
            deps=("voluptuous", "PyYAML"),
            python_version="3.14",
            source="pep723",
        ),
    ]


def test_resolve_files_default_deps_empty_when_unspecified(
    tmp_path: Path,
) -> None:
    (tmp_path / "plain.py").write_text(_NO_PEP723)
    resolved = resolve_files(tmp_path, discovered=["plain.py"])
    assert resolved == [
        ResolvedFile(
            path="plain.py",
            deps=(),
            python_version=None,
            source="default",
        ),
    ]


def test_validate_additional_targets_flags_missing_target(
    tmp_path: Path,
) -> None:
    errors = validate_additional_targets(
        tmp_path, additional_targets=["bin/ghost"]
    )
    assert len(errors) == 1
    assert "bin/ghost" in errors[0]
    assert "does not exist" in errors[0]


def test_validate_additional_targets_flags_py_extension(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("")
    errors = validate_additional_targets(
        tmp_path, additional_targets=["src/foo.py"]
    )
    assert len(errors) == 1
    assert "src/foo.py" in errors[0]
    assert "auto-discovered" in errors[0]


def test_validate_additional_targets_flags_directory(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    errors = validate_additional_targets(tmp_path, additional_targets=["bin"])
    assert len(errors) == 1
    assert "bin" in errors[0]
    assert "not a regular file" in errors[0]


def test_validate_additional_targets_accepts_valid_shebang_script(
    tmp_path: Path,
) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "foo").write_text("#!/usr/bin/env python3\n")
    assert (
        validate_additional_targets(tmp_path, additional_targets=["bin/foo"])
        == []
    )


def test_validate_additional_targets_flags_symlink(
    tmp_path: Path,
) -> None:
    """python-targets rejects symlinks outright.

    ``Path.is_file()`` follows symlinks, so the directory check
    doesn't catch a symlink-to-file. The validator must flag the
    entry itself so a consumer who lists a ``bin/alias -> bin/real``
    symlink in ``python-targets`` gets a clear "drop the entry"
    error rather than ruff / mypy silently re-linting the same
    content under a different path name.
    """
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "dotfiles").write_text("#!/usr/bin/env python3\n")
    (tmp_path / "bin" / "binfiles").symlink_to("dotfiles")
    errors = validate_additional_targets(
        tmp_path, additional_targets=["bin/binfiles"]
    )
    assert len(errors) == 1
    assert "bin/binfiles" in errors[0]
    assert "symlink" in errors[0]
    assert "drop" in errors[0]


def test_validate_additional_targets_accepts_empty_list(
    tmp_path: Path,
) -> None:
    assert validate_additional_targets(tmp_path, additional_targets=[]) == []


def test_validate_additional_targets_collects_all_errors(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("")
    (tmp_path / "bin").mkdir()  # a directory, not a regular file
    errors = validate_additional_targets(
        tmp_path,
        additional_targets=["bin/missing", "src/foo.py", "bin"],
    )
    assert len(errors) == 3
    assert any("does not exist" in e for e in errors)
    assert any("auto-discovered" in e for e in errors)
    assert any("not a regular file" in e for e in errors)


def test_consumer_has_ruff_line_length_finds_ruff_toml_value(
    tmp_path: Path,
) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 80\n")
    assert _consumer_has_ruff_line_length(tmp_path) is True


def test_consumer_has_ruff_line_length_finds_pyproject_value(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        "[tool.ruff]\nline-length = 100\n"
    )
    assert _consumer_has_ruff_line_length(tmp_path) is True


def test_consumer_has_ruff_line_length_ignores_pyproject_when_ruff_toml(
    tmp_path: Path,
) -> None:
    """ruff.toml shadows pyproject's [tool.ruff] entirely at runtime."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        "[tool.ruff]\nline-length = 100\n"
    )
    (tmp_path / "ruff.toml").write_text('[lint]\nextend-select = ["RUF059"]\n')
    # ruff.toml exists, doesn't pin line-length -> consumer effectively
    # has no setting, even though pyproject did.
    assert _consumer_has_ruff_line_length(tmp_path) is False


def test_consumer_has_ruff_line_length_returns_false_with_no_config(
    tmp_path: Path,
) -> None:
    assert _consumer_has_ruff_line_length(tmp_path) is False


def test_consumer_has_ruff_line_length_returns_false_with_empty_tool_ruff(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[tool.ruff]\nextend-select = ["RUF059"]\n'
    )
    assert _consumer_has_ruff_line_length(tmp_path) is False


def test_ruff_line_length_args_supplies_default_when_consumer_unset(
    tmp_path: Path,
) -> None:
    assert _ruff_line_length_args(tmp_path) == [
        "--line-length",
        str(DEFAULT_RUFF_LINE_LENGTH),
    ]


def test_ruff_line_length_args_suppresses_flag_when_consumer_set(
    tmp_path: Path,
) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    assert _ruff_line_length_args(tmp_path) == []
