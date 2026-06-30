# This is AI generated code
"""Subcommand visibility in ``--help`` is context-aware.

``args_parser`` lists different subcommands depending on the state of
the repo the CLI is invoked in (the cwd, classified by
``_classify_repo`` into ``SOURCE`` / ``ONBOARDED`` / ``PLAIN``). The
mechanism is a custom ``metavar="{...}"`` on ``add_subparsers`` plus a
per-subcommand ``help=`` omission for hidden commands.

These gate the visibility plumbing -- the runtime refusal guards
inside each command are tested separately (the ``_refuse_*`` paths in
``test_cli_integration.py`` and ``test_cli_upgrade_tools.py``).
"""

from __future__ import annotations

import pytest

from epilatow_repo_shared import cli


def _help_for_state(
    monkeypatch: pytest.MonkeyPatch, state: cli.RepoState
) -> str:
    monkeypatch.setattr(cli, "_classify_repo", lambda _path: state)
    return cli.args_parser().format_help()


def test_source_clone_lists_init_upgrade_status_and_upgrade_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    help_text = _help_for_state(monkeypatch, cli.RepoState.SOURCE)
    assert "init" in help_text
    assert "upgrade" in help_text
    assert "status" in help_text
    assert "upgrade-tools" in help_text
    # ``run-tests`` runs through the consumer's pinned version, so it
    # is not offered from the clone.
    assert "run-tests" not in help_text


def test_onboarded_consumer_hides_init_and_upgrade_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    help_text = _help_for_state(monkeypatch, cli.RepoState.ONBOARDED)
    assert "upgrade" in help_text
    assert "status" in help_text
    assert "run-tests" in help_text
    # Already onboarded: ``init`` is meaningless here, and
    # ``upgrade-tools`` is maintainer-only.
    assert "init" not in help_text
    assert "upgrade-tools" not in help_text


def test_plain_repo_lists_only_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    help_text = _help_for_state(monkeypatch, cli.RepoState.PLAIN)
    assert "init" in help_text
    for hidden in ("upgrade", "status", "run-tests", "upgrade-tools"):
        assert hidden not in help_text, f"{hidden} should be hidden"
