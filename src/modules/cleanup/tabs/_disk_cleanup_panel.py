"""Windows' own Disk Cleanup (cleanmgr /sageset + /sagerun), on the Large
Items tab.

Deliberately its own panel, not part of the checkbox tree the rest of this
tab uses: cleanmgr's dialog controls exactly what gets cleaned, opaquely --
this app cannot preview or select individual files the way a ScanItem-based
scanner does. See disk_cleanup_sageset.py for why this reaches categories
(System Restore & Shadow Copies, DirectX Shader Cache, old Windows Update
files) no scanner elsewhere in this app can.
"""
import logging

from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from core.long_op_pool import get_long_op_pool
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup import disk_cleanup_sageset as dcs

logger = logging.getLogger(__name__)


class _DiskCleanupPanel(QWidget):
    """Configure and run Windows' own Disk Cleanup via /sageset + /sagerun."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._pool = get_long_op_pool()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)

        row = QHBoxLayout()
        title = QLabel("<b>Windows Disk Cleanup</b>")
        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("muted")
        self._configure_btn = QPushButton("Configure Categories…")
        self._configure_btn.setToolTip(
            f"Runs: cleanmgr.exe /sageset:{dcs.PROFILE_NUMBER}\n"
            "Opens Windows' own Disk Cleanup dialog so you can pick which "
            "categories it should clean. Nothing is deleted by this button "
            "-- only the choice is saved.")
        self._run_btn = QPushButton("Run Configured Cleanup")
        self._run_btn.setToolTip(
            f"Runs: cleanmgr.exe /sagerun:{dcs.PROFILE_NUMBER}\n"
            "Silently cleans exactly whatever categories were checked in "
            "the Configure dialog above.")
        self._run_btn.setEnabled(False)
        row.addWidget(title)
        row.addStretch()
        row.addWidget(self._status_lbl)
        row.addWidget(self._configure_btn)
        row.addWidget(self._run_btn)
        layout.addLayout(row)

        description = QLabel(
            "Windows' own Disk Cleanup reaches categories this app's "
            "scanners cannot -- System Restore & Shadow Copies, old Windows "
            "Update files, DirectX Shader Cache, and more, each cleaned by "
            "Windows' own handler for it. Configure once to pick "
            "categories; Run replays exactly that choice, silently, every "
            "time.")
        description.setWordWrap(True)
        description.setObjectName("muted")
        layout.addWidget(description)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFixedHeight(90)
        self._output.hide()
        layout.addWidget(self._output)

        self._configure_btn.clicked.connect(self._configure)
        self._run_btn.clicked.connect(self._run)
        self._refresh_status()

    # ── Status ──

    def _refresh_status(self) -> None:
        def _read(_worker):
            return dcs.configured_categories()

        def _done(categories):
            if not widget_is_valid(self):
                return
            if categories is None:
                self._status_lbl.setText("Could not read configuration")
                self._run_btn.setEnabled(False)
            elif not categories:
                self._status_lbl.setText("Not configured yet")
                self._run_btn.setEnabled(False)
            else:
                self._status_lbl.setText(f"{len(categories)} categor{'y' if len(categories) == 1 else 'ies'} configured")
                self._run_btn.setEnabled(True)

        worker = Worker(_read)
        worker.signals.result.connect(_done)
        self._pool.start(worker)

    # ── Configure ──

    def _configure(self) -> None:
        self._configure_btn.setEnabled(False)
        self._output.hide()

        def _run(_worker):
            import subprocess
            # No real cleanmgr dialog takes 30 minutes to configure, but an
            # untimed blocking subprocess call waits forever if the process
            # never returns at all (a wedged descendant, per
            # core/appx_service.py's own documented lesson) -- this is a
            # generous backstop, not an expected duration.
            subprocess.run(dcs.sageset_command(), timeout=1800)

        def _done(_result):
            if not widget_is_valid(self):
                return
            self._configure_btn.setEnabled(True)
            self._refresh_status()

        def _err(message: str):
            if not widget_is_valid(self):
                return
            self._configure_btn.setEnabled(True)
            self._output.show()
            self._output.append(f"Error: {message}")

        self._worker = Worker(_run)
        self._worker.signals.result.connect(_done)
        self._worker.signals.error.connect(_err)
        self._pool.start(self._worker)

    # ── Run ──

    def _run(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Run Disk Cleanup")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            "About to run <b>cleanmgr /sagerun</b> with the categories "
            "chosen in Configure. This deletes according to whatever was "
            "checked there -- this app does not control or preview which "
            "files that includes.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return

        self._run_btn.setEnabled(False)
        self._configure_btn.setEnabled(False)
        self._output.show()
        self._output.clear()
        self._output.append("Running Disk Cleanup…")

        def _run(worker):
            import subprocess
            proc = subprocess.run(
                dcs.sagerun_command(), capture_output=True, text=True,
                errors="replace", timeout=1800)
            return proc.returncode

        def _done(returncode):
            if not widget_is_valid(self):
                return
            self._run_btn.setEnabled(True)
            self._configure_btn.setEnabled(True)
            self._output.append(f"Done (exit {returncode}).")
            logger.info("Disk Cleanup /sagerun finished, exit %s", returncode)

        def _err(message: str):
            if not widget_is_valid(self):
                return
            self._run_btn.setEnabled(True)
            self._configure_btn.setEnabled(True)
            self._output.append(f"Error: {message}")

        self._worker = Worker(_run)
        self._worker.signals.result.connect(_done)
        self._worker.signals.error.connect(_err)
        self._pool.start(self._worker)

    def _cancel_all(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
