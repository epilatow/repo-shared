"""Environment policies for development tools and test sessions."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager


def tool_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Disable persistent Node compilation caches in child tools."""
    result = dict(os.environ if environment is None else environment)
    result["NODE_DISABLE_COMPILE_CACHE"] = "1"
    return result


@contextmanager
def node_cache_disabled() -> Iterator[None]:
    """Apply the tool policy to descendants until the session closes."""
    name = "NODE_DISABLE_COMPILE_CACHE"
    previous = os.environ.get(name)
    os.environ[name] = tool_environment()[name]
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
