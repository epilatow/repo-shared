"""Probe test that runs Node under the compile-cache policy.

``PROBE_NODE`` names the Node binary; ``node_cache_probe.cjs`` must sit
in the working directory.
"""

import os
import subprocess


def test_node_cache() -> None:
    assert os.environ["NODE_DISABLE_COMPILE_CACHE"] == "1"
    subprocess.run(
        [os.environ["PROBE_NODE"], "node_cache_probe.cjs"], check=True
    )
