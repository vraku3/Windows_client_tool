import csv
import os
import subprocess
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QHeaderView, QLineEdit, QLabel,
    QProgressBar, QFileDialog, QCheckBox,
)
from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QColor

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.worker import COMWorker, Worker
from modules.driver_manager.driver_reader import (
    DriverInfo, classify_provider, fetch_drivers, _PSEUDO_CLASSES,
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
        self._drivers_ref = None  # [list of DriverInfo]
        self._sort_col: int = -1

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        export_btn = QPushButton("Export CSV")
        devmgr_btn = QPushButton("Open Device Manager")
        self._backup_btn = QPushButton("Backup Drivers")
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by name or class...")
        self._hide_pseudo_cb = QCheckBox("Hide pseudo-devices")
        self._hide_pseudo_cb.setChecked(True)
        self._status_lbl = QLabel("Click Refresh to load drivers.")
        toolbar.addWidget(self._refresh_btn)
        toolbar.addWidget(export_btn)
        toolbar.addWidget(devmgr_btn)
        toolbar.addWidget(self._backup_btn)
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
        layout.addWidget(self._table, 1)

        self._refresh_btn.clicked.connect(self._do_refresh)
        export_btn.clicked.connect(self._do_export)
        devmgr_btn.clicked.connect(self._open_devmgr)
        self._backup_btn.clicked.connect(self._backup_drivers)
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
            if self._refresh_btn:
                self._refresh_btn.setEnabled(True)
            if self._progress:
                self._progress.hide()
            if self._status_lbl:
                self._status_lbl.setText(f"Error: {err_str}")

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        self._workers.append(worker)

        if self.app and hasattr(self.app, "thread_pool"):
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

    def _backup_drivers(self) -> None:
        """Export all third-party drivers to a user-selected folder using pnputil."""
        if self._widget is None:
            return
        folder = QFileDialog.getExistingDirectory(
            self._widget, "Select Backup Folder"
        )
        if not folder:
            return
        self._backup_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        if self._status_lbl:
            self._status_lbl.setText(f"Exporting drivers to {folder}...")
        if self._progress:
            self._progress.show()

        def do_backup(_worker):
            result = subprocess.run(
                ["pnputil", "/export-driver", "*", folder],
                capture_output=True, text=True, timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.stdout + result.stderr, result.returncode

        worker = Worker(do_backup)
        worker.signals.result.connect(self._on_backup_done)
        worker.signals.error.connect(self._on_backup_error)
        self._workers.append(worker)
        if self.app and hasattr(self.app, "thread_pool"):
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)

    def _on_backup_done(self, result) -> None:
        output, returncode = result
        self._backup_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        if self._progress:
            self._progress.hide()
        if returncode == 0:
            msg = "Driver backup complete — exported to selected folder."
        else:
            msg = f"Driver backup finished with code {returncode}."
        if self._status_lbl:
            self._status_lbl.setText(msg)

    def _on_backup_error(self, err_str: str) -> None:
        self._backup_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        if self._progress:
            self._progress.hide()
        if self._status_lbl:
            self._status_lbl.setText(f"Backup error: {err_str}")
