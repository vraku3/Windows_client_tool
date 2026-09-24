"""History view (spec 5.8): sizes over time across snapshots and saved scans.

Two halves: a sparkline of total size against time, and the list it is drawn
from. The chart is deliberately simple — one series, no axes furniture — since
the question it answers is "is this growing, and when did it jump", not "what
exactly was it on the 14th". The list carries the exact numbers.

Also hosts the comparison result, because a diff is a statement about two
points in this same history and splitting them across two views would mean
navigating away from the thing you just compared.
"""
import time

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..formatting import format_bytes

LINE = QColor(0x3D, 0x8B, 0xD4)
FILL = QColor(0x3D, 0x8B, 0xD4, 60)
GRID = QColor(0x3F, 0x3F, 0x46)
TEXT = QColor(0x9D, 0x9D, 0x9D)


class Sparkline(QWidget):
    """Total size over time. One series, oldest to newest."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points: list[tuple[float, int]] = []
        self.setMinimumHeight(120)

    def set_points(self, points) -> None:
        self._points = sorted(points, key=lambda p: p[0])
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0x1F, 0x1F, 0x1F))
        metrics = QFontMetrics(painter.font())
        if len(self._points) < 2:
            painter.setPen(TEXT)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Two or more snapshots are needed to show a trend")
            return

        left, right = 70, self.width() - 12
        top, bottom = 12, self.height() - 22
        sizes = [size for _when, size in self._points]
        low, high = min(sizes), max(sizes)
        # A flat series would divide by zero and, worse, draw a line at the
        # top of the chart implying a maximum. Pad it into a band instead.
        if high == low:
            high = low + max(1, low // 20)

        painter.setPen(QPen(GRID, 1))
        for i in range(3):
            y = top + (bottom - top) * i / 2
            painter.drawLine(left, int(y), right, int(y))
            value = high - (high - low) * i / 2
            painter.setPen(TEXT)
            painter.drawText(4, int(y) + metrics.ascent() // 2,
                             format_bytes(int(value)))
            painter.setPen(QPen(GRID, 1))

        span = self._points[-1][0] - self._points[0][0] or 1.0
        polygon = QPolygonF()
        for when, size in self._points:
            x = left + (right - left) * (when - self._points[0][0]) / span
            y = bottom - (bottom - top) * (size - low) / (high - low)
            polygon.append(QPointF(x, y))

        area = QPolygonF(polygon)
        area.append(QPointF(right, bottom))
        area.append(QPointF(left, bottom))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(FILL)
        painter.drawPolygon(area)

        painter.setPen(QPen(LINE, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(polygon)
        painter.setBrush(LINE)
        for point in polygon:
            painter.drawEllipse(point, 3, 3)

        painter.setPen(TEXT)
        for index, label_point in ((0, polygon[0]), (-1, polygon[-1])):
            when = self._points[index][0]
            text = time.strftime("%Y-%m-%d", time.localtime(when))
            x = int(label_point.x())
            if index == -1:
                x -= metrics.horizontalAdvance(text)
            painter.drawText(x, self.height() - 6, text)


class HistoryView(QWidget):
    """Snapshot list plus trend, and the comparison result."""

    snapshot_chosen = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.summary = QLabel("No snapshots yet", self)
        self.summary.setObjectName("historySummary")
        self.summary.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(self.summary)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.chart = Sparkline(splitter)
        splitter.addWidget(self.chart)

        self.table = QTreeWidget(splitter)
        self.table.setColumnCount(5)
        self.table.setHeaderLabels(
            ["When", "Target", "Size", "Change", "Nodes"])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._chosen)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

    def set_snapshots(self, snapshots) -> None:
        """Newest first in the table; oldest first in the chart."""
        self.table.clear()
        ordered = sorted(snapshots, key=lambda s: s.timestamp)
        previous = None
        rows = []
        for info in ordered:
            change = "" if previous is None else format_bytes(
                info.total_size - previous)
            rows.append((info, change))
            previous = info.total_size

        for info, change in reversed(rows):
            item = QTreeWidgetItem([
                info.when, info.target, format_bytes(info.total_size),
                change, f"{info.node_count:,}",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, info.path)
            for column in (2, 3, 4):
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.table.addTopLevelItem(item)

        self.chart.set_points([(s.timestamp, s.total_size) for s in ordered])
        if not ordered:
            self.summary.setText(
                "No snapshots yet — use Tools ▸ Create snapshot after a scan.")
        elif len(ordered) == 1:
            self.summary.setText(
                f"1 snapshot — {format_bytes(ordered[0].total_size)}")
        else:
            overall = ordered[-1].total_size - ordered[0].total_size
            self.summary.setText(
                f"{len(ordered)} snapshots — {format_bytes(overall)} "
                f"since {ordered[0].when}")

    def show_comparison(self, text: str) -> None:
        self.summary.setText(text)

    def _chosen(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.snapshot_chosen.emit(path)
