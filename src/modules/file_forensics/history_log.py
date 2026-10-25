"""A local, append-only log of File Forensics findings -- every manual
search result and every live-watch detection. Same shape as
quick_fix_history.py: a capped JSON array under the app data dir.
"""
import json
import os
import tempfile
from threading import RLock
from datetime import datetime
from typing import List

#: Higher than quick_fix_history.py's 200 -- live-watch can generate many
#: entries on a churning folder, and losing recent forensic findings to a
#: tight cap would defeat the point of keeping history at all.
_CAP = 500
_LOCK = RLock()

#: Set by the most recent _load() call (via recent()/search()). `_load()`
#: swallows a corrupted-or-unreadable history file into a plain `[]`, which
#: is indistinguishable from a genuinely empty history to anything that only
#: looks at the return value -- this flag is what lets a caller (the History
#: tab) tell the two apart and surface a real refusal instead of collapsing
#: it into "nothing here", per this app's own "a refusal is never dropped"
#: rule (see CLAUDE.md's tweak_engine/security_dashboard sections).
_last_load_ok = True


def last_load_ok() -> bool:
    return _last_load_ok


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "file_forensics_history.json")


def _load() -> list:
    global _last_load_ok
    path = _history_path()
    if not os.path.exists(path):
        _last_load_ok = True
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _last_load_ok = True
        return data
    except (OSError, json.JSONDecodeError):
        _last_load_ok = False
        return []


def record(entry: dict) -> None:
    with _LOCK:
        entries = _load()
        entries.append({**entry, "at": datetime.now().isoformat(timespec="seconds")})
        path = _history_path()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path),
                                             prefix="forensics-history-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(entries[-_CAP:], stream, indent=2)
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def recent(limit: int = 50) -> List[dict]:
    return list(reversed(_load()))[:limit]


def search(query: str, limit: int = 50) -> List[dict]:
    needle = query.lower()
    matches = [e for e in reversed(_load())
              if any(needle in str(v).lower() for v in e.values())]
    return matches[:limit]
