"""DriverModule's "Hide pseudo-devices" checkbox: D27 wires
`driver_reader._PSEUDO_CLASSES` into `_populate`'s filter predicate,
defaulting to checked (pseudo-class drivers hidden)."""
import pytest
from PyQt6.QtWidgets import QHeaderView

from modules.driver_manager import driver_module as dmod
from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import update_history as uh


@pytest.fixture(autouse=True)
def _isolated_update_history(tmp_path, monkeypatch):
    """Every test in this file that installs/checks a vendor update goes
    through record_check/record_applied, which write to
    %APPDATA%/WindowsTweaker/driver_updates/update_history.json by
    default -- without this, a test run writes fabricated device history
    (fake GPUs, fake versions) straight into the REAL user's app-data
    file. Confirmed this actually happened once already: a prior run of
    this suite left a "GeForce RTX 4090" entry in the real file on this
    exact machine, which does not own an NVIDIA GPU at all."""
    monkeypatch.setattr(uh, "_history_path", lambda: str(tmp_path / "update_history.json"))


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


# ----------------------------------------------------------------------
# C07 follow-up: Task 39's save/restore machinery had zero real effect --
# every column here was Stretch or ResizeToContents, both of which Qt
# silently ignores setColumnWidth() on. Only Interactive columns can hold
# a manually-set or persisted width.
# ----------------------------------------------------------------------


def test_columns_other_than_device_name_are_interactive():
    mod = _module()
    header = mod._table.horizontalHeader()
    # Device Name shares the free width; every other column must be
    # Interactive so a drag-resize or a persisted width actually sticks.
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    for i in range(1, len(dmod.COLUMNS)):
        assert header.sectionResizeMode(i) == QHeaderView.ResizeMode.Interactive, \
            f"column {i} ({dmod.COLUMNS[i]}) is not Interactive"


def test_first_populate_fits_columns_when_nothing_was_persisted(monkeypatch):
    mod = _module()
    calls = []
    monkeypatch.setattr(mod._table, "resizeColumnsToContents", lambda: calls.append(1))
    mod._populate(_drivers())
    assert calls == [1]
    assert mod._columns_fitted_this_session is True


def test_first_populate_skips_the_fit_when_a_width_is_already_persisted():
    mod = dmod.DriverModule()
    app = _FakeApp()
    app.config._data[f"{dmod.DriverModule._CONFIG_PREFIX}.column_widths"] = \
        [150] * len(dmod.COLUMNS)
    mod.on_start(app)
    mod.create_widget()  # restore_column_widths() applies the saved widths here
    _created_modules.append(mod)

    assert mod._table.columnWidth(1) == 150
    mod._populate(_drivers())
    # The restored width must survive the first real populate untouched.
    assert mod._table.columnWidth(1) == 150


def test_a_later_populate_never_reverts_an_in_session_column_resize(monkeypatch):
    """Same class of bug Task 35 fixed for sort order: _populate() runs on
    every filter keystroke, hide-pseudo toggle and flag-combo change, not
    just a real refresh -- so a fit-once step that fired again on a later
    call would silently undo a column the user just dragged wider."""
    mod = _module()
    mod._populate(_drivers())  # first real populate -- fits once

    mod._table.setColumnWidth(1, 321)  # simulate a user drag-resize
    calls = []
    monkeypatch.setattr(mod._table, "resizeColumnsToContents", lambda: calls.append(1))

    mod._populate(_drivers(), filter_text="real")  # e.g. a filter keystroke

    assert calls == []
    assert mod._table.columnWidth(1) == 321


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


# ----------------------------------------------------------------------
# Final-review fix: device_name is NOT unique (Task 36's own
# driver_reader._dedup_key already established this for the class-chunk
# merge) -- resolving a row back to its DriverInfo by device_name alone,
# as _on_context_menu and _do_export both used to, silently picks the
# WRONG physical device whenever two rows share a generic name (multiple
# "USB Root Hub" entries is the real-machine case that surfaced this).
# ----------------------------------------------------------------------


def _colliding_hubs():
    return [
        DriverInfo("USB Root Hub (USB 3.0)", "USB", "1.0", "", "V", True, 0, "",
                   inf_name="oem10.inf", device_id="USB\\ROOT_HUB30\\1"),
        DriverInfo("USB Root Hub (USB 3.0)", "USB", "1.0", "", "V", True, 0, "",
                   inf_name="oem20.inf", device_id="USB\\ROOT_HUB30\\2"),
    ]


def _row_for_device_id(table, device_id: str) -> int:
    from PyQt6.QtCore import Qt
    return next(r for r in range(table.rowCount())
               if table.item(r, 0).data(Qt.ItemDataRole.UserRole) == device_id)


