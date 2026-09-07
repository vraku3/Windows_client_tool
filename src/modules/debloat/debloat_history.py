"""A local, append-only log of Debloat apply actions -- what ran, when,
how many of what succeeded. No HTML report (see the audit's P14 note on
scope); a "View History" dialog reads this directly.
"""
import json
import os
from datetime import datetime
from typing import List


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "debloat_history.json")


def record(kind: str, success: int, total: int) -> None:
    path = _history_path()
    entries = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            entries = []
    entries.append({"at": datetime.now().isoformat(timespec="seconds"),
                    "kind": kind, "success": success, "total": total})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries[-200:], f, indent=2)


def recent(limit: int = 20) -> List[dict]:
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return list(reversed(entries))[:limit]
