# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

project_root = Path(SPECPATH).parent
matplotlib_data = collect_data_files("matplotlib")

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
    # UPX-compressed executables are a well-known antivirus/SmartScreen
    # false-positive trigger (README already has to talk Windows users
    # past SmartScreen for the unsigned binary alone) and UPX is an
    # undeclared build dependency that PyInstaller silently skips
    # compression for when absent -- so a compressed build was a
    # function of whether the runner image happened to have `upx`
    # installed, not of the tag. Never enable without pinning `upx` as
    # an explicit, versioned build dependency.
    upx=False,
    console=True,
)
