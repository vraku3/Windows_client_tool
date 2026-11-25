"""Incident Correlation Analysis Dialog.

Presents multi-log correlation findings around an incident window:
- Root cause candidates with severity, confidence, and suggested remediation.
- Causal cascades across Windows subsystems.
- Precursor event sequence leading into the failure.
- Event velocity spikes (storm rate spikes).
"""
from __future__ import annotations

import html
import logging
from typing import Optional

from PyQt6.QtCore import Qt, QKeyCombination
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core.incident_correlation import IncidentCorrelation
from core.table_ui import set_role
from core.types import LogEntry

logger = logging.getLogger(__name__)


class IncidentCorrelationDialog(QDialog):
    """Dialog showing correlated diagnostic events and root-cause synthesis."""

    def __init__(self, correlation: IncidentCorrelation, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.correlation = correlation
        self.setWindowTitle(
            f"Cross-Log Incident Correlation — {correlation.anchor_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.resize(920, 680)
        self.setMinimumSize(640, 480)

        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._build_header(root)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._build_scroll_area(splitter)
        self._build_table(splitter)
        splitter.setSizes([320, 260])
        root.addWidget(splitter, 1)

        self._build_footer(root)

    def _build_header(self, root: QVBoxLayout) -> None:
        header_card = QFrame()
        header_card.setObjectName("card")
        hl = QVBoxLayout(header_card)
        hl.setContentsMargins(12, 10, 12, 10)
        hl.setSpacing(6)

        ts_str = self.correlation.anchor_time.strftime("%Y-%m-%d %H:%M:%S")
        title_lbl = QLabel(f"<b style='font-size:15px;'>⏱️ Incident Window Analysis: {ts_str} (±{self.correlation.window_minutes} min)</b>")
        hl.addWidget(title_lbl)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(16)

        lbl_tot = QLabel(f"<b>Total Events:</b> {self.correlation.total_events}")
        lbl_err = QLabel(f"<b>Errors:</b> {self.correlation.error_count}")
        set_role(lbl_err, "statusError")
        lbl_warn = QLabel(f"<b>Warnings:</b> {self.correlation.warning_count}")
        set_role(lbl_warn, "statusWarning")
        lbl_spikes = QLabel(f"<b>Velocity Spikes:</b> {len(self.correlation.spikes)}")

        metrics_row.addWidget(lbl_tot)
        metrics_row.addWidget(lbl_err)
        metrics_row.addWidget(lbl_warn)
        metrics_row.addWidget(lbl_spikes)
        metrics_row.addStretch()
        hl.addLayout(metrics_row)

        summary_lbl = QLabel(f"<i>{html.escape(self.correlation.summary_text)}</i>")
        summary_lbl.setWordWrap(True)
        set_role(summary_lbl, "muted")
        hl.addWidget(summary_lbl)

        root.addWidget(header_card)

    def _build_scroll_area(self, splitter: QSplitter) -> None:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        sl = QVBoxLayout(scroll_content)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(8)

        sl.addWidget(QLabel("<b style='font-size:13px;'>🎯 Hypothesized Root Causes:</b>"))
        if self.correlation.root_causes:
            for rc in self.correlation.root_causes:
                self._append_root_cause_card(sl, rc)
        else:
            no_rc = QLabel("No unambiguous root cause detected. Inspect the precursor events below.")
            set_role(no_rc, "muted")
            sl.addWidget(no_rc)

        if self.correlation.cascades:
            sl.addWidget(QLabel("<b style='font-size:13px;'>🔗 Detected Multi-System Cascades:</b>"))
            for cascade in self.correlation.cascades:
                self._append_cascade_card(sl, cascade)

        if self.correlation.precursors:
            sl.addWidget(QLabel(f"<b style='font-size:13px;'>⚡ Precursor Faults ({len(self.correlation.precursors)} in 120s prior):</b>"))
            for p in self.correlation.precursors[:6]:
                p_text = f"• [{p.timestamp.strftime('%H:%M:%S')}] <b>{html.escape(p.source)}</b>: {html.escape(p.message[:140])}"
                p_lbl = QLabel(p_text)
                p_lbl.setWordWrap(True)
                set_role(p_lbl, "statusError" if "err" in (p.level or "").lower() else "statusWarning")
                sl.addWidget(p_lbl)

        sl.addStretch()
        scroll_area.setWidget(scroll_content)
        splitter.addWidget(scroll_area)

    def _append_root_cause_card(self, sl: QVBoxLayout, rc) -> None:
        rc_card = QFrame()
        rc_card.setObjectName("card")
        rcl = QVBoxLayout(rc_card)
        rcl.setContentsMargins(10, 8, 10, 8)
        rcl.setSpacing(4)

        top_row = QHBoxLayout()
        rc_title = QLabel(f"<b>{html.escape(rc.title)}</b>")
        top_row.addWidget(rc_title)

        conf_badge = QLabel(f"Confidence: {rc.confidence}")
        set_role(conf_badge, "statusSuccess" if rc.confidence == "High" else "statusWarning")
        top_row.addWidget(conf_badge)
        top_row.addStretch()
        rcl.addLayout(top_row)

        expl = QLabel(f"<b>Finding:</b> {html.escape(rc.explanation)}")
        expl.setWordWrap(True)
        rcl.addWidget(expl)

        if rc.suggested_action:
            act_lbl = QLabel(f"<b>Recommended Action:</b> {html.escape(rc.suggested_action)}")
            act_lbl.setWordWrap(True)
            set_role(act_lbl, "statusInfo")
            rcl.addWidget(act_lbl)

        sl.addWidget(rc_card)

    def _append_cascade_card(self, sl: QVBoxLayout, cascade) -> None:
        c_card = QFrame()
        c_card.setObjectName("card")
        cl = QVBoxLayout(c_card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(4)

        c_header = QLabel(f"<b>{html.escape(cascade.name)}</b> ({cascade.confidence} Confidence)")
        set_role(c_header, "statusWarning")
        cl.addWidget(c_header)

        c_desc = QLabel(html.escape(cascade.description))
        c_desc.setWordWrap(True)
        cl.addWidget(c_desc)

        sl.addWidget(c_card)

    def _build_table(self, splitter: QSplitter) -> None:
        table_container = QWidget()
        tl = QVBoxLayout(table_container)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        tl.addWidget(QLabel("<b style='font-size:13px;'>📋 Correlated Event Sequence:</b>"))

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Time", "Source", "Level", "Message"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)

        self._populate_table()
        tl.addWidget(self._table)
        splitter.addWidget(table_container)

    def _build_footer(self, root: QVBoxLayout) -> None:
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        copy_btn = QPushButton("📋 Copy Markdown Report")
        copy_btn.setToolTip("Copy full incident triage findings and event chronology to clipboard.")
        copy_btn.clicked.connect(self._copy_markdown)
        btn_row.addWidget(copy_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

        QShortcut(QKeySequence("Escape"), self).activated.connect(self.accept)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.accept)

    def _populate_table(self) -> None:
        entries = self.correlation.events
        self._table.setRowCount(len(entries))
        for r, e in enumerate(entries):
            ts = e.timestamp.strftime("%H:%M:%S") if e.timestamp else "—"
            item_ts = QTableWidgetItem(ts)
            item_src = QTableWidgetItem(e.source or "—")
            item_lvl = QTableWidgetItem(e.level or "Info")
            item_msg = QTableWidgetItem((e.message or "")[:300])

            lvl = (e.level or "").lower()
            if "err" in lvl or "crit" in lvl:
                set_role(item_lvl, "statusError")
            elif "warn" in lvl:
                set_role(item_lvl, "statusWarning")

            self._table.setItem(r, 0, item_ts)
            self._table.setItem(r, 1, item_src)
            self._table.setItem(r, 2, item_lvl)
            self._table.setItem(r, 3, item_msg)

    def _copy_markdown(self) -> None:
        md = [
            f"# Incident Triage Report: {self.correlation.anchor_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Window**: ±{self.correlation.window_minutes} minutes",
            f"- **Total Events**: {self.correlation.total_events} ({self.correlation.error_count} errors, {self.correlation.warning_count} warnings)",
            f"- **Summary**: {self.correlation.summary_text}\n",
            "## Root Cause Candidates",
        ]
        for rc in self.correlation.root_causes:
            md.append(f"### {rc.title} ({rc.confidence} Confidence)")
            md.append(f"- **Finding**: {rc.explanation}")
            if rc.suggested_action:
                md.append(f"- **Action**: {rc.suggested_action}")
            md.append("")

        if self.correlation.cascades:
            md.append("## Detected Cascades")
            for c in self.correlation.cascades:
                md.append(f"- **{c.name}**: {c.description}")
            md.append("")

        if self.correlation.precursors:
            md.append("## Precursor Faults")
            for p in self.correlation.precursors:
                md.append(f"- [{p.timestamp.strftime('%H:%M:%S')}] {p.source} ({p.level}): {p.message[:160]}")
            md.append("")

        md.append("## Chronological Events")
        md.append("| Time | Source | Level | Message |")
        md.append("| --- | --- | --- | --- |")
        for e in self.correlation.events[:150]:
            ts = e.timestamp.strftime("%H:%M:%S") if e.timestamp else "—"
            clean_msg = " ".join((e.message or "").split())[:120].replace("|", "\\|")
            md.append(f"| {ts} | {e.source} | {e.level} | {clean_msg} |")

        text = "\n".join(md)
        QApplication.clipboard().setText(text)
