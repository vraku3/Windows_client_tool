"""Drive the Cleanup deleter on a throwaway tree under %TEMP% -- never a real
cache. Exits 1 if any outcome differs from what the deleter promises."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from modules.cleanup.cleanup_scanner import deleter as d
from modules.cleanup.cleanup_scanner.lock_owners import owners_of
from modules.cleanup.cleanup_scanner.scanners_system import _protected_entry


def main() -> int:
    root = tempfile.mkdtemp(prefix="wct-delete-check-")
    outside = tempfile.mkdtemp(prefix="wct-delete-target-")
    try:
        old = time.time() - 3 * 86400

        def put(rel, size):
            p = os.path.join(root, rel)
            os.makedirs(d.long_path(os.path.dirname(p)), exist_ok=True)
            with open(d.long_path(p), "wb") as f:
                f.write(b"x" * size)
            os.utime(d.long_path(p), (old, old))
            return p

        put("a.tmp", 1000)
        ro = put("ro.tmp", 10)
        os.chmod(ro, 0o444)
        put(os.path.join(*(["d" * 40] * 8), "deep.bin"), 7)
        fresh = os.path.join(root, "fresh.msi")
        with open(fresh, "wb") as f:
            f.write(b"y" * 50)
        locked = put("locked.log", 30)
        cookies = put(os.path.join("sub", "Cookies"), 5)
        with open(os.path.join(outside, "precious.txt"), "w") as f:
            f.write("keep")
        subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(root, "link"), outside],
                       check=True, capture_output=True)
        fd = os.open(locked, os.O_RDONLY)
        try:
            report = d.delete_path(root, True, keep_newer_than_s=86400,
                                   skip=_protected_entry)
            owners = owners_of(report.locked)
        finally:
            os.close(fd)
        print(report, owners)
        checks = {
            "freed 1017 bytes": report.freed == 1017,
            "locked file listed": [os.path.normcase(p) for p in report.locked]
                                  == [os.path.normcase(locked)],
            "locked file attributed to python": any(n.lower().startswith("python") for n in owners),
            "fresh file kept": os.path.exists(fresh) and report.kept_recent == 1,
            "Cookies file survives skip": os.path.exists(cookies),
            "junction target untouched": os.path.exists(os.path.join(outside, "precious.txt")),
            "junction itself unlinked": not os.path.lexists(os.path.join(root, "link")),
            "old files gone": not os.path.exists(os.path.join(root, "a.tmp"))
                              and not os.path.exists(ro),
            "root kept": os.path.isdir(root),
        }
        for name, ok in checks.items():
            print(("PASS " if ok else "FAIL ") + name)
        return 0 if all(checks.values()) else 1
    finally:
        shutil.rmtree(d.long_path(root), ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
