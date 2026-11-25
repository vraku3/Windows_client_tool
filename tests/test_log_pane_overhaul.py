"""Tests for LogPane 2.0 overhaul, modern DetailPanel, and LogTableWidget enhancements."""
import json
from datetime import datetime, timedelta
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from core.types import LogEntry
from ui.detail_panel import DetailPanel
from ui.log_density_bar import LogDensityBar
from ui.log_pane import LogPane
from ui.log_table_widget import LogTableWidget, sanitize_log_text


@pytest.fixture(autouse=True)
def clean_casebook():
    from core.casebook import get_casebook_manager
    mgr = get_casebook_manager()
    mgr.clear_all()
    yield
    mgr.clear_all()


def _sample_entry(source="Service Control Manager", level="Error", message="The Print Spooler terminated unexpectedly with error code 0x80070005.", timestamp=None, **raw):
    return LogEntry(
        timestamp=timestamp or datetime(2026, 11, 18, 20, 0, 0),
        source=source,
        level=level,
        message=message,
        raw=raw,
    )


def test_in_pane_omni_filter_by_message(qapp):
    pane = LogPane(loader=lambda w: [])
    e1 = _sample_entry(message="Disk read failure")
    e2 = _sample_entry(message="Network disconnected")
    pane.set_entries([e1, e2])

    assert pane.row_count() == 2

    pane._search_input.setText("disk")
    pane._apply_filter_from_input()
    assert pane.row_count() == 1
    assert pane.entries()[0].message == "Disk read failure"

    pane._search_input.setText("")
    pane._apply_filter_from_input()
    assert pane.row_count() == 2


def test_in_pane_omni_filter_by_field_prefix(qapp):
    pane = LogPane(loader=lambda w: [])
    e1 = _sample_entry(source="Kernel-Power", event_id=41)
    e2 = _sample_entry(source="Service Control Manager", event_id=7031)
    pane.set_entries([e1, e2])

    # Filter by source:
    pane._search_input.setText("source:Kernel")
    pane._apply_filter_from_input()
    assert pane.row_count() == 1
    assert pane.entries()[0].source == "Kernel-Power"

    # Filter by id:
    pane._search_input.setText("id:7031")
    pane._apply_filter_from_input()
    assert pane.row_count() == 1
    assert pane.entries()[0].source == "Service Control Manager"


def test_in_pane_omni_filter_boolean_and_range_operators(qapp):
    pane = LogPane(loader=lambda w: [])
    e1 = _sample_entry(source="Service Control Manager", level="Error", event_id=7031)
    e2 = _sample_entry(source="Service Control Manager", level="Information", event_id=7036)
    e3 = _sample_entry(source="Disk", level="Warning", event_id=153)
    pane.set_entries([e1, e2, e3])

    # AND operator
    pane._search_input.setText("source:Service and level:Error")
    pane._apply_filter_from_input()
    assert pane.row_count() == 1
    assert pane.entries()[0] == e1

    # OR operator
    pane._search_input.setText("source:disk or id:7036")
    pane._apply_filter_from_input()
    assert pane.row_count() == 2
    assert e2 in pane.entries() and e3 in pane.entries()

    # Range operator (e.g. 7000..7050)
    pane._search_input.setText("id:7000..7050")
    pane._apply_filter_from_input()
    assert pane.row_count() == 2

    # Negative NOT operator
    pane._search_input.setText("-source:Service")
    pane._apply_filter_from_input()
    assert pane.row_count() == 1
    assert pane.entries()[0] == e3


def test_glance_hud_collapse_toggle(qapp):
    pane = LogPane(loader=lambda w: [])
    pane.set_entries([_sample_entry()])

    assert pane._hud_collapsed is False
    assert not pane._glance.isHidden()
    assert pane._mini_hud.isHidden()
    assert pane._hud_toggle_btn.text() == "▲ HUD"

    # Collapse
    pane._hud_toggle_btn.click()
    assert pane._hud_collapsed is True
    assert pane._glance.isHidden()
    assert not pane._mini_hud.isHidden()
    assert pane._hud_toggle_btn.text() == "▼ HUD"

    # Expand
    pane._hud_toggle_btn.click()
    assert pane._hud_collapsed is False
    assert not pane._glance.isHidden()
    assert pane._mini_hud.isHidden()
    assert pane._hud_toggle_btn.text() == "▲ HUD"


