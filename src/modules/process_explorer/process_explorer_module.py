# src/modules/process_explorer/process_explorer_module.py
from __future__ import annotations
import logging
from typing import Optional

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QSplitter,
                              QTabWidget, QTreeView, QToolBar, QComboBox,
                              QLabel, QLineEdit, QPushButton, QMenu,
                              QMessageBox, QAbstractItemView)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.process_explorer.process_node import ProcessNode
from modules.process_explorer.process_collector import ProcessCollector
from modules.process_explorer.process_tree_model import ProcessTreeModel
from modules.process_explorer.process_actions import (
    kill_process, kill_tree, suspend_process, resume_process,
    set_priority, PRIORITY_LEVELS,
)
from modules.process_explorer.properties_dialog import ProcessPropertiesDialog
from modules.process_explorer.sysinternals_tab import SysinternalsTab
from modules.process_explorer.lower_pane.dll_view import DllView
from modules.process_explorer.lower_pane.handle_view import HandleView
from modules.process_explorer.lower_pane.thread_view import ThreadView
from modules.process_explorer.lower_pane.network_view import NetworkView
from modules.process_explorer.lower_pane.strings_view import StringsView
from modules.process_explorer.lower_pane.memory_map_view import MemoryMapView
from modules.process_explorer.lower_pane.activity_view import ActivityView
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)

_widget_valid = widget_is_valid


