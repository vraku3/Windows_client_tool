"""A local, append-only log of Cleanup runs -- when, and how many bytes
were freed. Mirrors quick_fix_history.py's/debloat_history.py's exact
shape (same JSON-under-APPDATA, same 200-entry cap, same "no history file
yet" -> [] contract).

Cleanup is the one module in this app that is meant to be run repeatedly,
week after week, and it had no memory of that at all: `CleanupModule`'s
header tracks only `self._freed_bytes`, a plain instance attribute zeroed
by `create_widget()` on every launch (verified by reading
`cleanup_module.py` -- "Freed this session" always starts at "0 B", even
the day after a 6 GB clean). A returning admin opening the tab a week later
has no way to tell whether last week's cleanup ran at all, or how much it
was worth, without this log. Quick Fix and Debloat solved exactly this
problem for their own run history already; Cleanup is the one obvious gap
left, so this reuses their proven shape rather than inventing a new one.

Only clicks that actually freed something are recorded (`freed_bytes <=
0` is skipped) -- a clean that deleted nothing is not a "run" worth
showing in a history a user reads to answer "did this help".
"""
import json
import os
from datetime import datetime
from typing import List


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "cleanup_history.json")


def record(freed_bytes: int) -> None:
    """Append one completed-clean entry. No-op for freed_bytes <= 0."""
    if freed_bytes <= 0:
        return
    path = _history_path()
    entries = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            entries = []
    entries.append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "freed_bytes": int(freed_bytes),
    })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries[-200:], f, indent=2)


def recent(limit: int = 20) -> List[dict]:
    """Most-recent-first, capped at `limit`. [] if nothing has run yet."""
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return list(reversed(entries))[:limit]


def total_freed_all_time() -> int:
    """Sum of every stored entry's freed_bytes.

    "All-time" is honest only up to the 200-entry cap `record()` keeps --
    a machine cleaned more than 200 times will have its oldest runs age
    out, the same trade-off quick_fix_history.py already makes for its
    own log. That is still a strictly better answer than the 0 this
    module reported before every restart.
    """
    path = _history_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(int(e.get("freed_bytes", 0)) for e in entries)
