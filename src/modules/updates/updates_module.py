import os
import subprocess
import sys
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTabWidget,
    QTableWidget, QHeaderView, QPlainTextEdit,
    QCheckBox, QTimeEdit, QComboBox, QProgressBar, QLineEdit, QMessageBox,
    QGroupBox, QScrollArea, QSpinBox,
)
from PyQt6.QtCore import QThreadPool, QTime, QElapsedTimer
from PyQt6.QtGui import QFont, QColor

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker, COMWorker
from core.windows_utils import is_reboot_pending
from core.blocklist import add_pattern, normalize_patterns
from core.events import NOTIFY_BALLOON, BalloonNotifyData
from core.table_ui import centered_item, center_header
from core.widget_life import widget_is_valid
from modules.updates.winget_updater import (
    fetch_updates, install_update, show_package_details, AppUpdate,
)
from modules.updates.windows_updater import (
    fetch_pending_updates, install_updates_iter, hide_update,
    WindowsUpdate, InstallResult,
)
from modules.updates.store_updates_tab import _StoreUpdatesTab
from modules.updates.run_all_tab import _RunAllTab
from ui.error_banner import ErrorBanner
import logging
from core.semantic_colors import semantic
logger = logging.getLogger(__name__)


_widget_valid = widget_is_valid


