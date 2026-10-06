# This is AI generated code
"""Mock-based unit tests for ``repo-shared run-tests``.

``run-tests`` runs the delivered tests in isolation against the
consumer's pinned project environment. The interesting
shape is the argv it constructs and the returncode mapping
(0 / 1 / anything-else), not the pytest execution itself -- so these
tests monkeypatch ``cli.sp.run`` and
``cli._running_from_local_repo_shared`` instead of running pytest
for real.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from epilatow_repo_shared import cli, shared_test_runner, sp
from epilatow_repo_shared.exit_codes import ExitCode


def _run_cli(argv: list[str]) -> ExitCode:
    parser = cli.args_parser()
    args = parser.parse_args(argv)
    return cli.main(args)


def _stub_sp_run(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> list[list[str]]:
    """Capture argv lists passed to ``cli.sp.run`` and stub the return."""
    captured: list[list[str]] = []

    def fake(
        cmd: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(list(cmd))
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout="", stderr=""
        )

    monkeypatch.setattr(sp, "run", fake)
    return captured


def _onboard_fake_consumer(tmp_path: Path) -> Path:
    """Plant ``_repo_shared/tests`` so ``run-tests`` doesn't refuse."""
    consumer = tmp_path / "consumer"
    (consumer / "_repo_shared" / "tests").mkdir(parents=True)
    return consumer


def test_run_tests_refuses_when_invoked_from_repo_shared_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    consumer = _onboard_fake_consumer(tmp_path)
    monkeypatch.setattr(
        cli, "_running_from_local_repo_shared", lambda: tmp_path
    )
    exit_code = _run_cli(["run-tests", "--repo", str(consumer)])
    assert exit_code == ExitCode.USAGE
    err = capsys.readouterr().err
    assert "run-tests" in err


def test_run_tests_refuses_when_repo_shared_tests_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    not_onboarded = tmp_path / "fresh"
    not_onboarded.mkdir()
    exit_code = _run_cli(["run-tests", "--repo", str(not_onboarded)])
    assert exit_code == ExitCode.CONFIG
    err = capsys.readouterr().err
    assert "_repo_shared/tests" in err
    assert "repo-shared init" in err


def test_run_tests_spawns_uv_run_pytest_against_vendored_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = _onboard_fake_consumer(tmp_path)
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    captured = _stub_sp_run(monkeypatch, returncode=0)

    assert _run_cli(["run-tests", "--repo", str(consumer)]) == ExitCode.SUCCESS
    assert len(captured) == 1
    argv = captured[0]
    assert argv[:2] == ["uv", "run"]
    assert argv[-2:] == [
        "python",
        str(Path(cli.__file__).resolve().with_name("shared_test_runner.py")),
    ]
    assert "-v" not in argv


def test_run_tests_verbose_flag_appends_dash_v(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = _onboard_fake_consumer(tmp_path)
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    captured = _stub_sp_run(monkeypatch, returncode=0)

    assert (
        _run_cli(["run-tests", "-v", "--repo", str(consumer)])
        == ExitCode.SUCCESS
    )
    assert "-v" in captured[0]


def test_run_tests_maps_pytest_returncode_one_to_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = _onboard_fake_consumer(tmp_path)
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    _stub_sp_run(monkeypatch, returncode=1)

    assert _run_cli(["run-tests", "--repo", str(consumer)]) == ExitCode.WARNING


def test_run_tests_maps_other_pytest_returncodes_to_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = _onboard_fake_consumer(tmp_path)
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    _stub_sp_run(monkeypatch, returncode=2)

    assert _run_cli(["run-tests", "--repo", str(consumer)]) == ExitCode.ERROR


def test_shared_runner_launch_preserves_environment_until_uv_loads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p consumer_preflight")
    monkeypatch.setenv("PYTEST_PLUGINS", "consumer_preflight")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "0")
    monkeypatch.setenv("CONSUMER_TEST_OPTION", "retained")
    parent_env = dict(os.environ)
    with patch.object(sp, "run", autospec=True) as run:
        run.return_value = subprocess.CompletedProcess([], 0)
        assert cli._run_shared_tests(tmp_path).returncode == 0
    run.assert_called_once_with(
        [
            "uv",
            "run",
            "--project",
            str(tmp_path),
            "python",
            str(
                Path(cli.__file__).resolve().with_name("shared_test_runner.py")
            ),
        ],
        cwd=tmp_path,
        check=False,
        timeout=sp.LONG_TIMEOUT_SECONDS,
    )
    assert dict(os.environ) == parent_env


@pytest.mark.parametrize("verbose", [False, True])
def test_shared_runner_sanitizes_pytest_after_environment_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verbose: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["shared-runner", *(["-v"] if verbose else [])]
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    monkeypatch.setenv("PYTEST_PLUGINS", "consumer_preflight")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "")
    monkeypatch.setenv("CONSUMER_TEST_OPTION", "retained")
    with patch.object(pytest, "main", autospec=True, return_value=1) as run:
        assert shared_test_runner.main() == 1
    assert "PYTEST_ADDOPTS" not in os.environ
    assert "PYTEST_PLUGINS" not in os.environ
    assert os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert os.environ["CONSUMER_TEST_OPTION"] == "retained"
    run.assert_called_once_with(
        [
            "-c",
            os.devnull,
            "--rootdir",
            str(tmp_path),
            "--noconftest",
            "_repo_shared/tests",
            *(["-v"] if verbose else []),
        ]
    )


@pytest.mark.parametrize(
    "text",
    [
        None,
        "invalid toml",
        "[project]\nname = 'consumer'\n",
        "[tool.repo-shared]\ntest-command = 17\n",
    ],
)
def test_unconfigured_or_invalid_test_command_uses_shared_runner(
    tmp_path: Path, text: str | None
) -> None:
    if text is not None:
        (tmp_path / "pyproject.toml").write_text(text)
    assert cli._read_test_command(tmp_path) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('"uv run pytest"', ["uv", "run", "pytest"]),
        ("['npm', 'run', 'check']", ["npm", "run", "check"]),
    ],
)
def test_explicit_test_command_preserves_consumer_invocation(
    tmp_path: Path, value: str, expected: list[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.repo-shared]\ntest-command = {value}\n"
    )
    assert cli._read_test_command(tmp_path) == expected
