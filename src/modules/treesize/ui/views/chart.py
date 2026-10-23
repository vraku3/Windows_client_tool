"""Chart view: treemap, with a breadcrumb (spec 5.8).

Layout is computed in `treemap.py`, which knows nothing about Qt. This file
paints it and handles interaction: hover highlights, click selects in the tree,
double-click drills in, and the breadcrumb tracks depth.

Cushion shading gives each tile a lit dome so nesting reads as depth rather
than as a flat mosaic. It is a per-tile linear gradient rather than a real
per-pixel cushion: at tens of thousands of tiles the difference is invisible
and the cost is not.
"""

from modules.treesize.help_widgets import (
    describe_control, explain_widget,
)
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFontMetrics, QLinearGradient, QPainter, QPen,
)
from PyQt6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout,
    QWidget,
)

from ...store.node_store import DIR
from ..formatting import Mode, format_value, weight_value
from ..treemap import HitGrid, build_treemap

# One hue per top-level branch, cycled. Chosen for even spacing round the wheel
# and equal-ish perceived lightness, so no branch looks more important than
# another purely because of its colour.
PALETTE = (
    QColor(0x4E, 0x79, 0xA7), QColor(0xF2, 0x8E, 0x2B), QColor(0xE1, 0x57, 0x59),
    QColor(0x76, 0xB7, 0xB2), QColor(0x59, 0xA1, 0x4F), QColor(0xED, 0xC9, 0x48),
    QColor(0xB0, 0x7A, 0xA1), QColor(0xFF, 0x9D, 0xA7), QColor(0x9C, 0x75, 0x5F),
)
LABEL_MIN_W = 46
LABEL_MIN_H = 15


