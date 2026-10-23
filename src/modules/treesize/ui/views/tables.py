"""Extensions, Users, Age of Files and Top Files (spec 5.8).

All four are the same shape: a table of aggregate rows over the selected
subtree, with a proportional bar in a percent column. They share one base class
so the columns, sorting and bar behave identically -- four near-identical
widgets that drift apart is exactly how a clone stops feeling like one product.
"""

from modules.treesize.help_widgets import (
    describe_headers, explain_widget,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem,
)

from ...store import aggregates
from ..directory_tree import ProportionBarDelegate
from ..formatting import Unit, format_bytes, format_count
from ..tree_model import BarFractionRole

SortValueRole = int(Qt.ItemDataRole.UserRole) + 10
NodeRole = int(Qt.ItemDataRole.UserRole) + 11


class _SortableItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn() if self.treeWidget() else 0
        mine = self.data(column, SortValueRole)
        theirs = other.data(column, SortValueRole)
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text(column).lower() < other.text(column).lower()


class AggregateTable(QTreeWidget):
    """Label, Files, Size, Allocated, % of Total -- with a bar in the last."""

    COLUMNS = ("Name", "Files", "Size", "Allocated", "% of Total")
    PERCENT_COLUMN = 4
    SIZE_COLUMN = 2
    #: Subclasses set this False where an inherent order beats sorting by size.
    SORT_BY_SIZE = True

    def __init__(self, first_column: str = "Name", parent=None) -> None:
        super().__init__(parent)
        columns = (first_column,) + self.COLUMNS[1:]
        self.setColumnCount(len(columns))
        self.setHeaderLabels(list(columns))
        describe_headers(self)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setItemDelegateForColumn(self.PERCENT_COLUMN,
                                      ProportionBarDelegate(self))
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._unit = Unit.AUTO
        self._decimals = 1
        if self.SORT_BY_SIZE:
            self.setSortingEnabled(True)
            self.sortByColumn(self.SIZE_COLUMN, Qt.SortOrder.DescendingOrder)
        explain_widget(self)

    def set_unit(self, unit: Unit, decimals: int | None = None) -> None:
        self._unit = unit
        if decimals is not None:
            self._decimals = decimals

    def show_rows(self, rows) -> None:
        self.clear()
        total = sum(row.size for row in rows) or 1
        self.setSortingEnabled(False)
        for row in rows:
            percent = row.size * 100.0 / total
            item = _SortableItem([
                row.label,
                format_count(row.count),
                format_bytes(row.size, self._unit, self._decimals),
                format_bytes(row.alloc, self._unit, self._decimals),
                f"{percent:.1f}%",
            ])
            item.setData(1, SortValueRole, row.count)
            item.setData(self.SIZE_COLUMN, SortValueRole, row.size)
            item.setData(3, SortValueRole, row.alloc)
            item.setData(self.PERCENT_COLUMN, SortValueRole, percent)
            item.setData(self.PERCENT_COLUMN, BarFractionRole, percent / 100.0)
            for column in range(1, len(self.COLUMNS)):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.addTopLevelItem(item)
        self.setSortingEnabled(self.SORT_BY_SIZE)


class ExtensionsView(AggregateTable):
    def __init__(self, parent=None) -> None:
        super().__init__("Extension", parent)

    def show_subtree(self, store, node: int, cache) -> None:
        self.show_rows(cache.get(store, node, "ext", aggregates.by_extension) or [])


class FileGroupsView(AggregateTable):
    def __init__(self, parent=None) -> None:
        super().__init__("File group", parent)

    def show_subtree(self, store, node: int, cache) -> None:
        self.show_rows(cache.get(store, node, "group", aggregates.by_file_group) or [])


class UsersView(AggregateTable):
    #: What by_owner() calls a node whose owner was never read.
    UNKNOWN = "(unknown)"
    OFF_NOTICE = ("Owners were not read for this scan — turn on "
                  "“Determine the owner of every file” in Options and "
                  "scan again.")

    def __init__(self, parent=None) -> None:
        super().__init__("Owner", parent)

    def show_subtree(self, store, node: int, cache) -> None:
        rows = cache.get(store, node, "owner", aggregates.by_owner) or []
        self.show_rows(rows)
        if rows and all(row.label == self.UNKNOWN for row in rows):
            # One "(unknown)" bucket holding the whole volume is not an
            # answer to "who is using the space". Say why it is empty and
            # where the switch is, rather than looking broken.
            self.clear()
            notice = QTreeWidgetItem([self.OFF_NOTICE, "", "", "", ""])
            notice.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addTopLevelItem(notice)


class AgeView(AggregateTable):
    """Chronological, never sorted by size: a histogram whose buckets jump
    around by magnitude cannot be read as a distribution."""

    SORT_BY_SIZE = False

    def __init__(self, parent=None) -> None:
        super().__init__("Age", parent)

    def show_subtree(self, store, node: int, cache, now: int | None = None) -> None:
        if now is None:
            from ...scan.filters import filetime_now
            now = filetime_now()
        rows = cache.get(store, node, "age",
                         lambda s, n: aggregates.by_age(s, n, now)) or []
        self.show_rows(rows)


class TopFilesView(QTreeWidget):
    """The largest N files in the subtree, biggest first."""

    node_activated = pyqtSignal(int)
    COLUMNS = ("Name", "Size", "Allocated", "% of Total", "Path")

    def __init__(self, limit: int = 100, parent=None) -> None:
        super().__init__(parent)
        self._limit = limit
        self._unit = Unit.AUTO
        self._decimals = 1
        self.setColumnCount(len(self.COLUMNS))
        self.setHeaderLabels(list(self.COLUMNS))
        describe_headers(self)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setItemDelegateForColumn(3, ProportionBarDelegate(self))
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        explain_widget(self)

    def set_unit(self, unit: Unit, decimals: int | None = None) -> None:
        self._unit = unit
        if decimals is not None:
            self._decimals = decimals

    def show_subtree(self, store, node: int, cache) -> None:
        self.clear()
        if store is None or node < 0:
            return
        top = cache.get(store, node, f"top{self._limit}",
                        lambda s, n: aggregates.top_files(s, n, self._limit)) or []
        largest = top[0][1] if top else 1
        for file_node, size in top:
            percent = size * 100.0 / (largest or 1)
            item = _SortableItem([
                store.name(file_node),
                format_bytes(size, self._unit, self._decimals),
                format_bytes(store.alloc[file_node], self._unit, self._decimals),
                f"{percent:.1f}%",
                store.path(file_node),
            ])
            item.setData(0, NodeRole, file_node)
            item.setData(1, SortValueRole, size)
            item.setData(3, BarFractionRole, percent / 100.0)
            for column in (1, 2, 3):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.addTopLevelItem(item)

    def _on_double_clicked(self, item, _column) -> None:
        node = item.data(0, NodeRole)
        if node is not None:
            self.node_activated.emit(int(node))
