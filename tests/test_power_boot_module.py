"""PowerBootModule.refresh_data() checked `hasattr(self, "_load_power")` /
`hasattr(self, "_load_boot")` -- attributes that were never assigned
anywhere in the file. `create_widget()` only bound local closures, never
`self`, so the hasattr guard was always False and the 60s auto-refresh
this module declares (`get_refresh_interval() -> 60_000`) silently did
nothing, forever. Same bug class as the WindowsFeaturesModule fix from the
Management + Network consolidation -- this pins the equivalent fix here.
"""
import time

from PyQt6.QtCore import QThreadPool

from modules.power_boot import power_module
from modules.power_boot.power_module import PowerBootModule


def _settle(qapp):
    QThreadPool.globalInstance().waitForDone(10_000)
    deadline = time.time() + 2
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_create_widget_binds_load_power_and_load_boot(qapp, monkeypatch):
    monkeypatch.setattr(power_module, "get_power_plans", lambda: [])
    monkeypatch.setattr(power_module, "get_hibernate_state", lambda: False)
    monkeypatch.setattr(power_module, "get_fast_startup", lambda: False)
    monkeypatch.setattr(power_module, "get_sleep_timeout_ac", lambda: 0)
    monkeypatch.setattr(power_module, "get_boot_entries", lambda: [])
    monkeypatch.setattr(power_module, "is_reboot_pending", lambda: False)

    module = PowerBootModule()
    widget = module.create_widget()  # held: an unretained widget is exactly
    _settle(qapp)                    # the shape of the 0xC0000409 crash this
                                      # module's new widget-lifetime guards fix

    assert widget is not None
    assert hasattr(module, "_load_power")
    assert hasattr(module, "_load_boot")
    assert callable(module._load_power)
    assert callable(module._load_boot)


def test_refresh_data_actually_reloads_power_and_boot(qapp, monkeypatch):
    calls = {"power": 0, "boot": 0}

    def _plans():
        calls["power"] += 1
        return []

    def _entries():
        calls["boot"] += 1
        return []

    monkeypatch.setattr(power_module, "get_power_plans", _plans)
    monkeypatch.setattr(power_module, "get_hibernate_state", lambda: False)
    monkeypatch.setattr(power_module, "get_fast_startup", lambda: False)
    monkeypatch.setattr(power_module, "get_sleep_timeout_ac", lambda: 0)
    monkeypatch.setattr(power_module, "get_boot_entries", _entries)
    monkeypatch.setattr(power_module, "is_reboot_pending", lambda: False)

    module = PowerBootModule()
    widget = module.create_widget()  # held for the module's whole lifetime,
    _settle(qapp)                    # matching a real composite host, which
    calls["power"] = 0                # reparents this into a permanent tab
    calls["boot"] = 0                  # page rather than letting it be GC'd

    module.refresh_data()
    _settle(qapp)
    assert widget is not None

    assert calls["power"] == 1
    assert calls["boot"] == 1


def test_refresh_data_before_the_widget_is_built_does_not_raise():
    """A composite host can tick a child that was never opened -- this must
    stay a safe no-op, exactly like the WindowsFeaturesModule precedent."""
    module = PowerBootModule()
    module.refresh_data()  # must not raise


def test_an_unretained_widget_does_not_crash_when_the_worker_lands(qapp, monkeypatch):
    """This is the actual shape of a real, reproduced 0xC0000409 crash this
    session: `create_widget()` starts `load_power()`/`load_boot()`'s
    Workers synchronously, and if nothing holds the returned widget, PyQt6
    destroys its C++ objects before the queued result signal is delivered.
    Without a widget-lifetime guard, delivering that signal into a deleted
    QWidget is a hard process crash, not a Python exception -- confirmed by
    reproducing it standalone (a script with no pytest at all) before this
    fix, and confirming it stopped after adding `_widget_valid()` checks to
    every worker-callback line in `load_power`/`load_boot`. This test is
    the in-process regression: no reference is kept, matching the crash's
    exact precondition, and it must complete without raising."""
    monkeypatch.setattr(power_module, "get_power_plans", lambda: [])
    monkeypatch.setattr(power_module, "get_hibernate_state", lambda: False)
    monkeypatch.setattr(power_module, "get_fast_startup", lambda: False)
    monkeypatch.setattr(power_module, "get_sleep_timeout_ac", lambda: 0)
    monkeypatch.setattr(power_module, "get_boot_entries", lambda: [])
    monkeypatch.setattr(power_module, "is_reboot_pending", lambda: False)

    module = PowerBootModule()
    module.create_widget()  # deliberately NOT held
    import gc
    gc.collect()
    _settle(qapp)  # must not crash or raise even though the widget is gone
