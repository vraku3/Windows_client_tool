from modules.driver_manager import driver_detail_dialog as ddmod
from modules.driver_manager.driver_detail_dialog import DriverDetailDialog
from modules.driver_manager.driver_reader import DriverInfo


class _SyncPool:
    """Runs a Worker synchronously on the calling thread instead of a real
    background one -- this codebase's established idiom for driving a
    Worker-based method in a test (see test_driver_module.py's own
    `_SyncPool`)."""

    def start(self, worker) -> None:
        worker.run()


def _driver():
    return DriverInfo(
        device_name="AMD Radeon RX 7900 XTX", driver_class="Display",
        version="31.0.1", date="2026-06-01", publisher="Advanced Micro Devices",
        signed=True, error_code=0, flags="", inf_name="oem12.inf",
        device_id="PCI\\VEN_1002&DEV_744C\\4&abc", hardware_id="PCI\\VEN_1002&DEV_744C",
        whql_certified=True,
    )


def test_dialog_shows_the_devices_own_fields(qapp, monkeypatch):
    monkeypatch.setattr(
        "modules.driver_manager.driver_detail_dialog.driver_store_size",
        lambda d: 1234567)
    monkeypatch.setattr(
        "modules.driver_manager.driver_detail_dialog.crashes_for",
        lambda name, records: [])
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    all_text = " ".join(
        w.text() for w in dlg.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)
        if hasattr(w, "text"))
    assert "AMD Radeon RX 7900 XTX" in all_text
    assert "PCI\\VEN_1002&DEV_744C" in all_text
    assert "oem12.inf" in all_text


