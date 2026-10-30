"""Environment policies for development tools and test sessions."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

_UV_BOUNDARY = '[tool.uv.workspace]\nmembers = []\nexclude = ["**"]\n'


def prepare_scratch_parent(parent: Path) -> None:
    """Keep nested uv fixtures outside their enclosing repo's workspace."""
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError as error:
        raise ValueError(
            f"Cannot create pytest scratch {parent}: {error}. Restore the "
            "target of a dangling symlink, or set PYTEST_DEBUG_TEMPROOT."
        ) from error
    boundary = parent / "pyproject.toml"
    if boundary.exists():
        if boundary.read_text(encoding="ascii") != _UV_BOUNDARY:
            raise ValueError(
                f"Unexpected pytest workspace boundary: {boundary}"
            )
        return
    descriptor, name = tempfile.mkstemp(prefix=".uv-boundary-", dir=parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(_UV_BOUNDARY)
        try:
            os.link(temporary, boundary)
        except FileExistsError:
            if boundary.read_text(encoding="ascii") != _UV_BOUNDARY:
                raise ValueError(
                    f"Unexpected pytest workspace boundary: {boundary}"
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def tool_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Disable persistent Node compilation caches in child tools."""
    result = dict(os.environ if environment is None else environment)
    result["NODE_DISABLE_COMPILE_CACHE"] = "1"
    return result


@contextmanager
def temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    """Restore the overridden variables when a nested session finishes."""
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def node_cache_disabled() -> Iterator[None]:
    """Apply the tool policy to descendants until the session closes."""
    with temporary_environment({"NODE_DISABLE_COMPILE_CACHE": "1"}):
        yield
