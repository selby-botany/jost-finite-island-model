"""Guard: committed worked-example reports match a fresh `fim run`."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = (
    "golden-part-vi",
    # Thirty loci run to equilibrium (about 90,000 generations), minutes not
    # seconds, so it stays out of the default suite.
    pytest.param("dear-nolan-low", marks=pytest.mark.slow),
)


@pytest.mark.parametrize("example", EXAMPLES)
def test_example_report_matches_a_fresh_run(example: str, tmp_path: Path) -> None:
    """The documented `report.json` is the run's exact output.

    Adding a statistic or changing the engine changes the report; this
    fails until the example directory is regenerated, so the docs cannot
    silently drift from the code.
    """
    directory = ROOT / "doc" / "examples" / example
    output = tmp_path / example
    subprocess.run(
        [
            sys.executable,
            "-m",
            "fim.launcher",
            "run",
            str(directory / "config.yaml"),
            "--output",
            str(output),
            "--quiet",
        ],
        check=True,
        cwd=ROOT,
    )
    fresh = json.loads((output / "report.json").read_text(encoding="utf-8"))
    committed = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert fresh == committed
