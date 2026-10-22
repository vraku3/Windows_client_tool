"""PerfMon chart styling with explicit gaps for missing measurements."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from core.semantic_colors import semantic
from modules.perfmon.perfmon_charts import _QtLineChart


class SensorChart(_QtLineChart):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, int(w), int(h), QColor(self.colours['background']))
        painter.setPen(QColor(self.colours['title']))
        painter.drawText(45, 16, f'{self._title} ({self._y_label}) - last 5 min')
        values = [v for v in self._data if v is not None]
        maximum = self._y_range[1] if self._y_range else max(1, max(values, default=1) * 1.1)
        left, top, bottom = 45, 28, h - 23
        width, height = max(1, w - left - 12), max(1, bottom - top)
        for i in range(5):
            y = int(bottom - height * i / 4)
            painter.setPen(QColor(self.colours['grid']))
            painter.drawLine(int(left), y, int(w - 12), y)
            painter.setPen(QColor(self.colours['axis']))
            painter.drawText(2, int(y + 4), f'{maximum * i / 4:.0f}')
        if self._threshold is not None:
            y = int(bottom - min(1, self._threshold / maximum) * height)
            painter.setPen(QPen(QColor(semantic('error')), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(left), y, int(w - 12), y)
        painter.setPen(QPen(self._color, 2))
        previous = None
        for i, value in enumerate(self._data):
            if value is None:
                previous = None
                continue
            point = (int(left + width * i / max(1, len(self._data) - 1)),
                     int(bottom - min(1, value / maximum) * height))
            if previous is not None:
                painter.drawLine(*previous, *point)
            previous = point
        painter.setPen(QColor(self.colours['axis']))
        painter.drawText(int(left), int(h - 5), 'Missing readings leave gaps; each chart has its own scale')
        painter.end()