def test_dialog_shows_whql_yes_or_no(qapp, monkeypatch):
    # `_driver()` carries a real OEM-numbered inf_name ("oem12.inf") --
    # without this monkeypatch (matching the sibling test just above),
    # __init__'s background size worker calls the REAL driver_store_size(),
    # which does a real registry lookup plus a real folder walk on any
    # machine where oem12 happens to exist.
    monkeypatch.setattr(
        "modules.driver_manager.driver_detail_dialog.driver_store_size",
        lambda d: 1234567)
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    labels = [w.text() for w in dlg.findChildren(
        __import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert any("WHQL" in t for t in labels)


def test_whql_shows_not_applicable_for_a_driverless_device(qapp, monkeypatch):
    """A device with NO driver installed at all (inf_name == "", see
    driver_reader._merge_driverless_devices) has no driver to be WHQL
    certified or not -- a confident "No" overstates what Windows actually
    knows here."""
    monkeypatch.setattr(
        "modules.driver_manager.driver_detail_dialog.driver_store_size",
        lambda d: None)
    driverless = DriverInfo(
        device_name="Unknown device", driver_class="Net", version="",
        date="", publisher="", signed=False, error_code=28, flags="",
        inf_name="", whql_certified=False,
    )
    dlg = DriverDetailDialog(driverless, reliability_records=[])
    labels = [w.text() for w in dlg.findChildren(
        __import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    whql_label_idx = next(i for i, t in enumerate(labels) if "WHQL" in t)
    # `_row()` builds a bold label ("<b>WHQL Certified:</b>") immediately
    # followed by its own value QLabel -- the very next label in
    # construction order, per `_row()`'s own layout.
    assert labels[whql_label_idx + 1] == "N/A"


# ----------------------------------------------------------------------
# Final-review finding I1 (site A): reliability_records=None ("we never
# looked") must render differently from reliability_records=[] ("a real
# search ran and found nothing") -- driver_module.py's call site always
# passes the former today, since it never actually fetches Reliability
# Monitor data (Task 10's documented scope decision).
# ----------------------------------------------------------------------


def _crashes_text(dlg) -> str:
    from PyQt6.QtWidgets import QTextEdit
    views = dlg.findChildren(QTextEdit)
    assert len(views) == 1
    return views[0].toPlainText()


def test_dialog_says_not_loaded_when_reliability_records_is_none(qapp):
    dlg = DriverDetailDialog(_driver(), reliability_records=None)
    text = _crashes_text(dlg)
    assert "not loaded" in text.lower()
    assert "No matching entries found." not in text


def test_dialog_defaults_to_not_loaded_when_reliability_records_omitted(qapp):
    dlg = DriverDetailDialog(_driver())
    text = _crashes_text(dlg)
    assert "not loaded" in text.lower()


def test_dialog_says_no_matching_entries_when_a_real_empty_search_ran(qapp, monkeypatch):
    monkeypatch.setattr(
        "modules.driver_manager.driver_detail_dialog.crashes_for",
        lambda name, records: [])
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    text = _crashes_text(dlg)
    assert text == "No matching entries found."


# ----------------------------------------------------------------------
# Double-click wiring gap: the brief's own Files section calls for
# "double-click + a Details... context-menu action", but only sketches
# the context-menu half. driver_module.py must resolve the clicked row
# to its real DriverInfo the same way _on_context_menu does (by
# device_id/_row_dedup_key, never by device_name alone -- device names
# collide, see test_driver_module.py's "colliding hubs" tests), and open
# the same DriverDetailDialog.
# ----------------------------------------------------------------------


def _colliding_hubs():
    return [
        DriverInfo("USB Root Hub (USB 3.0)", "USB", "1.0", "", "V", True, 0, "",
                   inf_name="oem10.inf", device_id="USB\\ROOT_HUB30\\1"),
        DriverInfo("USB Root Hub (USB 3.0)", "USB", "1.0", "", "V", True, 0, "",
                   inf_name="oem20.inf", device_id="USB\\ROOT_HUB30\\2"),
    ]


def test_double_click_opens_details_for_the_correct_row_when_names_collide(qapp, monkeypatch):
    from PyQt6.QtCore import Qt
    from modules.driver_manager import driver_module as dmod

    mod = dmod.DriverModule()
    mod.create_widget()
    mod._drivers_ref[0] = _colliding_hubs()
    mod._populate(mod._drivers_ref[0], "")

    table = mod._table
    row = next(r for r in range(table.rowCount())
              if table.item(r, 0).data(Qt.ItemDataRole.UserRole)
              == "USB\\ROOT_HUB30\\2")

    opened = []

    class _FakeDialog:
        def __init__(self, driver, reliability_records=None, parent=None):
            opened.append(driver)

        def exec(self):
            pass

    # _show_driver_details imports DriverDetailDialog locally from its
    # defining module (same pattern the brief's sample uses) -- patch it
    # at that origin, not on driver_module itself.
    monkeypatch.setattr(
        "modules.driver_manager.driver_detail_dialog.DriverDetailDialog",
        _FakeDialog)

    mod._table.cellDoubleClicked.emit(row, 0)

    assert len(opened) == 1
    # The SECOND device's own package, not the first row that happens to
    # share its visible name.
    assert opened[0].device_id == "USB\\ROOT_HUB30\\2"


def test_resolve_driver_for_row_matches_context_menu_resolution(monkeypatch):
    """`_resolve_driver_for_row` and `_on_context_menu`'s own resolution
    must agree -- both exist to solve the identical name-collision
    problem, so a divergence between them would silently resolve
    double-click and right-click to different devices on the same row."""
    from modules.driver_manager import driver_module as dmod

    mod = dmod.DriverModule()
    mod.create_widget()
    mod._drivers_ref[0] = _colliding_hubs()
    mod._populate(mod._drivers_ref[0], "")

    for row in range(mod._table.rowCount()):
        resolved = mod._resolve_driver_for_row(row)
        assert resolved is not None
        assert resolved.device_id == mod._table.item(row, 0).data(
            __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ItemDataRole.UserRole)


# ----------------------------------------------------------------------
# Final-review finding I4: driver_store_size() walks a real folder tree on
# disk (GB-scale for some packages, per its own docstring) and must not
# run synchronously in __init__ -- it now runs on a background Worker,
# with the row showing "Calculating..." until the result lands.
# ----------------------------------------------------------------------


def test_size_row_shows_calculating_before_the_worker_reports_back(qapp, monkeypatch):
    # A pool that never actually runs the worker -- the "while running"
    # state must be observable, not just the eventual result.
    monkeypatch.setattr(
        ddmod.QThreadPool, "globalInstance",
        staticmethod(lambda: type("NoopPool", (), {"start": lambda self, w: None})()))
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    assert dlg._size_lbl.text() == "Calculating…"


def test_size_row_updates_once_the_worker_reports_back(qapp, monkeypatch):
    monkeypatch.setattr(ddmod, "driver_store_size", lambda d: 1234567)
    monkeypatch.setattr(ddmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    assert dlg._size_lbl.text() == "1,234,567 bytes"


def test_size_row_shows_unknown_when_the_size_cannot_be_determined(qapp, monkeypatch):
    monkeypatch.setattr(ddmod, "driver_store_size", lambda d: None)
    monkeypatch.setattr(ddmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    assert dlg._size_lbl.text() == "Unknown"


def test_size_row_shows_unknown_when_the_worker_errors(qapp, monkeypatch):
    """Without an error handler, an exception in driver_store_size() left
    the row reading "Calculating…" forever with no way to notice."""
    def _raise(_d):
        raise OSError("boom")

    monkeypatch.setattr(ddmod, "driver_store_size", _raise)
    monkeypatch.setattr(ddmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _SyncPool()))
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    assert dlg._size_lbl.text() == "Unknown"


def test_reject_cancels_the_size_worker(qapp, monkeypatch):
    started = []

    class _RecordingPool:
        def start(self, worker) -> None:
            started.append(worker)
            # Deliberately NOT run here -- simulates the worker still
            # being in flight when the dialog is closed below.

    monkeypatch.setattr(ddmod, "driver_store_size", lambda d: 999)
    monkeypatch.setattr(ddmod.QThreadPool, "globalInstance",
                        staticmethod(lambda: _RecordingPool()))

    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    assert len(started) == 1
    worker = started[0]

    dlg.reject()
    assert worker.is_cancelled is True


def test_closing_the_dialog_before_the_worker_completes_does_not_crash(qapp, monkeypatch):
    """The dialog can be closed (Close button / Esc / window X, all of
    which route through reject()) before the size calculation finishes.
    A stale result landing on an ALREADY-DESTROYED dialog (what real Qt
    teardown does -- see test_widget_life.py / test_cleanup_late_signal.py's
    own `sip.delete()` pattern) must be a no-op via the `widget_is_valid`
    guard, not a crash on a dead QLabel.

    Calls `_on_size_computed` directly rather than through the signal:
    `_on_size_computed` is a bound method of the dialog itself, and PyQt
    auto-disconnects a bound-method connection when its receiver QObject
    is destroyed -- emitting through the signal after `sip.delete(dlg)`
    would pass trivially without ever reaching the guard. Calling the
    method directly is what actually exercises `widget_is_valid`."""
    from PyQt6 import sip

    # A pool that never runs the worker -- keeps this test from spawning a
    # real background thread that walks the filesystem after the dialog
    # (and the test) is gone.
    monkeypatch.setattr(
        ddmod.QThreadPool, "globalInstance",
        staticmethod(lambda: type("NoopPool", (), {"start": lambda self, w: None})()))

    dlg = DriverDetailDialog(_driver(), reliability_records=[])

    dlg.reject()
    sip.delete(dlg)  # what Qt teardown does to a closed dialog

    # Must not raise -- the guard inside _on_size_computed skips the dead
    # dialog before touching the (also-destroyed) _size_lbl.
    dlg._on_size_computed(999)
