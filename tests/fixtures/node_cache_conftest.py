"""Conftest that checks the compile-cache policy during pytest startup."""

import os

assert os.environ["NODE_DISABLE_COMPILE_CACHE"] == "1"


def pytest_configure() -> None:
    assert os.environ["NODE_DISABLE_COMPILE_CACHE"] == "1"
