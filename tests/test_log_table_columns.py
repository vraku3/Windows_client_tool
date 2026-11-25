"""Tests for LogTableWidget column customization and severity-weighted sorting."""

from __future__ import annotations

import datetime
from typing import List
import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from core.types import LogEntry
from ui.log_table_widget import (
    LogTableWidget,
    ColumnPickerDialog,
    SEVERITY_WEIGHTS,
    SeverityItem,
    EventIdItem,
    DeltaTimeItem,
)
from ui.log_pane import LogPane


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_entry(
    timestamp: float,
    level: str,
    message: str = "msg",
    source: str = "test-source",
    event_id: str | int = "",
    category: str = "",
    note: str = "",
) -> LogEntry:
    dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    raw = {}
    if event_id != "":
        raw["event_id"] = str(event_id)
    if category:
        raw["category"] = category
    if note:
        raw["_user_note"] = note
    return LogEntry(
        timestamp=dt,
        level=level,
        source=source,
        message=message,
        raw=raw,
    )


def test_default_columns_and_initial_visibility(qapp):
    table = LogTableWidget()
    all_cols = table.get_all_columns()
    expected_all = [
        "Time",
        "Source",
        "Level",
        "Event ID",
        "Category",
        "Message",
        "Delta Time (Δt)",
        "Note",
    ]
    assert all_cols == expected_all

    visible_cols = table.get_visible_columns()
    assert "Time" in visible_cols
    assert "Source" in visible_cols
    assert "Level" in visible_cols
    assert "Message" in visible_cols

    # Optional columns hidden by default
    assert "Event ID" not in visible_cols
    assert "Category" not in visible_cols
    assert "Delta Time (Δt)" not in visible_cols
    assert "Note" not in visible_cols

    assert table.is_column_visible("Time") is True
    assert table.is_column_visible("Event ID") is False
    assert table.is_column_hidden("Event ID") is True


def test_extra_columns_integration(qapp):
    table = LogTableWidget(
        extra_columns=["CustomCol1", "CustomCol2"],
        extra_values=lambda entry: ["val1", "val2"],
    )
    all_cols = table.get_all_columns()
    assert "CustomCol1" in all_cols
    assert "CustomCol2" in all_cols

    visible_cols = table.get_visible_columns()
    assert "CustomCol1" in visible_cols
    assert "CustomCol2" in visible_cols


def test_programmatic_visibility_toggling(qapp):
    table = LogTableWidget()
    changed_signals = []
    table.columns_changed.connect(lambda: changed_signals.append(True))

    # Toggle Event ID to visible using alias
    res = table.set_column_visibility("event_id", True)
    assert res is True
    assert table.is_column_visible("Event ID") is True
    assert "Event ID" in table.get_visible_columns()
    assert len(changed_signals) == 1

    # Toggle Note to visible
    table.set_column_hidden("note", False)
    assert table.is_column_visible("Note") is True
    assert len(changed_signals) == 2

    # Hide Time
    table.set_column_visibility("time", False)
    assert table.is_column_visible("Time") is False
    assert "Time" not in table.get_visible_columns()
    assert len(changed_signals) == 3

    # Unknown column returns False and doesn't crash
    assert table.set_column_visibility("NonExistentColumn", True) is False


def test_column_picker_dialog(qapp):
    table = LogTableWidget()
    dlg = table.create_column_picker_dialog()
    assert isinstance(dlg, ColumnPickerDialog)

    # Initial state matches table
    vis = dlg.get_visibility()
    assert vis["Time"] is True
    assert vis["Event ID"] is False

    # Test select all
    dlg.select_all()
    vis_all = dlg.get_visibility()
    assert all(vis_all.values())

    # Test deselect all (Time should remain checked as essential)
    dlg.deselect_all()
    vis_none = dlg.get_visibility()
    assert vis_none["Time"] is True
    assert not vis_none["Level"]
    assert not vis_none["Message"]

    # Test reset defaults
    dlg.reset_defaults()
    vis_def = dlg.get_visibility()
    assert vis_def["Time"] is True
    assert vis_def["Level"] is True
    assert vis_def["Source"] is True
    assert vis_def["Message"] is True
    assert not vis_def["Event ID"]


