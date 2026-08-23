"""Tests for source, wheel, and bundled package metadata."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import fim


def test_matplotlib_import_has_no_dependency_deprecations() -> None:
    """Supported runtime dependencies import without deprecation warnings."""
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-c",
            "import matplotlib.pyplot",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_version_loader_does_not_read_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated version.txt cannot override installed metadata."""
    source_version = Path(fim.__file__).resolve().parents[2] / "version.txt"
    original_is_file = Path.is_file

    def package_version(distribution: str) -> str:
        """Return fixture metadata for the expected distribution."""
        assert distribution == "fim"
        return "1.0.0"

    (tmp_path / "version.txt").write_text("9.9.9\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: False if path == source_version else original_is_file(path),
    )
    monkeypatch.setattr(fim, "version", package_version)

    assert fim._load_version() == "1.0.0"


def test_version_loader_reads_pyinstaller_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frozen application reads the version file bundled by its spec."""
    source_version = Path(fim.__file__).resolve().parents[2] / "version.txt"
    original_is_file = Path.is_file
    (tmp_path / "version.txt").write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: False if path == source_version else original_is_file(path),
    )

    assert fim._load_version() == "1.2.3"


def test_mplconfigdir_pinned_only_when_frozen_and_darwin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The macOS-only `MPLCONFIGDIR` override fires for exactly one combination.

    Regression test for a real, live-reproduced defect: PyInstaller's
    own `pyi_rth_mplconfig` runtime hook forces a fresh, unique-per-
    process `MPLCONFIGDIR` on every launch, so every `ProcessPoolExecutor`
    batch worker independently re-scanned every system font before it
    could report its first generation -- what a user watching the GUI's
    batch progress bar saw as "it hangs until ~20%, then it's apparent
    it's running." `fim._pin_mplconfigdir_for_macos_app_bundle`'s own
    docstring has the full story, including why the override is safe
    only on macOS (a stable onedir extraction path) and not on the
    Windows/Linux onefile builds (a fresh extraction path every
    launch, the exact staleness scenario PyInstaller's own hook exists
    to prevent).
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("MPLCONFIGDIR", "/pyinstaller/own/fresh/tempdir")

    fim._pin_mplconfigdir_for_macos_app_bundle()

    assert os.environ["MPLCONFIGDIR"] == str(Path.home() / ".matplotlib")


@pytest.mark.parametrize(
    ("platform", "frozen"),
    [
        ("darwin", False),
        ("win32", True),
        ("linux", True),
    ],
)
def test_mplconfigdir_left_alone_off_the_macos_app_bundle_combination(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    frozen: bool,
) -> None:
    """Every other platform/frozen combination leaves `MPLCONFIGDIR` untouched.

    Windows and Linux keep PyInstaller's own per-process value
    (`packaging/fim.spec`'s onefile `else:` branch re-extracts to a new
    temp directory every launch -- persisting a font cache across
    launches there risks "RuntimeError: Could not open facefile"
    against a deleted prior extraction); a non-frozen `darwin` run (a
    source checkout, `pip install`, or a test run like this one) has
    no PyInstaller runtime hook to undo in the first place.
    """
    monkeypatch.setattr(sys, "platform", platform)
    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("MPLCONFIGDIR", "/pyinstaller/own/fresh/tempdir")

    fim._pin_mplconfigdir_for_macos_app_bundle()

    assert os.environ["MPLCONFIGDIR"] == "/pyinstaller/own/fresh/tempdir"


def test_version_loader_reports_unknown_without_any_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source-less uninstalled import has an explicit unknown version."""
    source_version = Path(fim.__file__).resolve().parents[2] / "version.txt"
    original_is_file = Path.is_file

    def missing_distribution(distribution: str) -> str:
        """Raise the importlib error produced for an uninstalled package."""
        raise PackageNotFoundError(distribution)

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: False if path == source_version else original_is_file(path),
    )
    monkeypatch.setattr(fim, "version", missing_distribution)

    assert fim._load_version() == "0+unknown"
