"""Regression coverage for the verified redesign review."""
from datetime import datetime
from types import SimpleNamespace

from core.types import LogEntry


class Pool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


def test_log_chip_counts_reset_between_reads(qapp):
    from ui.log_pane import LogPane
    pane = LogPane(lambda w: [])
    pane.set_entries([LogEntry(datetime.now(), "Source", "Error", "bad")])
    pane.set_entries([])
    assert pane._chips.chips["error"].count == 0


def test_disk_partial_read_never_reports_green_zero(qapp):
    from modules.disk_health.disk_health_module import _ResultsView
    from modules.disk_health.disk_reader import DiskReport
    view = _ResultsView(DiskReport(errors={"volumes": "access denied"}))
    assert view._glance.tiles[1].value.text() == "\u2014"


def test_cert_read_reentry_and_stale_finished(qapp):
    from modules.certificate_viewer.cert_module import _CertTab
    pool = Pool()
    tab = _CertTab("MY", "user", pool)
    tab._load()
    old = pool.workers[-1]
    assert not tab._empty_state.action_button.isEnabled()
    tab._load()
    assert len(pool.workers) == 1
    tab._stop()
    tab._load()
    current = pool.workers[-1]
    old.signals.finished.emit()
    assert tab._worker is current
    assert not tab._refresh_btn.isEnabled()
    current.signals.finished.emit()
    assert tab._empty_state.action_button.isEnabled()


def test_dashboard_old_cancel_cannot_release_new_read(qapp):
    from modules.dashboard.tab_base import DashTab
    pool = Pool()
    tab = DashTab()
    tab.set_app(SimpleNamespace(thread_pool=pool))
    tab.run(lambda w: [], lambda rows: None)
    old = pool.workers[-1]
    tab.cancel_all()
    tab._busy = True
    tab.run(lambda w: [], lambda rows: None)
    old.signals.cancelled.emit()
    assert tab._busy
    tab.cancel_all()


def test_repeated_table_style_reuses_delegate(qapp):
    from PyQt6.QtWidgets import QTableWidget, QStyledItemDelegate
    from ui.table_style import apply_table_style
    table = QTableWidget(0, 3)
    apply_table_style(table)
    delegate = table.itemDelegate()
    count = len(table.findChildren(QStyledItemDelegate))
    apply_table_style(table, numeric_columns=(2,))
    assert table.itemDelegate() is delegate
    assert len(table.findChildren(QStyledItemDelegate)) == count


def test_scheduled_task_chips_have_keys_counts_and_filter(qapp):
    from modules.scheduled_tasks.tasks_module import TasksModule
    from modules.scheduled_tasks.tasks_reader import TaskInfo
    module = TasksModule()
    root = module.create_widget()
    rows = [TaskInfo("A", "\\A", "Ready", "Never", "0", "Never", "", "", "", True, 0),
            TaskInfo("B", "\\B", "Disabled", "Never", "0", "Never", "", "", "", False, 0)]
    module._on_tasks(rows)
    assert module._chips["all"].count == 2
    module._chips["disabled"].click()
    assert module._shown == [rows[1]]


def test_log_unavailable_result_carries_reason_and_window(qapp):
    from core.types import LogReadResult
    from ui.log_pane import LogPane
    pane = LogPane(lambda w: [])
    pane._on_result(LogReadResult(problems=["CAB extraction failed"]))
    assert pane._glance_tiles["error"].value.text() == "\u2014"
    assert "CAB extraction failed" in pane._glance_tiles["error"].toolTip()
    entries = [LogEntry(datetime(2026, 10, d), "Noisy" if d == 20 else "Problem", level, "x")
               for d, level in [(20, "Info"), (21, "Warning"), (22, "Info")]]
    pane._on_result(LogReadResult(entries))
    window = pane._glance_tiles["window"]
    assert "2026-10-20" in window.value.text()
    assert "2026-10-22" in window.sub.text()
    tip = pane._glance_tiles["sources"].toolTip()
    assert tip.index("Problem (1)") < tip.index("All sources:")


def test_cbs_absent_records_are_unavailable(monkeypatch):
    from modules.cbs_log import cbs_module as cbs
    from core.types import LogReadResult
    monkeypatch.setattr(cbs.os.path, "exists", lambda p: False)
    monkeypatch.setattr(cbs.os, "listdir", lambda p: [])
    result = cbs.CBSLogModule().load_entries(None)
    assert isinstance(result, LogReadResult)
    assert result.problems and not result.entries


