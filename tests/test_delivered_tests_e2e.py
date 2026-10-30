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
  consumer, on the oldest and the newest Python consumers run.
- Wrapper upgrade: ``upgrade --run-tests`` through the vendored
  wrapper, on the same two Pythons. In-process tests run ``cli.py`` on
  this repo's own Python; a consumer runs it on theirs.
- Failure-case detection: planting a lint-failing ``.py`` and an
  unformatted ``.md`` in the consumer must cause the relevant
  delivered test to reject -- so the bases as delivered actually
  enforce their rules in a consumer, not just against repo-shared's
  own content via the dogfood ``testpaths``.
- Phased execution: a green delivered suite proceeds into local tests,
  while any delivered failure prevents local test bodies from running.

Each test runs ``uv run pytest`` inside a tmp consumer. ``uv run``
resolves the consumer's venv on first invocation, so these tests are
slower than the in-process unit tests. They share one fake-source
clone of repo-shared (module-scoped fixture) so the ``shutil.copytree``
cost is paid once.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from conftest import FIXTURES, PYTHON_FLOOR, REPO_ROOT, git_in, run_cli

# Reuse the fake-source builder from the upgrade integration tests so a
# regression in one path doesn't go undetected here. The helper is
# private to ``test_cli_integration`` but pytest's rootdir handling
# puts ``tests/`` on ``sys.path``, so the import works at collection
# time.
from test_cli_integration import (
    _add_bump_commit,
    _clone_fake_source,
    _plant_consumer_startup_traps,
    _setup_consumer_no_origin,
)

from epilatow_repo_shared import cli
from epilatow_repo_shared.exit_codes import ExitCode

_NPX_REQUIRED = pytest.mark.skipif(
    shutil.which("npx") is None,
    reason="npx not on PATH; markdownlint delivered test cannot run",
)

# The newest Python consumers run. A pass on the floor says nothing
# about what a newer release removed or changed, or about compiled
# dependencies' separate builds for it.
_NEWEST_CONSUMER_PYTHON = "3.15"


@pytest.fixture(scope="module")
def _shared_fake_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Single fake-source clone of repo-shared, shared across this module."""
    parent = tmp_path_factory.mktemp("fake_source_parent")
    return _clone_fake_source(parent / "fake-source")


@pytest.fixture(params=[PYTHON_FLOOR, _NEWEST_CONSUMER_PYTHON])
def _consumer_python(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Pin the Python of a consumer created after this fixture runs."""
    python: str = request.param
    monkeypatch.setenv("UV_PYTHON", python)
    return python


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
    exit_code = run_cli(
        [
            "init",
            "--source",
            f"git+file://{_shared_fake_source}",
            "--repo",
            str(consumer),
        ]
    )
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


