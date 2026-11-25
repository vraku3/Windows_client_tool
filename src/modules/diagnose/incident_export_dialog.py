"""Dialog for configuring and exporting a diagnostic incident bundle."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)

from core.diagnostic_packager import create_incident_package
from core.semantic_colors import chrome, semantic
from core.table_ui import set_role

logger = logging.getLogger(__name__)


class IncidentExportDialog(QDialog):
    """Dialog allowing the user to configure scope, notes, and destination for diagnostic bundles."""

    def __init__(
        self,
        children: Sequence[Any],
        current_index: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.children = list(children)
        self.current_index = max(0, min(current_index, len(self.children) - 1)) if self.children else 0
        self.last_result: Optional[Dict[str, Any]] = None

        self.setWindowTitle("Export Diagnostic Incident Bundle")
        self.resize(580, 480)
        self.setMinimumSize(480, 400)

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── Header ───────────────────────────────────────────────────────
        header_card = QFrame()
        header_card.setObjectName("card")
        hl = QVBoxLayout(header_card)
        hl.setSpacing(4)
        hl.setContentsMargins(12, 10, 12, 10)

        title_lbl = QLabel("<b style='font-size:15px;'>📦 Diagnostic Incident Packaging</b>")
        desc_lbl = QLabel(
            "Package diagnostic logs, executive HTML report, PII-sanitized CSV, "
            "and system telemetry into an archive ready for analysis or vendor support."
        )
        set_role(desc_lbl, "muted")
        desc_lbl.setWordWrap(True)
        hl.addWidget(title_lbl)
        hl.addWidget(desc_lbl)
        layout.addWidget(header_card)

        # ── Scope selection ──────────────────────────────────────────────
        scope_card = QFrame()
        scope_card.setObjectName("card")
        sl = QVBoxLayout(scope_card)
        sl.setSpacing(8)
        sl.setContentsMargins(12, 10, 12, 10)

        sl.addWidget(QLabel("<b>Log Scope:</b>"))
        self._btn_group = QButtonGroup(self)

        total_children = len(self.children)
        self._rb_all = QRadioButton(f"All Diagnostic Channels ({total_children} sources: Event Viewer, CBS, DISM, WU, etc.)")
        self._rb_all.setChecked(True)
        self._btn_group.addButton(self._rb_all)
        sl.addWidget(self._rb_all)

        active_name = self.children[self.current_index].name if self.children else "Active Tab"
        self._rb_active = QRadioButton(f"Active Tab Only ({active_name})")
        self._btn_group.addButton(self._rb_active)
        sl.addWidget(self._rb_active)

        layout.addWidget(scope_card)

        # ── Destination path ─────────────────────────────────────────────
        dest_card = QFrame()
        dest_card.setObjectName("card")
        dl = QVBoxLayout(dest_card)
        dl.setSpacing(6)
        dl.setContentsMargins(12, 10, 12, 10)

        dl.addWidget(QLabel("<b>Save Archive To:</b>"))
        dest_row = QHBoxLayout()
        dest_row.setSpacing(6)

        # Default path in user's Downloads or home folder
        now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_folder = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(default_folder):
            default_folder = os.path.expanduser("~")
        default_file = os.path.join(default_folder, f"Incident_Bundle_{now_tag}.zip")

        self._path_edit = QLineEdit(default_file)
        self._path_edit.setMinimumHeight(28)
        dest_row.addWidget(self._path_edit, 1)

        browse_btn = QPushButton("Browse…")
        browse_btn.setMinimumHeight(28)
        browse_btn.clicked.connect(self._on_browse)
        dest_row.addWidget(browse_btn)

        dl.addLayout(dest_row)
        layout.addWidget(dest_card)

        # ── Incident Notes ───────────────────────────────────────────────
        notes_card = QFrame()
        notes_card.setObjectName("card")
        nl = QVBoxLayout(notes_card)
        nl.setSpacing(6)
        nl.setContentsMargins(12, 10, 12, 10)

        nl.addWidget(QLabel("<b>Incident Notes & Symptoms (Optional):</b>"))
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText(
            "Describe the symptoms, when the issue occurred, or any context "
            "(e.g., 'System experienced micro-stutter and audio crackling under heavy load')."
        )
        self._notes_edit.setMaximumHeight(85)
        nl.addWidget(self._notes_edit)
        layout.addWidget(notes_card)

        # ── Action Buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(85)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._export_btn = QPushButton("📦 Export Bundle")
        self._export_btn.setMinimumWidth(130)
        self._export_btn.setDefault(True)
        self._export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(self._export_btn)

        layout.addLayout(btn_row)

    def _on_browse(self) -> None:
        cur_path = self._path_edit.text().strip()
        dir_name = os.path.dirname(cur_path) if cur_path else ""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Incident Bundle",
            cur_path or os.path.join(dir_name, "Incident_Bundle.zip"),
            "ZIP Archives (*.zip);;All Files (*.*)",
        )
        if file_path:
            self._path_edit.setText(file_path)

    def _collect_readers_data(self) -> Dict[str, list]:
        data: Dict[str, list] = {}
        if self._rb_active.isChecked() and self.children:
            child = self.children[self.current_index]
            pane = getattr(child, "_pane", None)
            entries = getattr(pane, "_source", None) or getattr(pane, "_entries", None) or []
            data[child.name] = list(entries)
            return data

        for child in self.children:
            pane = getattr(child, "_pane", None)
            entries = getattr(pane, "_source", None) or getattr(pane, "_entries", None) or []
            data[child.name] = list(entries)
        return data

    def _on_export(self) -> None:
        out_path = self._path_edit.text().strip()
        if not out_path:
            QMessageBox.warning(self, "Invalid Path", "Please specify an output ZIP file path.")
            return

        if not out_path.lower().endswith(".zip"):
            out_path += ".zip"

        readers_data = self._collect_readers_data()
        notes = self._notes_edit.toPlainText().strip()

        # Check total entries
        total_collected = sum(len(v) for v in readers_data.values())
        if total_collected == 0:
            proceed = QMessageBox.question(
                self,
                "No Entries Loaded",
                "None of the diagnostic tabs have loaded entries yet.\n\n"
                "Do you want to proceed creating the bundle with empty logs,\n"
                "or click Cancel and 'Refresh All' first?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        try:
            res = create_incident_package(out_path, readers_data, extra_notes=notes)
            self.last_result = res
            size_kb = max(1, res["zip_size_bytes"] // 1024)

            msg = (
                f"Diagnostic Incident Bundle exported successfully!\n\n"
                f"📁 File: {os.path.basename(out_path)}\n"
                f"📍 Path: {out_path}\n"
                f"📊 Events Analyzed: {res['total_events']}\n"
                f"🚨 Errors / Critical: {res['error_count']}\n"
                f"⚠️ Warnings: {res['warning_count']}\n"
                f"📦 Size: {size_kb} KB\n\n"
                f"The archive contains report.html, events.json, events_sanitized.csv, and system_summary.txt."
            )

            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Incident Bundle Created")
            box.setText(msg)
            open_folder_btn = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()

            if box.clickedButton() == open_folder_btn:
                try:
                    os.startfile(os.path.dirname(out_path))
                except Exception as ex:
                    logger.warning("Could not open folder: %s", ex)

            self.accept()
        except Exception as e:
            logger.error("Failed to create incident package: %s", e, exc_info=True)
            QMessageBox.critical(self, "Export Error", f"Failed to create incident bundle:\n{e}")
