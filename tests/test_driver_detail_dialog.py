from modules.driver_manager.driver_detail_dialog import DriverDetailDialog
from modules.driver_manager.driver_reader import DriverInfo


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


def test_dialog_shows_whql_yes_or_no(qapp):
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    labels = [w.text() for w in dlg.findChildren(
        __import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert any("WHQL" in t for t in labels)


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
