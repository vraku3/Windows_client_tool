"""A local, append-only log of Quick Fix action runs -- what ran, when,
and whether it succeeded. Mirrors debloat_history.py's exact shape; a
"View History" dialog reads this directly. Added alongside the
module-consolidation merge (docs/superpowers/specs/
2026-09-15-module-consolidation-design.md) since Quick Fix became the
one "runs things" module in this app without any history of its own.
"""
import json
import os
from datetime import datetime
from typing import List


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "quick_fix_history.json")


def record(action: str, outcome: str) -> None:
    path = _history_path()
    entries = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            entries = []
    entries.append({"at": datetime.now().isoformat(timespec="seconds"),
                    "action": action, "outcome": outcome})
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