def _environment_python(project: Path) -> str:
    """Return the ``major.minor`` Python ``uv run`` uses in ``project``."""
    result = subprocess.run(
        ["uv", "run", "python", "--version"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    version = re.match(r"Python (\d+\.\d+)", result.stdout)
    assert version, result.stdout
    return version.group(1)


def _plant_python_file(consumer: Path) -> Path:
    """Add a clean file; the ruff and mypy gates skip without one."""
    path = consumer / "gate_target.py"
    # Lint data for the gates; nothing runs it, so it stays inline.
    path.write_text('"""Gate target."""\n')
    return path


def _plant_local_marker_test(consumer: Path) -> Path:
    """Add a local test whose marker proves its test body ran."""
    tests_dir = consumer / "tests"
    tests_dir.mkdir()
    marker = consumer / "local-suite-ran"
    shutil.copyfile(
        FIXTURES / "local_suite_probe.py", tests_dir / "test_local.py"
    )
    return marker


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
def test_delivered_tests_pass_on_clean_init_consumer(
    _consumer_python: str, _consumer: Path
) -> None:
    _plant_python_file(_consumer)
    result = _pytest_in(_consumer, "_repo_shared/tests")
    assert result.returncode == 0, (
        f"delivered tests failed against a clean consumer:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "skipped" not in result.stdout, result.stdout
    assert _environment_python(_consumer) == _consumer_python


@_NPX_REQUIRED
def test_wrapper_upgrade_runs_on_the_consumer_python(
    _consumer_python: str, tmp_path: Path
) -> None:
    fake_source = _clone_fake_source(tmp_path / "fake-source")
    source_url = f"git+file://{fake_source}"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _setup_consumer_no_origin(consumer, source_url)
    _plant_python_file(consumer)
    git_in(consumer, "add", "gate_target.py")
    git_in(consumer, "commit", "-m", "add a file for the python gates")
    sha = _add_bump_commit(fake_source)

    result = subprocess.run(
        [
            str(consumer / "_repo_shared" / "repo-shared"),
            "upgrade",
            sha,
            "--source",
            source_url,
            "--run-tests",
        ],
        cwd=consumer,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )

    assert result.returncode == 0, (
        f"wrapper upgrade failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "tests passed" in result.stdout
    assert "skipped" not in result.stdout, result.stdout
    update = consumer / ".wt" / f"repo-shared-update-{sha[:7]}"
    assert _environment_python(update) == _consumer_python


def test_consumers_default_to_the_python_floor(_consumer: Path) -> None:
    assert _environment_python(_consumer) == PYTHON_FLOOR


def test_python_floor_matches_the_package_requirement() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["requires-python"] == f">={PYTHON_FLOOR}"


@_NPX_REQUIRED
def test_code_quality_rejects_lint_violation_in_consumer(
    _consumer: Path,
) -> None:
    # Unused import is an F401 violation under ruff's default ruleset --
    # auto-discovery finds this .py file at the consumer root. Only the
    # gates read it and nothing runs it, so it stays inline as lint data.
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


@_NPX_REQUIRED
def test_local_tests_run_after_green_shared_phase(_consumer: Path) -> None:
    marker = _plant_local_marker_test(_consumer)

    result = _pytest_in(_consumer, "tests", "_repo_shared/tests")

    assert result.returncode == 0, (
        f"combined suite failed against a clean consumer:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert marker.is_file(), "local test did not run after shared tests passed"


@_NPX_REQUIRED
def test_red_shared_phase_prevents_local_test_execution(
    _consumer: Path,
) -> None:
    marker = _plant_local_marker_test(_consumer)
    # Lint data that turns the shared phase red; nothing runs it.
    (_consumer / "lint_me.py").write_text("import sys\n", encoding="utf-8")

    result = _pytest_in(_consumer, "tests", "_repo_shared/tests")
    combined = result.stdout + result.stderr

    assert result.returncode != 0, (
        "combined suite should fail when a shared gate fails:\n" + combined
    )
    assert not marker.exists(), "local test ran after a shared test failed"
    assert "repo-shared tests failed; local tests were not run" in combined


@_NPX_REQUIRED
@pytest.mark.parametrize("dotenv", [False, True])
def test_run_tests_isolates_startup_and_preserves_gate_overrides(
    _consumer: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    dotenv: bool,
) -> None:
    _plant_consumer_startup_traps(_consumer)
    monkeypatch.setenv("PYTHONPATH", str(_consumer))
    if dotenv:
        monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
        monkeypatch.delenv("PYTEST_PLUGINS", raising=False)
        env_file = _consumer.parent / "consumer.env"
        env_file.write_text(
            "PYTEST_ADDOPTS=--collect-only\n"
            "PYTEST_PLUGINS=consumer_startup_trap\n"
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=\n"
        )
        monkeypatch.setenv("UV_ENV_FILE", str(env_file))
    else:
        monkeypatch.delenv("UV_ENV_FILE", raising=False)
        monkeypatch.setenv("PYTEST_ADDOPTS", "-p consumer_startup_trap")
        monkeypatch.setenv("PYTEST_PLUGINS", "consumer_startup_trap")
    pyproject = _consumer / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + "\n[tool.repo-shared.code-quality]\n"
        "python-targets = ['gate-probe']\n"
        "[tool.repo-shared.markdown]\nextra-exclude-dirs = ['EXCLUDED.md']\n"
    )
    # ``gate-probe`` and ``unused.py`` are lint data for the delivered
    # gates; nothing runs them, so they stay inline.
    (_consumer / "gate-probe").write_text("value = 1\n")
    (_consumer / "EXCLUDED.md").write_text("# heading\n\n\n\nbody   \n")
    (_consumer / "ruff.toml").write_text(
        '[lint]\nselect = ["E", "F"]\nignore = ["F401"]\n'
    )
    (_consumer / "unused.py").write_text("import sys\n")
    argv = ["run-tests", "--repo", str(_consumer), "-v"]
    assert run_cli(argv) == ExitCode.SUCCESS
    output = capfd.readouterr().out
    assert "test_ruff_lint[gate-probe] PASSED" in output
    assert not (_consumer / "node_modules").exists()

    # Ordinary pytest still enforces consumer startup checks.
    result = _pytest_in(
        _consumer, "--collect-only", "_repo_shared/tests/test_in_sync.py"
    )
    assert result.returncode != 0
    assert "consumer startup needs local npm tools" in (
        result.stdout + result.stderr
    )

    # A broken gate must fail even when dotenv requests collection only.
    # The broken ``gate-probe`` is lint data too, never run.
    (_consumer / "gate-probe").write_text("value =\n")
    assert run_cli(argv) == ExitCode.WARNING
