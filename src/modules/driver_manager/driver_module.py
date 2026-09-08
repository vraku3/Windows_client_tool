import csv
import datetime
import logging
import os
import subprocess
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QHeaderView, QLineEdit, QLabel,
    QProgressBar, QFileDialog, QCheckBox, QApplication, QMenu,
    QStackedWidget, QComboBox,
)
from PyQt6.QtCore import Qt, QThreadPool, QItemSelectionModel
from PyQt6.QtGui import QColor

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.events import NAV_REQUEST_MODULE, NavRequestData
from core.module_groups import ModuleGroup
from core.search_provider import SearchProvider
from core.semantic_colors import semantic
from core.table_ui import (
    centered_item, center_header, fit_columns_once, NumericSortItem,
    restore_column_widths, save_column_widths,
)
from core.widget_life import widget_is_valid
from core.worker import COMWorker, Worker
from modules.driver_manager.driver_reader import (
    DriverInfo, classify_provider, fetch_drivers, published_name_for,
    _PSEUDO_CLASSES,
)
from modules.driver_manager.driver_search_provider import DriverSearchProvider
from ui.empty_state import EmptyState

logger = logging.getLogger(__name__)

COLUMNS = ["Device Name", "Class", "Version", "Date", "Publisher", "Provider", "Signed", "Status"]

FLAG_FILTER_OPTIONS = ["All", "Signed only", "Unsigned only", "Has error", "Old"]


def _date_sort_value(date_str: str) -> float:
    if not date_str:
        return 0.0
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").timestamp()
    except ValueError:
        return 0.0
    except OSError:
        # .timestamp() itself raises on a validly-parsed but out-of-range
        # date -- e.g. 1601-01-01, the CIM_DATETIME/FILETIME zero-epoch
        # sentinel WMI can report for a device with no genuine driver
        # date. strptime succeeded; the crash is in the conversion after
        # it, so a bare `except ValueError` around strptime doesn't catch
        # it. Group it with the unparseable case rather than crashing
        # _populate() (which runs on every refresh, filter keystroke,
        # checkbox toggle and flag-combo change).
        return 0.0