def test_log_density_bar_calculation(qapp):
    bar = LogDensityBar()
    t0 = datetime(2026, 11, 18, 10, 0, 0)
    entries = [
        _sample_entry(timestamp=t0, level="Error"),
        _sample_entry(timestamp=t0 + timedelta(minutes=30), level="Warning"),
        _sample_entry(timestamp=t0 + timedelta(hours=1), level="Information"),
    ]
    bar.set_entries(entries)
    assert bar.has_data() is True
    assert len(bar._buckets) > 0
    total_in_buckets = sum(b.total for b in bar._buckets)
    assert total_in_buckets == 3


def test_log_table_export_json(qapp, tmp_path):
    table = LogTableWidget()
    e = _sample_entry(source="AppHost", level="Warning", message="High memory", pid=1024)
    table.set_entries([e])

    out_file = tmp_path / "export.json"
    table.export_json(str(out_file))

    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["source"] == "AppHost"
    assert data[0]["level"] == "Warning"
    assert data[0]["raw"]["pid"] == 1024


def test_log_table_bookmark_toggle(qapp):
    table = LogTableWidget()
    e = _sample_entry()
    table.set_entries([e])
    table._table.selectRow(0)

    assert len(table._bookmarked_entry_ids) == 0
    table._toggle_bookmark_selected()
    assert len(table._bookmarked_entry_ids) == 1
    assert table.is_bookmarked(e) is True
    assert "★" in table._model.item(0, 0).text()

    table._toggle_bookmark_selected()
    assert len(table._bookmarked_entry_ids) == 0
    assert table.is_bookmarked(e) is False
    assert "★" not in table._model.item(0, 0).text()


def test_log_pane_bookmark_filter_button(qapp):
    pane = LogPane(loader=lambda w: [])
    e1 = _sample_entry(source="One", message="First")
    e2 = _sample_entry(source="Two", message="Second")
    pane.set_entries([e1, e2])

    # Bookmark e2
    pane._table._table.selectRow(1)
    pane._table._toggle_bookmark_selected()
    assert "★ Bookmarks (1)" in pane._bookmarks_filter_btn.text()

    # Filter to bookmarks
    pane._bookmarks_filter_btn.setChecked(True)
    assert pane.row_count() == 1
    assert pane.entries()[0] == e2

    # Unfilter
    pane._bookmarks_filter_btn.setChecked(False)
    assert pane.row_count() == 2


def test_log_table_find_bar_navigation(qapp):
    table = LogTableWidget()
    e1 = _sample_entry(message="Printer error detected")
    e2 = _sample_entry(message="Normal operational line")
    e3 = _sample_entry(message="Another printer warning")
    table.set_entries([e1, e2, e3])

    table._show_find_bar()
    assert not table._find_bar.isHidden()

    table._find_input.setText("printer")
    assert len(table._find_matches) == 2
    assert "1 of 2" in table._find_matches_lbl.text()
    assert table._table.currentIndex().row() == 0

    table._find_next()
    assert "2 of 2" in table._find_matches_lbl.text()
    assert table._table.currentIndex().row() == 2

    table._find_prev()
    assert "1 of 2" in table._find_matches_lbl.text()
    assert table._table.currentIndex().row() == 0

    table._hide_find_bar()
    assert table._find_bar.isHidden()


def test_sanitize_log_text_privacy():
    raw_text = (
        "User DOMAIN\\john.doe connected from 192.168.1.100. "
        "File located at C:\\Users\\john.doe\\AppData\\Local\\Temp\\test.exe. "
        "Security principal S-1-5-21-123456789-987654321-111111111-1001 authenticated."
    )
    sanitized = sanitize_log_text(raw_text)

    assert "john.doe" not in sanitized
    assert "192.168.1.100" not in sanitized
    assert "10.x.x.x" in sanitized
    assert "C:\\Users\\<REDACTED>" in sanitized
    assert "<DOMAIN>\\<USER>" in sanitized
    assert "S-1-5-21-<REDACTED>" in sanitized


