"""Dragging a monitor on the arrangement map has to be doable by a hand.

User report (2026-09-24): moving a monitor from one end to the other was
"really hard" and "snaps all over the place", and Esc did nothing. Causes:

* The map was scaled to just fit the monitors, so there was no empty space to
  drop one BEYOND either end.
* The dragged monitor itself was shoved around by overlap resolution while the
  mouse was still moving, so it jumped under the hand.
* There was no way to abandon a drag.
"""
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent

from modules.monitor_control import _arrangement_geometry as geo
from modules.monitor_control import view_model as vm
from modules.monitor_control._arrangement_canvas import ArrangementCanvas


def _view(target_id, name, pos, res=(2560, 1440)):
    return vm.MonitorView(
        target_id=target_id, name=name, connector="HDMI", adapter="GPU",
        active=True, resolution=res, position=pos, refresh_hz=144.0,
        rates_at_resolution=(144.0,), native_resolution=res,
        device_name=rf"\\.\DISPLAY{target_id}")


def _canvas(qapp):
    canvas = ArrangementCanvas()
    canvas.resize(900, 320)
    canvas.show()
    canvas.set_views([_view(1, "Dell", (0, 0)),
                      _view(2, "Gigabyte", (2560, 0)),
                      _view(3, "LG", (5120, 0), (2560, 1080))])
    canvas._rebuild_transform()
    return canvas


def _mouse(canvas, kind, point, held=Qt.MouseButton.LeftButton,
           button=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, point, canvas.mapToGlobal(point), button, held,
                       Qt.KeyboardModifier.NoModifier)


def _centre(canvas, target):
    x, y, w, h = geo.to_canvas(canvas._rects[target], canvas._transform)
    return QPointF(x + w / 2, y + h / 2), w, h


def _press(canvas, target):
    start, w, h = _centre(canvas, target)
    canvas.mousePressEvent(_mouse(canvas, QEvent.Type.MouseButtonPress, start))
    return start, w, h


def _move(canvas, point):
    canvas.mouseMoveEvent(_mouse(canvas, QEvent.Type.MouseMove, point,
                                 held=Qt.MouseButton.LeftButton,
                                 button=Qt.MouseButton.NoButton))


def _release(canvas, point):
    canvas.mouseReleaseEvent(_mouse(canvas, QEvent.Type.MouseButtonRelease,
                                    point, held=Qt.MouseButton.NoButton))


def test_escape_cancels_a_drag_and_nothing_is_emitted(qapp):
    canvas = _canvas(qapp)
    got = []
    canvas.moved.connect(lambda *a: got.append(a))
    start, w, _h = _press(canvas, 1)
    _move(canvas, QPointF(start.x() + 2 * w, start.y()))

    canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                   Qt.KeyboardModifier.NoModifier))
    assert canvas._dragging is None and canvas._drag_rect is None
    _release(canvas, QPointF(start.x() + 2 * w, start.y()))
    assert got == []
    assert canvas._rects[1] == (0, 0, 2560, 1440)      # never moved


def test_escape_with_no_drag_is_left_for_the_parent(qapp):
    canvas = _canvas(qapp)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                      Qt.KeyboardModifier.NoModifier)
    canvas.keyPressEvent(event)
    assert canvas._dragging is None


def test_losing_focus_mid_drag_cancels_it(qapp):
    from PyQt6.QtGui import QFocusEvent
    canvas = _canvas(qapp)
    got = []
    canvas.moved.connect(lambda *a: got.append(a))
    start, w, _h = _press(canvas, 1)
    _move(canvas, QPointF(start.x() + 2 * w, start.y()))
    canvas.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    _release(canvas, start)
    assert got == [] and canvas._dragging is None


def test_a_second_mouse_button_mid_drag_cancels_it(qapp):
    canvas = _canvas(qapp)
    start, w, _h = _press(canvas, 1)
    _move(canvas, QPointF(start.x() + 2 * w, start.y()))
    canvas.mousePressEvent(_mouse(canvas, QEvent.Type.MouseButtonPress, start,
                                  held=Qt.MouseButton.LeftButton
                                  | Qt.MouseButton.RightButton,
                                  button=Qt.MouseButton.RightButton))
    assert canvas._dragging is None


def test_the_dragged_monitor_follows_the_mouse_even_over_a_neighbour(qapp):
    """It is drawn where the mouse has it; only the LANDING preview avoids
    the neighbour. Before, the monitor itself jumped to the nearest free
    side as the pointer crossed each one."""
    canvas = _canvas(qapp)
    start, w, _h = _press(canvas, 1)
    _move(canvas, QPointF(start.x() + 1.2 * w, start.y()))
    others = [r for t, r in canvas._rects.items() if t != 1]
    assert any(geo.overlap(canvas._drag_rect, o) for o in others)
    assert not any(geo.overlap(canvas._landing, o) for o in others)


def test_there_is_room_to_drop_beyond_the_last_monitor(qapp):
    """The leftmost display can be dragged past the rightmost and land after
    it -- the map keeps spare room beyond both ends for exactly this."""
    canvas = _canvas(qapp)
    got = []
    canvas.moved.connect(lambda t, x, y: got.append((t, x, y)))
    start, w, _h = _press(canvas, 1)
    right_end = geo.to_canvas((7680, 0, 2560, 1440), canvas._transform)
    end = QPointF(right_end[0] + right_end[2] / 2, start.y())
    assert 0 <= end.x() <= canvas.width(), "the far-right slot is off the canvas"
    _move(canvas, end)
    _release(canvas, end)
    assert got and got[0][0] == 1 and got[0][1] >= 7680 - 400


def test_a_drag_that_ends_where_it_began_emits_nothing(qapp):
    canvas = _canvas(qapp)
    got = []
    canvas.moved.connect(lambda *a: got.append(a))
    start, _w, _h = _press(canvas, 1)
    _move(canvas, QPointF(start.x() + 1, start.y()))
    _release(canvas, start)
    assert got == []


def test_the_transform_does_not_change_during_a_drag(qapp):
    """A map that rescales under the hand is what makes a drag feel like it
    snaps all over."""
    canvas = _canvas(qapp)
    before = canvas._transform
    start, w, _h = _press(canvas, 1)
    _move(canvas, QPointF(start.x() + 3 * w, start.y() + 40))
    canvas._rebuild_transform()
    assert canvas._transform == before