class DriverModule(BaseModule):
    name = "Driver Manager"
    icon = "🖨️"
    description = "View and manage installed drivers"
    #: Reading and browsing is always available with no elevation; the one
    #: destructive action (uninstalling a driver package) calls
    #: require_admin() itself and is refused with a message pointing at the
    #: "Restart as Admin" banner rather than failing silently.
    requires_admin = False
    group = ModuleGroup.SYSTEM

    #: D23 sort-column persistence key prefix. Mirrors DebloatModule's
    #: `_CONFIG_PREFIX` pattern (src/modules/debloat/debloat_module.py).
    _CONFIG_PREFIX = "modules.driver_manager"

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._table: Optional[QTableWidget] = None
        self._table_stack: Optional[QStackedWidget] = None
        self._empty: Optional[EmptyState] = None
        self._progress: Optional[QProgressBar] = None
        self._status_lbl: Optional[QLabel] = None
        self._filter_edit: Optional[QLineEdit] = None
        self._flag_filter_combo: Optional[QComboBox] = None
        self._select_flagged_btn: Optional[QPushButton] = None
        self._hide_pseudo_cb: Optional[QCheckBox] = None
        self._refresh_btn: Optional[QPushButton] = None
        self._export_btn: Optional[QPushButton] = None
        self._cancel_backup_btn: Optional[QPushButton] = None
        self._backup_worker: Optional[Worker] = None
        # [list of DriverInfo] -- a single-element cell, not reassigned after
        # this, so DriverSearchProvider (built once in get_search_provider(),
        # itself called from on_start() before create_widget() ever runs) can
        # hold this same object and still see every future refresh's data.
        self._drivers_ref = [[]]
        self._sort_col: int = -1
        #: C07 follow-up: has `_populate()` already run its one-time
        #: resizeColumnsToContents() fit this session? See `_populate()`.
        self._columns_fitted_this_session = False

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._export_btn = QPushButton("Export CSV")
        devmgr_btn = QPushButton("Open Device Manager")
        wu_btn = QPushButton("Check Windows Update")
        wu_btn.setToolTip(
            "Opens Windows Update settings — this app does not match a "
            "specific device to a specific driver update.")
        self._backup_btn = QPushButton("Backup Drivers")
        self._cancel_backup_btn = QPushButton("Cancel (stops after current file)")
        self._cancel_backup_btn.setVisible(False)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by name or class...")
        self._flag_filter_combo = QComboBox()
        self._flag_filter_combo.addItems(FLAG_FILTER_OPTIONS)
        self._select_flagged_btn = QPushButton("Select flagged")
        self._hide_pseudo_cb = QCheckBox("Hide pseudo-devices")
        self._hide_pseudo_cb.setChecked(True)
        self._status_lbl = QLabel("Click Refresh to load drivers.")
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._export_btn)
        toolbar.addWidget(devmgr_btn)
        toolbar.addWidget(wu_btn)
        toolbar.addWidget(self._backup_btn)
        toolbar.addWidget(self._cancel_backup_btn)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self._filter_edit, 1)
        toolbar.addWidget(QLabel("Flag:"))
        toolbar.addWidget(self._flag_filter_combo)
        toolbar.addWidget(self._select_flagged_btn)
        toolbar.addWidget(self._hide_pseudo_cb)
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Interactive, not ResizeToContents: only Interactive columns can
        # hold a manually-set or persisted width (see core/table_ui.py's
        # fit_columns_once/save_column_widths/restore_column_widths). The
        # one-time content fit on first real population (below, in
        # _populate()) keeps a fresh install looking exactly as it did
        # under ResizeToContents.
        for i in range(1, len(COLUMNS)):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        self._table_stack = QStackedWidget()
        self._table_stack.addWidget(self._table)
        self._empty = EmptyState(
            "🖨️", "No drivers loaded", "Click Refresh to scan.", "Refresh")
        self._empty.action_triggered.connect(self._do_refresh)
        self._table_stack.addWidget(self._empty)
        layout.addWidget(self._table_stack, 1)

        self._refresh_btn.clicked.connect(self._do_refresh)
        self._export_btn.clicked.connect(self._do_export)
        devmgr_btn.clicked.connect(self._open_devmgr)
        wu_btn.clicked.connect(self._open_windows_update_settings)
        self._backup_btn.clicked.connect(self._backup_drivers)
        self._cancel_backup_btn.clicked.connect(self._on_cancel_backup)
        self._select_flagged_btn.clicked.connect(self._select_all_flagged)
        self._filter_edit.textChanged.connect(
            lambda txt: self._populate(self._drivers_ref[0], txt)
        )
        self._hide_pseudo_cb.stateChanged.connect(
            lambda _state: self._populate(self._drivers_ref[0], self._filter_edit.text())
        )
        self._flag_filter_combo.currentTextChanged.connect(
            lambda _txt: self._populate(self._drivers_ref[0], self._filter_edit.text())
        )
        self._table_stack.setCurrentIndex(1)

        # D23: restore the last sort column/order this session saved in
        # on_deactivate. Done ONCE here, via the header's indicator, rather
        # than reloaded from config on every _populate() call -- _populate
        # runs on every filter keystroke, hide-pseudo toggle and flag-combo
        # change, and a per-call reload-and-reapply silently reverted an
        # in-session header click back to the last-persisted order the
        # moment any of those fired. setSortingEnabled(True) (above) makes
        # the table re-sort itself on every future row rebuild using
        # whatever the header's indicator currently says, so setting it
        # once here is sufficient -- a later click updates it directly
        # (see _on_header_click) and on_deactivate persists whatever it
        # ends up at.
        cfg = self.app.config if self.app else None
        sort_col = int(cfg.get(f"{self._CONFIG_PREFIX}.sort_column", 0) or 0) if cfg else 0
        sort_order = (Qt.SortOrder(int(cfg.get(f"{self._CONFIG_PREFIX}.sort_order",
                      int(Qt.SortOrder.AscendingOrder.value)) or 0)) if cfg
                      else Qt.SortOrder.AscendingOrder)
        self._table.horizontalHeader().setSortIndicator(sort_col, sort_order)
        self._sort_col = sort_col

        # C07: restore any column widths saved from a previous session.
        if cfg is not None:
            restore_column_widths(self._table, cfg.get, self._CONFIG_PREFIX)

        return self._widget

    def on_start(self, app) -> None:
        self.app = app

    def on_activate(self) -> None:
        if not self._drivers_ref[0]:
            self._do_refresh()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        self._do_refresh()

    def get_search_provider(self) -> Optional[SearchProvider]:
        # A fresh instance per call, same as DebloatSearchProvider -- but
        # unlike that one, this needs a LIVE handle: there is no catalog file
        # to re-read, only whatever the last refresh put in _drivers_ref.
        # Passing the shared cell (never reassigned -- see __init__) means
        # every instance, however many are built, sees the same live data.
        return DriverSearchProvider(self._drivers_ref)

    def on_deactivate(self) -> None:
        header = self._table.horizontalHeader() if self._table else None
        if header is not None and self.app and getattr(self.app, "config", None):
            # `Qt.SortOrder` is a plain `enum.Enum` in this PyQt6 build, not
            # `IntEnum` -- `int(header.sortIndicatorOrder())` raises
            # `TypeError: int() argument must be a string, a bytes-like
            # object or a real number, not 'SortOrder'` (confirmed by a
            # real test exercising this exact call). `.value` first.
            self.app.config.set(f"{self._CONFIG_PREFIX}.sort_column",
                                int(header.sortIndicatorSection()))
            self.app.config.set(f"{self._CONFIG_PREFIX}.sort_order",
                                int(header.sortIndicatorOrder().value))
        if self._table is not None and self.app and getattr(self.app, "config", None):
            save_column_widths(self._table, self.app.config.set, self._CONFIG_PREFIX)
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_header_click(self, logical_index: int) -> None:
        if self._table is None:
            return
        hdr = self._table.horizontalHeader()
        if logical_index == self._sort_col:
            # Toggle order when clicking the same column
            order = hdr.sortIndicatorOrder()
            order = Qt.SortOrder.DescendingOrder if order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        else:
            order = Qt.SortOrder.AscendingOrder
        self._sort_col = logical_index
        self._table.sortItems(logical_index, order)

    def _populate(self, drivers: List[DriverInfo], filter_text: str = "") -> None:
        if self._table is None:
            return
        ft = filter_text.lower()
        hide_pseudo = self._hide_pseudo_cb.isChecked() if self._hide_pseudo_cb else True
        flag = self._flag_filter_combo.currentText() if self._flag_filter_combo else "All"
        visible = [
            d for d in drivers
            if (not ft or ft in d.device_name.lower() or ft in d.driver_class.lower())
            and not (hide_pseudo and d.driver_class in _PSEUDO_CLASSES)
            and (flag == "All"
                 or (flag == "Signed only" and d.signed)
                 or (flag == "Unsigned only" and not d.signed)
                 or (flag == "Has error" and d.error_code != 0)
                 or (flag == "Old" and "Old" in d.flags))
        ]
        self._table.setRowCount(len(visible))
        for r, d in enumerate(visible):
            provider = classify_provider(d.publisher)
            values = [
                d.device_name, d.driver_class, d.version, d.date,
                d.publisher, provider, "✓" if d.signed else "✗", d.flags,
            ]
            for c, val in enumerate(values):
                if c == 3:
                    item = NumericSortItem(str(val), _date_sort_value(d.date))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    item = centered_item(str(val), sortable=(c == 0))
                self._table.setItem(r, c, item)
            if d.error_code != 0 or not d.signed:
                for c in range(len(COLUMNS)):
                    cell = self._table.item(r, c)
                    if cell:
                        cell.setForeground(QColor(semantic("error")))

        # C07 follow-up: fit Interactive columns to content ONCE, on the
        # first real population this session, when nothing was persisted
        # for them -- never again after that (_populate runs on every
        # filter keystroke, hide-pseudo toggle and flag-combo change; doing
        # this every time would silently undo an in-session column drag,
        # the same class of bug Task 35 fixed for sort order).
        if not self._columns_fitted_this_session:
            self._columns_fitted_this_session = True
            cfg = self.app.config if self.app else None
            if cfg is not None:
                fit_columns_once(self._table, cfg.get, self._CONFIG_PREFIX)
            else:
                self._table.resizeColumnsToContents()

    def _select_all_flagged(self) -> None:
        if self._table is None:
            return
        self._table.clearSelection()
        selection = self._table.selectionModel()
        if selection is None:
            return
        for r in range(self._table.rowCount()):
            flags_item = self._table.item(r, 7)
            if flags_item and flags_item.text().strip():
                selection.select(self._table.model().index(r, 0),
                                 QItemSelectionModel.SelectionFlag.Select
                                 | QItemSelectionModel.SelectionFlag.Rows)

    def _do_refresh(self) -> None:
        if self._refresh_btn:
            self._refresh_btn.setEnabled(False)
        if self._status_lbl:
            self._status_lbl.setText("Loading...")
        if self._progress:
            self._progress.show()
        if self._table:
            self._table.setRowCount(0)

        worker = COMWorker(lambda _w: fetch_drivers())

        def on_result(data: List[DriverInfo]) -> None:
            # Guard against the widget having been torn down (module
            # switched away, app shutting down, or -- in tests -- the
            # module instance from a previous test) by the time this
            # closure fires; a Qt call on a deleted C++ object crashes
            # the process rather than raising. See core/widget_life.py.
            if not widget_is_valid(self._widget):
                return
            self._drivers_ref[0] = data
            if self._refresh_btn:
                self._refresh_btn.setEnabled(True)
            if self._progress:
                self._progress.hide()
            filter_text = self._filter_edit.text() if self._filter_edit else ""
            self._populate(data, filter_text)
            if self._table_stack is not None:
                self._table_stack.setCurrentIndex(0 if data else 1)
            if self._status_lbl:
                issues = sum(1 for d in data if d.error_code != 0 or not d.signed)
                self._status_lbl.setText(f"{len(data)} drivers, {issues} with issues.")

        def on_error(err_str: str) -> None:
            if not widget_is_valid(self._widget):
                return
            if self._refresh_btn:
                self._refresh_btn.setEnabled(True)
            if self._progress:
                self._progress.hide()
            if self._status_lbl:
                self._status_lbl.setText(f"Error: {err_str}")

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        self._workers.append(worker)

        if self.app and getattr(self.app, "thread_pool", None) is not None:
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)

    def _do_export(self) -> None:
        if self._widget is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Export CSV", "drivers.csv", "CSV (*.csv)"
        )
        if not path:
            return
        visible_names = {self._table.item(r, 0).text()
                        for r in range(self._table.rowCount())
                        if not self._table.isRowHidden(r)}
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for d in self._drivers_ref[0]:
                if d.device_name not in visible_names:
                    continue
                prov = classify_provider(d.publisher)
                writer.writerow([
                    d.device_name, d.driver_class, d.version, d.date,
                    d.publisher, prov, d.signed, d.flags,
                ])
        if self._status_lbl:
            self._status_lbl.setText(f"Exported to {os.path.basename(path)}")

    def _export_one_driver(self, published: str) -> None:
        folder = QFileDialog.getExistingDirectory(self._widget, "Export Driver")
        if not folder:
            return
        result = subprocess.run(
            ["pnputil", "/export-driver", published, folder],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            msg = f"Exported {published}"
        else:
            msg = f"Could not export {published}: {result.stdout + result.stderr}"
        logger.info(msg)
        self._status_lbl.setText(msg)

    def _open_windows_update_settings(self) -> None:
        os.startfile("ms-settings:windowsupdate")

    def _open_devmgr(self) -> None:
        subprocess.Popen(["mmc", "devmgmt.msc"])

    def _on_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        device_name = self._table.item(row, 0).text()
        driver = next((d for d in self._drivers_ref[0]
                      if d.device_name == device_name), None)
        published = published_name_for(driver.inf_name) if driver else None

        menu = QMenu(self._table)
        act_copy = menu.addAction("Copy device name")
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(device_name))
        menu.addSeparator()
        act_uninstall = menu.addAction("Uninstall driver package…")
        act_uninstall.setEnabled(bool(published))
        act_uninstall.setToolTip(
            "" if published else
            "This is a driver Windows ships inline, not an installed "
            "OEM package — remove it from Device Manager instead.")
        act_uninstall.triggered.connect(
            lambda: self._do_uninstall_driver(published, device_name))
        act_export_one = menu.addAction("Export this driver…")
        act_export_one.setEnabled(bool(published))
        act_export_one.triggered.connect(
            lambda: self._export_one_driver(published))
        act_rollback = menu.addAction("Roll back to previous version…")
        act_rollback.setToolTip(
            "Needs the previous driver still cached, which this app does "
            "not track — opens Device Manager, where Windows can check.")
        act_rollback.triggered.connect(self._open_devmgr)
        menu.addSeparator()
        act_cleanup = menu.addAction("Open Cleanup's Superseded Drivers panel")
        act_cleanup.triggered.connect(self._open_cleanup_driver_panel)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _do_uninstall_driver(self, published: str, device_name: str) -> None:
        if not self.require_admin():
            return
        if not confirm_destructive(
                self._widget, "Uninstall Driver Package",
                f"Uninstall the driver package for {device_name}?",
                detail=f"Runs: pnputil /delete-driver {published} /uninstall\n"
                      f"The device may stop working until Windows finds "
                      f"another driver for it."):
            return
        result = subprocess.run(
            ["pnputil", "/delete-driver", published, "/uninstall", "/force"],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            msg = f"Removed driver package {published}"
        else:
            msg = f"Could not remove {published}: {result.stdout + result.stderr}"
        logger.info(msg)
        self._status_lbl.setText(msg)
        self._do_refresh()

    def _open_cleanup_driver_panel(self) -> None:
        if self.app and hasattr(self.app, "event_bus"):
            self.app.event_bus.publish(
                NAV_REQUEST_MODULE, NavRequestData(module_name="Cleanup"))
            # The Superseded Drivers panel lives on Cleanup's Large Items
            # tab -- selecting the module is what this app's existing
            # navigation event already does; switching to that specific
            # inner tab is Cleanup's own concern, not something Driver
            # Manager reaches into.

    def _backup_drivers(self) -> None:
        """Export all third-party drivers to a user-selected folder using pnputil."""
        if self._widget is None:
            return
        folder = QFileDialog.getExistingDirectory(
            self._widget, "Select Backup Folder"
        )
        if not folder:
            return
        if not confirm_destructive(
                self._widget, "Export All Drivers",
                f"Export every driver to {folder}?",
                detail="This can take a while for a lot of drivers. Progress "
                      "is shown per driver, and Cancel stops after the "
                      "current one.",
                irreversible=False):
            return
        self._backup_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._filter_edit.setEnabled(False)
        self._cancel_backup_btn.setVisible(True)
        self._cancel_backup_btn.setEnabled(True)
        if self._status_lbl:
            self._status_lbl.setText(f"Exporting drivers to {folder}...")

        exportable = [d for d in self._drivers_ref[0] if published_name_for(d.inf_name)]
        if self._progress:
            self._progress.setRange(0, len(exportable))
            self._progress.setValue(0)
            self._progress.show()

        def do_backup(worker):
            # `exportable` is the SAME list computed above, before the
            # worker starts -- not recomputed from `self._drivers_ref[0]`
            # here, which could have been replaced by then (a refresh can
            # land mid-backup; the auto-refresh timer is not gated on a
            # backup being in progress). Recomputing risked desyncing the
            # worker's real export count from the progress bar's range.
            combined_output = []
            failures = 0
            for i, d in enumerate(exportable):
                if worker.is_cancelled:
                    combined_output.append("Cancelled.")
                    break
                published = published_name_for(d.inf_name)
                result = subprocess.run(
                    ["pnputil", "/export-driver", published, folder],
                    capture_output=True, text=True, timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                combined_output.append(result.stdout + result.stderr)
                if result.returncode != 0:
                    failures += 1
                worker.signals.progress.emit(i + 1)
            returncode = 0 if failures == 0 else 1
            return "\n".join(combined_output), returncode

        worker = Worker(do_backup)
        worker.signals.progress.connect(self._progress.setValue)
        worker.signals.result.connect(self._on_backup_done)
        worker.signals.error.connect(self._on_backup_error)
        # Worker.run() emits `cancelled` -- never `result` or `error` -- if
        # the cancel flag is set when `do_backup` returns, even when the
        # loop noticed it and returned cleanly. Without this connection a
        # cancelled backup (via the button below, or simply navigating away
        # while one is running, since on_deactivate()/on_stop() call
        # cancel_all_workers() unconditionally) left every control disabled
        # and the progress bar frozen for the rest of the session.
        worker.signals.cancelled.connect(self._on_backup_cancelled)
        self._workers.append(worker)
        self._backup_worker = worker
        if self.app and getattr(self.app, "thread_pool", None) is not None:
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)

    def _on_cancel_backup(self) -> None:
        if self._backup_worker is not None:
            self._backup_worker.cancel()
        self._cancel_backup_btn.setEnabled(False)

    def _on_backup_error_line(self, stderr: str) -> str:
        if "access" in stderr.lower() and "denied" in stderr.lower():
            return "denied — this driver package needs administrator"
        return stderr.strip() or "failed for an unreported reason"

    def _on_backup_done(self, result) -> None:
        output, returncode = result
        self._reset_backup_controls()
        if returncode == 0:
            msg = "Driver backup complete — exported to selected folder."
        else:
            msg = f"Driver backup finished: {self._on_backup_error_line(output)}"
        logger.info(msg)
        if self._status_lbl:
            self._status_lbl.setText(msg)

    def _on_backup_error(self, err_str: str) -> None:
        self._reset_backup_controls()
        msg = f"Backup error: {err_str}"
        logger.info(msg)
        if self._status_lbl:
            self._status_lbl.setText(msg)

    def _on_backup_cancelled(self) -> None:
        self._reset_backup_controls()
        msg = "Driver backup cancelled."
        logger.info(msg)
        if self._status_lbl:
            self._status_lbl.setText(msg)

    def _reset_backup_controls(self) -> None:
        self._backup_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._filter_edit.setEnabled(True)
        self._cancel_backup_btn.setVisible(False)
        self._cancel_backup_btn.setEnabled(True)
        if self._progress:
            self._progress.hide()
        self._backup_worker = None
