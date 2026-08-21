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
    datas=[(str(project_root / "version.txt"), "."), *matplotlib_data],
    # fim.gui.app and its screens are only reached through launcher.py's
    # conditional, zero-argv/--graphical branch (design doc
    # `20260819-claude-sonnet-5-graphical-interface.md` §5.1) -- PyInstaller's
    # static import scan should not be relied on to find them unassisted.
    hiddenimports=[
        "matplotlib.backends.backend_agg",
        "matplotlib.backends.backend_tkagg",
        "fim.gui.app",
        "fim.gui.screens.animation_screen",
        "fim.gui.screens.batch_results_screen",
        "fim.gui.screens.input_screen",
        "fim.gui.screens.open_run_screen",
        "fim.gui.screens.progress_screen",
        "fim.gui.screens.results_screen",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # tkinter itself is never excluded: fim.gui.app (§3.2) needs it,
        # and it ships with the Python interpreter rather than as a
        # separate dependency, so un-excluding it costs nothing new to
        # bundle. PyQt/PySide and their Matplotlib backends stay excluded
        # -- this project never uses them (§3.2's toolkit decision).
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib.backends.backend_qt",
        "matplotlib.backends.backend_qt5",
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
# comment was written). Windows keeps the single portable `fim.exe`
# onefile build unchanged -- it never touches `BUNDLE`, so the
# deprecation does not apply there. `CFBundleExecutable` in the `.app`
# points at the identical `fim` binary Terminal/Homebrew invoke
# directly, so a zero-argument Finder double-click reaches
# `fim.launcher.main`'s existing empty-argv branch exactly like
# Windows's double-click does -- there is no `FreeConsole()`-style
# trick needed here (that solves a Windows-only visible-console-window
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
