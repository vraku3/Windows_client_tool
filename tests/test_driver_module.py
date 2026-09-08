"""DriverModule's "Hide pseudo-devices" checkbox: D27 wires
`driver_reader._PSEUDO_CLASSES` into `_populate`'s filter predicate,
defaulting to checked (pseudo-class drivers hidden)."""
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
