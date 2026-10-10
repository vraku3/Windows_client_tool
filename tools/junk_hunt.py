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

from modules.cleanup.cleanup_scanner.discover import (  # noqa: E402
    catalog_targets, covered, find_uncovered_caches,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-mb", type=float, default=20)
    args = ap.parse_args()
    targets = catalog_targets()
    candidates = find_uncovered_caches(args.min_mb * 2**20, targets=[])
    rows = [(row.size / 2**20, covered(row.path, targets), row.path)
            for row in candidates.candidates]
    print(f"{len(rows)} cache folders >= {args.min_mb} MB; NOT covered by the catalog:")
    for mb, cov, path in rows:
        if not cov:
            print(f"  {mb:9.1f} MB  {path}")
    print("already covered:")
    for mb, cov, path in rows:
        if cov:
            print(f"  {mb:9.1f} MB  {path}")

    print(f"{candidates.unreadable} folders could not be read.")


if __name__ == "__main__":
    main()
