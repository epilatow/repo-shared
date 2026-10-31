# This is AI generated code
"""End-to-end tests for ``repo-shared upgrade-tools`` (maintainer-side).

``upgrade-tools`` only makes sense from inside a repo-shared clone: it
inspects ``[project] dependencies``, queries PyPI for newer versions,
and applies bumps in a worktree. The tests build a *fake repo-shared
clone* in tmp_path (a real ``git clone`` of this repo), patch
``_running_from_local_repo_shared`` to point at it, and stub PyPI
lookups via ``monkeypatch`` so the tests don't depend on real network
state.

The worktree-side dogfood in ``_cmd_upgrade_tools`` runs
``uv run --locked --extra test pytest shared/tests`` -- the quality-gate
subset, which ``test_code_quality`` parametrizes per tracked ``.py``
file. The fixture trims the clone's ``tests/`` to a single smoke test
so that per-file sweep stays small and the nested dogfood runs fast;
the dogfood-failure case instead drops a deliberately failing test
into ``shared/tests/``, where the scoped dogfood will run it.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import (
    FIXTURES,
    advance_origin_to_diverge,
    git_clone,
    git_in,
    run_cli,
)

from epilatow_repo_shared import cli, sp
from epilatow_repo_shared.exit_codes import ExitCode

REPO_ROOT = Path(__file__).resolve().parent.parent

_LOCKED = subprocess.CompletedProcess[str](
    [], 0, stderr="Resolved 3 packages\n"
)
_CONFLICT = subprocess.CompletedProcess[str](
    [], 1, stderr="error: No solution found when resolving dependencies\n"
)
_OUTAGE = subprocess.CompletedProcess[str](
    [], 2, stderr="error: Failed to fetch: https://pypi.org/simple/ruff/\n"
)
_BUILD_FAILURE = subprocess.CompletedProcess[str](
    [], 1, stderr="error: Failed to build `ruff==1.1`\n"
)
_RUFF_BUMP = ("ruff", "1.0", "1.1")
_MYPY_BUMP = ("mypy", "2.0", "2.1")


def _build_fake_clone(
    tmp_path: Path,
    *,
    pin_overrides: dict[str, str] | None = None,
    dogfood_failure: bool = False,
) -> Path:
    """Clone repo-shared into ``tmp_path/clone`` and rewire it for testing.

    Steps:
    - Real ``git clone`` (so package layout + .git history are valid).
    - Replace ``tests/`` with a single smoke test, keeping the per-file
      ``test_code_quality`` sweep of the worktree-side dogfood small.
    - When ``dogfood_failure`` is set, drop a deliberately failing test
      into ``shared/tests/`` so the scoped dogfood
      (``pytest shared/tests``) runs it and reports red.
    - Apply any ``pin_overrides`` to ``[project] dependencies`` so the
      "bump available" case can mock PyPI to a known-installable older
      version of a real package.
    - Commit the rewiring on a new branch named ``main`` (so
      ``origin/HEAD`` discovery and ``_can_push`` both work against a
      bare-clone origin).
    """
    clone = tmp_path / "clone"
    git_clone(REPO_ROOT, clone)
    git_in(clone, "checkout", "-B", "main")
    tests_dir = clone / "tests"
    for child in tests_dir.iterdir():
        if child.is_dir():
            subprocess.run(["rm", "-rf", str(child)], check=True)
        else:
            child.unlink()
    shutil.copyfile(FIXTURES / "dogfood_smoke.py", tests_dir / "test_smoke.py")
    if dogfood_failure:
        fail_path = clone / "shared" / "tests" / "test_deliberate_failure.py"
        shutil.copyfile(FIXTURES / "dogfood_failure.py", fail_path)

    if pin_overrides:
        pyproject = clone / "pyproject.toml"
        text = pyproject.read_text()
        for name, new_pin in pin_overrides.items():
            old_line_prefix = f'    "{name}=='
            replaced = False
            new_lines: list[str] = []
            for line in text.splitlines():
                if line.startswith(old_line_prefix) and not replaced:
                    new_lines.append(f'    "{name}=={new_pin}",')
                    replaced = True
                else:
                    new_lines.append(line)
            assert replaced, f"could not find pin for {name} to override"
            text = "\n".join(new_lines) + "\n"
        pyproject.write_text(text)

    # ``upgrade-tools`` creates a worktree at ``<clone>/.wt/...``; the
    # parent ``git status`` would otherwise show that dir as untracked
    # and the second-run dirty check would refuse. Append the entry so
    # the fixture's commit carries it forward.
    gitignore = clone / ".gitignore"
    existing = gitignore.read_text() if gitignore.is_file() else ""
    if ".wt" not in {line.strip() for line in existing.splitlines()}:
        gitignore.write_text(
            (existing if existing.endswith("\n") else existing + "\n")
            + ".wt\n"
        )
    git_in(clone, "add", "-A")
    git_in(clone, "commit", "-m", "test: stub tests + downgrade pins")

    origin = tmp_path / "origin.git"
    git_clone(clone, origin, bare=True)
    git_in(clone, "remote", "set-url", "origin", str(origin))
    git_in(clone, "push", "--quiet", "-u", "origin", "main")
    git_in(clone, "remote", "set-head", "origin", "main")
    return clone


@pytest.fixture
def fake_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Clean tmp clone of repo-shared, patched to *be* the repo-shared root."""
    clone = _build_fake_clone(tmp_path)
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    return clone