def test_context_menu_resolves_the_right_row_when_names_collide(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = _colliding_hubs()
    mod._populate(mod._drivers_ref[0], "")

    from PyQt6.QtCore import QPoint
    table = mod._table
    row = _row_for_device_id(table, "USB\\ROOT_HUB30\\2")
    monkeypatch.setattr(table, "indexAt", lambda pos: table.model().index(row, 0))
    captured = []
    monkeypatch.setattr(dmod.QMenu, "exec", lambda self, *a, **k: captured.append(self))

    mod._on_context_menu(QPoint(0, 0))

    menu = captured[0]
    actions = {a.text(): a for a in menu.actions()}
    calls = []
    monkeypatch.setattr(mod, "_do_uninstall_driver",
                        lambda published, name: calls.append(published))
    actions["Uninstall driver package…"].trigger()
    # The SECOND device's own package (oem20.inf), not the first row that
    # happens to share its visible name (oem10.inf).
    assert calls == ["oem20.inf"]


def test_export_filters_by_row_identity_not_visible_name(monkeypatch, tmp_path):
    from PyQt6.QtCore import Qt
    mod = _module()
    mod._drivers_ref[0] = _colliding_hubs()
    mod._populate(mod._drivers_ref[0], "")
    # Hide the FIRST device's row only -- both rows show the identical
    # "USB Root Hub (USB 3.0)" name, so a name-based filter would export
    # both (or the wrong one) regardless of which row is actually hidden.
    hidden_row = _row_for_device_id(mod._table, "USB\\ROOT_HUB30\\1")
    mod._table.setRowHidden(hidden_row, True)

    out = tmp_path / "drivers.csv"
    monkeypatch.setattr(dmod.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    mod._do_export()

    rows = out.read_text(encoding="utf-8").strip().splitlines()
    data_rows = [r for r in rows if "USB Root Hub" in r]
    assert len(data_rows) == 1, \
        "only the visible row's own device should be exported"


# ----------------------------------------------------------------------
# Task 7: "Copy Hardware ID" + "Why does this matter?" context-menu
# actions.
# ----------------------------------------------------------------------


def test_copy_hardware_id_puts_the_devices_hardware_id_on_the_clipboard(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0,
                   flags="", hardware_id="PCI\\VEN_1234"),
    ]
    mod._populate(mod._drivers_ref[0], "")
    copied = []
    monkeypatch.setattr(dmod.QApplication, "clipboard",
                        lambda: type("C", (), {"setText": lambda self, t: copied.append(t)})())
    mod._copy_hardware_id("PCI\\VEN_1234")
    assert copied == ["PCI\\VEN_1234"]


def test_why_does_this_matter_explains_each_flag_present():
    mod = _module()
    text = mod._explain_flags("🔴 Unsigned (as reported by Windows) 🟠 Old")
    assert "unsigned" in text.lower()
    assert "old" in text.lower()


# ----------------------------------------------------------------------
# Task 38 (C01): get_search_provider() wires a live handle into
# _drivers_ref, not a snapshot taken when the provider was built --
# see tests/test_driver_search_provider.py for the provider's own
# unit tests. These confirm DriverModule wires it correctly.
# ----------------------------------------------------------------------


def test_get_search_provider_returns_a_driver_search_provider():
    mod = _module()
    provider = mod.get_search_provider()
    assert type(provider).__name__ == "DriverSearchProvider"
    assert provider.module_name == "Driver Manager"


def test_get_search_provider_sees_current_drivers_by_name_and_class():
    from core.search_provider import SearchQuery

    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo("Realtek Ethernet Controller", "Net", "1.0", "2024-01-01",
                   "Realtek", True, 0, ""),
        DriverInfo("Generic USB Hub", "USB", "2.0", "2023-05-01",
                   "Microsoft", True, 0, ""),
    ]
    provider = mod.get_search_provider()
    by_name = provider.search(SearchQuery(text="realtek"))
    assert any("Realtek Ethernet Controller" in r.summary for r in by_name)
    by_class = provider.search(SearchQuery(text="usb"))
    assert any("Generic USB Hub" in r.summary for r in by_class)


def test_get_search_provider_called_before_a_refresh_finds_nothing_not_none():
    # get_search_provider() is really called from on_start(), before
    # create_widget() -- confirmed via ModuleRegistry.start_all(). Simulate
    # that ordering directly: a fresh module with no refresh yet.
    from core.search_provider import SearchQuery

    mod = dmod.DriverModule()
    mod.on_start(_FakeApp())
    provider = mod.get_search_provider()
    assert provider.search(SearchQuery(text="anything")) == []


# ----------------------------------------------------------------------
# Task 8: full inventory export (CSV + HTML) -- distinct from _do_export,
# which respects the current filter/visible rows. This exports the FULL
# current driver list unfiltered, with the full field set including the
# Task 1/2/3 fields (hardware_id, whql_certified, inf_name).
# ----------------------------------------------------------------------


def test_export_inventory_csv_includes_every_driver_and_new_fields(tmp_path, monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0,
                   flags="", hardware_id="PCI\\VEN_1", whql_certified=True),
        DriverInfo(device_name="B", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=False, error_code=0, flags=""),
    ]
    out = tmp_path / "inventory.csv"
    monkeypatch.setattr(dmod.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    mod._export_inventory()
    text = out.read_text(encoding="utf-8")
    assert "A" in text and "B" in text
    assert "PCI\\VEN_1" in text
    assert "True" in text  # whql_certified for A


def test_export_inventory_html_produces_a_table(tmp_path, monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    out = tmp_path / "inventory.html"
    monkeypatch.setattr(dmod.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), "HTML (*.html)"))
    mod._export_inventory()
    text = out.read_text(encoding="utf-8")
    assert "<table" in text and "A" in text


# ----------------------------------------------------------------------
# Task 9: "Snapshots" menu -- save baseline / diff against one.
# ----------------------------------------------------------------------


def test_save_baseline_action_calls_save_baseline_with_current_drivers(monkeypatch, tmp_path):
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    monkeypatch.setattr(dmod, "_ask_baseline_name", lambda parent: "session-1")
    mod._save_baseline_action()
    assert [m.name for m in db.list_baselines()] == ["session-1"]


def test_save_baseline_action_shows_a_warning_instead_of_crashing_on_oserror(monkeypatch, tmp_path):
    """Final-review finding I3 (site A): a name db.save_baseline can't
    write (e.g. one producing a path over MAX_PATH) must not propagate an
    uncaught exception out of this PyQt6 slot -- that aborts the whole
    process via qFatal() rather than being caught by main.py's handler."""
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    monkeypatch.setattr(dmod, "_ask_baseline_name", lambda parent: "bad-name")

    def _raise(*a, **k):
        raise OSError("path too long")
    monkeypatch.setattr(db, "save_baseline", _raise)
    warned = []
    monkeypatch.setattr(dmod.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a))

    mod._save_baseline_action()  # must not raise

    assert warned
    assert "bad-name" in warned[0][2]


def test_save_baseline_action_saves_without_confirmation_when_no_name_collides(monkeypatch, tmp_path):
    """A brand-new baseline name proceeds straight through -- no dialog."""
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    monkeypatch.setattr(dmod, "_ask_baseline_name", lambda parent: "fresh-name")

    def _fail_if_called(*a, **k):
        raise AssertionError("QMessageBox.question must not be shown for a "
                              "non-colliding baseline name")
    monkeypatch.setattr(dmod.QMessageBox, "question", _fail_if_called)

    mod._save_baseline_action()

    assert [m.name for m in db.list_baselines()] == ["fresh-name"]


