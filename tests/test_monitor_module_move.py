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


def _view(target_id=520, name="MO27Q28G"):
    return vm.MonitorView(
        target_id=target_id, name=name, connector="HDMI", adapter="GPU",
        active=True, resolution=(2560, 1440), position=(0, 0),
        refresh_hz=144.0, rates_at_resolution=(144.0,),
        native_resolution=(2560, 1440), device_name=r"\\.\DISPLAY2")


def _module():
    module = mm.MonitorControlModule()
    module.app = type("App", (), {"thread_pool": None})()
    module.create_widget()
    return module


def test_the_canvas_moved_signal_is_actually_connected():
    """The exact shape of the original bug: nothing listened."""
    module = _module()
    assert module._canvas.receivers(module._canvas.moved) > 0


def test_a_successful_move_never_starts_a_countdown(monkeypatch):
    module = _module()
    module._views = [_view()]

    monkeypatch.setattr(mm.dw, "set_position",
                        lambda device_name, x, y: (True, ""))
    started = []
    monkeypatch.setattr(mm.guard, "RevertCountdown",
                        lambda *a, **k: started.append(1))
    monkeypatch.setattr(module, "refresh_data", lambda: None)

    module._do_move_monitor(520, 2560, 0)
    assert started == []


def test_a_successful_move_passes_the_real_device_name_and_refreshes(monkeypatch):
    module = _module()
    module._views = [_view()]

    calls = []
    monkeypatch.setattr(
        mm.dw, "set_position",
        lambda device_name, x, y: (calls.append((device_name, x, y)), (True, ""))[1])
    refreshed = []
    monkeypatch.setattr(module, "refresh_data", lambda: refreshed.append(1))

    module._do_move_monitor(520, 2560, 0)
    assert calls == [(r"\\.\DISPLAY2", 2560, 0)]
    assert refreshed == [1]


def test_a_refused_move_says_why_and_snaps_the_drawing_back(monkeypatch):
    """Nothing changed, so `refresh_data()` never runs -- but the canvas
    already drew the drag at its dropped spot, so `set_views` re-runs with
    the last known REAL positions to undo just that, without touching the
    status message the way a full refresh would.
    """
    module = _module()
    view = _view()
    module._views = [view]

    monkeypatch.setattr(
        mm.dw, "set_position",
        lambda device_name, x, y: (False, "the new layout was refused"))
    refreshed = []
    monkeypatch.setattr(module, "refresh_data", lambda: refreshed.append(1))
    reset = []
    monkeypatch.setattr(module._canvas, "set_views",
                        lambda views: reset.append(list(views)))

    module._do_move_monitor(520, 2560, 0)
    assert "position not changed" in module._status.text()
    assert "refused" in module._status.text()
    assert refreshed == []
    assert reset == [[view]]


def test_an_unknown_target_is_ignored_not_crashed():
    module = _module()
    module._views = [_view()]
    module._do_move_monitor(9999, 0, 0)  # no matching view -- must not raise
