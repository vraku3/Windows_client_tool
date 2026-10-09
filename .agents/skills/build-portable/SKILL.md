---
name: build-portable
description: Use when building WinClientTool (the portable onefile exe and/or the onedir folder build), or asked to "build the app", "do a build", "make a new portable/exe" — builds via PyInstaller and always deploys the result to the Aplicații folder, overwriting the previous copy.
---

# build-portable

## Overview

Builds WinClientTool via PyInstaller and deploys the output to the folder the
user actually runs the app from day to day:
`C:\Users\iorda\OneDrive\1 Personal\Aplicații\`. That folder is separate from
this repo's own `dist/` (gitignored, local build output only) — a build that
never gets copied over just sits unused while the user keeps running a stale
exe. **The copy step is mandatory, every time, no exceptions** — this was
the exact failure mode that let 7 stale/duplicate WinClientTool exes pile up
in `Aplicații/` and `Aplicații/old/` before (cleaned up 2026-08-14).

## Steps — portable (onefile) build

1. Activate the venv: `.venv\Scripts\activate`
2. Clean rebuild if code changed since the last build (PyInstaller can reuse
   stale cached PKG/PYZ tocs otherwise):
   ```
   rm -f build/WinClientTool-portable/PYZ-00.toc build/WinClientTool-portable/PKG-00.toc
   ```
3. Build: `pyinstaller WinClientTool-portable.spec -y --distpath dist`
4. Sanity-check the output isn't a bootloader stub — `dist/WinClientTool-Portable.exe`
   should be ~50+ MB. A ~3MB result means `a.binaries`/`a.datas` got dropped
   from the EXE constructor in the spec (see AGENTS.md's Important Gotchas).
5. **Deploy it — always**, overwriting whatever's already there:
   ```
   cp "dist/WinClientTool-Portable.exe" "C:/Users/iorda/OneDrive/1 Personal/Aplicații/WinClientTool-Portable.exe"
   ```

## Steps — onedir (folder) build, if also requested

1. Same venv/activate as above.
2. Build: `pyinstaller WinClientTool.spec -y --distpath dist`
3. Sanity-check: `dist/WinClientTool/` should be a full multi-MB tree (the
   `_internal/` folder holding Qt/numpy/etc.), not just a bootloader-sized exe.
4. Deploy — overwrite the whole folder:
   ```
   rm -rf "C:/Users/iorda/OneDrive/1 Personal/Aplicații/WinClientTool"
   cp -r "dist/WinClientTool" "C:/Users/iorda/OneDrive/1 Personal/Aplicații/WinClientTool"
   ```

## Notes

- Don't ask before doing the deploy copy — it's a standing instruction, not a
  per-build decision. Do ask before deleting anything unexpected already
  sitting in `Aplicații/` that isn't a previous WinClientTool build (that
  folder holds unrelated personal apps too).
- If only source-checking (not producing a build the user will run), a plain
  `pyinstaller ... -y --distpath dist` without the deploy step is fine —
  this skill applies when the *point* of the build is to hand the user a
  runnable exe.
