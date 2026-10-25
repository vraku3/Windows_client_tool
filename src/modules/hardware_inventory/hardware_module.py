
from modules.hardware_inventory.help_widgets import (
    describe_control, explain_widget,
)
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
from core.worker import COMWorker, retire
from ui.empty_state import EmptyState
from modules.hardware_inventory import hardware_reader as hr
from modules.hardware_inventory import hardware_tabs as ht

logger = logging.getLogger(__name__)

LOADING_TEXT = "Click Refresh to load."


def _alive(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            # The taken item IS the layout, and takeAt() handed it to Python:
            # it is freed with its wrapper. deleteLater() freed it a second
            # time -- a double free that aborted whole-suite runs natively
            # (0xC0000409) whenever the garbage collector ran (2026-10-26).
            _clear_layout(item.layout())


class _LoadingTab(QWidget):
    """Shows loading state, loads data in a COMWorker, hands it to a setup fn."""

    def __init__(self, loader_fn, setup_table_fn, parent=None):
        super().__init__(parent)
        self._loader = loader_fn
        self._setup_fn = setup_table_fn
        self._workers: list = []
        self._current_worker = None

        layout = QVBoxLayout(self)
        self._status = QLabel(LOADING_TEXT)
        set_role(self._status, "muted")
        self._refresh_btn = QPushButton("Refresh")
        describe_control(self._refresh_btn, '_refresh_btn')
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

        self._empty = EmptyState("↻", "Hardware inventory", "Reading this tab's hardware information.",
                                 "Refresh", variant="empty")
        self._empty.action_triggered.connect(self._load)
        layout.addWidget(self._empty)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._content, 1)

        self._refresh_btn.clicked.connect(self._load)
        explain_widget(self)

    def _load(self):
        if not self._refresh_btn.isEnabled():
            return
        self._refresh_btn.setEnabled(False)
        self._status.setText("Loading...")
        self._progress.show()
        self._empty.set_variant("loading")
        self._empty.show()
        self._content.hide()
        # COMWorker initialises COM STA on the thread, which WMI needs.
        worker = COMWorker(self._loader)
        self._current_worker = worker
        worker.signals.result.connect(lambda data: self._deliver(worker, self._on_result, data))
        worker.signals.error.connect(lambda err: self._deliver(worker, self._on_error, err))
        worker.signals.cancelled.connect(lambda: self._deliver(worker, self._on_cancelled))
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _deliver(self, worker, callback, *args):
        if not _alive(self) or self._current_worker is not worker:
            return
        self._current_worker = None
        callback(*args)

    def _on_result(self, data):
        if not _alive(self):
            return
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._empty.hide()
        self._content.show()
        self._status.setText(f"Loaded at {datetime.now().strftime('%H:%M:%S')}.")
        _clear_layout(self._content_layout)
        self._setup_fn(self._content_layout, data)

    def _on_error(self, err):
        if not _alive(self):
            return
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText(f"Error: {err}")
        self._empty.set_variant("error", reason=str(err))
        self._empty.show()

    def _on_cancelled(self):
        if not _alive(self):
            return
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText(LOADING_TEXT)
        self._empty.set_variant("empty", reason="The read was cancelled. Refresh to try again.")

    def cancel(self) -> None:
        retire(self._workers)
        self._current_worker = None
        if _alive(self) and not self._refresh_btn.isEnabled():
            self._on_cancelled()

    @property
    def is_unloaded(self) -> bool:
        return self._status.text() == LOADING_TEXT


def _tab_specs(app_data_dir=None):
    """(title, loader, setup) for every tab, in display order.

    ``app_data_dir`` is only used by the Asset Record tab, to compare this
    run's record against the one saved on this machine last time (a drive,
    DIMM or monitor that changed). Default-arg capture (not a closure over
    the outer name) so each tab's loader is a plain, independently-callable
    function, same as every other entry here.
    """
    return [
        ("Overview", ht.load_overview, ht.setup_overview),
        ("CPU", hr.get_cpu_info, ht.setup_kv),
        ("Memory", ht.load_memory, ht.setup_memory),
        ("Storage", ht.load_storage, ht.setup_storage),
        ("GPU", hr.get_gpu_info, ht.setup_dict(["Name", "RAM", "Driver Version", "Driver Date", "Resolution"])),
        ("Monitors", ht.load_monitors, ht.setup_monitors),
        ("Network Adapters", hr.get_network_info, ht.setup_dict(["Name", "IP", "MAC", "Speed", "Up"])),
        ("Devices", ht.load_devices, ht.setup_devices),
        ("Firmware and Security", ht.load_firmware, ht.setup_firmware),
        ("Asset Record", lambda worker=None, _dir=app_data_dir: ht.load_asset(worker, _dir), ht.setup_asset),
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
        describe_control(export_btn, 'export_btn')
        export_btn.setFixedWidth(160)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(export_btn)
        vbox.addLayout(btn_row)

        tabs = QTabWidget()
        vbox.addWidget(tabs, 1)
        app_data_dir = getattr(self.app, "app_data_dir", None) if hasattr(self, "app") else None
        for title, loader, setup in _tab_specs(app_data_dir):
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
        explain_widget(outer, introduction=True)
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
