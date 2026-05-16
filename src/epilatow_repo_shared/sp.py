# This is AI generated code
"""Subprocess helpers with default timeouts.

Every subprocess invocation in this package goes through ``run`` so
no command can hang the CLI indefinitely. Two timeout categories
cover every real use:

- ``SHORT_TIMEOUT_SECONDS`` (60s) -- purely local operations: git
  plumbing against the working tree, ``uv init``, file-system
  scaffolding. No remote round-trip, no test runner.
- ``LONG_TIMEOUT_SECONDS`` (600s) -- anything that can block on the
  network or on a test runner: ``git fetch`` / ``push`` / ``ls-remote``,
  ``uv add`` / ``uv lock`` (which hit PyPI and git remotes), the
  consumer's pytest run invoked by ``repo-shared upgrade
  --run-tests`` / ``--push``, and the test-base classes' own tool
  invocations (``mdformat``, ``markdownlint-cli2``, ``mypy --strict``)
  which can take well over a minute on a moderate repo. Network ops
  share the long bucket because an unreachable remote can stall past
  the OS TCP-SYN budget (>60s on macOS), which is longer than the
  short timeout's entire budget.

Callers pass ``timeout=`` explicitly (an ``int`` for seconds, or
one of the two constants for the category). A ``TimeoutExpired``
exception escalates to the top-level CLI dispatcher in
``cli.main`` which maps it to ``ExitCode.TIMEOUT``.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any, Literal, overload

if TYPE_CHECKING:
    from collections.abc import Sequence

SHORT_TIMEOUT_SECONDS = 60
LONG_TIMEOUT_SECONDS = 600


@overload
def run(
    cmd: Sequence[str],
    *,
    timeout: int = ...,
    text: Literal[True],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]: ...


@overload
def run(
    cmd: Sequence[str],
    *,
    timeout: int = ...,
    text: Literal[False] = ...,
    **kwargs: Any,
) -> subprocess.CompletedProcess[bytes]: ...


def run(
    cmd: Sequence[str],
    *,
    timeout: int = SHORT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run ``cmd`` with a mandatory timeout.

    Thin wrapper around ``subprocess.run`` that forces every
    invocation to carry a timeout. Re-raises
    ``subprocess.TimeoutExpired`` so the top-level CLI dispatcher
    can map it to ``ExitCode.TIMEOUT``.

    Return type tracks ``text=``: ``text=True`` returns
    ``CompletedProcess[str]``, otherwise ``CompletedProcess[bytes]``.
    Callers that read ``stdout`` / ``stderr`` pass ``text=True``;
    callers that only inspect ``returncode`` get bytes and can ignore
    the payload.
    """
    return subprocess.run(cmd, timeout=timeout, **kwargs)
