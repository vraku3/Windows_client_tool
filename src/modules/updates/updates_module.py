import os
import html
import subprocess
import sys
from dataclasses import replace
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTabWidget,
    QTableWidget, QHeaderView, QPlainTextEdit,
    QCheckBox, QTimeEdit, QComboBox, QProgressBar, QLineEdit, QMessageBox,
    QGroupBox, QScrollArea, QSpinBox, QSplitter,
)
from PyQt6.QtCore import Qt, QThreadPool, QTime, QElapsedTimer, QUrl
from PyQt6.QtGui import QFont, QColor, QDesktopServices

from ui.app_facts import bind_row

from core.app_facts import AppFactsIndex
from core.base_module import BaseModule
from ui.empty_state import EmptyState
from ui.table_style import apply_table_style
from .overview import UpdatesOverview
from core.system_paths import cmd_exe
from core.module_groups import ModuleGroup
from core.worker import Worker, COMWorker, retire
from core.windows_utils import is_reboot_pending
from core.blocklist import add_pattern, normalize_patterns
from core.events import NOTIFY_BALLOON, BalloonNotifyData
from core.table_ui import centered_item, center_header, set_role
from core.widget_life import widget_is_valid
from modules.updates.winget_updater import (
    fetch_updates, install_update, show_package_details, AppUpdate, UpdateOutcome,
)
from modules.updates.windows_updater import (
    fetch_pending_updates, install_updates_iter, hide_update,
    WindowsUpdate, InstallResult, size_text, severity_text, reboot_text,
    HELD_TEXT, wu_status_text, split_scan, update_status_text,
)
from modules.updates.store_updates_tab import _StoreUpdatesTab
from modules.updates.run_all_tab import _RunAllTab
from ui.error_banner import ErrorBanner
import logging
from core.semantic_colors import chrome, semantic
logger = logging.getLogger(__name__)


_widget_valid = widget_is_valid


UNATTENDED_TASK_NAME = "WinClientTool_UnattendedMaintenance"
LEGACY_TASK_NAME = "WinClientTool_UpdateCheck"


def _start_scan(tab, worker, on_result=None):
    """Only the current read may update controls or the displayed offers."""
    tab._scan_worker = worker
    for name in ("_install_btn", "_update_sel_btn", "_update_all_btn", "_refresh_btn"):
        button = getattr(tab, name, None)
        if button is not None:
            button.setEnabled(False)
    tab._empty_state.action_button.setEnabled(False)

    def deliver(callback, value):
        if not _widget_valid(tab) or tab._scan_worker is not worker:
            return
        tab._scan_worker = None
        tab._empty_state.action_button.setEnabled(not getattr(tab, "_installing", False))
        tab._refresh_btn.setEnabled(not getattr(tab, "_installing", False))
        installing = getattr(tab, "_installing", False)
        callback(value)
        if installing:
            tab._installing = True
            tab._empty_state.action_button.setEnabled(False)
            for name in ("_install_btn", "_update_sel_btn", "_update_all_btn", "_refresh_btn"):
                button = getattr(tab, name, None)
                if button is not None:
                    button.setEnabled(False)

    worker.signals.result.connect(lambda result: deliver(on_result or tab._on_updates, result))
    worker.signals.error.connect(lambda error: deliver(tab._on_error, error))
    worker.signals.cancelled.connect(lambda: deliver(tab._on_error, "Scan cancelled; check again to retry."))
    worker.signals.finished.connect(lambda: tab._workers.remove(worker) if worker in tab._workers else None)
    tab._workers.append(worker)
    QThreadPool.globalInstance().start(worker)


def _maybe_toast(app, timer: Optional[QElapsedTimer], title: str, message: str) -> None:
    """Publish a tray balloon if the operation ran longer than 20s and the
    user hasn't disabled toasts."""
    if app is None or timer is None:
        return
    if timer.elapsed() <= 20_000:
        return
    if not app.config.get("app.toast_on_long_ops", True):
        return
    app.event_bus.publish(NOTIFY_BALLOON, BalloonNotifyData(title=title, message=message))


# ---------------------------------------------------------------------------
# Tab 1 — Application Updates (winget)
# ---------------------------------------------------------------------------

