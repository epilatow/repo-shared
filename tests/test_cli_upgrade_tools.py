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
``uv run --extra test pytest shared/tests`` -- the quality-gate
subset, which ``test_code_quality`` parametrizes per tracked ``.py``
file. The fixture trims the clone's ``tests/`` to a single smoke test
so that per-file sweep stays small and the nested dogfood runs fast;
the dogfood-failure case instead drops a deliberately failing test
into ``shared/tests/``, where the scoped dogfood will run it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from epilatow_repo_shared import cli
from epilatow_repo_shared.exit_codes import ExitCode

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_TEST = """\
def test_smoke_in_worktree_dogfood() -> None:
    assert True
"""
# Dropped into the clone's ``shared/tests/`` for the dogfood-failure
# case. ``test_code_quality`` also lints / type-checks this file, so it
# must stay ruff- and mypy-clean: the integration test relies on the
# dogfood going red on the deliberate ``AssertionError``, not on a
# spurious quality-gate failure.
FAILING_TEST = """\
def test_deliberately_failing_dogfood_test() -> None:
    # Used by an integration test to assert that a failing dogfood
    # leaves the bump worktree in place for the maintainer to inspect.
    raise AssertionError("deliberate failure for integration test")
"""


def _git_in(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test Author",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_cli(argv: list[str]) -> ExitCode:
    parser = cli.args_parser()
    args = parser.parse_args(argv)
    return cli.main(args)


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
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(clone)],
        check=True,
        capture_output=True,
    )
    _git_in(clone, "checkout", "-B", "main")
    tests_dir = clone / "tests"
    for child in tests_dir.iterdir():
        if child.is_dir():
            subprocess.run(["rm", "-rf", str(child)], check=True)
        else:
            child.unlink()
    (tests_dir / "test_smoke.py").write_text(SMOKE_TEST)
    if dogfood_failure:
        fail_path = clone / "shared" / "tests" / "test_deliberate_failure.py"
        fail_path.write_text(FAILING_TEST)

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
    if ".wt/" not in {line.strip() for line in existing.splitlines()}:
        gitignore.write_text(
            (existing if existing.endswith("\n") else existing + "\n")
            + ".wt/\n"
        )
    _git_in(clone, "add", "-A")
    _git_in(clone, "commit", "-m", "test: stub tests + downgrade pins")

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(clone), str(origin)],
        check=True,
        capture_output=True,
    )
    _git_in(clone, "remote", "set-url", "origin", str(origin))
    _git_in(clone, "push", "--quiet", "-u", "origin", "main")
    _git_in(clone, "remote", "set-head", "origin", "main")
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


def test_upgrade_tools_refuses_from_consumer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    exit_code = _run_cli(["upgrade-tools"])
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

    exit_code = _run_cli(["upgrade-tools", "--only", "ruff"])
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

    exit_code = _run_cli(["upgrade-tools"])
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

    exit_code = _run_cli(["upgrade-tools"])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "every pinned tool is up to date." in out
    # No worktree should have been spun up.
    wt_parent = fake_clone / ".wt"
    assert not wt_parent.exists()


@pytest.mark.usefixtures("fake_clone")
def test_upgrade_tools_only_filter_excluding_everything_is_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run_cli(["upgrade-tools", "--only", "nonexistent-pkg"])
    assert exit_code == ExitCode.CONFIG
    assert "excluded every pinned tool" in capsys.readouterr().err


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

    exit_code = _run_cli(["upgrade-tools", "--only", "ruff"])
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


