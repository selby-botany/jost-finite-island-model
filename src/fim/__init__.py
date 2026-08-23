"""Public package metadata for the finite island model simulator."""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _pin_mplconfigdir_for_macos_app_bundle() -> None:
    """Undo PyInstaller's per-process `MPLCONFIGDIR` override, on macOS only.

    PyInstaller's own `pyi_rth_mplconfig` runtime hook forces a *fresh,
    unique-per-process* `MPLCONFIGDIR` (a random `mkdtemp`, deleted at
    exit) on every single launch of a frozen build, unconditionally --
    confirmed live by printing `os.environ["MPLCONFIGDIR"]` from inside
    a real `.app` build, before any of this project's own code ever
    ran. That hook's own comment explains why: a *onefile* build
    re-extracts to a brand-new `_MEIxxxxx` temp directory every launch,
    so a font cache persisted across launches could reference a
    bundled font file from a now-deleted prior extraction
    ("RuntimeError: Could not open facefile") -- a real risk this
    project's own Windows/Linux builds still carry (`packaging/
    fim.spec`'s `else:` branch is plain onefile), left untouched here.

    It does not apply to this project's *macOS* build at all:
    `fim.spec`'s `darwin` branch is onedir+`BUNDLE`, whose extraction
    path (`Contents/Frameworks`) is the same file on disk every time
    the same installed `.app` is launched -- there is no "prior
    extraction" for a cached font path to go stale against. Overriding
    the hook's own per-process value (a plain assignment, not
    `setdefault`: the hook already set the key, so `setdefault` alone
    is a no-op -- found exactly this way, when an earlier version of
    this fix had no effect) is therefore safe only on macOS, where it
    turns a real, live-reproduced cost -- a fresh `ProcessPoolExecutor`
    batch's first wave of (default 10, one per CPU) workers each
    independently re-scanning every system font, all at once, at batch
    start, before any of them can report progress -- into a one-time
    cost paid at most once per `~/.matplotlib` lifetime instead of
    once per worker process per run. This is what a user watching the
    GUI's batch progress bar saw as "it hangs until ~20%, then it's
    apparent it's running." Live-verified: the same 10-replicate batch
    that took over a minute before this fix (all ten workers racing
    through their own font scan) completed in under ten seconds after.
    """
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        os.environ["MPLCONFIGDIR"] = str(Path.home() / ".matplotlib")


_pin_mplconfigdir_for_macos_app_bundle()


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
