from datetime import datetime
from PyQt6.QtWidgets import QApplication
from core.types import LogEntry
from ui.log_table_widget import LogTableWidget


def test_set_entries():
    widget = LogTableWidget()
    entries = [
        LogEntry(timestamp=datetime(2026, 3, 25, 12, 0), source="System", level="Error", message="Disk failure"),
        LogEntry(timestamp=datetime(2026, 3, 25, 12, 1), source="App", level="Info", message="Started"),
    ]
    widget.set_entries(entries)
    assert len(widget.get_entries()) == 2
    assert widget._status.text() == "2 entries"


def test_clear_entries():
    widget = LogTableWidget()
    entries = [
        LogEntry(timestamp=datetime(2026, 3, 25, 12, 0), source="Test", level="Warning", message="warn"),
    ]
    widget.set_entries(entries)
    widget.clear()
    assert len(widget.get_entries()) == 0
    assert widget._status.text() == "0 entries"


def test_append_entries():
    widget = LogTableWidget()
    e1 = [LogEntry(timestamp=datetime(2026, 3, 25, 12, 0), source="A", level="Info", message="first")]
    e2 = [LogEntry(timestamp=datetime(2026, 3, 25, 12, 1), source="B", level="Error", message="second")]
    widget.set_entries(e1)
    widget.append_entries(e2)
    assert len(widget.get_entries()) == 2
    assert widget._status.text() == "2 entries"


def test_copy_uses_the_generic_one_liner_when_no_formatter_is_given(qapp):
    widget = LogTableWidget()
    entry = LogEntry(timestamp=datetime(2026, 3, 25, 12, 0), source="System", level="Error", message="Disk failure")
    widget.set_entries([entry])
    widget._table.selectRow(0)
    widget.copy_selected_to_clipboard()
    assert QApplication.clipboard().text() == "2026-03-25 12:00:00 [Error] System: Disk failure"


def test_copy_uses_a_reader_supplied_formatter_when_one_is_given(qapp):
    # Event Viewer wires `event_analysis.entry_as_text` in here so "Copy row"
    # pastes a ticket-ready block (log name, computer, record number, the
    # event's own data fields), not just one line of message text.
    widget = LogTableWidget(copy_formatter=lambda e: f"FORMATTED: {e.message}")
    entry = LogEntry(timestamp=datetime(2026, 3, 25, 12, 0), source="System", level="Error", message="Disk failure")
    widget.set_entries([entry])
    widget._table.selectRow(0)
    widget.copy_selected_to_clipboard()
    assert QApplication.clipboard().text() == "FORMATTED: Disk failure"
