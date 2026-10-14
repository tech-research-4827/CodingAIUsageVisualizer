# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the copilot-usage CLI standalone executable."""

import os
import sys

sys.path.insert(0, os.path.abspath(SPECPATH))

from _spec_common import dearpygui_binaries, package_datas, plotly_datas, src_path  # noqa: E402

# cli.py の `gui` subcommand は dearpygui を遅延 import するため CLI bundle にも含める。
datas = package_datas(SPECPATH) + plotly_datas()
binaries = dearpygui_binaries()

a = Analysis(
    [os.path.join(SPECPATH, "entry_cli.py")],
    pathex=[src_path(SPECPATH)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["copilot_usage_visualizer"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="copilot-usage",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
