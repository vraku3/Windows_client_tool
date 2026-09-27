from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTabWidget,
    QHeaderView, QProgressBar, QLabel, QMessageBox,
    QLineEdit, QDialog, QDialogButtonBox, QFormLayout,
)
from PyQt6.QtCore import QThreadPool, QUrl
from PyQt6.QtGui import QDesktopServices
import logging
import os
from typing import Optional

from core.base_module import BaseModule
from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.worker import Worker, COMWorker
from modules.startup_manager.startup_reader import (
    get_registry_entries,
    set_registry_entry_enabled,
    remove_registry_entry,
    add_registry_entry,
    get_machine_registry_entries,
    get_runonce_entries,
    get_startup_folder_entries,
    set_startup_folder_entry_enabled,
    remove_startup_folder_entry,
    get_startup_folder_path,
    get_scheduled_task_entries,
    get_service_entries,
    get_browser_extensions,
    get_shell_extensions,
    get_icon_overlay_handlers,
)

from modules.startup_manager.unified_tab import UnifiedStartupTab

logger = logging.getLogger(__name__)

COLUMNS = ["Name", "Command/Path", "Status", "Notes"]


def _open_file_location(command: str) -> None:
    """Reveal the executable/script behind a startup command in Explorer."""
    if not command:
        return
    path = command.strip()
    if path.lower().startswith(("http://", "https://", "shell:", "ms-", "microsoft-edge")):
        return
    # Strip quotes and arguments: find first existing path from command segments
    import shlex
    try:
        parts = shlex.split(path)
    except ValueError:
        parts = path.split()
    for p in parts:
        p = p.strip('"')
        if not p:
            continue
        if os.path.isfile(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))
            return
        if os.path.isdir(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))
            return
    QDesktopServices.openUrl(QUrl.fromLocalFile(get_startup_folder_path()))


class _AddEntryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Startup Entry")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._name = QLineEdit()
        self._command = QLineEdit()
        self._command.setPlaceholderText(r"C:\path\to\program.exe --flag")
        form.addRow("Name:", self._name)
        form.addRow("Command:", self._command)
        layout.addLayout(form)
        hint = QLabel("Adds a value to HKCU\\...\\CurrentVersion\\Run.")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self):
        if not self._name.text().strip():
            QMessageBox.warning(self, "Missing Name", "Please enter a name.")
            return
        if not self._command.text().strip():
            QMessageBox.warning(self, "Missing Command", "Please enter a command.")
            return
        self.accept()

    def values(self):
        return self._name.text().strip(), self._command.text().strip()