def test_dialog_apply_updates_table(qapp):
    table = LogTableWidget()
    dlg = table.create_column_picker_dialog()

    # Turn on Event ID and Category
    dlg.set_column_state("Event ID", True)
    dlg.set_column_state("Category", True)
    dlg.accept()

    assert table.is_column_visible("Event ID") is True
    assert table.is_column_visible("Category") is True
    assert table.is_column_visible("Time") is True


def test_severity_weighted_sorting(qapp):
    """
    Test that sorting the Level column sorts by operational severity weight:
    Critical (4) > Error (3) > Warning (2) > Info (1) > Verbose/Debug (0).
    Alphabetical would sort: Critical < Error < Information < Verbose < Warning.
    """
    table = LogTableWidget()
    t0 = 1000.0
    entries = [
        _make_entry(t0 + 1, level="Information", message="info log"),
        _make_entry(t0 + 2, level="Warning", message="warning log"),
        _make_entry(t0 + 3, level="Critical", message="critical log"),
        _make_entry(t0 + 4, level="Verbose", message="verbose log"),
        _make_entry(t0 + 5, level="Error", message="error log"),
    ]
    table.set_entries(entries)

    # Sort Level descending: highest severity first
    table.sort_by_column("Level", Qt.SortOrder.DescendingOrder)
    sorted_levels_desc = []
    col_idx = table.get_column_index("Level")
    for row in range(table._model.rowCount()):
        sorted_levels_desc.append(table._model.item(row, col_idx).text())

    assert sorted_levels_desc == ["Critical", "Error", "Warning", "Information", "Verbose"]

    # Sort Level ascending: lowest severity first
    table.sort_by_column("Level", Qt.SortOrder.AscendingOrder)
    sorted_levels_asc = []
    for row in range(table._model.rowCount()):
        sorted_levels_asc.append(table._model.item(row, col_idx).text())

    assert sorted_levels_asc == ["Verbose", "Information", "Warning", "Error", "Critical"]


def test_numeric_event_id_sorting(qapp):
    """
    Test that Event ID column sorts numerically (2 < 9 < 10 < 100 < 7001),
    rather than lexicographically ('10' < '100' < '2' < '7001' < '9').
    """
    table = LogTableWidget()
    table.set_column_visibility("Event ID", True)
    t0 = 1000.0
    entries = [
        _make_entry(t0 + 1, level="Info", event_id=10),
        _make_entry(t0 + 2, level="Info", event_id=2),
        _make_entry(t0 + 3, level="Info", event_id=7001),
        _make_entry(t0 + 4, level="Info", event_id=100),
        _make_entry(t0 + 5, level="Info", event_id=9),
    ]
    table.set_entries(entries)

    col_idx = table.get_column_index("Event ID")
    table.sort_by_column("Event ID", Qt.SortOrder.AscendingOrder)
    ids_asc = [table._model.item(r, col_idx).text() for r in range(table._model.rowCount())]
    assert ids_asc == ["2", "9", "10", "100", "7001"]

    table.sort_by_column("Event ID", Qt.SortOrder.DescendingOrder)
    ids_desc = [table._model.item(r, col_idx).text() for r in range(table._model.rowCount())]
    assert ids_desc == ["7001", "100", "10", "9", "2"]


def test_delta_time_calculation_and_sorting(qapp):
    """
    Test that Delta Time (Δt) is computed relative to the previous entry in order,
    and sorts numerically by seconds.
    """
    table = LogTableWidget()
    table.set_column_visibility("Delta Time (Δt)", True)
    t0 = 1000.0
    entries = [
        _make_entry(t0, level="Info", message="first"),
        _make_entry(t0 + 5.5, level="Info", message="second (+5.5s)"),
        _make_entry(t0 + 7.0, level="Info", message="third (+1.5s)"),
        _make_entry(t0 + 17.0, level="Info", message="fourth (+10.0s)"),
    ]
    table.set_entries(entries)

    dt_col = table.get_column_index("Delta Time (Δt)")
    msg_col = table.get_column_index("Message")

    # Sort ascending: 0.0s < +1.5s < +5.5s < +10.0s
    table.sort_by_column("Delta Time (Δt)", Qt.SortOrder.AscendingOrder)
    sorted_msgs_asc = [table._model.item(r, msg_col).text() for r in range(table._model.rowCount())]
    assert sorted_msgs_asc == ["first", "third (+1.5s)", "second (+5.5s)", "fourth (+10.0s)"]

    # Sort descending: largest delta first (+10.0s > +5.5s > +1.5s > 0.0s)
    table.sort_by_column("Delta Time (Δt)", Qt.SortOrder.DescendingOrder)
    sorted_msgs_desc = [table._model.item(r, msg_col).text() for r in range(table._model.rowCount())]
    assert sorted_msgs_desc == ["fourth (+10.0s)", "second (+5.5s)", "third (+1.5s)", "first"]


