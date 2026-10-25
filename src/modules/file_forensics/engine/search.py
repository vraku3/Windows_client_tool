"""Bounded metadata-only search. No file contents, ACLs, signatures or Qt."""
import os
import time
from dataclasses import dataclass
from datetime import datetime

from .analysis import FileAnalysis
from .file_metadata import FileMetadata

RESULT_LIMIT = 5000


@dataclass(frozen=True)
class SearchBatch:
    rows: tuple
    folders: int
    files: int
    matches: int
    skipped: int
    outcome: str = 'searching'


def fast_analysis(path, stat):
    meta = FileMetadata(path, stat.st_size, datetime.fromtimestamp(stat.st_ctime),
                        datetime.fromtimestamp(stat.st_mtime), datetime.fromtimestamp(stat.st_atime),
                        'Not checked yet', bool(getattr(stat, 'st_file_attributes', 0) & 1))
    return FileAnalysis(meta, [], 'Not checked yet', [], None, None)


def search_batches(folder, name_filter='', recurse=True, *, cancelled=lambda: False,
                   limit=RESULT_LIMIT, interval=0.2):
    """Yield first match immediately, then bounded batches, including progress with no matches.

    Never follows directory reparse points (junctions can loop or escape the tree).
    Refusal of the requested root raises; other failures are counted, including links skipped.
    At the limit stop immediately: truncated means the search was bounded, not that an
    extra unreported match was proved. A stopped scan still flushes its buffered rows.
    """
    target = os.path.abspath(os.path.expandvars(folder))
    pending = [target]
    rows = []
    folders = files = matches = skipped = 0
    last = time.monotonic()
    outcome = 'completed'
    while pending:
        if cancelled():
            outcome = 'stopped'
            break
        root = pending.pop()
        try:
            with os.scandir(root) as entries:
                folders += 1
                for entry in entries:
                    if cancelled():
                        outcome = 'stopped'
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if recurse:
                                if getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
                                    skipped += 1
                                else:
                                    pending.append(entry.path)
                        elif entry.is_symlink():
                            skipped += 1
                        else:
                            files += 1
                            if not name_filter or name_filter.casefold() in entry.name.casefold():
                                rows.append(fast_analysis(entry.path, entry.stat(follow_symlinks=False)))
                                matches += 1
                    except OSError:
                        skipped += 1
                    if matches >= limit:
                        outcome = 'truncated'
                        break
                    now = time.monotonic()
                    if (matches == 1 and rows) or now - last >= interval or len(rows) >= 128:
                        yield SearchBatch(tuple(rows), folders, files, matches, skipped)
                        rows.clear()
                        last = now
        except OSError:
            if root == target:
                raise
            skipped += 1
        if outcome != 'completed':
            break
    yield SearchBatch(tuple(rows), folders, files, matches, skipped, outcome)
