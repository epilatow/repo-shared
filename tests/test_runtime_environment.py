"""Tool policy applies before consumer startup and restores state."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from epilatow_repo_shared import shared_test_runner
from epilatow_repo_shared.runtime_environment import node_cache_disabled


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


@pytest.mark.parametrize("isolated", [False, True])
def test_startup_and_node_tools_do_not_write_compile_cache(
    tmp_path: Path, isolated: bool
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise its real compile cache")
    test_dir = tmp_path / ("_repo_shared/tests" if isolated else "tests")
    test_dir.mkdir(parents=True)
    (test_dir / "test_probe.py").write_text(
        "import os\n"
        "import subprocess\n\n"
        "def test_node_cache():\n"
        "    assert os.environ['NODE_DISABLE_COMPILE_CACHE'] == '1'\n"
        f"    subprocess.run([{node!r}, '-e', "
        "\"require('node:module').enableCompileCache?.(); "
        "require('./probe.cjs')\"], check=True)\n"
    )
    (tmp_path / "probe.cjs").write_text("module.exports = 42;\n")
    (test_dir / "conftest.py").write_text(
        "import os\n"
        "assert os.environ['NODE_DISABLE_COMPILE_CACHE'] == '1'\n"
        "def pytest_configure():\n"
        "    assert os.environ['NODE_DISABLE_COMPILE_CACHE'] == '1'\n"
    )
    cache = tmp_path / "compile-cache"
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
    environment.update(
        NODE_DISABLE_COMPILE_CACHE="0", NODE_COMPILE_CACHE=str(cache)
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
    (tests / "test_probe.py").write_text(
        "import os\n"
        "import epilatow_repo_shared\n"
        "from pathlib import Path\n\n"
        "def test_older_package():\n"
        "    installed = Path(epilatow_repo_shared.__file__).parent\n"
        f"    assert str(installed) == {str(package)!r}\n"
        "    assert os.environ['NODE_DISABLE_COMPILE_CACHE'] == '1'\n"
        f"    assert os.environ['PYTEST_DEBUG_TEMPROOT'] == {str(scratch)!r}\n"
    )
    environment = dict(os.environ)
    environment.pop("PYTEST_DEBUG_TEMPROOT", None)
    environment.pop("REPO_SHARED_PYTEST_TEMPROOT", None)
    environment.update(
        PYTHONPATH=str(tmp_path), NODE_DISABLE_COMPILE_CACHE="0"
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
    (tmp_path / "test_probe.py").write_text(
        "import os\n\n"
        "def test_policy():\n"
        "    assert os.environ['NODE_DISABLE_COMPILE_CACHE'] == '1'\n"
    )
    if startup_failure:
        (tmp_path / "conftest.py").write_text(
            "import os\n"
            "assert os.environ['NODE_DISABLE_COMPILE_CACHE'] == '1'\n"
            "raise RuntimeError('startup failed')\n"
        )
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
    code = (
        "import os, pytest\n"
        "result = pytest.main(['-q', '--rootdir=.', 'test_probe.py'])\n"
        "assert os.environ['NODE_DISABLE_COMPILE_CACHE'] == '0'\n"
        "assert 'PYTEST_DEBUG_TEMPROOT' not in os.environ\n"
        "assert 'REPO_SHARED_PYTEST_TEMPROOT' not in os.environ\n"
        f"assert int(result) == {4 if startup_failure else 0}\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