class ProcessExplorerModule(BaseModule):
    name = "Process Explorer"
    icon = "🔬"
    description = "Real-time process tree with Sysinternals-level detail"
    requires_admin = False
    group = ModuleGroup.PROCESS

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._tree_view: Optional[QTreeView] = None
        self._model: Optional[ProcessTreeModel] = None
        self._collector: Optional[ProcessCollector] = None
        self._lower_tabs: Optional[QTabWidget] = None
        self._dll_view: Optional[DllView] = None
        self._handle_view: Optional[HandleView] = None
        self._thread_view: Optional[ThreadView] = None
        self._network_view: Optional[NetworkView] = None
        self._strings_view: Optional[StringsView] = None
        self._memory_map_view: Optional[MemoryMapView] = None
        self._activity_view: Optional[ActivityView] = None
        self._selected_node: Optional[ProcessNode] = None

    def on_start(self, app) -> None:
        self.app = app
        self._collector = ProcessCollector(interval_ms=1000)
        pool = getattr(app, "thread_pool", None)
        self._collector.set_thread_pool(pool)

        if pool is None:
            # No pool to hand it to. This is reachable: CLAUDE.md records
            # that QThreadPool.globalInstance() has come back None in this
            # codebase's import graph, and as a composite CHILD this module
            # is started by whatever app the host was given. Falling over
            # here disabled the whole tab; the service names are only used
            # to mark rows, so read them inline instead.
            self._collector.set_service_names(self._fetch_service_names(None))
            return

        # Fetch service names once in background
        w = Worker(self._fetch_service_names)
        w.signals.result.connect(
            lambda names: self._collector.set_service_names(names) if self._collector else None
        )
        self._workers.append(w)
        pool.start(w)

    @staticmethod
    def _fetch_service_names(worker) -> set:
        try:
            import win32service
            sc = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
            services = win32service.EnumServicesStatus(sc, win32service.SERVICE_WIN32,
                                                       win32service.SERVICE_STATE_ALL)
            win32service.CloseServiceHandle(sc)
            return {s[0].lower() for s in services}
        except Exception as e:
            logger.warning("_fetch_service_names failed: %s", e)
            return set()

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        outer = QVBoxLayout(self._widget)
        outer.setContentsMargins(0, 0, 0, 0)

        # Module-level tabs: Processes | Sysinternals
        module_tabs = QTabWidget()
        outer.addWidget(module_tabs)

        # ── Processes tab ──────────────────────────────────────────────
        proc_widget = QWidget()
        proc_layout = QVBoxLayout(proc_widget)
        proc_layout.setContentsMargins(0, 0, 0, 0)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        proc_layout.addWidget(self._error_banner)

        # Toolbar
        toolbar = self._build_toolbar()
        proc_layout.addWidget(toolbar)

        # Splitter: tree (top) + lower pane (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Process tree
        self._model = ProcessTreeModel()
        self._tree_view = QTreeView()
        self._tree_view.setModel(self._model)
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree_view.customContextMenuRequested.connect(self._show_context_menu)
        self._tree_view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._tree_view.doubleClicked.connect(self._on_double_click)
        splitter.addWidget(self._tree_view)

        # Lower pane
        self._lower_tabs = QTabWidget()
        self._dll_view    = DllView()
        self._handle_view = HandleView()
        self._thread_view = ThreadView()
        self._network_view = NetworkView()
        self._strings_view = StringsView()
        self._memory_map_view = MemoryMapView()
        self._activity_view = ActivityView()
        if self.app:
            self._strings_view.set_thread_pool(self.app.thread_pool)
        self._lower_tabs.addTab(self._dll_view,        "DLLs")
        self._lower_tabs.addTab(self._handle_view,     "Handles")
        self._lower_tabs.addTab(self._thread_view,     "Threads")
        self._lower_tabs.addTab(self._network_view,    "Network")
        self._lower_tabs.addTab(self._strings_view,    "Strings")
        self._lower_tabs.addTab(self._memory_map_view, "Memory Map")
        self._lower_tabs.addTab(self._activity_view,   "Activity")
        self._lower_tabs.currentChanged.connect(self._on_lower_tab_changed)
        splitter.addWidget(self._lower_tabs)
        splitter.setSizes([600, 250])

        proc_layout.addWidget(splitter)
        module_tabs.addTab(proc_widget, "Processes")

        # ── Sysinternals tab ──────────────────────────────────────────
        sys_tab = SysinternalsTab()
        module_tabs.addTab(sys_tab, "Sysinternals")

        # Wire collector signals
        if self._collector:
            self._collector.snapshot_ready.connect(self._model.load_snapshot)
            self._collector.process_added.connect(self._on_process_added)
            self._collector.process_removed.connect(self._on_process_removed)
            self._collector.processes_updated.connect(self._on_processes_updated)

        return self._widget

    def _build_toolbar(self) -> QToolBar:
        tb = QToolBar()
        tb.setMovable(False)

        kill_action = QAction("Kill", tb)
        kill_action.triggered.connect(self._action_kill)
        tb.addAction(kill_action)

        suspend_action = QAction("Suspend", tb)
        suspend_action.triggered.connect(self._action_suspend)
        tb.addAction(suspend_action)

        tb.addSeparator()

        priority_btn = QPushButton("Priority ▼")
        priority_btn.setFlat(True)
        pm = QMenu(priority_btn)
        for level in PRIORITY_LEVELS:
            pm.addAction(level.replace("_", " ").title(),
                         lambda checked=False, lvl=level: self._action_set_priority(lvl))
        priority_btn.setMenu(pm)
        tb.addWidget(priority_btn)

        sysinfo_action = QAction("System Information", tb)
        sysinfo_action.triggered.connect(self._open_system_information)
        tb.addAction(sysinfo_action)

        find_action = QAction("Find Handle or DLL", tb)
        find_action.setShortcut("Ctrl+F")
        find_action.triggered.connect(self._open_find)
        tb.addAction(find_action)

        tb.addSeparator()

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search processes…")
        self._search_box.setMaximumWidth(200)
        tb.addWidget(self._search_box)

        interval_combo = QComboBox()
        for ms, label in [(500, "0.5s"), (1000, "1s"), (2000, "2s"), (5000, "5s")]:
            interval_combo.addItem(label, ms)
        interval_combo.setCurrentIndex(1)
        interval_combo.currentIndexChanged.connect(
            lambda i: self._collector.set_interval(interval_combo.currentData()) if self._collector else None)
        tb.addWidget(QLabel("Refresh:"))
        tb.addWidget(interval_combo)

        flat_btn = QPushButton("Flat")
        flat_btn.setCheckable(True)
        flat_btn.toggled.connect(lambda flat: self._model.set_flat_mode(flat) if self._model else None)
        tb.addWidget(flat_btn)

        return tb

    def on_activate(self) -> None:
        if self._collector and not self._collector._timer.isActive():
            self._collector.start()

    def _cancel_lower_pane(self) -> None:
        for view in (self._dll_view, self._handle_view, self._thread_view,
                      self._network_view, self._strings_view, self._memory_map_view,
                      self._activity_view):
            if view is not None:
                view.cancel()

    def _open_system_information(self) -> None:
        """Process Explorer's System Information window.

        Modeless and reused: opening it twice should raise the one that is
        already up, not stack a second copy polling the same counters.
        Imported here rather than at module scope so the pane does not drag
        the GPU and performance engines in at startup -- which is why it is
        in HIDDEN_IMPORTS.
        """
        from ui.system_information import \
            SystemInformationDialog

        existing = getattr(self, "_sysinfo_dialog", None)
        if existing is not None and _widget_valid(existing):
            existing.raise_()
            existing.activateWindow()
            return
        dialog = SystemInformationDialog(self._widget)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._sysinfo_dialog = dialog
        dialog.show()

    def _open_find(self) -> None:
        """Find handle or DLL. Modeless and reused, like System Information."""
        from modules.process_explorer.find_dialog import FindHandleDialog

        existing = getattr(self, "_find_dialog", None)
        if existing is not None and _widget_valid(existing):
            existing.raise_()
            existing.activateWindow()
            return
        pool = getattr(self.app, "thread_pool", None) if self.app else None
        dialog = FindHandleDialog(thread_pool=pool, parent=self._widget)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.pid_chosen.connect(self._select_pid)
        self._find_dialog = dialog
        dialog.show()

    def _select_pid(self, pid: int) -> None:
        """Jump the tree to a pid, so a search result can be followed.

        Finding what holds a file is only half the job; the other half is
        getting to it.
        """
        if self._model is None or self._tree_view is None:
            return
        node = self._model._snapshot.get(pid)
        if node is None:
            return
        self._selected_node = node
        self._refresh_lower_pane()

    def _close_find(self) -> None:
        dialog = getattr(self, "_find_dialog", None)
        if dialog is not None and _widget_valid(dialog):
            dialog.close()
        self._find_dialog = None

    def _close_system_information(self) -> None:
        dialog = getattr(self, "_sysinfo_dialog", None)
        if dialog is not None and _widget_valid(dialog):
            # Its timer and its PDH query outlive the pane otherwise: the
            # query lives in the performance-counter service, not here.
            dialog.close()
        self._sysinfo_dialog = None

    def on_deactivate(self) -> None:
        if self._collector:
            self._collector.stop()
        self._cancel_lower_pane()
        self.cancel_all_workers()

    def on_stop(self) -> None:
        if self._collector:
            self._collector.stop()
        self._cancel_lower_pane()
        self._close_system_information()
        self._close_find()
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        if self._model:
            return f"Process Explorer — {len(self._model._snapshot)} processes"
        return "Process Explorer"

    def get_refresh_interval(self) -> Optional[int]:
        # Collector already auto-refreshes every 1s; expose for external pause
        return 1_000

    def refresh_data(self) -> None:
        # Collector handles its own refresh loop; no-op here to avoid double-tick
        pass

    # ── Signal handlers ──────────────────────────────────────────────

    def _on_process_added(self, node: ProcessNode):
        if self._model:
            snap = dict(self._model._snapshot)
            snap[node.pid] = node
            self._model.load_snapshot(snap)

    def _on_process_removed(self, pid: int):
        if self._model:
            snap = dict(self._model._snapshot)
            snap.pop(pid, None)
            self._model.load_snapshot(snap)

    def _on_processes_updated(self, changed_pids: list):
        if self._model and self._collector:
            snap = self._collector.get_snapshot()
            self._model.update_nodes({p: snap[p] for p in changed_pids if p in snap})

    def _on_selection_changed(self, selected, deselected):
        indexes = self._tree_view.selectionModel().selectedRows() if self._tree_view else []
        if not indexes:
            self._selected_node = None
            return
        node: ProcessNode = indexes[0].internalPointer()
        self._selected_node = node
        self._refresh_lower_pane()

    def _on_lower_tab_changed(self, idx: int):
        self._refresh_lower_pane()

    def _refresh_lower_pane(self):
        if not self._selected_node or not self._lower_tabs:
            return
        pid = self._selected_node.pid
        idx = self._lower_tabs.currentIndex()
        if idx == 0:
            self._dll_view.load_pid(pid)
        elif idx == 1:
            self._handle_view.load_pid(pid)
        elif idx == 2:
            self._thread_view.load_pid(pid)
        elif idx == 3:
            self._network_view.load_pid(pid)
        elif idx == 4:
            self._strings_view.load_exe(self._selected_node.exe)
        elif idx == 5:
            self._memory_map_view.load_pid(pid)
        elif idx == 6:
            self._activity_view.load_pid(pid)

    def _on_double_click(self, index):
        if not index.isValid():
            return
        node: ProcessNode = index.internalPointer()
        dlg = ProcessPropertiesDialog(node, thread_pool=self.app.thread_pool if self.app else None,
                                      parent=self._widget)
        dlg.exec()

    # ── Actions ──────────────────────────────────────────────────────

    def _action_kill(self):
        if not self._selected_node:
            return
        pid, name = self._selected_node.pid, self._selected_node.name
        reply = QMessageBox.question(
            self._widget, "Kill Process",
            f"Kill {name} (PID {pid})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            ok, err = kill_process(pid)
            if not ok:
                self._error_banner.set_error(f"Kill failed: {err}")

    def _action_suspend(self):
        if not self._selected_node:
            return
        if self._selected_node.is_suspended:
            ok, err = resume_process(self._selected_node.pid)
        else:
            ok, err = suspend_process(self._selected_node.pid)
        if not ok:
            self._error_banner.set_error(f"Action failed: {err}")

    def _action_set_priority(self, level: str):
        if not self._selected_node:
            return
        ok, err = set_priority(self._selected_node.pid, level)
        if not ok:
            self._error_banner.set_error(f"Priority change failed: {err}")

    def _show_context_menu(self, pos):
        if not self._selected_node:
            return
        menu = QMenu(self._tree_view)
        menu.addAction("Properties", self._open_properties)
        menu.addSeparator()
        menu.addAction("Kill", self._action_kill)
        menu.addAction("Kill Tree", self._action_kill_tree)
        menu.addAction("Suspend / Resume", self._action_suspend)
        menu.addSeparator()
        menu.addAction("Open File Location", self._action_open_location)
        menu.addAction("Check VirusTotal", self._action_check_vt)
        menu.exec(self._tree_view.mapToGlobal(pos))

    def _open_properties(self):
        if self._selected_node:
            dlg = ProcessPropertiesDialog(
                self._selected_node,
                thread_pool=self.app.thread_pool if self.app else None,
                parent=self._widget,
            )
            dlg.exec()

    def _action_kill_tree(self):
        if not self._selected_node:
            return
        ok, errors = kill_tree(self._selected_node.pid)
        if not ok:
            self._error_banner.set_error(
                "Kill tree partially failed: " + "; ".join(errors))

    def _action_open_location(self):
        if self._selected_node and self._selected_node.exe:
            import subprocess
            try:
                subprocess.Popen(
                    ["explorer", "/select,", self._selected_node.exe],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as e:
                self._error_banner.set_error(f"Could not open file location: {e}")

    def _action_check_vt(self):
        if not self._selected_node:
            return
        exe = self._selected_node.exe
        if not exe:
            QMessageBox.information(self._widget, "VirusTotal", "No executable path available.")
            return
        api_key = ""
        if self.app:
            api_key = self.app.config.get("virustotal.api_key", "")
        if not api_key:
            QMessageBox.information(
                self._widget, "VirusTotal",
                "No API key configured. Set 'virustotal.api_key' in settings.")
            return
        from core.virustotal_client import VTClient, compute_sha256
        sha = compute_sha256(exe)
        if not sha:
            self._error_banner.set_error("Could not compute SHA256.")
            return
        client = VTClient(api_key=api_key)

        def do_check(worker):
            return client.check(sha)

        w = Worker(do_check)
        w.signals.result.connect(self._on_vt_result)
        self._workers.append(w)
        if self.app:
            self.app.thread_pool.start(w)

    def _on_vt_result(self, result):
        if not result.found:
            reply = QMessageBox.question(
                self._widget, "VirusTotal — Unknown File",
                "This file is unknown to VirusTotal. Submit for analysis?\n\n"
                "⚠ This will upload the file binary. Do not submit files containing sensitive data.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes and self._selected_node:
                api_key = self.app.config.get("virustotal.api_key", "") if self.app else ""
                from core.virustotal_client import VTClient
                client = VTClient(api_key=api_key)
                analysis_id = client.submit_file(self._selected_node.exe)
                if analysis_id:
                    QMessageBox.information(self._widget, "VirusTotal",
                                            f"Submitted. Analysis ID: {analysis_id}\nCheck virustotal.com for results.")
        else:
            icon = "🟢" if result.malicious == 0 else ("🟠" if result.malicious <= 3 else "🔴")
            QMessageBox.information(self._widget, "VirusTotal Result",
                                    f"{icon} {result.score}\nSHA256: {result.sha256}")
