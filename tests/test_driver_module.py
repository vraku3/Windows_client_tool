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

class _FakeConfig:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class _FakeApp:
    thread_pool = None
    backup = None

    def __init__(self):
        self.config = _FakeConfig()


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


class _SyncPool:
    """Runs a Worker synchronously on the calling thread instead of a real
    background one, so a test can assert on the UI immediately after."""

    def start(self, worker) -> None:
        worker.run()


def test_cancelling_mid_backup_still_recovers_the_ui(monkeypatch):
    """Regression: Worker.run() (core/worker.py) emits `cancelled` -- never
    `result` -- whenever the cancel flag is set by the time `do_backup`
    returns, even when the per-driver loop noticed it and broke cleanly.
    Without a `cancelled` handler connected, every control (Backup/Refresh/
    Export/Filter/Cancel) stayed disabled and the progress bar stayed frozen
    for the rest of the session -- reachable just by clicking Cancel, or by
    navigating away mid-backup (on_deactivate -> cancel_all_workers()).
    This drives a REAL Worker through a real cancellation, not a stub."""
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo("A", "Net", "1.0", "", "V", True, 0, "", inf_name="oem1.inf"),
        DriverInfo("B", "Net", "1.0", "", "V", True, 0, "", inf_name="oem2.inf"),
    ]
    monkeypatch.setattr(dmod.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: "C:\\backup")
    monkeypatch.setattr(dmod, "confirm_destructive", lambda *a, **k: True)

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **k):
        # Cancel partway through -- after the first driver "exports" -- so
        # the loop's own `worker.is_cancelled` check is what breaks it,
        # exactly the path that leaves `self._cancelled` True when
        # `do_backup` returns.
        mod._backup_worker.cancel()
        return R()

    monkeypatch.setattr(dmod.subprocess, "run", fake_run)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))

    mod._backup_drivers()

    assert mod._backup_btn.isEnabled() is True
    assert mod._refresh_btn.isEnabled() is True
    assert mod._export_btn.isEnabled() is True
    assert mod._filter_edit.isEnabled() is True
    assert mod._cancel_backup_btn.isVisible() is False
    assert mod._backup_worker is None
    assert mod._status_lbl.text() == "Driver backup cancelled."


# ----------------------------------------------------------------------
# Task 35: flag filter combo, bulk-select flagged rows, numeric date sort,
# sort persistence, empty state.
# ----------------------------------------------------------------------


def test_signed_only_filter_hides_unsigned_rows(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo("A", "Net", "1.0", "", "V", True, 0, ""),
        DriverInfo("B", "Net", "1.0", "", "V", False, 0, "🔴 Unsigned"),
    ]
    mod._flag_filter_combo.setCurrentText("Unsigned only")
    mod._populate(mod._drivers_ref[0], "")
    assert mod._table.rowCount() == 1
    assert mod._table.item(0, 0).text() == "B"


def test_select_all_flagged_checks_every_red_row(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo("A", "Net", "1.0", "", "V", True, 0, ""),
        DriverInfo("B", "Net", "1.0", "", "V", False, 22, "🔴 Unsigned 🔴 Error(22)"),
    ]
    mod._populate(mod._drivers_ref[0], "")
    mod._select_all_flagged()
    assert len(mod._table.selectionModel().selectedRows()) == 1


def test_date_and_size_style_columns_use_numeric_sort():
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo("A", "Net", "1.0", "2020-01-01", "V", True, 0, ""),
        DriverInfo("B", "Net", "1.0", "2026-01-01", "V", True, 0, ""),
    ]
    mod._populate(mod._drivers_ref[0], "")
    assert isinstance(mod._table.item(0, 3), dmod.NumericSortItem)


