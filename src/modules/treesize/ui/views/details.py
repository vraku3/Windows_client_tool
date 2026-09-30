"""Details view (spec 5.8) — the table in the product screenshots.

Columns: Name, Size, Allocated, Files, Folders, % of Parent (with an inline
proportional bar), Last Modified, Last Accessed, Owner.

Shows the children of the selected node, not the whole tree: it is the
right-hand companion to the directory tree, and the tree is what does depth.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem,
)

from core.semantic_colors import chrome, semantic

from ...scan.filters import filetime_now
from ...store.node_store import (
    ADS, COMPRESSED, DIR, EXCLUDED, HARDLINK_DUP, HIDDEN, REPARSE, SPARSE,
)
from ..directory_tree import ProportionBarDelegate
from ..formatting import (
    Unit, age_tier, format_bytes, format_count, percent_of_parent,
)
from ..panels import format_filetime
from ..tree_model import BarFractionRole

#: age_tier() -> the colour its Last Modified cell gets. "active" uses the
#: same "something to look at" meaning `info` carries everywhere else in the
#: app; "stale" is dimmed with the muted chrome text colour rather than a
#: semantic one, because a year-old file is not a warning or an error -- it
#: is exactly what most of a disk is supposed to be.
_AGE_TIER_COLOR = {
    "active": lambda: semantic("info"),
    "stale": lambda: chrome("text_muted"),
}
LAST_MODIFIED_COLUMN = 6


def _signed(value: int, unit, decimals: int) -> str:
    """A change, with an explicit + so growth and shrinkage read differently
    at a glance. format_bytes already carries the minus sign."""
    text = format_bytes(value, unit, decimals)
    return "+" + text if value > 0 else text


def _attribute_letters(attrs: int) -> str:
    """Compact flag column, the way Explorer shows attributes.

    D irectory, H idden, R eparse point, C ompressed, S parse,
    A lternate data stream, L ink (hard-link duplicate).
    """
    return "".join(letter for bit, letter in (
        (DIR, "D"), (HIDDEN, "H"), (REPARSE, "R"), (COMPRESSED, "C"),
        (SPARSE, "S"), (ADS, "A"), (HARDLINK_DUP, "L"),
    ) if attrs & bit)

COLUMNS = ("Name", "Size", "Allocated", "Files", "Folders", "% of Parent",
           "Last Modified", "Last Accessed", "Created", "Owner", "Type",
           "Extension", "Attributes", "Path")
#: Shown by default. The rest are available from the column chooser, which is
#: how Pro handles its own longer column list -- everything at once is a wall.
DEFAULT_COLUMNS = ("Name", "Size", "Allocated", "Files", "Folders",
                   "% of Parent", "Last Modified", "Last Accessed", "Owner")
SIZE_COLUMN = 1
PERCENT_COLUMN = 5
SortValueRole = int(Qt.ItemDataRole.UserRole) + 10


class DetailsItem(QTreeWidgetItem):
    """Sorts on the underlying number, not the formatted string.

    Sorting the display text puts "9 B" above "10 GB" and "1.0 TB" below
    "999 MB" -- the classic size-column bug, and immediately obvious to anyone
    who uses the real product.
    """

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        column = self.treeWidget().sortColumn() if self.treeWidget() else 0
        mine = self.data(column, SortValueRole)
        theirs = other.data(column, SortValueRole)
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text(column).lower() < other.text(column).lower()


class DetailsView(QTreeWidget):
    node_activated = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(len(COLUMNS))
        self.setHeaderLabels(list(COLUMNS))
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setItemDelegateForColumn(PERCENT_COLUMN, ProportionBarDelegate(self))
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._store = None
        self._unit = Unit.AUTO
        self._decimals = 1
        self.itemDoubleClicked.connect(self._on_double_clicked)
        # Largest first, matching the tree and the product.
        self.sortByColumn(SIZE_COLUMN, Qt.SortOrder.DescendingOrder)
        # Right-clicking the header is where every table in Windows puts its
        # column chooser, so that is where this one lives too.
        header = self.header()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._column_menu)
        self.set_visible_columns(DEFAULT_COLUMNS)

    def set_visible_columns(self, names) -> None:
        wanted = set(names)
        for index, name in enumerate(COLUMNS):
            # Name is never hidden: a table of sizes with no names is useless,
            # and re-showing it would need a column chooser the user can no
            # longer reach a row to open.
            self.setColumnHidden(index, index != 0 and name not in wanted)

    def visible_columns(self) -> tuple:
        return tuple(name for i, name in enumerate(COLUMNS)
                     if not self.isColumnHidden(i))

    def _column_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        for index, name in enumerate(COLUMNS):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(index))
            action.setEnabled(index != 0)
            action.toggled.connect(
                lambda shown, i=index: self.setColumnHidden(i, not shown))
        menu.addSeparator()
        menu.addAction("Reset columns",
                       lambda: self.set_visible_columns(DEFAULT_COLUMNS))
        menu.exec(self.header().mapToGlobal(pos))

    def set_unit(self, unit: Unit, decimals: int | None = None) -> None:
        self._unit = unit
        if decimals is not None:
            self._decimals = decimals

    def show_children_of(self, store, node: int) -> None:
        self.clear()
        self._store = store
        if store is None or not (0 <= node < len(store)):
            return
        # Sorting is suspended while filling: re-sorting per insertion is
        # quadratic, and a folder with 40,000 children is ordinary.
        self.setSortingEnabled(False)
        # Captured once per fill, not per row, so a folder with 40,000
        # children does not have entries near a tier boundary land on
        # different sides of it depending on how long the fill took --
        # the same reasoning FilterSet.now in scan/filters.py uses for age
        # filtering.
        now = filetime_now()
        for child in store.children(node):
            if store.attrs[child] & EXCLUDED:
                continue
            self.addTopLevelItem(self._row(store, child, now))
        self.setSortingEnabled(True)

    def _row(self, store, node: int, now: int) -> QTreeWidgetItem:
        percent = percent_of_parent(store, node)
        is_dir = bool(store.attrs[node] & DIR)
        name = store.name(node)
        dot = name.rfind(".")
        extension = name[dot + 1:].lower() if 0 < dot < len(name) - 1 else ""
        item = DetailsItem([
            name,
            format_bytes(store.size[node], self._unit, self._decimals),
            format_bytes(store.alloc[node], self._unit, self._decimals),
            format_count(store.file_count[node]) if is_dir else "",
            format_count(store.folder_count[node]) if is_dir else "",
            f"{percent:.1f}%",
            format_filetime(store.mtime[node]),
            format_filetime(store.atime[node]),
            format_filetime(store.ctime[node]),
            store.owner(store.owner_id[node]) or "",
            "Folder" if is_dir else (f"{extension.upper()} file" if extension
                                     else "File"),
            extension,
            _attribute_letters(store.attrs[node]),
            store.path(node),
        ])
        item.setData(0, Qt.ItemDataRole.UserRole, node)
        item.setData(PERCENT_COLUMN, BarFractionRole, percent / 100.0)
        item.setData(SIZE_COLUMN, SortValueRole, store.size[node])
        item.setData(2, SortValueRole, store.alloc[node])
        item.setData(3, SortValueRole, store.file_count[node])
        item.setData(4, SortValueRole, store.folder_count[node])
        item.setData(PERCENT_COLUMN, SortValueRole, percent)
        item.setData(6, SortValueRole, store.mtime[node])
        item.setData(7, SortValueRole, store.atime[node])
        item.setData(8, SortValueRole, store.ctime[node])
        for column in range(1, len(COLUMNS)):
            item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
        tier = age_tier(store.mtime[node], now)
        color_fn = _AGE_TIER_COLOR.get(tier)
        if color_fn is not None:
            item.setForeground(LAST_MODIFIED_COLUMN, QColor(color_fn()))
        return item

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if node is not None:
            self.node_activated.emit(int(node))
