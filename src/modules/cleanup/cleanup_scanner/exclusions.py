"""User-set path exclusions for the Cleanup module's scanners.

A senior admin running 460+ catalog scanners against a real machine will
eventually hit one that is technically correct today but wrong for THIS
machine -- a custom cache directory still in active use, or a "safe" item
that turns out to be load-bearing for one particular workflow. Before
this, the only way to stop a specific path from being offered for
deletion again was editing the catalog's JSON and rebuilding the app.
This gives Cleanup the same kind of user override `core/blocklist.py`
already gives the Updates module's App Updates tab (`is_blocked`), but
for scan results instead of package names.

Exclusion is enforced inside `scan_cache.cached_scan` -- the one choke
point every tab in this module is required to go through (CLAUDE.md's
own rule: "`scan_cache.cached_scan(fn, min_age_days)`, not
`fn(min_age_days=...)`, from any tab"). An excluded path is filtered out
of a ScanResult there, before it is handed to any caller, so it can
never be displayed, selected or deleted through any of the eight tabs --
there is no second code path that has to be kept in sync, and no tab can
accidentally bypass it by calling a scanner directly.

Matching is prefix-based on the normalized path: excluding a directory
excludes everything found under it, the same nesting rule
`_common.dedupe_items` already applies.
"""
import json
import logging
import os
from pathlib import Path
from typing import List, Sequence

logger = logging.getLogger(__name__)


def _config_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = Path(base) / "WindowsTweaker"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "cleanup_exclusions.json"


def _normalize(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def list_exclusions() -> List[str]:
    """Every excluded path, in the form the user added it.

    [] both when nothing has been excluded yet and when the file exists
    but could not be parsed -- a corrupt exclusions file must not be
    read as "everything is excluded" (that would silently hide every
    scan result), and this is a display/deletion FILTER, not the only
    record of the machine's state, so failing open is the safer of the
    two wrong answers. The read failure is still logged, never swallowed.
    """
    path = _config_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.warning("cleanup exclusions file could not be read; "
                        "treating it as empty", exc_info=True)
        return []
    if not isinstance(data, list):
        logger.warning("cleanup exclusions file did not contain a list; "
                        "treating it as empty")
        return []
    return [str(p) for p in data]


def add_exclusion(path: str) -> bool:
    """Add `path` to the exclusion list.

    Returns False if an equivalent path (same after normalization -- a
    trailing slash or a different case does not count as new) is
    already on the list.
    """
    current = list_exclusions()
    norm = _normalize(path)
    if any(_normalize(p) == norm for p in current):
        return False
    current.append(path)
    _save(current)
    return True


def remove_exclusion(path: str) -> bool:
    """Remove `path` (normalized match). Returns False if it was not there."""
    current = list_exclusions()
    norm = _normalize(path)
    kept = [p for p in current if _normalize(p) != norm]
    if len(kept) == len(current):
        return False
    _save(kept)
    return True


def _save(paths: List[str]) -> None:
    path = _config_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(paths, f, indent=2)
    except OSError:
        logger.error("cleanup exclusions could not be saved; the change "
                      "is lost", exc_info=True)


def is_excluded(item_path: str, exclusions: Sequence[str]) -> bool:
    """True if `item_path` IS one of `exclusions`, or is nested inside one.

    Prefix matching on normalized paths -- excluding a folder excludes
    everything found underneath it, not just that exact path.
    """
    norm_item = _normalize(item_path)
    for excluded in exclusions:
        norm_excluded = _normalize(excluded)
        if norm_item == norm_excluded or norm_item.startswith(norm_excluded + os.sep):
            return True
    return False


def filter_items(items: list, exclusions: List[str] = None) -> list:
    """`items` (anything with a `.path`) with excluded ones removed.

    Reads the exclusion file itself when `exclusions` is omitted, so a
    caller with no list already in hand still gets the current rules
    applied without having to know this module's storage.
    """
    if exclusions is None:
        exclusions = list_exclusions()
    if not exclusions:
        return items
    return [i for i in items if not is_excluded(i.path, exclusions)]
