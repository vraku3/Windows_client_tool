"""A fan-curve graph you drag. Temperature across, duty up.

Dragging is constrained so the curve can never become one `curves.validate`
would reject: a point stays strictly between its neighbours' temperatures,
and its duty between theirs (the fan may never slow as it gets hotter).
Double-click adds a point, right-click removes one (two is the minimum).
"""
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from core.semantic_colors import semantic

T_MIN, T_MAX = 20.0, 100.0
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 42, 14, 14, 30
HANDLE = 6


class CurveEditor(QWidget):
    points_changed = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points: List[Tuple[float, float]] = []
        self._floor = 0.0
        self._critical: Optional[float] = None
        self._live_temp: Optional[float] = None
        self._live_duty: Optional[float] = None
        self._drag: Optional[int] = None
        self.setMinimumSize(420, 260)
        self.setMouseTracking(True)

    # ---- data ------------------------------------------------------------------------

    def set_curve(self, points, floor: float, critical: Optional[float]) -> None:
        self._points = [(float(t), float(d)) for t, d in points]
        self._floor, self._critical = floor, critical
        self.update()

    def set_live(self, temp: Optional[float], duty: Optional[float]) -> None:
        self._live_temp, self._live_duty = temp, duty
        self.update()

    def points(self) -> List[Tuple[float, float]]:
        return list(self._points)

    # ---- geometry --------------------------------------------------------------------

    def _plot(self) -> QRectF:
        return QRectF(MARGIN_L, MARGIN_T, self.width() - MARGIN_L - MARGIN_R,
                      self.height() - MARGIN_T - MARGIN_B)

    def _to_px(self, t: float, d: float) -> QPointF:
        r = self._plot()
        return QPointF(r.left() + (t - T_MIN) / (T_MAX - T_MIN) * r.width(),
                       r.bottom() - d / 100.0 * r.height())

    def _from_px(self, x: float, y: float) -> Tuple[float, float]:
        r = self._plot()
        t = T_MIN + (x - r.left()) / max(1.0, r.width()) * (T_MAX - T_MIN)
        d = (r.bottom() - y) / max(1.0, r.height()) * 100.0
        return round(t), round(d)

    def _hit(self, pos) -> Optional[int]:
        for i, (t, d) in enumerate(self._points):
            p = self._to_px(t, d)
            if abs(p.x() - pos.x()) <= HANDLE + 3 and abs(p.y() - pos.y()) <= HANDLE + 3:
                return i
        return None

    def _clamped(self, i: int, t: float, d: float) -> Tuple[float, float]:
        lo_t = self._points[i - 1][0] + 1 if i > 0 else T_MIN
        hi_t = self._points[i + 1][0] - 1 if i < len(self._points) - 1 else T_MAX
        lo_d = self._points[i - 1][1] if i > 0 else 0.0
        hi_d = self._points[i + 1][1] if i < len(self._points) - 1 else 100.0
        return min(max(t, lo_t), hi_t), min(max(d, lo_d), hi_d)

    # ---- mouse ------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        i = self._hit(event.position())
        if event.button() == Qt.MouseButton.RightButton and i is not None and len(self._points) > 2:
            del self._points[i]
            self._emit()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._drag = i
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._hit(event.position()) is not None
                           else Qt.CursorShape.ArrowCursor)
            super().mouseMoveEvent(event)
            return
        t, d = self._from_px(event.position().x(), event.position().y())
        self._points[self._drag] = self._clamped(self._drag, t, d)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self._emit()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        t, d = self._from_px(event.position().x(), event.position().y())
        event.accept()
        if any(abs(t - pt) < 2 for pt, _ in self._points):
            return
        self._points.append((t, d))
        self._points.sort()
        i = self._points.index((t, d))
        self._points[i] = self._clamped(i, t, d)
        self._emit()

    def _emit(self) -> None:
        self.update()
        self.points_changed.emit(self.points())

    # ---- painting ---------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_grid(p)
        self._paint_zones(p)
        self._paint_curve(p)
        self._paint_live(p)

    def _paint_grid(self, p: QPainter) -> None:
        r = self._plot()
        text = self.palette().text().color()
        faint = QColor(text)
        faint.setAlpha(45)
        p.fillRect(r, QColor(0, 0, 0, 25))
        for t in range(int(T_MIN), int(T_MAX) + 1, 10):
            x = int(self._to_px(t, 0).x())
            p.setPen(QPen(faint, 1))
            p.drawLine(x, int(r.top()), x, int(r.bottom()))
            p.setPen(text)
            p.drawText(x - 12, int(r.bottom()) + 16, f"{t}°")
        for d in range(0, 101, 25):
            y = int(self._to_px(T_MIN, d).y())
            p.setPen(QPen(faint, 1))
            p.drawLine(int(r.left()), y, int(r.right()), y)
            p.setPen(text)
            p.drawText(4, y + 4, f"{d}%")

    def _paint_zones(self, p: QPainter) -> None:
        r = self._plot()
        if self._floor > 0:
            y = self._to_px(T_MIN, self._floor).y()
            shade = QColor(semantic("warning"))
            shade.setAlpha(30)
            p.fillRect(QRectF(r.left(), y, r.width(), r.bottom() - y), shade)
        if self._critical is not None and T_MIN <= self._critical <= T_MAX:
            x = int(self._to_px(self._critical, 0).x())
            p.setPen(QPen(QColor(semantic("error")), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(x, int(r.top()), x, int(r.bottom()))
            p.drawText(x + 4, int(r.top()) + 12, "100%")

    def _paint_curve(self, p: QPainter) -> None:
        if not self._points:
            return
        r = self._plot()
        first = self._to_px(*self._points[0])
        path = QPainterPath(QPointF(r.left(), first.y()))
        for t, d in self._points:
            path.lineTo(self._to_px(t, d))
        path.lineTo(QPointF(r.right(), self._to_px(*self._points[-1]).y()))
        colour = QColor(semantic("info"))
        p.setPen(QPen(colour, 2))
        p.drawPath(path)
        p.setBrush(colour)
        for t, d in self._points:
            c = self._to_px(t, d)
            p.drawEllipse(c, HANDLE, HANDLE)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_live(self, p: QPainter) -> None:
        if self._live_temp is None:
            return
        r = self._plot()
        x = int(self._to_px(min(max(self._live_temp, T_MIN), T_MAX), 0).x())
        p.setPen(QPen(QColor(semantic("success")), 1.5))
        p.drawLine(x, int(r.top()), x, int(r.bottom()))
        label = f"{self._live_temp:.1f} °C"
        if self._live_duty is not None:
            dot = self._to_px(min(max(self._live_temp, T_MIN), T_MAX), self._live_duty)
            p.setBrush(QColor(semantic("success")))
            p.drawEllipse(dot, 4, 4)
            p.setBrush(Qt.BrushStyle.NoBrush)
            label += f"  →  {self._live_duty:.0f}%"
        p.drawText(min(x + 6, int(r.right()) - 110), int(r.top()) + 26, label)
