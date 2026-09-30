"""Boot Analyzer's widget, including the new Boot Time Trend table.

Runs the module's real lifecycle (on_start -> create_widget -> on_activate)
against a real QThreadPool so the background worker actually runs and
`_display_info` actually fires -- the same pattern `test_startup_tab.py`
uses. This machine has real boot history (see `test_boot_history.py`), so
the table is asserted to end up populated, not just "did not crash".
"""
import time

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QTableWidget

from modules.boot_analyzer.boot_analyzer_module import (
    BootAnalyzerModule,
    _widget_is_valid,
)
from modules.boot_analyzer.boot_history import BootHistoryResult, BootRecord
from datetime import datetime, timezone


class _FakeApp:
    def __init__(self):
        self.thread_pool = QThreadPool()


def _settle(qapp, predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_boot_history_table_populates_from_the_real_machine(qapp):
    module = BootAnalyzerModule()
    module.on_start(_FakeApp())
    widget = module.create_widget()
    try:
        module.on_activate()  # first-load guard triggers _load_info
        settled = _settle(
            qapp, lambda: module._boot_history_table.rowCount() > 0 or not module._scanning
        )
        assert settled, "boot analysis never completed"
        table = module._boot_history_table
        assert isinstance(table, QTableWidget)
        assert table.columnCount() == 3
        # This machine has real, measured boot history (test_boot_history.py's
        # own real-machine assertion) so the table must have rows, not just
        # "did not crash".
        assert table.rowCount() >= 1
        for row in range(table.rowCount()):
            for col in range(3):
                assert table.item(row, col) is not None
        assert module._boot_trend_label.text() != ""
    finally:
        module.on_stop()
        widget.deleteLater()


def test_display_info_guards_against_a_torn_down_widget(qapp):
    """A result landing after the tab was closed must be a silent no-op,
    not a crash -- the same class of bug CLAUDE.md documents as already
    having hit this exact module (PowerBootModule/BootAnalyzerModule,
    unguarded Worker signal into a deleted widget)."""
    module = BootAnalyzerModule()
    module.on_start(_FakeApp())
    module.create_widget()
    sip.delete(module._widget)  # synchronous, unlike deleteLater()
    assert not _widget_is_valid(module._widget)

    fake_result = {
        "boot_type": "UEFI",
        "boot_timeout": 3,
        "boot_entries": 1,
        "fast_startup": "Enabled",
        "last_boot": "n/a",
        "uptime_days": "0 days",
        "boot_history": BootHistoryResult(
            available=True,
            reason=None,
            records=[
                BootRecord(
                    boot_time=datetime(2026, 9, 30, tzinfo=timezone.utc),
                    ready_time=None,
                    duration_seconds=None,
                    prior_shutdown_time=None,
                    prior_shutdown_clean=None,
                )
            ],
        ),
    }
    # Must not raise.
    module._display_info(fake_result)


def test_render_boot_history_reports_a_refusal_not_an_empty_table(qapp):
    module = BootAnalyzerModule()
    module.on_start(_FakeApp())
    module.create_widget()
    try:
        module._render_boot_history(
            BootHistoryResult(available=False, reason="Access is denied", records=[])
        )
        assert "Access is denied" in module._boot_trend_label.text()
        assert module._boot_history_table.rowCount() == 0
    finally:
        module.on_stop()