def test_save_baseline_action_asks_before_overwriting_a_colliding_name_and_respects_no(monkeypatch, tmp_path):
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    db.save_baseline("existing", [
        DriverInfo(device_name="Old", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ])
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="New", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    monkeypatch.setattr(dmod, "_ask_baseline_name", lambda parent: "existing")
    asked = []
    monkeypatch.setattr(
        dmod.QMessageBox, "question",
        lambda *a, **k: (asked.append(a) or dmod.QMessageBox.StandardButton.No))
    save_calls = []
    monkeypatch.setattr(
        db, "save_baseline", lambda *a, **k: save_calls.append(a))

    mod._save_baseline_action()

    assert asked, "expected a confirmation dialog for the colliding name"
    assert "existing" in asked[0][2]
    assert save_calls == [], "save_baseline must not run when the user says No"


def test_save_baseline_action_overwrites_a_colliding_name_on_yes(monkeypatch, tmp_path):
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    db.save_baseline("existing", [
        DriverInfo(device_name="Old", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ])
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="New", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    monkeypatch.setattr(dmod, "_ask_baseline_name", lambda parent: "existing")
    monkeypatch.setattr(
        dmod.QMessageBox, "question",
        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)

    mod._save_baseline_action()

    saved = db.load_baseline("existing")
    assert saved is not None
    assert [d.device_name for d in saved] == ["New"]


def test_diff_against_baseline_action_shows_a_summary(monkeypatch, tmp_path):
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    db.save_baseline("old", [
        DriverInfo(device_name="Removed", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ])
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="Added", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    shown = []
    monkeypatch.setattr(dmod, "_choose_baseline", lambda parent, metas: "old")
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a))
    mod._diff_against_baseline_action()
    assert shown
    assert "Added" in shown[0][2] and "Removed" in shown[0][2]


def test_diff_against_baseline_action_handles_a_load_failure_without_crashing(monkeypatch, tmp_path):
    """Task 5 changed load_baseline() to return None (rather than raise) on
    a missing/corrupt baseline file -- a real race against list_baselines(),
    which just enumerated the sidecar successfully. The action must show an
    error and return, never pass None into diff_against_baseline()."""
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="Added", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    monkeypatch.setattr(dmod, "_choose_baseline", lambda parent, metas: "missing")
    monkeypatch.setattr(db, "load_baseline", lambda name: None)
    diff_calls = []
    monkeypatch.setattr(db, "diff_against_baseline",
                        lambda *a, **k: diff_calls.append(a))
    warned = []
    monkeypatch.setattr(dmod.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a))
    mod._diff_against_baseline_action()
    assert diff_calls == []
    assert warned


# ----------------------------------------------------------------------
# Task 12: System Restore Points toolbar button.
# ----------------------------------------------------------------------


def test_show_restore_points_runs_off_the_ui_thread(monkeypatch):
    """Final-review finding I4: `list_restore_points()` runs a PowerShell
    subprocess with a 30s timeout, so it must be dispatched via a Worker
    rather than called inline in the click handler. Patch QThreadPool to
    run it synchronously (this codebase's established idiom, see
    `_SyncPool` and `test_empty_state_shown_before_first_load_...`) and
    confirm a Worker actually got constructed and started, and that the
    button shows a loading state while it's "running"."""
    mod = _module()
    started = []

    class _RecordingPool:
        def start(self, worker) -> None:
            started.append(worker)
            worker.run()

    monkeypatch.setattr(dmod, "list_restore_points", lambda: [])
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _RecordingPool()))
    # The empty-list branch pops a real QMessageBox -- a modal dialog with
    # no event loop to click it hangs the test forever. Every sibling test
    # below mocks this; this one must too.
    monkeypatch.setattr(dmod.QMessageBox, "information", lambda *a, **k: None)

    mod._show_restore_points()

    assert len(started) == 1
    assert isinstance(started[0], dmod.Worker)
    # Restored after the (synchronously-run) worker's result lands.
    assert mod._restore_points_btn.isEnabled() is True
    assert mod._restore_points_btn.text() == "System Restore Points"


def test_restore_points_message_is_capped_with_a_more_line(monkeypatch):
    """A machine with default scheduled checkpoints can have dozens of
    restore points -- the QMessageBox must cap the DISPLAY (newest first)
    and say how many more there are, rather than showing every single
    one."""
    mod = _module()

    class _RecordingPool:
        def start(self, worker) -> None:
            worker.run()

    total = dmod._RESTORE_POINTS_DISPLAY_LIMIT + 7
    points = [
        {"CreationTime": f"202601{str(i + 1).zfill(2)}000000.000000-000",
         "Description": f"Checkpoint {i}"}
        for i in range(total)
    ]
    monkeypatch.setattr(dmod, "list_restore_points", lambda: points)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _RecordingPool()))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a))

    mod._show_restore_points()

    assert shown
    message = shown[0][2]
    assert "...and 7 more." in message
    # Only the capped, newest-first slice's descriptions should appear --
    # the oldest 7 (Checkpoint 0..6) must not be individually listed.
    for i in range(7):
        assert f"Checkpoint {i}\n" not in message
        assert not message.endswith(f"Checkpoint {i}")


def test_show_restore_points_disables_button_while_running(monkeypatch):
    """The button shows a loading state for as long as the worker has not
    reported back -- checked with a pool that doesn't run the worker at
    all, so the "while running" state is observable."""
    mod = _module()
    monkeypatch.setattr(dmod, "list_restore_points", lambda: [])
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: type("NoopPool", (), {"start": lambda self, w: None})()))

    mod._show_restore_points()

    assert mod._restore_points_btn.isEnabled() is False
    assert mod._restore_points_btn.text() == "Loading…"


def test_restore_points_worker_cancelled_mid_load_still_recovers_the_button(monkeypatch):
    """Regression, same class of bug `test_cancelling_mid_backup_still_
    recovers_the_ui` guards against for the backup worker: Worker.run()
    (core/worker.py) emits `cancelled` -- never `result` or `error` --
    whenever the cancel flag is set by the time the worker function
    returns. `on_deactivate()` calls `cancel_all_workers()`
    unconditionally on module switch, and `list_restore_points()` can
    legitimately still be running (up to its own 30s subprocess timeout)
    when that happens. Without a `cancelled` handler connected, the
    button was left stuck on "Loading…"/disabled for the rest of the
    session. Drives a REAL Worker through a real cancellation, not a
    stub."""

    class _CancelMidRunPool:
        """Cancels the worker from inside the "subprocess" call itself,
        then runs it -- so `worker.is_cancelled` is already True by the
        time `Worker.run()` checks it after `fn()` returns, exactly the
        path that emits `cancelled` instead of `result`."""

        def start(self, worker) -> None:
            self._worker = worker
            worker.run()

    mod = _module()
    pool = _CancelMidRunPool()

    def slow_list_restore_points():
        # Simulates a still-running subprocess call getting cancelled by
        # a module switch (on_deactivate -> cancel_all_workers()) before
        # it returns.
        pool._worker.cancel()
        return []

    monkeypatch.setattr(dmod, "list_restore_points", slow_list_restore_points)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: pool))

    mod._show_restore_points()

    assert mod._restore_points_btn.isEnabled() is True
    assert mod._restore_points_btn.text() == "System Restore Points"


