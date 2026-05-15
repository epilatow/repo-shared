# This is AI generated code
"""Subcommand visibility in ``--help`` is context-aware.

``args_parser`` shows different subcommand listings depending on
whether the CLI is running from a repo-shared maintainer clone or
from an installed consumer. The mechanism is a custom
``metavar="{...}"`` on ``add_subparsers`` plus a per-subcommand
``help=`` omission for hidden commands.

These gates the visibility plumbing -- the runtime refusal guards
inside the hidden subcommands are tested separately
(``test_cli_upgrade_tools.py::test_upgrade_tools_refuses_from_consumer``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from epilatow_repo_shared import cli


def test_upgrade_tools_hidden_from_consumer_help_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_running_from_local_repo_shared", lambda: None)
    help_text = cli.args_parser().format_help()
    assert "upgrade-tools" not in help_text
    assert "init" in help_text
    assert "upgrade" in help_text
    assert "status" in help_text
    assert "run-tests" in help_text


def test_upgrade_tools_visible_in_repo_shared_help_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli, "_running_from_local_repo_shared", lambda: tmp_path
    )
    help_text = cli.args_parser().format_help()
    assert "upgrade-tools" in help_text


def test_run_tests_hidden_from_maintainer_help_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run-tests`` is consumer-only and shouldn't show in maintainer help.

    A maintainer running pytest against ``shared/tests`` directly
    (or the repo's own testpaths via ``uv run pytest``) covers the
    maintainer's "dogfood the delivered tests" need; ``run-tests``
    is purely the consumer-side wrapper that points pytest at the
    vendored ``_repo_shared/tests`` location.
    """
    monkeypatch.setattr(
        cli, "_running_from_local_repo_shared", lambda: tmp_path
    )
    help_text = cli.args_parser().format_help()
    assert "run-tests" not in help_text
