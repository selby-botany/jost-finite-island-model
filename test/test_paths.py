"""Unit tests for shared project-root, results-directory, and
atomic-publish resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import fim
from fim import paths


def test_project_root_falls_back_to_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed and frozen applications never write inside their package.

    Regression test carried over from `test/cli/test_cli.py` (design doc
    `20260819-claude-sonnet-5-graphical-interface.md`, Milestone G0):
    `fim.paths.project_root` is anchored on the `fim` package's own
    `__init__.py` (`fim.__file__`) rather than the caller's own module
    file, so every caller — the CLI, the GUI, or any future front end —
    resolves the same root through one shared function. This test
    therefore patches `fim.__file__`, not `fim.cli.__file__` as it did
    before the extraction.
    """
    working_directory = tmp_path / "working"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(
        fim,
        "__file__",
        str(tmp_path / "installed" / "site-packages" / "fim" / "__init__.py"),
    )

    assert paths.project_root() == working_directory


def test_project_root_finds_the_real_checkout() -> None:
    """A real checkout resolves to the directory containing pyproject.toml."""
    assert (paths.project_root() / "pyproject.toml").is_file()


def test_results_directory_defaults_to_project_root_slash_results() -> None:
    """`results_directory` appends `results` to the resolved project root."""
    assert paths.results_directory() == paths.project_root() / "results"


def test_results_directory_accepts_a_root_override(tmp_path: Path) -> None:
    """An explicit root bypasses `project_root` entirely."""
    assert paths.results_directory(tmp_path) == tmp_path / "results"


def test_default_output_directory_matches_previous_cli_behavior(
    tmp_path: Path,
) -> None:
    """`fim.paths` reproduces `cli.py`'s pre-extraction directory naming.

    Regression proof for Milestone G0 (design doc §3.7, §6.3): the
    timestamped folder name format (`run-YYYYMMDD-HHMMSS`, UTC) is
    unchanged from the version this logic replaced inside `fim.cli`.
    """

    def clock() -> datetime:
        return datetime(2026, 8, 27, 14, 22, 5, tzinfo=UTC)

    output = paths.default_output_directory(tmp_path, clock=clock)

    assert output == tmp_path / "run-20260827-142205"


def test_default_output_directory_uses_results_directory_by_default() -> None:
    """Omitting `results` falls back to `results_directory()`."""
    output = paths.default_output_directory()
    assert output.parent == paths.results_directory()
    assert output.name.startswith("run-")


def test_atomic_directory_rejects_an_existing_target(tmp_path: Path) -> None:
    """A pre-existing target is refused outright, regardless of its contents.

    Regression proof for Milestone G0 (design doc §3.7, §6.3): the
    relocated `atomic_directory` still requires the final path not to
    exist at all — the same stricter-than-filename-checking contract
    `cli._atomic_directory` established (R7), reproduced here directly
    against `fim.paths` rather than only indirectly through
    `test/cli/test_cli.py`'s `cli.main(["run", ...])` integration tests
    (which keep passing unmodified, since the behavior itself did not
    change).
    """
    target = tmp_path / "output"
    target.mkdir()

    with (
        pytest.raises(FileExistsError, match="already exists"),
        paths.atomic_directory(target),
    ):
        pass


def test_atomic_directory_publishes_via_one_rename_on_success(
    tmp_path: Path,
) -> None:
    """A successful block's contents land at `target` via a single rename."""
    target = tmp_path / "output"

    with paths.atomic_directory(target) as working_directory:
        assert not target.exists()
        assert working_directory.parent == tmp_path
        (working_directory / "marker.txt").write_text("ok", encoding="utf-8")

    assert (target / "marker.txt").read_text(encoding="utf-8") == "ok"


def test_atomic_directory_discards_the_temporary_directory_on_failure(
    tmp_path: Path,
) -> None:
    """A raised exception leaves neither the target nor an orphaned temp dir."""
    target = tmp_path / "output"

    with (
        pytest.raises(RuntimeError, match="simulated failure"),
        paths.atomic_directory(target) as working_directory,
    ):
        (working_directory / "partial.txt").write_text("x", encoding="utf-8")
        raise RuntimeError("simulated failure")

    assert not target.exists()
    assert not list(tmp_path.glob(".output.*"))
