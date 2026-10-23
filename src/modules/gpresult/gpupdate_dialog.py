"""A dialog that runs `gpupdate` and shows what it is doing while it does it.

Two things drive the design:

* **A policy refresh goes silent for long stretches.** `gpupdate` can sit for
  tens of seconds between lines, so the output is streamed line by line as it
  arrives rather than shown as one blob at the end, and Cancel is polled on a
  timer rather than only when a line turns up -- otherwise the button feels
  dead exactly when someone wants it.
* **The exit code is not the verdict.** Refreshing computer policy needs
  elevation, and unelevated `gpupdate` reports that in its output text. The
  common outcome is therefore a *partial* refresh, which must not be rendered
  as either success or failure. `gpupdate.parse_output` works that out; this
  dialog only has to show it faithfully.
"""

from __future__ import annotations

from modules.gpresult.help_widgets import (
    describe_control, explain_widget,
)

import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from core.semantic_colors import semantic
from core.worker import Worker

from modules.gpresult.gpupdate import (
    GpupdateOptions, GpupdateResult, STATUS_CANCELLED, STATUS_FAILURE,
    STATUS_PARTIAL, STATUS_SUCCESS, TARGET_ALL, TARGET_COMPUTER, TARGET_USER,
    run_gpupdate,
)

logger = logging.getLogger(__name__)

_TARGETS = [
    ("Computer and User", TARGET_ALL),
    ("User only", TARGET_USER),
    ("Computer only (needs elevation)", TARGET_COMPUTER),
]

#: How the verdict is painted. `partial` is a warning rather than an error:
#: a user-policy refresh that worked while the computer half was refused is a
#: real, useful outcome and calling it a failure would be wrong.
_STATUS_COLOURS = {
    STATUS_SUCCESS: "success",
    STATUS_PARTIAL: "warning",
    STATUS_FAILURE: "error",
    STATUS_CANCELLED: "info",
}


class GpupdateDialog(QDialog):
    """Runs one `gpupdate` and reports honestly what came back."""

    #: Worker threads may not touch widgets, so output crosses back here.
    line_arrived = pyqtSignal(str)
    finished_with = pyqtSignal(object)

    def __init__(self, parent=None, thread_pool=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Refresh Group Policy")
        self.resize(720, 460)
        self._thread_pool = thread_pool
        self._worker = None
        self._cancelled = False
        self.result_obj: GpupdateResult = None

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Refresh:"))
        self._target = QComboBox()
        describe_control(self._target, '_target')
        for label, value in _TARGETS:
            self._target.addItem(label, value)
        controls.addWidget(self._target)

        self._force = QCheckBox("Reapply every setting (/force)")
        describe_control(self._force, '_force')
        self._force.setToolTip(
            "Without this, Windows only reapplies policies that changed.")
        controls.addWidget(self._force)
        controls.addStretch()

        self._run_btn = QPushButton("Run")
        describe_control(self._run_btn, '_run_btn')
        self._run_btn.setDefault(True)
        controls.addWidget(self._run_btn)
        layout.addLayout(controls)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText(
            "gpupdate output will appear here as it runs.")
        layout.addWidget(self._output, 1)

        self._verdict = QLabel("")
        self._verdict.setWordWrap(True)
        layout.addWidget(self._verdict)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._cancel_btn = self._buttons.addButton(
            "Cancel run", QDialogButtonBox.ButtonRole.RejectRole)
        self._cancel_btn.setEnabled(False)
        layout.addWidget(self._buttons)

        self._run_btn.clicked.connect(self.start)
        self._cancel_btn.clicked.connect(self._cancel)
        self._buttons.rejected.connect(self._on_close_requested)
        self._buttons.accepted.connect(self.accept)
        for button in (self._buttons.button(QDialogButtonBox.StandardButton.Close),):
            if button is not None:
                button.clicked.connect(self.close)

        self.line_arrived.connect(self._append_line)
        self.finished_with.connect(self._on_finished)
        explain_widget(self)

    # ------------------------------------------------------------------

    @property
    def options(self) -> GpupdateOptions:
        return GpupdateOptions(target=self._target.currentData(),
                               force=self._force.isChecked())

    def start(self) -> None:
        if self._worker is not None:
            return
        self._cancelled = False
        self._output.clear()
        self._verdict.setText("")
        self._verdict.setStyleSheet("")
        self._progress.show()
        self._run_btn.setEnabled(False)
        self._target.setEnabled(False)
        self._force.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        options = self.options

        def work(worker):
            return run_gpupdate(
                options,
                on_line=self.line_arrived.emit,
                # Worker.is_cancelled is a property, so it has to be wrapped
                # rather than passed -- passing it hands over a bool that was
                # sampled once and never changes.
                is_cancelled=lambda: worker.is_cancelled or self._cancelled,
            )

        worker = Worker(work)
        worker.signals.result.connect(self.finished_with.emit)
        worker.signals.error.connect(self._on_error)
        self._worker = worker
        if self._thread_pool is not None:
            self._thread_pool.start(worker)
        else:
            from PyQt6.QtCore import QThreadPool
            QThreadPool.globalInstance().start(worker)

    def _cancel(self) -> None:
        self._cancelled = True
        if self._worker is not None:
            self._worker.cancel()
        self._cancel_btn.setEnabled(False)
        self._append_line("-- cancelling --")

    def _on_close_requested(self) -> None:
        if self._worker is not None:
            self._cancel()
            return
        self.reject()

    # ------------------------------------------------------------------

    def _append_line(self, line: str) -> None:
        self._output.appendPlainText(line)

    def _on_error(self, message: str) -> None:
        self._worker = None
        self._progress.hide()
        self._restore_controls()
        self._verdict.setText("gpupdate could not be started: %s" % message)
        self._verdict.setStyleSheet("color: %s;" % semantic("error"))

    def _on_finished(self, result: GpupdateResult) -> None:
        self._worker = None
        self.result_obj = result
        self._progress.hide()
        self._restore_controls()

        for line in result.errors:
            self._append_line(line)

        self._verdict.setText(result.summary)
        colour = _STATUS_COLOURS.get(result.status, "warning")
        self._verdict.setStyleSheet("color: %s;" % semantic(colour))

        # The exit code is reported, never used as the verdict. When it
        # disagrees with the parsed outcome that disagreement is itself worth
        # showing -- it is the exact shape of bug this wrapper exists for.
        if result.exit_code is not None and not result.exit_code_agrees:
            self._append_line(
                "-- note: gpupdate exited %d, which does not match the "
                "outcome its own output reports --" % result.exit_code)

    def _restore_controls(self) -> None:
        self._run_btn.setEnabled(True)
        self._target.setEnabled(True)
        self._force.setEnabled(True)
        self._cancel_btn.setEnabled(False)

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._cancel()
        super().closeEvent(event)
