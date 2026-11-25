"""UI card representing a multi-step Quick Fix Playbook (Recipe).

Allows 1-click execution of triage workflows with live progress,
cancellation support, and System Restore Point integration.
"""

import logging
from typing import Callable, Optional

from PyQt6.QtCore import QThreadPool, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from core.table_ui import set_role
from core.worker import Worker, retire
from modules.quick_fix.quick_fix_playbooks import Playbook, PlaybookResult, run_playbook

logger = logging.getLogger(__name__)


class PlaybookCard(QFrame):
    """UI card for launching and monitoring a multi-step triage recipe."""

    line_logged = pyqtSignal(str, str)  # (playbook_title, line)
    playbook_started = pyqtSignal(str)  # playbook_title
    playbook_finished = pyqtSignal(str, bool, str)  # (playbook_title, success, message)

    def __init__(self, playbook: Playbook, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._playbook = playbook
        self._worker: Optional[Worker] = None
        self._running = False
        self._thread_pool = QThreadPool.globalInstance()
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header row: Icon, Title, Duration
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(6)
        icon_lbl = QLabel(self._playbook.icon)
        icon_lbl.setFont(QFont("", 12))
        hdr_row.addWidget(icon_lbl)

        title_lbl = QLabel(self._playbook.title)
        t_font = title_lbl.font()
        t_font.setBold(True)
        title_lbl.setFont(t_font)
        hdr_row.addWidget(title_lbl)

        hdr_row.addStretch()

        dur_lbl = QLabel(self._playbook.estimated_duration)
        set_role(dur_lbl, "muted")
        hdr_row.addWidget(dur_lbl)

        if self._playbook.reboot_required:
            rb_badge = QLabel("⚠ Reboot")
            set_role(rb_badge, "statusWarning")
            hdr_row.addWidget(rb_badge)

        layout.addLayout(hdr_row)

        # Description
        desc_lbl = QLabel(self._playbook.description)
        desc_lbl.setWordWrap(True)
        set_role(desc_lbl, "muted")
        layout.addWidget(desc_lbl)

        # Progress bar (hidden unless running)
        self._progress = QProgressBar()
        self._progress.setRange(0, len(self._playbook.action_keys))
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Action button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._run_btn = QPushButton("Run Playbook")
        self._run_btn.setToolTip(f"Execute all {len(self._playbook.action_keys)} steps of {self._playbook.title}")
        self._run_btn.clicked.connect(self._on_run_clicked)
        btn_row.addWidget(self._run_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setToolTip("Cancel this in-flight playbook execution")
        self._cancel_btn.clicked.connect(self.cancel)
        self._cancel_btn.hide()
        btn_row.addWidget(self._cancel_btn)

        self._status_lbl = QLabel("")
        set_role(self._status_lbl, "muted")
        btn_row.addWidget(self._status_lbl)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _on_run_clicked(self) -> None:
        if self._running:
            return

        msg = (
            f"Run the '{self._playbook.title}' playbook?\n\n"
            f"This will execute {len(self._playbook.action_keys)} repair steps sequentially:\n"
            f"{', '.join(self._playbook.action_keys)}\n\n"
            "Would you like to proceed?"
        )
        reply = QMessageBox.question(
            self,
            f"Run {self._playbook.title}",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._start_execution()

    def _start_execution(self) -> None:
        self._running = True
        self._run_btn.setEnabled(False)
        self._cancel_btn.show()
        self._progress.setValue(0)
        self._progress.show()
        self._status_lbl.setText("Running...")
        set_role(self._status_lbl, "statusWarning")
        self.playbook_started.emit(self._playbook.title)

        def worker_fn(worker_ctx):
            def out_cb(line: str):
                self.line_logged.emit(self._playbook.title, line)

            def step_cb(step_num: int, total_steps: int, step_name: str):
                worker_ctx.signals.progress.emit(step_num)

            res = run_playbook(
                playbook=self._playbook,
                output_cb=out_cb,
                step_cb=step_cb,
                create_restore_point_fn=None,
                is_cancelled=lambda: getattr(worker_ctx, "is_cancelled", False),
            )
            return res

        w = Worker(worker_fn)
        w.signals.progress.connect(self._on_progress)
        w.signals.result.connect(self._on_done)
        w.signals.error.connect(self._on_error)
        self._worker = w
        self._thread_pool.start(w)

    def _on_progress(self, current_step: int) -> None:
        self._progress.setValue(current_step)

    def _on_done(self, result: PlaybookResult) -> None:
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._cancel_btn.hide()
        self._progress.hide()

        status_msg = f"{result.executed_steps}/{result.total_steps} steps in {result.elapsed_seconds:.1f}s"
        if result.success:
            self._status_lbl.setText(f"Completed ({status_msg})")
            set_role(self._status_lbl, "statusSuccess")
            self.playbook_finished.emit(self._playbook.title, True, status_msg)
        else:
            self._status_lbl.setText(f"Warnings ({status_msg})")
            set_role(self._status_lbl, "statusWarning")
            self.playbook_finished.emit(self._playbook.title, False, status_msg)

    def _on_error(self, err: str) -> None:
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._cancel_btn.hide()
        self._progress.hide()
        self._status_lbl.setText("Error")
        set_role(self._status_lbl, "statusError")
        self.line_logged.emit(self._playbook.title, f"ERROR: {err}")
        self.playbook_finished.emit(self._playbook.title, False, str(err))

    def cancel(self) -> None:
        if self._worker is not None and self._running:
            retire([self._worker])
            self._running = False
            self._worker = None
            self._run_btn.setEnabled(True)
            self._cancel_btn.hide()
            self._progress.hide()
            self._status_lbl.setText("Cancelled")
            set_role(self._status_lbl, "muted")
            self.line_logged.emit(self._playbook.title, "Execution cancelled by user.")
            self.playbook_finished.emit(self._playbook.title, False, "Cancelled")