class _StartupTab(QWidget):
    def __init__(self, loader_fn, enable_fn=None, disable_fn=None,
                 remove_fn=None, add_fn=None, use_com=False, read_only=False,
                 parent=None):
        super().__init__(parent)
        self._loader = loader_fn
        self._enable_fn = enable_fn
        self._disable_fn = disable_fn
        self._remove_fn = remove_fn
        self._add_fn = add_fn
        self._use_com = use_com
        self._read_only = read_only
        self._entries = []
        self._filter = ""
        self._worker: Optional[Worker] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._loaded = False
        self._setup_ui()

    def auto_scan(self):
        """Auto-load on first activate (idempotent — no-op if already loaded)."""
        if not self._loaded:
            self._loaded = True
            self._load()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._enable_btn = QPushButton("Enable")
        self._disable_btn = QPushButton("Disable")
        self._remove_btn = QPushButton("Remove")
        self._add_btn = QPushButton("Add")
        self._open_btn = QPushButton("Open Location")
        self._status = QLabel("")

        self._enable_btn.setEnabled(False)
        self._disable_btn.setEnabled(False)
        self._remove_btn.setEnabled(False)
        self._open_btn.setEnabled(False)

        if self._read_only:
            self._enable_btn.hide()
            self._disable_btn.hide()
        if self._remove_fn is None:
            self._remove_btn.hide()
        if self._add_fn is None:
            self._add_btn.hide()

        toolbar.addWidget(self._refresh_btn)
        if not self._read_only:
            toolbar.addWidget(self._enable_btn)
            toolbar.addWidget(self._disable_btn)
        toolbar.addWidget(self._remove_btn)
        toolbar.addWidget(self._open_btn)
        toolbar.addWidget(self._add_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status)
        layout.addLayout(toolbar)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by name or command…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._search)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, 1)

        self._refresh_btn.clicked.connect(self._load)
        self._enable_btn.clicked.connect(self._enable_selected)
        self._disable_btn.clicked.connect(self._disable_selected)
        self._remove_btn.clicked.connect(self._remove_selected)
        self._add_btn.clicked.connect(self._add_new)
        self._open_btn.clicked.connect(self._open_selected)
        self._table.itemDoubleClicked.connect(self._open_selected)
        self._table.selectionModel().selectionChanged.connect(
            self._on_selection_changed
        )

    def _make_status_dot(self, enabled: bool) -> QLabel:
        """Return a small colored dot label: green=enabled, red=disabled."""
        color = "#44BB44" if enabled else "#DD3333"
        text = "Enabled" if enabled else "Disabled"
        lbl = QLabel(f"<span style='color:{color};'>&#x25CF;</span> {text}")
        lbl.setStyleSheet("QLabel { background: transparent; padding: 2px 4px; }")
        return lbl

    def _load(self):
        self._loaded = True
        self._refresh_btn.setEnabled(False)
        self._status.setText("Loading...")
        self._progress.show()
        self._table.setRowCount(0)

        loader = self._loader
        WorkerClass = COMWorker if self._use_com else Worker
        self._worker = WorkerClass(lambda _w: loader())
        self._worker.signals.result.connect(self._on_result)
        self._worker.signals.error.connect(self._on_error)
        self._thread_pool.start(self._worker)

    def _on_result(self, entries):
        self._entries = entries
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._render()

    def _render(self):
        """(Re)populate the table applying the current filter."""
        flt = self._filter.lower()
        visible = [
            e for e in self._entries
            if not flt or flt in e.name.lower() or flt in e.command.lower()
        ]
        self._table.setRowCount(len(visible))
        for r, e in enumerate(visible):
            self._table.setItem(r, 0, centered_item(e.name))
            self._table.setItem(r, 1, centered_item(e.command))
            self._table.setCellWidget(r, 2, self._make_status_dot(e.enabled))
            self._table.setItem(r, 3, centered_item(e.extra))
        shown = len(visible)
        total = len(self._entries)
        self._status.setText(
            f"{shown}/{total} entries" if flt else f"{total} entries"
        )

    def _on_filter_changed(self, text: str) -> None:
        self._filter = text
        self._render()

    def _on_error(self, err):
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status.setText(f"Error: {err}")

    def _on_selection_changed(self):
        has_sel = bool(self._table.selectedItems())
        self._enable_btn.setEnabled(has_sel and not self._read_only)
        self._disable_btn.setEnabled(has_sel and not self._read_only)
        self._remove_btn.setEnabled(has_sel and self._remove_fn is not None)
        self._open_btn.setEnabled(has_sel)

    def _selected_entry(self):
        rows = {i.row() for i in self._table.selectedIndexes()}
        if rows:
            r = min(rows)
            flt = self._filter.lower()
            visible = [
                e for e in self._entries
                if not flt or flt in e.name.lower() or flt in e.command.lower()
            ]
            if r < len(visible):
                return visible[r]
        return None

    def _open_selected(self):
        e = self._selected_entry()
        if e:
            _open_file_location(e.command)

    def _enable_selected(self):
        e = self._selected_entry()
        if e and self._enable_fn:
            try:
                self._enable_fn(e.name)
                self._status.setText(f"Enabled: {e.name}")
                self._load()
            except Exception as ex:
                logger.warning("Failed to enable %s: %s", e.name, ex)
                self._status.setText(f"Error: {ex}")

    def _disable_selected(self):
        e = self._selected_entry()
        if e and self._disable_fn:
            reply = QMessageBox.question(
                self, "Disable Startup Item",
                f"Disable '{e.name}'?\n\nThis will prevent it from starting automatically.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                self._disable_fn(e.name)
                self._status.setText(f"Disabled: {e.name}")
                self._load()
            except Exception as ex:
                logger.warning("Failed to disable %s: %s", e.name, ex)
                self._status.setText(f"Error: {ex}")

    def _remove_selected(self):
        e = self._selected_entry()
        if e and self._remove_fn:
            reply = QMessageBox.question(
                self, "Remove Startup Item",
                f"Remove '{e.name}'?\n\n"
                f"This permanently deletes it (unlike Disable).\n"
                f"{e.command}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                self._remove_fn(e.name)
                self._status.setText(f"Removed: {e.name}")
                self._load()
            except Exception as ex:
                logger.warning("Failed to remove %s: %s", e.name, ex)
                self._status.setText(f"Error: {ex}")

    def _add_new(self):
        if not self._add_fn:
            return
        dlg = _AddEntryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, command = dlg.values()
            try:
                self._add_fn(name, command)
                self._status.setText(f"Added: {name}")
                self._load()
            except Exception as ex:
                logger.warning("Failed to add %s: %s", name, ex)
                self._status.setText(f"Error: {ex}")

    def _cancel_all(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None


class StartupItemsModule(BaseModule):
    """The startup-items view. A child of `StartupBootModule` below."""

    name = "Startup Manager"
    icon = "🚀"
    description = "Manage startup programs and services"
    requires_admin = False
    group = ModuleGroup.MANAGE

    def create_widget(self) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)

        tabs = QTabWidget()

        app_dir = getattr(getattr(self, "app", None), "app_data_dir", None)
        history = os.path.join(app_dir, "startup_history.json") if app_dir else None
        tabs.addTab(UnifiedStartupTab(history), "Everything")
        tabs.addTab(
            _StartupTab(
                get_registry_entries,
                enable_fn=lambda n: set_registry_entry_enabled(n, True),
                disable_fn=lambda n: set_registry_entry_enabled(n, False),
                remove_fn=remove_registry_entry,
                add_fn=add_registry_entry,
            ),
            "Registry",
        )
        tabs.addTab(
            _StartupTab(
                get_machine_registry_entries,
                read_only=True,
            ),
            "Machine (HKLM) Run",
        )
        tabs.addTab(
            _StartupTab(
                get_startup_folder_entries,
                enable_fn=lambda n: set_startup_folder_entry_enabled(n, True),
                disable_fn=lambda n: set_startup_folder_entry_enabled(n, False),
                remove_fn=remove_startup_folder_entry,
            ),
            "Startup Folder",
        )
        tabs.addTab(
            _StartupTab(
                get_runonce_entries,
                read_only=True,
            ),
            "RunOnce",
        )
        tabs.addTab(
            _StartupTab(get_scheduled_task_entries, use_com=True, read_only=True),
            "Scheduled Tasks",
        )
        tabs.addTab(
            _StartupTab(get_service_entries, read_only=True),
            "Services",
        )
        tabs.addTab(
            _StartupTab(get_browser_extensions, read_only=True),
            "Browser Extensions",
        )
        tabs.addTab(
            _StartupTab(get_shell_extensions, read_only=True),
            "Shell Extensions",
        )
        tabs.addTab(
            _StartupTab(get_icon_overlay_handlers, read_only=True),
            "Icon Overlays",
        )

        self._startup_tabs = tabs
        tabs.currentChanged.connect(self._on_tab_changed)

        footer = QHBoxLayout()
        open_folder_btn = QPushButton("Open Startup Folder")
        open_folder_btn.clicked.connect(self._open_startup_folder)
        footer.addWidget(open_folder_btn)
        footer.addStretch()
        outer.addWidget(tabs, 1)
        outer.addLayout(footer)

        return container

    def _open_startup_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(get_startup_folder_path()))

    def _on_tab_changed(self, index: int) -> None:
        tab = self._startup_tabs.widget(index)
        if hasattr(tab, "auto_scan"):
            tab.auto_scan()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        if hasattr(self, "_startup_tabs"):
            tab = self._startup_tabs.currentWidget()
            if hasattr(tab, "auto_scan"):
                tab.auto_scan()
            elif hasattr(tab, "_load"):
                tab._load()

    def on_activate(self) -> None:
        if hasattr(self, "_startup_tabs"):
            tab = self._startup_tabs.currentWidget()
            if hasattr(tab, "auto_scan"):
                tab.auto_scan()

    def on_deactivate(self) -> None:
        self._cancel_all_tab_workers()

    def _cancel_all_tab_workers(self) -> None:
        if not hasattr(self, "_startup_tabs"):
            return
        for i in range(self._startup_tabs.count()):
            tab = self._startup_tabs.widget(i)
            if hasattr(tab, "_cancel_all"):
                tab._cancel_all()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()
        self._cancel_all_tab_workers()


class StartupBootModule(CompositeModule):
    """Everything that decides what happens between power-on and a desktop.

    These were three sidebar entries in two different groups: what starts, how
    fast it started, and the power and boot settings that govern both.
    """

    name = "Startup & Boot"
    icon = "🚀"
    description = "Startup items, boot timing, and power and boot configuration"
    group = ModuleGroup.SYSTEM

    def __init__(self):
        super().__init__()
        from modules.boot_analyzer.boot_analyzer_module import BootAnalyzerModule
        from modules.power_boot.power_module import PowerBootModule

        self.children = [StartupItemsModule(), BootAnalyzerModule(), PowerBootModule()]
