"""Public package metadata for the finite island model simulator."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# The standard library's own documented "library, not application" pattern
# (https://docs.python.org/3/howto/logging.html#configuring-logging-for-a-library):
# every module under this package logs via `logging.getLogger(__name__)`
# alone and never touches handler/level configuration itself, so importing
# `fim` -- from a test, from `fim.gui`, from a future library consumer --
# produces no "no handlers could be found" warning and no output at all
# unless something explicitly calls `fim.logging_setup.configure()`
# (`doc/fim-logging-design.md` §3.1).
logging.getLogger(__name__).addHandler(logging.NullHandler())


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


def _dev_checkout_is_dirty(repo_root: Path) -> bool:
    """Return whether `repo_root`'s working tree has uncommitted changes.

    Best-effort only: any failure (`git` missing, not actually a repo,
    a hung/slow filesystem) is treated as "not dirty" rather than
    raised, since this is purely cosmetic disambiguation text and must
    never be allowed to break `fim`/`fim-gui` startup.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


def _dev_commit_suffix(repo_root: Path) -> str | None:
    """Return a short-commit label for a `git` checkout, or `None`.

    Exists so several `fim-gui` windows launched from source at
    different commits -- exactly the case for comparing in-progress
    `dev` branch work side by side -- can be told apart from the About
    dialog and window title, which otherwise all show the same
    `version.txt` value between releases. Only ever called for the
    source-tree `version.txt` candidate in `_load_version` below, never
    for a PyInstaller bundle or an installed wheel, so an installed
    release build's version string is completely unaffected by this
    function even if `git` happens to be on the machine's `PATH`.

    Deliberately does not raise: a missing `git` binary, a checkout
    without a `.git` directory (e.g. a source tarball), or any other
    `git` failure all just mean "no commit label available," not a
    startup error.
    """
    if not (repo_root / ".git").is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    short_sha = result.stdout.strip()
    if not short_sha:
        return None
    label = f"{short_sha}"
    if _dev_checkout_is_dirty(repo_root):
        label += "-dirty"
    return label


def _load_version() -> str:
    """Return the version from the source tree, bundle, or package metadata.

    As a side effect, populates the module-level `__dev_commit__` when
    (and only when) the version actually came from a source-tree `git`
    checkout -- see `_dev_commit_suffix`'s own docstring.
    """
    global __dev_commit__  # noqa: PLW0603
    source_version = Path(__file__).resolve().parents[2] / "version.txt"
    candidates = [source_version]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        candidates.append(Path(bundle_root) / "version.txt")
    for candidate in candidates:
        if candidate.is_file():
            if candidate == source_version:
                __dev_commit__ = _dev_commit_suffix(candidate.parent)
            return candidate.read_text(encoding="utf-8").strip()
    try:
        return version("fim")
    except PackageNotFoundError:
        return "0+unknown"


__dev_commit__: str | None = None
__version__ = _load_version()

__all__ = ["__dev_commit__", "__version__"]
