"""Tool policy applies before consumer startup and restores state."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES

from epilatow_repo_shared import shared_test_runner
from epilatow_repo_shared.runtime_environment import (
    node_cache_disabled,
    prepare_scratch_parent,
)


@pytest.mark.parametrize("previous", [None, "", "0", "1"])
def test_node_policy_restores_environment_after_failure(
    monkeypatch: pytest.MonkeyPatch, previous: str | None
) -> None:
    if previous is None:
        monkeypatch.delenv("NODE_DISABLE_COMPILE_CACHE", raising=False)
    else:
        monkeypatch.setenv("NODE_DISABLE_COMPILE_CACHE", previous)
    with pytest.raises(RuntimeError), node_cache_disabled():
        assert os.environ["NODE_DISABLE_COMPILE_CACHE"] == "1"
        raise RuntimeError("test failure")
    assert os.environ.get("NODE_DISABLE_COMPILE_CACHE") == previous


def test_scratch_parent_works_through_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "tmp"
    link.symlink_to(target, target_is_directory=True)
    prepare_scratch_parent(link / "pytest")
    assert (target / "pytest" / "pyproject.toml").is_file()


def test_scratch_parent_explains_a_dangling_symlink(tmp_path: Path) -> None:
    link = tmp_path / "tmp"
    link.symlink_to(tmp_path / "gone", target_is_directory=True)
    with pytest.raises(ValueError, match="PYTEST_DEBUG_TEMPROOT"):
        prepare_scratch_parent(link / "pytest")


@pytest.mark.parametrize("isolated", [False, True])
def test_startup_and_node_tools_do_not_write_compile_cache(
    tmp_path: Path, isolated: bool
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise its real compile cache")
    test_dir = tmp_path / ("_repo_shared/tests" if isolated else "tests")
    test_dir.mkdir(parents=True)
    shutil.copyfile(
        FIXTURES / "node_cache_probe.py", test_dir / "test_probe.py"
    )
    for script in ("node_cache_probe.cjs", "node_cache_module.cjs"):
        shutil.copyfile(FIXTURES / script, tmp_path / script)
    shutil.copyfile(
        FIXTURES / "node_cache_conftest.py", test_dir / "conftest.py"
    )
    cache = tmp_path / "compile-cache"
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    environment.update(
        NODE_DISABLE_COMPILE_CACHE="0",
        NODE_COMPILE_CACHE=str(cache),
        PROBE_NODE=node,
    )
    command = (
        [sys.executable, str(Path(shared_test_runner.__file__))]
        if isolated
        else [sys.executable, "-m", "pytest", "-c", os.devnull, "tests"]
    )
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not cache.exists()


def test_isolated_runner_supports_package_without_new_helpers(
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    package = tmp_path / "epilatow_repo_shared"
    package.mkdir()
    (package / "__init__.py").write_text("")
    tests = tmp_path / "_repo_shared" / "tests"
    tests.mkdir(parents=True)
    shutil.copyfile(
        FIXTURES / "older_package_probe.py", tests / "test_probe.py"
    )
    environment = dict(os.environ)
    environment.pop("PYTEST_DEBUG_TEMPROOT", None)
    environment.pop("REPO_SHARED_PYTEST_TEMPROOT", None)
    environment.update(
        PYTHONPATH=str(tmp_path),
        NODE_DISABLE_COMPILE_CACHE="0",
        PROBE_PACKAGE=str(package),
        PROBE_SCRATCH=str(scratch),
    )
    result = subprocess.run(
        [
            sys.executable,
            str(Path(shared_test_runner.__file__)),
            "--default-temproot",
            str(scratch),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("startup_failure", [False, True])
def test_pytest_main_restores_environment(
    tmp_path: Path, startup_failure: bool
) -> None:
    shutil.copyfile(
        FIXTURES / "node_policy_probe.py", tmp_path / "test_probe.py"
    )
    if startup_failure:
        shutil.copyfile(
            FIXTURES / "startup_failure_conftest.py", tmp_path / "conftest.py"
        )
    driver = tmp_path / "pytest_main_driver.py"
    shutil.copyfile(FIXTURES / "pytest_main_driver.py", driver)
    environment = dict(os.environ)
    for name in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTEST_DEBUG_TEMPROOT",
        "REPO_SHARED_PYTEST_TEMPROOT",
    ):
        environment.pop(name, None)
    environment["NODE_DISABLE_COMPILE_CACHE"] = "0"
    expected_exit = 4 if startup_failure else 0
    result = subprocess.run(
        [sys.executable, str(driver), str(expected_exit)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
