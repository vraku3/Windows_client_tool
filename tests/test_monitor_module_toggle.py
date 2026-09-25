r"""`_do_toggle_monitor` skips the revert countdown, and this is why.

The guard exists for a change that can leave someone looking at a screen
with no way back. Neither direction of connect/disconnect can do that:
connecting only adds a display, and `can_set_target_active` already refuses
the one unsafe disconnect (turning off the last active display) before this
handler's write ever runs. So a countdown here is friction with nothing
behind it — see the handler's own docstring.

No real hardware: `display_writes.set_target_active` is monkeypatched.
"""
from __future__ import annotations

import pytest

from modules.monitor_control import monitor_module as mm
from modules.monitor_control import view_model as vm


@pytest.fixture(autouse=True)
def _no_real_display_read(monkeypatch):
    """`create_widget()` starts a real display read on a worker. Left running it
    finishes during a LATER, unrelated test, where `_offer_window_restore` can
    open a real modal `QMessageBox.question` and hang the whole run (seen
    2026-09-25, in test_startup_tab). These tests never need real hardware."""
    monkeypatch.setattr(mm.MonitorControlModule, "refresh_data", lambda self: None)


def _view(target_id=520, name="MO27Q28G", active=True):
    return vm.MonitorView(
        target_id=target_id, name=name, connector="HDMI", adapter="GPU",
        active=active, resolution=(2560, 1440) if active else None,
        position=(0, 0) if active else None, refresh_hz=144.0 if active else 0.0,
        rates_at_resolution=(144.0,), native_resolution=(2560, 1440),
        device_name=r"\\.\DISPLAY2" if active else None)


def _module():
    module = mm.MonitorControlModule()
    module.app = type("App", (), {"thread_pool": None})()
    module.create_widget()
    return module


def test_a_toggle_never_starts_a_countdown(monkeypatch):
    """The whole point: no `RevertCountdown` is created."""
    module = _module()
    module._views = [_view()]

    monkeypatch.setattr(mm.dw, "set_target_active",
                        lambda target_id, active: (True, ""))

    started = []
    monkeypatch.setattr(mm.guard, "RevertCountdown",
                        lambda *a, **k: started.append(1))

    module._do_toggle_monitor(520, False)
    assert started == []


def test_a_successful_disconnect_refreshes_the_view(monkeypatch):
    """A successful toggle re-reads the topology, same as a guarded change
    does after its countdown resolves.

    The "disconnected" message itself is transient by design — matching
    every guarded action, a synchronous `refresh_data()` call immediately
    overwrites the status label with "Reading displays…" — so the message
    is not asserted on here; `test_a_refused_toggle_...` below is where the
    status text is the thing actually meant to persist.
    """
    module = _module()
    module._views = [_view()]

    monkeypatch.setattr(mm.dw, "set_target_active",
                        lambda target_id, active: (True, ""))
    refreshed = []
    monkeypatch.setattr(module, "refresh_data", lambda: refreshed.append(1))

    module._do_toggle_monitor(520, False)
    assert refreshed == [1]


def test_a_refused_disconnect_says_why_and_never_refreshes(monkeypatch):
    """`can_set_target_active` refusing the last monitor surfaces here — the
    handler does not swallow it or pretend the toggle happened.

    No refresh on this path: nothing changed, so there is nothing to
    re-read, and calling `refresh_data()` anyway would overwrite this exact
    message with "Reading displays…" before anyone could read it.
    """
    module = _module()
    module._views = [_view()]

    monkeypatch.setattr(
        mm.dw, "set_target_active",
        lambda target_id, active: (
            False, "that is the only display in use — turning it off "
                  "would leave the machine with no visible output"))
    refreshed = []
    monkeypatch.setattr(module, "refresh_data", lambda: refreshed.append(1))

    module._do_toggle_monitor(520, False)
    assert "could not be disconnected" in module._status.text()
    assert "only display" in module._status.text()
    assert refreshed == []


def test_a_connect_also_skips_the_countdown(monkeypatch):
    module = _module()
    module._views = [_view(active=False)]

    monkeypatch.setattr(mm.dw, "set_target_active",
                        lambda target_id, active: (True, ""))
    started = []
    monkeypatch.setattr(mm.guard, "RevertCountdown",
                        lambda *a, **k: started.append(1))
    monkeypatch.setattr(module, "refresh_data", lambda: None)

    module._do_toggle_monitor(520, True)
    assert started == []
