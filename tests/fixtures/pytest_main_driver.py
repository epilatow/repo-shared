"""Run ``test_probe.py`` through ``pytest.main`` in this process.

The caller starts it with ``NODE_DISABLE_COMPILE_CACHE=0``, neither
temproot variable set, and the expected exit status as the first
argument. Fails unless the session exits with that status and leaves
those three variables as it found them.
"""

import os
import sys

import pytest

result = pytest.main(["-q", "--rootdir=.", "test_probe.py"])
assert os.environ["NODE_DISABLE_COMPILE_CACHE"] == "0"
assert "PYTEST_DEBUG_TEMPROOT" not in os.environ
assert "REPO_SHARED_PYTEST_TEMPROOT" not in os.environ
assert int(result) == int(sys.argv[1])
