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
import logging
import os
from datetime import datetime
from typing import List, Sequence, Tuple

logger = logging.getLogger(__name__)

#: How many paths one deletion entry keeps: the largest ones, which are the
#: ones anyone looking back is asking about. A temp sweep is thousands of
#: files; storing them all would make this log the junk it is meant to audit.
PATHS_PER_ENTRY = 25


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


def _deleted_path() -> str:
    return os.path.join(os.path.dirname(_history_path()), "cleanup_deleted.json")


def record_deleted(deleted: Sequence[Tuple[str, int]],
                   refused: Sequence[str] = ()) -> None:
    r"""Append WHAT one delete_items() call removed, and what it refused.

    The run log above only ever kept bytes, so when C:\ProgramData\Package
    Cache turned out to be missing (2026-10-05) nothing could say whether
    Cleanup had removed it. Written from delete_items() itself, the one
    place every Cleanup deletion passes through, so no tab can forget to.
    Keeps the PATHS_PER_ENTRY largest paths plus the full count and size.
    Never raises: a log that cannot be written must not fail a clean.
    """
    if not deleted and not refused:
        return
    largest = sorted(deleted, key=lambda d: d[1] or 0, reverse=True)[:PATHS_PER_ENTRY]
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "count": len(deleted),
        "bytes": int(sum(size or 0 for _path, size in deleted)),
        "largest": [[path, int(size or 0)] for path, size in largest],
        "refused": list(refused)[:PATHS_PER_ENTRY],
    }
    path = _deleted_path()
    try:
        entries = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    entries = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("cleanup deletion log unreadable, starting over: %s", e)
                entries = []
        entries.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries[-200:], f, indent=2)
    except OSError as e:
        logger.warning("could not write the cleanup deletion log %s: %s", path, e)


def recent_deleted(limit: int = 20) -> List[dict]:
    """Most-recent-first deletion entries. [] if none were ever recorded."""
    path = _deleted_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("cleanup deletion log unreadable: %s", e)
        return []
    return list(reversed(entries))[:limit]