def test_show_restore_points_shows_message_box_and_recovers_button_on_error(monkeypatch):
    """The `on_error` handler exists but had no test -- drive a real
    Worker through a real exception in list_restore_points()."""
    mod = _module()

    def _raise():
        raise OSError("boom")

    monkeypatch.setattr(dmod, "list_restore_points", _raise)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))

    mod._show_restore_points()

    assert shown and "boom" in shown[0]
    assert mod._restore_points_btn.isEnabled() is True
    assert mod._restore_points_btn.text() == "System Restore Points"


def test_show_restore_points_lists_them_by_creation_time_descending(monkeypatch):
    mod = _module()
    monkeypatch.setattr(
        dmod, "list_restore_points",
        lambda: [
            {"SequenceNumber": 1, "Description": "Older", "CreationTime": "20260101000000.000000-000"},
            {"SequenceNumber": 2, "Description": "Newer", "CreationTime": "20260201000000.000000-000"},
        ])
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._show_restore_points()
    assert shown
    assert shown[0].index("Newer") < shown[0].index("Older")


def test_show_restore_points_explains_a_failed_read(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dmod, "list_restore_points", lambda: None)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._show_restore_points()
    assert "could not" in shown[0].lower()


def test_show_restore_points_explains_an_empty_list(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dmod, "list_restore_points", lambda: [])
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._show_restore_points()
    assert "no restore points" in shown[0].lower()


# ----------------------------------------------------------------------
# Task 8: "Check for Vendor Update..." context-menu action -- elevation
# gate, no-provider explanations, and confirm-before-download/install.
# ----------------------------------------------------------------------

def test_driver_module_requires_admin_is_still_false_but_read_only_unelevated_is_true():
    mod = _module()
    assert mod.requires_admin is False
    assert mod.read_only_unelevated is True


def test_check_for_vendor_update_shows_no_provider_reason_when_vendor_unrecognized(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="Weird Card", driver_class="Display", version="1.0",
                  date="", publisher="V", signed=True, error_code=0, flags="",
                  hardware_id="PCI\\VEN_FFFF&DEV_0000"),
    ]
    mod._populate(mod._drivers_ref[0], "")
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._check_for_vendor_update(mod._drivers_ref[0][0])
    assert shown
    assert "vendor" in shown[0].lower()


def test_check_for_vendor_update_shows_no_adapter_reason_for_a_recognized_unsupported_vendor(monkeypatch):
    # Intel is recognized by vendor_id.py but has no registered provider
    # (unlike AMD and NVIDIA, both adapted now) -- the real case this
    # message exists for.
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="Intel Card", driver_class="Display", version="1.0",
                  date="", publisher="V", signed=True, error_code=0, flags="",
                  hardware_id="PCI\\VEN_8086&DEV_A780"),
    ]
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._check_for_vendor_update(mod._drivers_ref[0][0])
    assert shown
    assert "no update source" in shown[0].lower() or "not configured" in shown[0].lower()


def _fake_nvidia_update_provider():
    class _FakeProvider:
        vendor_name = "NVIDIA"
        allowed_download_domains = ["download.nvidia.com"]
        expected_signer = "NVIDIA Corporation"

        def check_for_update(self, d):
            from modules.driver_manager.vendor_updates.provider import UpdateInfo
            return UpdateInfo(vendor="NVIDIA", current_version="1.0",
                              latest_version="2.0",
                              download_url="https://us.download.nvidia.com/x.exe",
                              installer_signer="NVIDIA Corporation")
    return _FakeProvider()


def test_check_for_vendor_update_refuses_unelevated_before_any_confirm(monkeypatch):
    # The network check (provider.check_for_update) now runs on a Worker,
    # not inline -- _SyncPool drives that worker synchronously on this
    # thread so the rest of the test can assert on the UI immediately
    # afterward, the same pattern _backup_drivers's own tests use.
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")
    monkeypatch.setattr(dmod, "provider_for", lambda d: _fake_nvidia_update_provider())
    monkeypatch.setattr(dmod, "is_admin", lambda: False)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    asked_to_confirm = []
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: asked_to_confirm.append(a))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._check_for_vendor_update(driver)
    assert not asked_to_confirm  # never reached the confirm dialog
    assert shown
    assert "administrator" in shown[0].lower()


def test_check_for_vendor_update_asks_light_or_full_before_downloading_when_elevated(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")
    monkeypatch.setattr(dmod, "provider_for", lambda d: _fake_nvidia_update_provider())
    monkeypatch.setattr(dmod, "is_admin", lambda: True)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    asked = []

    def fake_ask(self, driver, update):
        asked.append((driver, update))
        return None  # cancel -- must not proceed to _run_vendor_update

    monkeypatch.setattr(dmod.DriverModule, "_ask_install_mode", fake_ask)
    mod._check_for_vendor_update(driver)
    assert asked
    asked_driver, asked_update = asked[0]
    assert asked_driver is driver
    assert asked_update.vendor == "NVIDIA"
    assert asked_update.current_version == "1.0"
    assert asked_update.latest_version == "2.0"


def test_check_for_vendor_update_proceeds_to_run_update_with_the_chosen_mode(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")
    monkeypatch.setattr(dmod, "provider_for", lambda d: _fake_nvidia_update_provider())
    monkeypatch.setattr(dmod, "is_admin", lambda: True)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    monkeypatch.setattr(dmod.DriverModule, "_ask_install_mode", lambda self, driver, update: "full")
    ran = []
    monkeypatch.setattr(dmod.DriverModule, "_run_vendor_update",
                        lambda self, driver, provider, update, mode: ran.append(mode))
    mod._check_for_vendor_update(driver)
    assert ran == ["full"]


def test_check_for_vendor_update_dispatches_the_network_check_on_a_worker_not_inline(monkeypatch):
    """Regression for the UI-thread-freeze finding: provider.check_for_update()
    is a real, uncached-per-call network round trip (NvidiaProvider: a pfid
    lookup plus a driver-lookup call, up to ~15s each). This proves the check
    itself goes through a Worker rather than running before dispatch -- a
    pool stub that deliberately never calls worker.run() must see NOTHING
    happen synchronously: no QMessageBox, no confirm, just a Worker handed
    to the pool."""
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")
    monkeypatch.setattr(dmod, "provider_for", lambda d: _fake_nvidia_update_provider())

    started = []

    class _NeverRunPool:
        def start(self, worker) -> None:
            # Deliberately does NOT call worker.run() -- if the network
            # check ran inline before reaching here, evidence of it (a
            # QMessageBox call) would already exist by the time this
            # method returns.
            started.append(worker)

    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _NeverRunPool()))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(
        dmod.QMessageBox, "question",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("confirm dialog reached before the worker ran")))

    mod._check_for_vendor_update(driver)

    assert len(started) == 1
    assert isinstance(started[0], dmod.Worker)
    assert started[0] in mod._workers
    assert not shown  # the network check never actually ran


