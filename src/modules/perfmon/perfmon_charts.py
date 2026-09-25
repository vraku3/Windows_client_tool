import logging
from collections import deque

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QPainterPath

logger = logging.getLogger(__name__)

# pyqtgraph is NOT bundled — use a custom Qt-based chart widget instead.
# This avoids any import complications in the frozen portable exe.
HAS_PYQTGRAPH = False  # intentionally False — always use Qt chart
pg = None

CHART_COLORS = {
    "cpu": "#00AAFF",
    "memory": "#AA44FF",
    "disk": "#FFAA00",
    "network": "#44FF88",
}


#: Everything `_QtLineChart.paintEvent` puts on screen, per theme. A custom
#: painter never sees the application stylesheet, so these cannot live in the
#: .qss files -- but they must still change with it, which is what
#: `ThemeManager.theme_changed` is for.
#:
#: The dark axis colour is #b0b0b0 and not the #888888 it was: #888888 on
#: #252525 measures 4.27:1, under the 4.5:1 the rest of the app is held to.
CHART_PALETTES = {
    "dark": {
        "background": "#252525",
        "title": "#e0e0e0",
        "axis": "#b0b0b0",
        "grid": "#3a3a3a",
        "value": "#ffffff",
    },
    "light": {
        "background": "#ffffff",
        "title": "#1e1e1e",
        "axis": "#5a5a5a",
        "grid": "#dcdcdc",
        "value": "#1e1e1e",
    },
}


class _QtLineChart(QWidget):
    """A real-time rolling line chart drawn with QPainter — no external deps."""

    MAX_POINTS = 300  # 5 minutes at 1s interval

    def __init__(self, title: str, y_label: str, color: str = "#00AAFF",
                 y_range: tuple = None, parent=None):
        super().__init__(parent)
        self._title = title
        self._y_label = y_label
        self._color = QColor(color)
        self._y_range = y_range  # (min, max) or None for auto-scale
        self._data: deque = deque(maxlen=self.MAX_POINTS)
        self._times: deque = deque(maxlen=self.MAX_POINTS)
        self._curve: QPainterPath = QPainterPath()
        self.setMinimumHeight(140)
        #: Dark until told otherwise -- never an empty dict, so paintEvent has
        #: no uncoloured state to guard against.
        self.colours = CHART_PALETTES["dark"]
        self._apply_background()

    def set_theme(self, theme: str) -> None:
        """Follow `theme`, repainting immediately. Unknown names stay put."""
        palette = CHART_PALETTES.get(theme)
        if palette is None or palette is self.colours:
            return
        self.colours = palette
        self._apply_background()
        self.update()

    def _apply_background(self) -> None:
        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor(self.colours["background"]))
        self.setPalette(p)

    def add_point(self, value: float) -> None:
        self._data.append(value)
        self._times.append(len(self._data))
        self.update()

    def _y_min(self) -> float:
        if not self._data:
            return 0.0
        return min(self._data)

    def _y_max(self) -> float:
        if not self._data:
            return 100.0
        return max(self._data)

    def _to_xy(self) -> list[QPointF]:
        """Convert data to QPointF list in widget coordinates."""
        if not self._data:
            return []
        w, h = self.width(), self.height()
        pad = 36  # left (y-axis) + 8 px padding
        right_pad = 8
        chart_w = w - pad - right_pad
        chart_h = h - 32  # top title + bottom x-axis

        y_min = self._y_range[0] if self._y_range else self._y_min()
        y_max = self._y_range[1] if self._y_range else self._y_max()
        if y_max == y_min:
            y_max = y_min + 1

        y_range = y_max - y_min
        points = []
        n = len(self._data)
        for i, val in enumerate(self._data):
            x = pad + (i / max(n - 1, 1)) * chart_w
            y = (h - 16) - ((val - y_min) / y_range) * chart_h
            points.append(QPointF(x, y))
        return points

    def paintEvent(self, event) -> None:
        w, h = self.width(), self.height()
        pad = 36
        right_pad = 8
        chart_w = w - pad - right_pad
        chart_h = h - 32
        chart_bottom = h - 16
        chart_top = 16

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        painter.fillRect(0, 0, int(w), int(h), QColor(self.colours["background"]))

        # Title
        painter.setPen(QPen(QColor(self.colours["title"]), 1))
        title_font = QFont("Segoe UI", 9)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(int(pad), 12, self._title)

        # Y-axis label
        lbl_font = QFont("Segoe UI", 7)
        painter.setFont(lbl_font)
        painter.setPen(QPen(QColor(self.colours["axis"]), 1))
        # Halfway between two ticks, not on one: at the 50 tick the unit
        # was drawn over the tick's own number ("5%0").
        painter.drawText(2, int(chart_bottom - chart_h * 0.375), self._y_label)

        # Grid lines
        grid_pen = QPen(QColor(self.colours["grid"]), 1)
        painter.setPen(grid_pen)
        for i in range(5):
            y = chart_top + (chart_h / 4) * i
            painter.drawLine(pad, int(y), int(pad + chart_w), int(y))

        # Determine Y scale
        y_min = self._y_range[0] if self._y_range else self._y_min()
        y_max = self._y_range[1] if self._y_range else self._y_max()
        if y_max == y_min:
            y_max = y_min + 1
        y_range = y_max - y_min

        # Y-axis tick labels
        painter.setFont(QFont("Segoe UI", 7))
        painter.setPen(QPen(QColor(self.colours["axis"]), 1))
        for i in range(5):
            frac = i / 4
            y_px = chart_bottom - frac * chart_h
            val = y_min + frac * y_range
            painter.drawText(1, int(y_px + 3), f"{val:.0f}")
            painter.drawLine(pad - 3, int(y_px), int(pad), int(y_px))

        # Draw the line
        points = self._to_xy()
        if len(points) < 2:
            painter.end()
            return

        # Filled area under curve
        fill_path = QPainterPath()
        fill_path.moveTo(points[0])
        for pt in points[1:]:
            fill_path.lineTo(pt)
        fill_path.lineTo(int(points[-1].x()), chart_bottom)
        fill_path.lineTo(int(points[0].x()), chart_bottom)
        fill_path.closeSubpath()
        fill_color = QColor(self._color)
        fill_color.setAlpha(40)
        painter.fillPath(fill_path, QBrush(fill_color))

        # Line
        line_pen = QPen(self._color, 2)
        painter.setPen(line_pen)
        line_path = QPainterPath()
        line_path.moveTo(points[0])
        for pt in points[1:]:
            line_path.lineTo(pt)
        painter.drawPath(line_path)

        # Current value indicator dot
        last = points[-1]
        dot_pen = QPen(self._color, 1)
        painter.setPen(dot_pen)
        painter.setBrush(QBrush(self._color))
        painter.drawEllipse(last, 3, 3)

        # Value text
        val_text = f"{self._data[-1]:.1f}"
        painter.setPen(QPen(QColor(self.colours["value"]), 1))
        # Beside the dot -- but the newest point is at the right edge, where
        # text to its right was clipped away, so put it on the left there.
        text_w = painter.fontMetrics().horizontalAdvance(val_text)
        x = last.x() + 6
        if x + text_w > self.width():
            x = last.x() - text_w - 6
        painter.drawText(int(x), int(last.y() - 4), val_text)


