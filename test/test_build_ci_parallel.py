"""Regression tests for parallel CI test scheduling."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ci_build_runs_non_gui_parallel_and_gui_serially() -> None:
    """`--ci` keeps stateful tests out of xdist while parallelizing the rest."""
    result = subprocess.run(
        [PROJECT_ROOT / "build", "--ci", "--dry-run"],
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )

    assert result.returncode == 0, result.stderr
    packaging = "-m packaging\\ and\\ not\\ slow --cov=fim"
    non_gui = "-n auto -m not\\ slow\\ and\\ not\\ packaging\\ and\\ not\\ gui"
    gui = "-m gui\\ and\\ not\\ slow test/gui"

    assert packaging in result.stdout
    assert non_gui in result.stdout
    assert gui in result.stdout
    assert result.stdout.index(packaging) < result.stdout.index(non_gui)
    assert result.stdout.index(non_gui) < result.stdout.index(gui)
    assert "--cov-report= --cov-fail-under=0" in result.stdout
    assert "--cov-append --cov-report=term-missing" in result.stdout
