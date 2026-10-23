"""QAbstractItemModel over the columnar node store (spec 5.4).

`QModelIndex.internalId()` carries the node index directly, so no per-row proxy
object is allocated. On a five-million-node scan an object per visible row would
be affordable, but an object per NODE would not, and Qt asks for indexes far
beyond what is on screen while sorting and expanding.

Node index 0 is a legitimate node, and `createIndex` cannot distinguish an
internalId of 0 from an unset one, so every id is stored as `node + 1` and the
root's parent is 0. `_node()` and `_id()` are the only places that know this.

Sorting reorders sibling ranges in place and signals layoutAboutToBeChanged /
layoutChanged, never a model reset: a reset on a multi-million-node model
collapses the whole tree and throws away the user's expansion state.
"""

from modules.treesize.help_text import header_help
from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt

from ..store.node_store import DIR, EXCLUDED
from .formatting import Mode, Unit, bar_fraction, format_value
from .icons import IconProvider

COLUMN_NAME = 0
COLUMN_VALUE = 1
COLUMN_HEADERS = ("Name", "Size")

# Role carrying the 0.0-1.0 bar fraction to the delegate. Qt reserves
# everything below UserRole for itself.
BarFractionRole = int(Qt.ItemDataRole.UserRole) + 1
NodeIndexRole = int(Qt.ItemDataRole.UserRole) + 2


