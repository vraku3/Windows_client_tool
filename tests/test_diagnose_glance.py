"""Loaded evidence, unavailable reads and actionable diagnostic summaries."""
from datetime import datetime, timedelta

from core.types import LogEntry
from ui.log_pane import LogPane


def record(level="Info", source="Windows", minute=0):
    return LogEntry(datetime(2026, 10, 25, 12, minute), source, level, "Evidence")


def test_glance_counts_loaded_window_and_tiles_filter_without_losing_source(qapp):
    pane = LogPane(lambda w: [])
    rows = [record("Critical"), record("Error", "App", 1),
            record("Warning", "App", 2), record()]
    pane.set_entries(rows)
    assert pane._glance_tiles["critical"].value.text() == "1"
    assert pane._glance_tiles["error"].value.text() == "1"
    assert pane._glance_tiles["warning"].value.text() == "1"
    assert "App (2)" in pane._glance_tiles["sources"].toolTip()
    pane._glance_tiles["error"].clicked.emit()
    assert pane.entries() == [rows[1]]
    assert pane.source_entries() == rows
    assert pane._glance_tiles["warning"].value.text() == "1"
    pane._chips.chips["all"].click()
    assert pane.row_count() == 4


def test_unreadable_is_unknown_and_not_a_zero_or_healthy_empty(qapp):
    pane = LogPane(lambda w: [], healthy_empty=True)
    assert pane._glance_tiles["error"].value.text() == "\u2014"
    pane.show_error("Access denied")
    assert pane._glance_tiles["error"].value.text() == "\u2014"
    assert "Access denied" in pane._glance_tiles["error"].toolTip()
    assert pane._empty.variant == "error"
    pane.set_entries([])
    assert pane._glance_tiles["error"].value.text() == "0"
    assert pane._empty.title_label.text() == "No crash evidence found"
    assert pane._empty.title_label.objectName() == "statusSuccess"


def test_partial_crash_read_never_claims_healthy(qapp):
    pane = LogPane(lambda w: [], healthy_empty=True,
                   read_problems=lambda: ["Security channel refused"])
    pane._on_result([])
    assert pane._glance_tiles["error"].value.text() == "\u2014"
    assert "incomplete" in pane._empty.title_label.text().lower()
    assert "refused" in pane._empty.hint_label.text()


def test_newest_problem_is_by_timestamp_with_absolute_tooltip(qapp):
    pane = LogPane(lambda w: [])
    pane.set_entries([record("Warning", minute=8), record("Critical", minute=2)])
    tile = pane._glance_tiles["newest"]
    assert "2026-10-25 12:08" in tile.toolTip()
    tile.clicked.emit()
    assert pane._table.selected_entry().level == "Warning"


def test_stability_tile_uses_sorted_real_metrics_and_refusal_reason(qapp):
    from modules.reliability.reliability_module import ReliabilityModule
    from modules.reliability.reliability_analysis import Metric
    module = ReliabilityModule()
    pane = module.create_widget()
    now = datetime(2026, 10, 25)
    module._metrics = [Metric(now, 4.0), Metric(now - timedelta(hours=1), 8.0)]
    pane._on_result([record()])
    assert module._stability_tile.value.text() == "4.0 / 10"
    assert pane.extra["stability_chart"].latest() == 4.0
    module._metrics = []
    module._problems = ["Stability metrics access denied"]
    pane._on_result([record()])
    assert module._stability_tile.value.text() == "\u2014"
    assert "denied" in module._stability_tile.toolTip()


def test_cancelled_read_retires_and_ignores_stale_result(qapp):
    class Pool:
        def start(self, worker):
            self.worker = worker
    pool = Pool()
    pane = LogPane(lambda w: [], thread_pool=pool)
    pane.load()
    old = pool.worker
    pane.cancel()
    pane.load()
    old.signals.result.emit([record("Error")])
    assert pane.row_count() == 0
    pool.worker.signals.result.emit([record("Warning")])
    assert pane.row_count() == 1
    pane.cancel()


def test_diagnose_host_does_not_add_a_second_reader_intro(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget
    from modules.diagnose.diagnose_module import DiagnoseModule
    from ui.module_intro import ModuleIntro
    module = DiagnoseModule()
    tabs = QTabWidget()
    page = QWidget()
    QVBoxLayout(page).addWidget(module.children[0].create_widget())
    tabs.addTab(page, "Event Viewer")
    module.wrap(tabs)
    assert not page.findChildren(ModuleIntro, options=Qt.FindChildOption.FindDirectChildrenOnly)


def test_cancelled_refresh_does_not_leave_tiles_claiming_a_read_is_running(qapp):
    class Pool:
        def start(self, worker):
            self.worker = worker
    pane = LogPane(lambda w: [], thread_pool=Pool())
    pane._on_result([record("Error")])
    pane.load(force=True)
    pane.cancel()
    assert "cancelled" in pane._glance_tiles["error"].toolTip().lower()
    assert pane.row_count() == 1
