r"""One measurement per tree, shared by every tab that wants it.

Overview, System Junk, Large Items and Quick Cleanup run overlapping
scanner sets, and each of them walks the same directories again. %TEMP% on
this machine is 47.36 GB across 46,825 directories and 53,926 files, a ~3s
walk, and it is measured by scan_temp_files AND scan_user_crash_dumps
(they are the same path) on the Overview, again on System Junk, and again
on every 60-second Quick Cleanup refresh.

The TTL is short on purpose. This is not a persistent index — it exists so
that opening the module and clicking through its tabs does not re-walk the
disk four times in twenty seconds. Anything older than that is measured
again, because the whole point of the pane is a current number.

Two properties this has to hold, and both are tested:

* **Callers get their own ScanItems.** The tabs toggle `selected` on the
  items they are handed, so serving the cached objects themselves would
  let one tab's checkboxes appear ticked in another.
* **A delete invalidates everything.** `delete_items` calls `invalidate()`.
  Serving a pre-delete measurement afterwards reports space as still
  reclaimable when it has already been freed.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from typing import Callable, Dict, Tuple

from modules.cleanup.cleanup_scanner import exclusions
from modules.cleanup.cleanup_scanner._common import ScanResult

logger = logging.getLogger(__name__)

#: Long enough to cover clicking through the tabs, short enough that the
#: number on screen is still about the machine as it is now.
DEFAULT_TTL_SECONDS = 60.0

_lock = threading.Lock()
_entries: Dict[Tuple[str, int], Tuple[float, ScanResult]] = {}


def _copy(result: ScanResult) -> ScanResult:
    """A caller-owned copy: same measurements, independent ScanItems."""
    clone = ScanResult()
    clone.items = [dataclasses.replace(item) for item in result.items]
    clone.total_size = result.total_size
    return clone


def _excluded(result: ScanResult) -> ScanResult:
    """`result` with anything the user has excluded removed.

    Applied to the CALLER'S COPY, never to the entry held in `_entries` --
    the raw measurement stays intact so removing an exclusion later
    reveals the item again immediately, on the very next call, without
    waiting out the TTL or needing an explicit `invalidate()`.
    """
    rules = exclusions.list_exclusions()
    if not rules:
        return result
    kept = exclusions.filter_items(result.items, rules)
    if len(kept) == len(result.items):
        return result
    result.items = kept
    result.total_size = sum(i.size for i in kept)
    return result


def cached_scan(scanner: Callable[..., ScanResult], min_age_days: int = 0,
                ttl_seconds: float = DEFAULT_TTL_SECONDS) -> ScanResult:
    """Run `scanner`, or hand back a recent measurement of the same thing.

    Keyed on the scanner's name and the age filter, because those are what
    change the answer. A scanner that raises is not cached — the next
    caller tries again rather than inheriting a failure.

    Anything the user has excluded (`cleanup_scanner.exclusions`) is
    filtered out of every result this returns, cached or fresh -- this is
    the one function every tab in the module is required to call scanners
    through, so it is also the one place an exclusion is guaranteed to be
    enforced no matter which tab is asking.
    """
    key = (getattr(scanner, "__name__", repr(scanner)), int(min_age_days))
    now = time.monotonic()

    with _lock:
        entry = _entries.get(key)
        if entry is not None and now - entry[0] < ttl_seconds:
            return _excluded(_copy(entry[1]))

    # Deliberately outside the lock: scans are seconds long, and holding it
    # would serialise every tab's worker behind the slowest walk.
    result = scanner(min_age_days=min_age_days)

    with _lock:
        _entries[key] = (time.monotonic(), result)
    return _excluded(_copy(result))


def invalidate() -> None:
    """Forget every measurement. Called after anything is deleted."""
    with _lock:
        count = len(_entries)
        _entries.clear()
    if count:
        logger.debug("Cleanup scan cache invalidated (%d entries)", count)