class DirectoryTreeModel(QAbstractItemModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._store = None
        self._root = -1
        self._mode = Mode.SIZE
        self._unit = Unit.AUTO
        self._decimals = 1
        # node -> tuple of child nodes, populated lazily. The store's
        # first_child/next_sibling chain is a linked list, and Qt needs random
        # access by row, so each expanded parent's children are materialised
        # once and reused.
        self._children: dict[int, tuple[int, ...]] = {}
        self._icons = IconProvider()
        # Sorting must apply to folders expanded AFTER the sort, not only to
        # the ones already materialised, or expanding a new folder silently
        # yields MFT order. Held here and applied inside children_of().
        self._sort_key = None
        self._sort_desc = True
        self._hide_empty = False

    # ---- population -----------------------------------------------------

    def set_scan(self, store, root: int) -> None:
        self.beginResetModel()
        self._store = store
        self._root = root
        self._children.clear()
        # Largest first, which is the whole point of the tool: the answer to
        # "what is filling my disk" should be the first row, not buried in an
        # alphabetical list.
        self._sort_key = lambda n: node_sort_value(store, n, self._mode) if store else 0
        self._sort_desc = True
        self.endResetModel()

    def clear(self) -> None:
        self.set_scan(None, -1)

    def set_hide_empty(self, hidden: bool) -> None:
        if hidden == self._hide_empty:
            return
        self._hide_empty = hidden
        # The child lists are cached, so they have to be dropped and the views
        # told, or the change would not appear until something else reset them.
        self.beginResetModel()
        self._children.clear()
        self.endResetModel()

    def set_mode(self, mode: Mode) -> None:
        """Mode is pane state: it repaints every row but moves none of them."""
        if mode is self._mode:
            return
        self._mode = mode
        # The sort key reads the mode, so changing mode reorders as well as
        # relabels -- switching to Number of files puts the fullest folder on
        # top, which is what the mode is for.
        if self._sort_key is not None and self._store is not None:
            self.sort(COLUMN_VALUE,
                      Qt.SortOrder.DescendingOrder if self._sort_desc
                      else Qt.SortOrder.AscendingOrder)
        self._repaint_all()

    def set_unit(self, unit: Unit, decimals: int | None = None) -> None:
        if unit is self._unit and (decimals is None or decimals == self._decimals):
            return
        self._unit = unit
        if decimals is not None:
            self._decimals = decimals
        self._repaint_all()

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def unit(self) -> Unit:
        return self._unit

    def refresh_values(self) -> None:
        """Repaint every row without moving any of them.

        A live update changes numbers, not structure, so a reset here would
        collapse the tree under someone who is watching a folder grow.
        """
        self._repaint_all()

    def refresh_structure(self) -> None:
        """Rows appeared or vanished: drop the cached child lists and relayout.

        The watcher inserts a node for a created file and marks a deleted one
        EXCLUDED, neither of which `children_of` would notice on its own --
        the tuple it cached is what the view keeps asking for. layoutChanged
        rather than a reset, for the same reason sorting uses it: a reset
        collapses the tree and discards the selection.

        Node indices never move (nodes are only appended), so an internalId
        stays valid across this and persistent indexes survive.
        """
        self.layoutAboutToBeChanged.emit()
        self._children.clear()
        self.layoutChanged.emit()
        self._repaint_all()

    def _repaint_all(self) -> None:
        if self._store is None or not len(self._store):
            return
        top = self.index(0, 0, QModelIndex())
        if top.isValid():
            self.dataChanged.emit(
                top, self.index(self.rowCount(QModelIndex()) - 1,
                                self.columnCount(QModelIndex()) - 1, QModelIndex()))

    # ---- index plumbing -------------------------------------------------

    def _node(self, index: QModelIndex) -> int:
        """The store node behind an index, or -1 for the invisible root."""
        return int(index.internalId()) - 1 if index.isValid() else -1

    def children_of(self, node: int) -> tuple[int, ...]:
        cached = self._children.get(node)
        if cached is not None:
            return cached
        store = self._store
        kids = [c for c in store.children(node) if not (store.attrs[c] & EXCLUDED)]
        if self._hide_empty:
            # Zero SIZE, not zero children: a folder holding only empty files
            # is what "empty" means to someone freeing space.
            kids = [c for c in kids
                    if store.size[c] > 0 or not (store.attrs[c] & DIR)]
        if self._sort_key is not None:
            kids.sort(key=self._sort_key, reverse=self._sort_desc)
        result = tuple(kids)
        self._children[node] = result
        return result

    def index(self, row, column, parent=QModelIndex()):
        if self._store is None or row < 0 or column < 0:
            return QModelIndex()
        if not parent.isValid():
            if row != 0 or self._root < 0:
                return QModelIndex()
            return self.createIndex(row, column, self._root + 1)
        kids = self.children_of(self._node(parent))
        if row >= len(kids):
            return QModelIndex()
        return self.createIndex(row, column, kids[row] + 1)

    def parent(self, index=QModelIndex()):
        node = self._node(index)
        if node < 0 or node == self._root:
            return QModelIndex()
        parent = self._store.parent[node]
        if not (0 <= parent < len(self._store)):
            return QModelIndex()
        if parent == self._root:
            return self.createIndex(0, 0, self._root + 1)
        grandparent = self._store.parent[parent]
        if not (0 <= grandparent < len(self._store)):
            return self.createIndex(0, 0, parent + 1)
        siblings = self.children_of(grandparent)
        try:
            row = siblings.index(parent)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, 0, parent + 1)

    def rowCount(self, parent=QModelIndex()):
        if self._store is None or self._root < 0:
            return 0
        if not parent.isValid():
            return 1
        if parent.column() != 0:
            return 0            # only column 0 carries children, as Qt requires
        return len(self.children_of(self._node(parent)))

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMN_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole:
            return header_help(self.headerData(section, orientation, Qt.ItemDataRole.DisplayRole))
        if (orientation is Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole
                and 0 <= section < len(COLUMN_HEADERS)):
            if section == COLUMN_VALUE:
                return self._mode.value
            return COLUMN_HEADERS[section]
        return None

    def hasChildren(self, parent=QModelIndex()):
        if self._store is None or self._root < 0:
            return False
        if not parent.isValid():
            return True
        node = self._node(parent)
        if not (self._store.attrs[node] & DIR):
            return False
        return len(self.children_of(node)) > 0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        node = self._node(index)
        if node < 0 or self._store is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == COLUMN_NAME:
                return self._store.name(node)
            return format_value(self._store, node, self._mode, self._unit,
                                self._decimals)
        if role == Qt.ItemDataRole.DecorationRole and index.column() == COLUMN_NAME:
            return self._icon_for(node)
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == COLUMN_VALUE:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == BarFractionRole:
            return bar_fraction(self._store, node, self._mode)
        if role == NodeIndexRole:
            return node
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._store.path(node)
        return None

    def _icon_for(self, node: int):
        """Explorer's own icon for the row, cached per extension.

        Per extension, not per file: a volume has a few hundred distinct
        extensions and half a million files, so an icon per file would cost
        more memory than the entire node store.
        """
        store = self._store
        if store.attrs[node] & DIR:
            return self._icons.folder()
        return self._icons.for_name(store.name(node))

    # ---- sorting --------------------------------------------------------

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        """Reorder sibling ranges in place; never reset the model.

        A reset collapses a multi-million-node tree and discards the user's
        expansion state, which is exactly what Pro does not do when you click
        a column header.
        """
        if self._store is None or self._root < 0:
            return
        descending = order is Qt.SortOrder.DescendingOrder
        store = self._store
        if column == COLUMN_NAME:
            def key(n):
                return store.name(n).lower()
        else:
            def key(n):
                return node_sort_value(store, n, self._mode)

        self.layoutAboutToBeChanged.emit()
        self._sort_key = key
        self._sort_desc = descending
        for parent, kids in list(self._children.items()):
            self._children[parent] = tuple(sorted(kids, key=key,
                                                  reverse=descending))
        self.layoutChanged.emit()


def node_sort_value(store, node: int, mode: Mode):
    if mode is Mode.FILES:
        return store.file_count[node]
    if mode is Mode.ALLOCATED:
        return store.alloc[node]
    return store.size[node]
