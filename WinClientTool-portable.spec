# -*- mode: python ; coding: utf-8 -*-
# Portable single-file build — one exe with everything embedded (onefile mode)

import os
import sys

project_root = os.path.dirname(os.path.abspath(SPEC))
sys.path.insert(0, project_root)
from pyinstaller_common import (get_main_script, get_datas, HIDDEN_IMPORTS,
                                write_version_info, exclude_ambient_icu)

# Generated from version_info.txt.in + src/_version.py, so the exe's
# Properties dialog cannot drift from the About pane.
VERSION_INFO = write_version_info(project_root)

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
    a.binaries,
    a.datas,
    exclude_binaries=False,  # required for onefile mode
    name='WinClientTool-Portable',
    version=VERSION_INFO,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
