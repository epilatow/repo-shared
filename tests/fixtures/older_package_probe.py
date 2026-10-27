"""Probe test for the isolated runner on an older installed package.

``PROBE_PACKAGE`` names the package directory the probe must import,
and ``PROBE_SCRATCH`` the scratch root the runner must select.
"""

import os
from pathlib import Path

import epilatow_repo_shared


def test_older_package() -> None:
    installed = Path(epilatow_repo_shared.__file__).parent
    assert str(installed) == os.environ["PROBE_PACKAGE"]
    assert os.environ["NODE_DISABLE_COMPILE_CACHE"] == "1"
    assert os.environ["PYTEST_DEBUG_TEMPROOT"] == os.environ["PROBE_SCRATCH"]
