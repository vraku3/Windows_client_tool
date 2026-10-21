r"""What is inside an item too big to be a single checkbox.

%TEMP% on the machine this was written for is 47.36 GB across 46,825
directories, and the Cleanup tab presented it as one row: "Temp Files —
47.4 GB". You could not see what was in it, could not pick part of it, and
ticking it deleted a directory a running process may be writing to. Opening
it up showed ~100 near-identical `wct_*` folders at 0.44 GB each — this
project's own pytest temp dirs — which is exactly the sort of thing someone
would want to know before agreeing to anything.

Measured lazily, when a row is expanded. The parent's total is already
known from the scan; walking every oversized item's children up front would
repeat the most expensive part of the sweep for rows nobody opens.
"""
from __future__ import annotations

import logging
import os
from typing import List, Tuple

from modules.cleanup.cleanup_scanner._common import ScanItem, get_dir_size
from modules.cleanup.cleanup_scanner.disk_size import on_disk_size

logger = logging.getLogger(__name__)

#: Below this, a directory is small enough that one row says everything
#: useful about it. 1 GB is roughly where "what IS that?" starts.
MIN_BYTES_TO_EXPAND = 1024 ** 3

#: Enough to see where the space went without turning 46,825 directories
#: into 46,825 rows.
DEFAULT_LIMIT = 40


def is_worth_expanding(item: ScanItem) -> bool:
    """True for a directory big enough that its contents are the story."""
    return bool(item.is_dir) and item.size >= MIN_BYTES_TO_EXPAND


def children_by_size(path: str, limit: int = DEFAULT_LIMIT
                     ) -> List[Tuple[str, int]]:
    """`[(path, bytes)]` for the immediate children, biggest first.

    Files and directories both, because a single enormous file inside an
    otherwise ordinary folder is exactly the thing worth surfacing.

    Anything unreadable is skipped rather than raised: this runs against
    live system directories where entries disappear mid-walk, and a
    breakdown that fails outright is worse than a partial one.
    """
    rows: List[Tuple[str, int]] = []
    try:
        with os.scandir(path) as entries:
            children = list(entries)
    except OSError:
        logger.debug("Could not open %s for breakdown", path, exc_info=True)
        return []

    for entry in children:
        try:
            if entry.is_dir(follow_symlinks=False):
                rows.append((entry.path, get_dir_size(entry.path)))
            elif entry.is_file(follow_symlinks=False):
                rows.append((entry.path, on_disk_size(entry.path, entry.stat(follow_symlinks=False))))
        except OSError:
            logger.debug("Skipped %s during breakdown", entry, exc_info=True)

    rows.sort(key=lambda row: row[1], reverse=True)
    return rows[:limit]
