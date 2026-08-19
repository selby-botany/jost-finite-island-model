"""Static checks on the PyInstaller build specification."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_FILE = PROJECT_ROOT / "packaging" / "fim.spec"


def test_upx_compression_is_disabled() -> None:
    """The Windows executable is never UPX-compressed.

    Regression test for R9: UPX-compressed executables are a well-known
    antivirus/SmartScreen false-positive trigger, and `upx` is an
    undeclared build dependency PyInstaller silently skips compression
    for when absent — so a compressed build was a function of whichever
    runner image happened to build it, not of the tag. `upx=True` must
    never come back without `upx` also becoming a pinned, versioned
    build dependency.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")

    assert "upx=False" in spec
    assert "upx=True" not in spec
