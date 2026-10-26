# This is AI generated code
"""Internal entry point for isolated shared gates in a consumer environment."""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

# Executed scripts resolve siblings from this CLI, not the target package.
if __package__ or TYPE_CHECKING:
    from epilatow_repo_shared.runtime_environment import (
        node_cache_disabled,
        prepare_scratch_parent,
        temporary_environment,
    )
else:
    from runtime_environment import (
        node_cache_disabled,
        prepare_scratch_parent,
        temporary_environment,
    )


def main() -> int:
    """Apply isolation after uv has loaded the consumer's dotenv settings.

    Empty pytest configuration excludes plugin-specific ini settings. The
    delivered gates read their own overrides from the consumer's pyproject,
    and their tool processes inherit its remaining environment settings.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--default-temproot", type=Path)
    options = parser.parse_args()
    os.environ.pop("PYTEST_ADDOPTS", None)
    os.environ.pop("PYTEST_PLUGINS", None)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    import pytest

    root = Path.cwd()
    args = [
        "-c",
        os.devnull,
        "--rootdir",
        str(root),
        "--noconftest",
        "_repo_shared/tests",
    ]
    if options.verbose:
        args.append("-v")
    # This file also runs in environments pinned before our current helpers.
    overrides: dict[str, str] = {}
    name = "PYTEST_DEBUG_TEMPROOT"
    marker = "REPO_SHARED_PYTEST_TEMPROOT"
    current = os.environ.get(name)
    if options.default_temproot is not None and (
        current is None or current == os.environ.get(marker)
    ):
        prepare_scratch_parent(options.default_temproot)
        overrides[name] = str(options.default_temproot)
        overrides[marker] = str(options.default_temproot)
    with ExitStack() as environment:
        environment.enter_context(node_cache_disabled())
        environment.enter_context(temporary_environment(overrides))
        return int(pytest.main(args))


if __name__ == "__main__":
    sys.exit(main())
