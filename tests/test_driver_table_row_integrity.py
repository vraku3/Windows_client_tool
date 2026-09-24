r"""Every row of the Driver Manager table must show its OWN device's data.

Real bug (found 2026-09-24 reviewing the tab): the table has sorting enabled
and `_populate` filled it row by row without turning it off. Setting a row's
first cell re-sorts the table, so the row moves and the rest of its cells
land on another row. Against the real machine 238 of 338 rows showed another
device's version/publisher or nothing at all -- a driver manager showing the
wrong driver version next to a device name.
"""
import random

import pytest
from PyQt6.QtCore import Qt

from modules.driver_manager import driver_module as dm
from modules.driver_manager.driver_reader import DriverInfo


def _driver(i: int, name: str) -> DriverInfo:
    return DriverInfo(
        device_name=name, driver_class=f"CLASS{i % 7}", version=f"{i}.0.{i}.1",
        date=f"2024-{(i % 12) + 1:02d}-15", publisher=f"Publisher {i}",
        signed=True, error_code=0, flags="", inf_name=f"oem{i}.inf",
        device_id=f"PCI\VEN_{i:04X}\DEV", hardware_id=f"PCI\VEN_{i:04X}",
        whql_certified=False)


@pytest.fixture
def module(qapp):
    module = dm.DriverModule()
    module.app = type("App", (), {"config": None, "thread_pool": None})()
    module.create_widget()
    return module


def _names_shuffled(n=60):
    names = [f"Device {chr(65 + (i * 7) % 26)}{i:02d}" for i in range(n)]
    random.Random(7).shuffle(names)          # deliberately not already sorted
    return names


def _assert_every_row_is_its_own(module, drivers):
    by_id = {d.device_id: d for d in drivers}
    table = module._table
    for r in range(table.rowCount()):
        name_item = table.item(r, 0)
        assert name_item is not None, f"row {r} has no name"
        d = by_id[name_item.data(Qt.ItemDataRole.UserRole)]
        assert name_item.text() == d.device_name
        assert table.item(r, 1).text() == d.driver_class, (r, d.device_name)
        assert table.item(r, 2).text() == d.version, (r, d.device_name)
        assert table.item(r, 4).text() == d.publisher, (r, d.device_name)


def test_every_row_shows_its_own_devices_data(module):
    drivers = [_driver(i, n) for i, n in enumerate(_names_shuffled())]
    module._populate(drivers)
    assert module._table.rowCount() == len(drivers)
    _assert_every_row_is_its_own(module, drivers)


def test_it_still_holds_after_a_filter_and_a_repopulate(module):
    drivers = [_driver(i, n) for i, n in enumerate(_names_shuffled())]
    module._populate(drivers)
    module._populate(drivers, "device a")
    _assert_every_row_is_its_own(module, [d for d in drivers
                                          if "device a" in d.device_name.lower()])
    module._populate(drivers)
    _assert_every_row_is_its_own(module, drivers)


def test_sorting_is_back_on_afterwards_and_the_users_sort_survives(module):
    drivers = [_driver(i, n) for i, n in enumerate(_names_shuffled())]
    module._populate(drivers)
    assert module._table.isSortingEnabled()
    names = [module._table.item(r, 0).text() for r in range(module._table.rowCount())]
    order = module._table.horizontalHeader().sortIndicatorOrder()
    expected = sorted(names, reverse=(order == Qt.SortOrder.DescendingOrder),
                      key=str.casefold)
    assert names == expected
