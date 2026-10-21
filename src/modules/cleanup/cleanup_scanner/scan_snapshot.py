"""Immutable scan facts, Windows path dedupe and bounded scan trend storage.

``per_tab`` is {tab_key: {scanner_callable_or_id: ScanResult}}. Scanner callables
carry catalog metadata in .spec; bare ids use catalog metadata when available.
Rows retain every tab membership; all global totals use unique_rows. No new scan
or filesystem metadata lookup is performed.
"""
from __future__ import annotations

import json
import logging
import ntpath
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from threading import RLock

from .knowledge import effective_safety, entry_for, id_for_scanner

logger = logging.getLogger(__name__)
_LOCK = RLock()
_LEVELS = {'safe': 0, 'caution': 1, 'danger': 2}


def _path_key(path):
    return ntpath.normcase(ntpath.normpath(str(path)))


@dataclass(frozen=True)
class SnapshotRow:
    id: str
    label: str
    tab: str
    category: str
    safety: str
    effective_safety: str
    path: str
    size: int
    selected: bool
    is_dir: bool = True
    keep_root: bool = True
    keep_newer_than_hours: int = 0
    description: str = ''


@dataclass(frozen=True)
class ScanSnapshot:
    rows: tuple[SnapshotRow, ...] = ()
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec='seconds'))
    preset: str = ''
    elevated: bool | None = None
    not_offered: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'rows', tuple(self.rows))
        object.__setattr__(self, 'not_offered', tuple(self.not_offered))

    @property
    def unique_rows(self) -> tuple[SnapshotRow, ...]:
        unique = {}
        for row in self.rows:
            key = _path_key(row.path)
            if key not in unique:
                unique[key] = row
                continue
            old = unique[key]
            # Scans may finish at different times: use the larger observation,
            # stricter safety, and require both copies to have been selected.
            unique[key] = replace(old, size=max(old.size, row.size),
                                  safety=max((old.safety, row.safety), key=lambda s: _LEVELS.get(s, 2)),
                                  effective_safety=max((old.effective_safety, row.effective_safety), key=lambda s: _LEVELS.get(s, 2)),
                                  selected=old.selected and row.selected)
        directories = {key for key, row in unique.items() if row.is_dir}
        kept = []
        for key, row in unique.items():
            parent = ntpath.dirname(key)
            while parent and parent != key:
                if parent in directories:
                    break
                key, parent = parent, ntpath.dirname(parent)
            else:
                kept.append(row)
        return tuple(kept)

    @property
    def total_size(self):
        return sum(row.size for row in self.unique_rows)

    @property
    def safe_size(self):
        return sum(row.size for row in self.unique_rows if row.effective_safety == 'safe')

    @property
    def review_size(self):
        return self.total_size - self.safe_size

    @property
    def selected_size(self):
        return sum(row.size for row in self.unique_rows if row.selected)

    def tabs_for(self, path):
        return tuple(dict.fromkeys(row.tab for row in self.rows if _path_key(row.path) == _path_key(path)))

    @classmethod
    def from_results(cls, per_tab, **metadata):
        return from_results(per_tab, **metadata)

    def append_summary(self, *, path=None):
        return append_summary(self, path=path)


def from_results(per_tab, **metadata) -> ScanSnapshot:
    from .catalog import load_catalog
    catalog = load_catalog()
    rows = []
    for tab, results in per_tab.items():
        for scanner, result in results.items():
            id_ = scanner if isinstance(scanner, str) else id_for_scanner(scanner)
            spec = getattr(scanner, 'spec', None) or catalog.get(id_)
            label = getattr(spec, 'label', id_.replace('_', ' '))
            category = getattr(spec, 'category', str(tab))
            for item in result.items:
                safety = effective_safety(getattr(spec, 'safety', item.safety), None)
                safety = max((safety, item.safety), key=lambda s: _LEVELS.get(s, 2))
                rows.append(SnapshotRow(id_, label, str(tab), category, safety,
                                        effective_safety(safety, entry_for(id_)), str(item.path),
                                        max(0, int(item.size)), bool(item.selected),
                                        item.is_dir, item.keep_root, item.keep_newer_than_hours,
                                        getattr(spec, 'description', '')))
    return ScanSnapshot(rows=tuple(rows), **metadata)


def _summary_path():
    return Path(os.environ.get('APPDATA') or Path.home()) / 'WindowsTweaker' / 'cleanup' / 'scan_summaries.json'


def read_summaries(path=None) -> list[dict]:
    """Oldest first. Quarantine malformed files without interrupting the UI."""
    path = Path(path) if path is not None else _summary_path()
    with _LOCK:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(payload, list) or any(not isinstance(e, dict) for e in payload):
                raise ValueError('expected a list of summary objects')
            return payload[-200:]
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logger.warning('Cannot read cleanup scan summaries %s: %s', path, exc)
            if path.is_file():
                try:
                    target = path.with_suffix(path.suffix + '.corrupt')
                    if target.exists():
                        target = path.with_name(path.name + '.' + datetime.now().strftime('%Y%m%d%H%M%S%f') + '.corrupt')
                    path.rename(target)
                except OSError as rename_exc:
                    logger.warning('Cannot quarantine cleanup summaries: %s', rename_exc)
            return []


def append_summary(snapshot: ScanSnapshot, *, path=None) -> bool:
    """Append aggregates only (no paths), atomically; storage errors never escape."""
    path = Path(path) if path is not None else _summary_path()
    temporary = None
    with _LOCK:
        try:
            entries = read_summaries(path)
            categories = defaultdict(int)
            for row in snapshot.unique_rows:
                categories[row.category] += row.size
            entries.append({'at': snapshot.at, 'total_size': snapshot.total_size,
                            'safe_size': snapshot.safe_size, 'review_size': snapshot.review_size,
                            'selected_size': snapshot.selected_size, 'locations': len(snapshot.unique_rows),
                            'categories': dict(categories), 'preset': snapshot.preset})
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                             dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(entries[-200:], stream, indent=2, ensure_ascii=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return True
        except (OSError, ValueError, TypeError) as exc:
            logger.warning('Cannot write cleanup scan summary: %s', exc)
            return False
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError as exc:
                    logger.warning('Cannot remove summary temporary file: %s', exc)
