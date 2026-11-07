import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QFrame, QDialog, QListWidget,
)
from PyQt6.QtCore import QThreadPool, pyqtSignal
from PyQt6.QtGui import QFont

from core.worker import Worker
from core.long_op_pool import get_long_op_pool
from modules.quick_fix.fix_actions import FixAction

logger = logging.getLogger(__name__)


class FixCard(QFrame):
    _line = pyqtSignal(str)
    history_changed = pyqtSignal()

    def __init__(self, action: FixAction, last_entry: dict = None,
                 recurring_count: int = 0, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._action = action
        self._running = False
        self._worker = None
        self._last_entry = last_entry
        self._recurring_count = recurring_count
        self._thread_pool = QThreadPool.globalInstance()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_lbl = QLabel(self._action.title)
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        title_row.addWidget(title_lbl)
        if self._action.reboot_required:
            badge = QLabel("⚠ Reboot required")
            badge.setStyleSheet("color: orange;")
            title_row.addStretch()
            title_row.addWidget(badge)
        layout.addLayout(title_row)

        desc = QLabel(self._action.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        self._run_btn = QPushButton("Run")
        from .help_text import ACTIONS
        prompt = (" Asks for confirmation before running." if self._action.confirm_text
                  else " Starts immediately without another confirmation.")
        self._run_btn.setToolTip(ACTIONS.get(self._action.key, self._action.description + " There is no app undo.") + prompt + " Requires administrator rights.")
        self._run_btn.setFixedWidth(80)
        layout.addWidget(self._run_btn)

        self._status = QLabel("")
        self._status.setToolTip("Recorded outcome or current precondition for this repair. A completed command is not an independent check that the original problem is resolved.")
        self._status.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._status)
        self._refresh_status()

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setMaximumHeight(150)
        mono_font = QFont("Consolas", 8)
        self._output.setFont(mono_font)
        self._output.hide()
        layout.addWidget(self._output)

        self._run_btn.clicked.connect(self._run)
        self._line.connect(self._output.appendPlainText)

    def _refresh_status(self) -> None:
        parts = []
        if self._recurring_count:
            parts.append(
                f"⚠ Run {self._recurring_count} times in the last 14 days — "
                f"if this keeps happening, the underlying cause needs a look.")
        if self._action.precondition is not None:
            msg = self._action.precondition()
            if msg is not None:
                parts.append(msg)
                self._status.setText(" ".join(parts))
                return
        if self._last_entry is not None:
            parts.append(
                f"Last run: {self._last_entry['at']} — {self._last_entry['outcome']}")
        self._status.setText(" ".join(parts))

    def _run(self):
        if self._running:
            return
        action = self._action

        if action.precondition is not None:
            msg = action.precondition()
            if msg is not None:
                self._status.setText(msg)
                return

        if action.confirm_text is not None:
            from PyQt6.QtWidgets import QMessageBox
            mb = QMessageBox(self)
            mb.setWindowTitle(action.title)
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setText(action.confirm_text)
            mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if mb.exec() != QMessageBox.StandardButton.Ok:
                return

        self._running = True
        self._run_btn.setEnabled(False)
        self._output.clear()
        self._output.show()
        self._status.setText("Running...")

        def append(line: str):
            self._line.emit(line)

        def do_work(_w):
            from modules.quick_fix import fix_actions
            self._worker = _w
            fix_actions.begin_tracking()
            try:
                action.fn(append)
            finally:
                failed = fix_actions.end_tracking()
            if failed:
                raise RuntimeError(f"{len(failed)} step(s) failed: " + "; ".join(failed[:3]))

        self._worker = Worker(do_work)
        w = self._worker
        w.signals.result.connect(lambda _r, w=w: self._on_done(w))
        w.signals.error.connect(lambda err, w=w: self._on_error(err, w))
        pool = get_long_op_pool() if action.long_running else self._thread_pool
        pool.start(w)

    def _on_done(self, worker) -> None:
        if self._worker is not worker:
            return
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        from modules.quick_fix import quick_fix_history
        self._last_entry = quick_fix_history.record(self._action.title, "ok")
        self.history_changed.emit()
        self._recurring_count = quick_fix_history.recurring_actions().get(
            self._action.title, 0)
        self._refresh_status()

    def _on_error(self, error_str: str, worker) -> None:
        if self._worker is not worker:
            return
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._output.appendPlainText(f"ERROR: {error_str}")
        from modules.quick_fix import quick_fix_history
        self._last_entry = quick_fix_history.record(self._action.title, "error")
        self.history_changed.emit()
        self._recurring_count = quick_fix_history.recurring_actions().get(
            self._action.title, 0)
        self._refresh_status()

    def cancel(self) -> None:
        if self._worker is not None and self._running:
            from core.worker import retire
            retire([self._worker])
            self._running = False
            self._worker = None
            self._run_btn.setEnabled(True)
            self._output.appendPlainText("Cancelled.")
            from modules.quick_fix import quick_fix_history
            self._last_entry = quick_fix_history.record(self._action.title, "cancelled")
            self._recurring_count = quick_fix_history.recurring_actions().get(
                self._action.title, 0)
            self._refresh_status()


class QuickFixHistoryDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Fix History")
        self.resize(520, 420)
        root = QVBoxLayout(self)
        from ui.module_intro import module_intro
        root.addWidget(module_intro("Read the recent repair outcomes recorded by this app. Nothing here changes Windows or undoes completed repairs; administrator rights are not needed to read local history."))

        self._list = QListWidget()
        from modules.quick_fix import quick_fix_history
        entries = quick_fix_history.recent(limit=20)
        if entries:
            for entry in entries:
                self._list.addItem(
                    f"{entry['at']} — {entry['action']}: {entry['outcome']}")
        else:
            self._list.addItem("No actions run yet.")
        root.addWidget(self._list)

        close_btn = QPushButton("Close")
        close_btn.setToolTip("Close this history window; no repair runs and the recorded history is kept.")
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)
