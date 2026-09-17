# src/modules/env_vars/env_vars_module.py
import ctypes
import logging
import winreg
from typing import List, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSplitter, QStackedWidget, QTableWidget,
    QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, fit_table
from ui.empty_state import EmptyState
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)

# Registry paths
_SYS_PATH = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_USR_PATH = r"Environment"


def _read_env_vars(hive, path: str) -> List[Tuple[str, str]]:
    """Return sorted list of (name, value) from a registry key."""
    pairs = []
    try:
        with winreg.OpenKey(hive, path, access=winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(k, i)
                    pairs.append((name, str(data)))
                    i += 1
                except OSError:
                    # OSError here is how winreg says the enumeration is
                    # finished. Control flow, not a failure.
                    logger.debug("_read_env_vars: registry enumeration finished", exc_info=True)
                    break
    except OSError as e:
        logger.warning("Could not read env vars from %s: %s", path, e)
    return sorted(pairs, key=lambda t: t[0].lower())


def _write_env_var(hive, path: str, name: str, value: str) -> None:
    with winreg.OpenKey(hive, path, access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_EXPAND_SZ, value)


def _delete_env_var(hive, path: str, name: str) -> None:
    with winreg.OpenKey(hive, path, access=winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, name)


def _broadcast_env_change() -> None:
    """Notify Explorer and running apps that environment changed."""
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF,       # HWND_BROADCAST
        0x001A,       # WM_SETTINGCHANGE
        0,
        "Environment",
        0x0002,       # SMTO_ABORTIFHUNG
        5000,
        None,
    )


class _EnvPanel(QWidget):
    """Single-scope (System or User) env-var panel."""

    def __init__(self, hive, reg_path: str, label: str, requires_admin: bool = False):
        super().__init__()
        self._hive = hive
        self._reg_path = reg_path
        self._requires_admin = requires_admin
        self._refreshed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{label} Variables</b>"))
        header.addStretch()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter…")
        self._search.setMaximumWidth(200)
        self._search.textChanged.connect(self._apply_filter)
        header.addWidget(self._search)
        layout.addLayout(header)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Name", "Value"])
        fit_table(self._table, stretch=[1], content=[0])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)

        self._table_stack = QStackedWidget()
        self._table_stack.addWidget(self._table)
        self._empty = EmptyState(
            "🔤", f"No {label.lower()} variables found",
            f"No {label} environment variables to show. Click Refresh to reload.",
            "Refresh",
        )
        self._empty.action_triggered.connect(self.refresh)
        self._table_stack.addWidget(self._empty)
        self._table_stack.setCurrentIndex(1)
        layout.addWidget(self._table_stack)

        btn_row = QHBoxLayout()
        for text, slot in [("Add", self._add), ("Edit", self._edit),
                            ("Delete", self._delete), ("Duplicate →", self._duplicate)]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._duplicate_target: "_EnvPanel | None" = None
        self._all_rows: List[Tuple[str, str]] = []

    def showEvent(self, event):
        super().showEvent(event)
        # Defer first refresh until the widget is actually visible to avoid
        # wasting CPU on registry reads before the user sees the panel
        if not self._refreshed:
            self._refreshed = True
            self.refresh()

    def set_duplicate_target(self, target: "_EnvPanel") -> None:
        self._duplicate_target = target

    def refresh(self) -> None:
        self._all_rows = _read_env_vars(self._hive, self._reg_path)
        self._apply_filter(self._search.text())

    def _apply_filter(self, text: str) -> None:
        filtered = [
            (n, v) for n, v in self._all_rows
            if text.lower() in n.lower() or text.lower() in v.lower()
        ] if text else self._all_rows
        self._table.setRowCount(0)
        for name, value in filtered:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, centered_item(name))
            self._table.setItem(row, 1, centered_item(value))
        self._table_stack.setCurrentIndex(0 if self._table.rowCount() else 1)

    def _selected_name(self) -> str | None:
        rows = self._table.selectedItems()
        if not rows:
            return None
        return self._table.item(self._table.currentRow(), 0).text()

    def _add(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Variable", "Variable name:")
        if not ok or not name.strip():
            return
        value, ok = QInputDialog.getText(self, "Add Variable", f"Value for {name}:")
        if not ok:
            return
        try:
            _write_env_var(self._hive, self._reg_path, name.strip(), value)
            _broadcast_env_change()
            self.refresh()
        except OSError as e:
            self._error_banner.set_error(f"Could not write variable: {e}")

    def _edit(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        current_value = self._table.item(self._table.currentRow(), 1).text()
        value, ok = QInputDialog.getText(self, "Edit Variable", f"Value for {name}:", text=current_value)
        if not ok:
            return
        try:
            _write_env_var(self._hive, self._reg_path, name, value)
            _broadcast_env_change()
            self.refresh()
        except OSError as e:
            self._error_banner.set_error(f"Could not update variable: {e}")

    def _delete(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        reply = QMessageBox.question(
            self, "Delete Variable",
            f"Delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            _delete_env_var(self._hive, self._reg_path, name)
            _broadcast_env_change()
            self.refresh()
        except OSError as e:
            self._error_banner.set_error(f"Could not delete variable: {e}")

    def _duplicate(self) -> None:
        name = self._selected_name()
        if name is None or self._duplicate_target is None:
            return
        value = self._table.item(self._table.currentRow(), 1).text()
        target = self._duplicate_target
        try:
            _write_env_var(target._hive, target._reg_path, name, value)
            _broadcast_env_change()
            target.refresh()
        except OSError as e:
            self._error_banner.set_error(f"Could not copy variable: {e}")


class EnvVarsModule(BaseModule):
    name = "Environment Variables"
    icon = "🔤"
    description = "View and edit System and User environment variables."
    requires_admin = False
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._widget: QWidget | None = None

    def create_widget(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._sys_panel = _EnvPanel(
            winreg.HKEY_LOCAL_MACHINE, _SYS_PATH, "System", requires_admin=True
        )
        self._usr_panel = _EnvPanel(
            winreg.HKEY_CURRENT_USER, _USR_PATH, "User", requires_admin=False
        )
        self._sys_panel.set_duplicate_target(self._usr_panel)
        self._usr_panel.set_duplicate_target(self._sys_panel)

        splitter.addWidget(self._sys_panel)
        splitter.addWidget(self._usr_panel)
        splitter.setSizes([400, 400])
        layout.addWidget(splitter)
        self._widget = root
        return root

    def on_activate(self) -> None:
        if self._widget:
            self._sys_panel.refresh()
            self._usr_panel.refresh()

    def on_deactivate(self) -> None:
        pass

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        pass