# ----------------------------------------------------------------------
# _run_vendor_update -- no dedicated test existed at all before the final
# whole-branch review (Findings C2 and I3): the undo-all button was never
# refreshed the one place a token is ever recorded, and the downloaded
# installer was never deleted, win or lose.
# ----------------------------------------------------------------------

def _fake_update():
    from modules.driver_manager.vendor_updates.provider import UpdateInfo
    return UpdateInfo(vendor="NVIDIA", current_version="1.0", latest_version="2.0",
                      download_url="https://us.download.nvidia.com/x.exe",
                      installer_signer="NVIDIA Corporation")


def _fake_provider():
    class _P:
        vendor_name = "NVIDIA"
        allowed_download_domains = ["download.nvidia.com"]
    return _P()


def test_run_vendor_update_enables_undo_all_button_on_a_successful_install(monkeypatch, tmp_path):
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684",
                        device_id="PCI\\DEV1")
    downloaded = tmp_path / "installer.exe"
    downloaded.write_bytes(b"fake")

    monkeypatch.setattr(dmod.vendor_pipeline, "download_and_verify",
                        lambda update, allowed_domains, extra_headers=None: dmod.vendor_pipeline.DownloadResult(
                            path=str(downloaded)))
    monkeypatch.setattr(dmod.vendor_pipeline, "install_light",
                        lambda path, driver: dmod.InstallResult(
                            ok=True, reason="", restore_point_taken=True))
    from modules.driver_manager.vendor_updates import rollback as rb_mod
    monkeypatch.setattr(rb_mod, "snapshot_before_install", lambda driver: "oem12.inf")
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    monkeypatch.setattr(dmod.QMessageBox, "information", lambda *a, **k: None)
    # _reread_driver_version shells out to PowerShell for real -- must be
    # mocked in every unit test, not just left to actually run one.
    monkeypatch.setattr(dmod.DriverModule, "_reread_driver_version", lambda self, driver: "1.0")

    assert mod._undo_all_updates_btn.isEnabled() is False  # precondition

    mod._run_vendor_update(driver, _fake_provider(), _fake_update())

    assert mod._applied_update_tokens == {"PCI\\DEV1": "oem12.inf"}
    assert mod._undo_all_updates_btn.isEnabled() is True


def test_run_vendor_update_deletes_the_downloaded_installer_on_success(monkeypatch, tmp_path):
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684",
                        device_id="PCI\\DEV1")
    downloaded = tmp_path / "installer.exe"
    downloaded.write_bytes(b"fake")

    monkeypatch.setattr(dmod.vendor_pipeline, "download_and_verify",
                        lambda update, allowed_domains, extra_headers=None: dmod.vendor_pipeline.DownloadResult(
                            path=str(downloaded)))
    monkeypatch.setattr(dmod.vendor_pipeline, "install_light",
                        lambda path, driver: dmod.InstallResult(
                            ok=True, reason="", restore_point_taken=True))
    from modules.driver_manager.vendor_updates import rollback as rb_mod
    monkeypatch.setattr(rb_mod, "snapshot_before_install", lambda driver: "oem12.inf")
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    monkeypatch.setattr(dmod.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(dmod.DriverModule, "_reread_driver_version", lambda self, driver: "1.0")

    mod._run_vendor_update(driver, _fake_provider(), _fake_update())

    assert downloaded.exists() is False


def test_run_vendor_update_deletes_the_downloaded_installer_even_when_install_fails(monkeypatch, tmp_path):
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684",
                        device_id="PCI\\DEV1")
    downloaded = tmp_path / "installer.exe"
    downloaded.write_bytes(b"fake")

    monkeypatch.setattr(dmod.vendor_pipeline, "download_and_verify",
                        lambda update, allowed_domains, extra_headers=None: dmod.vendor_pipeline.DownloadResult(
                            path=str(downloaded)))
    monkeypatch.setattr(dmod.vendor_pipeline, "install_light",
                        lambda path, driver: dmod.InstallResult(
                            ok=False, reason="pnputil failed", restore_point_taken=True))
    from modules.driver_manager.vendor_updates import rollback as rb_mod
    monkeypatch.setattr(rb_mod, "snapshot_before_install", lambda driver: "oem12.inf")
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    warned = []
    monkeypatch.setattr(dmod.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a[2]))

    mod._run_vendor_update(driver, _fake_provider(), _fake_update())

    assert downloaded.exists() is False
    assert warned  # install failure still reported
    # no token recorded, button stays disabled -- the install failed
    assert mod._applied_update_tokens == {}
    assert mod._undo_all_updates_btn.isEnabled() is False


class _RecordingPool:
    def __init__(self):
        self.started = []

    def start(self, worker) -> None:
        self.started.append(worker)
        worker.run()


def test_undo_this_update_is_disabled_when_nothing_was_updated_this_session():
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", inf_name="oem12.inf", device_id="PCI\\DEV1")
    assert mod._can_undo_update(driver) is False


