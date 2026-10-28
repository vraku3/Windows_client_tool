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


def _read_entries(path: str, what: str) -> List[dict]:
    """The log's dict entries, oldest first. [] when there is no log yet.

    Never raises: invalid UTF-8, invalid JSON, a top level that is not a
    list ({} or null) and entries that are not objects are all logged at
    WARNING and read as nothing -- a damaged log must not take down the tab
    that shows it (final review, 2026-10-11).
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError) as e:  # JSONDecodeError and UnicodeDecodeError
        logger.warning("%s unreadable: %s", what, e)
        return []
    if not isinstance(entries, list):
        logger.warning("%s is not a list (%s), ignoring it", what, type(entries).__name__)
        return []
    kept = [e for e in entries if isinstance(e, dict)]
    if len(kept) != len(entries):
        logger.warning("%s: ignored %d entries that are not objects", what, len(entries) - len(kept))
    return kept


def record(freed_bytes: int) -> None:
    """Append one completed-clean entry. No-op for freed_bytes <= 0."""
    if freed_bytes <= 0:
        return
    path = _history_path()
    entries = _read_entries(path, "cleanup run log")
    entries.append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "freed_bytes": int(freed_bytes),
    })
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries[-200:], f, indent=2)


def recent(limit: int = 20) -> List[dict]:
    """Most-recent-first, capped at `limit`. [] if nothing has run yet."""
    return list(reversed(_read_entries(_history_path(), "cleanup run log")))[:limit]


def total_freed_all_time() -> int:
    """Sum of every stored entry's freed_bytes.

    "All-time" is honest only up to the 200-entry cap `record()` keeps --
    a machine cleaned more than 200 times will have its oldest runs age
    out, the same trade-off quick_fix_history.py already makes for its
    own log. That is still a strictly better answer than the 0 this
    module reported before every restart.
    """
    total = 0
    for e in _read_entries(_history_path(), "cleanup run log"):
        try:
            total += int(e.get("freed_bytes", 0))
        except (TypeError, ValueError):
            logger.warning("cleanup run log: ignored a freed_bytes of %r", e.get("freed_bytes"))
    return total


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
    _append_entry({
        "at": datetime.now().isoformat(timespec="seconds"),
        "count": len(deleted),
        "bytes": int(sum(size or 0 for _path, size in deleted)),
        "largest": _largest(deleted),
        "refused": list(refused)[:PATHS_PER_ENTRY],
    })


def _append_entry(entry: dict) -> None:
    """Append to the log. Never raises: a log must not fail a clean."""
    try:
        path = _deleted_path()
        from core.timeline import record as timeline_record
        from core.formatting import format_size
        freed = entry.get("bytes", 0)
        timeline_record(os.path.dirname(path), "cleanup", "preset_clean" if entry.get("preset") else "clean_complete", f"Cleanup freed {format_size(freed)}", {"freed_bytes": freed, "files": entry.get("count", 0), "preset": entry.get("preset"), "cancelled": entry.get("cancelled", False)}, "warning" if entry.get("cancelled") or entry.get("refused") or entry.get("denied_count") else "info")
        entries = _read_entries(path, "cleanup deletion log")
        entries.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries[-200:], f, indent=2)
    except Exception as e:
        logger.warning("could not write the cleanup deletion log: %s", e)


def recent_deleted(limit: int = 20) -> List[dict]:
    """Most-recent-first deletion entries. [] if none were ever recorded,
    or if the log cannot be read (logged)."""
    return list(reversed(_read_entries(_deleted_path(), "cleanup deletion log")))[:limit]


#: How many locked / denied paths one entry keeps (the counts are always exact).
LEFTOVER_PATHS = 50


def _largest(removed: Sequence[Tuple[str, int]]) -> List[list]:
    top = sorted(removed, key=lambda d: d[1] or 0, reverse=True)[:PATHS_PER_ENTRY]
    return [[path, int(size or 0)] for path, size in top]


def record_report(report, refused, owners=None, removed=(), categories=None,
                  preset=None, source=None) -> None:
    """Append one clean's MEASURED outcome to the deletion log. Never raises.

    `removed` is [(path, measured_freed_bytes)]; the PATHS_PER_ENTRY largest
    are kept as "largest" so the log still answers "did Cleanup delete X".
    An empty folder removed or a link unlinked is a removal at 0 bytes, and a
    cancelled run is recorded even when it freed nothing.

    The three keyword fields are additive and optional (an entry without them
    is the old shape and every reader copes): `categories` maps a catalog
    category (the same key ``scan_summaries.json`` uses) to the MEASURED bytes
    that category freed in this clean -- never the scanned size; `preset` is
    the preset that chose the items ("light", "thorough", ...); `source` names
    what ran the clean ("Clean Now", "System Junk", "Duplicates"...). Each is
    stored only when given, so a caller that passes nothing writes exactly
    what it wrote before.
    """
    if not (report.files or report.locked or report.denied or report.kept_recent
            or report.cloud_only or refused or removed or report.cancelled):
        return
    protected = getattr(report, "protected", [])
    protected_paths = {path for path, _reason in protected}
    denied = [path for path in report.denied if path not in protected_paths]
    refusals = list(refused) + list(protected)
    _append_entry({
        "at": datetime.now().isoformat(timespec="seconds"),
        "count": report.files,
        "bytes": int(report.freed),
        "largest": _largest(removed),
        "locked": report.locked[:LEFTOVER_PATHS],
        "locked_count": len(report.locked),
        "owners": dict(owners or {}),
        "denied": denied[:LEFTOVER_PATHS],
        "denied_count": len(denied),
        "kept_recent": report.kept_recent,
        "cloud_only": report.cloud_only,
        "refused": [[path, reason] for path, reason in refusals][:PATHS_PER_ENTRY],
        "cancelled": bool(report.cancelled),
        **_optional_fields(categories, preset, source),
    })


def _optional_fields(categories, preset, source) -> dict:
    """The additive fields of an entry, only those that were given. A
    malformed category mapping is logged and dropped, never a failed clean."""
    extra = {}
    if categories is not None:
        try:
            extra["categories"] = {str(k): max(0, int(v or 0))
                                   for k, v in dict(categories).items()}
        except (TypeError, ValueError) as e:
            logger.warning("cleanup deletion log: ignored categories %r: %s", categories, e)
    if preset:
        extra["preset"] = str(preset)
    if source:
        extra["source"] = str(source)
    return extra


def summarise(entry: dict) -> str:
    """One line for the UI. Old-shape entries (no locked_count) say "items"."""
    from core.formatting import format_size
    noun = "files" if "locked_count" in entry else "items"
    parts = [f"Freed {format_size(entry.get('bytes', 0))} ({entry.get('count', 0):,} {noun})."]
    if entry.get("locked_count"):
        owners = entry.get("owners") or {}
        who = ", ".join(sorted(owners, key=lambda k: -owners[k]))
        parts.append(f"{entry['locked_count']:,} in use" + (f" by {who}." if who else "."))
    if entry.get("denied_count"):
        parts.append(f"{entry['denied_count']:,} access denied.")
    if entry.get("kept_recent"):
        parts.append(f"{entry['kept_recent']:,} newer than a day kept.")
    if entry.get("cloud_only"):
        parts.append(f"{entry['cloud_only']:,} cloud-only files left.")
    if entry.get("refused"):
        parts.append(f"{len(entry['refused'])} refused.")
    if entry.get("cancelled"):
        parts.append("Cancelled.")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Readers for the History tab and the history report (Qt-free)
# --------------------------------------------------------------------------

#: The History tab's filters: key -> (menu text, tooltip).
FILTERS = {
    "all": ("All cleans", "Every clean recorded in the deletion log."),
    "cancelled": ("Cancelled", "Cleans that were stopped before they finished."),
    "refused": ("With refusals",
                "Cleans in which Cleanup refused a path (a sign-in store, an Outlook "
                "data file, an exclusion) and left it alone."),
    "in_use": ("With files in use",
               "Cleans that left files behind because a running program held them open."),
}

#: What each kind of path row in one clean means, said in words.
PATH_KINDS = {
    "removed": ("Removed", "Deleted by this clean. The size is what deleting it measurably freed."),
    "locked": ("In use", "A running program held this file open, so it was left in place. "
                         "Close the program and clean again to remove it."),
    "denied": ("Access denied", "Windows refused access to this path, so it was left in place. "
                                "Running elevated may reach it; some paths are protected on purpose."),
    "refused": ("Refused", "Cleanup refused to touch this path on purpose and did not try "
                           "to delete it. The reason says why."),
}


def entry_time(entry: dict):
    """The entry's local time as seconds since the epoch, or None when its
    "at" is missing or unparseable (logged; an old or damaged entry must
    still list, just without a place on the time axis)."""
    at = entry.get("at")
    if not isinstance(at, str) or not at:
        return None
    try:
        return datetime.fromisoformat(at).timestamp()
    except (ValueError, OverflowError, OSError) as e:
        logger.warning("cleanup deletion log: unreadable time %r: %s", at, e)
        return None


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def freed_of(entry: dict, category=None) -> int:
    """Bytes the entry freed; for a category, that category's measured share.
    An entry that recorded no categories counts 0 for every category -- its
    split is not known, and inventing one would be a guess."""
    if category is None:
        return _int(entry.get("bytes", entry.get("freed_bytes", 0)))
    cats = entry.get("categories")
    return _int(cats.get(category)) if isinstance(cats, dict) else 0


def matches(entry: dict, filter_key: str = "all", category=None) -> bool:
    """True when `entry` passes the History tab's filter and category."""
    if category is not None:
        cats = entry.get("categories")
        if not isinstance(cats, dict) or category not in cats:
            return False
    if filter_key == "cancelled":
        return bool(entry.get("cancelled"))
    if filter_key == "refused":
        return bool(entry.get("refused"))
    if filter_key == "in_use":
        return bool(entry.get("locked_count") or entry.get("locked"))
    return True


