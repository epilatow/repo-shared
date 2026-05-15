# This is AI generated code
"""Exit-code conventions for the repo-shared CLI.

Low-numbered codes follow utils/bin conventions so shell wrappers
can pattern-match on category. The ``64`` / ``65`` / ``66`` codes
follow sysexits.h and are kept here for wrappers that already
pattern-match on them.

The ``description`` attribute on each member feeds
``ExitCode.epilog()``, which the top-level argparse parser
embeds in ``repo-shared --help`` so the codes are documented
where users actually look.
"""

from __future__ import annotations

import enum


class ExitCode(enum.IntEnum):
    SUCCESS = (0, "Success")
    WARNING = (1, "Warning (non-fatal issues; reserved)")
    USAGE = (2, "Usage / argument error")
    CONFIG = (3, "Repo / config-file misconfiguration")
    ERROR = (4, "General error (specific reason on stderr)")
    SUBPROCESS = (5, "A spawned subprocess exited non-zero")
    TIMEOUT = (9, "A spawned subprocess was killed for timeout")
    DIRTY = (65, "Working tree has uncommitted changes")

    description: str

    def __new__(cls, value: int, description: str = "") -> ExitCode:
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.description = description
        return obj

    @classmethod
    def epilog(cls) -> str:
        """Return an argparse ``epilog=`` block listing every code."""
        width = max(len(str(member.value)) for member in cls)
        lines = ["exit codes:"]
        for member in cls:
            lines.append(f"  {member.value:>{width}}  {member.description}")
        return "\n".join(lines)
