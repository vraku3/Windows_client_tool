"""A thin histogram of the log over its own time span.

Where the records are, and where the failures are, in one glance above the
table. Clicking a spike jumps there.

Painted rather than charted: it is a hundred-odd rectangles, and pulling in a
plotting library for that would cost more than it saves. `perfmon_charts.py`
draws the same way and for the same reason -- and, like it, every coordinate
handed to QPainter is an `int`, because PyQt6 refuses a float.
"""

from modules.log_viewer.help_widgets import (
    explain_widget,
)
from math import sqrt

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from core.semantic_colors import semantic

#: Tall enough for a spike to be visible, short enough not to cost the table
#: a row of its own.
STRIP_HEIGHT = 44


def bar_height(count: int, busiest: int, height: int = STRIP_HEIGHT) -> int:
    """How tall a bucket's bar is drawn, on a SQUARE-ROOT scale.

    Linear scaling is what the arithmetic suggests and it is useless here.
    CBS writes in bursts: on the real archive one bucket held the
    overwhelming majority of 138,683 records, so every other bar rounded to
    zero pixels and the strip read as a single block with nothing around it.

    A square root keeps the ordering -- busier is still taller -- while
    leaving a bucket a thousandth the size of the busiest one visible. A
    bucket holding anything at all is never drawn shorter than one pixel;
    an empty one is drawn not at all.
    """
    if count <= 0 or busiest <= 0:
        return 0
    return max(int(height * sqrt(count / busiest)), 1)


class DensityStrip(QWidget):
    """Bars for the loaded span. Emits the moment that was clicked."""

    #: A datetime the pane can scroll to.
    moment_picked = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._buckets: list = []
        self.setFixedHeight(STRIP_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Where the records and the errors are. Click to go "
                        "to that moment.")
        explain_widget(self)

    def set_buckets(self, buckets) -> None:
        self._buckets = list(buckets or [])
        self.update()

    def has_data(self) -> bool:
        return bool(self._buckets)

    # ---- painting -------------------------------------------------------

    def paintEvent(self, event) -> None:
        if not self._buckets:
            return
        painter = QPainter(self)
        try:
            width = self.width()
            height = self.height()
            count = len(self._buckets)
            busiest = max(bucket.total for bucket in self._buckets) or 1

            # Errors keep their own colour; everything else is deliberately
            # quiet, so a spike of failures reads at a glance against the
            # ordinary traffic behind it.
            traffic = QColor(semantic("info"))
            traffic.setAlpha(110)
            failures = QColor(semantic("error"))

            for index, bucket in enumerate(self._buckets):
                # int() throughout: PyQt6's QPainter refuses floats.
                left = int(index * width / count)
                right = int((index + 1) * width / count)
                bar = max(right - left, 1)
                tall = bar_height(bucket.total, busiest, height)
                if tall:
                    painter.fillRect(QRect(left, height - tall, bar, tall),
                                     traffic)
                if bucket.errors:
                    # Same scale, so an error bar is a readable share of its
                    # own column rather than a hairline beside 4,000 Info
                    # records -- and never shorter than two pixels, because
                    # a single failure is the thing worth seeing.
                    tall = max(bar_height(bucket.errors, busiest, height), 2)
                    painter.fillRect(QRect(left, height - tall, bar, tall),
                                     failures)
        finally:
            painter.end()

    # ---- interaction ----------------------------------------------------

    def moment_at(self, x: int):
        """The time under `x`, or None when there is nothing drawn."""
        if not self._buckets or self.width() <= 0:
            return None
        index = int(x * len(self._buckets) / self.width())
        index = min(max(index, 0), len(self._buckets) - 1)
        return self._buckets[index].start

    def mousePressEvent(self, event) -> None:
        when = self.moment_at(int(event.position().x()))
        if when is not None:
            self.moment_picked.emit(when)
            event.accept()
        else:
            super().mousePressEvent(event)