def category_totals(entries) -> dict:
    """category -> {"bytes", "cleans", "last_at"} over `entries` (per-category
    history). Only entries that recorded categories contribute."""
    out: dict = {}
    for e in entries:
        cats = e.get("categories")
        if not isinstance(cats, dict):
            continue
        at = e.get("at") if isinstance(e.get("at"), str) else ""
        for key, value in cats.items():
            row = out.setdefault(str(key), {"bytes": 0, "cleans": 0, "last_at": ""})
            row["bytes"] += _int(value)
            row["cleans"] += 1
            row["last_at"] = max(row["last_at"], at)
    return out


def categories_seen(entries) -> List[str]:
    """Every category any entry recorded, largest measured total first."""
    totals = category_totals(entries)
    return sorted(totals, key=lambda k: (-totals[k]["bytes"], k))


def trend_points(entries, category=None) -> List[Tuple[float, int]]:
    """(epoch seconds, freed bytes) per clean, oldest first; entries without a
    readable time are left out of the chart (they still list in the table)."""
    points = []
    for e in entries:
        when = entry_time(e)
        if when is not None:
            points.append((when, freed_of(e, category)))
    points.sort(key=lambda p: p[0])
    return points


def scan_points(summaries, category=None) -> List[Tuple[float, int]]:
    """(epoch seconds, reclaimable bytes) per scan summary, oldest first.
    For a category, that category's reclaimable size (absent counts 0)."""
    points = []
    for s in summaries or ():
        if not isinstance(s, dict):
            continue
        when = entry_time(s)
        if when is None:
            continue
        if category is None:
            value = _int(s.get("total_size"))
        else:
            cats = s.get("categories")
            value = _int(cats.get(category)) if isinstance(cats, dict) else 0
        points.append((when, value))
    points.sort(key=lambda p: p[0])
    return points


