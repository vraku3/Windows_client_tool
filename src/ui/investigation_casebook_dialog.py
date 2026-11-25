"""Investigation Casebook Dialog.

A unified investigation workspace dialog displaying all bookmarked and annotated events
across all diagnostic channels (Event Viewer, CBS Log, DISM, Windows Update, Reliability,
Crash Dumps, etc.).

Allows investigators to:
- Review and search evidence across channels.
- Annotate events with persistent investigator notes.
- Assign and manage diagnostic tags (e.g. "Root Cause", "Follow-up", "Benign", custom).
- Re-order evidence (Move Up / Move Down) for timeline narrative or executive reports.
- Export structured Case Briefs in Markdown and HTML formats for team handoff.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.casebook import (
    DEFAULT_TAGS,
    TAG_BENIGN,
    TAG_FOLLOW_UP,
    TAG_ROOT_CAUSE,
    CasebookEntry,
    CasebookManager,
    get_casebook_manager,
)
from core.table_ui import set_role
from core.semantic_colors import semantic
from ui.event_detail_dialog import EventDetailDialog

logger = logging.getLogger(__name__)


class InvestigationCasebookDialog(QDialog):
    """Investigation Casebook & Workspace Dialog."""

    def __init__(
        self,
        manager: Optional[CasebookManager] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager or get_casebook_manager()
        self._current_entry_id: Optional[str] = None
        self._displayed_entries: List[CasebookEntry] = []

        self.setWindowTitle("Investigation Casebook")
        self.resize(1020, 680)
        self.setMinimumSize(800, 520)

        self._setup_ui()
        self._load_data()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._build_header_and_toolbar(root)
        self._build_splitter(root)
        self._build_bottom_bar(root)

    def _build_header_and_toolbar(self, root: QVBoxLayout) -> None:
        # ── 1. Header & Summary Stats ──────────────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("card")
        h_layout = QHBoxLayout(header_frame)
        h_layout.setContentsMargins(8, 4, 8, 4)
        h_layout.setSpacing(12)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("📋 <b>Investigation Casebook & Incident Workspace</b>")
        set_role(title_lbl, "heading")
        title_vbox.addWidget(title_lbl)

        sub_lbl = QLabel("Persistent diagnostic evidence, investigator notes, and team handoff briefs.")
        set_role(sub_lbl, "muted")
        title_vbox.addWidget(sub_lbl)
        h_layout.addLayout(title_vbox, 1)

        # Quick stats badges
        self._stat_total = QLabel("Total: 0")
        set_role(self._stat_total, "metric")
        h_layout.addWidget(self._stat_total)

        self._stat_root = QLabel("🎯 Root Cause: 0")
        set_role(self._stat_root, "statusError")
        h_layout.addWidget(self._stat_root)

        self._stat_follow = QLabel("📌 Follow-up: 0")
        set_role(self._stat_follow, "statusWarning")
        h_layout.addWidget(self._stat_follow)

        self._stat_benign = QLabel("🟢 Benign: 0")
        set_role(self._stat_benign, "statusSuccess")
        h_layout.addWidget(self._stat_benign)

        root.addWidget(header_frame)

        # ── 2. Filter & Re-order Toolbar ───────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Filter casebook entries (text, source, tag, note)...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._search_input, 1)

        self._tag_filter = QComboBox()
        self._tag_filter.addItems([
            "All Tags",
            f"🎯 {TAG_ROOT_CAUSE}",
            f"📌 {TAG_FOLLOW_UP}",
            f"🟢 {TAG_BENIGN}",
            "📝 Has Notes",
            "★ Bookmarked Only",
        ])
        self._tag_filter.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self._tag_filter)

        self._btn_up = QPushButton("▲ Move Up")
        self._btn_up.setToolTip("Move selected evidence item up in sequence")
        self._btn_up.clicked.connect(self._on_move_up)
        toolbar.addWidget(self._btn_up)

        self._btn_down = QPushButton("▼ Move Down")
        self._btn_down.setToolTip("Move selected evidence item down in sequence")
        self._btn_down.clicked.connect(self._on_move_down)
        toolbar.addWidget(self._btn_down)

        root.addLayout(toolbar)

    def _build_splitter(self, root: QVBoxLayout) -> None:
        self._splitter = QSplitter(Qt.Orientation.Vertical)

        # Top half: Evidentiary Table
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "#", "Time", "Channel / Source", "Level", "Tags", "Investigator Note", "Message"
        ])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 36)
        self._table.setColumnWidth(1, 140)
        self._table.setColumnWidth(2, 170)
        self._table.setColumnWidth(3, 75)
        self._table.setColumnWidth(4, 150)
        self._table.setColumnWidth(5, 200)

        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(lambda _item: self._on_inspect_event())
        self._splitter.addWidget(self._table)

        # Bottom half: Inspector / Editor Pane
        self._editor_panel = QFrame()
        self._editor_panel.setObjectName("card")
        editor_layout = QVBoxLayout(self._editor_panel)
        editor_layout.setContentsMargins(10, 8, 10, 8)
        editor_layout.setSpacing(6)

        # Info row: title + inspect button
        info_row = QHBoxLayout()
        self._sel_title_lbl = QLabel("No entry selected")
        self._sel_title_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        set_role(self._sel_title_lbl, "heading")
        info_row.addWidget(self._sel_title_lbl, 1)

        self._btn_inspect = QPushButton("🔍 Inspect Full Event")
        self._btn_inspect.setToolTip("Open full Event Detail Inspector for this evidentiary record")
        self._btn_inspect.clicked.connect(self._on_inspect_event)
        info_row.addWidget(self._btn_inspect)

        self._btn_delete = QPushButton("🗑️ Remove from Casebook")
        self._btn_delete.setToolTip("Remove this record from the investigation casebook")
        self._btn_delete.clicked.connect(self._on_delete_entry)
        info_row.addWidget(self._btn_delete)
        editor_layout.addLayout(info_row)

        # Tags Management Row
        tags_row = QHBoxLayout()
        tags_row.setSpacing(6)
        tags_lbl = QLabel("<b>Tags:</b>")
        tags_row.addWidget(tags_lbl)

        self._btn_tag_root = QPushButton("🎯 Root Cause")
        self._btn_tag_root.setCheckable(True)
        self._btn_tag_root.clicked.connect(lambda: self._toggle_tag(TAG_ROOT_CAUSE))
        tags_row.addWidget(self._btn_tag_root)

        self._btn_tag_follow = QPushButton("📌 Follow-up")
        self._btn_tag_follow.setCheckable(True)
        self._btn_tag_follow.clicked.connect(lambda: self._toggle_tag(TAG_FOLLOW_UP))
        tags_row.addWidget(self._btn_tag_follow)

        self._btn_tag_benign = QPushButton("🟢 Benign")
        self._btn_tag_benign.setCheckable(True)
        self._btn_tag_benign.clicked.connect(lambda: self._toggle_tag(TAG_BENIGN))
        tags_row.addWidget(self._btn_tag_benign)

        self._custom_tag_input = QLineEdit()
        self._custom_tag_input.setPlaceholderText("+ Add custom tag...")
        self._custom_tag_input.setMaximumWidth(150)
        self._custom_tag_input.returnPressed.connect(self._on_add_custom_tag)
        tags_row.addWidget(self._custom_tag_input)

        self._btn_add_tag = QPushButton("Add")
        self._btn_add_tag.setMaximumWidth(45)
        self._btn_add_tag.clicked.connect(self._on_add_custom_tag)
        tags_row.addWidget(self._btn_add_tag)

        self._tags_display_lbl = QLabel("")
        set_role(self._tags_display_lbl, "muted")
        tags_row.addWidget(self._tags_display_lbl, 1)

        editor_layout.addLayout(tags_row)

        # Note Editor
        note_hdr_row = QHBoxLayout()
        note_hdr = QLabel("<b>📝 Investigator Note & Hypothesis:</b>")
        note_hdr_row.addWidget(note_hdr)
        note_hdr_row.addStretch()

        self._btn_save_note = QPushButton("Save Note")
        self._btn_save_note.clicked.connect(self._save_note_direct)
        note_hdr_row.addWidget(self._btn_save_note)
        editor_layout.addLayout(note_hdr_row)

        self._note_edit = QPlainTextEdit()
        self._note_edit.setPlaceholderText(
            "Enter diagnostic notes, analysis findings, correlation hypotheses, or resolution steps..."
        )
        self._note_edit.setMaximumHeight(90)
        self._note_debounce_timer = QTimer(self)
        self._note_debounce_timer.setSingleShot(True)
        self._note_debounce_timer.setInterval(400)
        self._note_debounce_timer.timeout.connect(self._auto_save_note)
        self._note_edit.textChanged.connect(lambda: self._note_debounce_timer.start())
        editor_layout.addWidget(self._note_edit)

        self._splitter.addWidget(self._editor_panel)
        self._splitter.setSizes([380, 200])
        root.addWidget(self._splitter, 1)

    def _build_bottom_bar(self, root: QVBoxLayout) -> None:
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        # Export Case Brief Menu Button
        self._btn_export = QPushButton("Export Case Brief ▾")
        self._btn_export.setToolTip("Export formatted case brief in Markdown or HTML for team handoff")
        export_menu = QMenu(self)

        act_cp_md = export_menu.addAction("📑 Copy Brief Markdown to Clipboard")
        act_cp_md.triggered.connect(self._copy_markdown_to_clipboard)

        act_save_md = export_menu.addAction("💾 Export Brief to Markdown (.md)...")
        act_save_md.triggered.connect(self._export_markdown_file)

        act_save_html = export_menu.addAction("🌐 Export Brief to HTML Report (.html)...")
        act_save_html.triggered.connect(self._export_html_file)

        act_cp_html = export_menu.addAction("📋 Copy Brief HTML to Clipboard")
        act_cp_html.triggered.connect(self._copy_html_to_clipboard)

        self._btn_export.setMenu(export_menu)
        bottom_bar.addWidget(self._btn_export)

        self._btn_clear_all = QPushButton("Clear Casebook...")
        self._btn_clear_all.setToolTip("Clear all entries from this casebook")
        self._btn_clear_all.clicked.connect(self._on_clear_all)
        bottom_bar.addWidget(self._btn_clear_all)

        bottom_bar.addStretch()

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.accept)
        bottom_bar.addWidget(self._btn_close)

        root.addLayout(bottom_bar)

    # ── Data Loading & Table Population ────────────────────────────────

    def _load_data(self) -> None:
        """Fetch records from CasebookManager and render."""
        entries = self._manager.get_entries()
        self._update_stats(entries)
        self._apply_filter()

    def _update_stats(self, entries: List[CasebookEntry]) -> None:
        total = len(entries)
        root_c = sum(1 for e in entries if TAG_ROOT_CAUSE in e.tags)
        follow_c = sum(1 for e in entries if TAG_FOLLOW_UP in e.tags)
        benign_c = sum(1 for e in entries if TAG_BENIGN in e.tags)

        self._stat_total.setText(f"Total: {total}")
        self._stat_root.setText(f"🎯 Root Cause: {root_c}")
        self._stat_follow.setText(f"📌 Follow-up: {follow_c}")
        self._stat_benign.setText(f"🟢 Benign: {benign_c}")

    def _apply_filter(self) -> None:
        q = self._search_input.text().strip().lower()
        tag_mode = self._tag_filter.currentText()

        all_entries = self._manager.get_entries()
        filtered: List[CasebookEntry] = []

        for e in all_entries:
            # Tag dropdown filtering
            if TAG_ROOT_CAUSE in tag_mode and TAG_ROOT_CAUSE not in e.tags:
                continue
            if TAG_FOLLOW_UP in tag_mode and TAG_FOLLOW_UP not in e.tags:
                continue
            if TAG_BENIGN in tag_mode and TAG_BENIGN not in e.tags:
                continue
            if "Has Notes" in tag_mode and not e.note.strip():
                continue
            if "Bookmarked Only" in tag_mode and not e.bookmarked:
                continue

            # Query text matching
            if q:
                match = (
                    q in e.message.lower()
                    or q in e.note.lower()
                    or q in e.source.lower()
                    or q in e.channel.lower()
                    or q in e.level.lower()
                    or any(q in t.lower() for t in e.tags)
                    or (e.event_id and q in str(e.event_id).lower())
                )
                if not match:
                    continue

            filtered.append(e)

        self._displayed_entries = filtered
        self._populate_table(filtered)

    def _populate_table(self, entries: List[CasebookEntry]) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(entries))

        for row, e in enumerate(entries):
            # 0: Order index
            item_order = QTableWidgetItem(str(row + 1))
            item_order.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_order.setData(Qt.ItemDataRole.UserRole, e.id)
            self._table.setItem(row, 0, item_order)

            # 1: Timestamp
            item_time = QTableWidgetItem(e.timestamp)
            self._table.setItem(row, 1, item_time)

            # 2: Channel / Source
            src_text = f"{e.channel} / {e.source}" if e.channel and e.channel != e.source else e.source
            item_src = QTableWidgetItem(src_text)
            self._table.setItem(row, 2, item_src)

            # 3: Level
            item_lvl = QTableWidgetItem(e.level)
            self._table.setItem(row, 3, item_lvl)

            # 4: Tags
            tags_str = ", ".join(e.tags) if e.tags else "—"
            item_tags = QTableWidgetItem(tags_str)
            if TAG_ROOT_CAUSE in e.tags:
                item_tags.setForeground(QColor(semantic("error")))
            elif TAG_FOLLOW_UP in e.tags:
                item_tags.setForeground(QColor(semantic("warning")))
            elif TAG_BENIGN in e.tags:
                item_tags.setForeground(QColor(semantic("success")))
            self._table.setItem(row, 4, item_tags)

            # 5: Investigator Note
            note_first_line = e.note.split("\n")[0] if e.note else ""
            item_note = QTableWidgetItem(f"📝 {note_first_line}" if note_first_line else "—")
            self._table.setItem(row, 5, item_note)

            # 6: Message
            msg_clean = " ".join((e.message or "").split())
            item_msg = QTableWidgetItem(msg_clean[:300])
            self._table.setItem(row, 6, item_msg)

        self._table.blockSignals(False)

        # Restore selection if current entry is still visible
        selected_row = -1
        if self._current_entry_id:
            for r, e in enumerate(entries):
                if e.id == self._current_entry_id:
                    selected_row = r
                    break

        if selected_row >= 0:
            self._table.selectRow(selected_row)
        elif len(entries) > 0:
            self._table.selectRow(0)
        else:
            self._clear_editor()

    # ── Selection & Editor Synchronization ─────────────────────────────

    def _on_selection_changed(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self._clear_editor()
            return
        row = rows[0].row()
        if 0 <= row < len(self._displayed_entries):
            entry = self._displayed_entries[row]
            self._current_entry_id = entry.id
            self._populate_editor(entry)

    def _populate_editor(self, entry: CasebookEntry) -> None:
        eid_str = f" [ID: {entry.event_id}]" if entry.event_id else ""
        self._sel_title_lbl.setText(
            f"Item #{entry.order + 1}: [{entry.level}] {entry.source}{eid_str} — {entry.timestamp}"
        )

        # Tag buttons
        self._btn_tag_root.blockSignals(True)
        self._btn_tag_follow.blockSignals(True)
        self._btn_tag_benign.blockSignals(True)
        self._btn_tag_root.setChecked(TAG_ROOT_CAUSE in entry.tags)
        self._btn_tag_follow.setChecked(TAG_FOLLOW_UP in entry.tags)
        self._btn_tag_benign.setChecked(TAG_BENIGN in entry.tags)
        self._btn_tag_root.blockSignals(False)
        self._btn_tag_follow.blockSignals(False)
        self._btn_tag_benign.blockSignals(False)

        tags_display = " | ".join(f"🏷️ {t}" for t in entry.tags) if entry.tags else "(No tags assigned)"
        self._tags_display_lbl.setText(tags_display)

        # Note field
        self._note_edit.blockSignals(True)
        self._note_edit.setPlainText(entry.note)
        self._note_edit.blockSignals(False)

        self._btn_inspect.setEnabled(True)
        self._btn_delete.setEnabled(True)
        self._btn_up.setEnabled(entry.order > 0)
        self._btn_down.setEnabled(entry.order < len(self._manager.get_entries()) - 1)

    def _clear_editor(self) -> None:
        self._current_entry_id = None
        self._sel_title_lbl.setText("No entry selected")
        self._btn_tag_root.setChecked(False)
        self._btn_tag_follow.setChecked(False)
        self._btn_tag_benign.setChecked(False)
        self._tags_display_lbl.setText("")
        self._note_edit.blockSignals(True)
        self._note_edit.clear()
        self._note_edit.blockSignals(False)
        self._btn_inspect.setEnabled(False)
        self._btn_delete.setEnabled(False)
        self._btn_up.setEnabled(False)
        self._btn_down.setEnabled(False)

    # ── Tag & Note Modification ────────────────────────────────────────

    def _toggle_tag(self, tag: str) -> None:
        if not self._current_entry_id:
            return
        entry = self._manager.get_entry(self._current_entry_id)
        if not entry:
            return

        if tag in entry.tags:
            self._manager.remove_tag(self._current_entry_id, tag)
        else:
            self._manager.add_tag(self._current_entry_id, tag)

        self._refresh_after_change()

    def _on_add_custom_tag(self) -> None:
        tag = self._custom_tag_input.text().strip()
        if not tag or not self._current_entry_id:
            return
        self._manager.add_tag(self._current_entry_id, tag)
        self._custom_tag_input.clear()
        self._refresh_after_change()

    def _auto_save_note(self) -> None:
        if not self._current_entry_id:
            return
        new_note = self._note_edit.toPlainText().strip()
        entry = self._manager.get_entry(self._current_entry_id)
        if entry and entry.note != new_note:
            self._manager.set_note(self._current_entry_id, new_note)
            # Update note in table cell directly without stealing focus
            rows = self._table.selectionModel().selectedRows()
            if rows:
                r = rows[0].row()
                note_first = new_note.split("\n")[0] if new_note else ""
                item = self._table.item(r, 5)
                if item:
                    item.setText(f"📝 {note_first}" if note_first else "—")

    def _save_note_direct(self) -> None:
        self._auto_save_note()
        self._refresh_after_change()

    def _refresh_after_change(self) -> None:
        entries = self._manager.get_entries()
        self._update_stats(entries)
        self._apply_filter()

    # ── Re-ordering ────────────────────────────────────────────────────

    def _on_move_up(self) -> None:
        if not self._current_entry_id:
            return
        if self._manager.move_up(self._current_entry_id):
            self._refresh_after_change()

    def _on_move_down(self) -> None:
        if not self._current_entry_id:
            return
        if self._manager.move_down(self._current_entry_id):
            self._refresh_after_change()

    # ── Event Inspection & Deletion ────────────────────────────────────

    def _on_inspect_event(self) -> None:
        if not self._current_entry_id:
            return
        cb_entry = self._manager.get_entry(self._current_entry_id)
        if not cb_entry:
            return
        log_entry = cb_entry.to_log_entry()
        dlg = EventDetailDialog(log_entry, parent=self)
        dlg.exec()

    def _on_delete_entry(self) -> None:
        if not self._current_entry_id:
            return
        ans = QMessageBox.question(
            self,
            "Remove Casebook Entry",
            "Remove this evidentiary record from the investigation casebook?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._manager.remove_entry(self._current_entry_id)
            self._refresh_after_change()

    def _on_clear_all(self) -> None:
        if not self._manager.get_entries():
            return
        ans = QMessageBox.warning(
            self,
            "Clear Casebook",
            "Are you sure you want to clear all records from this investigation casebook?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._manager.clear_all()
            self._refresh_after_change()

    # ── Exporting Case Brief ───────────────────────────────────────────

    def _copy_markdown_to_clipboard(self) -> None:
        md = self._manager.export_brief_markdown()
        QApplication.clipboard().setText(md)
        QMessageBox.information(
            self,
            "Case Brief Copied",
            "Formatted Markdown Case Brief copied to the clipboard!\nReady to paste into GitHub, Jira, or email.",
        )

    def _copy_html_to_clipboard(self) -> None:
        html_text = self._manager.export_brief_html()
        QApplication.clipboard().setText(html_text)
        QMessageBox.information(
            self,
            "HTML Brief Copied",
            "Styled HTML Case Brief copied to the clipboard!",
        )

    def _export_markdown_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Case Brief Markdown",
            f"Case_Brief_{os.environ.get('COMPUTERNAME', 'Investigation')}.md",
            "Markdown Files (*.md);;All Files (*)",
        )
        if path:
            if self._manager.export_brief_to_file(path, format_type="md"):
                QMessageBox.information(self, "Export Complete", f"Case Brief exported to:\n{path}")
            else:
                QMessageBox.critical(self, "Export Failed", f"Could not write file to:\n{path}")

    def _export_html_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Case Brief HTML Report",
            f"Case_Brief_{os.environ.get('COMPUTERNAME', 'Investigation')}.html",
            "HTML Files (*.html *.htm);;All Files (*)",
        )
        if path:
            if self._manager.export_brief_to_file(path, format_type="html"):
                QMessageBox.information(self, "Export Complete", f"HTML Case Brief exported to:\n{path}")
            else:
                QMessageBox.critical(self, "Export Failed", f"Could not write file to:\n{path}")