def test_update_scan_reentry_and_stale_results(qapp, monkeypatch):
    from modules.updates import updates_module as updates
    pool = Pool()
    monkeypatch.setattr(updates.QThreadPool, "globalInstance", lambda: pool)
    for cls in (updates._AppUpdatesTab, updates._WinUpdatesTab):
        tab = cls()
        tab._do_refresh()
        old = pool.workers[-1]
        count = len(pool.workers)
        assert not tab._empty_state.action_button.isEnabled()
        tab._do_refresh()
        assert len(pool.workers) == count
        old.signals.cancelled.emit()
        tab._do_refresh()
        current = pool.workers[-1]
        old.signals.result.emit([])
        assert tab._scan_worker is current
        assert not tab._refresh_btn.isEnabled()
        current.signals.result.emit([])
        assert tab._empty_state.action_button.isEnabled()
        tab._installing = True
        count = len(pool.workers)
        tab._do_refresh()
        assert len(pool.workers) == count
        tab._cancel_all()


def test_update_health_is_background_and_lifetime_guarded(qapp, monkeypatch):
    from modules.updates import updates_module as updates
    from PyQt6 import sip
    pool = Pool()
    mod = updates.UpdatesModule()
    mod.app = SimpleNamespace(thread_pool=pool)
    from modules.updates.overview import UpdatesOverview
    mod._overview = UpdatesOverview()
    mod._refresh_overview_health()
    assert len(pool.workers) == 1
    mod._refresh_overview_health()
    assert len(pool.workers) == 1
    sip.delete(mod._overview)
    pool.workers[0].signals.result.emit((None, None, "denied"))
    pool.workers[0].signals.finished.emit()
    assert mod._health_worker is None


def test_driverless_count_is_separate_and_age_neutral(qapp):
    from modules.driver_manager.driver_module import DriverModule
    from modules.driver_manager.driver_reader import DriverInfo
    mod = DriverModule()
    root = mod.create_widget()
    rows = [DriverInfo("Missing", "NET", "", "", "", False, 28, "No driver installed"),
            DriverInfo("Unsigned", "NET", "1", "2020-01-01", "Vendor", False, 0, "Old", "oem1.inf")]
    mod._drivers_ref[0] = rows
    mod._update_glance(rows)
    assert [t.value.text() for t in mod._glance.tiles] == ["1", "1", "1", "1", "0"]
    assert mod._glance.tiles[3].severity == "info"
    mod._glance.tiles[1].clicked.emit()
    assert mod._table.rowCount() == 1
    assert mod._table.item(0, 0).text() == "Unsigned"


def test_hardware_cards_find_identity_after_measurements(qapp):
    from PyQt6.QtWidgets import QWidget, QVBoxLayout
    from ui.glance import GlanceTile
    from modules.hardware_inventory.hardware_tabs import setup_overview
    root = QWidget()
    setup_overview(QVBoxLayout(root), {"GPU": [("RAM", "8 GB"), ("Model", "Graphics")],
                                      "Network": [("Speed", "1 Gbps"), ("Adapter", "Ethernet")],
                                      "Monitors": [("Resolution", "4K"), ("Name", "Display")]})
    tiles = {t.caption.text(): t for t in root.findChildren(GlanceTile)}
    for name, headline, detail in [("GPU", "Graphics", "RAM: 8 GB"),
                                    ("NETWORK", "Ethernet", "Speed: 1 Gbps"),
                                    ("MONITORS", "Display", "Resolution: 4K")]:
        assert tiles[name].value.text() == headline
        assert detail in tiles[name].sub.text()


def test_restore_defaults_to_systemdrive_and_other_volumes_selectable(qapp, monkeypatch):
    from modules.restore_manager.restore_module import RestoreManagerModule, _Snapshot
    from modules.restore_manager import restore_analysis as ra
    monkeypatch.setenv("SystemDrive", "D:")
    mod = RestoreManagerModule()
    root = mod.create_widget()
    mod._snapshot = _Snapshot(points=[], mounts=["D:", "E:"], protection=ra.Protection(mounts=["D:"]),
                              storage=[ra.ShadowStorage("D:", used=1024, maximum=2048),
                                       ra.ShadowStorage("E:", used=2048, maximum=4096)])
    mod._fill_drives()
    mod._update_glance()
    assert mod._volume_combo.currentText() == "D:"
    assert mod._glance.tiles[0].value.text() == "On"
    mod._volume_combo.setCurrentText("E:")
    assert mod._glance.tiles[0].value.text() == "Off"
    assert mod._glance.tiles[3].value.text() == f"{ra.format_bytes(2048)} / {ra.format_bytes(4096)}"


def test_quickfix_glance_uses_recent_history_and_opens_history(qapp, monkeypatch):
    from modules.quick_fix import quick_fix_history as history
    from modules.quick_fix.quick_fix_module import QuickFixModule
    monkeypatch.setattr(history, "recent", lambda limit=20: [
        {"action": "Flush DNS", "outcome": "error", "at": "2026-10-26T12:00:00"},
        {"action": "Repair", "outcome": "ok", "at": "2026-10-25T12:00:00"}])
    opened = []
    mod = QuickFixModule()
    monkeypatch.setattr(mod, "_show_history", lambda: opened.append(True))
    root = mod.create_widget()
    assert mod._glance["Recent failed runs"].value.text() == "1"
    assert mod._glance["Last run"].value.text() == "error"
    mod._glance["Last run"].clicked.emit()
    assert opened == [True]


