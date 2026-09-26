# src/modules/env_vars/env_vars_module.py
import logging
import os
import winreg
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import centered_item, fit_table, set_role
from core.worker import Worker
from modules.env_vars import env_ops, path_analysis
from modules.env_vars.env_ops import EnvVar, SYS_PATH as _SYS_PATH, USR_PATH as _USR_PATH

logger = logging.getLogger(__name__)


class _EnvPanel(QWidget):
    """Single-scope (System or User) env-var panel."""

    def __init__(self, hive, reg_path: str, label: str, requires_admin: bool = False):
        super().__init__()
        self._hive = hive
        self._reg_path = reg_path
        self._label = label
        self._requires_admin = requires_admin
        self._refreshed = False
        self._all_rows: List[EnvVar] = []
        self._duplicate_target: Optional["_EnvPanel"] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{label} Variables</b>"))
        self._status = QLabel("")
        set_role(self._status, "muted")
        header.addWidget(self._status)
        header.addStretch()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter name or value...")
        self._search.setMaximumWidth(240)
        self._search.textChanged.connect(self._apply_filter)
        header.addWidget(self._search)
        layout.addLayout(header)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Name", "Type", "Value", "Expanded"])
        fit_table(self._table, stretch=[2, 3], content=[0, 1])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        for text, slot in [("Add", self._add), ("Edit", self._edit),
                           ("Delete", self._delete), ("Copy to other scope", self._duplicate)]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._refreshed:
            self._refreshed = True
            self.refresh()

    def set_duplicate_target(self, target: "_EnvPanel") -> None:
        self._duplicate_target = target

    def variables(self) -> List[EnvVar]:
        return list(self._all_rows)

    def refresh(self) -> None:
        rows, why = env_ops.read_env(self._hive, self._reg_path)
        if rows is None:
            self._status.setText(f"Could not read: {why}")
            return
        self._status.setText(f"{len(rows)} variable(s)")
        self._all_rows = rows
        self._apply_filter(self._search.text())

    def _apply_filter(self, text: str) -> None:
        q = text.lower()
        rows = [v for v in self._all_rows if not q or q in v.name.lower() or q in v.value.lower()]
        self._table.setRowCount(len(rows))
        for r, v in enumerate(rows):
            kind = "Expandable" if v.kind == winreg.REG_EXPAND_SZ else "String"
            expanded = path_analysis.expand(v.value) if v.kind == winreg.REG_EXPAND_SZ else ""
            if expanded == v.value:
                expanded = ""
            for c, txt in enumerate((v.name, kind, v.value, expanded)):
                self._table.setItem(r, c, centered_item(txt))

    def _selected(self) -> Optional[EnvVar]:
        row = self._table.currentRow()
        item = self._table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        for v in self._all_rows:
            if v.name == item.text():
                return v
        return None

    def _report(self, res: env_ops.OpResult) -> None:
        if res.ok:
            self._status.setText(res.message)
        else:
            QMessageBox.critical(self, "Environment Variables", res.message)
        self.refresh()

    def _add(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Variable", "Variable name:")
        if not ok or not name.strip():
            return
        value, ok = QInputDialog.getText(self, "Add Variable", f"Value for {name}:")
        if not ok:
            return
        self._report(env_ops.set_verified(self._hive, self._reg_path, name.strip(), value))

    def _edit(self) -> None:
        var = self._selected()
        if var is None:
            return
        value, ok = QInputDialog.getText(self, "Edit Variable", f"Value for {var.name}:", text=var.value)
        if not ok or value == var.value:
            return
        if not confirm_destructive(
                self, f"Edit {self._label} variable",
                f"Change {var.name}?", detail=f"Old: {var.value}\nNew: {value}", irreversible=False):
            return
        self._report(env_ops.set_verified(self._hive, self._reg_path, var.name, value))

    def _delete(self) -> None:
        var = self._selected()
        if var is None:
            return
        if not confirm_destructive(
                self, f"Delete {self._label} variable", f"Delete '{var.name}'?",
                detail=f"Current value: {var.value}"):
            return
        self._report(env_ops.delete_verified(self._hive, self._reg_path, var.name))

    def _duplicate(self) -> None:
        var = self._selected()
        target = self._duplicate_target
        if var is None or target is None:
            return
        if not confirm_destructive(
                self, "Copy variable", f"Write {var.name} into {target._label} scope?",
                detail=f"Value: {var.value}", irreversible=False):
            return
        target._report(env_ops.set_verified(target._hive, target._reg_path, var.name, var.value))


class _PathPane(QWidget):
    """PATH findings for both scopes."""

    def __init__(self, thread_pool):
        super().__init__()
        self._pool = thread_pool
        self._workers: list = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>PATH analysis</b>"))
        self._status = QLabel("Analyse checks missing folders, duplicates, shadowed executables and "
                              "user-writable folders early in the system PATH.")
        set_role(self._status, "muted")
        head.addWidget(self._status, 1)
        self._btn = QPushButton("Analyse PATH")
        self._btn.clicked.connect(self.run)
        head.addWidget(self._btn)
        layout.addLayout(head)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Scope", "Entry", "Finding", "Detail"])
        fit_table(self._table, stretch=[3], content=[0, 1, 2])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    def cancel(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    def run(self) -> None:
        self._btn.setEnabled(False)
        self._status.setText("Analysing...")

        def work(_w):
            out = []
            for scope, hive, path, is_sys in (("System", winreg.HKEY_LOCAL_MACHINE, _SYS_PATH, True),
                                              ("User", winreg.HKEY_CURRENT_USER, _USR_PATH, False)):
                vars_, why = env_ops.read_env(hive, path)
                val = next((v.value for v in vars_ or [] if v.name.lower() == "path"), None)
                if vars_ is None:
                    out.append((scope, None, why))
                elif val is not None:
                    out.append((scope, path_analysis.analyse(val, is_sys), ""))
            return out

        w = Worker(work)
        w.signals.result.connect(self._done)
        w.signals.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        w.signals.finished.connect(lambda: self._btn.setEnabled(True))
        self._workers.append(w)
        self._pool.start(w)

    def _done(self, results) -> None:
        rows = []
        for scope, rep, why in results:
            if rep is None:
                rows.append((scope, "", "unknown", f"Could not read: {why}"))
                continue
            rows.extend((scope, str(f.entry_index + 1), f.kind, f.message) for f in rep.findings)
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, txt in enumerate(row):
                item = QTableWidgetItem(txt)
                if row[2] in ("writable", "missing", "unexpanded"):
                    item.setForeground(QColor(semantic("warning")))
                self._table.setItem(r, c, item)
        self._status.setText(f"{len(rows)} finding(s)" if rows else "No findings.")


class EnvVarsModule(BaseModule):
    name = "Environment Variables"
    icon = "🔤"
    description = "View and edit System and User environment variables, analyse PATH, diff snapshots."
    requires_admin = False
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._widget: QWidget | None = None
        self._path_pane: Optional[_PathPane] = None

    def create_widget(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 0)
        save_btn = QPushButton("Save snapshot...")
        cmp_btn = QPushButton("Compare with snapshot...")
        save_btn.clicked.connect(self._save_snapshot)
        cmp_btn.clicked.connect(self._compare_snapshot)
        bar.addWidget(save_btn)
        bar.addWidget(cmp_btn)
        bar.addStretch()
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._sys_panel = _EnvPanel(winreg.HKEY_LOCAL_MACHINE, _SYS_PATH, "System", requires_admin=True)
        self._usr_panel = _EnvPanel(winreg.HKEY_CURRENT_USER, _USR_PATH, "User", requires_admin=False)
        self._sys_panel.set_duplicate_target(self._usr_panel)
        self._usr_panel.set_duplicate_target(self._sys_panel)
        self._path_pane = _PathPane(self.thread_pool)
        splitter.addWidget(self._sys_panel)
        splitter.addWidget(self._usr_panel)
        splitter.addWidget(self._path_pane)
        splitter.setSizes([300, 300, 250])
        layout.addWidget(splitter, 1)
        self._widget = root
        return root

    def _snapshot_default(self) -> str:
        base = getattr(getattr(self, "app", None), "app_data_dir", "") or os.getcwd()
        return os.path.join(base, "env_snapshot.json")

    def _save_snapshot(self) -> None:
        if not self._widget:
            return
        path, _ = QFileDialog.getSaveFileName(self._widget, "Save environment snapshot",
                                              self._snapshot_default(), "JSON (*.json)")
        if not path:
            return
        try:
            env_ops.save_snapshot(path, env_ops.to_snapshot(self._sys_panel.variables(),
                                                            self._usr_panel.variables()))
        except OSError as e:
            QMessageBox.critical(self._widget, "Snapshot", f"Could not save: {e}")

    def _compare_snapshot(self) -> None:
        if not self._widget:
            return
        path, _ = QFileDialog.getOpenFileName(self._widget, "Compare with snapshot",
                                              self._snapshot_default(), "JSON (*.json)")
        if not path:
            return
        try:
            old = env_ops.load_snapshot(path)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self._widget, "Snapshot", f"Could not read the snapshot: {e}")
            return
        new = env_ops.to_snapshot(self._sys_panel.variables(), self._usr_panel.variables())
        lines = env_ops.diff_snapshots(old, new)
        box = QPlainTextEdit("\n".join(lines) or "No differences.")
        box.setReadOnly(True)
        box.setWindowTitle("Environment differences")
        box.resize(800, 400)
        set_role(box, "mono")
        self._diff_window = box
        box.show()

    def on_activate(self) -> None:
        if self._widget:
            self._sys_panel.refresh()
            self._usr_panel.refresh()

    def on_deactivate(self) -> None:
        if self._path_pane is not None:
            self._path_pane.cancel()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.on_deactivate()
