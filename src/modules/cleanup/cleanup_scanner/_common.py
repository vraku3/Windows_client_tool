"""Shared types and helpers for the cleanup scanner package."""
import logging
import os
import time
from core.formatting import format_size
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ScanItem:
    path: str
    size: int        # bytes
    is_dir: bool
    selected: bool = True
    safety: str = "safe"   # "safe" | "caution" | "danger"
    #: A directory item's CONTENTS are deleted and the folder itself kept
    #: (Temp, cache roots). False only where the folder itself is the junk:
    #: a superseded version folder, an empty folder, an orphaned profile.
    keep_root: bool = True
    #: Files modified more recently than this are left alone -- an installer
    #: extracting into %TEMP% reads its files back minutes later.
    keep_newer_than_hours: int = 0

@dataclass
class ScanResult:
    items: List[ScanItem] = field(default_factory=list)
    total_size: int = 0

    def selected_size(self) -> int:
        return sum(i.size for i in self.items if i.selected)

def get_dir_size(path: str) -> int:
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    logger.debug("Ignored OSError", exc_info=True)
    except OSError:
        logger.debug("Ignored OSError", exc_info=True)
    return total

def _make_item(path: str, safety: str = "safe", min_age_days: int = 0) -> Optional[ScanItem]:
    """Return ScanItem for path if it exists, else None. Respects min_age_days."""
    if not os.path.exists(path):
        return None
    if min_age_days > 0:
        try:
            mtime = os.path.getmtime(path)
            age_seconds = time.time() - mtime
            if age_seconds < min_age_days * 86400:
                return None
        except OSError:
            return None
    is_dir = os.path.isdir(path)
    size = get_dir_size(path) if is_dir else os.path.getsize(path)
    return ScanItem(path=path, size=size, is_dir=is_dir, safety=safety)

def _make_item_with_age(path: str, safety: str, min_age_days: int) -> Optional[ScanItem]:
    """Return ScanItem for a file only if it meets the age threshold. Direct file helper."""
    try:
        if min_age_days > 0:
            mtime = os.path.getmtime(path)
            if (time.time() - mtime) < min_age_days * 86400:
                return None
        size = os.path.getsize(path)
        return ScanItem(path=path, size=size, is_dir=False, safety=safety)
    except OSError:
        return None

__all__ = ['ScanItem', 'ScanResult', 'get_dir_size', 'format_size',
           '_make_item', '_make_item_with_age', 'dedupe_items',
           'total_of', 'logger']


def dedupe_items(items: List[ScanItem]) -> List[ScanItem]:
    r"""Drop items already covered by another item in the list.

    Two scanners can legitimately point at the same directory — `%TEMP%`
    and `%LOCALAPPDATA%\Temp` are the SAME path on Windows, so
    `scan_temp_files` and `scan_user_crash_dumps` both measured 39.4 GB and
    a naive sum reported 78.8 GB of "junk" for 39.4 GB of files. It also
    means Clean tries to delete the same tree twice.

    Two shapes are removed:

    * the same path twice, however each scanner spelled it, and
    * a path NESTED inside a directory already in the list — `%TEMP%\*.dmp`
      is counted by its parent `%TEMP%`, so counting it again inflates the
      total by the size of the dumps.

    The first occurrence wins, so the caller controls precedence by
    ordering. Sizes are not recomputed: each surviving item keeps the size
    its own scanner measured.
    """
    seen: dict = {}
    kept: List[ScanItem] = []
    for item in items:
        key = os.path.normcase(os.path.normpath(item.path))
        if key in seen:
            continue
        seen[key] = True
        kept.append(item)

    # Second pass for nesting, against the directories that survived.
    directories = sorted(
        (os.path.normcase(os.path.normpath(i.path)) for i in kept if i.is_dir),
        key=len)
    if not directories:
        return kept

    final: List[ScanItem] = []
    for item in kept:
        key = os.path.normcase(os.path.normpath(item.path))
        if any(key.startswith(parent + os.sep) for parent in directories):
            continue
        final.append(item)
    return final


def total_of(items: List[ScanItem]) -> int:
    """Bytes these items really represent, counting nothing twice."""
    return sum(i.size for i in dedupe_items(items))