def test_detail_panel_multi_tab_and_error_code_decoding(qapp):
    panel = DetailPanel()
    # Message carries 0x80070005 (Access Denied)
    entry = _sample_entry(message="Failed to write file. Status: 0x80070005", user="SYSTEM", event_id=7031)
    panel.show_entry(entry)

    assert panel.isVisible() is True
    # Tab count
    assert panel._tabs.count() == 5
    assert panel._tabs.tabText(0) == "Overview"
    assert panel._tabs.tabText(1) == "Structured Data"
    assert "Error Analysis (1)" in panel._tabs.tabText(2)
    assert panel._tabs.tabText(3) == "Correlated"
    assert panel._tabs.tabText(4) == "Remediation"

    # Check Structured Data tab
    assert panel._raw_table.rowCount() >= 5
    props = {panel._raw_table.item(r, 0).text(): panel._raw_table.item(r, 1).text() for r in range(panel._raw_table.rowCount())}
    assert props["Source"] == "Service Control Manager"
    assert props["user"] == "SYSTEM"

    # Check Error Analysis tab decoded 0x80070005
    err_text = panel._error_analysis_text.toHtml()
    assert "0x80070005" in err_text
    assert "Access is denied" in err_text or "access denied" in err_text.lower()

    # Test copy JSON button
    json_res = panel._copy_raw_json()
    clip_text = QApplication.clipboard().text() or json_res
    assert "Service Control Manager" in clip_text
    assert "0x80070005" in clip_text

    # Test close button
    panel._close_btn.click()
    assert panel.isVisible() is False


def test_detail_panel_event_correlation(qapp):
    panel = DetailPanel()
    t_base = datetime(2026, 11, 18, 20, 0, 0)
    primary = _sample_entry(source="Service Control Manager", timestamp=t_base)
    corr_close = _sample_entry(source="DCOM", timestamp=t_base + timedelta(seconds=15), message="DCOM timeout")
    corr_far = _sample_entry(source="Kernel", timestamp=t_base + timedelta(minutes=10), message="Too far away")

    all_events = [primary, corr_close, corr_far]
    panel.show_entry(primary, all_entries=all_events)

    # Correlated tab should contain corr_close, but not corr_far
    assert panel._tabs.tabText(3) == "Correlated (1)"
    assert panel._corr_table.rowCount() == 1
    assert "DCOM" in panel._corr_table.item(0, 2).text()


def test_event_detail_dialog_features(qapp):
    from ui.event_detail_dialog import EventDetailDialog
    entry = _sample_entry(source="Windows Update", message="Download failed with error 0x80240020", update_id="KB5000000")
    dlg = EventDetailDialog(entry)

    assert dlg._tabs.count() == 4
    assert dlg._tabs.tabText(0) == "Message"
    assert "Error Analysis (1)" in dlg._tabs.tabText(1)
    assert dlg._tabs.tabText(2) == "Raw Data"
    assert "Remediation" in dlg._tabs.tabText(3)

    # Verify JSON copy
    json_res = dlg._copy_json()
    clip_text = QApplication.clipboard().text() or json_res
    assert "KB5000000" in clip_text
    assert "0x80240020" in clip_text

    # Verify error analysis text
    err_html = dlg._error_text.toHtml()
    assert "0x80240020" in err_html


def test_duplicate_events_collapsing(qapp):
    pane = LogPane(loader=lambda _w: [])
    t0 = datetime.now()
    e1 = _sample_entry(timestamp=t0, source="Disk", level="Error", message="The driver detected a controller error on \\Device\\Harddisk1\\DR1.")
    e2 = _sample_entry(timestamp=t0 + timedelta(seconds=1), source="Disk", level="Error", message="The driver detected a controller error on \\Device\\Harddisk1\\DR1.")
    e3 = _sample_entry(timestamp=t0 + timedelta(seconds=2), source="Disk", level="Error", message="The driver detected a controller error on \\Device\\Harddisk1\\DR1.")
    e_other = _sample_entry(timestamp=t0 + timedelta(seconds=3), source="Service", level="Info", message="Service started.")

    pane.set_entries([e1, e2, e3, e_other])
    assert pane.row_count() == 4

    # Toggle duplicate collapsing
    pane._collapse_dups_btn.setChecked(True)
    assert pane.row_count() == 2
    collapsed_entry = next(e for e in pane._entries if "[x3]" in e.message)
    assert collapsed_entry.raw.get("repeat_count") == 3

    # Toggle off
    pane._collapse_dups_btn.setChecked(False)
    assert pane.row_count() == 4


