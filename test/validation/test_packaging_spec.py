"""Static checks on the PyInstaller build specification."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_FILE = PROJECT_ROOT / "packaging" / "fim.spec"


def test_upx_compression_is_disabled() -> None:
    """The Windows executable is never UPX-compressed.

    UPX-compressed executables are a well-known antivirus/SmartScreen
    false-positive trigger, and `upx` is an undeclared build dependency
    PyInstaller silently skips compression for when absent — so a
    compressed build was a function of whichever runner image happened
    to build it, not of the tag. `upx=True` must never come back
    without `upx` also becoming a pinned, versioned build dependency.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")

    assert "upx=False" in spec
    assert "upx=True" not in spec


def test_tkinter_and_its_matplotlib_backend_stay_excluded() -> None:
    """`tkinter`/`backend_tkagg` never creep back into a pywebview build.

    This project's GUI is pywebview, never Tk — a regression guard
    against either being un-excluded again, asserting the invariant
    statically rather than starting a real PyInstaller build to find
    out.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")
    excludes = spec.split("excludes=[", 1)[1].split("]", 1)[0]

    assert '"tkinter"' in excludes
    assert '"matplotlib.backends.backend_tkagg"' in excludes


def test_analysis_targets_the_launcher_not_the_bare_cli() -> None:
    """The frozen executable's entry point dispatches to the GUI too.

    `fim.launcher.main` (§5.1), not `fim.cli.main` directly — the bare
    CLI module has no zero-argument/`--graphical` branch of its own, so
    building against it would silently drop the GUI's only packaged
    entry point.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")

    assert '"fim" / "launcher.py"' in spec
    assert '"fim" / "cli.py"' not in spec


def test_the_webui_directory_is_bundled_as_data() -> None:
    """The static frontend ships inside the executable, not left behind.

    `fim.gui.app._webui_directory` resolves `index.html` and its assets
    relative to `sys._MEIPASS` in a frozen build — nothing renders
    without this `datas` entry actually bundling that directory tree.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")

    assert '"fim" / "gui" / "webui"' in spec
    assert '"fim/gui/webui"' in spec


def test_webview_is_a_declared_hidden_import() -> None:
    """`webview` is named explicitly, not left to PyInstaller's own scan.

    `fim.gui.app` (and therefore `webview`) is only ever reached through
    `launcher.py`'s conditional branch — PyInstaller's static import
    scan does not follow that indirection unassisted.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")

    assert '"webview"' in spec


def test_windows_and_linux_build_stays_a_single_onefile_executable() -> None:
    """Non-macOS platforms keep the portable, single-file `fim` build.

    Only macOS needs the onedir-plus-`BUNDLE` structure a `.app` requires
    (§3.2 below) — Windows and Linux never touch `COLLECT`/`BUNDLE` at
    all, so the `else` branch's `EXE()` call still receives
    `analysis.binaries`/`analysis.datas` directly, the onefile shape.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")
    else_branch = spec.split("else:", 1)[1]

    assert "analysis.binaries,\n        analysis.datas," in else_branch
    assert "exclude_binaries=True" not in else_branch


def test_macos_bundle_wraps_a_collected_onedir_build() -> None:
    """The macOS `.app` uses onedir mode, not onefile-plus-`BUNDLE`.

    Design doc 20260821-claude-sonnet-5-macos-linux-packaging.md §3.2:
    combining PyInstaller's single-file (onefile) `EXE()` output with
    `BUNDLE()` is deprecated as of PyInstaller 6.x ("a .app bundle can
    not be a single file... will become an error in v7.0") — confirmed
    against a real local build before this test was written. `COLLECT`
    must sit between the platform-specific `EXE()` (built with
    `exclude_binaries=True`) and `BUNDLE`.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")
    darwin_branch = spec.split('if sys.platform == "darwin":', 1)[1].split("else:", 1)[
        0
    ]

    assert "exclude_binaries=True" in darwin_branch
    assert "COLLECT(" in darwin_branch
    assert "BUNDLE(" in darwin_branch
    # COLLECT must come after EXE and before BUNDLE, not just be present.
    assert darwin_branch.index("EXE(") < darwin_branch.index("COLLECT(")
    assert darwin_branch.index("COLLECT(") < darwin_branch.index("BUNDLE(")


def test_macos_bundle_is_dock_visible_not_background_only() -> None:
    """The `.app` shows a normal Dock icon on a Finder double-click.

    Regression test: PyInstaller's own default `Info.plist` sets
    `LSBackgroundOnly: True` for a `console=True` build (its assumption
    that a console build is a Terminal-only tool) — confirmed by
    inspecting a real local build's `Info.plist` before this override
    was added, which otherwise silently hid the Dock icon despite
    `LSUIElement` already being unset.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")

    assert '"LSBackgroundOnly": False' in spec
    assert '"LSUIElement": False' in spec


def test_macos_bundle_identifier_and_version_are_set() -> None:
    """The `.app`'s bundle identifier and version strings are populated.

    Not PyInstaller's own placeholder defaults — `bundle_identifier` is
    the reverse-DNS app identity macOS uses to distinguish this app from
    any other, and the version strings are read from `version.txt` at
    build time rather than left blank.
    """
    spec = SPEC_FILE.read_text(encoding="utf-8")

    assert 'bundle_identifier="org.selby-botany.fim"' in spec
    assert '"CFBundleShortVersionString": project_version' in spec
    assert '"CFBundleVersion": project_version' in spec
