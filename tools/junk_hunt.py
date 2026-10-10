r"""Find cache folders on THIS machine that the Cleanup catalog does not cover.

    python tools/junk_hunt.py [--min-mb 20]

Read-only. Walks the usual app-data roots for folders whose NAME marks a
pure, self-rebuilding cache (Chromium/Electron caches, shader caches,
package-manager download caches, crash dumps, logs), measures each, and
reports the ones no catalog target already covers -- the candidates for new
catalog entries. Anything that holds a sign-in (Cookies, Local Storage,
IndexedDB, Session Storage, Network, tokens) is never a candidate: those
names are not on the list, and a candidate INSIDE one is skipped.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from modules.cleanup.cleanup_scanner import catalog  # noqa: E402

#: Folder names that are pure caches in every app that uses them.
CACHE_NAMES = {
    "cache", "cache_data", "code cache", "gpucache", "dawncache", "dawngraphitecache",
    "dawnwebgpucache", "grshadercache", "shadercache", "shader cache", "dxcache", "glcache",
    "vkcache", "nv_cache", "d3dscache", "crashpad", "crashdumps", "crash reports", "logs",
    "cacheddata", "cachedextensionvsixs", "cachedprofilesdata", "webcache", "htmlcache",
}
#: A candidate under any of these is NOT junk: deleting it signs someone out
#: or loses data.
NEVER_UNDER = {  # noqa
"local storage", "indexeddb", "session storage", "cookies", "network",
               "databases", "storage", r"user datadefaultextensions", "wallet"}

ROOTS = ["%LOCALAPPDATA%", "%APPDATA%", "%PROGRAMDATA%", r"%USERPROFILE%\.cache",
         r"%USERPROFILE%\AppData\LocalLow"]


def size_of(path, cap=400_000):
    total = n = 0
    for root, _d, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            n += 1
            if n > cap:
                return total
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def covered(path, targets):
    p = os.path.normcase(os.path.abspath(path))
    for t in targets:
        t = os.path.normcase(os.path.abspath(t))
        if p == t or p.startswith(t.rstrip("\\") + "\\") or t.startswith(p + "\\"):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-mb", type=float, default=20)
    args = ap.parse_args()
    targets = [t for spec in catalog.load_catalog().values() for t in catalog.targets_of(spec)]
    found = []
    for raw in ROOTS:
        root = os.path.normpath(os.path.expandvars(raw))
        if not os.path.isdir(root):
            continue
        for current, dirs, _files in os.walk(root, onerror=lambda e: None):
            low = current.lower()
            if any("\\" + n + "\\" in low + "\\" for n in NEVER_UNDER):
                dirs[:] = []
                continue
            depth = current[len(root):].count("\\")
            if depth > 7:
                dirs[:] = []
                continue
            for d in list(dirs):
                if d.lower() in CACHE_NAMES:
                    path = os.path.join(current, d)
                    found.append(path)
                    dirs.remove(d)                    # do not double count inside it
    rows = []
    for path in found:
        mb = size_of(path) / 2 ** 20
        if mb >= args.min_mb:
            rows.append((mb, covered(path, targets), path))
    rows.sort(reverse=True)
    print(f"{len(rows)} cache folders >= {args.min_mb} MB; NOT covered by the catalog:")
    for mb, cov, path in rows:
        if not cov:
            print(f"  {mb:9.1f} MB  {path}")
    print("already covered:")
    for mb, cov, path in rows:
        if cov:
            print(f"  {mb:9.1f} MB  {path}")


if __name__ == "__main__":
    main()
