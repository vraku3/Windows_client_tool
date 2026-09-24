r"""Devices that run the same driver collapse into one row.

Requested 2026-09-24: 32 logical processors were 32 "AMD Processor" rows, all
the same INF at the same version -- noise in a list meant to be read as
drivers. One row with a count, and a double-click for the individual devices.
"""
import csv

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog

from modules.driver_manager import driver_module as dm
from modules.driver_manager.driver_grouping import DriverGroup, group_drivers
from modules.driver_manager.driver_reader import DriverInfo


def _d(i, name="AMD Processor", version="10.0.26100.9549", inf="cpu.inf",
       signed=True, error=0, flags="", cls="PROCESSOR", publisher="Advanced Micro Devices"):
    return DriverInfo(
        device_name=name, driver_class=cls, version=version, date="2024-04-01",
        publisher=publisher, signed=signed, error_code=error, flags=flags,
        inf_name=inf, device_id=f"ACPI\\CPU_{i}", hardware_id="ACPI\\CPU",
        whql_certified=False)


# -- the pure rules ---------------------------------------------------------

def test_identical_drivers_become_one_group_in_first_seen_order():
    drivers = [_d(i) for i in range(32)] + [_d(99, name="AMD GPIO Controller", inf="gpio.inf")]
    groups = group_drivers(drivers)
    assert [g.count for g in groups] == [32, 1]
    assert groups[0].representative.device_id == "ACPI\\CPU_0"


def test_two_versions_of_one_driver_stay_two_rows():
    groups = group_drivers([_d(1, version="3.0.5.0"), _d(2, version="2.2.0.136")])
    assert [g.count for g in groups] == [1, 1]


def test_a_device_with_an_error_is_never_folded_into_healthy_ones():
    groups = group_drivers([_d(1), _d(2), _d(3, error=10)])
    assert sorted(g.count for g in groups) == [1, 2]


def test_an_unsigned_device_is_never_folded_into_signed_ones():
    groups = group_drivers([_d(1), _d(2, signed=False)])
    assert [g.count for g in groups] == [1, 1]


def test_a_different_inf_or_publisher_is_a_different_driver():
    assert len(group_drivers([_d(1), _d(2, inf="other.inf")])) == 2
    assert len(group_drivers([_d(1), _d(2, publisher="Someone Else")])) == 2


def test_group_flags_are_the_union_of_its_members():
    """Only some members share a hardware id; the warning must still show."""
    group = group_drivers([_d(1, flags=""), _d(2, flags="\U0001F7E0 Shared Hardware ID")])[0]
    assert "Shared Hardware ID" in group.flags


def test_flags_are_not_duplicated_across_members():
    group = group_drivers([_d(i, flags="\U0001F7E0 Shared Hardware ID") for i in range(5)])[0]
    assert group.flags.count("Shared Hardware ID") == 1


def test_a_single_device_is_a_group_of_one():
    group = group_drivers([_d(1)])[0]
    assert group.count == 1 and group.representative.device_id == "ACPI\\CPU_1"


def test_empty_input_gives_no_groups():
    assert group_drivers([]) == []


# -- the table --------------------------------------------------------------

@pytest.fixture
def module(qapp):
    module = dm.DriverModule()
    module.app = type("App", (), {"config": None, "thread_pool": None})()
    module.create_widget()
    return module


def _drivers():
    return ([_d(i) for i in range(32)]
            + [_d(100, name="Realtek NIC", version="11.1", inf="oem4.inf",
                  cls="NET", publisher="Realtek"),
               _d(101, name="AMD GPIO Controller", version="3.0.5.0", inf="gpio.inf", cls="SYSTEM"),
               _d(102, name="AMD GPIO Controller", version="3.0.5.0", inf="gpio.inf", cls="SYSTEM"),
               _d(103, name="AMD GPIO Controller", version="2.2.0.136", inf="gpio.inf", cls="SYSTEM")])


def _names(module):
    return [module._table.item(r, 0).text() for r in range(module._table.rowCount())]