class _AppUpdatesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self._updates: List[AppUpdate] = []
        self._loaded = False
        self._workers: List = []
        self._op_timer: Optional[QElapsedTimer] = None
        self._updates_listener = None
        self._setup_ui()

    def set_app(self, app) -> None:
        self.app = app

    def set_updates_listener(self, cb) -> None:
        """cb(updates: List[AppUpdate]) — called whenever the fetched list
        changes, so UpdatesModule can dispatch the msstore subset to the
        Store tab without a second winget call."""
        self._updates_listener = cb

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Toolbar
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._update_sel_btn = QPushButton("Update Selected")
        self._update_all_btn = QPushButton("Update All")
        self._details_btn = QPushButton("Package Details")
        self._block_btn = QPushButton("Add to Blocklist")
        self._update_sel_btn.setEnabled(False)
        self._update_all_btn.setEnabled(False)
        self._details_btn.setEnabled(False)
        self._block_btn.setEnabled(False)
        self._status_lbl = QLabel("Click Refresh to check for updates.")
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._update_sel_btn)
        toolbar.addWidget(self._update_all_btn)
        toolbar.addWidget(self._details_btn)
        toolbar.addWidget(self._block_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by name, id, or source…")
        filter_row.addWidget(self._filter_edit, 1)
        layout.addLayout(filter_row)

        # Progress bar (thin strip)
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Updates table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["Name", "ID", "Installed version", "Available", "Source", "Result"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        center_header(self._table)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        # Log output
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 8))

        # A drag handle between table and log, not a fixed 120px cap on the
        # log -- a user watching a long winget run had no way to see more
        # than a few lines of it, or to give the log space back to the table
        # once it was done.
        self._empty_state = EmptyState('check', "All up to date", "No offers were returned by the latest successful scan.", "Check again")
        self._empty_state.action_triggered.connect(self._do_refresh)
        self._empty_state.hide()
        layout.addWidget(self._empty_state, 1)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(self._log)
        splitter.setSizes([400, 120])
        layout.addWidget(splitter, 1)

        self._refresh_btn.clicked.connect(self._do_refresh)
        self._update_sel_btn.clicked.connect(self._do_update_selected)
        self._update_all_btn.clicked.connect(self._do_update_all)
        self._details_btn.clicked.connect(self._do_details)
        self._block_btn.clicked.connect(self._do_add_blocklist)
        self._filter_edit.textChanged.connect(self._apply_filter)

    def auto_scan(self):
        """Trigger a refresh if this tab hasn't been loaded yet."""
        if not self._loaded:
            self._loaded = True
            self._do_refresh()

    def _on_selection_changed(self) -> None:
        has_sel = bool(self._table.selectedIndexes())
        self._details_btn.setEnabled(has_sel)
        self._block_btn.setEnabled(has_sel)

    def _patterns(self) -> List[str]:
        return self.app.config.get("updates.blocklist_patterns", []) if self.app else []

    # ------------------------------------------------------------------
    # Worker functions — Worker passes itself as first argument, so all
    # callables passed to Worker() must accept a leading `worker` param.
    # ------------------------------------------------------------------

    def _do_refresh(self):
        if getattr(self, "_scan_worker", None) is not None or getattr(self, "_installing", False):
            return
        self._empty_state.action_button.setEnabled(False)
        self._empty_state.title_label.setText("Checking for updates")
        self._empty_state.set_variant("loading", progress_text="Reading available updates; no installation is started.")
        self._empty_state.show()
        self._table.hide()
        overview = getattr(self, "_overview", None)
        if overview is not None:
            overview.scan_failed("apps", "Scan in progress")
        AppFactsIndex.invalidate()
        self._loaded = True
        self._refresh_btn.setEnabled(False)
        self._update_sel_btn.setEnabled(False)
        self._update_all_btn.setEnabled(False)
        self._status_lbl.setText("Checking for updates...")
        self._progress.setRange(0, 0)
        self._progress.show()
        self._table.setRowCount(0)

        patterns = self._patterns()

        def _fetch(worker):
            return fetch_updates(patterns=patterns)

        w = Worker(_fetch)
        _start_scan(self, w)

    def _on_updates(self, updates: List[AppUpdate]):
        if not _widget_valid(self._table) or not _widget_valid(self._status_lbl):
            return
        self._updates = updates
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._render_table()
        self._status_lbl.setText(f"{len(updates)} update(s) available.")
        self._update_sel_btn.setEnabled(bool(updates) and not getattr(self, "_installing", False))
        self._update_all_btn.setEnabled(bool(updates) and not getattr(self, "_installing", False))
        if self._updates_listener:
            self._updates_listener(updates)

    def _render_table(self, results: Optional[dict] = None) -> None:
        apply_table_style(self._table)
        self._table.setRowCount(len(self._updates))
        for row, u in enumerate(self._updates):
            self._table.setItem(row, 0, centered_item(u.name))
            self._table.setItem(row, 1, centered_item(u.winget_id))
            self._table.setItem(row, 2, centered_item(u.installed_version))
            self._table.setItem(row, 3, centered_item(u.available_version))
            self._table.setItem(row, 4, centered_item(u.source))
            result_text = (results or {}).get(u.winget_id, "")
            result_item = centered_item(result_text)
            if result_text == "CONFIRMED":
                result_item.setForeground(QColor(semantic("success")))
            elif result_text:
                result_item.setForeground(QColor(semantic("warning")))
            self._table.setItem(row, 5, result_item)
            bind_row(self._table, row, u, safety=False)
        self._apply_filter(self._filter_edit.text())
        self._empty_state.title_label.setText("All up to date")
        self._empty_state.set_variant("empty")
        self._empty_state.setVisible(not self._updates)
        self._table.setVisible(bool(self._updates))
        overview = getattr(self, "_overview", None)
        if overview is not None:
            overview.set_apps(self._updates)

    def _apply_filter(self, text: str) -> None:
        text = (text or "").strip().lower()
        for row in range(self._table.rowCount()):
            if not text:
                self._table.setRowHidden(row, False)
                continue
            match = False
            for col in (0, 1, 4):
                item = self._table.item(row, col)
                if item is not None and text in item.text().lower():
                    match = True
                    break
            self._table.setRowHidden(row, not match)

    def _on_error(self, err_str: str):
        self._installing = False
        self._empty_state.action_button.setEnabled(True)
        self._empty_state.title_label.setText("Could not check updates")
        self._empty_state.set_variant("error", reason=err_str)
        self._empty_state.show()
        self._table.hide()
        overview = getattr(self, "_overview", None)
        if overview is not None:
            overview.scan_failed("apps", err_str)
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText(f"Error: {err_str}")

    def _do_update_selected(self):
        selected_rows = {i.row() for i in self._table.selectedIndexes()}
        if not selected_rows:
            return
        ids = [self._updates[r].winget_id for r in selected_rows if r < len(self._updates)]
        self._run_updates(ids)

    def _do_update_all(self):
        # Routed through the same per-item loop as "Update Selected" so every
        # package gets real progress and an isolated pass/fail, instead of one
        # opaque `winget upgrade --all` call.
        self._run_updates([u.winget_id for u in self._updates])

    def _run_updates(self, ids: List[str]):
        if getattr(self, "_installing", False) or getattr(self, "_scan_worker", None) is not None:
            return
        if not ids:
            return
        self._installing = True
        self._empty_state.action_button.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._update_sel_btn.setEnabled(False)
        self._update_all_btn.setEnabled(False)
        self._log.clear()
        self._progress.setRange(0, len(ids))
        self._progress.setValue(0)
        self._progress.show()
        self._op_timer = QElapsedTimer()
        self._op_timer.start()

        targets = {u.winget_id: u.available_version for u in self._updates if u.winget_id in ids}
        befores = {u.winget_id: u.installed_version for u in self._updates if u.winget_id in ids}
        names = {u.winget_id: u.name for u in self._updates if u.winget_id in ids}
        verify = bool(self.app.config.get("updates.verify_after_update", True)) if self.app else True

        def _run(worker):
            outcomes = {}
            stopped_reason = ""
            for i, wid in enumerate(ids):
                if stopped_reason:
                    outcomes[wid] = UpdateOutcome(wid, "SKIPPED", stopped_reason)
                    worker.signals.log_line.emit(f"SKIPPED {wid}: {stopped_reason}")
                    worker.signals.progress.emit(i + 1)
                    continue
                if worker.is_cancelled:
                    worker.signals.log_line.emit("Cancelled.")
                    break
                worker.signals.log_line.emit(f"Updating {wid}...")
                outcome = install_update(
                    wid, lambda line: worker.signals.log_line.emit(line),
                    is_cancelled=lambda: worker.is_cancelled,
                    target_version=targets.get(wid, ""),
                    before_version=befores.get(wid, ""),
                    package_name=names.get(wid, ""),
                    verify=verify,
                )
                worker.signals.log_line.emit(f"  {outcome.state}: {outcome.detail}")
                outcomes[wid] = outcome
                if outcome.state == "STILL RUNNING":
                    stopped_reason = outcome.detail
                worker.signals.progress.emit(i + 1)
            return outcomes

        w = Worker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.progress.connect(self._progress.setValue)
        w.signals.result.connect(self._on_update_done)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_update_done(self, outcomes: dict):
        self._installing = False
        self._empty_state.action_button.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._update_all_btn.setEnabled(True)
        self._progress.hide()
        confirmed = sum(1 for o in outcomes.values() if o)
        _maybe_toast(
            self.app, self._op_timer, "App updates finished",
            f"{confirmed}/{len(outcomes)} package(s) confirmed updated.",
        )
        if outcomes:
            self._verify_after(outcomes)
        else:
            self._do_refresh()

    def _verify_after(self, outcomes: dict) -> None:
        """Re-scan for the table only: each package's verdict already came from
        reading ITS installed version back (install_update), never from being
        absent from this list -- a failed re-scan would make every package look updated."""
        patterns = self._patterns()

        def _fetch(worker):
            return fetch_updates(patterns=patterns)

        w = Worker(_fetch)
        _start_scan(self, w, lambda after: self._on_verify_done(outcomes, after))

    def _on_verify_done(self, outcomes: dict, after_updates: List[AppUpdate]) -> None:
        results = {wid: ("CONFIRMED" if o else o.state) for wid, o in outcomes.items()}
        self._updates = after_updates
        self._render_table(results)
        confirmed = sum(1 for o in outcomes.values() if o)
        not_confirmed = [f"{wid}: {o.detail}" for wid, o in outcomes.items() if not o]
        self._status_lbl.setText(f"Verified — {confirmed}/{len(outcomes)} confirmed updated.")
        if not_confirmed:
            self._log.appendPlainText("\nNot confirmed:\n  " + "\n  ".join(not_confirmed))
        self._update_sel_btn.setEnabled(len(after_updates) > 0)
        self._update_all_btn.setEnabled(len(after_updates) > 0)
        if self._updates_listener:
            self._updates_listener(after_updates)

    def _do_details(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if not rows or rows[0] >= len(self._updates):
            return
        winget_id = self._updates[rows[0]].winget_id
        self._log.clear()
        self._log.appendPlainText(f"> winget show --id {winget_id} --exact\n")

        def _run(worker):
            show_package_details(winget_id, lambda line: worker.signals.log_line.emit(line))
            return None

        w = Worker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _do_add_blocklist(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if not rows or self.app is None or rows[0] >= len(self._updates):
            return
        u = self._updates[rows[0]]
        add_pattern(self.app, u.winget_id)
        self._status_lbl.setText(f"Blocked: {u.winget_id}")
        self._do_refresh()

    def _cancel_all(self) -> None:
        self._scan_worker = None
        retire(self._workers)


# ---------------------------------------------------------------------------
# Tab 2 — Windows Updates (COM)
# ---------------------------------------------------------------------------

class _WinUpdatesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self._updates: List[WindowsUpdate] = []
        #: AMD display drivers the hold keeps out: shown, never selectable.
        self._held: List[WindowsUpdate] = []
        self._loaded = False
        self._workers: List = []
        self._op_timer: Optional[QElapsedTimer] = None
        self._setup_ui()

    def set_app(self, app) -> None:
        self.app = app

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        # Reboot pending banner
        self._reboot_banner = QLabel("A system reboot is pending.")
        self._reboot_banner.setStyleSheet(
            "background: #FF8800; color: white; padding: 4px; font-weight: bold;"
        )
        self._reboot_banner.hide()
        layout.addWidget(self._reboot_banner)

        # Toolbar
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._install_btn = QPushButton("Install Selected")
        self._hide_btn = QPushButton("Hide Selected")
        self._block_btn = QPushButton("Add to Blocklist")
        self._history_btn = QPushButton("History")
        self._open_btn = QPushButton("Open Settings")
        self._install_btn.setEnabled(False)
        self._hide_btn.setEnabled(False)
        self._block_btn.setEnabled(False)
        self._status_lbl = QLabel("Click Refresh to check for Windows Updates.")
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._install_btn)
        toolbar.addWidget(self._hide_btn)
        toolbar.addWidget(self._block_btn)
        toolbar.addWidget(self._history_btn)
        toolbar.addWidget(self._open_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)

        opts_row = QHBoxLayout()
        self._chk_hidden = QCheckBox("Show hidden updates")
        opts_row.addWidget(self._chk_hidden)
        opts_row.addStretch()
        layout.addLayout(opts_row)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["KB", "Title", "Classification", "Severity", "Download",
             "Restart", "Released", "Result"]
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        center_header(self._table)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 8))

        self._empty_state = EmptyState('check', "All up to date", "No offers were returned by the latest successful scan.", "Check again")
        self._empty_state.action_triggered.connect(self._do_refresh)
        self._empty_state.hide()
        layout.addWidget(self._empty_state, 1)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(self._log)
        splitter.setSizes([400, 120])
        layout.addWidget(splitter, 1)

        self._refresh_btn.clicked.connect(self._do_refresh)
        self._install_btn.clicked.connect(self._do_install)
        self._hide_btn.clicked.connect(self._do_hide)
        self._block_btn.clicked.connect(self._do_add_blocklist)
        self._history_btn.clicked.connect(self._do_history)
        self._open_btn.clicked.connect(self._do_open_settings)
        self._chk_hidden.toggled.connect(lambda _c: self._do_refresh())

    def auto_scan(self):
        """Trigger a refresh if this tab hasn't been loaded yet."""
        if not self._loaded:
            self._loaded = True
            self._do_refresh()

        # Check reboot on init
        try:
            if is_reboot_pending():
                self._reboot_banner.show()
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)

    def _on_selection_changed(self) -> None:
        has_sel = bool(self._table.selectedIndexes())
        self._hide_btn.setEnabled(has_sel)
        self._block_btn.setEnabled(has_sel)

    def _patterns(self) -> List[str]:
        return self.app.config.get("updates.blocklist_patterns", []) if self.app else []

    def _do_refresh(self):
        if getattr(self, "_scan_worker", None) is not None or getattr(self, "_installing", False):
            return
        self._empty_state.action_button.setEnabled(False)
        self._empty_state.title_label.setText("Checking for updates")
        self._empty_state.set_variant("loading", progress_text="Reading available updates; no installation is started.")
        self._empty_state.show()
        self._table.hide()
        overview = getattr(self, "_overview", None)
        if overview is not None:
            overview.scan_failed("windows", "Scan in progress")
        self._loaded = True
        self._refresh_btn.setEnabled(False)
        self._install_btn.setEnabled(False)
        self._status_lbl.setText("Searching for updates...")
        self._progress.setRange(0, 0)
        self._progress.show()
        self._table.setRowCount(0)

        include_hidden = self._chk_hidden.isChecked()
        patterns = self._patterns()
        include_drivers = self.app.config.get("updates.wu_include_drivers", False) if self.app else False

        def _fetch(worker):
            held: List[WindowsUpdate] = []
            updates = fetch_pending_updates(
                include_hidden=include_hidden, patterns=patterns, include_drivers=include_drivers,
                held_out=held,
            )
            return {"updates": updates, "held": held}

        w = COMWorker(_fetch)
        _start_scan(self, w)

    def _on_updates(self, payload) -> None:
        if not _widget_valid(self._table) or not _widget_valid(self._status_lbl):
            return
        updates, held = split_scan(payload)
        self._updates = updates
        self._held = held
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._render_table()
        self._status_lbl.setText(wu_status_text(updates, held))
        self._install_btn.setEnabled(any(not u.pending_restart for u in updates) and not getattr(self, "_installing", False))
        try:
            self._reboot_banner.setVisible(is_reboot_pending())
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)

    def _render_table(self, results: Optional[dict] = None) -> None:
        apply_table_style(self._table, size_columns=(4,), date_columns=(6,))
        self._table.setRowCount(len(self._updates))
        for row, u in enumerate(self._updates):
            self._table.setItem(row, 0, centered_item(u.kb))
            title = u.title + ("  [hidden]" if u.is_hidden else "")
            self._table.setItem(row, 1, centered_item(title))
            self._table.setItem(row, 2, centered_item(u.classification))
            self._table.setItem(row, 3, centered_item(severity_text(u)))
            self._table.setItem(row, 4, centered_item(size_text(u)))
            self._table.setItem(row, 5, centered_item(reboot_text(u)))
            self._table.setItem(row, 6, centered_item(u.release_date))
            key = u.kb or u.title
            result_text = (results or {}).get(key, "") or update_status_text(u)
            result_item = centered_item(result_text)
            if result_text.startswith("OK"):
                result_item.setForeground(QColor(semantic("success")))
            elif result_text:
                result_item.setForeground(QColor("#e06c75"))
            self._table.setItem(row, 7, result_item)
            if u.pending_restart:
                for col in range(self._table.columnCount()):
                    self._table.item(row, col).setFlags(Qt.ItemFlag.ItemIsEnabled)
        self._render_held_rows()
        self._empty_state.title_label.setText("All up to date")
        self._empty_state.set_variant("empty")
        self._empty_state.setVisible(not self._updates and not self._held)
        self._table.setVisible(bool(self._updates or self._held))
        overview = getattr(self, "_overview", None)
        if overview is not None:
            overview.set_windows(self._updates, self._held)

    def _render_held_rows(self) -> None:
        """Held AMD display drivers go below the installable rows, greyed and
        NOT selectable -- Install/Hide/Blocklist act on selected rows only, so
        nothing on this tab can act on one."""
        base = len(self._updates)
        self._table.setRowCount(base + len(self._held))
        grey = QColor(chrome("text_muted"))
        for i, u in enumerate(self._held):
            row = base + i
            title = u.title + (" [hidden]" if u.is_hidden else "")
            cells = [u.kb, title, u.classification, severity_text(u), size_text(u),
                     reboot_text(u), u.release_date, HELD_TEXT]
            for col, text in enumerate(cells):
                item = centered_item(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)      # not selectable
                item.setForeground(grey)
                item.setToolTip(HELD_TEXT)
                self._table.setItem(row, col, item)

    def _on_error(self, err_str: str):
        self._installing = False
        self._empty_state.action_button.setEnabled(True)
        self._empty_state.title_label.setText("Could not check updates")
        self._empty_state.set_variant("error", reason=err_str)
        self._empty_state.show()
        self._table.hide()
        overview = getattr(self, "_overview", None)
        if overview is not None:
            overview.scan_failed("windows", err_str)
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText(f"Error: {err_str}")

    def _do_install(self):
        if getattr(self, "_installing", False) or getattr(self, "_scan_worker", None) is not None:
            return
        selected_rows = {i.row() for i in self._table.selectedIndexes()}
        if not selected_rows:
            return
        selected = [self._updates[r] for r in selected_rows if r < len(self._updates)
                    and not self._updates[r].pending_restart]
        if not selected:
            return

        from core.disk_space import check_wu_preflight
        from modules.updates.windows_updater import download_size_mb
        warnings = []
        reason = check_wu_preflight(download_size_mb(selected, warnings.append))
        if reason:
            self._status_lbl.setText(f"Blocked: {reason}")
            return

        self._refresh_btn.setEnabled(False)
        self._install_btn.setEnabled(False)
        self._log.clear()
        self._installing = True
        self._empty_state.action_button.setEnabled(False)
        for warning in warnings:
            self._log.appendPlainText(warning)
        self._progress.setRange(0, len(selected))
        self._progress.setValue(0)
        self._progress.show()
        self._op_timer = QElapsedTimer()
        self._op_timer.start()

        app = self.app

        def _run(worker):
            if app is not None and app.config.get("updates.restore_point_before_install", True):
                from core.system_restore import create_restore_point
                worker.signals.log_line.emit("Creating a System Restore point before installing…")
                ok, _out = create_restore_point("Windows Client Tool — before WU install")
                if not ok:
                    raise RuntimeError(
                        "Nothing was installed: the System Restore point could not be created "
                        "(System Restore off or blocked by policy?). Turn off 'Create a System Restore "
                        "point before installing Windows updates' in Settings to install without one.")
                worker.signals.log_line.emit("Restore point created.")

            def _progress(done, total, title):
                worker.signals.progress.emit(done)

            return install_updates_iter(
                selected,
                progress_cb=_progress,
                output_cb=lambda line: worker.signals.log_line.emit(line),
                is_cancelled=lambda: worker.is_cancelled,
            )

        w = COMWorker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.progress.connect(self._progress.setValue)
        w.signals.result.connect(self._on_install_done)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_install_done(self, results: List[InstallResult]):
        self._installing = False
        self._empty_state.action_button.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        installed = sum(1 for r in results if r.success)
        pending = sum(r.pending_restart for r in results)
        restart_note = f" Restart required to finish {pending} update(s)" if pending else ""
        self._status_lbl.setText(f"{installed}/{len(results)} installed." + restart_note)
        _maybe_toast(
            self.app, self._op_timer, "Windows Update finished",
            f"{installed}/{len(results)} update(s) installed." + restart_note,
        )

        if self.app is not None:
            from modules.updates.history_writer import append_run
            append_run(self.app.app_data_dir, freed_bytes=0, updates_installed=installed,
                       winget_count=0, pending_restart=pending)

        try:
            self._reboot_banner.setVisible(bool(pending) or is_reboot_pending())
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)

        if self.app and self.app.config.get("updates.verify_after_update", True) and results:
            self._verify_after(results)
        else:
            self._do_refresh()

    def _verify_after(self, results: List[InstallResult]) -> None:
        include_hidden = self._chk_hidden.isChecked()
        patterns = self._patterns()
        include_drivers = self.app.config.get("updates.wu_include_drivers", False) if self.app else False

        def _fetch(worker):
            held: List[WindowsUpdate] = []
            updates = fetch_pending_updates(
                include_hidden=include_hidden, patterns=patterns, include_drivers=include_drivers,
                held_out=held,
            )
            return {"updates": updates, "held": held}

        w = COMWorker(_fetch)
        _start_scan(self, w, lambda after: self._on_verify_done(results, after))

    def _on_verify_done(self, results: List[InstallResult], payload) -> None:
        if not _widget_valid(self._table) or not _widget_valid(self._status_lbl):
            return
        after_updates, held = split_scan(payload)
        self._held = held
        waiting = {(r.kb, r.title) for r in results if r.pending_restart}
        failed = {(r.kb, r.title) for r in results if r.result_code in (0, 1, 3, 4, 5)}
        for update in after_updates:
            if (update.kb, update.title) in failed:
                update.pending_restart = False
            elif (update.kb, update.title) in waiting:
                # The install result is newer evidence than a lagging scan.
                update.pending_restart = True
        present = {(u.kb, u.title) for u in after_updates}
        previous = {(u.kb, u.title): u for u in self._updates}
        for result in results:
            key = (result.kb, result.title)
            if key in waiting and key not in present:
                old = previous.get(key)
                retained = (replace(old, identity=None, pending_restart=True) if old else
                            WindowsUpdate(result.kb, result.title, "", 0, "",
                                          pending_restart=True))
                after_updates.append(retained)
                present.add(key)
        still_pending_titles = {u.title for u in after_updates}
        verify_map = {}
        for r in results:
            key = r.kb or r.title
            if r.result_code in (0, 1, 3, 4, 5):
                verify_map[key] = f"FAILED — {r.message}"
            elif r.pending_restart:
                verify_map[key] = "Installed -- restart to finish"
            elif not r.success:
                verify_map[key] = f"FAILED — {r.message}"
            elif r.title in still_pending_titles:
                verify_map[key] = "UNCHANGED — still pending"
            else:
                verify_map[key] = "OK — CONFIRMED"
        self._updates = after_updates
        self._render_table(verify_map)
        self._status_lbl.setText("Verified — " + wu_status_text(after_updates, held))
        self._install_btn.setEnabled(any(not u.pending_restart for u in after_updates))

    def _do_hide(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        targets = [self._updates[r] for r in rows if r < len(self._updates)]
        if not targets or self.app is None:
            return
        reply = QMessageBox.question(
            self, "Hide Updates",
            f"Hide {len(targets)} update(s)? They won't be offered again unless "
            "\"Show hidden updates\" is checked.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def _run(worker):
            done = 0
            for u in targets:
                try:
                    hide_update(u)
                    done += 1
                except Exception as e:
                    worker.signals.log_line.emit(f"Could not hide {u.title}: {e}")
            return done

        w = COMWorker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.result.connect(lambda _n: self._do_refresh())
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _do_add_blocklist(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if not rows or self.app is None or rows[0] >= len(self._updates):
            return
        u = self._updates[rows[0]]
        pattern = u.kb if u.kb and u.kb != "N/A" else u.title
        add_pattern(self.app, pattern)
        self._status_lbl.setText(f"Blocked: {pattern}")
        self._do_refresh()

    def _do_history(self) -> None:
        self._log.clear()
        self._log.appendPlainText("Fetching Windows Update history…")

        def _run(worker):
            import win32com.client
            from core.wu_error_codes import decode_wu_error

            session = win32com.client.Dispatch("Microsoft.Update.Session")
            searcher = session.CreateUpdateSearcher()
            total = searcher.GetTotalHistoryCount()
            take = min(30, total)
            lines = [f"Last {take} of {total} entries:"]
            result_text = {2: "OK", 3: "PARTIAL", 4: "FAILED", 5: "CANCELLED"}
            if take > 0:
                h = searcher.QueryHistory(0, take)
                for i in range(h.Count):
                    e = h.Item(i)
                    code = int(e.ResultCode)
                    r = result_text.get(code, f"code {code}")
                    line = f"  {e.Date}  {r}  {e.Title}"
                    if code == 4:
                        line += f"  ({decode_wu_error(e.HResult)})"
                    lines.append(line)
            return "\n".join(lines)

        w = COMWorker(_run)
        w.signals.result.connect(self._log.appendPlainText)
        w.signals.error.connect(lambda e: self._log.appendPlainText(f"Error: {e}"))
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _do_open_settings(self) -> None:
        try:
            subprocess.Popen(["explorer.exe", "ms-settings:windowsupdate"])
        except Exception as e:
            self._error_banner.set_error(f"Could not open Settings: {e}")

    def _cancel_all(self) -> None:
        self._scan_worker = None
        retire(self._workers)


# ---------------------------------------------------------------------------
# Tab 5 — Settings (blocklist, general options, scheduled maintenance)
# ---------------------------------------------------------------------------

class _UpdateSettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self._setup_ui()

    def set_app(self, app) -> None:
        self.app = app
        self._load()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        # -- General --
        general_box = QGroupBox("General")
        general_lay = QVBoxLayout(general_box)
        self._chk_verify = QCheckBox("Verify versions after updating and report confirmed/unchanged")
        self._chk_restore = QCheckBox("Create a System Restore point before installing Windows updates")
        self._chk_drivers = QCheckBox("Include driver updates from Windows Update")
        general_lay.addWidget(self._chk_verify)
        general_lay.addWidget(self._chk_restore)
        general_lay.addWidget(self._chk_drivers)
        layout.addWidget(general_box)

        # -- Blocklist --
        block_box = QGroupBox("Blocklist")
        block_lay = QVBoxLayout(block_box)
        block_desc = QLabel(
            "One entry per line: a winget package id (e.g. Microsoft.Teams) or text from a "
            "Windows Update title (e.g. Radeon). '*' works as a wildcard. Matching packages "
            "and updates never appear in scans and are never installed."
        )
        block_desc.setWordWrap(True)
        block_lay.addWidget(block_desc)
        self._block_edit = QPlainTextEdit()
        self._block_edit.setFont(QFont("Consolas", 9))
        self._block_edit.setFixedHeight(110)
        block_lay.addWidget(self._block_edit)
        block_btn_row = QHBoxLayout()
        self._block_save_btn = QPushButton("Save Blocklist")
        self._block_info_lbl = QLabel("")
        block_btn_row.addWidget(self._block_save_btn)
        block_btn_row.addWidget(self._block_info_lbl)
        block_btn_row.addStretch()
        block_lay.addLayout(block_btn_row)
        layout.addWidget(block_box)

        # -- Scheduled unattended maintenance --
        sched_box = QGroupBox("Scheduled Unattended Maintenance")
        sched_lay = QVBoxLayout(sched_box)
        sched_desc = QLabel(
            "Creates a Task Scheduler entry that runs this app headlessly (--unattended) "
            "with the checked stages, on a recurring schedule."
        )
        sched_desc.setWordWrap(True)
        sched_lay.addWidget(sched_desc)

        stages_row = QHBoxLayout()
        self._sched_wu = QCheckBox("Windows Update")
        self._sched_winget = QCheckBox("winget")
        self._sched_store = QCheckBox("Microsoft Store")
        self._sched_cleanup = QCheckBox("Cleanup (safe)")
        self._sched_dism = QCheckBox("DISM WinSxS")
        for c in (self._sched_wu, self._sched_winget, self._sched_store, self._sched_cleanup, self._sched_dism):
            stages_row.addWidget(c)
        stages_row.addStretch()
        sched_lay.addLayout(stages_row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Every"))
        self._sched_days = QSpinBox()
        self._sched_days.setRange(1, 90)
        self._sched_days.setValue(7)
        interval_row.addWidget(self._sched_days)
        interval_row.addWidget(QLabel("day(s) at"))
        self._sched_time = QTimeEdit(QTime(3, 0))
        interval_row.addWidget(self._sched_time)
        interval_row.addStretch()
        sched_lay.addLayout(interval_row)

        sched_btn_row = QHBoxLayout()
        self._sched_save_btn = QPushButton("Create / Update Task")
        self._sched_remove_btn = QPushButton("Remove Task")
        self._sched_run_btn = QPushButton("Run Now")
        self._sched_health_refresh_btn = QPushButton("Refresh Task Health")
        sched_btn_row.addWidget(self._sched_save_btn)
        sched_btn_row.addWidget(self._sched_remove_btn)
        sched_btn_row.addWidget(self._sched_run_btn)
        sched_btn_row.addWidget(self._sched_health_refresh_btn)
        sched_btn_row.addStretch()
        sched_lay.addLayout(sched_btn_row)
        self._sched_status_lbl = QLabel("")
        sched_lay.addWidget(self._sched_status_lbl)
        # Distinct from _sched_status_lbl above, which only ever echoes the
        # result of the button just clicked here (Task saved / removed /
        # started) and says nothing about whether a PAST scheduled run
        # actually succeeded. This is read fresh from `schtasks` itself —
        # see task_health.py for why that matters.
        self._task_health_lbl = QLabel("Task health: not checked yet.")
        self._task_health_lbl.setWordWrap(True)
        sched_lay.addWidget(self._task_health_lbl)
        self._task_copy_lbl = QLabel("")
        self._task_copy_lbl.setWordWrap(True)
        sched_lay.addWidget(self._task_copy_lbl)
        self._task_copy_btn = QPushButton("Update task to this copy")
        self._task_copy_btn.hide()
        self._task_copy_btn.clicked.connect(self._save_schedule)
        sched_lay.addWidget(self._task_copy_btn)
        self._latest_report_lbl = QLabel("")
        self._latest_report_lbl.linkActivated.connect(lambda link: QDesktopServices.openUrl(QUrl(link)))
        sched_lay.addWidget(self._latest_report_lbl)
        layout.addWidget(sched_box)

        # -- Legacy quick winget-only schedule (kept as-is for existing users) --
        legacy_box = QGroupBox("Quick winget Auto-Update (legacy)")
        legacy_lay = QVBoxLayout(legacy_box)
        legacy_desc = QLabel("A simpler task that silently runs 'winget upgrade --all' on a schedule.")
        legacy_desc.setWordWrap(True)
        legacy_lay.addWidget(legacy_desc)
        legacy_row = QHBoxLayout()
        legacy_row.addWidget(QLabel("Time:"))
        self._legacy_time = QTimeEdit(QTime(9, 0))
        legacy_row.addWidget(self._legacy_time)
        legacy_row.addWidget(QLabel("Frequency:"))
        self._legacy_freq = QComboBox()
        self._legacy_freq.addItems(["Daily", "Weekly"])
        legacy_row.addWidget(self._legacy_freq)
        legacy_row.addStretch()
        legacy_lay.addLayout(legacy_row)
        legacy_btn_row = QHBoxLayout()
        self._legacy_save_btn = QPushButton("Save")
        self._legacy_remove_btn = QPushButton("Remove")
        legacy_btn_row.addWidget(self._legacy_save_btn)
        legacy_btn_row.addWidget(self._legacy_remove_btn)
        legacy_btn_row.addStretch()
        legacy_lay.addLayout(legacy_btn_row)
        self._legacy_status_lbl = QLabel("")
        legacy_lay.addWidget(self._legacy_status_lbl)
        layout.addWidget(legacy_box)

        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self._chk_verify.toggled.connect(lambda v: self._set_cfg("updates.verify_after_update", v))
        self._chk_restore.toggled.connect(lambda v: self._set_cfg("updates.restore_point_before_install", v))
        self._chk_drivers.toggled.connect(lambda v: self._set_cfg("updates.wu_include_drivers", v))
        self._block_save_btn.clicked.connect(self._save_blocklist)
        self._sched_save_btn.clicked.connect(self._save_schedule)
        self._sched_remove_btn.clicked.connect(self._remove_schedule)
        self._sched_run_btn.clicked.connect(self._run_schedule_now)
        self._sched_health_refresh_btn.clicked.connect(self._refresh_task_health)
        self._legacy_save_btn.clicked.connect(self._save_legacy)
        self._legacy_remove_btn.clicked.connect(self._remove_legacy)

    def _set_cfg(self, key: str, value) -> None:
        if self.app is not None:
            self.app.config.set(key, value)
            # Explicit flush (not just the debounced autosave) — these settings
            # can be read moments later by a separate --unattended process
            # (via "Run Now" or the scheduled task itself), which loads
            # config.json fresh from disk and would otherwise race the
            # in-process autosave timer.
            self.app.config.save()

    def _load(self) -> None:
        if self.app is None:
            return
        self._chk_verify.setChecked(self.app.config.get("updates.verify_after_update", True))
        self._chk_restore.setChecked(self.app.config.get("updates.restore_point_before_install", True))
        self._chk_drivers.setChecked(self.app.config.get("updates.wu_include_drivers", False))

        patterns = self.app.config.get("updates.blocklist_patterns", [])
        self._block_edit.setPlainText("\n".join(patterns))
        self._block_info_lbl.setText(f"{len(patterns)} pattern(s) saved.")

        stages = set(self.app.config.get("updates.unattended_stages", ["wu", "winget", "cleanup"]))
        self._sched_wu.setChecked("wu" in stages)
        self._sched_winget.setChecked("winget" in stages)
        self._sched_store.setChecked("store" in stages)
        self._sched_cleanup.setChecked("cleanup" in stages)
        self._sched_dism.setChecked("dism" in stages)
        self._sched_days.setValue(int(self.app.config.get("updates.unattended_interval_days", 7)))
        time_str = self.app.config.get("updates.unattended_time", "03:00")
        try:
            h, m = (int(x) for x in time_str.split(":"))
            self._sched_time.setTime(QTime(h, m))
        except Exception:
            logger.warning("Ignored bad saved unattended time %r", time_str)

        self._refresh_task_health()

    def _refresh_task_health(self) -> None:
        """Re-read the scheduled task's actual last-run outcome from
        `schtasks` and reflect it in `_task_health_lbl`. Cheap enough
        (~tens of ms, measured) to run synchronously on the UI thread, same
        as every other schtasks call already in this class."""
        from modules.updates.task_health import check_task_health, task_copy_mismatch, latest_report
        health = check_task_health(UNATTENDED_TASK_NAME)
        self._task_copy_btn.hide()
        self._task_copy_lbl.setText(f"Task executable: {health.exe_path or 'unreadable / not registered'}")
        if task_copy_mismatch(health, sys.executable):
            set_role(self._task_copy_lbl, "statusWarning")
            self._task_copy_lbl.setText(f"Warning: task runs another copy: {health.exe_path}\n"
                                       f"This copy: {sys.executable}")
            self._task_copy_btn.show()
        report = latest_report(self.app.app_data_dir) if self.app else None
        self._latest_report_lbl.setText(
            f'<a href="{html.escape(QUrl.fromLocalFile(report).toString(), quote=True)}">Latest maintenance report</a>'
            if report else "Latest maintenance report: none yet")

        if health.exists is None:
            set_role(self._task_health_lbl, "statusWarning")
            self._task_health_lbl.setText(
                f"Task health: could not be checked — {health.error}"
            )
            return
        if not health.exists:
            set_role(self._task_health_lbl, "statusInfo")
            self._task_health_lbl.setText(
                "Task health: no scheduled task exists yet — click Create / Update Task."
            )
            return

        state_text = (
            "Enabled" if health.enabled else "Disabled" if health.enabled is False else "unknown state"
        )
        if health.last_run:
            run_text = f"last ran {health.last_run} — {health.last_result_text or 'unknown result'}"
        else:
            run_text = "has never run"
        next_text = f"next run {health.next_run}" if health.next_run else "no next run scheduled"
        self._task_health_lbl.setText(f"Task health: {state_text}; {run_text}; {next_text}.")

        failed_last_run = (
            health.last_run is not None
            and health.last_result_code not in (None, 0)
        )
        if failed_last_run:
            set_role(self._task_health_lbl, "statusError")
        elif health.enabled is False:
            set_role(self._task_health_lbl, "statusWarning")
        else:
            set_role(self._task_health_lbl, "statusSuccess")

    def _save_blocklist(self) -> None:
        if self.app is None:
            return
        patterns = normalize_patterns(self._block_edit.toPlainText())
        self.app.config.set("updates.blocklist_patterns", patterns)
        # Flush now — unattended_runner.py may read this from a fresh process.
        if self.app.config.save() is False:
            self._block_info_lbl.setText(
                f"NOT saved — the settings file could not be written (see the log). "
                f"{len(patterns)} pattern(s) apply to this session only; the scheduled "
                "maintenance run still uses the old list.")
            return
        self._block_info_lbl.setText(f"Saved — {len(patterns)} pattern(s).")

    def _selected_unattended_stages(self) -> List[str]:
        stages = []
        if self._sched_wu.isChecked():
            stages.append("wu")
        if self._sched_winget.isChecked():
            stages.append("winget")
        if self._sched_store.isChecked():
            stages.append("store")
        if self._sched_cleanup.isChecked():
            stages.append("cleanup")
        if self._sched_dism.isChecked():
            stages.append("dism")
        return stages

    def _save_schedule(self) -> None:
        if self.app is None:
            return
        stages = self._selected_unattended_stages()
        if not stages:
            self._sched_status_lbl.setText("Select at least one stage.")
            return
        days = self._sched_days.value()
        time_str = self._sched_time.time().toString("HH:mm")

        self.app.config.set("updates.unattended_stages", stages)
        self.app.config.set("updates.unattended_interval_days", days)
        self.app.config.set("updates.unattended_time", time_str)
        # Flush now, before we create/run the task below — schtasks may invoke
        # --unattended (a separate process reading config.json from disk)
        # well before the 2s autosave debounce would otherwise fire.
        self.app.config.save()

        if getattr(sys, "frozen", False):
            exe_cmd = f'"{sys.executable}"'
        else:
            script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "main.py"))
            exe_cmd = f'"{sys.executable}" "{script}"'
        task_cmd = [
            "schtasks", "/create", "/f",
            "/tn", UNATTENDED_TASK_NAME,
            "/tr", f'{exe_cmd} --unattended --stages {",".join(stages)}',
            "/sc", "DAILY", "/mo", str(days),
            "/st", time_str, "/rl", "HIGHEST",
        ]
        try:
            result = subprocess.run(
                task_cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            if result.returncode == 0:
                self._sched_status_lbl.setText(f"Task saved — every {days} day(s) at {time_str}.")
            else:
                self._sched_status_lbl.setText(f"Error: {result.stderr.strip()}")
        except Exception as e:
            self._sched_status_lbl.setText(f"Error: {e}")
        self._refresh_task_health()

    def _remove_schedule(self) -> None:
        try:
            result = subprocess.run(
                ["schtasks", "/delete", "/f", "/tn", UNATTENDED_TASK_NAME],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            self._sched_status_lbl.setText(
                "Task removed." if result.returncode == 0 else f"Error: {result.stderr.strip()}"
            )
        except Exception as e:
            self._sched_status_lbl.setText(f"Error: {e}")
        self._refresh_task_health()

    def _run_schedule_now(self) -> None:
        try:
            result = subprocess.run(
                ["schtasks", "/run", "/tn", UNATTENDED_TASK_NAME],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            self._sched_status_lbl.setText(
                "Task started." if result.returncode == 0 else f"Error: {result.stderr.strip()}"
            )
        except Exception as e:
            self._sched_status_lbl.setText(f"Error: {e}")

    def _save_legacy(self) -> None:
        time_str = self._legacy_time.time().toString("HH:mm")
        freq = self._legacy_freq.currentText().upper()
        script = (
            "winget upgrade --all --silent "
            "--accept-source-agreements --accept-package-agreements"
        )
        cmd = [
            "schtasks", "/create", "/f",
            "/tn", LEGACY_TASK_NAME,
            "/tr", f'"{cmd_exe()}" /c "{script}"',
            "/sc", freq,
            "/st", time_str,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            self._legacy_status_lbl.setText(
                "Schedule saved." if result.returncode == 0 else f"Error: {result.stderr.strip()}"
            )
        except Exception as e:
            self._legacy_status_lbl.setText(f"Error: {e}")

    def _remove_legacy(self) -> None:
        cmd = ["schtasks", "/delete", "/f", "/tn", LEGACY_TASK_NAME]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            self._legacy_status_lbl.setText(
                "Schedule removed." if result.returncode == 0 else f"Error: {result.stderr.strip()}"
            )
        except Exception as e:
            self._legacy_status_lbl.setText(f"Error: {e}")


# ---------------------------------------------------------------------------
# UpdatesModule — BaseModule entry point
# ---------------------------------------------------------------------------

class UpdatesModule(BaseModule):
    name = "Updates"
    icon = "🔄"
    description = "Windows Update, winget, and Microsoft Store update management"
    requires_admin = True
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._app_tab: Optional[_AppUpdatesTab] = None
        self._win_tab: Optional[_WinUpdatesTab] = None
        self._store_tab: Optional[_StoreUpdatesTab] = None
        self._runall_tab: Optional[_RunAllTab] = None
        self._settings_tab: Optional[_UpdateSettingsTab] = None
        self._tabs: Optional[QTabWidget] = None

    def create_widget(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        self._overview = UpdatesOverview()
        layout.addWidget(self._overview)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        self._app_tab = _AppUpdatesTab()
        self._app_tab.set_app(self.app)
        self._app_tab.set_updates_listener(self._on_app_updates)

        self._win_tab = _WinUpdatesTab()
        self._win_tab.set_app(self.app)
        self._app_tab._overview = self._overview
        self._win_tab._overview = self._overview

        self._store_tab = _StoreUpdatesTab()

        self._runall_tab = _RunAllTab()
        self._runall_tab.set_app(self.app)

        self._settings_tab = _UpdateSettingsTab()
        self._settings_tab.set_app(self.app)

        self._tabs.addTab(self._app_tab, "App Updates")
        self._tabs.addTab(self._win_tab, "Windows Updates")
        self._tabs.addTab(self._store_tab, "Microsoft Store")
        self._tabs.addTab(self._runall_tab, "Run All")
        self._tabs.addTab(self._settings_tab, "Settings")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        for key, index in (("apps", 0), ("windows", 1), ("held", 1), ("restart", 1), ("last", 4), ("task", 4)):
            self._overview.tiles_by_key[key].clicked.connect(lambda i=index: self._tabs.setCurrentIndex(i))
        from modules.cleanup.help_ui import explain_panes
        from .help_text import CONTROLS, HEADERS, FIELDS, PANES
        explain_panes(self._tabs, CONTROLS, HEADERS, FIELDS, PANES)
        return root

    def _refresh_overview_health(self):
        if getattr(self, "_health_worker", None) is not None or not hasattr(self, "_overview"):
            return
        worker = Worker(self._read_overview_health)
        self._health_worker = worker

        def received(result):
            if _widget_valid(self._overview) and self._health_worker is worker:
                self._overview.set_health(*result)

        def failed(message):
            logger.warning("Update overview health read failed: %s", message)
            if _widget_valid(self._overview) and self._health_worker is worker:
                for key in ("task", "last", "restart"):
                    self._overview.tiles_by_key[key].set_unreadable(str(message))

        def finished():
            if self._health_worker is worker:
                self._health_worker = None

        worker.signals.result.connect(received)
        worker.signals.error.connect(failed)
        worker.signals.finished.connect(finished)
        self._workers.append(worker)
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        pool = getattr(self.app, "thread_pool", None) or self.thread_pool
        pool.start(worker)

    @staticmethod
    def _read_overview_health(worker):
        from .task_health import check_task_health
        health = check_task_health(UNATTENDED_TASK_NAME)
        reason = ""
        try:
            from core.pending_reboot import check
            pending = check()
            restart = pending.needed if pending.needed or not pending.unreadable else None
            reason = "; ".join(pending.unreadable)
        except Exception as exc:
            logger.warning("Could not read pending restart: %s", exc)
            restart, reason = None, str(exc)
        return health, restart, reason

    def _on_app_updates(self, updates: List[AppUpdate]) -> None:
        if self._store_tab is not None:
            self._store_tab.set_updates(updates)

    def on_activate(self) -> None:
        self._refresh_overview_health()
        self._on_tab_changed(self._tabs.currentIndex() if self._tabs else 0)

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self._app_tab.auto_scan()
        elif index == 1:
            self._win_tab.auto_scan()
        elif index == 2:
            # Store tab piggybacks on the shared winget scan — no separate call.
            self._app_tab.auto_scan()
        elif index == 4:
            # Settings' task-health line is a live read, not a cache — refresh
            # it every time the tab becomes visible, not just at module build.
            self._settings_tab._refresh_task_health()

    def on_deactivate(self) -> None:
        # Leaving the pane cancels nothing: a cancel used to reach winget as
        # `taskkill /T /F` in the middle of an install. Scans and installs run
        # on until they finish; Run All has Stop (before the next package), and
        # app exit (on_stop) stops the rest between packages.
        pass

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()
        for tab in (self._app_tab, self._win_tab, self._store_tab, self._runall_tab):
            if tab is not None:
                tab._cancel_all()