class TreemapWidget(QWidget):
    node_clicked = pyqtSignal(int)
    node_drilled = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self._store = None
        self._root = -1
        self._rects: list = []
        self._grid: HitGrid | None = None
        self._hover = -1
        self._colors: dict[int, QColor] = {}
        self.max_depth = 6
        self._mode = Mode.SIZE
        explain_widget(self)

    def set_value_mode(self, mode: Mode) -> None:
        """Spec 5.5's pane mode: what a tile's AREA measures."""
        if mode is self._mode:
            return
        self._mode = mode
        self._rebuild()

    def set_scan(self, store, root: int) -> None:
        self._store = store
        self._root = root
        self._colors.clear()
        self._rebuild()

    def _rebuild(self) -> None:
        if self._store is None or self._root < 0 or self.width() < 4:
            self._rects, self._grid = [], None
            self.update()
            return
        self._rects = build_treemap(self._store, self._root,
                                    float(self.width()), float(self.height()),
                                    max_depth=self.max_depth, mode=self._mode)
        self._grid = HitGrid(self._rects, float(self.width()), float(self.height()))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild()

    def _color_for(self, rect) -> QColor:
        """Colour by top-level ancestor, darkened with depth.

        Sharing a hue down a branch is what lets the eye follow a folder's
        subtree without reading a single label.
        """
        store = self._store
        node = rect.node
        ancestor = node
        while (store.parent[ancestor] not in (-1, self._root)
               and 0 <= store.parent[ancestor] < len(store)):
            ancestor = store.parent[ancestor]
        base = self._colors.get(ancestor)
        if base is None:
            base = PALETTE[len(self._colors) % len(PALETTE)]
            self._colors[ancestor] = base
        return base.darker(100 + 12 * max(0, rect.depth - 1))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(0x1F, 0x1F, 0x1F))
        if not self._rects:
            painter.setPen(QColor(0x9D, 0x9D, 0x9D))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No scan")
            return
        metrics = QFontMetrics(painter.font())
        for rect in self._rects:
            if rect.depth == 0 or rect.w < 1 or rect.h < 1:
                continue
            box = QRectF(rect.x, rect.y, rect.w, rect.h)
            color = self._color_for(rect)
            # Cushion shading: a light-to-dark gradient across the tile reads
            # as a lit dome, which is what makes nesting legible.
            gradient = QLinearGradient(box.topLeft(), box.bottomRight())
            gradient.setColorAt(0.0, color.lighter(125))
            gradient.setColorAt(1.0, color.darker(120))
            painter.fillRect(box, gradient)
            if rect.node == self._hover:
                painter.setPen(QPen(QColor(0xFF, 0xFF, 0xFF), 2))
                painter.drawRect(box.adjusted(1, 1, -1, -1))
            elif rect.w > 3 and rect.h > 3:
                painter.setPen(QPen(color.darker(160), 1))
                painter.drawRect(box)
            if rect.w >= LABEL_MIN_W and rect.h >= LABEL_MIN_H:
                painter.setPen(QColor(0xFF, 0xFF, 0xFF))
                name = self._store.name(rect.node)
                painter.drawText(box.adjusted(3, 1, -2, -1),
                                 int(Qt.AlignmentFlag.AlignLeft
                                     | Qt.AlignmentFlag.AlignTop),
                                 metrics.elidedText(name, Qt.TextElideMode.ElideMiddle,
                                                    int(rect.w) - 6))

    def mouseMoveEvent(self, event):
        hit = self._grid.hit(event.position().x(), event.position().y()) if self._grid else None
        node = hit.node if hit else -1
        if node != self._hover:
            self._hover = node
            if node >= 0 and self._store is not None:
                self.setToolTip(f"{self._store.path(node)}\n"
                                f"{format_value(self._store, node, self._mode)}")
            else:
                self.setToolTip("")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = -1
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        hit = self._grid.hit(event.position().x(), event.position().y()) if self._grid else None
        if hit:
            self.node_clicked.emit(hit.node)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        hit = self._grid.hit(event.position().x(), event.position().y()) if self._grid else None
        if hit and self._store is not None and self._store.attrs[hit.node] & DIR:
            self.node_drilled.emit(hit.node)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class SliceChart(QWidget):
    """Pie or bar over the immediate children of the current node.

    Deliberately only ONE level deep, unlike the treemap. A pie of a whole
    subtree is unreadable, and Pro's pie answers a different question from its
    treemap: "how is this folder divided", not "where is everything".

    Children past the tenth are folded into one neutral "Other" slice rather
    than cycling the palette. Reusing a hue for an unrelated folder is actively
    misleading, and dropping them silently makes the percentages not add to 100.
    """

    MAX_SLICES = 10
    OTHER_COLOR = QColor(0x6B, 0x6B, 0x6B)

    node_clicked = pyqtSignal(int)

    def __init__(self, bars: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._bars = bars
        self._store = None
        self._node = -1
        self._mode = Mode.SIZE
        #: (node, name, text, fraction, color) -- `text` is preformatted
        #: because what a slice MEANS depends on the mode, and neither painter
        #: should have to work that out again.
        self._rows: list = []
        self.setMinimumHeight(160)
        explain_widget(self)

    def set_value_mode(self, mode: Mode) -> None:
        if mode is self._mode:
            return
        self._mode = mode
        self.set_scan(self._store, self._node)

    def set_scan(self, store, node: int) -> None:
        self._store = store
        self._node = node
        self._rows = []
        if store is not None and 0 <= node < len(store):
            from ...store.node_store import EXCLUDED

            def weight(n):
                return weight_value(store, n, self._mode)

            kids = [c for c in store.children(node)
                    if not (store.attrs[c] & EXCLUDED) and weight(c) > 0]
            kids.sort(key=weight, reverse=True)
            total = sum(weight(c) for c in kids)
            if total > 0:
                head, tail = kids[:self.MAX_SLICES], kids[self.MAX_SLICES:]
                for i, child in enumerate(head):
                    self._rows.append((child, store.name(child),
                                       format_value(store, child, self._mode),
                                       weight(child) / total,
                                       PALETTE[i % len(PALETTE)]))
                if tail:
                    folded = sum(weight(c) for c in tail)
                    self._rows.append((-1, f"Other ({len(tail)})",
                                       self._fold_text(folded),
                                       folded / total, self.OTHER_COLOR))
        self.update()

    def _fold_text(self, folded: int) -> str:
        """The "Other" slice has no node, so it cannot go through format_value.

        Under Percent it carries no absolute number at all -- the share it is
        drawn at already IS the number, and printing "100.0%" beside a 12%
        wedge would be worse than printing nothing.
        """
        from ..formatting import format_bytes, format_count
        if self._mode is Mode.FILES:
            return format_count(folded)
        if self._mode is Mode.PERCENT:
            return ""
        return format_bytes(folded)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0x1F, 0x1F, 0x1F))
        if not self._rows:
            painter.setPen(QColor(0x9D, 0x9D, 0x9D))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Nothing to chart")
            return
        if self._bars:
            self._paint_bars(painter)
        else:
            self._paint_pie(painter)

    def _paint_pie(self, painter: QPainter) -> None:
        size = min(self.height() - 16, self.width() // 2)
        cx, cy, r = size // 2 + 12, self.height() // 2, size // 2 - 6
        angle = 90 * 16                     # start at twelve o'clock
        for _node, _name, _size, fraction, color in self._rows:
            sweep = -int(360 * 16 * fraction)
            painter.setPen(QPen(QColor(0x1F, 0x1F, 0x1F), 1))
            painter.setBrush(color)
            painter.drawPie(cx - r, cy - r, r * 2, r * 2, angle, sweep)
            angle += sweep
        # Donut hole, which is what keeps small slices legible at the centre.
        painter.setBrush(QColor(0x1F, 0x1F, 0x1F))
        painter.setPen(Qt.PenStyle.NoPen)
        inner = int(r * 0.55)
        painter.drawEllipse(cx - inner, cy - inner, inner * 2, inner * 2)
        self._paint_legend(painter, size + 28)

    def _paint_bars(self, painter: QPainter) -> None:
        metrics = QFontMetrics(painter.font())
        row_height = max(18, metrics.height() + 6)
        label_width = 160
        for i, (_node, name, text, fraction, color) in enumerate(self._rows):
            y = 8 + i * row_height
            if y + row_height > self.height():
                break
            painter.setPen(QColor(0xF1, 0xF1, 0xF1))
            painter.drawText(8, y + metrics.ascent(),
                             metrics.elidedText(name, Qt.TextElideMode.ElideMiddle,
                                                label_width - 12))
            track = self.width() - label_width - 110
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0x2A, 0x2D, 0x2E))
            painter.drawRect(label_width, y, track, row_height - 8)
            painter.setBrush(color)
            painter.drawRect(label_width, y, int(track * fraction), row_height - 8)
            painter.setPen(QColor(0x9D, 0x9D, 0x9D))
            painter.drawText(label_width + track + 8, y + metrics.ascent(),
                             f"{text}  {fraction * 100:.1f}%")

    def _paint_legend(self, painter: QPainter, x: int) -> None:
        metrics = QFontMetrics(painter.font())
        step = metrics.height() + 4
        for i, (_node, name, text, fraction, color) in enumerate(self._rows):
            y = 12 + i * step
            if y + step > self.height():
                break
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(x, y, 10, 10)
            painter.setPen(QColor(0xF1, 0xF1, 0xF1))
            painter.drawText(x + 16, y + metrics.ascent() - 2,
                             f"{name}  —  {text}  "
                             f"({fraction * 100:.1f}%)")

    def mousePressEvent(self, event):
        """Clicking a bar selects that folder. Pie slices are not hit-tested:
        the angle maths is easy to get subtly wrong and a wrong selection is
        worse than none."""
        if not self._bars or not self._rows:
            return
        metrics = QFontMetrics(self.font())
        row_height = max(18, metrics.height() + 6)
        index = int((event.position().y() - 8) // row_height)
        if 0 <= index < len(self._rows):
            node = self._rows[index][0]
            if node >= 0:
                self.node_clicked.emit(node)
                event.accept()
                return
        super().mousePressEvent(event)


class ChartView(QWidget):
    """Treemap plus the breadcrumb that tracks drill depth."""

    node_clicked = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._store = None
        self._trail: list[int] = []
        self._chart_mode = 0
        self._value_mode = Mode.SIZE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        crumb_bar = QWidget(self)
        crumb_row = QHBoxLayout(crumb_bar)
        crumb_row.setContentsMargins(6, 2, 6, 2)
        crumb_row.setSpacing(2)
        self._crumbs = QHBoxLayout()
        self._crumbs.setContentsMargins(0, 0, 0, 0)
        self._crumbs.setSpacing(2)
        crumb_row.addLayout(self._crumbs, 1)

        # Treemap / Pie / Bar, as Pro offers. The treemap answers "where is
        # everything"; the pie and bar answer "how is THIS folder divided",
        # which is a different question and why all three exist.
        self._mode_buttons = QButtonGroup(self)
        self._mode_buttons.setExclusive(True)
        for index, label in enumerate(("Treemap", "Pie", "Bar")):
            button = QToolButton(crumb_bar)
            describe_control(button, 'button')
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setAutoRaise(True)
            button.setObjectName("chartModeButton")
            self._mode_buttons.addButton(button, index)
            crumb_row.addWidget(button)
        self._mode_buttons.idClicked.connect(self.set_chart_mode)
        layout.addWidget(crumb_bar)

        self.treemap = TreemapWidget(self)
        self.pie = SliceChart(bars=False, parent=self)
        self.bars = SliceChart(bars=True, parent=self)
        self.pie.hide()
        self.bars.hide()
        for widget in (self.treemap, self.pie, self.bars):
            layout.addWidget(widget, 1)
        self.treemap.node_clicked.connect(self.node_clicked)
        self.treemap.node_drilled.connect(self.drill_to)
        self.pie.node_clicked.connect(self.node_clicked)
        self.bars.node_clicked.connect(self.node_clicked)
        explain_widget(self)

    def set_value_mode(self, mode: Mode) -> None:
        """Spec 5.5's Mode. Distinct from set_chart_mode, which chooses
        between treemap, pie and bar -- that one picks the SHAPE, this one
        picks what the shape is measuring."""
        self._value_mode = mode
        for widget in (self.treemap, self.pie, self.bars):
            widget.set_value_mode(mode)

    def set_chart_mode(self, index: int) -> None:
        for i, widget in enumerate((self.treemap, self.pie, self.bars)):
            widget.setVisible(i == index)
        self._chart_mode = index
        if self._trail:
            self._show(self._trail[-1])

    def _show(self, node: int) -> None:
        self.treemap.set_scan(self._store, node)
        self.pie.set_scan(self._store, node)
        self.bars.set_scan(self._store, node)

    def set_scan(self, store, root: int) -> None:
        self._store = store
        self._trail = [root] if root >= 0 else []
        self._show(root)
        self._rebuild_crumbs()

    def drill_to(self, node: int) -> None:
        if self._store is None:
            return
        if node in self._trail:
            self._trail = self._trail[:self._trail.index(node) + 1]
        else:
            self._trail.append(node)
        self._show(node)
        self._rebuild_crumbs()
        self.node_clicked.emit(node)

    def _rebuild_crumbs(self) -> None:
        while self._crumbs.count():
            item = self._crumbs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for depth, node in enumerate(self._trail):
            if depth:
                self._crumbs.addWidget(QLabel("›"))
            button = QPushButton(self._store.name(node) if self._store else "")
            describe_control(button, 'button')
            button.setFlat(True)
            button.setObjectName("breadcrumb")
            button.clicked.connect(lambda _checked=False, n=node: self.drill_to(n))
            self._crumbs.addWidget(button)
        self._crumbs.addStretch(1)