def _stub_pypi(
    monkeypatch: pytest.MonkeyPatch,
    mapping: Mapping[str, str | None],
) -> None:
    """Stub ``_query_pypi_latest`` against ``mapping`` (None means skip)."""

    def fake(pkg: str) -> str | None:
        return mapping.get(pkg, None)

    monkeypatch.setattr(cli, "_query_pypi_latest", fake)


def _resolve_bumps(
    tmp_path: Path, bumps: list[tuple[str, str, str]]
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    pyproject = tmp_path / "pyproject.toml"
    text = 'dependencies = ["ruff==1.0", "mypy==2.0"]\n'
    pyproject.write_text(text)
    return cli._resolve_compatible_bumps(
        wt_path=tmp_path,
        wt_pyproject=pyproject,
        original_text=text,
        bumps=bumps,
    )


def _assert_attempt_discarded(
    clone: Path, bumps: list[tuple[str, str, str]]
) -> None:
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    assert not wt_path.exists()
    branch_check = subprocess.run(
        [
            "git",
            "rev-parse",
            "--verify",
            f"refs/heads/repo-shared/tool-bump-{bump_hash}",
        ],
        cwd=clone,
        capture_output=True,
        check=False,
    )
    assert branch_check.returncode != 0, "bump branch should be deleted"


def test_upgrade_tools_refuses_from_consumer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    exit_code = run_cli(["upgrade-tools"])
    assert exit_code == ExitCode.USAGE
    assert "maintainer-only" in capsys.readouterr().err


def test_upgrade_tools_refuses_dirty_clone_when_a_bump_is_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The dirty refusal only fires once a bump is actually available
    # (a quiet PyPI is a no-op that must not be blocked by an unrelated
    # dirty tree), so stub a real ruff bump before dirtying the clone.
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})
    (clone / "DIRTY_MARKER").write_text("local edit\n")
    capsys.readouterr()

    exit_code = run_cli(["upgrade-tools", "--only", "ruff"])
    assert exit_code == ExitCode.DIRTY
    assert "uncommitted changes" in capsys.readouterr().err
    # The refusal happens before any worktree is spun up.
    assert not (clone / ".wt").exists()


