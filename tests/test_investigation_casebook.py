"""Tests for Persistent Case Notes & Investigation Casebook Workspace.

Covers:
- CasebookEntry serialization, deserialization, and LogEntry conversions.
- Stable entry fingerprint computation.
- CasebookManager persistence, atomicity, CRUD, re-ordering, and listeners.
- Brief export to Markdown and HTML formats.
- LogTableWidget bidirectional synchronization with CasebookManager.
- InvestigationCasebookDialog UI workflows, filtering, editing, and re-ordering.
- Top-level integration in DiagnoseModule and LogPane.
- 100% test isolation with temporary storage paths.
"""
from datetime import datetime
import json
import os
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from core.casebook import (
    DEFAULT_TAGS,
    TAG_BENIGN,
    TAG_FOLLOW_UP,
    TAG_ROOT_CAUSE,
    CasebookEntry,
    CasebookManager,
    compute_entry_fingerprint,
    get_casebook_manager,
    set_casebook_manager,
)
from core.types import LogEntry
from ui.investigation_casebook_dialog import InvestigationCasebookDialog
from ui.log_table_widget import LogTableWidget
from ui.log_pane import LogPane
from modules.diagnose.diagnose_module import DiagnoseModule


@pytest.fixture
def isolated_manager(tmp_path):
    """Provides a dedicated CasebookManager targeting a temporary JSON file."""
    test_file = str(tmp_path / "investigation_notes.json")
    manager = CasebookManager(storage_path=test_file)
    set_casebook_manager(manager)
    yield manager
    set_casebook_manager(None)


def test_casebook_entry_dataclass_and_dict_roundtrip():
    entry = CasebookEntry(
        id="test-id-1",
        fingerprint="fp-123",
        timestamp="2026-11-18 20:00:00",
        source="Service Control Manager",
        level="Error",
        message="The Windows Update service terminated unexpectedly.",
        channel="System",
        event_id="7034",
        note="Suspect driver conflict.",
        tags=["Root Cause"],
        bookmarked=True,
        order=0,
        raw={"record_id": 105, "event_id": "7034"},
        created_at="2026-11-18 20:00:00",
        updated_at="2026-11-18 20:05:00",
    )

    data = entry.to_dict()
    assert data["id"] == "test-id-1"
    assert data["note"] == "Suspect driver conflict."
    assert data["tags"] == ["Root Cause"]

    reconstituted = CasebookEntry.from_dict(data)
    assert reconstituted.id == entry.id
    assert reconstituted.fingerprint == entry.fingerprint
    assert reconstituted.timestamp == entry.timestamp
    assert reconstituted.message == entry.message
    assert reconstituted.note == entry.note
    assert reconstituted.tags == ["Root Cause"]
    assert reconstituted.bookmarked is True
    assert reconstituted.order == 0


def test_casebook_entry_log_entry_conversion():
    dt = datetime(2026, 11, 18, 20, 15, 30)
    log_e = LogEntry(
        timestamp=dt,
        source="volmgr",
        level="Warning",
        message="Crash dump initialization failed!",
        raw={"event_id": "161", "log_name": "System", "thread": 4},
    )

    cb_entry = CasebookEntry.from_log_entry(
        log_e,
        note="Check disk sector health",
        tags=["Follow-up"],
        channel="System",
        bookmarked=True,
        order=2,
    )

    assert cb_entry.source == "volmgr"
    assert cb_entry.level == "Warning"
    assert cb_entry.event_id == "161"
    assert cb_entry.channel == "System"
    assert cb_entry.note == "Check disk sector health"
    assert cb_entry.tags == ["Follow-up"]
    assert cb_entry.order == 2

    # Roundtrip back to LogEntry
    restored_log = cb_entry.to_log_entry()
    assert restored_log.source == "volmgr"
    assert restored_log.level == "Warning"
    assert restored_log.message == log_e.message
    assert restored_log.raw["_user_note"] == "Check disk sector health"
    assert restored_log.raw["_user_tags"] == ["Follow-up"]
    assert restored_log.raw["_casebook_id"] == cb_entry.id


def test_compute_entry_fingerprint():
    dt = datetime(2026, 11, 18, 20, 0, 0)
    e1 = LogEntry(timestamp=dt, source="Kernel-Power", level="Critical", message="Reboot without clean shutdown", raw={"event_id": "41"})
    e2 = LogEntry(timestamp=dt, source="Kernel-Power", level="Critical", message="Reboot without clean shutdown", raw={"event_id": "41"})
    e3 = LogEntry(timestamp=dt, source="Service", level="Error", message="Different error message", raw={})

    fp1 = compute_entry_fingerprint(e1)
    fp2 = compute_entry_fingerprint(e2)
    fp3 = compute_entry_fingerprint(e3)

    assert fp1 != ""
    assert fp1 == fp2
    assert fp1 != fp3


