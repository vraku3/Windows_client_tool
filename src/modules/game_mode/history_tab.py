"""Lazy session history browser for Game Mode."""
from __future__ import annotations

import datetime
import logging

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
                             QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from ui.error_banner import ErrorBanner
from ui.table_style import apply_table_style

from .engine.session_history import load_history, session_stats

logger = logging.getLogger(__name__)


def _duration(seconds):
    if seconds is None:
        return "ON / not restored"
    minutes = max(0, int(seconds)) // 60
    return f"{minutes // 60} h {minutes % 60} min"


def _date(timestamp):
    if timestamp is None:
        return "Not yet restored"
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError, TypeError):
        return "Unknown"


def _restore_status(entry):
    if entry.get("restore_ok") is True:
        return "Fully restored", "success"
    if entry.get("restore_ok") is False:
        if entry.get("restore_restored", 0):
            return "Partial restore", "warning"
        return "Restore failed", "error"
    return "ON / not restored", "warning"


class HistoryTab(QWidget):
    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self._folder = folder
        self._loaded = False
        self._history = []
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self._summary = QLabel("Visit History to load sessions.", self)
        self._summary.setWordWrap(True)
        top.addWidget(self._summary, 1)
        refresh = QPushButton("Refresh", self)
        refresh.clicked.connect(lambda: self.activate(refresh=True))
        top.addWidget(refresh)
        layout.addLayout(top)
        self._error = ErrorBanner("", self)
        self._error.hide()
        layout.addWidget(self._error)
        self._table = QTableWidget(0, 6, self)
        self._table.setHorizontalHeaderLabels(["Date", "Duration", "Preset", "Actions", "Handles freed", "Restore status"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        apply_table_style(self._table, numeric_columns=(3, 4))
        self._table.itemSelectionChanged.connect(self._show_detail)
        layout.addWidget(self._table, 3)
        self._detail = QPlainTextEdit(self)
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select a session to see its actions and restoration issues.")
        layout.addWidget(self._detail, 2)

    def invalidate(self):
        self._loaded = False
        if self.isVisible():
            self.activate()

    def activate(self, refresh=False):
        if self._loaded and not refresh:
            return
        try:
            self._history = list(reversed(load_history(None, folder=self._folder)))
            stats = session_stats(self._history)
            average = stats["average_duration_s"]
            rate = stats["success_rate"]
            self._summary.setText(
                f"{stats['total_sessions']} sessions, average "
                + (f"{average / 3600:.1f} hours" if average is not None else "not available")
                + (f", {rate:.0f}% restored" if rate is not None else ", no restoration attempts")
                + (" — no sessions recorded yet" if not self._history else ""))
            self._table.setRowCount(0)
            self._detail.clear()
            for row, entry in enumerate(self._history):
                before, after = entry.get("handles_before"), entry.get("handles_after")
                freed = f"{before - after:,}" if before is not None and after is not None else "Unknown"
                status, color = _restore_status(entry)
                cells = [_date(entry["started"]), _duration(entry.get("duration_s")),
                         entry.get("preset", "Unknown"),
                         f"{entry.get('actions_ok', 0)}/{entry.get('actions_total', 0)} OK", freed, status]
                self._table.insertRow(row)
                for column, value in enumerate(cells):
                    item = QTableWidgetItem(str(value))
                    if column == 5:
                        item.setForeground(QColor(semantic(color)))
                    self._table.setItem(row, column, item)
            self._error.hide()
            self._loaded = True
            if self._history:
                self._table.selectRow(0)
        except (OSError, ValueError, TypeError) as error:
            logger.warning("game mode: could not load session history: %s", error)
            self._error.set_error(f"Session history could not be loaded: {error}")

    def _show_detail(self):
        row = self._table.currentRow()
        if not 0 <= row < len(self._history):
            return
        entry = self._history[row]
        lines = [f"ON: {_date(entry['started'])}    OFF: {_date(entry.get('ended'))}",
                 f"Preset: {entry.get('preset', 'Unknown')}    Duration: {_duration(entry.get('duration_s'))}",
                 f"Restore: {_restore_status(entry)[0]}",
                 f"Actions: {entry.get('actions_ok', 0)} succeeded, {entry.get('actions_failed', 0)} failed, "
                 f"{entry.get('actions_skipped', 0)} skipped",
                 f"Services stopped: {entry.get('services_stopped', 0)}    Apps closed: {entry.get('apps_closed', 0)}"
                 f"    Tasks disabled: {entry.get('tasks_disabled', 0)}",
                 f"Handles: {entry.get('handles_before')} → {entry.get('handles_after')}    "
                 f"Processes: {entry.get('processes_before')} → {entry.get('processes_after')}", "", "Changes:"]
        lines.extend(f"[{e['status']}] {e['label']} ({e['kind']})" for e in entry.get("actions", []))
        lines.extend(["", "ON report:"])
        lines.extend(f"[{r['level']}] {r['text']}" for r in entry.get("on_report", []))
        lines.extend(["", "Restoration issues:"])
        lines.extend(entry.get("restore_issues") or ["None reported" if entry.get("restore_ok") is not None
                                                   else "Restoration has not been recorded."])
        self._detail.setPlainText("\n".join(lines))