def test_upgrade_tools_no_op_ignores_dirty_clone(
    fake_clone: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A quiet PyPI is a no-op; the dirty tree must not turn it into a
    # spurious failure.
    pinned = dict(
        cli._read_pinned_deps((fake_clone / "pyproject.toml").read_text())
    )
    _stub_pypi(monkeypatch, pinned)
    (fake_clone / "DIRTY_MARKER").write_text("local edit\n")

    exit_code = run_cli(["upgrade-tools"])
    assert exit_code == ExitCode.SUCCESS
    assert "every pinned tool is up to date." in capsys.readouterr().out


def test_upgrade_tools_no_op_when_pypi_matches_current_pins(
    fake_clone: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((fake_clone / "pyproject.toml").read_text())
    )
    _stub_pypi(monkeypatch, pinned)

    exit_code = run_cli(["upgrade-tools"])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "every pinned tool is up to date." in out
    # No worktree should have been spun up.
    wt_parent = fake_clone / ".wt"
    assert not wt_parent.exists()


def test_upgrade_tools_does_not_call_unchecked_pins_up_to_date(
    fake_clone: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((fake_clone / "pyproject.toml").read_text())
    )
    _stub_pypi(monkeypatch, {})

    assert run_cli(["upgrade-tools"]) == ExitCode.ERROR

    captured = capsys.readouterr()
    assert "up to date" not in captured.out
    for name in pinned:
        assert name in captured.err.splitlines()[-1]
    assert not (fake_clone / ".wt").exists()


def test_upgrade_tools_fails_when_one_pin_goes_unchecked_and_none_bump(
    fake_clone: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((fake_clone / "pyproject.toml").read_text())
    )
    _stub_pypi(monkeypatch, {**pinned, "ruff": None})

    assert run_cli(["upgrade-tools"]) == ExitCode.ERROR

    captured = capsys.readouterr()
    assert "every pinned tool is up to date." not in captured.out
    assert captured.err.splitlines()[-1].endswith("queried for: ruff.")


@pytest.mark.usefixtures("fake_clone")
def test_upgrade_tools_only_filter_excluding_everything_is_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_cli(["upgrade-tools", "--only", "nonexistent-pkg"])
    assert exit_code == ExitCode.CONFIG
    assert "excluded every pinned tool" in capsys.readouterr().err


def test_upgrade_tools_bumps_through_symlinked_wt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, pinned)
    target = tmp_path / "local-storage"
    target.mkdir()
    (clone / ".wt").symlink_to(target, target_is_directory=True)

    exit_code = run_cli(["upgrade-tools", "--only", "ruff"])
    assert exit_code == ExitCode.SUCCESS
    bump_hash = cli._tool_bump_hash([("ruff", "0.5.0", pinned["ruff"])])
    assert (target / f"repo-shared-tool-bump-{bump_hash}").is_dir()


def test_upgrade_tools_reports_wt_link_with_missing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, pinned)
    (clone / ".wt").symlink_to(tmp_path / "gone", target_is_directory=True)
    capsys.readouterr()

    exit_code = run_cli(["upgrade-tools", "--only", "ruff"])
    assert exit_code == ExitCode.ERROR
    assert "restore its target" in capsys.readouterr().err


def test_upgrade_tools_bumps_one_pin_in_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Downgrade ruff in the fake clone so PyPI's "latest" (real current
    # pin) is a genuine bump; uv lock will resolve and install it.
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})
    capsys.readouterr()

    exit_code = run_cli(["upgrade-tools", "--only", "ruff"])
    assert exit_code == ExitCode.SUCCESS

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    assert wt_path.is_dir()
    bumped_pyproject = (wt_path / "pyproject.toml").read_text()
    assert f'"ruff=={current_ruff}"' in bumped_pyproject
    assert '"ruff==0.5.0"' not in bumped_pyproject

    # And a commit landed on the bump branch in the worktree.
    log = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%s"], cwd=wt_path, text=True
    ).strip()
    assert log.startswith("- deps: bump ")
    assert " from " in log and " to " in log and log.endswith(".")


