"""Virtual result rows, real-value sorting, and inexpensive forensic filters."""
import os
from datetime import datetime
from PyQt6.QtCore import QAbstractTableModel, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QStyle
from core.formatting import format_size
from core.semantic_colors import semantic, chrome
from core.procengine.signatures import VALID, NOT_SIGNED, INVALID

COLUMNS = ('Name', 'Folder', 'Size', 'Created', 'Modified', 'Locked by', 'Likely creator', 'Creator signed')
TIPS = ('File name and type; searching never changes it.', 'Containing folder; hover for the full path.',
        'File size in bytes, sorted numerically.', 'Local creation time, sorted by timestamp.',
        'Local last modification time, sorted by timestamp.',
        'Programs with a matching open File handle in the readable snapshot; gaps are disclosed in details.',
        'Closest process start time within five seconds; a guess, not proof of authorship.',
        'Signature of the top creator candidate; checked only on request for the selected row.')
SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
EXECUTABLES = {'.exe', '.dll', '.msi', '.ps1', '.bat', '.cmd', '.vbs', '.js'}


def matches_filter(row, kind):
    if kind == 'all':
        return True
    if kind == 'locked':
        return bool(row.locking_processes)
    if kind == 'unsigned':
        return bool(row.top_creator_signature and row.top_creator_signature.status == NOT_SIGNED)
    if kind == 'executables':
        return os.path.splitext(row.metadata.path)[1].lower() in EXECUTABLES
    if kind in ('hour', 'day'):
        age = (datetime.now() - row.metadata.created).total_seconds()
        return 0 <= age <= (3600 if kind == 'hour' else 86400)
    return False


def signature_text(sig):
    if sig is None:
        return 'Not checked yet'
    return {VALID: 'Signed', NOT_SIGNED: 'Unsigned', INVALID: 'INVALID'}.get(sig.status, 'Could not check')


class ResultsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.by_path = {}

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(COLUMNS):
            if role == Qt.ItemDataRole.DisplayRole:
                return COLUMNS[section]
            if role == Qt.ItemDataRole.ToolTipRole:
                return TIPS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        m, col = row.metadata, index.column()
        pending = row.lock_inspection.state == 'could_not_check'
        values = (os.path.basename(m.path), os.path.dirname(m.path), m.size, m.created, m.modified,
                  '...' if pending else ', '.join(p.process for p in row.locking_processes) or 'None observed',
                  '...' if not row.creator_ready else row.creator_candidates[0].name if row.creator_candidates else 'No candidate',
                  signature_text(row.top_creator_signature))
        if role == SORT_ROLE:
            value = values[col]
            return value.timestamp() if isinstance(value, datetime) else value.casefold() if isinstance(value, str) else value
        if role == Qt.ItemDataRole.DisplayRole:
            value = values[col]
            return format_size(value) if col == 2 else value.strftime('%Y-%m-%d %H:%M:%S') if isinstance(value, datetime) else str(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            return m.path if col in (0, 1) else row.locking_summary if col == 5 else TIPS[col]
        if role == Qt.ItemDataRole.DecorationRole and col == 0:
            kind = QStyle.StandardPixmap.SP_ComputerIcon if matches_filter(row, 'executables') else QStyle.StandardPixmap.SP_FileIcon
            return QApplication.style().standardIcon(kind)
        if role == Qt.ItemDataRole.ForegroundRole and col >= 5:
            if col == 5 and row.locking_processes:
                return QColor(semantic('warning'))
            sig = row.top_creator_signature
            if col == 7 and sig:
                return QColor(semantic('success' if sig.status == VALID else 'error' if sig.status in (NOT_SIGNED, INVALID) else 'warning'))
            return QColor(chrome('text_muted'))
        return None

    def append(self, rows):
        if rows:
            self.beginInsertRows(self.index(-1, -1), len(self.rows), len(self.rows) + len(rows) - 1)
            self.rows.extend(rows)
            self.by_path.update((row.metadata.path, i) for i, row in enumerate(self.rows[-len(rows):], len(self.rows)-len(rows)))
            self.endInsertRows()

    def replace(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.by_path = {row.metadata.path: i for i, row in enumerate(self.rows)}
        self.endResetModel()

    def update_row(self, row):
        self.update_rows([row])

    def update_rows(self, rows):
        changed = []
        for row in rows:
            index = self.by_path.get(row.metadata.path)
            if index is not None:
                self.rows[index] = row
                changed.append(index)
        if changed:
            self.dataChanged.emit(self.index(min(changed), 0), self.index(max(changed), len(COLUMNS)-1))



class ResultsFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind = 'all'
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)

    def filterAcceptsRow(self, row, parent):
        return matches_filter(self.sourceModel().rows[row], self.kind)

    def set_kind(self, kind):
        self.kind = kind
        self.invalidateFilter()
