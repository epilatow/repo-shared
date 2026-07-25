# This is AI generated code
"""Tests for the subprocess timeout wrapper."""

from __future__ import annotations

import subprocess
from unittest import mock

from epilatow_repo_shared import sp


def test_run_defaults_check_to_false() -> None:
    with mock.patch.object(subprocess, "run", autospec=True) as run:
        sp.run(["command"])

    run.assert_called_once_with(
        ["command"],
        timeout=sp.SHORT_TIMEOUT_SECONDS,
        check=False,
    )


def test_run_forwards_explicit_check() -> None:
    with mock.patch.object(subprocess, "run", autospec=True) as run:
        sp.run(["command"], check=True)

    run.assert_called_once_with(
        ["command"],
        timeout=sp.SHORT_TIMEOUT_SECONDS,
        check=True,
    )
