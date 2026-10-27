"""Probe test that records where pytest placed its scratch.

The record goes to the JSON file named by ``PROBE_RECORD``; the test
fails when ``PROBE_FAIL`` is ``1``.
"""

import json
import os
from pathlib import Path

import pytest


def test_probe(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    pytestconfig: pytest.Config,
) -> None:
    (tmp_path / "marker").write_text("kept")
    record = {
        "base": str(tmp_path_factory.getbasetemp()),
        "path": str(tmp_path),
        "policy": pytestconfig.getini("tmp_path_retention_policy"),
        "count": int(pytestconfig.getini("tmp_path_retention_count")),
    }
    Path(os.environ["PROBE_RECORD"]).write_text(json.dumps(record))
    assert os.environ.get("PROBE_FAIL") != "1"
