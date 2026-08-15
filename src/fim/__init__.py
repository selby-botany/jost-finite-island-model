"""Public package metadata for the finite island model simulator."""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _load_version() -> str:
    """Return the version from the source tree, bundle, or package metadata."""
    candidates = [Path(__file__).resolve().parents[2] / "version.txt"]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        candidates.append(Path(bundle_root) / "version.txt")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    try:
        return version("fim")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _load_version()

__all__ = ["__version__"]
