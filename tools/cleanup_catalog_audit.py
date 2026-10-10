r"""Which catalog paths would sign someone out, or delete data, if cleaned?

    python tools/cleanup_catalog_audit.py [--all]

Reads the Cleanup catalog (no disk access needed) and flags every path whose
LAST component is not a recognised cache / temp / log / crash-dump folder,
or which is, or sits inside, a store that holds a sign-in or user data:
cookies, Local Storage, IndexedDB, a browser "User Data" root, an app's whole
data root, a Store app's whole LocalCache, game installs, configs.

Flags only -- the decision per entry is a person's, recorded in the catalog.
Exits 1 if anything that is still marked "safe" is flagged.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFS = Path(__file__).resolve().parent.parent / "src" / "modules" / "cleanup" / "cleanup_scanner" / "definitions"

#: Leaf names that are pure, self-rebuilding caches, temp or logs.
CACHE_LEAVES = re.compile(
    r"^(cache|cache_data|caches|code cache|gpucache|grshadercache|shadercache|shader ?cache|"
    r"\w*dawn\w*cache|dxcache|glcache|vkcache|nv_cache|d3dscache|webcache|htmlcache|mediacache|media cache|"
    r"crashpad|crashdumps|crash ?reports?|crashes|dumps?|minidumps?|reportarchive|reportqueue|"
    r"logs?|log ?files|diagnostics|traces?|etl|tmp|temp|temporary|temporary files|"
    r"cacheddata|cachedextensions|cachedextensionvsixs|cachedprofilesdata|blob_storage|scriptcache|"
    r"component_crx_cache|extensions_crx_cache|thumbnails?|thumbcache|downloads?|pending|"
    r"http-?cache|npm-cache|pip|uv|\.cache|_cacache|archive-v\d+|llvmcache.*|cache\..*|"
    r"\*\.log|\*\.tmp|\*\.dmp|\*\.etl|.*\.(log|tmp|dmp|etl|old|bak)|installer\.exe|"
    r"optguideondevicemodel|cachestorage)$", re.I)

#: A path is, or reaches into, one of these: deleting signs out or loses data.
SIGN_IN_OR_DATA = re.compile(
    r"(\\|^)(cookies|local storage|indexeddb|session storage|network|login data|web data|"
    r"databases|storage|wallet|accounts?|credentials?|tokens?|config|cfg|saves?|savegames?|"
    r"steamapps|userdata|user data|profiles?|documents|media|photos?|outlook|onenote)(\\|$)", re.I)

#: A Store app's whole LocalCache / LocalState holds its WebView profile (the sign-in).
WHOLE_PACKAGE = re.compile(r"\\packages\\[^\\]+\\(localcache|localstate|roamingstate)$", re.I)


def judge(path: str) -> list:
    reasons = []
    leaf = path.rstrip("\\").split("\\")[-1]
    if not CACHE_LEAVES.match(leaf):
        reasons.append(f"leaf '{leaf}' is not a cache/temp/log folder")
    if SIGN_IN_OR_DATA.search(path) and not CACHE_LEAVES.match(leaf):
        reasons.append("is or contains a sign-in / user-data store")
    if WHOLE_PACKAGE.search(path):
        reasons.append("a Store app's whole LocalCache/LocalState (holds its sign-in)")
    return reasons


def unreviewed() -> list:
    """(file stem, spec, [(path, reasons)]) for every ENABLED entry, at any
    safety level, that still points at something not reviewed as junk.
    Empty is the state the catalog must stay in (tests pin it)."""
    from cleanup_catalog_remediate import REVIEWED
    out = []
    for f in sorted(DEFS.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for spec in data["scanners"]:
            if spec.get("disabled_reason"):
                continue
            hits = [(p, judge(p)) for p in spec["paths"] if p.lower() not in REVIEWED]
            hits = [(p, r) for p, r in hits if r]
            if hits:
                out.append((f.stem, spec, hits))
    return out


def main() -> int:
    argparse.ArgumentParser().parse_args()
    found = unreviewed()
    for stem, spec, hits in found:
        print(f"{stem}/{spec['id']}  [{spec['safety']}]")
        for p, r in hits:
            print(f"    {p}\n        -> {'; '.join(r)}")
    print(f"\n{len(found)} enabled entries still point at something that is not reviewed junk")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
