"""The graphs on the Performance tab.

Pure `QPainter`, like `perfmon_charts.py` next door -- no pyqtgraph, no
matplotlib. Two widgets:

- `PerfGraph`, one filled history plot with Task Manager's grid.
- `CoreGrid`, the wall of small per-core plots.

**Every coordinate handed to a PyQt6 draw call is cast to `int`.**
`drawLine`, `fillRect`, `drawText` and `drawPolygon` reject a float and raise
a TypeError -- and this raises inside `paintEvent`, a reimplemented Qt
virtual, where an exception goes to `sys.excepthook` and then `qFatal()`.
Uncatchable, and the window dies. That rule is already recorded in CLAUDE.md
for the PerfMon charts; it applies here for the same reason.

The history is a `deque` with a fixed maximum, so a graph left running for a
day costs the same as one left running for a minute.
"""
from collections import deque
from typing import List, Optional

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import QSizePolicy, QWidget

from core.semantic_colors import semantic

#: 60 seconds at one sample a second, which is the window Task Manager shows.
HISTORY = 60

#: Task Manager's grid is a fixed 10x6, so the graph reads the same whatever
#: the widget's size.
GRID_COLUMNS = 10
GRID_ROWS = 6


class PerfGraph(QWidget):
    """One filled history plot, scaled 0..`ceiling`."""

    def __init__(self, colour: Optional[str] = None, ceiling: float = 100.0,
                 parent=None) -> None:
        super().__init__(parent)
        self._history = deque(maxlen=HISTORY)
        self._ceiling = max(1.0, ceiling)
        self._colour = colour
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    # ---- data -----------------------------------------------------------

    def push(self, value: Optional[float]) -> None:
        """Add a sample. `None` is recorded as a gap, not as zero.

        The distinction the whole engine keeps: a reading we do not have is
        not a reading of nothing, and a graph that draws it as zero invents
        a dip that never happened.
        """
        self._history.append(None if value is None else float(value))
        self.update()

    def set_ceiling(self, ceiling: float) -> None:
        self._ceiling = max(1.0, float(ceiling))
        self.update()

    def history(self) -> List:
        return list(self._history)

    def clear(self) -> None:
        self._history.clear()
        self.update()

    def latest(self) -> Optional[float]:
        for value in reversed(self._history):
            if value is not None:
                return value
        return None

    # ---- painting -------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        line = QColor(self._colour or semantic("info"))

        self._paint_background(painter, rect, line)
        self._paint_grid(painter, rect, line)
        self._paint_series(painter, rect, line)
        painter.end()

    def _paint_background(self, painter, rect, line) -> None:
        wash = QColor(line)
        wash.setAlpha(18)
        painter.fillRect(rect, wash)
        border = QColor(line)
        border.setAlpha(90)
        painter.setPen(QPen(border, 1))
        painter.drawRect(rect)

    def _paint_grid(self, painter, rect, line) -> None:
        grid = QColor(line)
        grid.setAlpha(38)
        painter.setPen(QPen(grid, 1))
        for column in range(1, GRID_COLUMNS):
            x = int(rect.left() + rect.width() * column / GRID_COLUMNS)
            painter.drawLine(x, int(rect.top()), x, int(rect.bottom()))
        for row in range(1, GRID_ROWS):
            y = int(rect.top() + rect.height() * row / GRID_ROWS)
            painter.drawLine(int(rect.left()), y, int(rect.right()), y)

    def _paint_series(self, painter, rect, line) -> None:
        """Each run of real samples as its own filled shape.

        Runs, not one polygon: a gap where a reading was missing has to be a
        gap. Joining across it would draw a straight line through time that
        nothing measured.
        """
        points = self._points(rect)
        if not points:
            return
        fill = QColor(line)
        fill.setAlpha(70)
        for run in points:
            if len(run) < 2:
                continue
            shape = QPolygon(run)
            shape.append(QPoint(run[-1].x(), int(rect.bottom())))
            shape.append(QPoint(run[0].x(), int(rect.bottom())))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPolygon(shape)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(line, 2))
            painter.drawPolyline(QPolygon(run))

    def _points(self, rect) -> List[List[QPoint]]:
        history = list(self._history)
        if not history:
            return []
        # Anchored to the RIGHT edge: a graph that has not filled its window
        # yet grows from the right, the way Task Manager's does, rather than
        # stretching a few samples across the whole width.
        span = max(1, HISTORY - 1)
        step = rect.width() / span
        runs, run = [], []
        for index, value in enumerate(history):
            if value is None:
                if run:
                    runs.append(run)
                    run = []
                continue
            offset = len(history) - 1 - index
            x = int(rect.right() - offset * step)
            share = min(1.0, max(0.0, value / self._ceiling))
            y = int(rect.bottom() - share * rect.height())
            run.append(QPoint(x, y))
        if run:
            runs.append(run)
        return runs


