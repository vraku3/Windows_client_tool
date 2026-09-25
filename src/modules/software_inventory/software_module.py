import csv
import subprocess
import winreg
from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QHeaderView, QLineEdit, QLabel,
    QProgressBar, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.table_ui import centered_item, center_header
import logging
logger = logging.getLogger(__name__)


@dataclass
class SoftwareEntry:
    name: str
    version: str
    publisher: str
    install_date: str
    size_mb: str
    type_: str          # "64-bit" | "32-bit" | "User"
    source: str         # "registry"
    uninstall_string: str = ""


def format_install_date(raw: str) -> str:
    """`20260907` -> `2026-09-07`. Anything that is not a real YYYYMMDD date is
    shown as it was written: installers put all sorts of things in this value,
    and a guess would be worse than the original text."""
    import datetime
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        try:
            return datetime.datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return raw
    return raw


def format_estimated_size(raw: str) -> str:
    """The registry's `EstimatedSize` is in KB. A missing value or a 0 means
    the installer never said -- not that the program takes no space -- so it
    is shown as a dash rather than as "0.0 MB", which read as a measurement."""
    try:
        kilobytes = int((raw or "").strip())
    except ValueError:
        return "—"
    if kilobytes <= 0:
        return "—"
    return f"{kilobytes / 1024:.1f} MB"


def _read_registry_uninstall(hive, key_path: str, type_label: str) -> List[SoftwareEntry]:
    entries = []
    try:
        with winreg.OpenKey(hive, key_path) as k:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(k, i)
                    with winreg.OpenKey(k, subkey_name) as sk:
                        def rv(name, default=""):
                            try:
                                return str(winreg.QueryValueEx(sk, name)[0])
                            except OSError:
                                return default
                        name = rv("DisplayName")
                        if not name:
                            i += 1
                            continue
                        entries.append(SoftwareEntry(
                            name=name,
                            version=rv("DisplayVersion"),
                            publisher=rv("Publisher"),
                            install_date=format_install_date(rv("InstallDate")),
                            size_mb=format_estimated_size(rv("EstimatedSize")),
                            type_=type_label,
                            source="registry",
                            uninstall_string=rv("UninstallString"),
                        ))
                    i += 1
                except OSError:
                    # OSError here is how winreg says the enumeration is
                    # finished. Control flow, not a failure.
                    logger.debug("_read_registry_uninstall: registry enumeration finished", exc_info=True)
                    break
    except OSError:
        logger.debug("Ignored OSError", exc_info=True)
    return entries


def fetch_software() -> List[SoftwareEntry]:
    all_entries: List[SoftwareEntry] = []

    # 64-bit
    all_entries += _read_registry_uninstall(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        "64-bit",
    )
    # 32-bit
    all_entries += _read_registry_uninstall(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        "32-bit",
    )
    # Current user
    all_entries += _read_registry_uninstall(
        winreg.HKEY_CURRENT_USER,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        "User",
    )

    # Deduplicate by normalized name
    seen: set = set()
    deduped: List[SoftwareEntry] = []
    for e in all_entries:
        key = e.name.lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    deduped.sort(key=lambda e: e.name.lower())
    return deduped


COLUMNS = ["Name", "Version", "Publisher", "Install Date", "Size", "Type", "Source"]


