"""Probe test that checks the compile-cache policy inside a session."""

import os


def test_policy() -> None:
    assert os.environ["NODE_DISABLE_COMPILE_CACHE"] == "1"
