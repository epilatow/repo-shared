"""Consumer-local test whose marker file proves its body ran."""

from pathlib import Path


def test_local_suite_runs() -> None:
    Path("local-suite-ran").write_text("yes", encoding="utf-8")
