"""Regression tests for parallel CI test scheduling."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

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


def test_ci_can_defer_artifact_assembly_without_skipping_any_test() -> None:
    """`--ci --no-package` retains all test layers and documentation gates."""
    complete = subprocess.run(
        [PROJECT_ROOT / "build", "--ci", "--dry-run"],
        capture_output=True,
        check=False,
        text=True,
    )
    deferred = subprocess.run(
        [PROJECT_ROOT / "build", "--ci", "--no-package", "--dry-run"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert complete.returncode == 0, complete.stderr
    assert deferred.returncode == 0, deferred.stderr
    assert "+ python3 -m build\n" in complete.stdout
    checks, _ = complete.stdout.rsplit("+ python3 -m build\n", 1)
    temporary_name = r"fim-(api|tests|help)\.[A-Za-z0-9]+"
    assert re.sub(temporary_name, r"fim-\1.TEMP", deferred.stdout) == re.sub(
        temporary_name, r"fim-\1.TEMP", checks
    )


@pytest.mark.parametrize("flag", ["--no-docs", "--no-lint", "--no-test", "--no-type"])
def test_ci_cannot_skip_validation(flag: str) -> None:
    """Deferring artifacts never permits skipping a CI validation layer."""
    result = subprocess.run(
        [PROJECT_ROOT / "build", "--ci", "--no-package", flag, "--dry-run"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert "--ci" in result.stderr
    assert not result.stdout


def test_artifact_only_build_runs_no_other_layers() -> None:
    """The separate package step assembles artifacts without repeating checks."""
    result = subprocess.run(
        [
            PROJECT_ROOT / "build",
            "--no-lint",
            "--no-type",
            "--no-test",
            "--no-docs",
            "--dry-run",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "+ python3 -m build\n"
