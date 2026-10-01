"""A local, append-only log of Quick Fix action runs -- what ran, when,
and whether it succeeded. Mirrors debloat_history.py's exact shape; a
"View History" dialog reads this directly. Added alongside the
module-consolidation merge (docs/superpowers/specs/
2026-09-15-module-consolidation-design.md) since Quick Fix became the
one "runs things" module in this app without any history of its own.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List

logger = logging.getLogger(__name__)


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "quick_fix_history.json")


def record(action: str, outcome: str) -> dict:
    """Append a run and return the entry just written, so a caller that
    already has the file open (`_FixCard._on_done`/`_on_error`/`cancel`)
    can update its own "Last run" display without a second read of the
    same file it just wrote."""
    path = _history_path()
    entries = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            entries = []
    entry = {"at": datetime.now().isoformat(timespec="seconds"),
             "action": action, "outcome": outcome}
    entries.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries[-200:], f, indent=2)
    return entry


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


def last_by_action(limit: int = 200) -> Dict[str, dict]:
    """The most recent entry for each distinct action title in the last
    `limit` runs, keyed by the title `record()` was called with.

    Added so Quick Fix's card list can show "Last run: <when> - <outcome>"
    on every card without opening the separate History dialog -- before
    this, the run log existed ONLY as that dialog's read-only list, with
    no link back from a card to its own history, so a returning admin
    re-running the same fix a week later had no way to tell (short of
    opening History and reading down a flat, unfiltered list of every
    action) whether they'd already tried it recently and what happened.

    `recent()` is already newest-first, so the first occurrence of a title
    walking that list IS its most recent entry -- one pass, no per-title
    re-scan, which matters because Quick Fix builds a card for every one of
    ~24 catalog actions up front in `create_widget()`."""
    result: Dict[str, dict] = {}
    for entry in recent(limit=limit):
        result.setdefault(entry["action"], entry)
    return result


def recurring_actions(min_count: int = 3, days: int = 14) -> Dict[str, int]:
    """Actions run `min_count` or more times within the last `days` days --
    a signal that whatever the fix addresses keeps coming back rather than
    staying fixed after one run (Flush DNS run five times in two weeks
    points at a real, unresolved DNS/network problem, not a fix that
    legitimately needs repeating). The card for a flagged action shows the
    count instead of staying silent the Nth time someone reaches for the
    same button.

    Scans the full history (`recent(limit=200)` covers everything the file
    can ever hold -- `record()` caps the file itself at 200 entries), not
    just the "View History" dialog's default 20-row window, since a
    recurring pattern can span more runs than that.

    An entry with a malformed or missing timestamp is skipped rather than
    raising or being silently counted as "now" -- `record()` has always
    written `datetime.now().isoformat()`, so a bad value here would only
    come from hand-edited or corrupted history, not normal operation."""
    cutoff = datetime.now() - timedelta(days=days)
    counts: Dict[str, int] = {}
    for entry in recent(limit=200):
        at_raw = entry.get("at")
        action = entry.get("action")
        if not at_raw or not action:
            continue
        try:
            at = datetime.fromisoformat(at_raw)
        except ValueError:
            logger.debug("Skipping history entry with unparseable timestamp %r for action %r",
                         at_raw, action)
            continue
        if at < cutoff:
            continue
        counts[action] = counts.get(action, 0) + 1
    return {action: n for action, n in counts.items() if n >= min_count}
