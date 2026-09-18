"""A local, append-only log of File Forensics findings -- every manual
search result and every live-watch detection. Same shape as
quick_fix_history.py: a capped JSON array under the app data dir.
"""
import json
import os
from datetime import datetime
from typing import List

#: Higher than quick_fix_history.py's 200 -- live-watch can generate many
#: entries on a churning folder, and losing recent forensic findings to a
#: tight cap would defeat the point of keeping history at all.
_CAP = 500


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "file_forensics_history.json")


def _load() -> list:
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def record(entry: dict) -> None:
    entries = _load()
    entries.append({**entry, "at": datetime.now().isoformat(timespec="seconds")})
    with open(_history_path(), "w", encoding="utf-8") as f:
        json.dump(entries[-_CAP:], f, indent=2)


def recent(limit: int = 50) -> List[dict]:
    return list(reversed(_load()))[:limit]


def search(query: str, limit: int = 50) -> List[dict]:
    needle = query.lower()
    matches = [e for e in reversed(_load())
              if any(needle in str(v).lower() for v in e.values())]
    return matches[:limit]
