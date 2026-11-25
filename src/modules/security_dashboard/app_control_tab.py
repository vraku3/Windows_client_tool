"""Smart App Control (SAC) and WDAC Inspector Tab for Security Dashboard.

Displays:
1. Smart App Control operational state & PowerShell language mode.
2. Active Code Integrity policies (.cip).
3. Cloud trust lookup for any file via Defender MpCmdRun -TrustCheck.
4. Recent Code Integrity block events (Event IDs 3077 and 3118) with details.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.sac_inspector import CIBlockEvent, check_file_trust, get_recent_ci_blocks, get_sac_state
from core.semantic_colors import semantic
from core.table_ui import center_header, set_role
from core.widget_life import widget_is_valid
from core.worker import Worker, retire

logger = logging.getLogger(__name__)


class AppControlTab(QWidget):
    """Smart App Control & Windows Defender Application Control Inspector UI."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._workers: List[Worker] = []
        self._trust_workers: List[Worker] = []
        self._blocks: List[CIBlockEvent] = []
        self.loaded = False

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 1. Status Overview Card
        status_frame = QFrame(self)
        status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        status_lay = QGridLayout(status_frame)
        status_lay.setContentsMargins(12, 10, 12, 10)
        status_lay.setSpacing(10)

        # SAC Status
        sac_title = QLabel("Smart App Control (SAC):", status_frame)
        font = sac_title.font()
        font.setBold(True)
        sac_title.setFont(font)
        self._sac_badge = QLabel("Reading...", status_frame)
        set_role(self._sac_badge, "statusInfo")
        status_lay.addWidget(sac_title, 0, 0)
        status_lay.addWidget(self._sac_badge, 0, 1)

        # PS Language Mode
        ps_title = QLabel("PowerShell Language Mode:", status_frame)
        ps_title.setFont(font)
        self._ps_badge = QLabel("Reading...", status_frame)
        set_role(self._ps_badge, "statusInfo")
        status_lay.addWidget(ps_title, 0, 2)
        status_lay.addWidget(self._ps_badge, 0, 3)

        # Active Policies
        pol_title = QLabel("Active WDAC Policies:", status_frame)
        pol_title.setFont(font)
        self._pol_label = QLabel("Reading...", status_frame)
        status_lay.addWidget(pol_title, 1, 0)
        status_lay.addWidget(self._pol_label, 1, 1, 1, 3)

        layout.addWidget(status_frame)

        # 2. File Cloud Trust Inspector Card
        trust_frame = QFrame(self)
        trust_frame.setFrameShape(QFrame.Shape.StyledPanel)
        trust_lay = QVBoxLayout(trust_frame)
        trust_lay.setContentsMargins(12, 10, 12, 10)
        trust_lay.setSpacing(6)

        trust_header = QLabel("Check File Cloud Trust (Defender & SAC)", trust_frame)
        trust_header.setFont(font)
        trust_lay.addWidget(trust_header)

        picker_row = QHBoxLayout()
        self._file_edit = QLineEdit(trust_frame)
        self._file_edit.setPlaceholderText("Enter or browse executable file path to inspect...")
        browse_btn = QPushButton("Browse...", trust_frame)
        browse_btn.clicked.connect(self._browse_file)
        self._check_btn = QPushButton("Check Cloud Trust", trust_frame)
        self._check_btn.clicked.connect(self._check_trust)

        picker_row.addWidget(self._file_edit, 1)
        picker_row.addWidget(browse_btn)
        picker_row.addWidget(self._check_btn)
        trust_lay.addLayout(picker_row)

        self._trust_result_lbl = QLabel("Select an executable or library to evaluate its reputation in the Microsoft Cloud.", trust_frame)
        self._trust_result_lbl.setWordWrap(True)
        trust_lay.addWidget(self._trust_result_lbl)

        layout.addWidget(trust_frame)

        # 3. Code Integrity Blocks Section
        table_header_row = QHBoxLayout()
        blocks_title = QLabel("Recent Code Integrity & SAC Block Events", self)
        blocks_title.setFont(font)
        table_header_row.addWidget(blocks_title)

        refresh_btn = QPushButton("Refresh Events", self)
        refresh_btn.clicked.connect(self.load)
        table_header_row.addWidget(refresh_btn)

        copy_btn = QPushButton("Copy Selected Details", self)
        copy_btn.clicked.connect(self._copy_selected_detail)
        table_header_row.addWidget(copy_btn)

        table_header_row.addStretch(1)
        layout.addLayout(table_header_row)

        self._progress = QProgressBar(self)
        self._progress.setFixedHeight(3)
        self._progress.setRange(0, 0)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Splitter: Table on top, Event Details below
        splitter = QSplitter(Qt.Orientation.Vertical, self)

        self._table = QTableWidget(0, 5, self)
        self._table.setHorizontalHeaderLabels(["Timestamp", "Event ID", "Calling Process", "Target File", "Policy"])
        for i in range(5):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 160)
        self._table.setColumnWidth(1, 80)
        self._table.setColumnWidth(2, 160)
        self._table.setColumnWidth(3, 340)
        self._table.setColumnWidth(4, 180)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.currentItemChanged.connect(lambda *_: self._show_selected_detail())
        splitter.addWidget(self._table)

        self._detail_view = QTextBrowser(self)
        self._detail_view.setPlaceholderText("Select a blocked event to see its hash, policy GUID, and explanation.")
        splitter.addWidget(self._detail_view)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    def load(self) -> None:
        """Fetch SAC state and recent block events asynchronously."""
        if not widget_is_valid(self):
            return

        self._progress.show()

        def fetch_work(worker: Worker):
            if worker.is_cancelled:
                return None
            state = get_sac_state()
            if worker.is_cancelled:
                return None
            events = get_recent_ci_blocks(max_events=60)
            return {"state": state, "events": events}

        w = Worker(fetch_work)
        w.signals.result.connect(self._on_loaded)
        w.signals.error.connect(self._on_load_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_loaded(self, result: Optional[dict]) -> None:
        if not widget_is_valid(self):
            return
        self._progress.hide()
        if not result:
            return

        state = result.get("state", {})
        self._blocks = result.get("events", [])
        self.loaded = True

        # SAC Badge
        state_label = state.get("state_label", "Unknown")
        self._sac_badge.setText(state_label)
        if state.get("is_enforced"):
            set_role(self._sac_badge, "statusSuccess")
        elif state.get("is_eval"):
            set_role(self._sac_badge, "statusWarning")
        else:
            set_role(self._sac_badge, "statusInfo")

        # PS Badge
        ps_mode = state.get("ps_language_mode", "Unknown")
        self._ps_badge.setText(ps_mode)
        if ps_mode == "ConstrainedLanguage":
            set_role(self._ps_badge, "statusWarning")
        elif ps_mode == "FullLanguage":
            set_role(self._ps_badge, "statusSuccess")
        else:
            set_role(self._ps_badge, "statusInfo")

        # Policies
        policies = state.get("active_policies", [])
        has_sac = state.get("has_sac_policy", False)
        sac_text = " (includes SAC policy)" if has_sac else ""
        if policies:
            self._pol_label.setText(f"{len(policies)} active policy files{sac_text}: {', '.join(policies[:4])}{'...' if len(policies) > 4 else ''}")
        else:
            self._pol_label.setText("No active CIP policies found.")

        # Populate Table
        self._table.setRowCount(len(self._blocks))
        for row, evt in enumerate(self._blocks):
            item_time = QTableWidgetItem(evt.timestamp.replace("T", " ")[:19])
            item_eid = QTableWidgetItem(str(evt.event_id))
            item_proc = QTableWidgetItem(evt.process_name)
            item_file = QTableWidgetItem(evt.target_file)
            item_pol = QTableWidgetItem(evt.policy_name)

            if evt.is_sac:
                item_pol.setForeground(QColor(semantic("warning")))

            self._table.setItem(row, 0, item_time)
            self._table.setItem(row, 1, item_eid)
            self._table.setItem(row, 2, item_proc)
            self._table.setItem(row, 3, item_file)
            self._table.setItem(row, 4, item_pol)

        if self._blocks:
            self._table.selectRow(0)
            self._show_selected_detail()
        else:
            self._detail_view.setPlainText("No recent Code Integrity or Smart App Control block events found in the event log.")

    def _on_load_error(self, err: str) -> None:
        if not widget_is_valid(self):
            return
        self._progress.hide()
        self._detail_view.setPlainText(f"Error reading Code Integrity events: {err}")

    def _show_selected_detail(self) -> None:
        if not widget_is_valid(self):
            return
        row = self._table.currentRow()
        if row < 0 or row >= len(self._blocks):
            return

        evt = self._blocks[row]
        sac_desc = (
            "Blocked by Smart App Control (SAC) because the executable or script was unsigned "
            "and lacked cloud reputation in the Intelligent Security Graph."
            if evt.is_sac
            else "Blocked by Windows Defender Application Control (WDAC) policy."
        )

        detail_text = (
            f"Event ID: {evt.event_id}\n"
            f"Timestamp: {evt.timestamp}\n"
            f"Status Code: {evt.status_code}\n"
            f"Policy Name: {evt.policy_name} ({evt.policy_guid})\n"
            f"Calling Process: {evt.process_name}\n"
            f"Target File: {evt.target_file}\n"
            f"SHA-256 Hash: {evt.sha256 or 'N/A'}\n\n"
            f"Explanation:\n{sac_desc}"
        )
        self._detail_view.setPlainText(detail_text)

    def _copy_selected_detail(self) -> None:
        text = self._detail_view.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Executable or Library to Check Cloud Trust",
            "",
            "Executables & Drivers (*.exe *.dll *.sys *.ps1);;All Files (*.*)",
        )
        if path:
            self._file_edit.setText(path)

    def _check_trust(self) -> None:
        path = self._file_edit.text().strip()
        if not path:
            self._trust_result_lbl.setText("Please specify a valid file path.")
            set_role(self._trust_result_lbl, "statusWarning")
            return

        self._check_btn.setEnabled(False)
        self._trust_result_lbl.setText(f"Querying Microsoft Cloud trust via Defender for '{path}'...")
        set_role(self._trust_result_lbl, "statusInfo")

        def trust_work(worker: Worker):
            if worker.is_cancelled:
                return False, "Cancelled"
            return check_file_trust(path)

        w = Worker(trust_work)
        w.signals.result.connect(self._on_trust_result)
        w.signals.error.connect(lambda err: self._on_trust_result((False, f"Trust check error: {err}")))
        self._trust_workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_trust_result(self, result: tuple) -> None:
        if not widget_is_valid(self):
            return
        self._check_btn.setEnabled(True)
        is_trusted, message = result
        self._trust_result_lbl.setText(message)
        if is_trusted:
            set_role(self._trust_result_lbl, "statusSuccess")
        else:
            set_role(self._trust_result_lbl, "statusError")

    def cancel(self) -> None:
        retire(self._workers)
        retire(self._trust_workers)
        if widget_is_valid(self._progress):
            self._progress.hide()

    def _cancel_all(self) -> None:
        self.cancel()
