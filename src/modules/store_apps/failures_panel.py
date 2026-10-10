"""The "Install / update failures" tab: deployment_failures rendered as a table.

Qt only; every judgement it shows is made in `deployment_failures`.
"""
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHeaderView, QLabel, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.semantic_colors import semantic
from core.table_ui import set_role
from modules.store_apps.deployment_failures import (
    LOG_NAME, STATE_LABELS, STATE_NOT_INSTALLED, STATE_PENDING, STATE_RESOLVED,
    FailureGroup, FailureReport,
)
from ui.empty_state import EmptyState

_COLUMNS = ["State", "App", "Error", "What it means", "Failures",
            "Last failure", "Tried version", "Installed version", "Started by"]

_STATE_COLOUR = {
    STATE_PENDING: "warning",
    STATE_RESOLVED: "success",
    STATE_NOT_INSTALLED: "info",
}


def _local(when: Optional[datetime]) -> str:
    return when.astimezone().strftime("%Y-%m-%d %H:%M") if when else ""


def _tooltip(g: FailureGroup) -> str:
    lines = [f"{g.code_text} {g.symbol}".strip(), g.meaning]
    if g.summary:
        lines += ["", "Windows said:", g.summary]
    if g.first and g.first != g.last:
        lines.append(f"\nFirst failure: {_local(g.first)}")
    if g.resolved_at:
        lines.append(f"Installed successfully at {_local(g.resolved_at)}")
    if g.attempted_full_name:
        lines.append(f"Package: {g.attempted_full_name}")
    return "\n".join(lines)


def summary_line(report: FailureReport) -> str:
    """The one-line header: what was read, over what window, how many pending."""
    since = f" since {_local(report.log_since)}" if report.log_since else ""
    pending = len(report.pending)
    pending_text = (f"{pending} still pending" if pending
                    else "none still pending")
    return (f"{report.events} failed deployment(s) in {LOG_NAME}{since} "
            f"(event 404), {len(report.groups)} distinct -- {pending_text}. "
            "The log is circular; older failures have rotated out.")


class DeploymentFailuresPanel(QWidget):
    """Table of grouped deployment failures, with honest empty/refused states."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        set_role(self._summary, "muted")
        layout.addWidget(self._summary)

        self._stack = QStackedWidget()
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        header = self._table.horizontalHeader()
        # Every column sized to its text; the meaning column is the long one
        # and a wide table scrolls sideways rather than eliding the advice.
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self._table.setHorizontalScrollMode(
            QTableWidget.ScrollMode.ScrollPerPixel)
        self._stack.addWidget(self._table)
        self._stack.addWidget(EmptyState("⏳", "Not read yet",
                                         "Failures load with the app list."))
        self._stack.setCurrentIndex(1)
        layout.addWidget(self._stack)

    @property
    def table(self) -> QTableWidget:
        return self._table

    def _set_empty(self, glyph: str, title: str, hint: str) -> None:
        old = self._stack.widget(1)
        self._stack.removeWidget(old)
        old.deleteLater()
        self._stack.addWidget(EmptyState(glyph, title, hint))
        self._stack.setCurrentIndex(1)

    def show_report(self, report: FailureReport) -> None:
        if report.read_error:
            self._summary.setText("")
            self._set_empty("⚠️", "Could not read the deployment log",
                            report.read_error)
            return
        self._summary.setText(summary_line(report))
        if not report.groups:
            since = (f" since {_local(report.log_since)}"
                     if report.log_since else "")
            self._set_empty("✅", "No failed installs or updates",
                            f"Windows logged no failed app deployment{since}.")
            return
        self._table.setRowCount(0)
        for g in report.groups:
            self._add_row(g)
        self._stack.setCurrentIndex(0)

    def _add_row(self, g: FailureGroup) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        values = [
            STATE_LABELS.get(g.state, g.state), g.display_name, g.code_text,
            g.meaning, str(g.count), _local(g.last), g.attempted_version,
            g.installed_version, g.caller,
        ]
        tip = _tooltip(g)
        colour = _STATE_COLOUR.get(g.state)
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setToolTip(tip)
            if col == 0:
                item.setData(Qt.ItemDataRole.UserRole, g.state)
                if colour:
                    item.setForeground(QColor(semantic(colour)))
            self._table.setItem(row, col, item)
