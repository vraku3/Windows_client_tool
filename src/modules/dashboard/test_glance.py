"""Headless regression checks for Dashboard's measured summaries."""
from types import SimpleNamespace as NS

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_overview_tile_loading_and_trend(qapp):
    from ui.glance import GlanceTile
    from .overview_widgets import MetricTile
    tile = MetricTile("CPU")
    assert isinstance(tile, GlanceTile)
    assert "Loading" in tile.reason
    tile.show_reading("81%", "32 logical processors", 81, [12, 40, 81])
    assert tile.value.text() == "81%"
    assert tile.history == [12, 40, 81]


def test_connections_counts_filter_and_refusal(qapp):
    from .connections import Connection
    from .connections_tab import ConnectionsTab
    tab = ConnectionsTab()
    tab._apply([Connection("TCP4", "0.0.0.0", 80, "", 0, "LISTEN", 1, "web"),
                Connection("TCP4", "10.0.0.1", 81, "8.8.8.8", 443, "ESTABLISHED", 2, "browser")])
    assert tab._glance["listening"].value.text() == "1"
    assert tab._glance["external"].value.text() == "1"
    assert "browser" in tab._glance["top"].sub.text()
    tab._glance["external"].clicked.emit()
    assert tab._table.rowCount() == 1
    tab._apply(None)
    assert tab._glance["external"].reason


def test_details_counts_refused_tokens_and_unknown_cpu(qapp):
    from .details_tab import DetailsTab
    tab = DetailsTab()
    rows = [NS(details=NS(elevated=True), rates=NS(cpu_percent=15)),
            NS(details=NS(elevated=None), rates=NS(cpu_percent=None))]
    tab._show_summary(NS(by_pid=dict(enumerate(rows))))
    assert tab._glance["elevated"].value.text() == "1"
    assert "1 of 2" in tab._glance["elevated"].sub.text()
    assert tab._glance["unsigned"].reason
    tab._show_summary(NS(by_pid={1: rows[1]}))
    assert tab._glance["high"].reason
    tab._failed("access denied")
    assert all(t.reason for t in tab._glance.values())


def test_app_history_totals_and_chips(qapp):
    from core.procengine.usage import AppUsage
    from core.procengine.columns import fmt_cpu_time
    from .app_history_tab import AppHistoryTab
    tab = AppHistoryTab()
    usage = [AppUsage("Busy", 30_000_000, 2), AppUsage("Idle", 0, 1)]
    tab._apply((NS(by_pid={1: None, 2: None, 3: None}, refused=1), usage))
    assert tab._glance["top"].value.text() == "Busy"
    assert tab._glance["apps"].value.text() == "2"
    assert tab._glance["total"].value.text() == fmt_cpu_time(30_000_000)
    tab._chips.chips["multi"].click()
    assert tab._table.rowCount() == 1
    tab._failed("refused")
    assert tab._glance["total"].reason == "refused"


def test_startup_does_not_invent_impact_or_dates(qapp):
    from .startup_tab import StartupTab
    tab = StartupTab()
    entries = [NS(enabled=True, name="App", command="app.exe", source="run", extra="")]
    tab._apply((entries, []))
    assert tab._glance["enabled"].value.text() == "1"
    assert all(tab._glance[k].reason for k in ("impact", "unsigned", "recent"))
    tab._chips.chips["disabled"].click()
    assert tab._table.rowCount() == 0
    tab._apply(([], ["Run keys refused"]))
    assert tab._glance["enabled"].reason == "Run keys refused"


def test_users_sessions_and_memory_summary(qapp):
    from .users_tab import UsersTab
    from .sessions import Session
    tab = UsersTab()
    tab._show_sessions([Session(1, "Console", "A", "", "Active", ""),
                        Session(2, "RDP-1", "B", "", "Disconnected", "remote")])
    assert tab._session_cards.count() == 2
    assert tab._session_cards.itemAt(1).widget().value.text() == "Disconnected"
    assert tab._glance["active"].value.text() == "1"
    assert tab._glance["disconnected"].value.text() == "1"
    tab._apply((NS(by_pid={}, refused=0), []))
    assert tab._glance["memory"].reason
    tab._show_sessions(None)
    assert tab._glance["active"].reason


