"""Read-only duplicate benchmark: python tools/duplicates_bench.py FOLDER [--cache PATH]."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from modules.cleanup.duplicates.enumerate import ScanOptions
from modules.cleanup.duplicates.finder import find_duplicates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    parser.add_argument("--cache", help="Optional SQLite cache outside the scanned folder")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--min-mb", type=float, default=1)
    args = parser.parse_args()
    options = ScanOptions(roots=[args.folder], min_size=int(args.min_mb * 1024**2),
                          threads=args.threads, use_cache=bool(args.cache), cache_path=args.cache)
    result = find_duplicates(options)
    hashing = sum(t for stage, t in result.timings.items() if stage in ("sample", "full"))
    print(json.dumps({"files": result.listed, "groups": len(result.groups),
                      "hardlink_aliases": result.hardlinks, "skipped": result.skipped,
                      "errors": len(result.errors), "cache_hits": result.cache_hits,
                      "bytes_hashed": result.bytes_hashed,
                      "stage_MB_s": {stage: count / max(.000001, result.timings[stage]) / 1_000_000
                                     for stage, count in result.stage_bytes.items()},
                      "hash_MB_s": result.bytes_hashed / max(.000001, hashing) / 1_000_000,
                      "timings_seconds": result.timings}, indent=2))


if __name__ == "__main__":
    main()