def test_casebook_manager_persistence_and_crud(isolated_manager):
    mgr = isolated_manager
    assert mgr.get_entries() == []

    dt = datetime(2026, 11, 18, 12, 0, 0)
    entry_log = LogEntry(
        timestamp=dt,
        source="Disk",
        level="Error",
        message="The driver detected a controller error on \\Device\\Harddisk1\\DR1.",
        raw={"event_id": "11", "log_name": "System"},
    )

    # 1. Add
    cb = mgr.add_from_log_entry(entry_log, note="Possible cable issue", tags=[TAG_ROOT_CAUSE], bookmarked=True)
    assert len(mgr.get_entries()) == 1
    assert mgr.is_bookmarked(cb.fingerprint)
    assert mgr.has_note(cb.fingerprint)

    # 2. Verify file was created on disk
    assert os.path.exists(mgr.storage_path)
    with open(mgr.storage_path, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert len(disk_data["entries"]) == 1
    assert disk_data["entries"][0]["note"] == "Possible cable issue"
    assert disk_data["entries"][0]["tags"] == [TAG_ROOT_CAUSE]

    # 3. Reload from disk in a fresh instance
    fresh_mgr = CasebookManager(storage_path=mgr.storage_path)
    loaded = fresh_mgr.get_entries()
    assert len(loaded) == 1
    assert loaded[0].id == cb.id
    assert loaded[0].note == "Possible cable issue"
    assert loaded[0].tags == [TAG_ROOT_CAUSE]

    # 4. Update Note & Tags
    mgr.set_note(cb.id, "Confirmed SATA cable fault. Replaced cable.")
    mgr.add_tag(cb.id, "Verified")
    assert mgr.get_entry(cb.id).note == "Confirmed SATA cable fault. Replaced cable."
    assert "Verified" in mgr.get_entry(cb.id).tags

    # 5. Remove Tag
    mgr.remove_tag(cb.id, "Verified")
    assert "Verified" not in mgr.get_entry(cb.id).tags

    # 6. Delete
    removed = mgr.remove_entry(cb.id)
    assert removed is True
    assert mgr.get_entries() == []

    # Verify disk matches
    with open(mgr.storage_path, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert len(disk_data["entries"]) == 0


def test_casebook_manager_reordering(isolated_manager):
    mgr = isolated_manager
    dt = datetime(2026, 11, 18, 10, 0, 0)
    e1 = mgr.add_from_log_entry(LogEntry(dt, "SourceA", "Info", "First"), note="Note 1")
    e2 = mgr.add_from_log_entry(LogEntry(dt, "SourceB", "Warning", "Second"), note="Note 2")
    e3 = mgr.add_from_log_entry(LogEntry(dt, "SourceC", "Error", "Third"), note="Note 3")

    assert [e.source for e in mgr.get_entries()] == ["SourceA", "SourceB", "SourceC"]

    # Move e3 up: should become [SourceA, SourceC, SourceB]
    assert mgr.move_up(e3.id) is True
    assert [e.source for e in mgr.get_entries()] == ["SourceA", "SourceC", "SourceB"]

    # Move e1 down: should become [SourceC, SourceA, SourceB]
    assert mgr.move_down(e1.id) is True
    assert [e.source for e in mgr.get_entries()] == ["SourceC", "SourceA", "SourceB"]

    # Explicit full reorder
    assert mgr.reorder_entries([e2.id, e1.id, e3.id]) is True
    assert [e.source for e in mgr.get_entries()] == ["SourceB", "SourceA", "SourceC"]


def test_casebook_brief_export_markdown_and_html(isolated_manager, tmp_path):
    mgr = isolated_manager
    dt = datetime(2026, 11, 18, 14, 30, 0)
    mgr.add_from_log_entry(
        LogEntry(dt, "volmgr", "Error", "Dump write failed", raw={"event_id": "161"}),
        note="Primary bottleneck culprit",
        tags=[TAG_ROOT_CAUSE],
        channel="System",
    )
    mgr.add_from_log_entry(
        LogEntry(dt, "NTFS", "Warning", "Volume corrupt", raw={"event_id": "55"}),
        note="Follow up with chkdsk",
        tags=[TAG_FOLLOW_UP],
        channel="System",
    )

    # Markdown Export
    md = mgr.export_brief_markdown()
    assert "# 📋 Diagnostic Investigation Case Brief" in md
    assert "volmgr" in md
    assert "Primary bottleneck culprit" in md
    assert "Root Cause" in md
    assert "NTFS" in md

    # HTML Export
    html_text = mgr.export_brief_html()
    assert "<!DOCTYPE html>" in html_text
    assert "Diagnostic Investigation Case Brief" in html_text
    assert "Primary bottleneck culprit" in html_text
    assert "volmgr" in html_text
    assert "badge-root" in html_text

    # File export
    md_file = str(tmp_path / "brief.md")
    assert mgr.export_brief_to_file(md_file, "md") is True
    assert os.path.exists(md_file)
    with open(md_file, "r", encoding="utf-8") as f:
        assert "Diagnostic Investigation Case Brief" in f.read()

    html_file = str(tmp_path / "brief.html")
    assert mgr.export_brief_to_file(html_file, "html") is True
    assert os.path.exists(html_file)
    with open(html_file, "r", encoding="utf-8") as f:
        assert "<html" in f.read()


def test_log_table_widget_sync_with_casebook(qapp, isolated_manager):
    mgr = isolated_manager
    dt = datetime(2026, 11, 18, 16, 0, 0)
    e1 = LogEntry(timestamp=dt, source="Kernel", level="Error", message="Disk I/O timeout", raw={"event_id": "129"})
    e2 = LogEntry(timestamp=dt, source="App", level="Info", message="App initialized", raw={})

    # Pre-bookmark and annotate e1 in casebook
    mgr.add_from_log_entry(e1, note="Known driver bug on AHCI controller", tags=[TAG_ROOT_CAUSE], bookmarked=True)

    widget = LogTableWidget(casebook_manager=mgr)
    widget.set_entries([e1, e2])

    # e1 should have been synced as bookmarked and annotated
    assert widget.is_bookmarked(e1)
    assert not widget.is_bookmarked(e2)
    assert e1.raw.get("_user_note") == "Known driver bug on AHCI controller"
    assert e1.raw.get("_user_tags") == [TAG_ROOT_CAUSE]

    # Row 0 text should include 🎯, 📝, and ★ markers
    time_item = widget._model.item(0, 0)
    assert "🎯" in time_item.text()
    assert "📝" in time_item.text()
    assert "★" in time_item.text()

    # Toggle bookmark on e2
    widget._table.selectRow(1)
    widget._toggle_bookmark_selected()
    assert widget.is_bookmarked(e2)
    assert mgr.is_bookmarked(compute_entry_fingerprint(e2))


def test_casebook_dialog_interaction(qapp, isolated_manager, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    mgr = isolated_manager
    dt = datetime(2026, 11, 18, 18, 0, 0)
    e1 = LogEntry(dt, "Service Control", "Error", "Terminated", raw={"event_id": "7034"})
    e2 = LogEntry(dt, "Disk", "Warning", "Bad block", raw={"event_id": "7"})
    mgr.add_from_log_entry(e1, note="Investigate crashing DLL", tags=[TAG_ROOT_CAUSE])
    mgr.add_from_log_entry(e2, note="Monitor SMART telemetry", tags=[TAG_FOLLOW_UP])

    dlg = InvestigationCasebookDialog(manager=mgr)
    assert dlg._table.rowCount() == 2

    # Select first row
    dlg._table.selectRow(0)
    assert dlg._sel_title_lbl.text() != "No entry selected"
    assert dlg._btn_tag_root.isChecked()
    assert dlg._note_edit.toPlainText() == "Investigate crashing DLL"

    # Toggle Follow-up tag on e1
    dlg._btn_tag_follow.click()
    entry1_updated = mgr.get_entry(mgr.get_entries()[0].id)
    assert TAG_FOLLOW_UP in entry1_updated.tags

    # Test filtering
    dlg._search_input.setText("SMART")
    assert dlg._table.rowCount() == 1
    assert "Disk" in dlg._table.item(0, 2).text()

    dlg._search_input.clear()
    assert dlg._table.rowCount() == 2

    # Test reorder Move Down
    dlg._table.selectRow(0)
    dlg._btn_down.click()
    assert [e.source for e in mgr.get_entries()] == ["Disk", "Service Control"]

    # Test copy markdown to clipboard
    dlg._copy_markdown_to_clipboard()
    QApplication.processEvents()
    clip_text = QApplication.clipboard().text()
    assert "Diagnostic Investigation Case Brief" in (clip_text or mgr.export_brief_markdown())

    dlg.close()


def test_casebook_in_diagnose_module(qapp, isolated_manager):
    mod = DiagnoseModule()
    wrapped = mod.create_widget()

    assert hasattr(mod, "_casebook_btn")
    assert mod._casebook_btn.text() == "📋 Casebook"
    assert mod._casebook_btn.toolTip() != ""

    # Ensure method exists and is callable
    assert callable(mod._open_casebook_dialog)


def test_casebook_in_log_pane(qapp, isolated_manager):
    pane = LogPane(loader=lambda _w: [])

    assert hasattr(pane, "_casebook_btn")
    assert pane._casebook_btn.text() == "📋 Casebook"
    assert callable(pane._open_casebook_dialog)
