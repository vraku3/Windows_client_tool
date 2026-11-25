"""Side-by-side visual difference comparison dialog for two log entries."""
from __future__ import annotations

import html
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from core.semantic_colors import chrome, semantic
from core.types import LogEntry


class EventDiffDialog(QDialog):
    """Side-by-side comparison between two log records."""

    def __init__(self, entry_a: LogEntry, entry_b: LogEntry, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._a = entry_a
        self._b = entry_b

        self.setWindowTitle("Event Comparison — Side-by-Side Diff")
        self.resize(920, 680)
        self.setMinimumSize(600, 400)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Header banner with Delta Time ───────────────────────────────
        header = QFrame()
        header.setObjectName("card")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 8, 12, 8)

        t_diff = abs((entry_b.timestamp - entry_a.timestamp).total_seconds())
        diff_str = (
            f"{int(t_diff)} seconds" if t_diff < 60
            else f"{int(t_diff // 60)} minutes, {int(t_diff % 60)} seconds" if t_diff < 3600
            else f"{int(t_diff // 3600)} hours, {int((t_diff % 3600) // 60)} minutes"
        )
        muted = chrome("text_muted")
        lbl_info = QLabel(
            f"<b>Comparing Event A vs Event B</b> &nbsp;|&nbsp; "
            f"<span style='color:{muted};'>Time Delta (Δt):</span> <b>{diff_str}</b>"
        )
        self._delta_lbl = lbl_info
        h_layout.addWidget(lbl_info)
        h_layout.addStretch()
        layout.addWidget(header)

        # ── Side-by-side comparison panels ─────────────────────────────
        diff_layout = QHBoxLayout()
        diff_layout.setSpacing(8)

        # Panel A
        panel_a = self._build_event_card("Event A (Earlier)" if entry_a.timestamp <= entry_b.timestamp else "Event A", entry_a)
        diff_layout.addWidget(panel_a, 1)

        # Panel B
        panel_b = self._build_event_card("Event B (Later)" if entry_b.timestamp > entry_a.timestamp else "Event B", entry_b)
        diff_layout.addWidget(panel_b, 1)

        layout.addLayout(diff_layout, 1)

        # ── Raw fields diff table ───────────────────────────────────────
        lbl_raw = QLabel("<b>Property & Raw Fields Comparison:</b>")
        layout.addWidget(lbl_raw)

        self._diff_table = QTableWidget()
        self._diff_table.setColumnCount(3)
        self._diff_table.setHorizontalHeaderLabels(["Field", "Event A Value", "Event B Value"])
        self._diff_table.horizontalHeader().setStretchLastSection(True)
        self._diff_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._diff_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._diff_table.setAlternatingRowColors(True)
        self._diff_table.verticalHeader().setVisible(False)
        self._populate_diff_table()
        layout.addWidget(self._diff_table, 1)

        # ── Close button ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        QShortcut(QKeySequence("Escape"), self).activated.connect(self.accept)

    def _build_event_card(self, title: str, entry: LogEntry) -> QWidget:
        widget = QFrame()
        widget.setObjectName("card")
        v = QVBoxLayout(widget)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(4)

        t_lbl = QLabel(f"<b>{title}</b>")
        v.addWidget(t_lbl)

        meta = QLabel(
            f"<b>Time:</b> {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}<br>"
            f"<b>Source:</b> {html.escape(str(entry.source))}<br>"
            f"<b>Level:</b> {html.escape(str(entry.level))}"
        )
        v.addWidget(meta)

        v.addWidget(QLabel("<b>Message:</b>"))
        msg_edit = QTextEdit()
        msg_edit.setReadOnly(True)
        msg_edit.setFont(QFont("Consolas", 9))
        msg_edit.setPlainText(str(entry.message or ""))
        v.addWidget(msg_edit, 1)
        return widget

    def _populate_diff_table(self) -> None:
        raw_a = self._a.raw or {}
        raw_b = self._b.raw or {}
        all_keys = ["Timestamp", "Source", "Level", *sorted(set(raw_a.keys()) | set(raw_b.keys()))]

        self._diff_table.setRowCount(len(all_keys))
        for row, k in enumerate(all_keys):
            if k == "Timestamp":
                val_a = self._a.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                val_b = self._b.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            elif k == "Source":
                val_a = str(self._a.source)
                val_b = str(self._b.source)
            elif k == "Level":
                val_a = str(self._a.level)
                val_b = str(self._b.level)
            else:
                val_a = str(raw_a.get(k, "—"))
                val_b = str(raw_b.get(k, "—"))

            k_item = QTableWidgetItem(k)
            k_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            a_item = QTableWidgetItem(val_a)
            a_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            b_item = QTableWidgetItem(val_b)
            b_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)

            # Highlight differences
            if val_a != val_b:
                k_item.setForeground(QColor(semantic("warning")))

            self._diff_table.setItem(row, 0, k_item)
            self._diff_table.setItem(row, 1, a_item)
            self._diff_table.setItem(row, 2, b_item)
