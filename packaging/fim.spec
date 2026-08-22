# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

project_root = Path(SPECPATH).parent
matplotlib_data = collect_data_files("matplotlib")
project_version = (project_root / "version.txt").read_text(encoding="utf-8").strip()

analysis = Analysis(
    [str(project_root / "src" / "fim" / "launcher.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[
        (str(project_root / "version.txt"), "."),
        # The static frontend `fim.gui.app._webui_directory` resolves
        # relative to `sys._MEIPASS` in a frozen build (that function's
        # own docstring) -- bundled here as a directory tree so every
        # HTML/CSS/JS file under it extracts intact, matching how
        # `version.txt` above already round-trips through the exact same
        # `sys._MEIPASS` mechanism.
        (str(project_root / "src" / "fim" / "gui" / "webui"), "fim/gui/webui"),
        *matplotlib_data,
    ],
    # `fim.gui.app` (and therefore `webview`) is only reached through
    # `launcher.py`'s conditional, zero-argv/`--graphical` branch (design
    # doc `20260821-claude-sonnet-5-graphical-interface.md` §5.1) --
    # PyInstaller's static import scan should not be relied on to find it
    # unassisted. `webview` itself matches `hook-webview.py`'s own
    # package name; PyInstaller auto-discovers that hook (both
    # `pywebview`'s bundled copy and `pyinstaller-hooks-contrib`'s own
    # carry one) without any `hookspath` entry here, and on Windows that
    # hook additionally collects `webview`'s own `lib` subdirectory (the
    # WebView2 loader) automatically.
    hiddenimports=["matplotlib.backends.backend_agg", "webview"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # tkinter and its Matplotlib backend are excluded, not merely
        # unused: this project's GUI is pywebview (§3.2), never Tk, and
        # never has been on this branch's own history -- excluding both
        # keeps a `tkinter` import from creeping back in unnoticed and
        # costs nothing, since neither ships as a project dependency to
        # begin with. Regression-guarded by `test/validation/
        # test_packaging_spec.py`'s own
        # `test_tkinter_and_its_matplotlib_backend_stay_excluded`.
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib.backends.backend_qt",
        "matplotlib.backends.backend_qt5",
        "matplotlib.backends.backend_tkagg",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

# UPX-compressed executables are a well-known antivirus/SmartScreen
# false-positive trigger (README already has to talk Windows users past
# SmartScreen for the unsigned binary alone) and UPX is an undeclared
# build dependency that PyInstaller silently skips compression for when
# absent -- so a compressed build was a function of whether the runner
# image happened to have `upx` installed, not of the tag. Never enable
# without pinning `upx` as an explicit, versioned build dependency.
# Regression test: test/validation/test_packaging_spec.py.

# macOS needs a structurally different build than Windows/Linux: a
# double-click-launchable `.app` (design doc
# 20260821-claude-sonnet-5-macos-linux-packaging.md §3.2) is a
# `BUNDLE()` wrapping the executable, and PyInstaller only supports that
# combined with onedir mode -- combining a single-file (onefile) `EXE()`
# with `BUNDLE()` builds but prints "don't make sense... clashes with
# macOS's security... will become an error in v7.0" (confirmed against a
# real local PyInstaller 6.22.0 build on this exact spec before this
# comment was written). Windows and Linux keep the single portable `fim`
# onefile build unchanged -- neither touches `BUNDLE`, so the
# deprecation does not apply to either. `CFBundleExecutable` in the
# `.app` points at the identical `fim` binary Terminal/Homebrew invoke
# directly, so a zero-argument Finder double-click reaches
# `fim.launcher.main`'s existing empty-argv branch exactly like
# Windows's double-click does -- there is no `FreeConsole()`-style trick
# needed here (that solves a Windows-only visible-console-window
# problem); `BUNDLE` solves Finder's launch mechanism instead.
if sys.platform == "darwin":
    executable = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name="fim",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
    )
    collected = COLLECT(
        executable,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name="fim",
    )
    app = BUNDLE(
        collected,
        name="fim.app",
        # No custom icon exists in this repository yet, matching the
        # Windows build's own unset-icon state today -- PyInstaller's
        # bundled default is used rather than inventing artwork here.
        icon=None,
        bundle_identifier="org.selby-botany.fim",
        info_plist={
            "CFBundleShortVersionString": project_version,
            "CFBundleVersion": project_version,
            "NSHighResolutionCapable": True,
            # A normal, Dock-visible application, not a background-only
            # menu-bar agent. Both keys are needed: PyInstaller's own
            # default `Info.plist` sets `LSBackgroundOnly: True` for a
            # `console=True` build (its assumption that a console build
            # is a Terminal-only tool) -- confirmed by inspecting a real
            # local build's `Info.plist` before adding this override,
            # which would otherwise hide the Dock icon on a Finder
            # double-click despite `LSUIElement` already being unset.
            "LSUIElement": False,
            "LSBackgroundOnly": False,
        },
    )
else:
    executable = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name="fim",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
    )