def test_upgrade_tools_lands_a_bump_beside_an_unchecked_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff, "mypy": None})

    assert run_cli(["upgrade-tools"]) == ExitCode.SUCCESS

    assert "mypy: could not query PyPI; skipping." in capsys.readouterr().err
    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    log = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%s"], cwd=wt_path, text=True
    ).strip()
    assert log == f"- deps: bump ruff from 0.5.0 to {current_ruff}."


@pytest.mark.parametrize(
    ("tool", "old_version"), [("ruff", "0.5.0"), ("mdformat", "0.7.22")]
)
def test_upgrade_tools_push_ff_merges_into_default_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool: str,
    old_version: str,
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_version = pinned[tool]
    clone = _build_fake_clone(tmp_path, pin_overrides={tool: old_version})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, pinned)
    origin = tmp_path / "origin.git"
    pre_push_origin_head = subprocess.check_output(
        ["git", "rev-parse", "main"], cwd=origin, text=True
    ).strip()
    capsys.readouterr()

    exit_code = run_cli(["upgrade-tools", "--push"])
    assert exit_code == ExitCode.SUCCESS

    post_push_origin_head = subprocess.check_output(
        ["git", "rev-parse", "main"], cwd=origin, text=True
    ).strip()
    assert post_push_origin_head != pre_push_origin_head
    landed_pyproject = subprocess.check_output(
        ["git", "show", "main:pyproject.toml"], cwd=origin, text=True
    )
    assert f'"{tool}=={current_version}"' in landed_pyproject
    bumps = [(tool, old_version, current_version)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    assert not wt_path.exists()


def test_upgrade_tools_with_push_rejects_when_origin_non_fast_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})
    advance_origin_to_diverge(tmp_path / "origin.git", tmp_path)
    capsys.readouterr()

    exit_code = run_cli(["upgrade-tools", "--only", "ruff", "--push"])
    assert exit_code == ExitCode.ERROR
    err = capsys.readouterr().err
    assert "git push --dry-run" in err
    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    assert not wt_path.exists()


def test_upgrade_tools_exits_clean_when_every_bump_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The canonical shape: PyPI has a newer version, but the per-pin
    # sweep can't land it together with the existing pin set. The
    # resolver returns accepted=[] + skipped=[bump]; the command should
    # print the status, clean up worktree + branch, and exit SUCCESS.
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})

    bumps = [("ruff", "0.5.0", current_ruff)]

    def fake_resolve(
        **_: object,
    ) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
        return ([], list(bumps))

    monkeypatch.setattr(cli, "_resolve_compatible_bumps", fake_resolve)
    capsys.readouterr()

    exit_code = run_cli(["upgrade-tools", "--only", "ruff"])
    assert exit_code == ExitCode.SUCCESS

    out = capsys.readouterr().out
    assert "no upstream-compatible bumps available this run" in out
    assert "skipped (conflict): ruff 0.5.0" in out
    _assert_attempt_discarded(clone, bumps)


def test_upgrade_tools_conflict_beside_an_unchecked_pin_is_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff, "mypy": None})
    bumps = [("ruff", "0.5.0", current_ruff)]

    def fake_resolve(
        **_: object,
    ) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
        return ([], list(bumps))

    monkeypatch.setattr(cli, "_resolve_compatible_bumps", fake_resolve)

    assert run_cli(["upgrade-tools"]) == ExitCode.ERROR

    assert (
        capsys.readouterr().err.splitlines()[-1].endswith("queried for: mypy.")
    )
    _assert_attempt_discarded(clone, bumps)


