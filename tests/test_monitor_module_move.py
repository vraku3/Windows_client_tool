r"""`ArrangementCanvas.moved` was never connected to anything.

Real bug (2026-09-23, user-reported): dragging a monitor in the arrangement
map to match how the monitors sit physically on the desk visually snapped
the icon into its new spot, but never told Windows -- the canvas only draws
and tracks the drag, it does not write. The next `refresh_data()` would
have silently put it back too, since `set_views()` rebuilds `_rects` from
each view's real (unchanged) position.

`_do_move_monitor` is now what `moved` is wired to. No real hardware:
`display_writes.set_position` is monkeypatched.

Same "no countdown" reasoning as `_do_toggle_monitor` (see
test_monitor_module_toggle.py): only `DM_POSITION` changes here, never
resolution or refresh, so this can't leave a monitor showing no signal --
there's nothing for the revert countdown to protect against.
"""
from __future__ import annotations

from modules.monitor_control import monitor_module as mm
from modules.monitor_control import view_model as vm


def _view(target_id, name, pos, res=(2560, 1440)):
    return vm.MonitorView(
        target_id=target_id, name=name, connector="HDMI", adapter="GPU",
        active=True, resolution=res, position=pos,
        refresh_hz=144.0, rates_at_resolution=(144.0,),
        native_resolution=res, device_name=rf"\\.\DISPLAY{target_id}")


def _three():
    """The real desk: Dell primary at the origin, two more to its right."""
    return [_view(1, "Dell", (0, 0)),
            _view(2, "Gigabyte", (2560, 0)),
            _view(3, "LG", (5120, 0), (2560, 1080))]


def _module(views):
    module = mm.MonitorControlModule()
    module.app = type("App", (), {"thread_pool": None})()
    module.create_widget()
    module._views = views
    return module


def _capture(monkeypatch, module, result=(True, "")):
    calls = []
    monkeypatch.setattr(mm.dw, "set_layout",
                        lambda changes: (calls.append(dict(changes)), result)[1])
    monkeypatch.setattr(module, "refresh_data", lambda: None)
    return calls


def test_the_canvas_moved_signal_is_actually_connected():
    """The exact shape of the original bug: nothing listened."""
    module = _module(_three())
    assert module._canvas.receivers(module._canvas.moved) > 0


def test_a_move_never_starts_a_countdown(monkeypatch):
    module = _module(_three())
    _capture(monkeypatch, module)
    started = []
    monkeypatch.setattr(mm.guard, "RevertCountdown",
                        lambda *a, **k: started.append(1))
    module._do_move_monitor(2, 2560, 1440)
    assert started == []


def test_moving_a_secondary_writes_only_what_changed(monkeypatch):
    module = _module(_three())
    calls = _capture(monkeypatch, module)
    module._do_move_monitor(2, 2560, 300)          # nudge Gigabyte down
    assert calls == [{2: (2560, 300)}]


def test_dragging_the_primary_right_rebases_everyone_on_it(monkeypatch):
    """The reported bug. Dell is the primary at (0,0); dropping it to the
    right of the LG means it is no longer at the origin, so Windows needs it
    kept at (0,0) and the others moved the other way -- one write of the whole
    layout, not a lone position Windows refuses."""
    module = _module(_three())
    calls = _capture(monkeypatch, module)
    module._do_move_monitor(1, 7680, 0)
    assert len(calls) == 1
    assert calls[0] == {2: (-5120, 0), 3: (-2560, 0)}


def test_a_drop_that_overlaps_is_refused_and_the_drawing_snaps_back(monkeypatch):
    module = _module(_three())
    calls = _capture(monkeypatch, module)
    reset = []
    monkeypatch.setattr(module._canvas, "set_views",
                        lambda views: reset.append(1))
    module._do_move_monitor(2, 1000, 0)             # on top of the Dell
    assert calls == []
    assert "overlap" in module._status.text()
    assert reset == [1]


def test_a_move_to_where_it_already_is_writes_nothing(monkeypatch):
    module = _module(_three())
    calls = _capture(monkeypatch, module)
    module._do_move_monitor(2, 2560, 0)
    assert calls == []


def test_a_refused_write_says_why_and_snaps_the_drawing_back(monkeypatch):
    module = _module(_three())
    calls = _capture(monkeypatch, module, (False, "the new layout was refused"))
    refreshed = []
    monkeypatch.setattr(module, "refresh_data", lambda: refreshed.append(1))
    reset = []
    monkeypatch.setattr(module._canvas, "set_views",
                        lambda views: reset.append(1))
    module._do_move_monitor(2, 2560, 300)
    assert "position not changed" in module._status.text()
    assert "refused" in module._status.text()
    assert refreshed == [] and reset == [1]


def test_an_unknown_target_is_ignored_not_crashed():
    module = _module(_three())
    module._do_move_monitor(9999, 0, 0)  # no matching view -- must not raise


def test_a_click_without_moving_does_not_write_anything(qapp):
    """Selecting a monitor must not count as moving it: every `moved` is a
    write to Windows, and a write is a visible flicker."""
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from modules.monitor_control import _arrangement_geometry as geo

    module = _module(_three())
    canvas = module._canvas
    canvas.resize(800, 300)
    canvas.set_views(module._views)
    canvas._rebuild_transform()
    got = []
    canvas.moved.connect(lambda t, x, y: got.append((t, x, y)))
    x, y, w, h = geo.to_canvas(canvas._rects[2], canvas._transform)
    p = QPointF(x + w / 2, y + h / 2)

    def ev(kind):
        return QMouseEvent(kind, p, canvas.mapToGlobal(p),
                           Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev(QEvent.Type.MouseButtonPress))
    canvas.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease))
    assert got == []


def test_dropping_the_primary_onto_another_monitor_lands_beside_it(qapp):
    """The drag the user reported as 'hard': a real drop, with the neighbour
    under it, previews and reports a clear slot instead of an overlap."""
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from modules.monitor_control import _arrangement_geometry as geo

    module = _module(_three())
    canvas = module._canvas
    canvas.resize(900, 320)
    canvas.set_views(module._views)
    canvas._rebuild_transform()
    got = []
    canvas.moved.connect(lambda t, x, y: got.append((t, x, y)))
    x, y, w, h = geo.to_canvas(canvas._rects[1], canvas._transform)
    start = QPointF(x + w / 2, y + h / 2)
    end = QPointF(start.x() + w * 1.3, start.y())      # well onto the Gigabyte

    def ev(kind, p, btn=Qt.MouseButton.LeftButton, held=Qt.MouseButton.LeftButton):
        return QMouseEvent(kind, p, canvas.mapToGlobal(p), btn, held,
                           Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev(QEvent.Type.MouseButtonPress, start))
    canvas.mouseMoveEvent(ev(QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton))
    canvas.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, end,
                                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert len(got) == 1
    target, nx, ny = got[0]
    rects = {t: r for t, r in canvas._rects.items()}
    assert not any(geo.overlap(rects[target], r)
                   for t, r in rects.items() if t != target)