class CoreGrid(QWidget):
    """A small plot per logical processor, laid out as a grid.

    One widget rather than 32: at 32 cores, 32 child widgets each with their
    own paint event and layout is measurably worse than one that draws 32
    rectangles.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._histories: List[deque] = []
        self._colour = None
        self._kinds: List[str] = []       # "P" / "E" / "" per plot
        self._labels: List[str] = []
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def push(self, loads) -> None:
        """One sample per core."""
        if len(self._histories) != len(loads):
            self._histories = [deque(maxlen=HISTORY) for _ in loads]
        for history, value in zip(self._histories, loads):
            history.append(None if value is None else float(value))
        self.update()

    def set_kinds(self, kinds) -> None:
        """Mark efficiency cores (drawn in the warning hue) on a hybrid CPU."""
        self._kinds = list(kinds)
        self.update()

    def set_labels(self, labels) -> None:
        """A short caption in the corner of each plot ("Node 0", "P3")."""
        self._labels = list(labels)
        self.update()

    def cores(self) -> int:
        return len(self._histories)

    def history(self, core: int) -> List:
        if 0 <= core < len(self._histories):
            return list(self._histories[core])
        return []

    def paintEvent(self, event) -> None:
        if not self._histories:
            return
        painter = QPainter(self)
        colour = QColor(self._colour or semantic("info"))
        columns, rows = self._shape()
        cell_w = self.width() / columns
        cell_h = self.height() / rows

        for index, history in enumerate(self._histories):
            column, row = index % columns, index // columns
            cell = QRect(int(column * cell_w) + 1, int(row * cell_h) + 1,
                         int(cell_w) - 3, int(cell_h) - 3)
            kind = self._kinds[index] if index < len(self._kinds) else ""
            self._paint_core(painter, cell, history,
                             QColor(semantic("warning")) if kind == "E" else colour)
            if index < len(self._labels) and self._labels[index]:
                painter.setPen(self.palette().text().color())
                painter.drawText(cell.adjusted(4, 2, 0, 0),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                 self._labels[index])
        painter.end()

    def _shape(self):
        """Rows and columns for the core count.

        Task Manager lays these out roughly square; a single row of 32 would
        make each plot four pixels wide.
        """
        count = len(self._histories)
        columns = 1
        while columns * columns < count:
            columns += 1
        rows = (count + columns - 1) // columns
        return max(1, columns), max(1, rows)

    def _paint_core(self, painter, cell, history, colour) -> None:
        wash = QColor(colour)
        wash.setAlpha(18)
        painter.fillRect(cell, wash)
        border = QColor(colour)
        border.setAlpha(70)
        painter.setPen(QPen(border, 1))
        painter.drawRect(cell)

        values = [value for value in history if value is not None]
        if len(values) < 2:
            return
        span = max(1, HISTORY - 1)
        step = cell.width() / span
        points = []
        for offset, value in enumerate(reversed(values)):
            x = int(cell.right() - offset * step)
            if x < cell.left():
                break
            share = min(1.0, max(0.0, value / 100.0))
            points.append(QPoint(x, int(cell.bottom() - share * cell.height())))
        if len(points) < 2:
            return
        shape = QPolygon(points)
        shape.append(QPoint(points[-1].x(), int(cell.bottom())))
        shape.append(QPoint(points[0].x(), int(cell.bottom())))
        fill = QColor(colour)
        fill.setAlpha(60)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPolygon(shape)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(colour, 1))
        painter.drawPolyline(QPolygon(points))
