# This is AI generated code
"""Tests for the subprocess timeout wrapper."""

from __future__ import annotations

import subprocess
from unittest import mock

from epilatow_repo_shared import sp
from epilatow_repo_shared.runtime_environment import tool_environment


def test_run_defaults_check_to_false() -> None:
    with mock.patch.object(subprocess, "run", autospec=True) as run:
        sp.run(["command"])

    run.assert_called_once_with(
        ["command"],
        timeout=sp.SHORT_TIMEOUT_SECONDS,
        check=False,
        env=tool_environment(),
    )


def test_run_forwards_explicit_check() -> None:
    with mock.patch.object(subprocess, "run", autospec=True) as run:
        sp.run(["command"], check=True)

    run.assert_called_once_with(
        ["command"],
        timeout=sp.SHORT_TIMEOUT_SECONDS,
        check=True,
        env=tool_environment(),
    )


def test_run_enforces_policy_without_mutating_explicit_environment() -> None:
    environment = {
        "CONSUMER_OPTION": "retained",
        "NODE_DISABLE_COMPILE_CACHE": "0",
    }
    with mock.patch.object(subprocess, "run", autospec=True) as run:
        sp.run(["command"], env=environment)

    assert environment["NODE_DISABLE_COMPILE_CACHE"] == "0"
    run.assert_called_once_with(
        ["command"],
        timeout=sp.SHORT_TIMEOUT_SECONDS,
        check=False,
        env={"CONSUMER_OPTION": "retained", "NODE_DISABLE_COMPILE_CACHE": "1"},
    )
