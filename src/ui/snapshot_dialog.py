from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout,
    QWidget,
)
from core import system_snapshot
from core.table_ui import set_role

logger = logging.getLogger(__name__)


class SnapshotDialog(QDialog):
    """System Snapshot & Baseline Differential ("Before & After") Dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("System Baseline Snapshot & Differential Analysis")
        self.setMinimumSize(860, 560)
        self.setObjectName("snapshotDialog")

        self._snapshots: List[system_snapshot.SystemSnapshot] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Header description
        header = QLabel(
            "Capture a machine state baseline before software installations, tweaks, or updates, "
            "and compare differentials ('Before & After') to pinpoint added services, autostarts, and tasks."
        )
        header.setWordWrap(True)
        set_role(header, "muted")
        layout.addWidget(header)

        # Action Buttons Top Bar
        top_bar = QHBoxLayout()
        self._lbl_label = QLabel("Snapshot Label:")
        self._txt_label = QLineEdit()
        self._txt_label.setPlaceholderText("e.g. Pre-Game Install Baseline")
        self._btn_take = QPushButton("📸 Take Snapshot Now")
        self._btn_take.clicked.connect(self._take_snapshot)

        self._btn_load = QPushButton("📁 Load…")
        self._btn_load.clicked.connect(self._load_from_file)
        self._btn_save = QPushButton("💾 Save…")
        self._btn_save.clicked.connect(self._save_to_file)

        top_bar.addWidget(self._lbl_label)
        top_bar.addWidget(self._txt_label)
        top_bar.addWidget(self._btn_take)
        top_bar.addWidget(self._btn_load)
        top_bar.addWidget(self._btn_save)
        layout.addLayout(top_bar)

        # Splitter: Left = Snapshots List, Right = Comparison Results
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left List
        left_widget = QWidget(splitter)
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)

        left_lay.addWidget(QLabel("<b>Captured Snapshots:</b>"))
        self._list_snapshots = QListWidget()
        self._list_snapshots.itemSelectionChanged.connect(self._on_selection_changed)
        left_lay.addWidget(self._list_snapshots)

        btn_compare = QPushButton("🔍 Compare Selected vs Previous")
        btn_compare.clicked.connect(self._compare_snapshots)
        left_lay.addWidget(btn_compare)

        splitter.addWidget(left_widget)

        # Right Panel
        right_widget = QWidget(splitter)
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)

        right_lay.addWidget(QLabel("<b>Differential Analysis Report:</b>"))
        self._txt_diff = QPlainTextEdit()
        self._txt_diff.setReadOnly(True)
        set_role(self._txt_diff, "iseConsole")
        self._txt_diff.setPlaceholderText(
            "Select a snapshot and click 'Compare Selected vs Previous' to inspect changes."
        )
        right_lay.addWidget(self._txt_diff)

        btn_copy = QPushButton("📋 Copy Markdown Report")
        btn_copy.clicked.connect(self._copy_markdown)
        right_lay.addWidget(btn_copy)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        # Bottom Close Button
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _take_snapshot(self) -> None:
        label = self._txt_label.text().strip() or "Baseline"
        self._btn_take.setEnabled(False)
        self._btn_take.setText("Capturing…")
        try:
            snap = system_snapshot.take_snapshot(label)
            self._snapshots.append(snap)
            item = QListWidgetItem(f"📸 {snap.label} ({snap.timestamp[:19]})")
            item.setData(Qt.ItemDataRole.UserRole, snap)
            self._list_snapshots.addItem(item)
            self._list_snapshots.setCurrentItem(item)
            self._txt_label.clear()
            self._txt_diff.setPlainText(
                f"Snapshot '{snap.label}' captured successfully!\n"
                f"- Services indexed: {len(snap.services)}\n"
                f"- Autostarts indexed: {len(snap.autostarts)}\n"
                f"- Scheduled Tasks indexed: {len(snap.tasks)}\n"
                f"- Driver packages: {len(snap.drivers)}"
            )
        except Exception as e:
            logger.exception("Error taking snapshot: %s", e)
            QMessageBox.warning(self, "Snapshot Error", f"Could not capture system snapshot: {e}")
        finally:
            self._btn_take.setEnabled(True)
            self._btn_take.setText("📸 Take Snapshot Now")

    def _compare_snapshots(self) -> None:
        if len(self._snapshots) < 2:
            QMessageBox.information(
                self,
                "Need 2 Snapshots",
                "Please capture or load at least 2 snapshots to perform a differential comparison."
            )
            return

        curr_row = self._list_snapshots.currentRow()
        if curr_row <= 0:
            idx_b = len(self._snapshots) - 1
            idx_a = idx_b - 1
        else:
            idx_b = curr_row
            idx_a = curr_row - 1

        snap_a = self._snapshots[idx_a]
        snap_b = self._snapshots[idx_b]

        diff = system_snapshot.compare_snapshots(snap_a, snap_b)
        report_md = system_snapshot.format_diff_markdown(diff)
        self._txt_diff.setPlainText(report_md)

    def _copy_markdown(self) -> None:
        text = self._txt_diff.toPlainText().strip()
        if text:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            QMessageBox.information(self, "Copied", "Differential report copied to clipboard!")

    def _on_selection_changed(self) -> None:
        item = self._list_snapshots.currentItem()
        if item:
            snap: system_snapshot.SystemSnapshot = item.data(Qt.ItemDataRole.UserRole)
            if snap:
                self._txt_diff.setPlainText(
                    f"Selected Snapshot: {snap.label}\n"
                    f"Timestamp: {snap.timestamp}\n"
                    f"OS Build: {snap.os_build}\n\n"
                    f"Indexed Items:\n"
                    f"- Services: {len(snap.services)}\n"
                    f"- Autostart Entries: {len(snap.autostarts)}\n"
                    f"- Scheduled Tasks: {len(snap.tasks)}\n"
                    f"- Driver Packages: {len(snap.drivers)}\n\n"
                    f"Select another snapshot and click 'Compare Selected vs Previous' to view changes."
                )

    def _save_to_file(self) -> None:
        item = self._list_snapshots.currentItem()
        if not item:
            return
        snap: system_snapshot.SystemSnapshot = item.data(Qt.ItemDataRole.UserRole)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save System Snapshot", f"{snap.label}.json", "JSON Files (*.json)"
        )
        if path:
            try:
                from dataclasses import asdict
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(asdict(snap), f, indent=2)
                QMessageBox.information(self, "Saved", f"Snapshot saved to {path}")
            except Exception as e:
                logger.exception("Error saving snapshot: %s", e)
                QMessageBox.warning(self, "Save Error", str(e))

    def _load_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open System Snapshot", "", "JSON Files (*.json)"
        )
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                snap = system_snapshot.SystemSnapshot(
                    label=data.get("label", "Loaded"),
                    timestamp=data.get("timestamp", ""),
                    os_build=data.get("os_build", ""),
                    computer_name=data.get("computer_name", ""),
                    services=data.get("services", {}),
                    autostarts=data.get("autostarts", {}),
                    tasks=data.get("tasks", {}),
                    drivers=data.get("drivers", []),
                )
                self._snapshots.append(snap)
                item = QListWidgetItem(f"📁 {snap.label} ({snap.timestamp[:19]})")
                item.setData(Qt.ItemDataRole.UserRole, snap)
                self._list_snapshots.addItem(item)
                self._list_snapshots.setCurrentItem(item)
            except Exception as e:
                logger.exception("Error loading snapshot: %s", e)
                QMessageBox.warning(self, "Load Error", str(e))
