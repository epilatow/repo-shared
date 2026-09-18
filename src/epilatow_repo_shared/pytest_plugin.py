"""Run repo-shared checks before consumer-owned tests.

The shared tests and the consumer's tests are collected in one pytest
session. Keep the shared items at the front of that session, then stop at the
boundary when any shared item failed so a slow consumer suite never starts
against a tree that already failed its common quality gates.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class _SharedTestPhase:
    session: pytest.Session
    nodeids: frozenset[str]
    last_nodeid: str
    failed: bool = False


_phase: _SharedTestPhase | None = None


def _shared_test_dir(session: pytest.Session) -> Path:
    root = session.config.rootpath.resolve()
    vendored = root / "_repo_shared" / "tests"
    if vendored.is_dir():
        return vendored
    return root / "shared" / "tests"


def _is_shared_item(item: pytest.Item, shared_test_dir: Path) -> bool:
    try:
        Path(item.path).resolve().relative_to(shared_test_dir)
    except ValueError:
        return False
    return True


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(
    session: pytest.Session,
    items: list[pytest.Item],
) -> Generator[None, object, None]:
    """Put shared items first and remember the shared/local boundary."""
    global _phase

    yield

    shared_test_dir = _shared_test_dir(session)
    shared = [item for item in items if _is_shared_item(item, shared_test_dir)]
    local = [
        item for item in items if not _is_shared_item(item, shared_test_dir)
    ]
    items[:] = [*shared, *local]
    if shared and local:
        _phase = _SharedTestPhase(
            session=session,
            nodeids=frozenset(item.nodeid for item in shared),
            last_nodeid=shared[-1].nodeid,
        )
    else:
        _phase = None


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Stop the session after a red shared phase, before local tests."""
    if _phase is None or report.nodeid not in _phase.nodeids:
        return
    if report.failed:
        _phase.failed = True
    if (
        report.when == "teardown"
        and report.nodeid == _phase.last_nodeid
        and _phase.failed
    ):
        _phase.session.shouldfail = (
            "repo-shared tests failed; local tests were not run"
        )
