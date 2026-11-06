"""Directory tree view and its proportional-bar delegate (spec 5.4).

Each row shows the value, a proportional bar, an icon, and the name -- Pro's
arrangement. The bar is painted by a QStyledItemDelegate rather than being a
widget per row, because a widget per row does not survive a large scan.
"""
from PyQt6.QtCore import QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QStyledItemDelegate, QStyleOptionViewItem,
    QTreeView,
)

from ..store.node_store import DIR
from .formatting import weight_value
from .tree_model import (
    BarFractionRole, COLUMN_VALUE, DirectoryTreeModel, NodeIndexRole,
)

# Sampled from Pro's dark scheme; the light sheet overrides via set_bar_colors.
BAR_FILL = QColor(0x3D, 0x8B, 0xD4)
BAR_TRACK = QColor(0x2A, 0x2D, 0x2E)
BAR_HEIGHT = 6
BAR_MARGIN = 4


class ProportionBarDelegate(QStyledItemDelegate):
    """Paints the value text with a proportional bar beneath it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fill = BAR_FILL
        self._track = BAR_TRACK

    def set_bar_colors(self, fill: QColor, track: QColor) -> None:
        self._fill = fill
        self._track = track

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex):
        """Reserve room beneath the text so the bar is not drawn through it."""
        size = super().sizeHint(option, index)
        size.setHeight(size.height() + BAR_HEIGHT + 2)
        return size

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        # Text occupies everything above the bar strip; the bar gets the rest.
        # Painting both into the same rect leaves the number sitting on top of
        # its own bar, which is unreadable at small sizes.
        text_option = QStyleOptionViewItem(option)
        text_option.rect = option.rect.adjusted(0, 0, 0, -(BAR_HEIGHT + 1))
        super().paint(painter, text_option, index)
        fraction = index.data(BarFractionRole)
        if fraction is None:
            return
        bar = option.rect.adjusted(BAR_MARGIN, 0, -BAR_MARGIN, -2)
        bar.setTop(bar.bottom() - BAR_HEIGHT)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._track)
        painter.drawRect(bar)
        filled = int(bar.width() * max(0.0, min(1.0, float(fraction))))
        if filled > 0:
            painter.setBrush(self._fill)
            painter.drawRect(bar.x(), bar.y(), filled, bar.height())
        painter.restore()


class DirectoryTree(QTreeView):
    """The left-hand tree. Emits the store node index on selection."""

    node_selected = pyqtSignal(int)
    node_activated = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model = DirectoryTreeModel(self)
        self.setModel(self._model)
        self._delegate = ProportionBarDelegate(self)
        self.setItemDelegateForColumn(COLUMN_VALUE, self._delegate)

        self.setUniformRowHeights(True)          # required for large models
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(False)
        self.setExpandsOnDoubleClick(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.header().setStretchLastSection(True)
        self.header().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(0, 320)

        self.selectionModel().currentChanged.connect(self._on_current_changed)
        self.doubleClicked.connect(self._on_double_clicked)

    @property
    def tree_model(self) -> DirectoryTreeModel:
        return self._model

    def set_scan(self, store, root: int) -> None:
        self._model.set_scan(store, root)
        top = self._model.index(0, 0, QModelIndex())
        if top.isValid():
            self.expand(top)
            self.setCurrentIndex(top)

    def expand_to_size(self, threshold: float, limit: int = 5000) -> int:
        """Expand every folder worth at least `threshold` (spec 5.4).

        Pro's expand-to-size-threshold: the one Expand entry that answers
        "show me everything worth looking at and nothing else", rather than a
        fixed depth that is too shallow on one branch and too deep on the next.

        Judged on the CURRENT mode, so under Number of files the threshold is
        a file count -- the column being looked at is the one it applies to.
        Under Percent it falls back to size: percent-of-parent is not
        monotonic down a branch (a lone child of a tiny folder is 100% of it),
        so a percentage cannot prune anything, and a threshold that prunes
        nothing expands the entire volume.

        `limit` is not a nicety. "Everything over 1 MB" on a real volume is
        hundreds of thousands of rows, and opening them all is a freeze.
        """
        model = self._model
        store = model._store
        if store is None or not len(store):
            return 0

        opened = 0
        stack = [model.index(0, 0, QModelIndex())]
        while stack and opened < limit:
            index = stack.pop()
            if not index.isValid():
                continue
            node = int(index.internalId()) - 1
            if not (0 <= node < len(store)) or not (store.attrs[node] & DIR):
                continue
            if weight_value(store, node, model.mode) < threshold:
                # A subtree total only ever shrinks going down, so a folder
                # under the threshold cannot contain one over it. Not
                # descending is what keeps this cheap on a whole volume.
                continue
            self.expand(index)
            opened += 1
            for row in range(model.rowCount(index)):
                stack.append(model.index(row, 0, index))
        return opened

    def selected_node(self) -> int:
        index = self.currentIndex()
        if not index.isValid():
            return -1
        value = index.data(NodeIndexRole)
        return -1 if value is None else int(value)

    def _on_current_changed(self, current: QModelIndex, _previous) -> None:
        if current.isValid():
            value = current.data(NodeIndexRole)
            if value is not None:
                self.node_selected.emit(int(value))

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        value = index.data(NodeIndexRole)
        if value is not None:
            self.node_activated.emit(int(value))
        self.setExpanded(index, not self.isExpanded(index))
