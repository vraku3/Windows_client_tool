"""Disk Space: every volume, and where the space on the chosen one went."""
import logging
import os
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QHeaderView, QLabel,
                             QMenu, QProgressBar, QPushButton, QTableWidget,
                             QVBoxLayout)

from core.events import NAV_REQUEST_MODULE, NavRequestData
from core.worker import Worker

from . import folder_sizes as fs
from .tab_base import DashModule, DashTab, fmt_size, numeric_item

logger = logging.getLogger(__name__)

VOLUME_HEADERS = ("Drive", "File system", "Size", "Used", "Free", "Used %")


class DiskSpaceTab(DashTab):
    REFRESH_MS = 15000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scan_worker = None
        self._last_scan: Optional[fs.FolderScan] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._vol = QTableWidget(0, len(VOLUME_HEADERS), self)
        self._vol.setHorizontalHeaderLabels(list(VOLUME_HEADERS))
        self._vol.verticalHeader().setVisible(False)
        self._vol.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._vol.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._vol.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._vol.setMaximumHeight(150)
        self._vol.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._vol.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self._vol)

        bar = QHBoxLayout()
        self._scan_btn = QPushButton("Find the biggest folders on the selected drive", self)
        self._scan_btn.setEnabled(False)
        self._scan_btn.clicked.connect(self._scan)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_scan)
        self._treesize_btn = QPushButton("Open TreeSize…", self)
        self._treesize_btn.setToolTip("For a full, drillable scan")
        self._treesize_btn.clicked.connect(self._open_treesize)
        for w in (self._scan_btn, self._cancel_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self._treesize_btn)
        layout.addLayout(bar)
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._folders = QTableWidget(0, 3, self)
        self._folders.setHorizontalHeaderLabels(["Folder", "Size", "Share of drive"])
        self._folders.verticalHeader().setVisible(False)
        self._folders.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._folders.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._folders.setToolTip("Double-click, or right-click, to open a folder in TreeSize")
        self._folders.cellDoubleClicked.connect(self._open_folder_in_treesize)
        self._folders.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._folders.customContextMenuRequested.connect(self._show_folder_menu)
        layout.addWidget(self._folders, 1)
        self.status = QLabel("Select a drive, then find its biggest folders.", self)
        layout.addWidget(self.status)

    # ---- volumes -----------------------------------------------------------------

    def refresh(self) -> None:
        self.run(lambda _w: fs.volumes(), self._apply_volumes)

    def _apply_volumes(self, rows) -> None:
        keep = self._selected_mount()
        table = self._vol
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for r, v in enumerate(rows):
            cells = (v["mount"], v["fs"], fmt_size(v["total"]), fmt_size(v["used"]),
                     fmt_size(v["free"]), f"{v['percent']:.0f}%")
            for c, text in enumerate(cells):
                table.setItem(r, c, numeric_item(text, text))
            table.item(r, 0).setData(Qt.ItemDataRole.UserRole, v["mount"])
            if keep == v["mount"]:
                table.selectRow(r)
        if keep is None and rows:
            table.selectRow(0)          # the system drive: a scan is one click away
        table.blockSignals(False)
        self._selection_changed()

    def _selected_mount(self):
        rows = self._vol.selectionModel().selectedRows() if self._vol.selectionModel() else []
        item = self._vol.item(rows[0].row(), 0) if rows else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selection_changed(self) -> None:
        self._scan_btn.setEnabled(self._selected_mount() is not None and self._scan_worker is None)

    # ---- folder scan -------------------------------------------------------------

    def _scan(self) -> None:
        mount = self._selected_mount()
        pool = self._pool()
        if mount is None:
            return
        self._scan_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress.setValue(0)
        self._progress.show()
        self.status.setText(f"Scanning {mount} …")

        def work(worker):
            return fs.scan_top_level(
                mount, cancelled=lambda: bool(worker and worker.is_cancelled),
                on_progress=lambda pct, name: worker.signals.progress.emit(pct) if worker else None)

        if pool is None:
            self._scan_done(work(None))
            return
        worker = Worker(work)
        worker.signals.progress.connect(self._progress.setValue)
        worker.signals.result.connect(self._scan_done)
        worker.signals.error.connect(self._scan_failed)
        self._scan_worker = worker
        self._workers.append(worker)
        pool.start(worker)

    def _cancel_scan(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.cancel()

    def _scan_finished_ui(self) -> None:
        self._scan_worker = None
        self._progress.hide()
        self._cancel_btn.setEnabled(False)
        self._selection_changed()

    def _scan_failed(self, message) -> None:
        self._scan_finished_ui()
        logger.error("folder scan failed: %s", message)
        self.status.setText(f"The scan failed: {message}")

    def _scan_done(self, scan) -> None:
        self._scan_finished_ui()
        self._last_scan = scan
        table = self._folders
        volume_total = next((v for v in fs.volumes() if v["mount"] == scan.root), None)
        capacity = volume_total["used"] if volume_total else scan.total
        table.setRowCount(len(scan.entries))
        for r, (name, size) in enumerate(scan.entries):
            share = size * 100 / capacity if capacity else 0
            table.setItem(r, 0, numeric_item(name, name))
            table.setItem(r, 1, numeric_item(size, fmt_size(size)))
            table.setItem(r, 2, numeric_item(share, f"{share:.1f}%"))
            table.item(r, 1).setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
        parts = [f"{scan.root}: {fmt_size(scan.total)} in {len(scan.entries)} folders, {scan.files:,} files"]
        if scan.cancelled:
            parts.append("CANCELLED - these are partial figures")
        if scan.unreadable:
            parts.append(f"{scan.unreadable:,} item(s) could not be read (run as administrator "
                         "for system folders); their space is not counted")
        self.status.setText("   ·   ".join(parts))

    def _open_treesize(self) -> None:
        # Pass the selected drive along: landing on TreeSize's empty Home tab
        # after already picking a drive here means re-typing or re-browsing
        # to the exact same letter a second time.
        if self._app is not None:
            self._app.event_bus.publish(
                NAV_REQUEST_MODULE,
                NavRequestData(module_name="TreeSize", path=self._selected_mount()))

    def _folder_path_at(self, row: int) -> Optional[str]:
        """The real path a folder-scan row stands for, or None for the
        "(files in the root)" sentinel row, which names no folder to open."""
        if self._last_scan is None or not (0 <= row < len(self._last_scan.entries)):
            return None
        name, _size = self._last_scan.entries[row]
        if name == "(files in the root)":
            return None
        return os.path.join(self._last_scan.root, name)

    def _open_folder_in_treesize(self, row: int, _column: int = 0) -> None:
        path = self._folder_path_at(row)
        if path is None or self._app is None:
            return
        self._app.event_bus.publish(
            NAV_REQUEST_MODULE, NavRequestData(module_name="TreeSize", path=path))

    def _show_folder_menu(self, pos) -> None:
        row = self._folders.rowAt(pos.y())
        path = self._folder_path_at(row)
        menu = QMenu(self)
        action = menu.addAction("Open in TreeSize…")
        action.setEnabled(path is not None)
        action.triggered.connect(lambda: self._open_folder_in_treesize(row))
        menu.exec(self._folders.viewport().mapToGlobal(pos))

    def cancel_all(self) -> None:
        super().cancel_all()
        self._scan_worker = None


class DiskSpaceModule(DashModule):
    name = "Disk Space"
    icon = "💽"
    description = "Every volume, and where the space on a drive went"
    tab_class = DiskSpaceTab