def test_dashboard_measured_counts_replace_permanent_unknowns(qapp):
    from modules.dashboard.startup_tab import StartupTab
    from modules.startup_manager.startup_reader import StartupEntry
    from modules.dashboard.details_tab import DetailsTab
    startup = StartupTab()
    startup._apply(([StartupEntry("A", "a", True, "registry_run"),
                      StartupEntry("B", "b", False, "service")], []))
    assert [t.value.text() for t in startup._glance.values()] == ["1", "1", "1", "2"]
    details = DetailsTab()
    rows = {1: SimpleNamespace(details=SimpleNamespace(elevated=True), rates=SimpleNamespace(cpu_percent=15)),
            2: SimpleNamespace(details=SimpleNamespace(elevated=False), rates=SimpleNamespace(cpu_percent=2))}
    details._show_summary(SimpleNamespace(by_pid=rows))
    assert [t.value.text() for t in details._glance.values()] == ["2", "1", "1"]


def test_boot_fallback_interval_has_separate_label(qapp):
    from modules.boot_analyzer.boot_history_view import BootHistoryPanel
    from modules.boot_analyzer import boot_history as bh
    panel = BootHistoryPanel()
    panel.set_facts(bh.BootFacts(problems=["Detailed trace refused"], fallback_boots=[
        bh.FallbackBootRecord(datetime(2026, 10, 26), 17, True)]))
    assert panel._duration.value.text() == "\u2014"
    assert panel._fallback_duration.value.text() == "17 s"
    assert "APPROXIMATE" in panel._fallback_duration.caption.text()


def test_network_extras_refreshes_each_activation_and_after_save(qapp, monkeypatch):
    from contextlib import nullcontext
    from PyQt6.QtWidgets import QPushButton
    from modules.network_extras import net_extras_module as extras
    mod = extras.NetExtrasModule()
    root = mod.create_widget()
    pool = Pool()
    monkeypatch.setattr(type(mod), "thread_pool", property(lambda _: pool))
    mod.on_activate()
    mod.on_activate()
    assert len(pool.workers) == 2
    monkeypatch.setattr(extras.winreg, "OpenKey", lambda *a: nullcontext(None))
    monkeypatch.setattr(extras.winreg, "SetValueEx", lambda *a: None)
    save = next(b for b in root.findChildren(QPushButton) if b.text() == "Save")
    save.click()
    assert len(pool.workers) == 3
    mod.on_stop()


def test_certificate_error_and_cancel_leave_retry_available(qapp):
    from modules.certificate_viewer.cert_module import _CertTab
    pool = Pool()
    tab = _CertTab("MY", "user", pool)
    tab._load()
    worker = pool.workers[-1]
    worker.signals.error.emit("Store access denied")
    worker.signals.finished.emit()
    assert tab._empty_state.action_button.isEnabled()
    tab._load()
    tab._stop()
    assert tab._empty_state.action_button.isEnabled()
    tab.ensure_loaded()
    assert len(pool.workers) == 3
    tab._stop()


def test_update_scan_delivery_cannot_unlock_install_controls(qapp, monkeypatch):
    from modules.updates import updates_module as updates
    from core.worker import Worker
    pool = Pool()
    monkeypatch.setattr(updates.QThreadPool, "globalInstance", lambda: pool)
    for cls in (updates._AppUpdatesTab, updates._WinUpdatesTab):
        for signal in ("result", "error"):
            tab = cls()
            worker = Worker(lambda w: [])
            updates._start_scan(tab, worker)
            tab._installing = True
            getattr(worker.signals, signal).emit([] if signal == "result" else "scan failed")
            assert tab._installing
            assert not tab._empty_state.action_button.isEnabled()
            assert not tab._refresh_btn.isEnabled()
            buttons = (tab._install_btn,) if cls is updates._WinUpdatesTab else (tab._update_sel_btn, tab._update_all_btn)
            assert all(not b.isEnabled() for b in buttons)
            tab._cancel_all()


def test_parser_result_preserves_read_refusal(qapp, tmp_path, monkeypatch):
    from modules.cbs_log.cbs_parser import CBSParser
    path = tmp_path / "CBS.log"
    path.write_text("evidence", encoding="utf-8")
    parser = CBSParser(str(path))
    monkeypatch.setattr(parser, "_detect_encoding", lambda: "utf-8")
    def refused(*args, **kwargs):
        raise PermissionError("denied by policy")
    monkeypatch.setattr("builtins.open", refused)
    result = parser.parse_result()
    assert not result.entries
    assert "denied by policy" in result.problems[0]