def path_rows(entry: dict) -> List[Tuple[str, str, int, str]]:
    """(kind, path, size, reason) for every path one entry kept, in the order
    removed, in use, access denied, refused. `size` is -1 when the log never
    measured it (only removals carry a size). Malformed rows are skipped."""
    rows = []
    for item in entry.get("largest") or ():
        if isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
            size = _int(item[1]) if len(item) > 1 else -1
            rows.append(("removed", item[0], size, ""))
    for kind in ("locked", "denied"):
        for path in entry.get(kind) or ():
            if isinstance(path, str):
                rows.append((kind, path, -1, ""))
    for item in entry.get("refused") or ():
        if isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
            reason = str(item[1]) if len(item) > 1 else "refused"
            rows.append(("refused", item[0], -1, reason))
        elif isinstance(item, str):
            rows.append(("refused", item, -1, "refused"))
    return rows


def kind_count(entry: dict, kind: str) -> int:
    """The EXACT number of paths of `kind` in the clean (the stored lists are
    capped; the counts are not). Old entries fall back to the list length."""
    if kind == "locked":
        return _int(entry.get("locked_count", len(entry.get("locked") or ())))
    if kind == "denied":
        return _int(entry.get("denied_count", len(entry.get("denied") or ())))
    if kind == "removed":
        return _int(entry.get("count", len(entry.get("largest") or ())))
    return len(entry.get("refused") or ())


def top_owner(entries):
    """(process, cleans it held files in) for the process that held files in
    the most cleans, or None when no clean recorded a holder."""
    seen: dict = {}
    for e in entries:
        owners = e.get("owners")
        if isinstance(owners, dict):
            for name in owners:
                seen[str(name)] = seen.get(str(name), 0) + 1
    if not seen:
        return None
    name = min(seen, key=lambda k: (-seen[k], k.lower()))
    return name, seen[name]
