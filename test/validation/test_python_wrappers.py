"""Behavioral tests for repository-local Python tool resolution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _isolated_environment() -> dict[str, str]:
    """Return an environment without an activated Python virtual environment."""
    return {
        **os.environ,
        "FIM_PYTHON": sys.executable,
        "PATH": os.defpath,
        "PYTHON": "python3",
    }


def test_ruff_wrapper_uses_selected_project_python() -> None:
    """The Ruff command works without its interpreter directory on PATH."""
    result = subprocess.run(
        [str(PROJECT_ROOT / "bin" / "ruff"), "--version"],
        cwd=PROJECT_ROOT,
        env=_isolated_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ruff ")


def test_build_lint_uses_repository_python_wrappers() -> None:
    """Build linting works without activating the development environment."""
    result = subprocess.run(
        [
            str(PROJECT_ROOT / "build"),
            "--no-type",
            "--no-test",
            "--no-docs",
            "--no-package",
        ],
        cwd=PROJECT_ROOT,
        env=_isolated_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "All checks passed!" in result.stdout
