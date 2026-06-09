# This is AI generated code
"""End-to-end: the README's documented ``uvx --from ...`` onboarding works.

Existing CLI integration tests call ``cli.main([...])`` in-process via
the test's editable install -- they exercise the ``init`` flow but
bypass two layers a real consumer hits:

1. ``uvx --from "git+..."`` resolves the git URL, builds the wheel, and
   creates an ephemeral venv to run the executable in.
2. ``uv`` looks up the executable name in the installed wheel's
   ``[project.scripts]`` and refuses with "An executable named
   ``<name>`` is not provided by package ..." if it isn't registered.

A real consumer's first command goes through both. This test runs the
literal ``uvx --from "git+file://<fake_source>" repo-shared init
<consumer>`` invocation as a subprocess so the script-entry-point and
ephemeral-venv layers stay covered.

Slow: uvx builds the wheel + resolves the ephemeral venv on each
invocation (cached after the first). One test, one onboarding.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from test_cli_integration import _clone_fake_source

_UVX_REQUIRED = pytest.mark.skipif(
    shutil.which("uvx") is None, reason="uvx not on PATH"
)


@pytest.fixture(scope="module")
def _shared_fake_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    parent = tmp_path_factory.mktemp("uvx_fake_source_parent")
    return _clone_fake_source(parent / "fake-source")


@_UVX_REQUIRED
def test_uvx_from_git_invokes_repo_shared_init(
    _shared_fake_source: Path, tmp_path: Path
) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(consumer)],
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        [
            "uvx",
            "--from",
            f"git+file://{_shared_fake_source}",
            "repo-shared",
            "init",
            str(consumer),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, (
        f"uvx onboarding invocation failed -- the documented "
        f"README flow is broken:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert (consumer / "_repo_shared").is_dir(), (
        "uvx invocation exit-0 but the consumer wasn't onboarded "
        "(no _repo_shared/ directory):\n" + result.stdout
    )
    assert (consumer / "CLAUDE.md").is_file(), (
        "consumer is missing the canonical-path CLAUDE.md copy"
    )
    assert (consumer / "AGENTS.md").is_symlink(), (
        "consumer is missing the canonical-path AGENTS.md symlink"
    )
    assert (consumer / ".gitignore").is_file(), (
        "consumer is missing the seeded .gitignore"
    )
