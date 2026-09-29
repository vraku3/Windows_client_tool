# src/modules/env_vars/env_vars_module.py
import logging
import os
import winreg
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import centered_item, fit_table, set_role
from core.worker import Worker
from modules.env_vars import effective_env, env_ops, path_analysis, process_env_scan
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


class _EffectivePane(QWidget):
    """System vs. User vs. what THIS process actually inherited.

    Answers the classic support question ("I set it, why doesn't my program
    see it?") by comparing the registry to the running process' own
    environment: a mismatch means the value needs a new process (a re-login,
    or a restart) before it takes effect, not that the write failed.
    """

    def __init__(self, sys_panel: "_EnvPanel", usr_panel: "_EnvPanel", thread_pool):
        super().__init__()
        self._sys_panel = sys_panel
        self._usr_panel = usr_panel
        self._pool = thread_pool
        self._rows: List[effective_env.EffectiveRow] = []
        self._workers: list = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Effective (this process)</b>"))
        self._status = QLabel(
            "What this running app actually inherited, vs. the registry. A row marked "
            "stale needs a new process (log off/on, or a restart) to pick up the change "
            "— the registry write is not live in anything already running.")
        set_role(self._status, "muted")
        self._status.setWordWrap(True)
        head.addWidget(self._status, 1)
        self._only_stale = QPushButton("Show only stale")
        self._only_stale.setCheckable(True)
        self._only_stale.toggled.connect(self._refresh)
        head.addWidget(self._only_stale)
        self._scan_btn = QPushButton("Scan running processes...")
        self._scan_btn.setToolTip(
            "Select a variable row, then check every OTHER running process (not just this "
            "app) for whether it still has the current registry value.")
        self._scan_btn.clicked.connect(self._scan_selected)
        head.addWidget(self._scan_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        head.addWidget(refresh_btn)
        layout.addLayout(head)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Name", "System", "User", "This process has", "State"])
        fit_table(self._table, stretch=[1, 2, 3], content=[0, 4])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    def cancel(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    def _refresh(self, *_a) -> None:
        self._rows = effective_env.compare(self._sys_panel.variables(), self._usr_panel.variables())
        rows = effective_env.stale_rows(self._rows) if self._only_stale.isChecked() else self._rows
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            state = "stale" if row.is_stale else ("process-only" if row.process_only else "current")
            values = (row.name, row.system_value or "—", row.user_value or "—",
                     row.process_value if row.process_value is not None else "—", state)
            for c, text in enumerate(values):
                item = centered_item(text)
                if state == "stale":
                    item.setForeground(QColor(semantic("warning")))
                self._table.setItem(r, c, item)
        self._status.setText(
            f"{len(rows)} row(s) shown. Stale rows need a new process to take effect."
            if not self._only_stale.isChecked() else
            f"{len(rows)} stale row(s): set in the registry, not yet in this process.")

    # ── scan every running process for the selected variable ───────────────

    def _selected_row(self) -> Optional[effective_env.EffectiveRow]:
        item = self._table.item(self._table.currentRow(), 0) if self._table.currentRow() >= 0 else None
        if item is None:
            return None
        return next((r for r in self._rows if r.name == item.text()), None)

    def _scan_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            self._status.setText("Select a variable row first, then Scan running processes.")
            return
        name, expected = row.name, row.combined_registry_value
        self._scan_btn.setEnabled(False)
        self._status.setText(f"Scanning running processes for {name}...")

        def work(_w):
            return process_env_scan.scan_running_processes(name, expected)

        w = Worker(work)
        w.signals.result.connect(lambda rows: self._on_scan_result(name, expected, rows))
        w.signals.error.connect(self._on_scan_error)
        w.signals.finished.connect(lambda: self._scan_btn.setEnabled(True))
        self._workers.append(w)
        self._pool.start(w)

    def _on_scan_error(self, err: str) -> None:
        self._status.setText(f"Process scan failed: {err}")

    def _on_scan_result(self, name: str, expected: Optional[str],
                        rows: List[process_env_scan.ProcessEnvRow]) -> None:
        summary = process_env_scan.summarize(rows)
        self._status.setText(
            f"{name}: {summary.has_value} of {summary.total} running processes have a value "
            f"({summary.stale} stale, {summary.refused} refused).")
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Running processes with {name}")
        dlg.resize(760, 480)
        layout = QVBoxLayout(dlg)
        header = QLabel(
            f"Registry says a new process would get: {expected if expected is not None else '(not set)'}")
        header.setWordWrap(True)
        set_role(header, "muted")
        layout.addWidget(header)
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["PID", "Process", "User", "Value", "State"])
        fit_table(table, stretch=[1, 3], content=[0, 2, 4])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        shown = [r for r in rows if r.value is not None or not r.readable]
        table.setRowCount(len(shown))
        for r, row in enumerate(shown):
            if not row.readable:
                state, value = f"refused ({row.refusal})", "—"
            elif row.is_stale:
                state, value = "stale", row.value
            else:
                state, value = "current", row.value
            for c, text in enumerate((str(row.pid), row.name, row.username or "—", value, state)):
                item = centered_item(text)
                if not row.readable:
                    item.setForeground(QColor(semantic("info")))
                elif row.is_stale:
                    item.setForeground(QColor(semantic("warning")))
                table.setItem(r, c, item)
        layout.addWidget(table, 1)
        absent = summary.total - summary.has_value - summary.refused
        footer = QLabel(
            f"{absent} process(es) not shown: readable, and this variable is simply absent there. "
            "A refused row means the process belongs to another user/SYSTEM and could not be "
            "checked at all -- it is not evidence either way.")
        footer.setWordWrap(True)
        set_role(footer, "muted")
        layout.addWidget(footer)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(dlg.accept)
        layout.addWidget(btns)
        dlg.exec()


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
        self._effective_pane: Optional[_EffectivePane] = None

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
        self._effective_pane = _EffectivePane(self._sys_panel, self._usr_panel, self.thread_pool)
        splitter.addWidget(self._sys_panel)
        splitter.addWidget(self._usr_panel)
        splitter.addWidget(self._effective_pane)
        splitter.addWidget(self._path_pane)
        splitter.setSizes([260, 260, 220, 220])
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
            self._effective_pane._refresh()

    def on_deactivate(self) -> None:
        if self._path_pane is not None:
            self._path_pane.cancel()
        if self._effective_pane is not None:
            self._effective_pane.cancel()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self.on_deactivate()