def test_undo_this_update_rolls_back_the_devices_own_token(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", inf_name="oem12.inf", device_id="PCI\\DEV1")
    mod._applied_update_tokens["PCI\\DEV1"] = "oem12.inf"
    assert mod._can_undo_update(driver) is True
    called = []
    monkeypatch.setattr(dmod, "rollback_one", lambda token: called.append(token) or
                        dmod.InstallResult(ok=True, reason="", restore_point_taken=True))
    pool = _RecordingPool()
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance", staticmethod(lambda: pool))
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._undo_this_update(driver)
    assert called == ["oem12.inf"]
    assert mod._applied_update_tokens == {}  # consumed on success
    assert shown
    assert len(pool.started) == 1
    assert isinstance(pool.started[0], dmod.Worker)


def test_undo_this_update_still_works_after_a_refresh_changes_the_infs_oem_number():
    """Finding I1: after Refresh, Win32_PnPSignedDriver.InfName reports the
    NEW oem number for this device -- keying the lookup on
    published_name_for(driver.inf_name) permanently fails to match past
    that point. Keying on device_id (stable across Refresh) is the fix."""
    mod = _module()
    mod._applied_update_tokens["PCI\\DEV1"] = "oem12.inf"
    # Simulate the post-Refresh DriverInfo: same device_id, new inf_name.
    driver_after = DriverInfo(device_name="A", driver_class="Net", version="2.0",
                              date="", publisher="V", signed=True, error_code=0,
                              flags="", inf_name="oem77.inf", device_id="PCI\\DEV1")
    assert mod._can_undo_update(driver_after) is True


def test_undo_this_update_does_not_raise_if_the_token_is_already_gone_when_the_result_lands(monkeypatch):
    """Nothing disables the row action or the toolbar button while a
    rollback is in flight -- only the enable checks at menu-open/dispatch
    time -- so the token this worker is rolling back can legitimately be
    removed by something else (a concurrent "Undo All", or this same undo
    triggered twice) before its own result arrives. A bare
    `del dict[key]` would raise KeyError in that case; simulate it by
    having the rollback itself clear the dict as a side effect (standing
    in for a concurrent "Undo All" finishing first) before returning
    success."""
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", inf_name="oem12.inf", device_id="PCI\\DEV1")
    mod._applied_update_tokens["PCI\\DEV1"] = "oem12.inf"

    def fake_rollback(token):
        mod._applied_update_tokens.clear()
        return dmod.InstallResult(ok=True, reason="", restore_point_taken=True)

    monkeypatch.setattr(dmod, "rollback_one", fake_rollback)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _RecordingPool()))
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._undo_this_update(driver)  # must not raise KeyError
    assert mod._applied_update_tokens == {}
    assert shown


def test_undo_this_update_reports_a_rollback_failure(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", inf_name="oem12.inf", device_id="PCI\\DEV1")
    mod._applied_update_tokens["PCI\\DEV1"] = "oem12.inf"
    monkeypatch.setattr(dmod, "rollback_one",
                        lambda token: dmod.InstallResult(ok=False, reason="store pruned it",
                                                         restore_point_taken=True))
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _RecordingPool()))
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "warning",
                        lambda *a, **k: shown.append(a[2]))
    mod._undo_this_update(driver)
    assert shown
    assert "store pruned it" in shown[0]
    # token stays -- rollback failed, nothing to consume
    assert mod._applied_update_tokens == {"PCI\\DEV1": "oem12.inf"}


def test_undo_this_update_does_nothing_if_the_user_declines_the_confirm(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", inf_name="oem12.inf", device_id="PCI\\DEV1")
    mod._applied_update_tokens["PCI\\DEV1"] = "oem12.inf"
    called = []
    monkeypatch.setattr(dmod, "rollback_one", lambda token: called.append(token))
    pool = _RecordingPool()
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance", staticmethod(lambda: pool))
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.No)
    mod._undo_this_update(driver)
    assert called == []
    assert pool.started == []
    assert mod._applied_update_tokens == {"PCI\\DEV1": "oem12.inf"}


def test_undo_all_updates_this_session_reports_every_result(monkeypatch):
    mod = _module()
    mod._applied_update_tokens.update({"PCI\\DEV1": "oem1.inf", "PCI\\DEV2": "oem2.inf"})
    monkeypatch.setattr(dmod, "bulk_rollback_all", lambda tokens: [
        dmod.InstallResult(ok=True, reason="", restore_point_taken=True),
        dmod.InstallResult(ok=False, reason="not found", restore_point_taken=True),
    ])
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _RecordingPool()))
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._undo_all_updates_this_session()
    assert shown
    assert "1" in shown[0] and "not found" in shown[0]
    # Finding I2: only the SUCCEEDED device_id (PCI\DEV1, the first result)
    # is dropped -- the failed one (PCI\DEV2) must remain, retryable.
    assert mod._applied_update_tokens == {"PCI\\DEV2": "oem2.inf"}


def test_undo_all_updates_this_session_dispatches_on_a_worker_not_inline(monkeypatch):
    mod = _module()
    mod._applied_update_tokens["PCI\\DEV1"] = "oem1.inf"

    def slow_bulk_rollback(tokens):
        raise AssertionError("bulk_rollback_all must not run before the "
                             "worker is actually started")

    monkeypatch.setattr(dmod, "bulk_rollback_all", slow_bulk_rollback)

    class _NeverRunPool:
        def __init__(self):
            self.started = []

        def start(self, worker) -> None:
            self.started.append(worker)
            # Deliberately never calls worker.run() -- proves dispatch
            # alone doesn't execute the real rollback inline.

    pool = _NeverRunPool()
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance", staticmethod(lambda: pool))
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._undo_all_updates_this_session()
    assert len(pool.started) == 1
    assert isinstance(pool.started[0], dmod.Worker)
    assert not shown  # nothing shown yet -- the worker never ran


def test_undo_all_updates_this_session_keeps_the_failed_token_only(monkeypatch):
    """Finding I2, isolated: 1 of 2 succeeds -- exactly the failed
    device_id's token remains afterward, not an empty dict."""
    mod = _module()
    mod._applied_update_tokens.update({"PCI\\OK": "oem1.inf", "PCI\\FAIL": "oem2.inf"})

    def fake_bulk(tokens):
        # Positionally aligned with the input token list.
        return [
            dmod.InstallResult(ok=(t == "oem1.inf"), reason="" if t == "oem1.inf" else "gone",
                               restore_point_taken=True)
            for t in tokens
        ]

    monkeypatch.setattr(dmod, "bulk_rollback_all", fake_bulk)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _RecordingPool()))
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(dmod.QMessageBox, "information", lambda *a, **k: None)
    mod._undo_all_updates_this_session()
    assert mod._applied_update_tokens == {"PCI\\FAIL": "oem2.inf"}


# ----------------------------------------------------------------------
# Update Status column + history recording
# ----------------------------------------------------------------------