def test_an_out_of_range_date_does_not_crash_the_numeric_sort():
    """Regression: datetime.timestamp() itself raises OSError on a validly
    parsed but out-of-range date (e.g. 1601-01-01, the CIM_DATETIME/FILETIME
    zero-epoch sentinel WMI can report for a device with no genuine driver
    date) -- strptime succeeds, so a bare `except ValueError` around it
    doesn't catch the crash. This used to blow up _populate() itself."""
    assert dmod._date_sort_value("1601-01-01") == 0.0
    assert dmod._date_sort_value("1969-12-31") == 0.0
    assert dmod._date_sort_value("") == 0.0
    assert dmod._date_sort_value("not-a-date") == 0.0


def test_header_click_sort_survives_a_later_filter_change(monkeypatch):
    """Regression: _populate() used to reload the last-PERSISTED sort from
    config and force-reapply it on every call -- and _populate runs on
    every filter keystroke, hide-pseudo toggle, and flag-combo change, not
    just a real refresh -- so clicking a header to sort, then typing one
    character into the filter box, silently reverted the sort back to
    whatever was last saved in on_deactivate.

    The fixture rows deliberately DISAGREE on order between column 0
    (Device Name) and column 1 (Class): sorted by Device Name, "Alpha"
    (Class "Bcls") comes first; sorted by Class, "Zeta" (Class "Acls")
    comes first. A test whose two columns happen to agree on row order
    can't tell a real column-1 sort from a silent revert to the default
    column-0 sort -- confirmed by review: the original fixture did exactly
    that and passed even against the unfixed, reverting code."""
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo("Alpha", "Bcls", "1.0", "", "V", True, 0, ""),
        DriverInfo("Zeta", "Acls", "1.0", "", "V", True, 0, ""),
    ]
    mod._populate(mod._drivers_ref[0], "")
    mod._on_header_click(1)  # sort by the "Class" column, ascending
    assert mod._table.item(0, 1).text() == "Acls"

    mod._populate(mod._drivers_ref[0], "")  # e.g. a filter keystroke
    assert mod._table.item(0, 1).text() == "Acls"


def test_sort_persists_on_deactivate_and_restores_on_the_next_create_widget():
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo("Zeta", "Bcls", "1.0", "", "V", True, 0, ""),
        DriverInfo("Alpha", "Acls", "1.0", "", "V", True, 0, ""),
    ]
    mod._populate(mod._drivers_ref[0], "")
    mod._on_header_click(1)
    shared_config = mod.app.config
    mod.on_deactivate()

    mod2 = dmod.DriverModule()
    mod2.on_start(_FakeApp())
    mod2.app.config = shared_config  # same persisted store, fresh module
    mod2.create_widget()
    mod2._drivers_ref[0] = [
        DriverInfo("Zeta", "Bcls", "1.0", "", "V", True, 0, ""),
        DriverInfo("Alpha", "Acls", "1.0", "", "V", True, 0, ""),
    ]
    mod2._populate(mod2._drivers_ref[0], "")
    assert mod2._table.item(0, 1).text() == "Acls"


def test_empty_state_shown_before_first_load_and_hidden_once_drivers_arrive(monkeypatch):
    mod = _module()
    assert mod._table_stack.currentIndex() == 1  # empty state, nothing loaded

    monkeypatch.setattr(dmod, "fetch_drivers",
                        lambda: [DriverInfo("A", "Net", "1.0", "", "V", True, 0, "")])
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    mod._do_refresh()  # runs the real COMWorker/on_result path synchronously
    assert mod._table_stack.currentIndex() == 0

    monkeypatch.setattr(dmod, "fetch_drivers", lambda: [])
    mod._do_refresh()
    assert mod._table_stack.currentIndex() == 1


# ----------------------------------------------------------------------
# Task 36: export respects the filter, single-driver export.
# ----------------------------------------------------------------------


def test_export_writes_only_visible_rows(tmp_path, monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo("A", "Net", "1.0", "", "V", True, 0, ""),
        DriverInfo("B", "Net", "1.0", "", "V", True, 0, ""),
    ]
    mod._filter_edit.setText("a")
    mod._populate(mod._drivers_ref[0], "a")
    out = tmp_path / "drivers.csv"
    monkeypatch.setattr(dmod.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    mod._do_export()
    text = out.read_text(encoding="utf-8")
    assert "A" in text and "B" not in text
