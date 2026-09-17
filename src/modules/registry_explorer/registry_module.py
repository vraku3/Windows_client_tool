# src/modules/registry_explorer/registry_module.py
import logging
import subprocess

from PyQt6.QtCore import QModelIndex, Qt, QThreadPool
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QSplitter,
    QStackedWidget, QTableWidget, QTreeView, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.worker import Worker
from modules.registry_explorer.registry_model import RegistryTreeModel
from ui.empty_state import EmptyState
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)

# Common keys for quick navigation
_QUICK_NAV = {
    "Run (HKCU)":    r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run",
    "Run (HKLM)":    r"HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Run",
    "Uninstall":     r"HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    "Environment":   r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    "Services":      r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services",
    "Policies":      r"HKEY_LOCAL_MACHINE\SOFTWARE\Policies",
}


class RegistryExplorerModule(BaseModule):
    name = "Registry Explorer"
    icon = "⚙️"
    description = "Read-only registry tree browser with search, quick-nav, and .reg export."
    requires_admin = False
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._widget: QWidget | None = None
        self._model: RegistryTreeModel | None = None
        self._workers: list = []

    def create_widget(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        # Toolbar
        tb = QHBoxLayout()
        tb.setContentsMargins(4, 4, 4, 4)

        self._path_bar = QLineEdit()
        self._path_bar.setPlaceholderText("Key path (e.g. HKEY_LOCAL_MACHINE\\SOFTWARE)")
        self._path_bar.returnPressed.connect(self._nav_to_path)
        tb.addWidget(self._path_bar, stretch=1)

        nav_btn = QPushButton("Go")
        nav_btn.clicked.connect(self._nav_to_path)
        tb.addWidget(nav_btn)

        # Quick-nav dropdown
        quick_btn = QPushButton("Quick Nav ▾")
        quick_menu = QMenu(quick_btn)
        for label, path in _QUICK_NAV.items():
            quick_menu.addAction(label, lambda p=path: self._path_bar.setText(p) or self._nav_to_path())
        quick_btn.setMenu(quick_menu)
        tb.addWidget(quick_btn)

        copy_path_btn = QPushButton("Copy Path")
        copy_path_btn.clicked.connect(self._copy_path)
        tb.addWidget(copy_path_btn)

        export_btn = QPushButton("Export .reg")
        export_btn.clicked.connect(self._export_reg)
        tb.addWidget(export_btn)

        layout.addLayout(tb)

        # Search bar
        search_row = QHBoxLayout()
        search_row.setContentsMargins(4, 0, 4, 0)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search key names (searches within current hive)…")
        search_row.addWidget(self._search_input, stretch=1)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._run_search)
        search_row.addWidget(search_btn)
        self._search_status = QLabel("")
        search_row.addWidget(self._search_status)
        layout.addLayout(search_row)

        # Main splitter: tree (left) + values (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._model = RegistryTreeModel()
        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setUniformRowHeights(True)
        self._tree.selectionModel().currentChanged.connect(self._on_key_selected)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._tree_context_menu)
        splitter.addWidget(self._tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._values_table = QTableWidget(0, 3)
        self._values_table.setHorizontalHeaderLabels(["Name", "Type", "Data"])
        hdr = self._values_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        center_header(self._values_table)
        self._values_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._values_table.verticalHeader().setVisible(False)

        self._values_stack = QStackedWidget()
        self._values_stack.addWidget(self._values_table)
        self._values_empty = EmptyState(
            "⚙️", "No values",
            "Select a registry key on the left to view its values.",
        )
        self._values_stack.addWidget(self._values_empty)
        self._values_stack.setCurrentIndex(1)
        right_layout.addWidget(self._values_stack)
        splitter.addWidget(right)
        splitter.setSizes([380, 620])

        layout.addWidget(splitter)

        # Keyboard shortcut: Ctrl+C copies selected value data
        QShortcut(QKeySequence("Ctrl+C"), self._values_table).activated.connect(self._copy_selected_value)

        self._widget = root
        return root

    def _on_key_selected(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid() or self._model is None:
            return
        path = self._model.key_path(current)
        self._path_bar.setText(path)
        values = self._model.values_for(current)
        self._values_table.setRowCount(0)
        for name, type_str, data in values:
            row = self._values_table.rowCount()
            self._values_table.insertRow(row)
            self._values_table.setItem(row, 0, centered_item(name))
            self._values_table.setItem(row, 1, centered_item(type_str))
            self._values_table.setItem(row, 2, centered_item(data))
        self._values_stack.setCurrentIndex(0 if self._values_table.rowCount() else 1)

    def _copy_path(self) -> None:
        idx = self._tree.currentIndex()
        if idx.isValid() and self._model:
            QApplication.clipboard().setText(self._model.key_path(idx))

    def _copy_selected_value(self) -> None:
        items = self._values_table.selectedItems()
        if items:
            QApplication.clipboard().setText(items[-1].text())

    def _export_reg(self) -> None:
        idx = self._tree.currentIndex()
        if not idx.isValid() or self._model is None:
            QMessageBox.information(self._widget, "Export", "Select a key first.")
            return
        path = self._model.key_path(idx)
        file, _ = QFileDialog.getSaveFileName(
            self._widget, "Export Registry Key", f"{path.split(chr(92))[-1]}.reg", "Registry Files (*.reg)"
        )
        if not file:
            return
        result = subprocess.run(
            ["reg", "export", path, file, "/y"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
        )
        if result.returncode == 0:
            QMessageBox.information(self._widget, "Export", f"Exported to:\n{file}")
        else:
            self._error_banner.set_error(
                f"Export failed: {result.stderr or result.stdout}")

    def _nav_to_path(self) -> None:
        """Expand tree to the path typed in path bar."""
        text = self._path_bar.text().strip()
        if not text or self._model is None:
            return
        # Find matching root
        parts = text.replace("/", "\\").split("\\")
        # Find root index
        for row in range(self._model.rowCount()):
            root_idx = self._model.index(row, 0)
            if self._model.data(root_idx) == parts[0]:
                idx = root_idx
                for part in parts[1:]:
                    self._tree.expand(idx)
                    found = False
                    for child_row in range(self._model.rowCount(idx)):
                        child = self._model.index(child_row, 0, idx)
                        if self._model.data(child, Qt.ItemDataRole.DisplayRole) == part:
                            idx = child
                            found = True
                            break
                    if not found:
                        break
                self._tree.setCurrentIndex(idx)
                self._tree.scrollTo(idx)
                return

    def _run_search(self) -> None:
        text = self._search_input.text().strip()
        if not text:
            return
        self._search_status.setText("Searching…")
        hive_idx = self._tree.currentIndex()
        if not hive_idx.isValid():
            hive_idx = self._model.index(0, 0) if self._model else QModelIndex()

        # Walk to find matching key names — runs in worker thread
        node = hive_idx.internalPointer() if hive_idx.isValid() else None

        def work(worker):
            results = []
            if node is None:
                return results
            stack = [node]
            while stack:
                if worker.is_cancelled:
                    break
                n = stack.pop()
                if text.lower() in n.name.lower():
                    results.append(f"{n.hive}\\{n.path}" if n.path else n.name)
                    if len(results) >= 100:
                        break
                try:
                    stack.extend(n.children())
                except Exception:
                    logger.warning("Ignored Exception", exc_info=True)
            return results

        def on_result(paths):
            count = len(paths)
            self._search_status.setText(
                f"{count} result(s){' (first 100)' if count == 100 else ''}"
            )

        w = Worker(work)
        w.signals.result.connect(on_result)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _tree_context_menu(self, pos) -> None:
        idx = self._tree.indexAt(pos)
        if not idx.isValid() or self._model is None:
            return
        menu = QMenu(self._tree)
        menu.addAction("Copy Key Path", self._copy_path)
        menu.addAction("Export .reg", self._export_reg)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.cancel_all_workers()
