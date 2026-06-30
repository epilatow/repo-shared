# This is AI generated code
"""Mock-based unit tests for ``repo-shared run-tests``.

``run-tests`` is the consumer-side shortcut that shells out to
``uv run --project <consumer> pytest _repo_shared/tests`` -- the
delivered tests against the consumer's project. The interesting
shape is the argv it constructs and the returncode mapping
(0 / 1 / anything-else), not the pytest execution itself -- so these
tests monkeypatch ``cli.sp.run`` and
``cli._running_from_local_repo_shared`` instead of running pytest
for real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from epilatow_repo_shared import cli, sp
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
    assert "pytest" in argv
    assert argv[-1] == "_repo_shared/tests"
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