def test_update_status_column_shows_a_dash_for_a_device_with_no_provider(monkeypatch):
    monkeypatch.setattr(dmod, "provider_for", lambda d: None)
    driver = DriverInfo(device_name="Intel Card", driver_class="Display", version="1.0",
                        date="", publisher="V", signed=True, error_code=0, flags="",
                        hardware_id="PCI\\VEN_8086&DEV_A780", device_id="PCI\\INTEL1")
    mod = _module()
    mod._populate([driver])
    assert mod._table.item(0, len(dmod.COLUMNS) - 1).text() == "—"


def test_check_for_vendor_update_records_history_and_updates_the_status_cell(monkeypatch):
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684",
                        device_id="PCI\\DEV1")
    monkeypatch.setattr(dmod, "provider_for", lambda d: _fake_nvidia_update_provider())
    monkeypatch.setattr(dmod, "is_admin", lambda: False)  # stop before the mode dialog
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    monkeypatch.setattr(dmod.QMessageBox, "information", lambda *a, **k: None)
    mod = _module()
    mod._populate([driver])
    mod._check_for_vendor_update(driver)
    history = uh.get("PCI\\DEV1")
    assert history is not None
    assert history.last_check_outcome == uh.OUTCOME_UPDATE_FOUND
    assert history.last_seen_vendor_version == "2.0"
    status_cell = mod._table.item(0, len(dmod.COLUMNS) - 1)
    assert "2.0" in status_cell.text()


def _fake_provider_for_bulk(vendor_name="NVIDIA"):
    class _P:
        vendor_name = vendor_name
        allowed_download_domains = ["download.nvidia.com"]
    return _P()


def test_check_all_for_updates_finds_updates_and_offers_bulk_install(monkeypatch):
    d1 = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display", version="1.0",
                    date="", publisher="V", signed=True, error_code=0, flags="",
                    hardware_id="PCI\\VEN_10DE&DEV_2684", device_id="PCI\\DEV1")
    d2 = DriverInfo(device_name="Some Unrelated Device", driver_class="System", version="1.0",
                    date="", publisher="V", signed=True, error_code=0, flags="",
                    hardware_id="PCI\\VEN_FFFF&DEV_0000", device_id="PCI\\DEV2")
    mod = _module()
    mod._drivers_ref[0] = [d1, d2]

    class _P:
        vendor_name = "NVIDIA"

        def check_for_update(self, driver):
            return _fake_update()

    def fake_provider_for(d):
        return _P() if d is d1 else None

    monkeypatch.setattr(dmod, "provider_for", fake_provider_for)
    monkeypatch.setattr(dmod, "is_admin", lambda: True)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance", staticmethod(lambda: _SyncPool()))
    asked = []

    def fake_ask_bulk(self, found, checked_count):
        asked.append((found, checked_count))
        return "light"

    monkeypatch.setattr(dmod.DriverModule, "_ask_bulk_install_mode", fake_ask_bulk)
    bulk_calls = []
    monkeypatch.setattr(dmod.DriverModule, "_bulk_install",
                        lambda self, found, mode: bulk_calls.append((found, mode)))

    mod._check_all_for_updates()

    assert len(asked) == 1
    assert asked[0][1] == 1  # only d1 has a provider -- d2 is filtered out before the sweep
    assert len(bulk_calls) == 1
    found, mode = bulk_calls[0]
    assert mode == "light"
    assert len(found) == 1
    assert found[0][0] is d1


def test_ask_bulk_install_mode_returns_the_clicked_choice():
    # Real QMessageBox, real buttons -- clickedButton() is simulated by
    # calling the button's own click() rather than faking the whole Qt
    # class, since QMessageBox.exec() would otherwise block on a real
    # modal event loop in a headless test.
    mod = _module()
    d1 = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display", version="1.0",
                    date="", publisher="V", signed=True, error_code=0, flags="")
    found = [(d1, _fake_provider(), _fake_update())]

    from PyQt6.QtWidgets import QMessageBox as RealQMessageBox
    original_exec = RealQMessageBox.exec

    def fake_exec(self):
        # Click "Install All (FULL)" instead of actually blocking on exec().
        for btn in self.buttons():
            if btn.text().replace("&", "") == "Install All (FULL)":
                btn.click()
                return 0
        return original_exec(self)

    RealQMessageBox.exec = fake_exec
    try:
        result = mod._ask_bulk_install_mode(found, checked_count=5)
    finally:
        RealQMessageBox.exec = original_exec
    assert result == "full"


def test_check_all_for_updates_reports_nothing_found_and_still_no_ops_cleanly(monkeypatch):
    d1 = DriverInfo(device_name="Some Unrelated Device", driver_class="System", version="1.0",
                    date="", publisher="V", signed=True, error_code=0, flags="",
                    hardware_id="PCI\\VEN_FFFF&DEV_0000", device_id="PCI\\DEV1")
    mod = _module()
    mod._drivers_ref[0] = [d1]
    monkeypatch.setattr(dmod, "provider_for", lambda d: None)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    mod._check_all_for_updates()
    assert shown
    assert "no devices" in shown[0].lower()


def test_bulk_install_records_history_and_undo_tokens_for_each_success(monkeypatch, tmp_path):
    d1 = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display", version="1.0",
                    date="", publisher="V", signed=True, error_code=0, flags="",
                    hardware_id="PCI\\VEN_10DE&DEV_2684", device_id="PCI\\DEV1")
    downloaded = tmp_path / "installer.exe"
    downloaded.write_bytes(b"fake")

    monkeypatch.setattr(dmod.vendor_pipeline, "download_and_verify",
                        lambda update, allowed_domains, extra_headers=None:
                            dmod.vendor_pipeline.DownloadResult(path=str(downloaded)))
    monkeypatch.setattr(dmod.vendor_pipeline, "install_light",
                        lambda path, driver: dmod.InstallResult(ok=True, reason="", restore_point_taken=True))
    from modules.driver_manager.vendor_updates import rollback as rb_mod
    monkeypatch.setattr(rb_mod, "snapshot_before_install", lambda driver: "oem12.inf")
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance", staticmethod(lambda: _SyncPool()))
    monkeypatch.setattr(dmod.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(dmod.DriverModule, "_reread_driver_version", lambda self, driver: "2.0")

    mod = _module()
    mod._populate([d1])

    mod._bulk_install([(d1, _fake_provider(), _fake_update())], "light")

    assert mod._applied_update_tokens == {"PCI\\DEV1": "oem12.inf"}
    history = uh.get("PCI\\DEV1")
    assert history is not None
    assert history.last_applied_vendor_version == "2.0"
    assert history.last_applied_windows_version == "2.0"
    assert history.last_applied_mode == "light"


def test_bulk_install_reports_a_failed_device_without_stopping_the_batch(monkeypatch, tmp_path):
    d1 = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display", version="1.0",
                    date="", publisher="V", signed=True, error_code=0, flags="",
                    hardware_id="PCI\\VEN_10DE&DEV_2684", device_id="PCI\\DEV1")
    downloaded = tmp_path / "installer.exe"
    downloaded.write_bytes(b"fake")

    monkeypatch.setattr(dmod.vendor_pipeline, "download_and_verify",
                        lambda update, allowed_domains, extra_headers=None:
                            dmod.vendor_pipeline.DownloadResult(path=None, reason="signature invalid"))
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance", staticmethod(lambda: _SyncPool()))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information", lambda *a, **k: shown.append(a[2]))

    mod = _module()
    mod._bulk_install([(d1, _fake_provider(), _fake_update())], "light")

    assert shown
    assert "signature invalid" in shown[0]
    assert mod._applied_update_tokens == {}


