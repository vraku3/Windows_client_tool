"""Filtering and sorting for the Installed Apps tab. No Qt.

The list itself comes from `software_inventory.fetch_software()` (the Uninstall
registry keys). Sizes there are display strings ("1.2 GB"), so sorting needs a
number back out of them, and a missing size is `None` -- unknown, never 0.
"""
import re
from datetime import date, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Tuple

_UNIT = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
_SIZE = re.compile(r"^\s*([\d.,]+)\s*([KMGT]?B)\s*$", re.IGNORECASE)
RECENT_DAYS = 30


def size_bytes(text: str) -> Optional[int]:
    m = _SIZE.match(text or "")
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(",", "")) * _UNIT[m.group(2).upper()])
    except (ValueError, KeyError):
        return None


def installed_on(entry) -> Optional[date]:
    text = (getattr(entry, "install_date", "") or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _recent(entry) -> bool:
    when = installed_on(entry)
    return when is not None and when >= date.today() - timedelta(days=RECENT_DAYS)


def _big(entry) -> bool:
    size = size_bytes(getattr(entry, "size_mb", ""))
    return size is not None and size >= 1024 ** 3


FILTERS: Tuple[Tuple[str, str, Callable], ...] = (
    ("all", "All", lambda e: True),
    ("recent", f"Installed <{RECENT_DAYS} days", _recent),
    ("big", "Over 1 GB", _big),
    ("64", "64-bit", lambda e: e.type_ == "64-bit"),
    ("32", "32-bit", lambda e: e.type_ == "32-bit"),
    ("user", "Per-user", lambda e: e.type_ == "User"),
)
_BY_KEY = {k: fn for k, _, fn in FILTERS}


def passes(key: str, entry) -> bool:
    return _BY_KEY.get(key, _BY_KEY["all"])(entry)


def counts(entries: Iterable) -> Dict[str, int]:
    entries = list(entries)
    return {k: sum(1 for e in entries if fn(e)) for k, _, fn in FILTERS}


def matches(entry, needle: str) -> bool:
    needle = needle.strip().lower()
    if not needle:
        return True
    return needle in f"{entry.name} {entry.publisher} {entry.version}".lower()


def visible(entries: Iterable, key: str, needle: str) -> List:
    return [e for e in entries if passes(key, e) and matches(e, needle)]


def total_size(entries: Iterable) -> Tuple[int, int]:
    """(known bytes, how many had no size). The unknown count is shown next to
    the total: a sum that silently skips apps understates what is installed."""
    total, unknown = 0, 0
    for e in entries:
        size = size_bytes(getattr(e, "size_mb", ""))
        if size is None:
            unknown += 1
        else:
            total += size
    return total, unknown
