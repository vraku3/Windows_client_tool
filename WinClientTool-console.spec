# Console debug build — for diagnosing startup crashes
# Run: pyinstaller WinClientTool-console.spec -y --distpath "C:/Users/iorda/OneDrive/1 Personal/Aplicații"
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
    a.binaries,
    a.datas,
    exclude_binaries=False,
    name='WinClientTool-Console',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # <-- console window so we can see print() output
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