# ----------------------------------------------------------------------
# manual_download_only (e.g. Realtek: real update, no automated download)
# ----------------------------------------------------------------------

def _fake_manual_only_provider():
    class _P:
        vendor_name = "Realtek"

        def check_for_update(self, driver):
            from modules.driver_manager.vendor_updates.provider import UpdateInfo
            return UpdateInfo(vendor="Realtek", current_version="10.74.1128.2024",
                              latest_version="10.80.50",
                              download_url="https://www.realtek.com/Download/List?cate_id=584",
                              installer_signer="Realtek Semiconductor Corp.",
                              manual_download_only=True)
    return _P()


def test_check_for_vendor_update_shows_an_informational_message_for_manual_download_only(monkeypatch):
    driver = DriverInfo(device_name="Realtek PCIe 5GbE Family Controller", driver_class="Net",
                        version="10.74.1128.2024", date="", publisher="Realtek", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10EC&DEV_8126",
                        device_id="PCI\\DEV1")
    monkeypatch.setattr(dmod, "provider_for", lambda d: _fake_manual_only_provider())
    monkeypatch.setattr(dmod, "is_admin", lambda: True)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance", staticmethod(lambda: _SyncPool()))
    asked = []
    monkeypatch.setattr(dmod.DriverModule, "_ask_install_mode",
                        lambda self, driver, update: asked.append(1))
    shown = []
    # A plain QMessageBox.information() call's text can't be clicked or
    # selected -- this message carries a URL, so it must go through
    # _show_info_with_link (a real clickable <a href> anchor) instead.
    monkeypatch.setattr(dmod.DriverModule, "_show_info_with_link",
                        lambda self, title, html: shown.append(html))

    mod = _module()
    mod._check_for_vendor_update(driver)

    assert not asked  # never offered a LIGHT/FULL choice for this update
    assert shown
    assert "10.80.50" in shown[0]
    assert 'href="https://www.realtek.com/Download/List?cate_id=584"' in shown[0]


def test_check_all_for_updates_separates_manual_only_from_auto_installable(monkeypatch):
    d1 = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display", version="1.0",
                    date="", publisher="V", signed=True, error_code=0, flags="",
                    hardware_id="PCI\\VEN_10DE&DEV_2684", device_id="PCI\\DEV1")
    d2 = DriverInfo(device_name="Realtek PCIe 5GbE Family Controller", driver_class="Net",
                    version="10.74.1128.2024", date="", publisher="Realtek", signed=True,
                    error_code=0, flags="", hardware_id="PCI\\VEN_10EC&DEV_8126",
                    device_id="PCI\\DEV2")

    class _AutoP:
        vendor_name = "NVIDIA"
        def check_for_update(self, driver):
            return _fake_update()

    def fake_provider_for(d):
        return _AutoP() if d is d1 else _fake_manual_only_provider()

    mod = _module()
    mod._drivers_ref[0] = [d1, d2]
    monkeypatch.setattr(dmod, "provider_for", fake_provider_for)
    monkeypatch.setattr(dmod, "is_admin", lambda: True)
    monkeypatch.setattr(dmod.QThreadPool, "globalInstance", staticmethod(lambda: _SyncPool()))
    asked = []

    def fake_ask_bulk(self, found, checked_count):
        asked.append(found)
        return None  # skip installing

    monkeypatch.setattr(dmod.DriverModule, "_ask_bulk_install_mode", fake_ask_bulk)
    shown = []
    monkeypatch.setattr(dmod.DriverModule, "_show_info_with_link",
                        lambda self, title, html: shown.append(html))

    mod._check_all_for_updates()

    assert len(asked) == 1
    assert len(asked[0]) == 1
    assert asked[0][0][0] is d1  # only the auto-installable one reaches the bulk-mode choice
    assert any("manual download" in s.lower() for s in shown)
    assert any("href=" in s for s in shown)  # a real clickable link, not inert text


def test_show_info_with_link_makes_its_url_actually_clickable_and_selectable(monkeypatch):
    # The real bug report: QMessageBox.information's plain text can't be
    # clicked OR selected -- "what good is a link in a picture". Fake the
    # box construction/exec (avoids a real modal loop) but use the REAL
    # PyQt6 QLabel/QMessageBox classes so the actual flags being set are
    # what's under test, not a test double's promise that they would be.
    from PyQt6.QtWidgets import QLabel as RealQLabel
    from PyQt6.QtCore import Qt

    mod = _module()
    boxes = []
    original_init = dmod.QMessageBox.__init__

    class _CapturedBox(dmod.QMessageBox):
        def __init__(self, *a, **k):
            original_init(self, *a, **k)
            boxes.append(self)

        def exec(self):
            return 0  # never actually block on a modal loop

    monkeypatch.setattr(dmod, "QMessageBox", _CapturedBox)
    mod._show_info_with_link("Title", 'Visit <a href="https://example.com/x">https://example.com/x</a>')

    assert len(boxes) == 1
    label = boxes[0].findChild(RealQLabel)
    assert label is not None
    flags = label.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
    assert flags & Qt.TextInteractionFlag.LinksAccessibleByMouse
    assert label.openExternalLinks() is True
