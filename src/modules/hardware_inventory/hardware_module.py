import logging
import os
from datetime import datetime
from typing import Optional

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import set_role
from core.worker import COMWorker
from modules.hardware_inventory import hardware_reader as hr
from modules.hardware_inventory import hardware_tabs as ht

logger = logging.getLogger(__name__)

LOADING_TEXT = "Click Refresh to load."


def _alive(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


class _LoadingTab(QWidget):
    """Shows loading state, loads data in a COMWorker, hands it to a setup fn."""

    def __init__(self, loader_fn, setup_table_fn, parent=None):
        super().__init__(parent)
        self._loader = loader_fn
        self._setup_fn = setup_table_fn
        self._workers: list = []

        layout = QVBoxLayout(self)
        self._status = QLabel(LOADING_TEXT)
        set_role(self._status, "muted")
        self._refresh_btn = QPushButton("Refresh")
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._status)
        layout.addLayout(btn_row)
        layout.addWidget(self._progress)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._content, 1)

        self._refresh_btn.clicked.connect(self._load)

    def _load(self):
        self._refresh_btn.setEnabled(False)
        self._status.setText("Loading...")
        self._progress.show()
        # COMWorker initialises COM STA on the thread, which WMI needs.
        worker = COMWorker(self._loader)
        worker.signals.result.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_result(self, data):
        if not _alive(self):
            return
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText(f"Loaded at {datetime.now().strftime('%H:%M:%S')}.")
        for i in reversed(range(self._content_layout.count())):
            item = self._content_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        self._setup_fn(self._content_layout, data)

    def _on_error(self, err):
        if not _alive(self):
            return
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText(f"Error: {err}")

    def cancel(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    @property
    def is_unloaded(self) -> bool:
        return self._status.text() == LOADING_TEXT


def _tab_specs():
    """(title, loader, setup) for every tab, in display order."""
    return [
        ("Overview", hr.get_overview, ht.setup_kv),
        ("CPU", hr.get_cpu_info, ht.setup_kv),
        ("Memory", ht.load_memory, ht.setup_memory),
        ("Storage", ht.load_storage, ht.setup_storage),
        ("GPU", hr.get_gpu_info, ht.setup_dict(["Name", "RAM", "Driver Version", "Driver Date", "Resolution"])),
        ("Monitors", ht.load_monitors, ht.setup_monitors),
        ("Network Adapters", hr.get_network_info, ht.setup_dict(["Name", "IP", "MAC", "Speed", "Up"])),
        ("Firmware and Security", ht.load_firmware, ht.setup_firmware),
        ("Asset Record", ht.load_asset, ht.setup_asset),
    ]


class HardwareModule(BaseModule):
    name = "Hardware Info"
    icon = "🖥️"
    description = "Hardware inventory: memory slots, drives, monitors, firmware and an exportable asset record"
    requires_admin = False
    group = ModuleGroup.SYSTEM

    def create_widget(self) -> QWidget:
        outer = QWidget()
        vbox = QVBoxLayout(outer)
        vbox.setContentsMargins(0, 0, 0, 0)

        export_btn = QPushButton("Export HTML Report")
        export_btn.setFixedWidth(160)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(export_btn)
        vbox.addLayout(btn_row)

        tabs = QTabWidget()
        vbox.addWidget(tabs, 1)
        for title, loader, setup in _tab_specs():
            tabs.addTab(_LoadingTab(loader, setup), title)
        # After the addTab loop: adding the first tab fires currentChanged.
        tabs.currentChanged.connect(self._load_current_if_new)

        def do_export():
            path, _ = QFileDialog.getSaveFileName(
                outer, "Export Report", "hardware_report.html", "HTML (*.html)")
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(hr.generate_html_report())
                os.startfile(path)

        export_btn.clicked.connect(do_export)
        self._hw_tabs = tabs
        return outer

    def _load_current_if_new(self, _index: int = 0) -> None:
        tab = self._hw_tabs.currentWidget()
        if isinstance(tab, _LoadingTab) and tab.is_unloaded:
            tab._load()

    def get_refresh_interval(self) -> Optional[int]:
        return 120_000

    def refresh_data(self) -> None:
        """Refresh only the visible tab; the others load when first opened."""
        if hasattr(self, "_hw_tabs"):
            tab = self._hw_tabs.currentWidget()
            if isinstance(tab, _LoadingTab) and not tab.is_unloaded:
                tab._load()

    def on_activate(self) -> None:
        if hasattr(self, "_hw_tabs"):
            self._load_current_if_new()

    def on_deactivate(self) -> None:
        self._cancel_all_tabs()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()
        self._cancel_all_tabs()

    def _cancel_all_tabs(self) -> None:
        """_LoadingTab instances own their COMWorkers; BaseModule only covers
        workers on the module itself, so each tab is cancelled individually."""
        if not hasattr(self, "_hw_tabs"):
            return
        for i in range(self._hw_tabs.count()):
            tab = self._hw_tabs.widget(i)
            if hasattr(tab, "cancel"):
                tab.cancel()
