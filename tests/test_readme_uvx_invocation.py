# This is AI generated code
"""README's documented ``uvx --from ...`` invocations match pyproject.

The README's onboarding section tells consumers to run
``uvx --from "git+..." <executable> init``. ``<executable>`` must be a
key in ``[project.scripts]`` of this package's pyproject -- otherwise
``uvx`` errors with "An executable named ``<name>`` is not provided by
package ``epilatow-repo-shared``" and the documented onboarding flow
breaks at the user's first command. This test catches that drift
statically (no subprocess) so a README edit + pyproject rename can't
ship together silently.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ``uvx --from "<url>"`` followed by the first non-flag token; tolerant
# of backslash-continued shell lines that get joined before matching.
_UVX_FROM_PATTERN = re.compile(r'uvx\s+--from\s+"[^"]+"\s+(\S+)')


def test_readme_uvx_invocations_match_project_scripts() -> None:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = set(pyproject.get("project", {}).get("scripts", {}))
    assert scripts, "no [project.scripts] entry in pyproject.toml"

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    # Join backslash-continued shell lines so a multi-line ``uvx``
    # invocation reads as one logical command for the regex.
    joined = re.sub(r"\\\n\s*", " ", readme)
    invocations = _UVX_FROM_PATTERN.findall(joined)
    assert invocations, (
        'no `uvx --from "..." <name>` invocations found in README -- '
        "test is brittle if the documented onboarding flow changes shape; "
        "update the regex or remove this test."
    )

    for executable in invocations:
        assert executable in scripts, (
            f"README documents `uvx --from ... {executable}` but "
            f"{executable!r} is not in [project.scripts] "
            f"(known: {sorted(scripts)!r}). Either fix the README or "
            f"add the entry to pyproject's [project.scripts]."
        )
