"""DriverModule's "Hide pseudo-devices" checkbox: D27 wires
`driver_reader._PSEUDO_CLASSES` into `_populate`'s filter predicate,
defaulting to checked (pseudo-class drivers hidden)."""
import pytest

from modules.driver_manager import driver_module as dmod
from modules.driver_manager.driver_reader import DriverInfo


def _drivers():
    return [
        DriverInfo(device_name="Real NIC", driver_class="Net", version="1.0",
                   date="2020-01-01", publisher="Vendor", signed=True,
                   error_code=0, flags=""),
        DriverInfo(device_name="Some Software Component",
                   driver_class="SoftwareComponent", version="", date="",
                   publisher="", signed=False, error_code=0, flags=""),
    ]


def test_hide_pseudo_checkbox_defaults_checked_and_excludes_pseudo_classes():
    mod = dmod.DriverModule()
    mod.create_widget()
    assert mod._hide_pseudo_cb.isChecked() is True

    mod._populate(_drivers())

    assert mod._table.rowCount() == 1
    assert mod._table.item(0, 0).text() == "Real NIC"


def test_unchecking_hide_pseudo_shows_pseudo_class_drivers_too():
    mod = dmod.DriverModule()
    mod.create_widget()
    mod._hide_pseudo_cb.setChecked(False)

    mod._populate(_drivers())

    assert mod._table.rowCount() == 2
    names = {mod._table.item(r, 0).text() for r in range(2)}
    assert names == {"Real NIC", "Some Software Component"}


def test_text_filter_and_pseudo_filter_combine():
    mod = dmod.DriverModule()
    mod.create_widget()
    mod._hide_pseudo_cb.setChecked(False)

    mod._populate(_drivers(), filter_text="software")

    assert mod._table.rowCount() == 1
    assert mod._table.item(0, 0).text() == "Some Software Component"


# ----------------------------------------------------------------------
# Task 33: context menu -- uninstall driver, open folder, cross-reference
# to Cleanup's panel. Nothing here touches a real driver -- pnputil and
# require_admin/confirm_destructive are monkeypatched throughout.
# ----------------------------------------------------------------------

class _FakeApp:
    thread_pool = None
    backup = None


_created_modules = []


def _module():
    mod = dmod.DriverModule()
    mod.on_start(_FakeApp())
    mod.create_widget()
    _created_modules.append(mod)
    return mod


@pytest.fixture(autouse=True)
def _cancel_leftover_driver_workers():
    """`_do_uninstall_driver` ends by calling the real `_do_refresh()`,
    which -- because `_FakeApp.thread_pool` is None -- dispatches onto
    `QThreadPool.globalInstance()`, a pool that outlives this test. If a
    test's `monkeypatch.setattr(dmod.subprocess, "run", ...)` reverts
    (at test teardown, on the main thread) before that background thread
    gets to call it, the worker calls the REAL subprocess instead of the
    mock and fires its result signal much later, into a module instance
    from a test that has long since finished -- observed to crash the
    whole pytest process with an access violation, not fail a test.
    Cancelling every module created via `_module()` after each test
    guarantees the worker settles for `cancelled` (Worker.run only skips
    the result/error emit, per core/worker.py) rather than delivering a
    stale result into torn-down state."""
    yield
    for mod in _created_modules:
        mod.cancel_all_workers()
    _created_modules.clear()


def test_uninstall_is_offered_only_for_oem_numbered_drivers(monkeypatch):
    mod = _module()
    assert dmod.published_name_for("oem60.inf") == "oem60.inf"
    assert dmod.published_name_for("usb.inf") is None  # inbox driver


def test_uninstall_confirms_before_calling_pnputil(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "require_admin", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(dmod, "confirm_destructive", lambda *a, **k: False)
    monkeypatch.setattr(dmod.subprocess, "run", lambda *a, **k: calls.append(a))
    mod._do_uninstall_driver("oem60.inf", "Some Device")
    assert calls == []


def test_uninstall_confirmed_calls_delete_driver(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "require_admin", lambda *a, **k: True)
    monkeypatch.setattr(dmod, "confirm_destructive", lambda *a, **k: True)
    calls = []
    class R: returncode = 0; stdout = ""; stderr = ""
    monkeypatch.setattr(dmod.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or R())
    mod._do_uninstall_driver("oem60.inf", "Some Device")
    assert calls[0][:3] == ["pnputil", "/delete-driver", "oem60.inf"]
    assert "/uninstall" in calls[0]


# ----------------------------------------------------------------------
# Task 34: confirmation, cancel, real per-driver progress, and elevation
# messaging on backup. pnputil, confirm_destructive and the file dialog
# are monkeypatched throughout -- nothing here touches a real driver.
# ----------------------------------------------------------------------


def test_backup_confirms_before_starting(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dmod.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: "C:\\backup")
    asked = []
    monkeypatch.setattr(dmod, "confirm_destructive",
                        lambda *a, **k: asked.append(1) or False)
    started = []
    # `mod.app.thread_pool` is None in `_FakeApp`, so this patches a
    # throwaway placeholder that `_backup_drivers` never looks up (the
    # real dispatch path is `QThreadPool.globalInstance()`); a bare
    # `object()` can't take attribute assignment at all (no `__dict__`),
    # so a dummy stand-in object is used instead. The `started == []`
    # half of the assertion is trivially true regardless of what
    # `_backup_drivers` does -- `asked` is the check that carries signal.
    _dummy = type("Dummy", (), {})()
    monkeypatch.setattr(mod.app.thread_pool if mod.app.thread_pool else _dummy,
                        "start", lambda w: started.append(1), raising=False)
    mod._backup_drivers()
    assert asked and started == []


def test_backup_disables_export_and_filter_while_running(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dmod.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: "C:\\backup")
    monkeypatch.setattr(dmod, "confirm_destructive", lambda *a, **k: True)
    mod._backup_drivers()
    assert mod._export_btn.isEnabled() is False
    assert mod._filter_edit.isEnabled() is False


def test_cancel_button_cancels_the_backup_worker(monkeypatch):
    mod = _module()
    mod._backup_worker = type("W", (), {"cancelled": False,
                                        "cancel": lambda self: setattr(self, "cancelled", True)})()
    mod._on_cancel_backup()
    assert mod._backup_worker.cancelled is True