def test_upgrade_tools_outage_discards_the_attempt_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})
    monkeypatch.setenv("UV_DEFAULT_INDEX", "http://127.0.0.1:9/simple")
    monkeypatch.setenv("UV_HTTP_RETRIES", "0")
    monkeypatch.setenv("UV_NO_CACHE", "1")

    exit_code = run_cli(["upgrade-tools", "--only", "ruff"])

    assert exit_code == ExitCode.SUBPROCESS
    captured = capsys.readouterr()
    assert "Failed to fetch" in captured.err
    assert "other than a version conflict" in captured.err
    assert "skipped (conflict)" not in captured.out
    _assert_attempt_discarded(clone, [("ruff", "0.5.0", current_ruff)])


def test_upgrade_tools_lock_timeout_discards_the_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})

    def fake_resolve(**_: object) -> None:
        raise subprocess.TimeoutExpired(["uv", "lock"], 600)

    monkeypatch.setattr(cli, "_resolve_compatible_bumps", fake_resolve)

    assert run_cli(["upgrade-tools", "--only", "ruff"]) == ExitCode.TIMEOUT
    _assert_attempt_discarded(clone, [("ruff", "0.5.0", current_ruff)])


@pytest.mark.parametrize(
    "outcomes",
    [
        pytest.param([_OUTAGE], id="first-lock"),
        pytest.param([_BUILD_FAILURE], id="unbuildable-package"),
        pytest.param([_CONFLICT, _LOCKED, _OUTAGE], id="during-sweep"),
        pytest.param(
            [_CONFLICT, _LOCKED, _CONFLICT, _OUTAGE], id="final-flush"
        ),
    ],
)
def test_resolve_stops_at_a_lock_failure_that_is_not_a_conflict(
    tmp_path: Path, outcomes: list[subprocess.CompletedProcess[str]]
) -> None:
    with patch.object(sp, "run", autospec=True) as run:
        run.side_effect = outcomes
        with pytest.raises(cli.LockFailedError):
            _resolve_bumps(tmp_path, [_RUFF_BUMP, _MYPY_BUMP])
    assert run.call_count == len(outcomes)


def _write_lockable_project(tmp_path: Path, requirement: str) -> str:
    text = (
        '[project]\nname = "probe"\nversion = "0"\n'
        'requires-python = ">=3.14"\n'
        f'dependencies = ["ruff==1.0", "{requirement}"]\n'
    )
    (tmp_path / "pyproject.toml").write_text(text)
    return text


def test_uv_reports_a_real_conflict_the_way_we_read_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The bumped pin and the cap exclude every version, so uv reports the
    # conflict offline, whatever its cache holds.
    text = _write_lockable_project(tmp_path, "ruff<1.1")
    monkeypatch.setenv("UV_OFFLINE", "1")

    assert not cli._try_bump_set(
        wt_path=tmp_path,
        wt_pyproject=tmp_path / "pyproject.toml",
        original_text=text,
        bumps=[_RUFF_BUMP],
    )

    report = capsys.readouterr().err
    assert "ruff==1.1" in report
    assert "ruff<1.1" in report


def test_uv_reports_a_real_outage_the_way_we_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _write_lockable_project(tmp_path, "pytest")
    monkeypatch.setenv("UV_DEFAULT_INDEX", "http://127.0.0.1:9/simple")
    monkeypatch.setenv("UV_HTTP_RETRIES", "0")
    monkeypatch.setenv("UV_NO_CACHE", "1")

    with pytest.raises(cli.LockFailedError):
        cli._try_bump_set(
            wt_path=tmp_path,
            wt_pyproject=tmp_path / "pyproject.toml",
            original_text=text,
            bumps=[_RUFF_BUMP],
        )


