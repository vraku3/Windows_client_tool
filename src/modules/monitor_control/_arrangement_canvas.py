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

#: How close, in ON-SCREEN pixels, a dragged monitor must come to a
#: neighbour's edge before it snaps. Measured in canvas pixels because that is
#: what a hand can judge: the map is drawn at roughly 1/30th scale, so the old
#: fixed 32 DESKTOP pixels was about one canvas pixel -- no snap at all, and a
#: drop that landed a hair off the neighbour.
_SNAP_CANVAS_PX = 9


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
        self._drag_start_rect: Optional[Tuple[int, int, int, int]] = None
        #: Where the dragged monitor is drawn: follows the mouse freely.
        self._drag_rect: Optional[Tuple[int, int, int, int]] = None
        #: Where it will really land if released now (never on another monitor).
        self._landing: Optional[Tuple[int, int, int, int]] = None
        self.setMinimumHeight(240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        """Fit the layout with spare room around it.

        Fitted to the monitors alone, the map fills the canvas edge to edge
        and there is nowhere to drop a monitor BEYOND either end -- moving
        the leftmost display to the far right was close to impossible. So the
        fit is padded by one monitor width each side and half a height above
        and below. The transform is stable for a whole drag because the
        dragged monitor's original rectangle, not its preview, is what is fitted.
        """
        rects = list(self._rects.values())
        if rects:
            box = geo.bounding_box(rects)
            pad_x = max(r[2] for r in rects)
            pad_y = max(r[3] for r in rects) // 2
            rects = rects + [(box[0] - pad_x, box[1] - pad_y, 1, 1),
                             (box[0] + box[2] + pad_x, box[1] + box[3] + pad_y, 1, 1)]
        self._transform = geo.fit(rects, canvas=self._map_area(), margin=14)

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
                if target_id == self._dragging:
                    continue                 # drawn last, on top
                self._draw_monitor(painter, by_id[target_id],
                                   geo.to_canvas(rect, self._transform),
                                   active=True)
            self._paint_drag(painter, by_id)

        parked = [v for v in self._views if v.target_id not in self._rects]
        if parked:
            self._draw_parked(painter, parked)

    def _paint_drag(self, painter, by_id) -> None:
        """The monitor being dragged: where it was, where the mouse has it,
        and where it will land -- three different places, all shown."""
        if self._dragging is None or self._drag_rect is None:
            return
        view = by_id.get(self._dragging)
        if view is None:
            return
        ghost_pen = QPen(QColor(chrome("text_muted")))
        ghost_pen.setStyle(Qt.PenStyle.DotLine)
        ghost_pen.setWidth(2)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(ghost_pen)                       # where it started
        x, y, w, h = geo.to_canvas(self._drag_start_rect, self._transform)
        painter.drawRoundedRect(QRectF(x, y, w, h), 6, 6)

        if self._landing is not None and self._landing != self._drag_rect:
            land_pen = QPen(QColor(semantic("success")))   # where it will land
            land_pen.setStyle(Qt.PenStyle.DashLine)
            land_pen.setWidth(3)
            painter.setPen(land_pen)
            x, y, w, h = geo.to_canvas(self._landing, self._transform)
            painter.drawRoundedRect(QRectF(x, y, w, h), 6, 6)

        painter.setOpacity(0.8)                         # what the mouse holds
        self._draw_monitor(painter, view,
                           geo.to_canvas(self._drag_rect, self._transform),
                           active=True)
        painter.setOpacity(1.0)

    def _cancel_drag(self) -> None:
        """Abandon a drag: nothing is emitted and nothing has changed."""
        if self._dragging is None:
            return
        self._dragging = None
        self._drag_start_rect = None
        self._drag_rect = None
        self._landing = None
        self.update()

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        if event.key() == Qt.Key.Key_Escape and self._dragging is not None:
            event.accept()
            self._cancel_drag()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 - Qt naming
        self._cancel_drag()      # a drag must not survive losing the window
        super().focusOutEvent(event)

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
        if event.button() != Qt.MouseButton.LeftButton:
            self._cancel_drag()      # a second button mid-drag means never mind
            return
        self.setFocus()              # so Esc reaches keyPressEvent
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
            self._drag_start_rect = self._rects[target_id]
            self._drag_rect = self._rects[target_id]
            self._landing = self._rects[target_id]
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt naming
        if self._dragging is None or self._transform is None:
            event.ignore()
            return
        event.accept()
        rect = self._drag_start_rect
        corner = (event.position().x() - self._drag_offset[0],
                  event.position().y() - self._drag_offset[1])
        desktop = geo.to_desktop_point(corner, self._transform)
        others = [r for tid, r in self._rects.items() if tid != self._dragging]
        threshold = max(1, int(_SNAP_CANVAS_PX / self._transform.scale))
        # The monitor follows the mouse (only lightly snapped to edges), so it
        # never jumps under the hand; the overlap-free landing spot is a
        # separate preview, not where the monitor is drawn.
        self._drag_rect = geo.snap(
            (int(desktop[0]), int(desktop[1]), rect[2], rect[3]),
            others, threshold)
        self._landing = geo.resolve_overlaps(self._drag_rect, others)
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt naming
        if self._dragging is None:
            event.ignore()
            return
        event.accept()
        target = self._dragging
        started, landing = self._drag_start_rect, self._landing
        self._cancel_drag()
        if started is None or landing is None or landing[:2] == started[:2]:
            return  # a click, or a drag that ended where it began
        self._rects[target] = landing
        self.update()
        self.moved.emit(target, landing[0], landing[1])