def test_the_table_shows_one_row_per_distinct_driver_with_a_count(module):
    module._populate(_drivers())
    names = _names(module)
    assert len(names) == 4                       # 32 CPUs, NIC, GPIO x2, GPIO 2.2
    assert any(n.startswith("AMD Processor") and "×32" in n for n in names)
    assert any(n.startswith("AMD GPIO Controller") and "×2" in n for n in names)
    assert "Realtek NIC" in names                # a single device has no suffix


def test_turning_grouping_off_shows_every_device(module):
    module._drivers_ref[0] = _drivers()          # what a refresh stores
    module._populate(_drivers())
    module._group_cb.setChecked(False)
    assert module._table.rowCount() == len(_drivers())
    assert not any("×" in n for n in _names(module))


def test_toggling_grouping_back_on_regroups(module):
    module._drivers_ref[0] = _drivers()
    module._group_cb.setChecked(False)
    module._group_cb.setChecked(True)
    assert module._table.rowCount() == 4


def test_the_status_line_says_devices_and_distinct_drivers(module):
    module._drivers_ref[0] = _drivers()
    assert "36 devices, 4 distinct drivers" in module._status_summary(_drivers())
    module._group_cb.setChecked(False)
    assert module._status_summary(_drivers()).startswith("36 drivers")


def test_a_filter_applies_to_devices_before_grouping(module):
    module._populate(_drivers(), "realtek")
    assert _names(module) == ["Realtek NIC"]


def test_context_menu_and_actions_use_the_real_name_not_the_suffixed_one(module):
    module._drivers_ref[0] = _drivers()
    module._populate(_drivers())
    row = next(r for r in range(module._table.rowCount())
               if module._table.item(r, 0).text().startswith("AMD Processor"))
    assert "×" in module._table.item(row, 0).text()
    assert module._resolve_driver_for_row(row).device_name == "AMD Processor"


def test_double_click_on_a_group_opens_the_instances_dialog(module, monkeypatch):
    module._drivers_ref[0] = _drivers()
    module._populate(_drivers())
    row = next(r for r in range(module._table.rowCount())
               if module._table.item(r, 0).text().startswith("AMD Processor"))
    opened = []
    monkeypatch.setattr(module, "_show_group_instances", lambda g: opened.append(g))
    monkeypatch.setattr(module, "_show_driver_details", lambda d: opened.append(("details", d)))
    module._on_cell_double_clicked(row, 0)
    assert len(opened) == 1 and isinstance(opened[0], DriverGroup) and opened[0].count == 32


def test_double_click_on_a_single_device_still_opens_its_details(module, monkeypatch):
    module._drivers_ref[0] = _drivers()
    module._populate(_drivers())
    row = next(r for r in range(module._table.rowCount())
               if module._table.item(r, 0).text() == "Realtek NIC")
    opened = []
    monkeypatch.setattr(module, "_show_group_instances", lambda g: opened.append("group"))
    monkeypatch.setattr(module, "_show_driver_details", lambda d: opened.append(d.device_name))
    module._on_cell_double_clicked(row, 0)
    assert opened == ["Realtek NIC"]


def test_the_instances_dialog_lists_every_device_and_opens_details(qapp, monkeypatch):
    from modules.driver_manager.driver_instances_dialog import DriverInstancesDialog
    group = group_drivers([_d(i) for i in range(5)])[0]
    dlg = DriverInstancesDialog(group)
    assert dlg.table.rowCount() == 5
    assert dlg.table.item(3, 1).text() == "ACPI\\CPU_3"
    assert dlg.member_at(3).device_id == "ACPI\\CPU_3"
    assert dlg.member_at(99) is None
    assert dlg.isSizeGripEnabled() or dlg.minimumWidth() > 0     # a real, resizable window


def test_csv_export_lists_every_device_in_a_grouped_row(module, monkeypatch, tmp_path):
    module._drivers_ref[0] = _drivers()
    module._populate(_drivers())
    out = tmp_path / "drivers.csv"
    monkeypatch.setattr(dm.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    module._do_export()
    rows = list(csv.reader(open(out, encoding="utf-8")))
    assert len(rows) - 1 == len(_drivers())       # header + all 36 devices