def test_upgrade_tools_dogfood_failure_leaves_resumable_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(
        tmp_path,
        pin_overrides={"ruff": "0.5.0"},
        dogfood_failure=True,
    )
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})
    capsys.readouterr()

    exit_code = run_cli(["upgrade-tools", "--only", "ruff"])
    assert exit_code == ExitCode.ERROR
    err = capsys.readouterr().err
    assert "dogfood suite failed" in err

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    assert wt_path.is_dir(), "failed dogfood should leave the worktree"
    # The bumped pyproject got written before the dogfood ran.
    bumped_pyproject = (wt_path / "pyproject.toml").read_text()
    assert f'"ruff=={current_ruff}"' in bumped_pyproject
    # The bump is committed before dogfood so the worktree remains
    # inspectable and resumable after a red run.
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=wt_path, text=True
    )
    assert status == ""
    subject = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=wt_path,
        text=True,
    ).strip()
    assert subject == f"- deps: bump ruff from 0.5.0 to {current_ruff}."

    capsys.readouterr()
    retry = run_cli(["upgrade-tools", "--only", "ruff"])
    assert retry == ExitCode.ERROR
    captured = capsys.readouterr()
    assert "resuming existing bump worktree" in captured.out
    assert "dogfood suite failed" in captured.err
    assert (
        subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=wt_path,
            text=True,
        )
        == ""
    )


def test_upgrade_tools_resumes_existing_worktree_on_re_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})

    first = run_cli(["upgrade-tools", "--only", "ruff"])
    assert first == ExitCode.SUCCESS

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    head_after_first = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%H"], cwd=wt_path, text=True
    ).strip()
    capsys.readouterr()

    second = run_cli(["upgrade-tools", "--only", "ruff"])
    assert second == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "resuming existing bump worktree" in out
    head_after_second = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%H"], cwd=wt_path, text=True
    ).strip()
    assert head_after_second == head_after_first


def test_upgrade_tools_resume_rejects_stale_lock_without_dirtying_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})

    first = run_cli(["upgrade-tools", "--only", "ruff"])
    assert first == ExitCode.SUCCESS

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    pyproject = wt_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            'version = "0.1.0"',
            'version = "0.1.1"',
            1,
        )
    )
    git_in(wt_path, "add", "pyproject.toml")
    git_in(wt_path, "commit", "-m", "test: make lock stale")
    capsys.readouterr()

    retry = run_cli(["upgrade-tools", "--only", "ruff"])
    assert retry == ExitCode.ERROR
    captured = capsys.readouterr()
    assert "resuming existing bump worktree" in captured.out
    assert "dogfood suite failed" in captured.err
    assert (
        subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=wt_path,
            text=True,
        )
        == ""
    )


def test_upgrade_tools_dirty_worktree_blocks_re_run_without_force_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})
    first = run_cli(["upgrade-tools", "--only", "ruff"])
    assert first == ExitCode.SUCCESS

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    (wt_path / "DEBUG_SCRATCH").write_text("mid-investigation edit\n")
    capsys.readouterr()

    second = run_cli(["upgrade-tools", "--only", "ruff"])
    assert second == ExitCode.DIRTY
    err = capsys.readouterr().err
    assert "uncommitted changes" in err
    assert "--force-retry" in err
    assert wt_path.is_dir()
    assert (wt_path / "DEBUG_SCRATCH").is_file()


def test_upgrade_tools_force_retry_recreates_dirty_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pinned = dict(
        cli._read_pinned_deps((REPO_ROOT / "pyproject.toml").read_text())
    )
    current_ruff = pinned["ruff"]
    clone = _build_fake_clone(tmp_path, pin_overrides={"ruff": "0.5.0"})
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: clone)
    _stub_pypi(monkeypatch, {**pinned, "ruff": current_ruff})
    first = run_cli(["upgrade-tools", "--only", "ruff"])
    assert first == ExitCode.SUCCESS

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    (wt_path / "DEBUG_SCRATCH").write_text("mid-investigation edit\n")
    capsys.readouterr()

    second = run_cli(["upgrade-tools", "--only", "ruff", "--force-retry"])
    assert second == ExitCode.SUCCESS
    assert wt_path.is_dir()
    assert not (wt_path / "DEBUG_SCRATCH").exists()
    bumped_pyproject = (wt_path / "pyproject.toml").read_text()
    assert f'"ruff=={current_ruff}"' in bumped_pyproject
