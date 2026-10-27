"""Pytest scratch placement preserves native concurrency and retention."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypedDict

import pytest
from conftest import FIXTURES, git_init_repo

from epilatow_repo_shared import shared_test_runner
from epilatow_repo_shared.pytest_scratch import (
    default_pytest_temproot,
    pytest_scratch,
)


class _Observation(TypedDict):
    base: str
    path: str
    policy: str
    count: int


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _probe_repo(repo: Path, metadata: Path | None = None) -> Path:
    repo.mkdir()
    if metadata is not None:
        subprocess.run(
            ["git", "init", "-q", f"--separate-git-dir={metadata}", str(repo)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    git_init_repo(repo)
    tests = repo / "_repo_shared" / "tests"
    tests.mkdir(parents=True)
    (repo / ".gitignore").write_text("tmp/\n.wt/\n")
    shutil.copyfile(FIXTURES / "scratch_probe.py", tests / "test_probe.py")
    _git(repo, "add", ".gitignore", "_repo_shared/tests/test_probe.py")
    _git(repo, "commit", "-qm", "test fixture")
    return repo


def _run_probe(
    repo: Path,
    record: Path,
    *args: str,
    isolated: bool = False,
    overrides: dict[str, str] | None = None,
    expected_exit: int = 0,
    natural_config: bool = False,
) -> _Observation:
    environment = dict(os.environ)
    for name in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTEST_DEBUG_TEMPROOT",
        "REPO_SHARED_PYTEST_TEMPROOT",
        "UV_ENV_FILE",
    ):
        environment.pop(name, None)
    environment["PROBE_RECORD"] = str(record)
    environment.update(overrides or {})
    command = (
        [
            sys.executable,
            str(Path(shared_test_runner.__file__)),
            "--default-temproot",
            str(default_pytest_temproot(repo, repo)),
        ]
        if isolated
        else [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *([] if natural_config else ["-c", os.devnull, "--rootdir=."]),
            "_repo_shared/tests",
            *args,
        ]
    )
    result = subprocess.run(
        command,
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    value: _Observation = json.loads(record.read_text())
    return value


@pytest.mark.parametrize("isolated", [False, True])
def test_main_checkout_owns_scratch_for_linked_worktrees(
    tmp_path: Path, isolated: bool
) -> None:
    repo = _probe_repo(tmp_path / "main checkout")
    linked = repo / ".wt" / "feature branch"
    _git(repo, "worktree", "add", "-b", "feature", str(linked))
    expected = repo / "tmp" / "pytest"
    assert default_pytest_temproot(linked, linked) == expected
    observation = _run_probe(
        linked, tmp_path / "record.json", isolated=isolated
    )
    assert Path(observation["base"]).is_relative_to(expected)
    assert not (linked / "tmp" / "pytest").exists()
    assert observation["policy"] == "all"
    assert observation["count"] == 3


@pytest.mark.parametrize("linked", [False, True])
def test_separate_git_metadata_does_not_own_scratch(
    tmp_path: Path, linked: bool
) -> None:
    metadata = tmp_path / "metadata.git"
    repo = _probe_repo(tmp_path / "checkout", metadata)
    source = repo
    if linked:
        source = repo / ".wt" / "feature"
        _git(repo, "worktree", "add", "-b", "feature", str(source))
    assert default_pytest_temproot(source, source) == repo / "tmp" / "pytest"
    observation = _run_probe(source, tmp_path / "record.json")
    assert Path(observation["base"]).is_relative_to(repo / "tmp" / "pytest")
    assert not (metadata / "tmp").exists()


def test_unknown_main_checkout_requires_explicit_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _probe_repo(tmp_path / "main", tmp_path / "metadata.git")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-b", "feature", str(linked))
    with pytest.raises(ValueError, match="PYTEST_DEBUG_TEMPROOT"):
        default_pytest_temproot(linked, linked)
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(explicit))
    with pytest_scratch(linked, linked):
        assert os.environ["PYTEST_DEBUG_TEMPROOT"] == str(explicit)
    assert not (linked / "tmp").exists()


def test_nested_repository_owns_scratch_with_natural_config_discovery(
    tmp_path: Path,
) -> None:
    outer = _probe_repo(tmp_path / "outer")
    (outer / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["_repo_shared/tests"]\n'
    )
    (outer / "fixtures").mkdir()
    inner = _probe_repo(outer / "fixtures" / "inner")
    observation = _run_probe(
        inner, tmp_path / "record.json", natural_config=True
    )
    assert Path(observation["base"]).is_relative_to(inner / "tmp" / "pytest")


def test_nongit_session_uses_invocation_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_DEBUG_TEMPROOT", raising=False)
    monkeypatch.delenv("REPO_SHARED_PYTEST_TEMPROOT", raising=False)
    with tempfile.TemporaryDirectory(prefix="repo-shared-nongit-") as name:
        invocation = Path(name).resolve()
        expected = invocation / "tmp" / "pytest"
        assert default_pytest_temproot(
            Path(os.devnull).parent, invocation
        ) == (expected)
        with pytest_scratch(Path(os.devnull).parent, invocation):
            assert os.environ["PYTEST_DEBUG_TEMPROOT"] == str(expected)
            assert expected.is_dir()


@pytest.mark.parametrize("isolated", [False, True])
def test_explicit_temproot_and_basetemp_are_honored(
    tmp_path: Path, isolated: bool
) -> None:
    repo = _probe_repo(tmp_path / "repo")
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    observation = _run_probe(
        repo,
        tmp_path / "record.json",
        isolated=isolated,
        overrides={"PYTEST_DEBUG_TEMPROOT": str(explicit)},
    )
    assert Path(observation["base"]).is_relative_to(explicit)
    assert not (repo / "tmp" / "pytest").exists()
    if not isolated:
        base = tmp_path / "dedicated-basetemp"
        observation = _run_probe(
            repo, tmp_path / "base.json", f"--basetemp={base}"
        )
        assert Path(observation["base"]) == base
        assert not (repo / "tmp" / "pytest").exists()


@pytest.mark.parametrize("isolated", [False, True])
def test_inherited_defaults_rebind_for_another_repository(
    tmp_path: Path, isolated: bool
) -> None:
    repo = _probe_repo(tmp_path / "repo")
    inherited = str(tmp_path / "other-repo" / "tmp" / "pytest")
    observation = _run_probe(
        repo,
        tmp_path / "record.json",
        isolated=isolated,
        overrides={
            "PYTEST_DEBUG_TEMPROOT": inherited,
            "REPO_SHARED_PYTEST_TEMPROOT": inherited,
        },
    )
    assert Path(observation["base"]).is_relative_to(repo / "tmp" / "pytest")
    assert not Path(inherited).exists()


def test_concurrent_runs_use_distinct_numbered_directories(
    tmp_path: Path,
) -> None:
    repo = _probe_repo(tmp_path / "repo")
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = [
            pool.submit(_run_probe, repo, tmp_path / f"record-{i}.json")
            for i in range(2)
        ]
        observations = [run.result() for run in runs]
    bases = {value["base"] for value in observations}
    assert len(bases) == 2
    assert all(Path(base).name.startswith("pytest-") for base in bases)


def test_native_retention_keeps_only_configured_runs(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "repo")
    runs = [
        _run_probe(
            repo,
            tmp_path / f"record-{i}.json",
            "-o",
            "tmp_path_retention_count=2",
        )
        for i in range(3)
    ]
    assert not Path(runs[0]["base"]).exists()
    assert all(Path(run["path"]).exists() for run in runs[1:])


@pytest.mark.parametrize("failed", [False, True])
def test_native_failed_policy_preserves_failure_diagnostics(
    tmp_path: Path, failed: bool
) -> None:
    repo = _probe_repo(tmp_path / "repo")
    observation = _run_probe(
        repo,
        tmp_path / "record.json",
        "-o",
        "tmp_path_retention_policy=failed",
        overrides={"PROBE_FAIL": "1" if failed else "0"},
        expected_exit=1 if failed else 0,
    )
    assert Path(observation["path"]).exists() == failed


def test_scratch_environment_restores_after_nested_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _probe_repo(tmp_path / "first")
    second = _probe_repo(tmp_path / "second")
    for name in ("PYTEST_DEBUG_TEMPROOT", "REPO_SHARED_PYTEST_TEMPROOT"):
        monkeypatch.delenv(name, raising=False)
    with pytest_scratch(first, first):
        expected = dict(os.environ)
        with pytest.raises(RuntimeError), pytest_scratch(second, second):
            assert os.environ["PYTEST_DEBUG_TEMPROOT"] == str(
                second / "tmp" / "pytest"
            )
            raise RuntimeError("startup failed")
        assert dict(os.environ) == expected
    assert "PYTEST_DEBUG_TEMPROOT" not in os.environ
    assert "REPO_SHARED_PYTEST_TEMPROOT" not in os.environ


def test_uv_fixtures_do_not_modify_checkout_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _probe_repo(tmp_path / "repo")
    project = repo / "pyproject.toml"
    content = (
        '[project]\nname = "outer"\nversion = "0.0.0"\n'
        'requires-python = ">=3.14"\n'
    )
    project.write_text(content)
    monkeypatch.delenv("PYTEST_DEBUG_TEMPROOT", raising=False)
    monkeypatch.delenv("REPO_SHARED_PYTEST_TEMPROOT", raising=False)
    with pytest_scratch(repo, repo):
        fixture = repo / "tmp" / "pytest" / "fixture"
        subprocess.run(
            ["uv", "init", "--bare", "--name", "fixture", str(fixture)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
            ["uv", "lock", "--offline"],
            cwd=fixture,
            check=True,
            capture_output=True,
            timeout=60,
        )
    assert project.read_text() == content
    assert (fixture / "uv.lock").exists()
    assert not (repo / "uv.lock").exists()
