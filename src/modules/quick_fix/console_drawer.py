"""Unified Collapsible Console Log Drawer for Quick Fix module.

Displays live timestamped output from fix actions and triage playbooks,
with execution timer, line count badge, and clear/copy controls.
"""

import time
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from core.table_ui import set_role


class QuickFixConsoleDrawer(QFrame):
    """Collapsible drawer showing unified stream of logs with execution timing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._expanded = False
        self._line_count = 0
        self._start_time: Optional[float] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Header bar
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self._toggle_btn = QPushButton("📟 Console Log (0)")
        self._toggle_btn.setToolTip("Toggle slide-out console log drawer to inspect live command output")
        self._toggle_btn.clicked.connect(self.toggle_expanded)
        header_row.addWidget(self._toggle_btn)

        self._timer_lbl = QLabel("Ready")
        set_role(self._timer_lbl, "muted")
        header_row.addWidget(self._timer_lbl)

        header_row.addStretch()

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setToolTip("Clear all console log lines")
        self._clear_btn.clicked.connect(self.clear_log)
        header_row.addWidget(self._clear_btn)

        layout.addLayout(header_row)

        # Console text area (hidden by default)
        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setMaximumHeight(160)
        self._text_edit.setFont(QFont("Consolas", 8))
        self._text_edit.hide()
        layout.addWidget(self._text_edit)

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._text_edit.setVisible(self._expanded)
        arrow = "▲" if self._expanded else "▼"
        self._toggle_btn.setText(f"📟 Console Log ({self._line_count}) {arrow}")

    def append_line(self, source: str, line: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        prefix = f"[{timestamp}] [{source}] " if source else f"[{timestamp}] "
        full_line = f"{prefix}{line}"
        self._text_edit.appendPlainText(full_line)
        self._line_count += 1
        arrow = "▲" if self._expanded else "▼"
        self._toggle_btn.setText(f"📟 Console Log ({self._line_count}) {arrow}")

    def notify_start(self, action_name: str) -> None:
        self._start_time = time.monotonic()
        self._timer_lbl.setText(f"Running: {action_name}...")
        set_role(self._timer_lbl, "statusWarning")

    def notify_complete(self, action_name: str, success: bool, message: str = "") -> None:
        elapsed_str = ""
        if self._start_time is not None:
            elapsed = time.monotonic() - self._start_time
            elapsed_str = f" in {elapsed:.1f}s"
            self._start_time = None

        status = "Completed" if success else "Failed"
        suffix = f": {message}" if message else ""
        self._timer_lbl.setText(f"{action_name} {status}{elapsed_str}{suffix}")
        set_role(self._timer_lbl, "statusSuccess" if success else "statusError")

    def clear_log(self) -> None:
        self._text_edit.clear()
        self._line_count = 0
        arrow = "▲" if self._expanded else "▼"
        self._toggle_btn.setText(f"📟 Console Log (0) {arrow}")
        self._timer_lbl.setText("Cleared")
        set_role(self._timer_lbl, "muted")