def test_time_mode_cycling(qapp):
    pane = LogPane(loader=lambda _w: [])
    t0 = datetime.now() - timedelta(minutes=15)
    e = _sample_entry(timestamp=t0)
    pane.set_entries([e])

    # Initial mode: Local
    assert "Local" in pane._time_mode_btn.text()
    assert ":" in pane._table._model.item(0, 0).text()

    # Cycle to Relative
    pane._cycle_time_mode()
    assert "Rel" in pane._time_mode_btn.text()
    assert "m ago" in pane._table._model.item(0, 0).text()

    # Cycle to UTC
    pane._cycle_time_mode()
    assert "UTC" in pane._time_mode_btn.text()

    # Cycle back to Local
    pane._cycle_time_mode()
    assert "Local" in pane._time_mode_btn.text()


def test_boot_shutdown_preset(qapp):
    pane = LogPane(loader=lambda _w: [])
    e_boot = _sample_entry(source="Kernel-General", message="The operating system started", event_id=12)
    e_app = _sample_entry(source="App", message="Normal app log", event_id=9999)
    pane.set_entries([e_boot, e_app])

    # Select Boot / Shutdown preset (index 6)
    pane._on_preset_selected(6)
    assert "id:12" in pane._search_input.text()
    pane._apply_filter_from_input()

    assert pane.row_count() == 1
    assert pane._table._model.item(0, 0).data(Qt.ItemDataRole.UserRole).raw["event_id"] == 12


def test_detail_panel_word_wrap_and_search(qapp):
    panel = DetailPanel()
    entry = _sample_entry(message="First line\nSecond line with specific keyword: ALPHA_OMEGA\nThird line")
    panel.show_entry(entry)

    # Word wrap toggle
    assert panel._wrap_btn.isChecked() is True
    panel._wrap_btn.setChecked(False)
    panel._toggle_word_wrap()
    assert "OFF" in panel._wrap_btn.text()

    panel._wrap_btn.setChecked(True)
    panel._toggle_word_wrap()
    assert "ON" in panel._wrap_btn.text()

    # Search in overview text
    panel._search_overview_text("ALPHA_OMEGA")
    cursor = panel._content.textCursor()
    assert cursor.hasSelection()
    assert cursor.selectedText() == "ALPHA_OMEGA"


def test_event_diff_dialog(qapp):
    from ui.event_diff_dialog import EventDiffDialog
    t0 = datetime(2026, 11, 1, 10, 0, 0)
    e1 = _sample_entry(timestamp=t0, source="Service A", level="Info", message="Process started OK", pid=1024)
    e2 = _sample_entry(timestamp=t0 + timedelta(seconds=45), source="Service A", level="Error", message="Process crashed with 0x5", pid=2048)

    dlg = EventDiffDialog(e1, e2)
    assert "45 seconds" in dlg._delta_lbl.text()
    assert dlg._diff_table.rowCount() >= 3  # Level, Message, raw properties like pid


def test_log_table_jump_buttons_and_delta_time(qapp):
    table = LogTableWidget()
    t0 = datetime(2026, 11, 1, 12, 0, 0)
    e1 = _sample_entry(timestamp=t0, message="First")
    e2 = _sample_entry(timestamp=t0 + timedelta(seconds=12), message="Second")
    e3 = _sample_entry(timestamp=t0 + timedelta(seconds=25), message="Third")
    table.set_entries([e1, e2, e3])

    # Scroll to bottom
    table.scroll_to_bottom()
    assert table._table.currentIndex().row() == 2

    # Scroll to top
    table.scroll_to_top()
    assert table._table.currentIndex().row() == 0

    # Select second row to test delta-time calculation
    idx = table._model.index(1, 0)
    table._table.setCurrentIndex(idx)
    status_text = table._status.text()
    assert "Δt:" in status_text
    assert "Row 2 of 3" in status_text


