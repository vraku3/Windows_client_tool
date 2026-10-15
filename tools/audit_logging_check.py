"""Real-machine read of Audit & Logging readiness and the persistence v2 sources.

Read-only. Run unelevated or elevated:
    python tools/audit_logging_check.py
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def main() -> int:
    import pythoncom
    pythoncom.CoInitialize()
    from modules.security_dashboard import log_readiness
    from modules.startup_manager import persistence

    print("log_readiness.read_all():")
    for s in log_readiness.read_all():
        print("  ", s)
    inv = persistence.collect()
    items = inv.items
    print("\npersistence.collect(): %d items" % len(items))
    for src, n in sorted(Counter(getattr(i, "source", "?") for i in items).items()):
        print("   %-28s %d" % (src, n))
    for p in inv.problems:
        print("   problem:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
