# This is AI generated code
"""Internal entry point for isolated shared gates in a consumer environment."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    """Apply isolation after uv has loaded the consumer's dotenv settings.

    Empty pytest configuration excludes plugin-specific ini settings. The
    delivered gates read their own overrides from the consumer's pyproject,
    and their tool processes inherit its remaining environment settings.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
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
    return int(pytest.main(args))


if __name__ == "__main__":
    sys.exit(main())
