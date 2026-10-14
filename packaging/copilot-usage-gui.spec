# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the copilot-usage-gui standalone executable."""

import os
import sys

sys.path.insert(0, os.path.abspath(SPECPATH))

from _spec_common import (  # noqa: E402
    dearpygui_binaries, package_datas, package_version, plotly_datas, src_path,
)

datas = package_datas(SPECPATH) + plotly_datas()
binaries = dearpygui_binaries()

a = Analysis(
    [os.path.join(SPECPATH, "entry_gui.py")],
    pathex=[src_path(SPECPATH)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["copilot_usage_visualizer", "dearpygui", "dearpygui.dearpygui"],
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
    [] if sys.platform == "darwin" else a.binaries,
    [] if sys.platform == "darwin" else a.datas,
    [],
    name="copilot-usage-gui",
    exclude_binaries=sys.platform == "darwin",
    icon=os.path.join(SPECPATH, "assets", "usage-icon.ico") if sys.platform == "win32" else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # GUI なので console=False。標準出力ログが欲しい場合は True に変更する。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Finder / Dock から起動するアプリは依存を束内へ集め、設定は束外へ保存する。
if sys.platform == "darwin":
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="copilot-usage-gui")
    app = BUNDLE(
        coll,
        name="Coding AI Usage Visualizer.app",
        icon=os.path.join(SPECPATH, "assets", "usage-icon.icns"),
        bundle_identifier="io.github.tech-research-4827.CodingAIUsageVisualizer",
        version=package_version(SPECPATH),
        info_plist={"CFBundleDisplayName": "Coding AI Usage Visualizer", "NSHighResolutionCapable": True},
    )
