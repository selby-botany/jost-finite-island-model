"""Behavioral tests for repository-local Python tool resolution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIM_WRAPPER = PROJECT_ROOT / "bin" / "fim"
FIM_GUI_WRAPPER = PROJECT_ROOT / "bin" / "fim-gui"


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


def test_bin_fim_invokes_the_launcher_not_cli_directly() -> None:
    """`bin/fim` routes through `fim.launcher`, not `fim.cli` directly.

    Regression test: `bin/fim` predates the launcher dispatch and could
    silently drop the zero-argv GUI dispatch and `--graphical [--detach]`
    for anyone using this wrapper if it ever called `fim.cli` directly
    again -- found live once, in an earlier session:
    `bin/fim --graphical --detach` failed with `fim: error: the
    following arguments are required: command`, `fim.cli`'s own
    argparse error for a flag it has never heard of.
    """
    wrapper_text = FIM_WRAPPER.read_text(encoding="utf-8")

    assert "-m fim.launcher" in wrapper_text
    assert "-m fim.cli" not in wrapper_text


def test_bin_fim_version_works_end_to_end_through_the_launcher() -> None:
    """`bin/fim --version` runs for real through the full launcher dispatch.

    Not `--graphical`/`--detach` directly: a real functional test of
    those would spawn an actual detached GUI process this test would
    then be responsible for cleaning up. `--version` still exercises the
    exact same `bin/fim` -> `python3 -m fim.launcher` -> (no graphical
    flag matched) -> `fim.cli.main` path for real, in an isolated
    environment, with no GUI involved.
    """
    result = subprocess.run(
        [str(FIM_WRAPPER), "--version"],
        cwd=PROJECT_ROOT,
        env=_isolated_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("fim ")


def test_bin_fim_gui_wrapper_exists_and_invokes_the_gui_module() -> None:
    """`bin/fim-gui` exists and provides the same entry point Homebrew does.

    `doc/usage.md`/`README.md` document `fim-gui` as available
    (correctly, for a real `pip install` or the Homebrew formula, both of
    which register the `fim-gui` console-script entry point from
    `pyproject.toml`), but the `bin/`-on-PATH dev-clone workflow
    (`install/README.md`'s "Run from a clone") needs its own wrapper too.
    """
    assert FIM_GUI_WRAPPER.is_file()
    mode = FIM_GUI_WRAPPER.stat().st_mode
    assert mode & 0o111, "bin/fim-gui is not executable"

    wrapper_text = FIM_GUI_WRAPPER.read_text(encoding="utf-8")
    assert "fim.gui.app" in wrapper_text
