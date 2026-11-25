"""DriverStore Packages & Cleanup Dialog.

Displays all installed OEM driver packages in the DriverStore, highlights superseded
packages, and allows safe removal via `pnputil /delete-driver`.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.semantic_colors import semantic
from core.table_ui import center_header, set_role
from core.widget_life import widget_is_valid
from core.worker import Worker, retire
from modules.driver_manager.driver_store_scanner import (
    DriverPackage,
    delete_driver_package,
    scan_driver_store,
)

logger = logging.getLogger(__name__)


class DriverStoreDialog(QDialog):
    """Dialog for inspecting and cleaning up superseded OEM drivers in DriverStore."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DriverStore Cleanup — Superseded & OEM Drivers")
        self.resize(960, 600)
        self._packages: List[DriverPackage] = []
        self._workers: List[Worker] = []

        self._build_ui()
        self._load_packages()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 1. Summary Header Card
        header_frame = QFrame(self)
        header_frame.setFrameShape(QFrame.Shape.StyledPanel)
        h_lay = QHBoxLayout(header_frame)
        h_lay.setContentsMargins(12, 8, 12, 8)

        self._total_lbl = QLabel("Total Packages: Reading...", header_frame)
        self._superseded_lbl = QLabel("Superseded: Reading...", header_frame)
        self._space_lbl = QLabel("Reclaimable: Reading...", header_frame)

        font = self._total_lbl.font()
        font.setBold(True)
        self._total_lbl.setFont(font)
        self._superseded_lbl.setFont(font)
        self._space_lbl.setFont(font)

        h_lay.addWidget(self._total_lbl)
        h_lay.addSpacing(20)
        h_lay.addWidget(self._superseded_lbl)
        h_lay.addSpacing(20)
        h_lay.addWidget(self._space_lbl)
        h_lay.addStretch(1)

        root.addWidget(header_frame)

        # 2. Filter & Controls Bar
        filter_bar = QHBoxLayout()

        self._only_superseded_cb = QCheckBox("Show only superseded (safe to clean)", self)
        self._only_superseded_cb.setChecked(True)
        self._only_superseded_cb.toggled.connect(self._apply_filter)
        filter_bar.addWidget(self._only_superseded_cb)

        filter_bar.addSpacing(16)
        filter_bar.addWidget(QLabel("Search:", self))
        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("Filter by name, provider, class...")
        self._search_edit.textChanged.connect(self._apply_filter)
        filter_bar.addWidget(self._search_edit, 1)

        select_all_btn = QPushButton("Select All", self)
        select_all_btn.clicked.connect(lambda: self._set_all_checks(True))
        filter_bar.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All", self)
        deselect_all_btn.clicked.connect(lambda: self._set_all_checks(False))
        filter_bar.addWidget(deselect_all_btn)

        root.addLayout(filter_bar)

        # Progress bar
        self._progress = QProgressBar(self)
        self._progress.setFixedHeight(3)
        self._progress.setRange(0, 0)
        self._progress.hide()
        root.addWidget(self._progress)

        # 3. Table
        self._table = QTableWidget(0, 7, self)
        self._table.setHorizontalHeaderLabels([
            "Select",
            "Published Name",
            "Original Name",
            "Provider",
            "Class",
            "Version & Date",
            "Status / Superseded By",
        ])
        for i in range(7):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(2, 140)
        self._table.setColumnWidth(3, 170)
        self._table.setColumnWidth(4, 110)
        self._table.setColumnWidth(5, 150)
        self._table.setColumnWidth(6, 200)
        center_header(self._table)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table, 1)

        # 4. Action Footer
        action_bar = QHBoxLayout()

        self._force_cb = QCheckBox("Force deletion (/force)", self)
        self._force_cb.setToolTip("Deletes package even if referenced by devices (use caution)")
        action_bar.addWidget(self._force_cb)

        action_bar.addStretch(1)

        refresh_btn = QPushButton("Refresh", self)
        refresh_btn.clicked.connect(self._load_packages)
        action_bar.addWidget(refresh_btn)

        self._delete_btn = QPushButton("Delete Selected Packages", self)
        set_role(self._delete_btn, "statusWarning")
        self._delete_btn.clicked.connect(self._delete_selected)
        action_bar.addWidget(self._delete_btn)

        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.accept)
        action_bar.addWidget(close_btn)

        root.addLayout(action_bar)

    def _load_packages(self) -> None:
        if not widget_is_valid(self):
            return
        self._progress.show()
        self._delete_btn.setEnabled(False)

        def work(worker: Worker):
            if worker.is_cancelled:
                return []
            return scan_driver_store()

        w = Worker(work)
        w.signals.result.connect(self._on_packages_loaded)
        w.signals.error.connect(self._on_load_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_packages_loaded(self, packages: List[DriverPackage]) -> None:
        if not widget_is_valid(self):
            return
        self._progress.hide()
        self._delete_btn.setEnabled(True)
        self._packages = packages

        superseded_count = sum(1 for p in packages if p.is_superseded)
        reclaimable_mb = sum(p.size_bytes for p in packages if p.is_superseded) / (1024 * 1024)

        self._total_lbl.setText(f"Total OEM Packages: {len(packages)}")
        self._superseded_lbl.setText(f"Superseded (Older): {superseded_count}")
        if superseded_count > 0:
            set_role(self._superseded_lbl, "statusWarning")
        else:
            set_role(self._superseded_lbl, "statusSuccess")

        if reclaimable_mb > 0:
            self._space_lbl.setText(f"Estimated Reclaimable: ~{reclaimable_mb:.1f} MB")
        else:
            self._space_lbl.setText("Estimated Reclaimable: —")

        self._apply_filter()

    def _on_load_error(self, err: str) -> None:
        if not widget_is_valid(self):
            return
        self._progress.hide()
        self._delete_btn.setEnabled(True)
        QMessageBox.warning(self, "Error Scanning DriverStore", f"Could not enumerate driver packages:\n{err}")

    def _apply_filter(self) -> None:
        if not widget_is_valid(self):
            return

        query = self._search_edit.text().strip().lower()
        only_superseded = self._only_superseded_cb.isChecked()

        visible_pkgs = []
        for p in self._packages:
            if only_superseded and not p.is_superseded:
                continue
            if query:
                haystack = f"{p.published_name} {p.original_name} {p.provider_name} {p.class_name} {p.driver_version}".lower()
                if query not in haystack:
                    continue
            visible_pkgs.append(p)

        self._table.setRowCount(len(visible_pkgs))
        for row, pkg in enumerate(visible_pkgs):
            # Checkbox item
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk_item.setCheckState(Qt.CheckState.Checked if pkg.is_superseded else Qt.CheckState.Unchecked)
            chk_item.setData(Qt.ItemDataRole.UserRole, pkg)

            item_pub = QTableWidgetItem(pkg.published_name)
            item_orig = QTableWidgetItem(pkg.original_name)
            item_prov = QTableWidgetItem(pkg.provider_name)
            item_cls = QTableWidgetItem(pkg.class_name)
            ver_text = f"{pkg.driver_version} ({pkg.driver_date})" if pkg.driver_date else pkg.driver_version
            item_ver = QTableWidgetItem(ver_text)

            if pkg.is_superseded:
                status_text = f"Superseded by {pkg.superseded_by}"
                item_status = QTableWidgetItem(status_text)
                item_status.setForeground(QColor(semantic("warning")))
            else:
                status_text = "Latest Version"
                item_status = QTableWidgetItem(status_text)
                item_status.setForeground(QColor(semantic("success")))

            self._table.setItem(row, 0, chk_item)
            self._table.setItem(row, 1, item_pub)
            self._table.setItem(row, 2, item_orig)
            self._table.setItem(row, 3, item_prov)
            self._table.setItem(row, 4, item_cls)
            self._table.setItem(row, 5, item_ver)
            self._table.setItem(row, 6, item_status)

    def _set_all_checks(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item is not None:
                item.setCheckState(state)

    def _delete_selected(self) -> None:
        to_delete: List[DriverPackage] = []
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                pkg = item.data(Qt.ItemDataRole.UserRole)
                if pkg:
                    to_delete.append(pkg)

        if not to_delete:
            QMessageBox.information(self, "No Packages Selected", "Please select one or more driver packages to delete.")
            return

        names = ", ".join(p.published_name for p in to_delete[:6])
        if len(to_delete) > 6:
            names += f", ... (+{len(to_delete) - 6} more)"

        reply = QMessageBox.question(
            self,
            "Confirm DriverStore Deletion",
            f"Are you sure you want to delete {len(to_delete)} driver package(s) from DriverStore?\n\n{names}\n\n"
            "This will permanently uninstall these OEM packages.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        force = self._force_cb.isChecked()
        self._progress.show()
        self._delete_btn.setEnabled(False)

        def delete_work(worker: Worker):
            results = []
            for pkg in to_delete:
                if worker.is_cancelled:
                    break
                ok, msg = delete_driver_package(pkg.published_name, force=force)
                results.append((pkg.published_name, ok, msg))
            return results

        w = Worker(delete_work)
        w.signals.result.connect(self._on_deletion_done)
        w.signals.error.connect(self._on_load_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_deletion_done(self, results: list) -> None:
        if not widget_is_valid(self):
            return
        self._progress.hide()
        self._delete_btn.setEnabled(True)

        successes = sum(1 for _, ok, _ in results if ok)
        failures = sum(1 for _, ok, _ in results if not ok)

        msg = f"DriverStore Cleanup Result:\nSuccessfully deleted: {successes}\nFailed: {failures}"
        if failures > 0:
            failed_details = "\n".join(f"{name}: {m}" for name, ok, m in results if not ok)
            msg += f"\n\nDetails:\n{failed_details[:600]}"
            QMessageBox.warning(self, "DriverStore Cleanup Completed with Warnings", msg)
        else:
            QMessageBox.information(self, "DriverStore Cleanup Completed", msg)

        self._load_packages()

    def closeEvent(self, event) -> None:
        retire(self._workers)
        super().closeEvent(event)
