# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

project_root = Path(SPECPATH).parent
matplotlib_data = collect_data_files("matplotlib")

analysis = Analysis(
    [str(project_root / "src" / "fim" / "cli.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[(str(project_root / "version.txt"), "."), *matplotlib_data],
    hiddenimports=["matplotlib.backends.backend_agg"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
    upx=True,
    console=True,
)