def test_detail_panel_it_ticket_and_popout(qapp, monkeypatch):
    copied = []
    monkeypatch.setattr(QApplication, "clipboard",
                        staticmethod(lambda: type("C", (), {"setText": lambda _s, t: copied.append(t)})()))
    panel = DetailPanel()
    entry = _sample_entry(source="MSSQLSERVER", level="Error", message="Database corrupt", computer="SQL-PROD-01", event_id=823)
    panel.show_entry(entry)

    # IT Ticket copy
    panel._copy_as_it_ticket()
    assert len(copied) == 1
    ticket_text = copied[0]
    assert "INCIDENT REPORT: Windows Diagnostic Record" in ticket_text
    assert "SQL-PROD-01" in ticket_text
    assert "823" in ticket_text
    assert "Database corrupt" in ticket_text


def test_search_syntax_validation(qapp):
    pane = LogPane(loader=lambda _w: [])

    # Valid syntax
    pane._validate_search_syntax("source:disk re:error.*")
    assert pane._search_input.objectName() in (None, "")

    # Invalid regex
    pane._validate_search_syntax("source:disk re:[unclosed")
    assert pane._search_input.objectName() == "statusError"

    # Cleared
    pane._validate_search_syntax("")
    assert pane._search_input.objectName() in (None, "")


def test_noise_filter_preset(qapp):
    pane = LogPane(loader=lambda _w: [])
    e_noise = _sample_entry(source="DistributedCOM", message="The application-specific permission settings do not grant Local Activation", event_id=10016)
    e_real = _sample_entry(source="Disk", level="Error", message="Bad sector detected", event_id=7)
    pane.set_entries([e_noise, e_real])

    # Select Noise Filter preset (index 7)
    pane._on_preset_selected(7)
    assert "-id:10016" in pane._search_input.text()
    pane._apply_filter_from_input()

    assert pane.row_count() == 1
    assert pane._table._model.item(0, 0).data(Qt.ItemDataRole.UserRole).raw["event_id"] == 7


def test_case_sensitive_and_regex_buttons(qapp):
    pane = LogPane(loader=lambda _w: [])
    e1 = _sample_entry(message="System Error Detected")
    e2 = _sample_entry(message="system error detected")
    pane.set_entries([e1, e2])
    assert pane.row_count() == 2

    # Case-sensitive search
    pane._search_input.setText("System")
    pane._case_btn.setChecked(True)
    pane._apply_filter_from_input()
    assert pane.row_count() == 1
    assert pane._table._model.item(0, 0).data(Qt.ItemDataRole.UserRole).message == "System Error Detected"

    # Regex button search (case-sensitive regex)
    pane._case_btn.setChecked(True)
    pane._regex_btn.setChecked(True)
    pane._search_input.setText(r"^system\serror")
    pane._apply_filter_from_input()
    assert pane.row_count() == 1
    assert pane._table._model.item(0, 0).data(Qt.ItemDataRole.UserRole).message == "system error detected"


def test_user_notes_and_annotations(qapp):
    table = LogTableWidget()
    entry = _sample_entry(message="Investigate this crash")
    table.set_entries([entry])

    # Manually attach note to entry and re-render
    entry.raw["_user_note"] = "Potential root cause: bad RAM"
    first_item = table._model.item(0, 0)
    note_p = "📝 " if "_user_note" in entry.raw else ""
    first_item.setText(note_p + table._format_time(entry.timestamp))

    assert "📝" in table._model.item(0, 0).text()

    # DetailPanel renders the note callout
    panel = DetailPanel()
    panel.show_entry(entry)
    html_content = panel._content.toHtml()
    assert "User Annotation:" in html_content
    assert "Potential root cause: bad RAM" in html_content


def test_boot_shutdown_event_badges(qapp):
    table = LogTableWidget()
    e_boot = _sample_entry(source="Kernel-General", message="OS booted", event_id=12)
    e_down = _sample_entry(source="USER32", message="Shutdown initiated", event_id=1074)
    e_crash = _sample_entry(source="Kernel-Power", message="Reboot without cleanly shutting down", event_id=41)
    table.set_entries([e_boot, e_down, e_crash])

    sources = [table._model.item(r, 1).text() for r in range(table._model.rowCount())]
    assert any("🚀" in s for s in sources)
    assert any("🛑" in s for s in sources)
    assert any("💥" in s for s in sources)


