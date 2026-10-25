"""Bounded metadata-only search. No file contents, ACLs, signatures or Qt."""
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime

from .analysis import FileAnalysis, LockInspection
from .file_metadata import FileMetadata

RESULT_LIMIT = 5000
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchFilters:
    """Inclusive metadata constraints, evaluated before counting a match."""
    extensions: str = ''
    created_days: int = 0
    modified_days: int = 0
    min_size: int = 0

    def accepts(self, name, stat, now):
        extensions = {'.' + item.strip().lstrip('.').casefold()
                      for item in self.extensions.split(',') if item.strip().lstrip('.')}
        return (not extensions or os.path.splitext(name)[1].casefold() in extensions) and (
            stat.st_size >= self.min_size and
            (not self.created_days or 0 <= now - stat.st_ctime <= self.created_days * 86400) and
            (not self.modified_days or 0 <= now - stat.st_mtime <= self.modified_days * 86400))

    def summary(self):
        return (f'extensions: {self.extensions or "all"}; created within: {self.created_days or "any"} days; '
                f'modified within: {self.modified_days or "any"} days; minimum: {self.min_size} bytes')


@dataclass
class _Failures:
    links: int = 0
    unreadable: int = 0
    items: tuple = ()

    def record(self, path, error):
        self.unreadable += 1
        if len(self.items) < 200:
            self.items += ((path, str(error)),)
        if self.unreadable <= 50:
            logger.warning('Could not read %s: %s', path, error)

    def batch(self, rows, folders, files, matches, outcome='searching'):
        return SearchBatch(tuple(rows), folders, files, matches, self.links + self.unreadable,
                           outcome, self.links, self.unreadable, self.items)

    def finish(self):
        if self.unreadable > 50:
            logger.warning('%s additional unreadable items; individual warnings limited to 50', self.unreadable - 50)


@dataclass(frozen=True)
class SearchBatch:
    rows: tuple
    folders: int
    files: int
    matches: int
    skipped: int
    outcome: str = 'searching'
    skipped_links: int = 0
    unreadable: int = 0
    unreadable_items: tuple = ()


def fast_analysis(path, stat):
    meta = FileMetadata(path, stat.st_size, datetime.fromtimestamp(stat.st_ctime),
                        datetime.fromtimestamp(stat.st_mtime), datetime.fromtimestamp(stat.st_atime),
                        'Not checked yet', bool(getattr(stat, 'st_file_attributes', 0) & 1))
    return FileAnalysis(meta, [], 'Not checked yet', [], None, None,
                        lock_inspection=LockInspection('could_not_check', 'Not checked yet'), creator_ready=False)


def search_batches(folder, name_filter='', recurse=True, *, cancelled=lambda: False,
                   limit=RESULT_LIMIT, interval=0.2, filters=None):
    """Yield first match immediately, then bounded batches, including progress with no matches.

    Never follows directory reparse points (junctions can loop or escape the tree).
    Refusal of the requested root raises; other failures are counted, including links skipped.
    At the limit stop immediately: truncated means the search was bounded, not that an
    extra unreported match was proved. A stopped scan still flushes its buffered rows.
    """
    filters = filters or SearchFilters()
    started = time.time()
    target = os.path.abspath(os.path.expandvars(folder))
    pending = [target]
    rows = []
    folders = files = matches = 0
    failures = _Failures()
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
                                    failures.links += 1
                                else:
                                    pending.append(entry.path)
                        elif entry.is_symlink():
                            failures.links += 1
                        else:
                            files += 1
                            if not name_filter or name_filter.casefold() in entry.name.casefold():
                                stat = entry.stat(follow_symlinks=False)
                                if filters.accepts(entry.name, stat, started):
                                    rows.append(fast_analysis(entry.path, stat))
                                    matches += 1
                    except OSError as error:
                        failures.record(entry.path, error)
                    if matches >= limit:
                        outcome = 'truncated'
                        break
                    now = time.monotonic()
                    if (matches == 1 and rows) or now - last >= interval or len(rows) >= 128:
                        yield failures.batch(rows, folders, files, matches)
                        rows.clear()
                        last = now
        except OSError as error:
            failures.record(root, error)
            if root == target:
                failures.finish()
                raise
        if outcome != 'completed':
            break
    failures.finish()
    yield failures.batch(rows, folders, files, matches, outcome)