def test_buttons_wired_in_table_and_log_pane(qapp):
    # LogTableWidget status bar button
    table = LogTableWidget()
    assert hasattr(table, "_columns_btn")
    assert table._columns_btn is not None
    assert table._columns_btn.text() == "Columns..."

    # LogPane toolbar button
    pane = LogPane(loader=lambda worker: [])
    assert hasattr(pane, "_columns_btn")
    assert pane._columns_btn is not None
    assert pane._columns_btn.text() == "Columns..."


def test_export_csv_respects_visible_columns(qapp, tmp_path):
    table = LogTableWidget()
    entries = [_make_entry(1000.0, level="Error", message="disk failure", source="Disk", event_id=7)]
    table.set_entries(entries)

    # By default: Time, Source, Level, Message (matching existing format)
    csv_file = tmp_path / "export1.csv"
    table.export_csv(str(csv_file))
    content1 = csv_file.read_text(encoding="utf-8")
    lines1 = content1.strip().splitlines()
    assert lines1[0] == "Time,Source,Level,Message"
    assert "disk failure" in lines1[1]

    # Enable Event ID and Note
    table.set_column_visibility("Event ID", True)
    table.set_column_visibility("Note", True)
    csv_file2 = tmp_path / "export2.csv"
    table.export_csv(str(csv_file2))
    content2 = csv_file2.read_text(encoding="utf-8")
    lines2 = content2.strip().splitlines()
    assert lines2[0] == "Time,Source,Level,Event ID,Message,Note"
    assert "7" in lines2[1]


def test_unknown_severity_and_empty_event_id_sorting(qapp):
    table = LogTableWidget()
    table.set_column_visibility("Event ID", True)
    t0 = 1000.0
    entries = [
        _make_entry(t0 + 1, level="CustomSeverity", message="unknown level", event_id=""),
        _make_entry(t0 + 2, level="Critical", message="critical", event_id=12),
        _make_entry(t0 + 3, level="Info", message="info", event_id="not-a-number"),
    ]
    table.set_entries(entries)

    # Sort Level ascending: CustomSeverity (-1) < Info (1) < Critical (4)
    table.sort_by_column("Level", Qt.SortOrder.AscendingOrder)
    col_idx = table.get_column_index("Level")
    levels = [table._model.item(r, col_idx).text() for r in range(table._model.rowCount())]
    assert levels == ["CustomSeverity", "Info", "Critical"]

    # Sort Event ID ascending: empty/non-numeric items do not crash
    table.sort_by_column("Event ID", Qt.SortOrder.AscendingOrder)
    assert table._model.rowCount() == 3


def test_add_entries_delta_time_continuation(qapp):
    table = LogTableWidget()
    table.set_column_visibility("Delta Time (Δt)", True)
    t0 = 1000.0
    entries_batch1 = [
        _make_entry(t0, level="Info", message="b1-1"),
        _make_entry(t0 + 2.0, level="Info", message="b1-2"),
    ]
    table.set_entries(entries_batch1)

    # Now append batch2 using add_entries
    entries_batch2 = [
        _make_entry(t0 + 6.0, level="Info", message="b2-1 (+4.0s relative to b1-2)"),
    ]
    table.add_entries(entries_batch2)
    assert len(table.get_entries()) == 3


def test_export_sanitized_and_html_respect_visible_columns(qapp, tmp_path):
    table = LogTableWidget()
    entries = [_make_entry(1000.0, level="Warning", message="test alert", source="Sec", event_id=4624)]
    table.set_entries(entries)

    # Default export
    csv_file = tmp_path / "sanitized.csv"
    table.export_sanitized_csv(str(csv_file))
    txt = csv_file.read_text(encoding="utf-8")
    assert "Time,Source,Level,Message" in txt
    assert "Event ID" not in txt

    # HTML export
    html_file = tmp_path / "report.html"
    table.export_html(str(html_file))
    html_txt = html_file.read_text(encoding="utf-8")
    assert "<th>Time</th>" in html_txt
    assert "<th>Source</th>" in html_txt
    assert "<th>Event ID</th>" not in html_txt

