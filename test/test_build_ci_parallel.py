"""Regression tests for parallel CI test scheduling."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ci_build_uses_xdist_loadgroup_for_parallel_gui_safe_execution() -> None:
    """`--ci` parallelizes tests while assigning GUI tests to one worker."""
    result = subprocess.run(
        [PROJECT_ROOT / "build", "--ci", "--dry-run"],
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "-n auto --dist=loadgroup -m not\\ slow" in result.stdout
