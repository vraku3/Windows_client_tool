r"""The monitor map: rectangles where the displays really are.

Drawing only. Every coordinate decision lives in `_arrangement_geometry`,
which is tested without a display — including the case this widget would
otherwise get wrong, a monitor placed left of the primary and therefore at
a negative x.

Inactive-but-connected monitors are parked in a strip below the map rather
than drawn on it: they have no position and no size, so there is nowhere
honest to put them, and inventing one would say they are somewhere they are
not.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from core.semantic_colors import chrome, semantic
from modules.monitor_control import _arrangement_geometry as geo

logger = logging.getLogger(__name__)

_PARKED_STRIP = 74


class ArrangementCanvas(QWidget):
    """Click to select a monitor; drag an active one to move it."""

    selected = pyqtSignal(int)          # target_id
    moved = pyqtSignal(int, int, int)   # target_id, x, y

    def __init__(self, parent=None):
        super().__init__(parent)
        self._views: List = []
        self._rects: Dict[int, Tuple[int, int, int, int]] = {}
        self._transform: Optional[geo.Transform] = None
        self._selected_id: Optional[int] = None
        self._dragging: Optional[int] = None
        self._drag_offset = (0.0, 0.0)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    # ── data ──

    def set_views(self, views) -> None:
        self._views = list(views)
        self._rects = {
            v.target_id: (v.position[0], v.position[1],
                          v.resolution[0], v.resolution[1])
            for v in self._views
            if v.active and v.position and v.resolution
        }
        if self._selected_id not in {v.target_id for v in self._views}:
            self._selected_id = None
        self.update()

    @property
    def selected_target(self) -> Optional[int]:
        return self._selected_id

    # ── geometry ──

    def _map_area(self) -> Tuple[int, int]:
        return self.width(), max(self.height() - _PARKED_STRIP, 40)

    def _rebuild_transform(self) -> None:
        self._transform = geo.fit(list(self._rects.values()),
                                  canvas=self._map_area(), margin=14)

    def _target_at(self, point) -> Optional[int]:
        if self._transform is None:
            return None
        for target_id, rect in self._rects.items():
            x, y, w, h = geo.to_canvas(rect, self._transform)
            if QRectF(x, y, w, h).contains(point):
                return target_id
        return None

    # ── painting ──

    def paintEvent(self, event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._rebuild_transform()

        if not self._views:
            painter.setPen(QColor(chrome("text_muted")))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No displays detected")
            return

        by_id = {v.target_id: v for v in self._views}
        if self._transform is not None:
            for target_id, rect in self._rects.items():
                self._draw_monitor(painter, by_id[target_id],
                                   geo.to_canvas(rect, self._transform),
                                   active=True)

        parked = [v for v in self._views if v.target_id not in self._rects]
        if parked:
            self._draw_parked(painter, parked)

    def _draw_monitor(self, painter, view, box, *, active):
        x, y, w, h = box
        rect = QRectF(x, y, w, h)
        chosen = view.target_id == self._selected_id

        if active:
            fill = QColor(chrome("surface_selected")) if chosen else QColor(chrome("surface"))
            edge = QColor(semantic("success")) if chosen else QColor(chrome("outline"))
        else:
            fill = QColor(chrome("surface_inactive"))
            edge = QColor(chrome("text_muted"))

        painter.setBrush(QBrush(fill))
        pen = QPen(edge)
        pen.setWidth(3 if chosen else 2)
        if not active:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 6, 6)

        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(chrome("text")) if active else QColor(chrome("text_muted")))
        painter.drawText(rect.adjusted(6, 6, -6, -6),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         view.name)

        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(chrome("text_muted")))
        if active and view.resolution:
            detail = (f"{view.resolution[0]}x{view.resolution[1]}\n"
                      f"{view.refresh_hz:g} Hz")
        else:
            detail = "not in use"
        painter.drawText(rect.adjusted(6, 6, -6, -6),
                         Qt.AlignmentFlag.AlignCenter, detail)

    def _draw_parked(self, painter, parked):
        """Connected monitors that are not on the desktop, below the map."""
        top = self.height() - _PARKED_STRIP + 8
        painter.setPen(QColor(chrome("text_muted")))
        painter.drawText(10, top - 4, "Connected, not in use:")

        width = 150
        for index, view in enumerate(parked):
            box = (12 + index * (width + 10), top + 6, width, 44)
            self._draw_monitor(painter, view, box, active=False)

    # ── interaction ──

    def mousePressEvent(self, event):  # noqa: N802 - Qt naming
        event.accept()
        target_id = self._target_at(event.position())
        if target_id is None:
            self._selected_id = None
            self.update()
            return
        self._selected_id = target_id
        self.selected.emit(target_id)
        if self._transform is not None:
            x, y, _w, _h = geo.to_canvas(self._rects[target_id], self._transform)
            self._drag_offset = (event.position().x() - x,
                                 event.position().y() - y)
            self._dragging = target_id
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt naming
        if self._dragging is None or self._transform is None:
            event.ignore()
            return
        event.accept()
        rect = self._rects[self._dragging]
        corner = (event.position().x() - self._drag_offset[0],
                  event.position().y() - self._drag_offset[1])
        desktop = geo.to_desktop_point(corner, self._transform)
        others = [r for tid, r in self._rects.items() if tid != self._dragging]
        snapped = geo.snap((int(desktop[0]), int(desktop[1]), rect[2], rect[3]),
                           others)
        self._rects[self._dragging] = snapped
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt naming
        if self._dragging is None:
            event.ignore()
            return
        event.accept()
        rect = self._rects[self._dragging]
        self.moved.emit(self._dragging, rect[0], rect[1])
        self._dragging = None
