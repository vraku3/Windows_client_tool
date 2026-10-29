"""_StoreUpdatesTab — Microsoft Store view.

Renders the msstore-sourced subset of the shared winget fetch (App Updates
and Store tabs both read from one `winget upgrade --include-unknown` scan,
dispatched via UpdatesModule — no duplicate winget calls), plus Store-only
actions: trigger a background update scan via the MDM CIM class, open the
Store's Downloads & updates page, and reset the Store cache (wsreset.exe).
"""
import logging
import subprocess
import time
from typing import List

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTableWidget, QVBoxLayout, QWidget,
)

from core.table_ui import centered_item, center_header
from core.widget_life import widget_is_valid
from core.worker import COMWorker, Worker
from modules.updates.operation_guard import maintenance_worker
from modules.updates.winget_updater import AppUpdate
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)


_widget_valid = widget_is_valid


class _StoreUpdatesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self._updates: List[AppUpdate] = []
        self._workers: list = []
        self._busy_owner = ""
        self._updating = False
        self._finish_callback = None
        self._setup_ui()

    def set_app(self, app) -> None:
        self.app = app
        self._sync_buttons()

    def set_finish_callback(self, callback) -> None:
        self._finish_callback = callback

    def set_maintenance_busy(self, owner) -> None:
        self._busy_owner = owner or ""
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        busy = bool(self._busy_owner) or self._updating
        self._trigger_btn.setEnabled(not busy)
        self._update_btn.setEnabled(
            not busy and bool({i.row() for i in self._table.selectedIndexes()}))

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        toolbar = QHBoxLayout()
        self._trigger_btn = QPushButton("Verify Store Updates")
        self._update_btn = QPushButton("Update Selected")
        self._update_btn.setEnabled(False)
        self._update_btn.setToolTip(
            "Upgrade the selected Store offers through winget by exact id. "
            "No rollback is provided; a restart may be needed.")
        self._open_btn = QPushButton("Open Store")
        self._reset_btn = QPushButton("Reset Store Cache")
        self._reset_btn.setToolTip("Runs wsreset.exe — clears the Store cache and closes the Store app.")
        self._status_lbl = QLabel("Refreshed together with the App Updates tab.")
        toolbar.addWidget(self._trigger_btn)
        toolbar.addWidget(self._update_btn)
        toolbar.addWidget(self._open_btn)
        toolbar.addWidget(self._reset_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Name", "Id", "Installed", "Available"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.itemSelectionChanged.connect(self._sync_buttons)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 8))

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(self._log)
        splitter.setSizes([400, 120])
        layout.addWidget(splitter, 1)

        self._trigger_btn.clicked.connect(self._do_trigger)
        self._update_btn.clicked.connect(self._do_update)
        self._open_btn.clicked.connect(self._do_open_store)
        self._reset_btn.clicked.connect(self._do_reset)

    def set_updates(self, all_updates: List[AppUpdate]) -> None:
        """Called by UpdatesModule after a shared winget fetch — filters to msstore rows."""
        self._updates = [u for u in all_updates if (u.source or "").strip().lower() == "msstore"]
        if not _widget_valid(self._table):
            return
        self._table.setRowCount(len(self._updates))
        for row, u in enumerate(self._updates):
            self._table.setItem(row, 0, centered_item(u.name))
            self._table.setItem(row, 1, centered_item(u.winget_id))
            self._table.setItem(row, 2, centered_item(u.installed_version))
            self._table.setItem(row, 3, centered_item(u.available_version))
        self._status_lbl.setText(
            f"{len(self._updates)} Store app(s) with updates."
            if self._updates else "No Store app updates pending (via winget)."
        )
        self._sync_buttons()

    def _selected_updates(self) -> List[AppUpdate]:
        rows = sorted({index.row() for index in self._table.selectedIndexes()})
        return [self._updates[row] for row in rows if row < len(self._updates)]

    def _do_update(self) -> None:
        targets = self._selected_updates()
        if not targets or self.app is None or self._updating:
            return
        names = "\n".join(f"* {u.name} ({u.winget_id})" for u in targets[:8])
        if len(targets) > 8:
            names += f"\n* and {len(targets) - 8} more"
        answer = QMessageBox.question(
            self, "Update Store apps",
            f"Update {len(targets)} Microsoft Store app(s) through winget?\n\n"
            + names
            + "\n\nNo rollback is provided; a restart may be needed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._updating = True
        self._sync_buttons()
        self._log.clear()
        app = self.app

        @maintenance_worker("Updates install")
        def _run(worker):
            from modules.updates.store_update import update_apps
            return update_apps(
                app, targets,
                log=lambda line: worker.signals.log_line.emit(line),
                is_cancelled=lambda: worker.is_cancelled,
                verify=bool(app.config.get("updates.verify_after_update", True)),
            )

        w = Worker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.result.connect(self._on_update_done)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_update_done(self, results) -> None:
        self._updating = False
        self._sync_buttons()
        if not _widget_valid(self._status_lbl):
            return
        confirmed = sum(1 for _update, outcome in results if outcome)
        self._status_lbl.setText(
            f"{confirmed}/{len(results)} Store app(s) confirmed updated.")
        if self._finish_callback is not None:
            self._finish_callback()

    def _do_trigger(self):
        self._trigger_btn.setEnabled(False)
        self._status_lbl.setText("Verifying Store updates (up to 90s)...")
        self._log.clear()
        self._log.appendPlainText(
            "Recording installed versions, then asking the Store to update. "
            "This watches for up to 90 seconds.")

        @maintenance_worker("Updates install")
        def _run(worker):
            from core.appx_service import fetch_packages_or_none
            from core.mdm_store_trigger import trigger_store_scan
            from modules.updates.store_verify import verify_store_update

            def log(line: str) -> None:
                worker.signals.log_line.emit(line)

            return verify_store_update(
                fetch=fetch_packages_or_none,
                trigger=lambda emit: trigger_store_scan(output_cb=emit),
                sleep=time.sleep,
                clock=time.monotonic,
                log=log,
                should_stop=lambda: worker.is_cancelled,
            )

        w = COMWorker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.result.connect(self._on_trigger_done)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_trigger_done(self, result) -> None:
        self._sync_buttons()
        if not _widget_valid(self._status_lbl):
            return
        self._status_lbl.setText(result.message)
        self._log.appendPlainText(result.message)

    def _on_error(self, err: str):
        self._updating = False
        self._sync_buttons()
        self._reset_btn.setEnabled(True)
        self._log.appendPlainText(f"Error: {err}")

    def _do_open_store(self):
        try:
            subprocess.Popen(["explorer.exe", "ms-windows-store://downloadsandupdates"])
        except Exception as e:
            self._error_banner.set_error(f"Could not open Microsoft Store: {e}")

    def _do_reset(self):
        reply = QMessageBox.question(
            self, "Reset Store Cache",
            "This runs wsreset.exe, which clears the Microsoft Store cache and "
            "closes the Store app if it's running. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._reset_btn.setEnabled(False)
        self._log.appendPlainText("Resetting Store cache (wsreset.exe)...")

        @maintenance_worker("Updates Store cache reset")
        def _run(worker):
            try:
                subprocess.run(
                    ["wsreset.exe"], timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except subprocess.TimeoutExpired:
                logger.warning("wsreset.exe did not exit within 30s (usually still fine)")
            return True

        w = Worker(_run)
        w.signals.result.connect(self._on_reset_done)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_reset_done(self, _result) -> None:
        self._reset_btn.setEnabled(True)
        self._log.appendPlainText("Store cache reset.")

    def _cancel_all(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