def test_power_grid_missing_clocks_and_plan(qapp):
    from .power_tab import PowerTab
    from .power import PowerPlan
    tab = PowerTab()
    nominal = [NS(index=0, current_mhz=4000, max_mhz=4000, limit_mhz=4000, throttled=False)]
    tab._show(nominal, [4400])
    assert tab._glance["clock"].value.text() == "4,400 MHz"
    assert tab._heat._loads == [110]
    tab._show(nominal, None)
    assert tab._heat._loads == [None]
    assert tab._glance["clock"].reason
    tab._plans_loaded([PowerPlan("guid", "Balanced", True)])
    assert tab._glance["plan"].value.text() == "Balanced"
    tab._plans_loaded(None)
    assert tab._glance["plan"].reason


def test_energy_package_is_not_core_total(qapp):
    from .energy_tab import EnergyTab
    from .energy import EnergyReading
    tab = EnergyTab()
    tab._meter = NS(watt_hours=0.1)
    tab._show(EnergyReading(25, {0: 10, 1: 5}))
    assert tab._glance["package"].value.text() == "25.0 W"
    assert tab._glance["cores"].value.text() == "15.0 W"
    tab._show(EnergyReading(None, {0: 10}))
    assert tab._glance["package"].reason
    assert "cores only" in tab._headline.text()
    tab._show(EnergyReading(20, {}))
    assert tab._glance["cores"].reason
    tab._unavailable = "No hardware meter"
    tab.refresh()
    assert all(t.reason for t in tab._glance.values())
    assert tab._empty.variant == "empty"


def test_thermal_summary_and_no_sensors(qapp):
    from .thermal_tab import ThermalTab
    from modules.thermal_control.engine.model import TEMPERATURE
    tab = ThermalTab()
    sensor = NS(kind=TEMPERATURE, value=65, hardware="GPU", display=lambda: "65 C")
    tab._show_readings(([("GPU core", sensor)], "", []))
    assert tab._glance["gpu"].value.text() == "65 C"
    assert tab._glance["alerts"].value.text() == "0"
    tab._show_readings(([], "Sensor access refused", []))
    assert all(t.reason for t in tab._glance.values())
    tab._readings_failed("refused")
    assert tab._empty.variant == "error"


def test_flight_summary_empty_and_loaded(qapp):
    from .flight_tab import FlightTab
    tab = FlightTab()
    assert tab._glance["state"].value.text() == "Ready"
    assert tab._glance["duration"].reason
    tab._trace.samples = [{"t": 4.0}, {"t": 10.0}]
    tab._redraw()
    assert tab._glance["samples"].value.text() == "2"
    assert tab._glance["duration"].value.text() == "6.0 s"
    assert tab._empty.isHidden()


def test_disk_drive_cards_and_usage(qapp):
    from .disk_space_tab import DiskSpaceTab
    tab = DiskSpaceTab()
    assert not tab._empty.action_button.isEnabled()
    tab._apply_volumes([{"mount": "C:/", "fs": "NTFS", "total": 100,
                         "used": 96, "free": 4, "percent": 96}])
    card = tab._drive_cards.itemAt(0).widget()
    assert card.value.text() == "96% used"
    assert card.severity == "danger"
    assert tab._selected_mount() == "C:/"
    assert tab._empty.action_button.isEnabled()


def test_benchmark_bar_compares_same_test_only(qapp, monkeypatch):
    from .benchmarks_tab import BenchmarksTab
    from .benchmarks import BenchResult
    monkeypatch.setattr(BenchmarksTab, "_fill_drives", lambda self: None)
    monkeypatch.setattr(BenchmarksTab, "_show_last_run", lambda self: None)
    tab = BenchmarksTab()
    tab._render([BenchResult("Memory", 80, "MB/s")], {"Memory": -20}, "done")
    bar = tab._table.cellWidget(0, 1)
    assert bar.value() == 800
    assert "100.0 MB/s" in bar.toolTip()
    assert tab._table.cellWidget(0, 2).text() == "-20.0%"
    tab._render([BenchResult("Memory", 0, "MB/s", "could not run")], {}, "failed")
    assert tab._table.cellWidget(0, 1) is None
