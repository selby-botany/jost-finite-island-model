"""Tests for source, wheel, and bundled package metadata."""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import fim


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
