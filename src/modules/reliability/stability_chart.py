"""A compact sparkline of Windows' own reliability stability index (0-10),
next to the Reliability tab's Refresh button.

Real data on THIS machine, read live 2026-09-29:
`Win32_ReliabilityStabilityMetrics` is HOURLY here (not the daily granularity
some docs describe) and the index fell from a flat 10.0 on 2026-09-04 to 4.4
by 2026-09-29 -- a three-week decline with no single hour dropping more than
~3 points. `reliability_analysis.summary_text` already reports the single
biggest one-hour drop (`find_dips`), but that undersells exactly the kind of
slow trend Windows' own Reliability Monitor draws as a literal chart. This
widget is that chart, in miniature, plus a hover tooltip stating the range.

Pure QPainter -- same choice as `ui/perf_graph.py` and
`modules/perfmon/perfmon_charts.py`, and for the same reason: no
pyqtgraph/matplotlib dependency in the frozen build. Every drawn coordinate
is cast to `int`: PyQt6 raises inside `paintEvent` on a float coordinate,
which is unrecoverable (`ui/perf_graph.py`'s own docstring, CLAUDE.md).

Colours come from `core.semantic_colors` (`semantic`/`chrome`), never a hex
literal here: `tests/test_no_frozen_colours.py` is a ratchet that only ever
goes down, and a QPainter canvas has no stylesheet to fall back on for
theme-following chrome, which is exactly what `chrome()` exists for.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import QSizePolicy, QWidget

from core.semantic_colors import chrome, semantic
from modules.reliability.reliability_analysis import Metric, sparkline_tooltip

#: Windows' own index is always 0..10, never rescaled to what happens to be loaded.
_CEILING = 10.0
_LABEL_WIDTH = 32


def _band_colour(value: Optional[float]) -> QColor:
    """Windows' own colour sense for the index: healthy / degraded / poor."""
    if value is None:
        return QColor(chrome("outline"))
    if value >= 7:
        return QColor(semantic("success"))
    if value >= 4:
        return QColor(semantic("warning"))
    return QColor(semantic("error"))


class StabilityChart(QWidget):
    """A fixed-size sparkline over the whole loaded stability-index history."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._metrics: List[Metric] = []
        self.setFixedHeight(26)
        self.setMinimumWidth(190)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip("No stability index history yet.")

    def set_metrics(self, metrics: List[Metric]) -> None:
        """Replace the plotted history. `metrics` need not be pre-sorted."""
        self._metrics = sorted(metrics or [], key=lambda m: m.start)
        self.setToolTip(sparkline_tooltip(self._metrics))
        self.update()

    def latest(self) -> Optional[float]:
        return self._metrics[-1].index if self._metrics else None

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        latest = self.latest()
        colour = _band_colour(latest)

        label_rect = QRect(rect.left(), rect.top(), _LABEL_WIDTH, rect.height())
        painter.setPen(colour)
        text = "n/a" if latest is None else f"{latest:.1f}"
        painter.drawText(label_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), text)

        plot_rect = QRect(rect.left() + _LABEL_WIDTH + 4, rect.top(),
                          rect.width() - _LABEL_WIDTH - 4, rect.height())
        self._paint_plot(painter, plot_rect, colour)
        painter.end()

    def _paint_plot(self, painter: QPainter, rect: QRect, colour: QColor) -> None:
        border = QColor(colour)
        border.setAlpha(80)
        painter.setPen(QPen(border, 1))
        painter.drawRect(rect)

        points = self._points(rect)
        if len(points) < 2:
            return
        fill = QColor(colour)
        fill.setAlpha(60)
        shape = QPolygon(points)
        shape.append(QPoint(points[-1].x(), int(rect.bottom())))
        shape.append(QPoint(points[0].x(), int(rect.bottom())))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPolygon(shape)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(colour, 2))
        painter.drawPolyline(QPolygon(points))

    def _points(self, rect: QRect) -> List[QPoint]:
        """One point per loaded metric, evenly spaced across the width.

        Spaced by INDEX, not by elapsed time: a gap in the WMI history (this
        machine has none, but the format does not guarantee it) would
        otherwise squeeze the rest of the series into a sliver.
        """
        if len(self._metrics) < 2:
            return []
        span = max(1, len(self._metrics) - 1)
        step = rect.width() / span
        points = []
        for i, m in enumerate(self._metrics):
            x = int(rect.left() + i * step)
            share = min(1.0, max(0.0, m.index / _CEILING))
            y = int(rect.bottom() - share * rect.height())
            points.append(QPoint(x, y))
        return points