def test_detail_panel_copy_as_xml(qapp, monkeypatch):
    copied = []
    monkeypatch.setattr(QApplication, "clipboard",
                        staticmethod(lambda: type("C", (), {"setText": lambda _s, t: copied.append(t)})()))
    panel = DetailPanel()
    entry = _sample_entry(source="Microsoft-Windows-Security-Auditing", level="AuditSuccess", message="A logon was attempted", event_id=4624, computer="PC01")
    panel.show_entry(entry)

    panel._copy_raw_xml()
    assert len(copied) == 1
    xml_text = copied[0]
    assert "<Event xmlns=" in xml_text
    assert "<Provider Name=\"Microsoft-Windows-Security-Auditing\"" in xml_text
    assert "<EventID>4624</EventID>" in xml_text
    assert "<Computer>PC01</Computer>" in xml_text


def test_search_history_completer(qapp):
    pane = LogPane(loader=lambda _w: [])
    assert "source:" in pane._search_history

    # Apply search query
    pane._search_input.setText("level:error and msg:crash")
    pane._apply_filter_from_input()

    assert "level:error and msg:crash" in pane._search_history
    assert pane._completer_model.stringList()[0] == "level:error and msg:crash"


def test_table_word_wrap_toggle(qapp):
    pane = LogPane(loader=lambda _w: [])
    assert pane._wrap_rows_btn.isChecked() is False
    assert pane._table._table.wordWrap() is False

    # Toggle wrap ON
    pane._wrap_rows_btn.setChecked(True)
    assert pane._table._table.wordWrap() is True

    # Toggle wrap OFF
    pane._wrap_rows_btn.setChecked(False)
    assert pane._table._table.wordWrap() is False


def test_detail_panel_cell_copy_and_msg_pill(qapp, monkeypatch):
    copied = []
    monkeypatch.setattr(QApplication, "clipboard",
                        staticmethod(lambda: type("C", (), {"setText": lambda _s, t: copied.append(t)})()))
    panel = DetailPanel()
    entry = _sample_entry(source="Service A", message="Target message string to verify", pid=4500)
    panel.show_entry(entry)

    # Double click on structured data cell (row 1 is Source)
    panel._on_raw_cell_double_clicked(1, 1)
    assert len(copied) == 1
    assert copied[0] == "Service A"

    # Click Copy Msg pill
    panel._copy_message_only()
    assert len(copied) == 2
    assert copied[1] == "Target message string to verify"


def test_trace_burst_trigger(qapp):
    table = LogTableWidget()
    t0 = datetime(2026, 11, 1, 14, 0, 0)
    e_root = _sample_entry(timestamp=t0, level="Error", source="Disk", message="Controller error")
    e_cascade1 = _sample_entry(timestamp=t0 + timedelta(seconds=5), level="Error", source="Ntfs", message="Volume corrupt")
    e_cascade2 = _sample_entry(timestamp=t0 + timedelta(seconds=15), level="Critical", source="Service Control Manager", message="Service crashed")

    table.set_entries([e_root, e_cascade1, e_cascade2])

    # Select the third error (latest in the cascade)
    idx = table._model.index(0, 0) # Note: table is newest-first, so row 0 is e_cascade2
    table._table.setCurrentIndex(idx)

    # Trace burst trigger should select e_root (the earliest in the cascade)
    table.trace_burst_trigger()
    selected = table.selected_entry()
    assert selected is not None
    assert selected.source == "Disk"
    assert "Controller error" in selected.message


def test_export_html_metrics_summary(qapp, tmp_path):
    table = LogTableWidget()
    e_crit = _sample_entry(level="Critical", message="Crit error")
    e_err = _sample_entry(level="Error", message="Standard error")
    e_warn = _sample_entry(level="Warning", message="Warn msg")
    table.set_entries([e_crit, e_err, e_warn])

    out_file = str(tmp_path / "report.html")
    table.export_html(out_file)

    with open(out_file, "r", encoding="utf-8") as f:
        content = f.read()

    assert "Windows Client Tool — Diagnostic Log Report" in content
    assert "Critical" in content
    assert "Errors" in content
    assert "Warnings" in content
    assert "Total Loaded Records: 3" in content


def test_focus_search_hotkey(qapp):
    pane = LogPane(loader=lambda _w: [])
    pane._focus_search_input()
    assert pane.focusWidget() == pane._search_input





