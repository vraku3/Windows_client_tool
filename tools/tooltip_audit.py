"""Run a construction-only, offscreen explanation coverage report."""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # Must precede any Qt imports.

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ui.tooltip_audit import audit_modules, format_table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, metavar="PATH")
    parser.add_argument("--module", metavar="NAME")
    args = parser.parse_args()
    started = time.perf_counter()
    report = {"modules": [], "errors": []}
    try:
        with tempfile.TemporaryDirectory(prefix="wct-tooltip-") as directory:
            report["modules"] = audit_modules(directory, args.module)
        print(format_table(report["modules"]))
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        print(f"Audit error: {report['errors'][-1]}", file=sys.stderr)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    print(f"Elapsed: {report['elapsed_seconds']:.3f}s")
    if args.json:
        try:
            args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Cannot write JSON: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
