"""Measured F3 summaries and source-failure isolation."""
from datetime import datetime
from types import SimpleNamespace

import pytest


def test_startup_task_root_refusal_keeps_other_sources(monkeypatch):
    import win32com.client
    from modules.startup_manager import persistence as p

    def refused(_path):
        raise OSError("Task root: path not found")

    service = SimpleNamespace(Connect=lambda: None, GetFolder=refused)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _name: service)
    inv = p.Inventory(items=[p.Item("Kept", "app.exe", "Run", "User", "Run")])
    p.read_tasks(inv)
    assert len(inv.items) == 1
    assert any("path not found" in problem for problem in inv.problems)


def test_startup_task_subfolder_refusal_keeps_readable_siblings(monkeypatch):
    import win32com.client
    from modules.startup_manager import persistence as p

    class Folder:
        Path = "\\Protected"

        def GetTasks(self, _flags):
            raise PermissionError("protected folder")

        def GetFolders(self, _flags):
            return []

    root = SimpleNamespace(Path="\\", GetTasks=lambda _flags: [],
                           GetFolders=lambda _flags: [Folder()])
    service = SimpleNamespace(Connect=lambda: None, GetFolder=lambda _path: root)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _name: service)
    inv = p.Inventory()
    p.read_tasks(inv)
    assert any("protected folder" in problem for problem in inv.problems)


def test_restore_glance_preserves_unknown_and_measures_system_volume(qapp):
    from modules.restore_manager.restore_module import RestoreManagerModule, _Snapshot
    from modules.restore_manager import restore_analysis as ra

    mod = RestoreManagerModule()
    root = mod.create_widget()
    mod._snapshot = _Snapshot(points_error="denied", protection_error="denied", storage_error="denied")
    mod._update_glance()
    assert all(tile.value.text() == "—" for tile in mod._glance.tiles)
    mod._snapshot = _Snapshot(points=[], protection=ra.Protection(mounts=["C:"]),
                              storage=[ra.ShadowStorage("C:", used=1024**2, maximum=2*1024**2)])
    mod._update_glance()
    assert [t.value.text() for t in mod._glance.tiles] == ["On", "0", "—", "1 MB / 2 MB"]
    root.deleteLater()


def test_disk_glance_discloses_missing_temperature_and_reader_errors(qapp):
    from modules.disk_health.disk_health_module import _ResultsView
    from modules.disk_health import disk_reader as dr

    view = _ResultsView(dr.DiskReport(errors={"disks": "access denied"}))
    assert view._glance.tiles[0].value.text() == "—"
    assert view._glance.tiles[2].value.text() == "—"
    report = dr.DiskReport(disks=[dr.PhysicalDiskInfo("0", "SSD", health="Healthy", temperature=42)])
    view = _ResultsView(report)
    assert view._glance.tiles[0].value.text() == "1"
    assert view._glance.tiles[2].value.text() == "42 C"


