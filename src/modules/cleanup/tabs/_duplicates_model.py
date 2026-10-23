"""Virtual duplicate tree: one model, no widget/item per file."""
from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal

from modules.cleanup.cleanup_scanner import format_size
from modules.cleanup.duplicates.models import ScanResult, validate_selection

SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
HEADERS = ("Set / file path", "Size", "Folder", "Modified", "Created", "Files",
           "Potential reclaimable", "Hash", "Method / confidence", "Notes")


class DuplicatesModel(QAbstractItemModel):
    selection_changed = pyqtSignal()
    selection_refused = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = ScanResult()
        self._reclaimable = {}
        self.selected: set[str] = set()

    def set_result(self, result):
        self.beginResetModel()
        self.result = result
        self._reclaimable = {id(group): group.reclaimable for group in result.groups}
        self.selected.clear()
        self.endResetModel()
        self.selection_changed.emit()

    def set_selected(self, selected):
        validate_selection(self.result.groups, set(selected))
        self.beginResetModel()
        self.selected = set(selected)
        self.endResetModel()
        self.selection_changed.emit()

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self.result.groups)
        if parent.column() or parent.internalId() & 0xFFFFFFFF:
            return 0
        return len(self.result.groups[parent.internalId() >> 32].files)

    def index(self, row, column, parent=QModelIndex()):
        if row < 0 or column < 0 or column >= len(HEADERS) or row >= self.rowCount(parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, row << 32)
        return self.createIndex(row, column, (parent.internalId() & ~0xFFFFFFFF) | (row + 1))

    def parent(self, child):
        if not child.isValid() or not child.internalId() & 0xFFFFFFFF:
            return QModelIndex()
        group = child.internalId() >> 32
        return self.createIndex(group, 0, group << 32)

    def group_entry(self, index):
        if not index.isValid():
            return None, None
        group = self.result.groups[index.internalId() >> 32]
        file_index = (index.internalId() & 0xFFFFFFFF) - 1
        return group, group.files[file_index] if file_index >= 0 else None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole:
            from modules.cleanup.help_text import HEADERS as HELP
            return HELP.get(HEADERS[section])
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        _, entry = self.group_entry(index)
        if entry is not None and index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags if index.isValid() else Qt.ItemFlag.NoItemFlags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        group, entry = self.group_entry(index)
        if group is None:
            return None
        column = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and column == 0 and entry:
            return Qt.CheckState.Checked if entry.path in self.selected else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ToolTipRole:
            return entry.path if entry else "Keep at least one file. Candidates are verified exactly before removal."
        if role not in (Qt.ItemDataRole.DisplayRole, SORT_ROLE):
            return None
        if entry is None:
            values = (f"Set {group.id + 1}", group.files[0].size, "", 0, 0,
                      len(group.files), self._reclaimable[id(group)], group.digest[:16],
                      f"{group.method} / {group.confidence}", "")
        else:
            values = (entry.path, entry.size, os.path.dirname(entry.path), entry.mtime_ns,
                      entry.ctime_ns, 0, 0, "", group.confidence,
                      f"{entry.nlink} hard links; not reclaimable" if entry.nlink > 1 else "")
        value = values[column]
        if role == SORT_ROLE:
            return value
        if column in (1, 6):
            return format_size(value) if value else ""
        if column in (3, 4):
            return datetime.fromtimestamp(value / 1e9).strftime("%Y-%m-%d %H:%M:%S") if value else ""
        return str(value) if value else ""

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        group, entry = self.group_entry(index)
        if role != Qt.ItemDataRole.CheckStateRole or index.column() != 0 or entry is None:
            return False
        selected = set(self.selected)
        if value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value):
            selected.add(entry.path)
        else:
            selected.discard(entry.path)
        try:
            validate_selection([group], selected.intersection(f.path for f in group.files))
        except ValueError as exc:
            self.selection_refused.emit(str(exc))
            return False
        self.selected = selected
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.selection_changed.emit()
        return True


class DuplicatesProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.setRecursiveFilteringEnabled(True)
        self.setAutoAcceptChildRows(True)
        self.setSortRole(SORT_ROLE)
        self._needle = ""

    def setFilterFixedString(self, text):
        self._needle = text.casefold()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        # Search raw fields, not all ten formatted columns. The base proxy
        # checks ancestors again for every child; keep that path constant time.
        if not self._needle:
            return True
        model = self.sourceModel()
        group = model.result.groups[parent.internalId() >> 32] if parent.isValid() else model.result.groups[row]
        if parent.isValid():
            return self._needle in group.files[row].path.casefold()
        text = f"Set {group.id + 1} {group.digest} {group.method} {group.confidence}"
        return self._needle in text.casefold()

    def lessThan(self, left, right):
        a = self.sourceModel().data(left, SORT_ROLE)
        b = self.sourceModel().data(right, SORT_ROLE)
        if isinstance(a, str) and isinstance(b, str):
            return a.casefold() < b.casefold()
        return a < b