class RealTimeChart(QWidget):
    """A real-time line chart with a rolling window and dark styling."""

    MAX_POINTS = 300  # 5 minutes at 1s interval

    def __init__(self, title: str, y_label: str, color: str = "#00AAFF",
                 y_range: tuple = None, parent=None):
        super().__init__(parent)
        self._title = title
        self._color = color
        self._data = deque(maxlen=self.MAX_POINTS)
        self._times = deque(maxlen=self.MAX_POINTS)
        self._plot_widget: QWidget | None = None
        self._curve = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._plot_widget = _QtLineChart(title, y_label, color, y_range)
        layout.addWidget(self._plot_widget)

    def add_point(self, value: float) -> None:
        self._data.append(value)
        self._times.append(len(self._data))
        if self._plot_widget is not None:
            self._plot_widget.add_point(value)


class PerfMonDashboard(QWidget):
    """Dashboard with multiple real-time charts in tabs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self.cpu_chart = RealTimeChart(
            "CPU Usage", "%", color=CHART_COLORS["cpu"], y_range=(0, 100)
        )
        self._tabs.addTab(self.cpu_chart, "CPU")

        self.memory_chart = RealTimeChart(
            "Memory Usage", "%", color=CHART_COLORS["memory"], y_range=(0, 100)
        )
        self._tabs.addTab(self.memory_chart, "Memory")

        self.disk_chart = RealTimeChart(
            "Disk Activity", "%", color=CHART_COLORS["disk"], y_range=(0, 100)
        )
        self._tabs.addTab(self.disk_chart, "Disk")

        self.net_chart = RealTimeChart(
            "Network I/O", "KB/s", color=CHART_COLORS["network"]
        )
        self._tabs.addTab(self.net_chart, "Network")

    def update_from_snapshot(self, snapshot: dict) -> None:
        """Update all charts from a performance snapshot."""
        if "cpu_total" in snapshot:
            self.cpu_chart.add_point(snapshot["cpu_total"])
        if "memory_percent" in snapshot:
            self.memory_chart.add_point(snapshot["memory_percent"])
        if "disk_percent" in snapshot:
            self.disk_chart.add_point(snapshot["disk_percent"])