def test_power_cards_keep_windows_plan_identity_and_active_state(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel, QListWidget
    from modules.power_boot.power_module import PowerBootModule

    mod = PowerBootModule()
    plans = QListWidget()
    mod._fill_plan_cards(plans, [("one", "Balanced", True), ("two", "Performance", False)])
    assert plans.count() == 2
    assert plans.currentItem().data(Qt.ItemDataRole.UserRole) == "one"
    card = plans.itemWidget(plans.item(0))
    assert [label.text() for label in card.findChildren(QLabel)] == ["Balanced", "Active plan"]


def test_startup_glance_discloses_partial_reads_and_filters(qapp):
    from modules.startup_manager import persistence as p
    from modules.startup_manager.unified_tab import UnifiedStartupTab

    tab = UnifiedStartupTab()
    assert all(t.value.text() == "—" for t in tab._glance.tiles)
    tab._inventory = p.Inventory(items=[p.Item("App", "app.exe", "Run", "User", "Run")],
                                  problems=["Task Scheduler denied"])
    tab._loaded = True
    tab._render()
    assert tab._glance.tiles[0].value.text() == "1"
    assert "some sources unavailable" in tab._glance.tiles[0].sub.text()
    tab._glance.tiles[1].clicked.emit()
    assert tab._chip == "Flagged"


def test_boot_glance_uses_chronological_duration_history(qapp):
    from modules.boot_analyzer.boot_history_view import BootHistoryPanel
    from modules.boot_analyzer import boot_history as bh

    panel = BootHistoryPanel()
    panel.set_facts(bh.BootFacts(problems=["access denied"]))
    assert panel._duration.value.text() == "—"
    assert "denied" in panel._duration.toolTip()
    boots = [bh.BootRecord(datetime(2026, 10, day), ms, ms, 0, 2, False, 0, 0)
             for day, ms in [(25, 20000), (24, 30000)]]
    panel.set_facts(bh.BootFacts(boots=boots))
    assert panel._duration.value.text() == "20.0 s"
    assert panel._duration.history == [30.0, 20.0]


def test_hardware_overview_cards_keep_missing_values_unknown(qapp):
    from PyQt6.QtWidgets import QWidget, QVBoxLayout
    from ui.glance import GlanceTile
    from modules.hardware_inventory.hardware_tabs import setup_overview

    root = QWidget()
    layout = QVBoxLayout(root)
    setup_overview(layout, {"CPU": [("Model", "Test CPU"), ("Cores", "8")],
                            "Memory": [("Total", "32 GB")]})
    tiles = root.findChildren(GlanceTile)
    assert len(tiles) == 7
    assert tiles[0].value.text() == "Test CPU"
    assert tiles[1].value.text() == "32 GB"
    assert tiles[2].value.text() == "—"


def test_hardware_refresh_replaces_the_previous_card_grid(qapp):
    from modules.hardware_inventory.hardware_module import _LoadingTab
    from modules.hardware_inventory.hardware_tabs import setup_overview

    tab = _LoadingTab(lambda _w: {}, setup_overview)
    tab._on_result({"CPU": [("Model", "First CPU")]})
    tab._on_result({"CPU": [("Model", "Second CPU")]})
    assert tab._content_layout.count() == 2  # one grid and its trailing stretch


def test_driver_glance_measures_inventory_without_pseudo_filter(qapp):
    from modules.driver_manager.driver_module import DriverModule
    from modules.driver_manager.driver_reader import DriverInfo

    mod = DriverModule()
    mod.create_widget()
    mod._update_glance(None, "access denied")
    assert all(t.value.text() == "—" for t in mod._glance.tiles)
    drivers = [DriverInfo("NIC", "NET", "1", "2020-01-01", "Vendor", False, 28, "Old", "oem1.inf")]
    mod._update_glance(drivers)
    assert [t.value.text() for t in mod._glance.tiles[:3]] == ["1", "1", "1"]


@pytest.mark.parametrize("code", [0, 1])
def test_driver_refusal_never_becomes_zero_problems(monkeypatch, code):
    from modules.driver_manager import driver_reader as dr

    monkeypatch.setattr(dr.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=code, stdout="", stderr="Get-CimInstance: Access denied"))
    with pytest.raises(dr.DriverReadError, match="Access denied"):
        dr.fetch_drivers()


def test_features_glance_counts_pending_separately(qapp):
    from modules.windows_features.features_module import WindowsFeaturesModule

    mod = WindowsFeaturesModule()
    root = mod.create_widget()
    mod._update_glance(None, "administrator required")
    assert all(t.value.text() == "—" for t in mod._glance.tiles)
    mod._update_glance([("A", "Enabled"), ("B", "Disabled"), ("C", "Enable Pending"),
                        ("D", "Disable Pending"), ("E", "Disabled with Payload Removed")])
    assert [t.value.text() for t in mod._glance.tiles] == ["1", "2", "2"]
    root.deleteLater()
