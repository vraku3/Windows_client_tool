import datetime
from typing import List, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QStackedWidget, QTableWidget,
    QHeaderView, QLabel, QProgressBar, QTabWidget,
)
from PyQt6.QtCore import QThreadPool
from PyQt6.QtGui import QColor

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, center_header
from core.worker import Worker
from ui.empty_state import EmptyState
import logging
logger = logging.getLogger(__name__)

_USER_COLS = ["Username", "Full Name", "Enabled", "Last Logon", "Password Age (days)", "Comment"]
_GROUP_COLS = ["Group Name", "Members", "Comment"]

_UF_ACCOUNTDISABLE = 0x0002


def _fmt_time(t) -> str:
    if not t:
        return "Never"
    try:
        if isinstance(t, datetime.datetime):
            if t.year < 1970:
                return "Never"
            return t.strftime("%Y-%m-%d %H:%M")
        return datetime.datetime.fromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        logger.warning("Ignored Exception", exc_info=True)
        return str(t)


def get_users() -> List[Dict]:
    import win32net
    users = []
    resume = 0
    while True:
        data, _, resume = win32net.NetUserEnum(None, 2, 0, resume)
        for u in data:
            flags = u.get("flags", 0)
            enabled = not bool(flags & _UF_ACCOUNTDISABLE)
            logon_ts = u.get("last_logon", 0)
            pw_age_sec = u.get("password_age", 0)
            pw_age_days = int(pw_age_sec / 86400) if pw_age_sec else 0
            users.append({
                "Username": u.get("name", ""),
                "Full Name": u.get("full_name", ""),
                "Enabled": "Yes" if enabled else "No",
                "Last Logon": _fmt_time(logon_ts),
                "Password Age (days)": str(pw_age_days),
                "Comment": u.get("comment", ""),
            })
        if not resume:
            break
    return sorted(users, key=lambda u: u["Username"].lower())


def get_groups() -> List[Dict]:
    import win32net
    groups = []
    resume = 0
    while True:
        data, _, resume = win32net.NetLocalGroupEnum(None, 1, resume)
        for g in data:
            gname = g.get("name", "")
            # get members
            members = []
            try:
                mem_data, _, _ = win32net.NetLocalGroupGetMembers(None, gname, 1)
                members = [m.get("name", "") for m in mem_data]
            except Exception:
                logger.warning("Ignored Exception", exc_info=True)
            groups.append({
                "Group Name": gname,
                "Members": ", ".join(members),
                "Comment": g.get("comment", ""),
            })
        if not resume:
            break
    return sorted(groups, key=lambda g: g["Group Name"].lower())


def _fill_table(table: QTableWidget, rows: List[Dict], cols: List[str]):
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, col in enumerate(cols):
            table.setItem(r, c, centered_item(str(row.get(col, ""))))


class LocalUsersModule(BaseModule):
    name = "Local Users & Groups"
    icon = "👥"
    description = "View local user accounts and group memberships"
    requires_admin = False
    group = ModuleGroup.MANAGE

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._status_label = QLabel("Click Refresh to load.")
        toolbar.addWidget(self._refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status_label)
        layout.addLayout(toolbar)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        self._user_table = QTableWidget(0, len(_USER_COLS))
        self._user_table.setHorizontalHeaderLabels(_USER_COLS)
        self._user_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._user_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._user_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._user_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._user_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._user_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        center_header(self._user_table)
        self._user_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._user_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._user_table.setAlternatingRowColors(True)

        self._user_stack = QStackedWidget()
        self._user_stack.addWidget(self._user_table)
        self._user_empty = EmptyState(
            "👤", "No local users found",
            "Click Refresh to load local user accounts.",
            "Refresh",
        )
        self._user_empty.action_triggered.connect(self._do_refresh)
        self._user_stack.addWidget(self._user_empty)
        self._user_stack.setCurrentIndex(1)
        tabs.addTab(self._user_stack, "Users")

        self._group_table = QTableWidget(0, len(_GROUP_COLS))
        self._group_table.setHorizontalHeaderLabels(_GROUP_COLS)
        self._group_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._group_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._group_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        center_header(self._group_table)
        self._group_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._group_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._group_table.setAlternatingRowColors(True)

        self._group_stack = QStackedWidget()
        self._group_stack.addWidget(self._group_table)
        self._group_empty = EmptyState(
            "👥", "No local groups found",
            "Click Refresh to load local groups.",
            "Refresh",
        )
        self._group_empty.action_triggered.connect(self._do_refresh)
        self._group_stack.addWidget(self._group_empty)
        self._group_stack.setCurrentIndex(1)
        tabs.addTab(self._group_stack, "Groups")

        self._refresh_btn.clicked.connect(self._do_refresh)
        self._lu_tabs = tabs
        return outer

    def _do_refresh(self):
        self._refresh_btn.setEnabled(False)
        self._status_label.setText("Loading...")
        self._progress.show()

        def _load(_w):
            return get_users(), get_groups()

        worker = Worker(_load)
        worker.signals.result.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_result(self, data):
        users, groups = data
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        _fill_table(self._user_table, users, _USER_COLS)
        # Colour disabled accounts
        for r in range(self._user_table.rowCount()):
            item = self._user_table.item(r, 2)
            if item and item.text() == "No":
                for c in range(self._user_table.columnCount()):
                    cell = self._user_table.item(r, c)
                    if cell:
                        cell.setForeground(QColor("#888888"))
        _fill_table(self._group_table, groups, _GROUP_COLS)
        self._user_stack.setCurrentIndex(0 if self._user_table.rowCount() else 1)
        self._group_stack.setCurrentIndex(0 if self._group_table.rowCount() else 1)
        self._status_label.setText(f"{len(users)} user(s), {len(groups)} group(s)")

    def _on_error(self, err: str):
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status_label.setText(f"Error: {err}")

    def on_activate(self):
        if not getattr(self, "_loaded", False):
            self._loaded = True
            self._do_refresh()

    def on_start(self, app): self.app = app
    def on_stop(self): self.cancel_all_workers()
    def on_deactivate(self): self.cancel_all_workers()

    def get_refresh_interval(self) -> Optional[int]:
        """Auto-refresh every 60 seconds."""
        return 60_000
