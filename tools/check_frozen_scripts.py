"""Prove the portable exe carries every Script Library module.

    .venv\\Scripts\\python.exe tools\\check_frozen_scripts.py [dist\\WinClientTool-Portable.exe]

Built-in scripts are Python modules (catalog/*.py) compiled INTO the exe, so
a portable copy on another PC has all of them; they are written to that PC's
%TEMP% only when run. This reads the exe's PYZ archive and fails if any
module under src/modules/scripts_hub is missing from it -- a catalog file
added without a HIDDEN_IMPORTS line would otherwise ship a smaller library
with no error anywhere. Exits 1 on a missing module.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source_modules():
    base = os.path.join(ROOT, "src")
    out = set()
    for folder, _dirs, files in os.walk(os.path.join(base, "modules", "scripts_hub")):
        for name in files:
            if name.endswith(".py"):
                rel = os.path.relpath(os.path.join(folder, name), base)[:-3].replace(os.sep, ".")
                out.add(rel[:-len(".__init__")] if rel.endswith(".__init__") else rel)
    return out


def frozen_modules(exe):
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    archive = CArchiveReader(exe)
    data = archive.extract("PYZ.pyz")
    data = data if isinstance(data, bytes) else data[1]
    path = os.path.join(tempfile.gettempdir(), "wct-pyz-check.pyz")
    with open(path, "wb") as f:
        f.write(data)
    try:
        return set(ZlibArchiveReader(path).toc.keys())
    finally:
        os.remove(path)


def main(argv):
    exe = argv[0] if argv else os.path.join(ROOT, "dist", "WinClientTool-Portable.exe")
    want = source_modules()
    have = frozen_modules(exe)
    missing = sorted(want - have)
    print(f"{len(want)} Script Library modules in src, {len(want) - len(missing)} in {exe}")
    for m in missing:
        print("MISSING", m)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
