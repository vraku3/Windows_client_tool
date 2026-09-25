"""Where the space went: the size of each top-level folder on a drive. No Qt.

A quick answer, not TreeSize: it walks with `os.scandir` (which never opens a
file, so OneDrive placeholders stay placeholders), sums logical size, and never
follows a junction or symlink -- following one double-counts, and on C: it
loops. A folder it is refused is counted and reported, never skipped in
silence: a total that quietly leaves out what it could not read is how someone
decides the disk is fine.
"""
import logging
import os
import stat
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FolderScan:
    root: str
    #: (name, bytes) sorted largest first; loose files at the root are one entry.
    entries: List[Tuple[str, int]] = field(default_factory=list)
    files: int = 0
    unreadable: int = 0
    cancelled: bool = False

    @property
    def total(self) -> int:
        return sum(size for _, size in self.entries)


def _is_reparse(entry) -> bool:
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _tree_size(path: str, scan: FolderScan, cancelled: Callable[[], bool]) -> int:
    total = 0
    stack = [path]
    while stack:
        if cancelled():
            return total
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink() or _is_reparse(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            scan.files += 1
                    except OSError as e:
                        scan.unreadable += 1
                        logger.debug("unreadable entry %s: %s", entry.path, e)
        except OSError as e:
            scan.unreadable += 1
            logger.debug("unreadable folder %s: %s", current, e)
    return total


def scan_top_level(root: str, cancelled: Callable[[], bool] = lambda: False,
                   on_progress: Optional[Callable[[int, str], None]] = None) -> FolderScan:
    """Size every immediate child folder of `root`, plus its loose files."""
    scan = FolderScan(root=root)
    loose = 0
    try:
        with os.scandir(root) as it:
            children = list(it)
    except OSError as e:
        scan.unreadable += 1
        logger.warning("cannot list %s: %s", root, e)
        return scan
    folders = []
    for entry in children:
        try:
            if entry.is_symlink() or _is_reparse(entry):
                continue
            if entry.is_dir(follow_symlinks=False):
                folders.append(entry)
            else:
                loose += entry.stat(follow_symlinks=False).st_size
                scan.files += 1
        except OSError:
            scan.unreadable += 1
    for index, entry in enumerate(folders):
        if cancelled():
            scan.cancelled = True
            break
        size = _tree_size(entry.path, scan, cancelled)
        scan.entries.append((entry.name, size))
        if on_progress:
            on_progress(int((index + 1) * 100 / max(1, len(folders))), entry.name)
    if cancelled():
        scan.cancelled = True
    if loose:
        scan.entries.append(("(files in the root)", loose))
    scan.entries.sort(key=lambda e: e[1], reverse=True)
    return scan


def volumes() -> List[dict]:
    """Fixed and removable volumes with usage, for the drive table."""
    import psutil
    rows = []
    for part in psutil.disk_partitions(all=False):
        if not part.fstype or "cdrom" in part.opts:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError as e:
            logger.warning("disk_usage %s: %s", part.mountpoint, e)
            continue
        rows.append({"mount": part.mountpoint, "fs": part.fstype,
                     "total": usage.total, "used": usage.used, "free": usage.free,
                     "percent": usage.percent})
    return rows