UNATTENDED_TASK_NAME = "WinClientTool_UnattendedMaintenance"
LEGACY_TASK_NAME = "WinClientTool_UpdateCheck"


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
        self._table.setHorizontalHeaderLabels(["Name", "ID", "Installed", "Available", "Source", "Result"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        center_header(self._table)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table, 1)

        # Log output
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setFont(QFont("Consolas", 8))
        layout.addWidget(self._log)

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
        w.signals.result.connect(self._on_updates)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_updates(self, updates: List[AppUpdate]):
        if not _widget_valid(self._table) or not _widget_valid(self._status_lbl):
            return
        self._updates = updates
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._render_table()
        self._status_lbl.setText(f"{len(updates)} update(s) available.")
        self._update_sel_btn.setEnabled(len(updates) > 0)
        self._update_all_btn.setEnabled(len(updates) > 0)
        if self._updates_listener:
            self._updates_listener(updates)

    def _render_table(self, results: Optional[dict] = None) -> None:
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
            elif result_text == "UNCHANGED":
                result_item.setForeground(QColor(semantic("warning")))
            self._table.setItem(row, 5, result_item)
        self._apply_filter(self._filter_edit.text())

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
        if not ids:
            return
        self._refresh_btn.setEnabled(False)
        self._update_sel_btn.setEnabled(False)
        self._update_all_btn.setEnabled(False)
        self._log.clear()
        self._progress.setRange(0, len(ids))
        self._progress.setValue(0)
        self._progress.show()
        self._op_timer = QElapsedTimer()
        self._op_timer.start()

        before_map = {u.winget_id: u.installed_version for u in self._updates if u.winget_id in ids}

        def _run(worker):
            for i, wid in enumerate(ids):
                if worker.is_cancelled:
                    worker.signals.log_line.emit("Cancelled.")
                    break
                worker.signals.log_line.emit(f"Updating {wid}...")
                install_update(
                    wid, lambda line: worker.signals.log_line.emit(line),
                    is_cancelled=lambda: worker.is_cancelled,
                )
                worker.signals.progress.emit(i + 1)
            return before_map

        w = Worker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.progress.connect(self._progress.setValue)
        w.signals.result.connect(self._on_update_done)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_update_done(self, before_map: dict):
        self._refresh_btn.setEnabled(True)
        self._update_all_btn.setEnabled(True)
        self._progress.hide()
        _maybe_toast(
            self.app, self._op_timer, "App updates finished",
            f"{len(before_map)} package(s) processed.",
        )
        if self.app and self.app.config.get("updates.verify_after_update", True) and before_map:
            self._verify_after(before_map)
        else:
            self._do_refresh()

    def _verify_after(self, before_map: dict) -> None:
        patterns = self._patterns()

        def _fetch(worker):
            return fetch_updates(patterns=patterns)

        w = Worker(_fetch)
        w.signals.result.connect(lambda after: self._on_verify_done(before_map, after))
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_verify_done(self, before_map: dict, after_updates: List[AppUpdate]) -> None:
        after_ids = {u.winget_id for u in after_updates}
        results = {wid: ("UNCHANGED" if wid in after_ids else "CONFIRMED") for wid in before_map}
        self._updates = after_updates
        self._render_table(results)
        confirmed = sum(1 for v in results.values() if v == "CONFIRMED")
        self._status_lbl.setText(f"Verified — {confirmed}/{len(results)} confirmed updated.")
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
        for w in self._workers:
            w.cancel()
        self._workers.clear()


# ---------------------------------------------------------------------------
# Tab 2 — Windows Updates (COM)
# ---------------------------------------------------------------------------

class _WinUpdatesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self._updates: List[WindowsUpdate] = []
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

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["KB", "Title", "Classification", "Size (MB)", "Released", "Result"]
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        center_header(self._table)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table, 1)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setFont(QFont("Consolas", 8))
        layout.addWidget(self._log)

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
        self._loaded = True
        self._refresh_btn.setEnabled(False)
        self._install_btn.setEnabled(False)
        self._status_lbl.setText("Searching for updates...")
        self._progress.setRange(0, 0)
        self._progress.show()
        self._table.setRowCount(0)

        include_hidden = self._chk_hidden.isChecked()
        patterns = self._patterns()

        def _fetch(worker):
            return fetch_pending_updates(include_hidden=include_hidden, patterns=patterns)

        w = COMWorker(_fetch)
        w.signals.result.connect(self._on_updates)
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_updates(self, updates: List[WindowsUpdate]):
        if not _widget_valid(self._table) or not _widget_valid(self._status_lbl):
            return
        self._updates = updates
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._render_table()
        self._status_lbl.setText(f"{len(updates)} pending update(s).")
        self._install_btn.setEnabled(len(updates) > 0)
        try:
            self._reboot_banner.setVisible(is_reboot_pending())
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)

    def _render_table(self, results: Optional[dict] = None) -> None:
        self._table.setRowCount(len(self._updates))
        for row, u in enumerate(self._updates):
            self._table.setItem(row, 0, centered_item(u.kb))
            title = u.title + ("  [hidden]" if u.is_hidden else "")
            self._table.setItem(row, 1, centered_item(title))
            self._table.setItem(row, 2, centered_item(u.classification))
            self._table.setItem(row, 3, centered_item(f"{u.size_mb:.1f}"))
            self._table.setItem(row, 4, centered_item(u.release_date))
            key = u.kb or u.title
            result_text = (results or {}).get(key, "")
            result_item = centered_item(result_text)
            if result_text.startswith("OK"):
                result_item.setForeground(QColor(semantic("success")))
            elif result_text:
                result_item.setForeground(QColor("#e06c75"))
            self._table.setItem(row, 5, result_item)

    def _on_error(self, err_str: str):
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText(f"Error: {err_str}")

    def _do_install(self):
        selected_rows = {i.row() for i in self._table.selectedIndexes()}
        if not selected_rows:
            return
        selected = [self._updates[r] for r in selected_rows if r < len(self._updates)]

        from core.disk_space import check_wu_preflight
        reason = check_wu_preflight(sum(u.size_mb for u in selected))
        if reason:
            self._status_lbl.setText(f"Blocked: {reason}")
            return

        self._refresh_btn.setEnabled(False)
        self._install_btn.setEnabled(False)
        self._log.clear()
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
                worker.signals.log_line.emit(
                    "Restore point created." if ok else "Restore point creation failed or was skipped."
                )

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
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        installed = sum(1 for r in results if r.success)
        self._status_lbl.setText(f"{installed}/{len(results)} installed.")
        _maybe_toast(
            self.app, self._op_timer, "Windows Update finished",
            f"{installed}/{len(results)} update(s) installed.",
        )

        if self.app is not None:
            from modules.updates.history_writer import append_run
            append_run(self.app.app_data_dir, freed_bytes=0, updates_installed=installed, winget_count=0)

        try:
            self._reboot_banner.setVisible(is_reboot_pending())
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)

        if self.app and self.app.config.get("updates.verify_after_update", True) and results:
            self._verify_after(results)
        else:
            self._do_refresh()

    def _verify_after(self, results: List[InstallResult]) -> None:
        include_hidden = self._chk_hidden.isChecked()
        patterns = self._patterns()

        def _fetch(worker):
            return fetch_pending_updates(include_hidden=include_hidden, patterns=patterns)

        w = COMWorker(_fetch)
        w.signals.result.connect(lambda after: self._on_verify_done(results, after))
        w.signals.error.connect(self._on_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_verify_done(self, results: List[InstallResult], after_updates: List[WindowsUpdate]) -> None:
        still_pending_titles = {u.title for u in after_updates}
        verify_map = {}
        for r in results:
            key = r.kb or r.title
            if not r.success:
                verify_map[key] = f"FAILED — {r.message}"
            elif r.title in still_pending_titles:
                verify_map[key] = "UNCHANGED — still pending"
            else:
                verify_map[key] = "OK — CONFIRMED"
        self._updates = after_updates
        self._render_table(verify_map)
        self._status_lbl.setText(f"Verified — {len(after_updates)} update(s) still pending.")
        self._install_btn.setEnabled(len(after_updates) > 0)

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
        for w in self._workers:
            w.cancel()
        self._workers.clear()


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
        sched_btn_row.addWidget(self._sched_save_btn)
        sched_btn_row.addWidget(self._sched_remove_btn)
        sched_btn_row.addWidget(self._sched_run_btn)
        sched_btn_row.addStretch()
        sched_lay.addLayout(sched_btn_row)
        self._sched_status_lbl = QLabel("")
        sched_lay.addWidget(self._sched_status_lbl)
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
        self._chk_drivers.setChecked(self.app.config.get("updates.wu_include_drivers", True))

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

    def _save_blocklist(self) -> None:
        if self.app is None:
            return
        patterns = normalize_patterns(self._block_edit.toPlainText())
        self.app.config.set("updates.blocklist_patterns", patterns)
        self.app.config.save()  # flush now — unattended_runner.py may read this from a fresh process
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
            "/tr", f'cmd /c "{script}"',
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
        self._tabs = QTabWidget()

        self._app_tab = _AppUpdatesTab()
        self._app_tab.set_app(self.app)
        self._app_tab.set_updates_listener(self._on_app_updates)

        self._win_tab = _WinUpdatesTab()
        self._win_tab.set_app(self.app)

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
        return self._tabs

    def _on_app_updates(self, updates: List[AppUpdate]) -> None:
        if self._store_tab is not None:
            self._store_tab.set_updates(updates)

    def on_activate(self) -> None:
        self._on_tab_changed(self._tabs.currentIndex() if self._tabs else 0)

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self._app_tab.auto_scan()
        elif index == 1:
            self._win_tab.auto_scan()
        elif index == 2:
            # Store tab piggybacks on the shared winget scan — no separate call.
            self._app_tab.auto_scan()

    def on_deactivate(self) -> None:
        for tab in (self._app_tab, self._win_tab, self._store_tab, self._runall_tab):
            if tab is not None:
                tab._cancel_all()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()
        for tab in (self._app_tab, self._win_tab, self._store_tab, self._runall_tab):
            if tab is not None:
                tab._cancel_all()
