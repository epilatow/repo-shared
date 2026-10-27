"""Deliberately failing test for an ``upgrade-tools`` test clone.

Dropped into the clone's ``shared/tests/`` so its dogfood run goes red,
which must leave the bump worktree in place for inspection. The clone's
quality gates check this file too, so it stays lint- and type-clean for
the run to fail on this test alone.
"""


def test_deliberately_failing_dogfood_test() -> None:
    raise AssertionError("deliberate failure for integration test")