def test_upgrade_tools_push_ff_merges_into_default_branch(
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
    origin = tmp_path / "origin.git"
    pre_push_origin_head = subprocess.check_output(
        ["git", "rev-parse", "main"], cwd=origin, text=True
    ).strip()
    capsys.readouterr()

    exit_code = _run_cli(["upgrade-tools", "--only", "ruff", "--push"])
    assert exit_code == ExitCode.SUCCESS

    post_push_origin_head = subprocess.check_output(
        ["git", "rev-parse", "main"], cwd=origin, text=True
    ).strip()
    assert post_push_origin_head != pre_push_origin_head
    landed_pyproject = subprocess.check_output(
        ["git", "show", "main:pyproject.toml"], cwd=origin, text=True
    )
    assert f'"ruff=={current_ruff}"' in landed_pyproject
    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    assert not wt_path.exists()


def _advance_origin_to_diverge(origin: Path, tmp_path: Path) -> None:
    """Plant a commit directly on ``origin``'s main so it diverges.

    Same shape as the helper in ``test_cli_integration.py``; lifted
    here to keep the upgrade-tools file self-contained.
    """
    workspace = tmp_path / f"{origin.stem}-divergent-workspace"
    subprocess.run(
        ["git", "clone", "--quiet", str(origin), str(workspace)],
        check=True,
        capture_output=True,
    )
    (workspace / "ORIGIN_DIVERGENCE_MARKER").write_text("plant\n")
    _git_in(workspace, "add", "ORIGIN_DIVERGENCE_MARKER")
    _git_in(workspace, "commit", "-m", "test: divergent commit on origin")
    _git_in(workspace, "push", "--quiet", "origin", "main")


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
    _advance_origin_to_diverge(tmp_path / "origin.git", tmp_path)
    capsys.readouterr()

    exit_code = _run_cli(["upgrade-tools", "--only", "ruff", "--push"])
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
    # sweep can't land it together with the existing pin set (e.g.
    # mdformat 1.0 capped by mdformat-tables<0.8). The resolver
    # returns accepted=[] + skipped=[bump]; the command should print
    # the status, clean up worktree + branch, and exit SUCCESS.
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

    exit_code = _run_cli(["upgrade-tools", "--only", "ruff"])
    assert exit_code == ExitCode.SUCCESS

    out = capsys.readouterr().out
    assert "no upstream-compatible bumps available this run" in out
    assert "skipped (conflict): ruff 0.5.0" in out

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
    )
    assert branch_check.returncode != 0, "bump branch should be deleted"


def test_upgrade_tools_dogfood_failure_leaves_worktree(
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

    exit_code = _run_cli(["upgrade-tools", "--only", "ruff"])
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
    # No commit lands when the dogfood fails -- the bumped file is
    # uncommitted in the worktree for the maintainer to inspect.
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=wt_path, text=True
    )
    assert "pyproject.toml" in status


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

    first = _run_cli(["upgrade-tools", "--only", "ruff"])
    assert first == ExitCode.SUCCESS

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    head_after_first = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%H"], cwd=wt_path, text=True
    ).strip()
    capsys.readouterr()

    second = _run_cli(["upgrade-tools", "--only", "ruff"])
    assert second == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "resuming existing bump worktree" in out
    head_after_second = subprocess.check_output(
        ["git", "log", "-1", "--pretty=%H"], cwd=wt_path, text=True
    ).strip()
    assert head_after_second == head_after_first


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
    first = _run_cli(["upgrade-tools", "--only", "ruff"])
    assert first == ExitCode.SUCCESS

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    (wt_path / "DEBUG_SCRATCH").write_text("mid-investigation edit\n")
    capsys.readouterr()

    second = _run_cli(["upgrade-tools", "--only", "ruff"])
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
    first = _run_cli(["upgrade-tools", "--only", "ruff"])
    assert first == ExitCode.SUCCESS

    bumps = [("ruff", "0.5.0", current_ruff)]
    bump_hash = cli._tool_bump_hash(bumps)
    wt_path = clone / ".wt" / f"repo-shared-tool-bump-{bump_hash}"
    (wt_path / "DEBUG_SCRATCH").write_text("mid-investigation edit\n")
    capsys.readouterr()

    second = _run_cli(["upgrade-tools", "--only", "ruff", "--force-retry"])
    assert second == ExitCode.SUCCESS
    assert wt_path.is_dir()
    assert not (wt_path / "DEBUG_SCRATCH").exists()
    bumped_pyproject = (wt_path / "pyproject.toml").read_text()
    assert f'"ruff=={current_ruff}"' in bumped_pyproject
