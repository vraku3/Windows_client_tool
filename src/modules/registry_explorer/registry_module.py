# src/modules/registry_explorer/registry_module.py
import datetime
import logging
import subprocess

from PyQt6.QtCore import QModelIndex, Qt, QThreadPool
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QLineEdit, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTreeView, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.worker import Worker
from modules.registry_explorer import registry_scan
from modules.registry_explorer.registry_model import RegistryTreeModel

logger = logging.getLogger(__name__)

_QUICK_NAV = registry_scan.BOOKMARKS


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
        quick_btn = QPushButton("Bookmarks ▾")
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
        self._search_input.setPlaceholderText("Search text (under the current key)...")
        self._search_input.returnPressed.connect(self._run_search)
        search_row.addWidget(self._search_input, stretch=1)
        self._search_values = QCheckBox("Value data")
        search_row.addWidget(self._search_values)
        search_row.addWidget(QLabel("or changed in"))
        self._recent_combo = QComboBox()
        for label, days in (("(any time)", 0), ("last 1 day", 1), ("last 7 days", 7), ("last 30 days", 30)):
            self._recent_combo.addItem(label, days)
        search_row.addWidget(self._recent_combo)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._run_search)
        search_row.addWidget(search_btn)
        self._search_status = QLabel("")
        search_row.addWidget(self._search_status)
        layout.addLayout(search_row)
        self._results = QListWidget()
        self._results.setMaximumHeight(150)
        self._results.hide()
        self._results.itemActivated.connect(self._open_result)
        layout.addWidget(self._results)

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
        right_layout.addWidget(self._values_table)
        splitter.addWidget(right)
        splitter.setSizes([380, 620])

        layout.addWidget(splitter, 1)

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
            QMessageBox.critical(self._widget, "Export Failed", result.stderr or result.stdout)

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

    def _open_result(self, item) -> None:
        self._path_bar.setText(item.data(Qt.ItemDataRole.UserRole))
        self._nav_to_path()

    def _run_search(self) -> None:
        text = self._search_input.text().strip()
        days = self._recent_combo.currentData()
        root = self._path_bar.text().strip()
        if not root:
            self._search_status.setText("Select a key (or type a path) to search under first.")
            return
        if not text and not days:
            return
        self._search_status.setText("Searching...")
        since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)) if days else None
        in_values = self._search_values.isChecked()

        def work(worker):
            return registry_scan.scan(root, text=text, in_values=in_values, modified_since=since,
                                      is_cancelled=lambda: worker.is_cancelled)

        w = Worker(work)
        w.signals.result.connect(self._on_search_result)
        w.signals.error.connect(lambda e: self._search_status.setText(f"Search failed: {e}"))
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _on_search_result(self, res) -> None:
        if self._widget is None:
            return
        self._results.clear()
        for h in sorted(res.hits, key=lambda h: h.last_write, reverse=True):
            label = f"{h.path}   [{h.what}, {h.last_write:%Y-%m-%d %H:%M} UTC]"
            if h.detail:
                label += f"  {h.detail}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, h.path)
            self._results.addItem(item)
        self._results.setVisible(bool(res.hits))
        note = f"{len(res.hits)} hit(s) in {res.keys_visited} keys"
        if res.truncated:
            note += " (stopped at a limit)"
        if res.refused:
            note += f"; {len(res.refused)} key(s) could not be read"
        self._search_status.setText(note)

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
