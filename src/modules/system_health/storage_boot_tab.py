"""Storage Integrity & Boot Health Tab for System Health Module.

Displays:
1. Fast Startup & Kernel Uptime metrics.
2. Windows Recovery Environment (WinRE) & Secure Boot posture.
3. SSD TRIM operational state (NTFS & ReFS).
4. Volume Integrity & Dirty Bit table with pending autochk/chkdsk status.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any, List, Optional

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
from modules.system_health.kernel_memory import KernelMemorySnapshot, get_kernel_memory_snapshot
from modules.system_health.reboot_pending import PendingRebootStatus, get_pending_reboot_status
from modules.system_health.storage_boot_engine import (
    BootHealthSnapshot,
    VolumeIntegrity,
    get_boot_storage_health,
)
from modules.system_health.vss_inspector import get_vss_writers_status, restart_vss_services
from modules.system_health.winsxs_analyzer import WinSxSHealthSnapshot, analyze_cbs_packages

logger = logging.getLogger(__name__)


class StorageBootTab(QWidget):
    """Storage Integrity and Boot Health UI."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._workers: List[Worker] = []
        self._snapshot: Optional[BootHealthSnapshot] = None
        self.loaded = False

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # 1. Overall Health Banner
        banner_frame = QFrame(self)
        banner_frame.setFrameShape(QFrame.Shape.StyledPanel)
        b_lay = QHBoxLayout(banner_frame)
        b_lay.setContentsMargins(12, 8, 12, 8)

        self._health_badge = QLabel("Health: Reading...", banner_frame)
        font = self._health_badge.font()
        font.setBold(True)
        self._health_badge.setFont(font)
        b_lay.addWidget(self._health_badge)

        self._recs_lbl = QLabel("", banner_frame)
        self._recs_lbl.setWordWrap(True)
        set_role(self._recs_lbl, "muted")
        b_lay.addWidget(self._recs_lbl, 1)

        refresh_btn = QPushButton("Refresh Status", banner_frame)
        refresh_btn.clicked.connect(self.load)
        b_lay.addWidget(refresh_btn)

        root.addWidget(banner_frame)

        self._progress = QProgressBar(self)
        self._progress.setFixedHeight(3)
        self._progress.setRange(0, 0)
        self._progress.hide()
        root.addWidget(self._progress)

        # 2. Boot Health Section
        boot_frame = QFrame(self)
        boot_frame.setFrameShape(QFrame.Shape.StyledPanel)
        boot_lay = QGridLayout(boot_frame)
        boot_lay.setContentsMargins(12, 10, 12, 10)
        boot_lay.setSpacing(8)

        boot_title = QLabel("Boot Health & Configuration", boot_frame)
        boot_title.setFont(font)
        boot_lay.addWidget(boot_title, 0, 0, 1, 4)

        # Fast Startup
        boot_lay.addWidget(QLabel("Fast Startup (Hybrid Boot):", boot_frame), 1, 0)
        self._fast_startup_lbl = QLabel("Reading...", boot_frame)
        boot_lay.addWidget(self._fast_startup_lbl, 1, 1)

        # Kernel Uptime
        boot_lay.addWidget(QLabel("Kernel Uptime:", boot_frame), 1, 2)
        self._uptime_lbl = QLabel("Reading...", boot_frame)
        boot_lay.addWidget(self._uptime_lbl, 1, 3)

        # WinRE
        boot_lay.addWidget(QLabel("Windows Recovery (WinRE):", boot_frame), 2, 0)
        self._winre_lbl = QLabel("Reading...", boot_frame)
        boot_lay.addWidget(self._winre_lbl, 2, 1)

        # Secure Boot
        boot_lay.addWidget(QLabel("UEFI Secure Boot:", boot_frame), 2, 2)
        self._secure_boot_lbl = QLabel("Reading...", boot_frame)
        boot_lay.addWidget(self._secure_boot_lbl, 2, 3)

        # WinRE Action Button
        winre_btn = QPushButton("Reboot into Recovery Environment (WinRE)...", boot_frame)
        winre_btn.clicked.connect(self._reboot_winre)
        boot_lay.addWidget(winre_btn, 3, 0, 1, 2)

        # Pending Reboot Status
        boot_lay.addWidget(QLabel("Pending Restart Matrix:", boot_frame), 4, 0)
        self._reboot_lbl = QLabel("Reading...", boot_frame)
        self._reboot_lbl.setWordWrap(True)
        boot_lay.addWidget(self._reboot_lbl, 4, 1, 1, 3)

        # Kernel Memory Pool Status
        boot_lay.addWidget(QLabel("Kernel Memory (Pools & Commit):", boot_frame), 5, 0)
        self._kernel_mem_lbl = QLabel("Reading...", boot_frame)
        boot_lay.addWidget(self._kernel_mem_lbl, 5, 1, 1, 3)

        root.addWidget(boot_frame)

        # 3. Servicing & Component Store Section
        sxs_frame = QFrame(self)
        sxs_frame.setFrameShape(QFrame.Shape.StyledPanel)
        sxs_lay = QHBoxLayout(sxs_frame)
        sxs_lay.setContentsMargins(12, 8, 12, 8)

        self._sxs_lbl = QLabel("WinSxS & Servicing Packages: Reading...", sxs_frame)
        font = self._sxs_lbl.font()
        font.setBold(True)
        self._sxs_lbl.setFont(font)
        sxs_lay.addWidget(self._sxs_lbl, 1)

        self._vss_lbl = QLabel("VSS Writers: Reading...", sxs_frame)
        sxs_lay.addWidget(self._vss_lbl, 1)

        vss_fix_btn = QPushButton("Reset Stalled VSS Writers", sxs_frame)
        vss_fix_btn.setToolTip("Restarts VSS, swprv, and CryptSvc to recover from failed writer locks")
        vss_fix_btn.clicked.connect(self._fix_vss_writers)
        sxs_lay.addWidget(vss_fix_btn)

        root.addWidget(sxs_frame)

        # 3. Storage Integrity Section
        storage_frame = QFrame(self)
        storage_frame.setFrameShape(QFrame.Shape.StyledPanel)
        storage_lay = QVBoxLayout(storage_frame)
        storage_lay.setContentsMargins(12, 10, 12, 10)
        storage_lay.setSpacing(6)

        storage_title = QLabel("Storage Integrity & Volume Health", storage_frame)
        storage_title.setFont(font)
        storage_lay.addWidget(storage_title)

        trim_row = QHBoxLayout()
        self._trim_lbl = QLabel("SSD TRIM: Reading...", storage_frame)
        trim_row.addWidget(self._trim_lbl)
        trim_row.addStretch(1)

        chkdsk_btn = QPushButton("Queue File System Check on Next Boot...", storage_frame)
        chkdsk_btn.clicked.connect(self._queue_chkdsk)
        trim_row.addWidget(chkdsk_btn)
        storage_lay.addLayout(trim_row)

        # Volumes Table
        self._table = QTableWidget(0, 5, storage_frame)
        self._table.setHorizontalHeaderLabels([
            "Volume",
            "File System",
            "Free Space / Total",
            "Dirty Bit Status",
            "Autochk / Chkdsk Pending",
        ])
        for i in range(5):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 90)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(2, 190)
        self._table.setColumnWidth(3, 220)
        self._table.setColumnWidth(4, 180)
        center_header(self._table)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        storage_lay.addWidget(self._table, 1)

        root.addWidget(storage_frame, 1)

    def load(self) -> None:
        if not widget_is_valid(self):
            return
        self._progress.show()

        def work(worker: Worker):
            if worker.is_cancelled:
                return None
            boot_snap = get_boot_storage_health()
            if worker.is_cancelled:
                return None
            reboot_snap = get_pending_reboot_status()
            mem_snap = get_kernel_memory_snapshot()
            sxs_snap = analyze_cbs_packages()
            vss_writers, vss_err = get_vss_writers_status()
            return {
                "boot": boot_snap,
                "reboot": reboot_snap,
                "mem": mem_snap,
                "sxs": sxs_snap,
                "vss_writers": vss_writers,
                "vss_err": vss_err,
            }

        w = Worker(work)
        w.signals.result.connect(self._on_loaded)
        w.signals.error.connect(self._on_load_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_loaded(self, data: Optional[Any]) -> None:
        if not widget_is_valid(self):
            return
        self._progress.hide()
        if not data:
            return

        if isinstance(data, BootHealthSnapshot):
            snapshot: BootHealthSnapshot = data
            reboot_snap: Optional[PendingRebootStatus] = None
            mem_snap: Optional[KernelMemorySnapshot] = None
            sxs_snap: Optional[WinSxSHealthSnapshot] = None
            vss_writers = []
            vss_err = ""
        else:
            snapshot: BootHealthSnapshot = data.get("boot")
            reboot_snap: Optional[PendingRebootStatus] = data.get("reboot")
            mem_snap: Optional[KernelMemorySnapshot] = data.get("mem")
            sxs_snap: Optional[WinSxSHealthSnapshot] = data.get("sxs")
            vss_writers = data.get("vss_writers", [])
            vss_err = data.get("vss_err", "")

        if not snapshot:
            return

        self._snapshot = snapshot
        self.loaded = True

        # Overall Health
        self._health_badge.setText(f"Overall Health: {snapshot.overall_health}")
        if snapshot.overall_health == "Healthy":
            set_role(self._health_badge, "statusSuccess")
        elif snapshot.overall_health == "Notice":
            set_role(self._health_badge, "statusInfo")
        elif snapshot.overall_health == "Warning":
            set_role(self._health_badge, "statusWarning")
        else:
            set_role(self._health_badge, "statusError")

        recs_text = " • ".join(snapshot.recommendations) if snapshot.recommendations else "All boot and volume integrity checks passed cleanly."
        self._recs_lbl.setText(recs_text)

        # Boot Info
        fs_str = "Enabled" if snapshot.fast_startup_enabled else "Disabled (Clean Boot)"
        self._fast_startup_lbl.setText(fs_str)
        if snapshot.fast_startup_enabled:
            set_role(self._fast_startup_lbl, "statusInfo")
        else:
            set_role(self._fast_startup_lbl, "statusSuccess")

        uptime_str = f"{snapshot.kernel_uptime_hours:.1f} hours ({snapshot.kernel_uptime_days:.1f} days)"
        self._uptime_lbl.setText(uptime_str)
        if snapshot.kernel_uptime_days >= 7.0:
            set_role(self._uptime_lbl, "statusWarning")
        else:
            set_role(self._uptime_lbl, "statusSuccess")

        winre_str = "Enabled" if snapshot.winre_enabled else "Disabled"
        if snapshot.winre_location:
            winre_str += f" ({snapshot.winre_location})"
        self._winre_lbl.setText(winre_str)
        if snapshot.winre_enabled:
            set_role(self._winre_lbl, "statusSuccess")
        else:
            set_role(self._winre_lbl, "statusWarning")

        sb_str = "Enabled" if snapshot.secure_boot_enabled else "Disabled / Legacy"
        self._secure_boot_lbl.setText(sb_str)
        if snapshot.secure_boot_enabled:
            set_role(self._secure_boot_lbl, "statusSuccess")
        else:
            set_role(self._secure_boot_lbl, "statusInfo")

        # Pending Reboot Status
        if reboot_snap:
            if reboot_snap.is_pending:
                self._reboot_lbl.setText("REBOOT PENDING: " + " • ".join(reboot_snap.reasons))
                set_role(self._reboot_lbl, "statusWarning")
            else:
                self._reboot_lbl.setText("No pending restart required. Servicing state is clean.")
                set_role(self._reboot_lbl, "statusSuccess")

        # Kernel Memory Status
        if mem_snap:
            mem_text = (
                f"NonPagedPool: {mem_snap.nonpaged_pool_mb:.0f} MB | "
                f"PagedPool: {mem_snap.paged_pool_mb:.0f} MB | "
                f"Commit: {mem_snap.commit_ratio_pct:.0f}% ({mem_snap.commit_total_mb:.0f} / {mem_snap.commit_limit_mb:.0f} MB) | "
                f"Handles: {mem_snap.handles_count:,}"
            )
            self._kernel_mem_lbl.setText(mem_text)
            if mem_snap.is_nonpaged_leak or mem_snap.is_commit_saturated:
                set_role(self._kernel_mem_lbl, "statusWarning")
            else:
                set_role(self._kernel_mem_lbl, "statusSuccess")

        # WinSxS Status
        if sxs_snap:
            self._sxs_lbl.setText(f"WinSxS: {sxs_snap.installed_count} Installed, {sxs_snap.superseded_count} Superseded, {sxs_snap.staged_count} Staged")
            if sxs_snap.component_cleanup_recommended:
                set_role(self._sxs_lbl, "statusWarning")
            else:
                set_role(self._sxs_lbl, "statusSuccess")

        # VSS Writers Status
        if vss_writers:
            failed_w = [w for w in vss_writers if w.is_failed]
            if failed_w:
                self._vss_lbl.setText(f"VSS Writers: {len(failed_w)} Failed ({', '.join(w.name for w in failed_w[:2])})")
                set_role(self._vss_lbl, "statusError")
            else:
                self._vss_lbl.setText(f"VSS Writers: {len(vss_writers)} Stable")
                set_role(self._vss_lbl, "statusSuccess")
        elif vss_err:
            self._vss_lbl.setText(f"VSS: {vss_err[:40]}...")
            set_role(self._vss_lbl, "statusInfo")
        else:
            self._vss_lbl.setText("VSS: None reported")

        # Storage TRIM
        trim_parts = []
        if snapshot.trim_ntfs_enabled:
            trim_parts.append("NTFS: Active")
        else:
            trim_parts.append("NTFS: Disabled")
        if snapshot.trim_refs_enabled:
            trim_parts.append("ReFS: Active")
        else:
            trim_parts.append("ReFS: Disabled")
        self._trim_lbl.setText(f"SSD TRIM: {', '.join(trim_parts)}")
        if snapshot.trim_ntfs_enabled and snapshot.trim_refs_enabled:
            set_role(self._trim_lbl, "statusSuccess")
        else:
            set_role(self._trim_lbl, "statusWarning")

        # Volumes Table
        volumes = snapshot.volumes or []
        self._table.setRowCount(len(volumes))
        for row, vol in enumerate(volumes):
            item_vol = QTableWidgetItem(vol.drive_letter)
            item_fs = QTableWidgetItem(vol.file_system)

            free_gb = vol.free_bytes / (1024 ** 3)
            total_gb = vol.total_bytes / (1024 ** 3)
            space_str = f"{free_gb:.1f} GB free of {total_gb:.1f} GB" if total_gb > 0 else "—"
            item_space = QTableWidgetItem(space_str)

            item_status = QTableWidgetItem(vol.status_text)
            if vol.is_dirty:
                item_status.setForeground(QColor(semantic("error")))
            else:
                item_status.setForeground(QColor(semantic("success")))

            chk_str = "Yes (Queued)" if vol.chkdsk_queued else "No"
            item_chk = QTableWidgetItem(chk_str)
            if vol.chkdsk_queued:
                item_chk.setForeground(QColor(semantic("warning")))

            self._table.setItem(row, 0, item_vol)
            self._table.setItem(row, 1, item_fs)
            self._table.setItem(row, 2, item_space)
            self._table.setItem(row, 3, item_status)
            self._table.setItem(row, 4, item_chk)

    def _on_load_error(self, err: str) -> None:
        if not widget_is_valid(self):
            return
        self._progress.hide()
        self._health_badge.setText(f"Health check error: {err}")
        set_role(self._health_badge, "statusError")

    def _reboot_winre(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reboot to Windows Recovery",
            "Are you sure you want to restart your computer into the Windows Recovery Environment (WinRE)?\n\n"
            "Save all work before continuing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                subprocess.Popen(["shutdown.exe", "/r", "/o", "/t", "0"])
            except Exception as e:
                QMessageBox.critical(self, "Reboot Failed", f"Could not trigger WinRE reboot: {e}")

    def _queue_chkdsk(self) -> None:
        row = self._table.currentRow()
        drive = "C:"
        if row >= 0 and self._snapshot and self._snapshot.volumes and row < len(self._snapshot.volumes):
            drive = self._snapshot.volumes[row].drive_letter

        reply = QMessageBox.question(
            self,
            "Queue File System Check",
            f"Schedule a filesystem consistency check (autochk / chkdsk) on drive {drive} during next system reboot?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Requires admin privilege
                res = subprocess.run(
                    ["fsutil.exe", "dirty", "set", drive],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if res.returncode == 0:
                    QMessageBox.information(self, "Chkdsk Queued", f"Drive {drive} has been flagged for file system check on next restart.")
                    self.load()
                else:
                    QMessageBox.warning(self, "Operation Refused", f"Could not set dirty bit on {drive}:\n{res.stderr or res.stdout}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to execute command: {e}")

    def _fix_vss_writers(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reset VSS Writers",
            "This will restart the Volume Shadow Copy (VSS), Software Shadow Copy Provider (swprv), and Cryptographic Services (CryptSvc) to clear any stalled or failed writer locks.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._progress.show()

            def work(worker: Worker):
                return restart_vss_services()

            w = Worker(work)
            w.signals.result.connect(lambda res: self._on_vss_fixed(res) if widget_is_valid(self) else None)
            w.signals.error.connect(lambda err: QMessageBox.critical(self, "Error", f"Failed to restart VSS: {err}"))
            self._workers.append(w)
            QThreadPool.globalInstance().start(w)

    def _on_vss_fixed(self, result: tuple) -> None:
        self._progress.hide()
        ok, msg = result
        if ok:
            QMessageBox.information(self, "VSS Reset Complete", "VSS services were restarted successfully.\nRefreshing status...")
            self.load()
        else:
            QMessageBox.warning(self, "VSS Reset Warning", f"Some services could not be restarted:\n{msg}")

    def cancel(self) -> None:
        retire(self._workers)
        if widget_is_valid(self._progress):
            self._progress.hide()

    def _cancel_all(self) -> None:
        self.cancel()
