import csv
import os
import subprocess
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QHeaderView, QLineEdit, QLabel,
    QProgressBar, QFileDialog, QCheckBox, QApplication, QMenu,
)
from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QColor

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.events import NAV_REQUEST_MODULE, NavRequestData
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.widget_life import widget_is_valid
from core.worker import COMWorker, Worker
from modules.driver_manager.driver_reader import (
    DriverInfo, classify_provider, fetch_drivers, published_name_for,
    _PSEUDO_CLASSES,
)

COLUMNS = ["Device Name", "Class", "Version", "Date", "Publisher", "Provider", "Signed", "Status"]


class DriverModule(BaseModule):
    name = "Driver Manager"
    icon = "🖨️"
    description = "View and manage installed drivers"
    requires_admin = False
    group = ModuleGroup.SYSTEM

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._table: Optional[QTableWidget] = None
        self._progress: Optional[QProgressBar] = None
        self._status_lbl: Optional[QLabel] = None
        self._filter_edit: Optional[QLineEdit] = None
        self._hide_pseudo_cb: Optional[QCheckBox] = None
        self._refresh_btn: Optional[QPushButton] = None
        self._export_btn: Optional[QPushButton] = None
        self._cancel_backup_btn: Optional[QPushButton] = None
        self._backup_worker: Optional[Worker] = None
        self._drivers_ref = None  # [list of DriverInfo]
        self._sort_col: int = -1

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._export_btn = QPushButton("Export CSV")
        devmgr_btn = QPushButton("Open Device Manager")
        self._backup_btn = QPushButton("Backup Drivers")
        self._cancel_backup_btn = QPushButton("Cancel (stops after current file)")
        self._cancel_backup_btn.setVisible(False)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by name or class...")
        self._hide_pseudo_cb = QCheckBox("Hide pseudo-devices")
        self._hide_pseudo_cb.setChecked(True)
        self._status_lbl = QLabel("Click Refresh to load drivers.")
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(self._export_btn)
        toolbar.addWidget(devmgr_btn)
        toolbar.addWidget(self._backup_btn)
        toolbar.addWidget(self._cancel_backup_btn)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self._filter_edit, 1)
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
        for i in range(1, len(COLUMNS)):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table, 1)

        self._refresh_btn.clicked.connect(self._do_refresh)
        self._export_btn.clicked.connect(self._do_export)
        devmgr_btn.clicked.connect(self._open_devmgr)
        self._backup_btn.clicked.connect(self._backup_drivers)
        self._cancel_backup_btn.clicked.connect(self._on_cancel_backup)
        self._filter_edit.textChanged.connect(
            lambda txt: self._populate(self._drivers_ref[0], txt)
        )
        self._hide_pseudo_cb.stateChanged.connect(
            lambda _state: self._populate(self._drivers_ref[0], self._filter_edit.text())
        )
        self._drivers_ref = [[]]

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

    def on_deactivate(self) -> None:
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
        visible = [
            d for d in drivers
            if (not ft or ft in d.device_name.lower() or ft in d.driver_class.lower())
            and not (hide_pseudo and d.driver_class in _PSEUDO_CLASSES)
        ]
        self._table.setRowCount(len(visible))
        for r, d in enumerate(visible):
            provider = classify_provider(d.publisher)
            items = [
                d.device_name, d.driver_class, d.version, d.date,
                d.publisher, provider, "✓" if d.signed else "✗", d.flags,
            ]
            for c, val in enumerate(items):
                item = centered_item(str(val), sortable=(c == 0))
                self._table.setItem(r, c, item)
            if d.error_code != 0 or not d.signed:
                for c in range(len(COLUMNS)):
                    cell = self._table.item(r, c)
                    if cell:
                        cell.setForeground(QColor("#CC2222"))

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
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for d in self._drivers_ref[0]:
                prov = classify_provider(d.publisher)
                writer.writerow([
                    d.device_name, d.driver_class, d.version, d.date,
                    d.publisher, prov, d.signed, d.flags,
                ])
        if self._status_lbl:
            self._status_lbl.setText(f"Exported to {os.path.basename(path)}")

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
            self._status_lbl.setText(f"Removed driver package {published}")
        else:
            self._status_lbl.setText(
                f"Could not remove {published}: {result.stdout + result.stderr}")
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
                detail="This can take a while and cannot be cancelled "
                      "part-way through cleanly — pnputil does not report "
                      "progress per driver.",
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
            exportable = [d for d in self._drivers_ref[0] if published_name_for(d.inf_name)]
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
        self._backup_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._filter_edit.setEnabled(True)
        self._cancel_backup_btn.setVisible(False)
        self._cancel_backup_btn.setEnabled(True)
        if self._progress:
            self._progress.hide()
        if returncode == 0:
            msg = "Driver backup complete — exported to selected folder."
        else:
            msg = f"Driver backup finished: {self._on_backup_error_line(output)}"
        if self._status_lbl:
            self._status_lbl.setText(msg)

    def _on_backup_error(self, err_str: str) -> None:
        self._backup_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._filter_edit.setEnabled(True)
        self._cancel_backup_btn.setVisible(False)
        self._cancel_backup_btn.setEnabled(True)
        if self._progress:
            self._progress.hide()
        if self._status_lbl:
            self._status_lbl.setText(f"Backup error: {err_str}")
