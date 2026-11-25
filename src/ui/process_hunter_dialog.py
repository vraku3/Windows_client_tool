from __future__ import annotations

import logging
import os
import subprocess
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)
from core.table_ui import set_role
from modules.process import process_tree

logger = logging.getLogger(__name__)


class ProcessHunterDialog(QDialog):
    """Live Process Tree & Threat Anomaly Hunter Dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live Process Tree & Anomaly Hunter")
        self.setMinimumSize(920, 580)
        self.setObjectName("processHunterDialog")

        self._anomalous_nodes: List[process_tree.ProcessNode] = []
        self._roots: List[process_tree.ProcessNode] = []

        self._setup_ui()
        self._refresh_processes()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Header Description
        header = QLabel(
            "Monitors active process hierarchy and inspects Parent-Child anomalies (e.g. Office or Browser "
            "spawning shells, processes running from Temp, masquerading System32 binaries, or encoded execution)."
        )
        header.setWordWrap(True)
        set_role(header, "muted")
        layout.addWidget(header)

        # Summary Bar
        summary_row = QHBoxLayout()
        self._lbl_total = QLabel("Total Processes: 0")
        set_role(self._lbl_total, "statusInfo")
        self._lbl_flagged = QLabel("Anomalies Flagged: 0")
        set_role(self._lbl_flagged, "statusWarning")

        btn_refresh = QPushButton("🔄 Refresh Scan")
        btn_refresh.clicked.connect(self._refresh_processes)

        summary_row.addWidget(self._lbl_total)
        summary_row.addWidget(self._lbl_flagged)
        summary_row.addStretch(1)
        summary_row.addWidget(btn_refresh)
        layout.addLayout(summary_row)

        # Anomalies Table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "PID", "Risk", "Process Name", "Parent Process", "Anomalies / MITRE Heuristic", "Executable Path"
        ])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        # Action Buttons
        act_row = QHBoxLayout()
        btn_kill = QPushButton("🛑 Terminate Process Tree")
        btn_kill.clicked.connect(self._terminate_selected)
        btn_open = QPushButton("📂 Open in Explorer")
        btn_open.clicked.connect(self._open_in_explorer)
        btn_copy = QPushButton("📋 Copy Anomaly Summary")
        btn_copy.clicked.connect(self._copy_summary)

        act_row.addWidget(btn_kill)
        act_row.addWidget(btn_open)
        act_row.addWidget(btn_copy)
        act_row.addStretch(1)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        act_row.addWidget(btn_close)

        layout.addLayout(act_row)

    def _refresh_processes(self) -> None:
        try:
            roots, anomalous = process_tree.build_process_tree()
            self._roots = roots
            self._anomalous_nodes = anomalous

            # Count total
            total_count = sum(self._count_tree(r) for r in roots)
            self._lbl_total.setText(f"Total Running Processes: {total_count}")
            self._lbl_flagged.setText(f"Anomalies Flagged: {len(anomalous)}")

            if len(anomalous) > 0:
                set_role(self._lbl_flagged, "statusError" if any(a.risk_level == "CRITICAL" for a in anomalous) else "statusWarning")
            else:
                set_role(self._lbl_flagged, "statusSuccess")

            self._populate_table()
        except Exception as e:
            logger.exception("Error scanning process anomalies: %s", e)
            QMessageBox.warning(self, "Process Scan Error", str(e))

    def _count_tree(self, node: process_tree.ProcessNode) -> int:
        return 1 + sum(self._count_tree(c) for c in node.children)

    def _populate_table(self) -> None:
        self._table.setRowCount(0)
        for row, node in enumerate(self._anomalous_nodes):
            self._table.insertRow(row)

            item_pid = QTableWidgetItem(str(node.pid))
            item_pid.setData(Qt.ItemDataRole.UserRole, node)

            item_risk = QTableWidgetItem(node.risk_level)
            item_name = QTableWidgetItem(node.name)
            item_parent = QTableWidgetItem(node.parent_name or f"PID {node.ppid}")
            item_anom = QTableWidgetItem("; ".join(node.anomalies))
            item_path = QTableWidgetItem(node.exe or "(Access Denied)")

            self._table.setItem(row, 0, item_pid)
            self._table.setItem(row, 1, item_risk)
            self._table.setItem(row, 2, item_name)
            self._table.setItem(row, 3, item_parent)
            self._table.setItem(row, 4, item_anom)
            self._table.setItem(row, 5, item_path)

    def _get_selected_node(self) -> Optional[process_tree.ProcessNode]:
        row = self._table.currentRow()
        if row >= 0:
            item = self._table.item(row, 0)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _terminate_selected(self) -> None:
        node = self._get_selected_node()
        if not node:
            QMessageBox.information(self, "Select Process", "Please select a process row from the table first.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Process Termination",
            f"Are you sure you want to terminate process '{node.name}' (PID: {node.pid}) and its entire child process tree?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                res = subprocess.run(
                    ["taskkill.exe", "/PID", str(node.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=0x08000000,
                )
                if res.returncode == 0:
                    QMessageBox.information(self, "Terminated", f"Process {node.name} (PID: {node.pid}) terminated.")
                    self._refresh_processes()
                else:
                    QMessageBox.warning(self, "Kill Failed", f"Could not terminate PID {node.pid}: {res.stderr.strip() or res.stdout.strip()}")
            except Exception as e:
                logger.exception("Error killing process: %s", e)
                QMessageBox.warning(self, "Kill Error", str(e))

    def _open_in_explorer(self) -> None:
        node = self._get_selected_node()
        if node and node.exe and os.path.exists(node.exe):
            try:
                subprocess.Popen(["explorer.exe", "/select,", node.exe])
            except Exception as e:
                logger.warning("Could not open in Explorer: %s", e)
        else:
            QMessageBox.information(self, "Location Not Found", "Executable path is unavailable or inaccessible.")

    def _copy_summary(self) -> None:
        lines = ["# Process Anomaly Hunter Findings:"]
        for node in self._anomalous_nodes:
            lines.append(f"- **{node.name}** (PID {node.pid}) [Risk: {node.risk_level}]: {'; '.join(node.anomalies)}")
            if node.exe:
                lines.append(f"  Path: `{node.exe}`")
        text = "\n".join(lines)

        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copied", "Anomaly findings copied to clipboard!")
