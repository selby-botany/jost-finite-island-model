"""Guard: committed worked-example reports match a fresh `fim run`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = (
    # A single locus needs the noise-adequacy gate's own evidence window to
    # grow to around 130,000 generations before its own mean is precise
    # enough (`20260927-claude-sonnet-5-noise-aware-convergence-design.md`,
    # `selby/restricted`) -- over two minutes, so both examples are now out
    # of the default suite, not only the thirty-loci one below.
    pytest.param("golden-part-vi", marks=pytest.mark.slow),
    # Thirty loci run to equilibrium (about 275,000 generations), minutes not
    # seconds, so it stays out of the default suite.
    pytest.param("dear-nolan-low", marks=pytest.mark.slow),
)


def test_dear_nolan_high_configuration_matches_its_derivation() -> None:
    """`dear-nolan-high/config.yaml` is exactly what `reproduce.py` derives.

    The example's `p_0` is the validation suite's near-equilibrium start
    (`_dn2_equilibrium_start`), written into the file by the script; a
    change to that derivation fails here until the file is rewritten.
    """
    script = ROOT / "doc" / "examples" / "dear-nolan-high" / "reproduce.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


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
        # The command records each run in a Study index under the results
        # directory; keep that out of the developer's real `results/`.
        env={**os.environ, "FIM_RESULTS_DIRECTORY": str(tmp_path / "results")},
    )
    fresh = json.loads((output / "report.json").read_text(encoding="utf-8"))
    committed = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert fresh == committed
