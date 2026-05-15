# This is AI generated code
"""End-to-end: the delivered tests run inside a freshly-init'd consumer.

The upgrade-integration tests transitively cause the delivered tests to
run (via ``upgrade --run-tests``) but they only assert ``"tests passed"``
in the CLI's outer log. A regression where ``_inject_shared_testpaths``
silently broke and pytest collected ZERO delivered tests would slip
through that assertion (zero failures is zero failures).

This module closes that gap directly:

- Collection: pytest discovery in a fresh consumer must list every
  delivered test file.
- Clean pass: the delivered tests pass against a minimal post-init
  consumer.
- Failure-case detection: planting a lint-failing ``.py`` and an
  unformatted ``.md`` in the consumer must cause the relevant
  delivered test to reject -- so the bases as delivered actually
  enforce their rules in a consumer, not just against repo-shared's
  own content via the dogfood ``testpaths``.

Each test runs ``uv run pytest`` inside a tmp consumer. ``uv run``
resolves the consumer's venv on first invocation, so these tests are
slower than the in-process unit tests. They share one fake-source
clone of repo-shared (module-scoped fixture) so the ``shutil.copytree``
cost is paid once.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Reuse the fake-source builder from the upgrade integration tests so a
# regression in one path doesn't go undetected here. The helper is
# private to ``test_cli_integration`` but pytest's rootdir handling
# puts ``tests/`` on ``sys.path``, so the import works at collection
# time.
from test_cli_integration import _clone_fake_source

from epilatow_repo_shared import cli
from epilatow_repo_shared.exit_codes import ExitCode

_NPX_REQUIRED = pytest.mark.skipif(
    shutil.which("npx") is None,
    reason="npx not on PATH; markdownlint delivered test cannot run",
)


@pytest.fixture(scope="module")
def _shared_fake_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Single fake-source clone of repo-shared, shared across this module."""
    parent = tmp_path_factory.mktemp("fake_source_parent")
    return _clone_fake_source(parent / "fake-source")


@pytest.fixture
def _consumer(
    tmp_path: Path,
    _shared_fake_source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Init a fresh tmp consumer against the module's fake source."""
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(consumer)],
        check=True,
        capture_output=True,
    )
    args = cli.args_parser().parse_args(
        [
            "init",
            "--source",
            f"git+file://{_shared_fake_source}",
            str(consumer),
        ]
    )
    exit_code = cli.main(args)
    assert exit_code in (ExitCode.SUCCESS, ExitCode.WARNING), (
        f"init failed against a fresh consumer: {exit_code}"
    )
    return consumer


def _pytest_in(consumer: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "pytest", *args],
        cwd=consumer,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


@_NPX_REQUIRED
def test_all_delivered_tests_collected_in_init_consumer(
    _consumer: Path,
) -> None:
    result = _pytest_in(_consumer, "--collect-only", "-q")
    output = result.stdout + result.stderr
    for delivered in (
        "test_code_quality.py",
        "test_markdown_format.py",
        "test_markdownlint.py",
        "test_repo_shared_drift.py",
        "test_in_sync.py",
    ):
        assert delivered in output, (
            f"{delivered} missing from pytest collection -- testpaths "
            f"injection or vendor delivery may be broken:\n{output}"
        )


@_NPX_REQUIRED
def test_delivered_tests_pass_on_clean_init_consumer(_consumer: Path) -> None:
    result = _pytest_in(_consumer, "_repo_shared/tests")
    assert result.returncode == 0, (
        f"delivered tests failed against a clean consumer:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


@_NPX_REQUIRED
def test_code_quality_rejects_lint_violation_in_consumer(
    _consumer: Path,
) -> None:
    # Unused import is an F401 violation under ruff's default ruleset --
    # auto-discovery finds this .py file at the consumer root.
    (_consumer / "lint_me.py").write_text("import sys\n")
    result = _pytest_in(_consumer, "_repo_shared/tests/test_code_quality.py")
    assert result.returncode != 0, (
        f"code_quality test should have rejected lint_me.py:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "lint_me.py" in combined, (
        "Failure output should name the offending file:\n" + combined
    )


@_NPX_REQUIRED
def test_mdformat_rejects_unformatted_markdown_in_consumer(
    _consumer: Path,
) -> None:
    # Triple blank lines + trailing whitespace are mdformat violations
    # under the configured ``--wrap=79 --number`` settings.
    (_consumer / "BAD.md").write_text(
        "# heading\n\n\n\nbody text with trailing spaces   \n"
    )
    result = _pytest_in(
        _consumer, "_repo_shared/tests/test_markdown_format.py"
    )
    assert result.returncode != 0, (
        f"markdown_format test should have rejected BAD.md:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
