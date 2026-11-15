# -*- mode: python ; coding: utf-8 -*-

import os
import sys

project_root = os.path.dirname(os.path.abspath(SPEC))
sys.path.insert(0, project_root)
from pyinstaller_common import get_main_script, get_datas, HIDDEN_IMPORTS, exclude_ambient_icu

a = Analysis(
    [get_main_script(project_root)],
    pathex=[],
    binaries=[],
    datas=get_datas(project_root),
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
exclude_ambient_icu(a)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WinClientTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WinClientTool',
)
