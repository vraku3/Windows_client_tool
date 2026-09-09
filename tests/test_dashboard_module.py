"""Dashboard's driver-problem tile.

Reads Driver Manager's already-fetched, live driver list via the module
registry -- it never triggers a new scan itself. `_driver_problem_count`
is a plain module-level function precisely so it can be unit-tested here
without building a real `_DashboardWidget` or a Qt application.
"""
from modules.dashboard.dashboard_module import _driver_problem_count
from modules.driver_manager.driver_reader import DriverInfo


class _FakeDriverModule:
    name = "Driver Manager"

    def __init__(self, drivers):
        self._drivers_ref = [drivers]


class _FakeRegistry:
    def __init__(self, modules):
        self.modules = modules


class _FakeApp:
    def __init__(self, modules):
        self.module_registry = _FakeRegistry(modules)


def test_driver_problem_count_counts_unsigned_and_errored_devices():
    drivers = [
        DriverInfo(device_name="OK", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
        DriverInfo(device_name="Unsigned", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=False, error_code=0, flags=""),
        DriverInfo(device_name="Errored", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=28, flags=""),
    ]
    app = _FakeApp([_FakeDriverModule(drivers)])
    assert _driver_problem_count(app) == 2


def test_driver_problem_count_is_none_when_driver_manager_not_found():
    app = _FakeApp([])
    assert _driver_problem_count(app) is None


def test_driver_problem_count_is_none_when_driver_manager_has_not_scanned_yet():
    """`_drivers_ref[0]` is still the empty list DriverModule.__init__ seeds
    it with -- that's "never scanned", not "scanned and found zero
    drivers", and must not render as the tile's real 0 answer."""
    app = _FakeApp([_FakeDriverModule([])])
    assert _driver_problem_count(app) is None


def test_driver_problem_count_is_a_real_zero_after_a_clean_scan():
    drivers = [
        DriverInfo(device_name="OK", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    app = _FakeApp([_FakeDriverModule(drivers)])
    assert _driver_problem_count(app) == 0