class SoftwareModule(BaseModule):
    name = "Software Inventory"
    icon = "📦"
    description = "Installed software inventory"
    requires_admin = False
    group = ModuleGroup.TOOLS

    def create_widget(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        # --- Toolbar ---
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        uninstall_btn = QPushButton("Uninstall")
        export_btn = QPushButton("Export CSV")
        filter_edit = QLineEdit()
        filter_edit.setPlaceholderText("Filter by name or publisher...")
        status_lbl = QLabel("Click Refresh to load.")
        uninstall_btn.setEnabled(False)

        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(uninstall_btn)
        toolbar.addWidget(export_btn)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(filter_edit, 1)
        toolbar.addWidget(status_lbl)
        layout.addLayout(toolbar)

        # --- Progress bar ---
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setFixedHeight(4)
        progress.hide()
        layout.addWidget(progress)

        # --- Table ---
        table = QTableWidget(0, len(COLUMNS))
        table.setHorizontalHeaderLabels(COLUMNS)
        center_header(table)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(COLUMNS)):
            table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents
            )
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(table, 1)

        # Mutable reference so closures can update it
        entries_ref: list = [[]]

        # ------------------------------------------------------------------
        def populate(entries: List[SoftwareEntry], filter_text: str = "") -> None:
            ft = filter_text.lower()
            visible = [
                e for e in entries
                if not ft or ft in e.name.lower() or ft in e.publisher.lower()
            ]
            table.setRowCount(len(visible))
            for r, e in enumerate(visible):
                vals = [
                    e.name, e.version, e.publisher, e.install_date,
                    e.size_mb, e.type_, e.source,
                ]
                for c, v in enumerate(vals):
                    table.setItem(r, c, centered_item(str(v)))
                # Store the full SoftwareEntry on the Name cell for later retrieval
                table.item(r, 0).setData(Qt.ItemDataRole.UserRole, e)

        def do_refresh() -> None:
            refresh_btn.setEnabled(False)
            uninstall_btn.setEnabled(False)
            status_lbl.setText("Loading...")
            progress.show()
            table.setRowCount(0)

            self._worker = Worker(lambda _w: fetch_software())

            def on_result(entries: List[SoftwareEntry]) -> None:
                entries_ref[0] = entries
                refresh_btn.setEnabled(True)
                progress.hide()
                populate(entries, filter_edit.text())
                status_lbl.setText(f"{len(entries)} programs installed.")

            def on_error(err: str) -> None:
                refresh_btn.setEnabled(True)
                progress.hide()
                status_lbl.setText(f"Error: {err}")

            self._worker.signals.result.connect(on_result)
            self._worker.signals.error.connect(on_error)
            self._workers.append(self._worker)
            self.thread_pool.start(self._worker)

        def do_uninstall() -> None:
            rows = {idx.row() for idx in table.selectedIndexes()}
            if not rows:
                return
            r = min(rows)
            item = table.item(r, 0)
            if item is None:
                return
            entry: SoftwareEntry = item.data(Qt.ItemDataRole.UserRole)
            if not entry or not entry.uninstall_string:
                status_lbl.setText("No uninstall string available.")
                return
            reply = QMessageBox.question(
                w,
                "Uninstall",
                f"Uninstall '{entry.name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                subprocess.Popen(entry.uninstall_string, shell=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
                status_lbl.setText(f"Uninstall launched for: {entry.name}")

        def do_export() -> None:
            path, _ = QFileDialog.getSaveFileName(
                w, "Export CSV", "software.csv", "CSV (*.csv)"
            )
            if not path:
                return
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(COLUMNS)
                for e in entries_ref[0]:
                    writer.writerow(
                        [e.name, e.version, e.publisher, e.install_date,
                         e.size_mb, e.type_, e.source]
                    )
            status_lbl.setText(f"Exported {len(entries_ref[0])} rows.")

        # ------------------------------------------------------------------
        refresh_btn.clicked.connect(do_refresh)
        uninstall_btn.clicked.connect(do_uninstall)
        export_btn.clicked.connect(do_export)
        filter_edit.textChanged.connect(
            lambda txt: populate(entries_ref[0], txt)
        )
        table.selectionModel().selectionChanged.connect(
            lambda: uninstall_btn.setEnabled(bool(table.selectedItems()))
        )

        self._software_load_fn = do_refresh
        return w

    def on_start(self, app=None) -> None:
        pass

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_refresh_interval(self) -> Optional[int]:
        return 120_000

    def refresh_data(self) -> None:
        if hasattr(self, "_software_load_fn"):
            self._software_load_fn()

    def on_activate(self) -> None:
        if not getattr(self, "_software_loaded", False) and hasattr(self, "_software_load_fn"):
            self._software_loaded = True
            self._software_load_fn()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()
