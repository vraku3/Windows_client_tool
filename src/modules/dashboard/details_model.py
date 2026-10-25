"""The Details table's model and its sorting proxy.

Thin on purpose: everything about what a column IS lives in
`procengine/columns.py`, which is Qt-free and tested without a display. This
file is the Qt adapter over it.

**It updates in place, it does not reset.** A `beginResetModel` once a second
drops the selection and the scroll position, so a row cannot be clicked while
the table is live -- it deselects under the pointer. That is the lesson
`LogModel.append` already carries in this codebase, and it matters more here
because this table refreshes every second forever.

Row order is therefore the order processes were first seen: new ones are
appended, dead ones removed, and everything else keeps its row. The visible
order comes from the sorting proxy above it.
"""
from typing import Dict, List, Optional

from PyQt6.QtCore import (QAbstractTableModel, QModelIndex,
                          QSortFilterProxyModel, Qt)
from PyQt6.QtGui import QColor

from core.semantic_colors import semantic

from core.procengine.columns import (BY_KEY, DEFAULT_KEYS, RIGHT, Column,
                                 cell_text, cell_tooltip, sort_key)

#: A custom role so the proxy can ask for the row's ProcessInfo without
#: going through Qt's type conversion.
INFO_ROLE = Qt.ItemDataRole.UserRole + 1


class DetailsModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._columns: List[Column] = [BY_KEY[key] for key in DEFAULT_KEYS]
        self._order: List[int] = []           # pids, in row order
        self._by_pid: Dict[int, object] = {}

    # ---- columns --------------------------------------------------------

    def columns(self) -> List[Column]:
        return list(self._columns)

    def set_columns(self, keys) -> None:
        wanted = [BY_KEY[key] for key in keys if key in BY_KEY]
        if not wanted:
            # Never leave the table with no columns: it would look broken
            # and there would be nothing to right-click to fix it.
            return
        self.beginResetModel()
        self._columns = wanted
        self.endResetModel()

    def column_at(self, section: int) -> Optional[Column]:
        if 0 <= section < len(self._columns):
            return self._columns[section]
        return None

    # ---- data -----------------------------------------------------------

    def set_snapshot(self, snapshot) -> None:
        """Take a new reading, keeping every row that survived it.

        Removals first, then additions, then one dataChanged over the rest:
        that ordering keeps the row indices valid for Qt at every step.
        """
        incoming = snapshot.by_pid
        self._apply_removals(set(incoming))
        self._apply_additions(incoming)
        self._by_pid = dict(incoming)

        if self._order:
            top = self.index(0, 0)
            bottom = self.index(len(self._order) - 1, len(self._columns) - 1)
            self.dataChanged.emit(top, bottom)

    def _apply_removals(self, live) -> None:
        # Backwards, so each removal cannot shift the index of the next.
        for row in range(len(self._order) - 1, -1, -1):
            if self._order[row] not in live:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._order[row]
                self.endRemoveRows()

    def _apply_additions(self, incoming) -> None:
        fresh = [pid for pid in incoming if pid not in set(self._order)]
        if not fresh:
            return
        start = len(self._order)
        self.beginInsertRows(QModelIndex(), start, start + len(fresh) - 1)
        self._order.extend(fresh)
        self.endInsertRows()

    def info(self, row: int):
        if 0 <= row < len(self._order):
            return self._by_pid.get(self._order[row])
        return None

    def pid_at(self, row: int) -> Optional[int]:
        if 0 <= row < len(self._order):
            return self._order[row]
        return None

    # ---- QAbstractTableModel -------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._order)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        info = self.info(index.row())
        if info is None:
            return None
        column = self.column_at(index.column())
        if column is None:
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return cell_text(column, info)
        if role == Qt.ItemDataRole.ToolTipRole:
            return cell_tooltip(column, info)
        if role == INFO_ROLE:
            return info
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column.align == RIGHT:
                return int(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._foreground(column, info)
        return None

    def _foreground(self, column: Column, info):
        """Grey a value we could not read, so a dash reads as "refused"
        rather than as a value someone typed."""
        if column.value(info) is None and column.reason is not None:
            return QColor(semantic("warning"))
        return None

    def headerData(self, section, orientation,
                   role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        column = self.column_at(section)
        if column is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return column.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return column.description
        return None


class DetailsProxy(QSortFilterProxyModel):
    """Sorts on the column's VALUE and filters on the text a person types.

    `lessThan` is overridden rather than leaning on a sort role: the keys are
    Python tuples, and handing those through Qt's variant conversion to be
    compared is how a sort quietly orders by something else.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""
        self._category = "all"

    def set_category(self, key):
        self._category = key
        self.invalidateFilter()

    def set_needle(self, text: str) -> None:
        self._needle = (text or "").strip().lower()
        self.invalidateFilter()

    def lessThan(self, left, right) -> bool:
        model = self.sourceModel()
        column = model.column_at(left.column())
        left_info = model.info(left.row())
        right_info = model.info(right.row())
        if column is None or left_info is None or right_info is None:
            return False
        return sort_key(column, left_info) < sort_key(column, right_info)

    def filterAcceptsRow(self, row, parent) -> bool:
        model = self.sourceModel()
        info = model.info(row)
        if info is None:
            return False
        from .glance_support import process_matches
        if not process_matches(info, self._category):
            return False
        # The fields someone actually searches by. Matching every column
        # would let a memory figure match a typed digit.
        haystacks = (info.raw.name, str(info.raw.pid), info.details.user or "",
                     info.details.cmdline or "", info.details.description or "")
        return any(self._needle in field.lower() for field in haystacks)
