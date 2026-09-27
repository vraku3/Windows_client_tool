import datetime
import logging
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QPlainTextEdit,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import center_header, centered_item, fit_table, set_role
from core.worker import Worker
from modules.local_users import accounts as acc

logger = logging.getLogger(__name__)

_USER_COLS = ["Username", "Full Name", "Enabled", "Last Logon", "Days Idle",
              "Password Age (days)", "Groups", "Flags"]
_GROUP_COLS = ["Group Name", "Members", "Member list", "Comment"]


class _NumItem(QTableWidgetItem):
    """Shows text, sorts on a number stored in UserRole."""

    def __init__(self, text: str, number: float):
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole, number)
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def __lt__(self, other) -> bool:
        a, b = self.data(Qt.ItemDataRole.UserRole), other.data(Qt.ItemDataRole.UserRole)
        if isinstance(other, _NumItem) and a is not None and b is not None:
            return a < b
        return super().__lt__(other)


def _fmt_time(epoch: int) -> str:
    if not epoch:
        return "Never"
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def flag_text(a: acc.Account) -> str:
    bits = []
    if a.flags & acc.UF_DONT_EXPIRE_PASSWD:
        bits.append("pw never expires")
    if a.flags & acc.UF_PASSWD_NOTREQD:
        bits.append("pw not required")
    if a.flags & acc.UF_LOCKOUT:
        bits.append("locked out")
    return ", ".join(bits)


def format_password_age(seconds) -> str:
    """Days since the password was set. NetUserEnum reports 0 seconds for an
    account whose password was NEVER set (the built-in Administrator, Guest,
    DefaultAccount...), which is not the same as "changed today" -- that is a
    small positive number of seconds. The 0 read as "0 days" on every one of
    them; it is a dash."""
    if not seconds:
        return "—"
    return str(int(seconds / 86400))


def detail_text(a: acc.Account, snap: acc.Snapshot) -> str:
    age = a.password_age_days
    lines = [
        f"Account:        {a.name}" + ("" if a.enabled else "  (disabled)"),
        f"Full name:      {a.full_name or '-'}",
        f"SID:            {a.sid or 'could not be resolved'}",
        f"RID:            {a.rid}",
        f"Last logon:     {_fmt_time(a.last_logon)}   (logon count {a.num_logons}; local record only, "
        "not updated by Microsoft-account or domain sign-ins)",
        f"Password age:   {'never set' if age is None else f'{age} days'}",
        f"Profile path:   {a.profile or '(none recorded)'}",
        f"Flags:          {flag_text(a) or 'none of interest'}",
        f"Comment:        {a.comment or '-'}",
        "Member of:      " + (f"unreadable ({a.groups_error})" if a.groups_error else ", ".join(a.groups) or "(no local groups)"),
    ]
    return "\n".join(lines)


class LocalUsersModule(BaseModule):
    name = "Local Users & Groups"
    icon = "👥"
    description = "Local accounts, group membership in both directions, and findings"
    requires_admin = False
    group = ModuleGroup.MANAGE

    def __init__(self):
        super().__init__()
        self._snap: Optional[acc.Snapshot] = None
        self._chip = "All"
        self._chips: dict = {}
        self._rows: List[acc.Account] = []
        self._widget: Optional[QWidget] = None
        self._loaded = False

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._copy_btn = QPushButton("Copy details")
        self._copy_btn.clicked.connect(self._copy_details)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search name, full name, SID, group, profile...")
        self._search.textChanged.connect(self._render_users)
        self._status_label = QLabel("Loading...")
        set_role(self._status_label, "muted")
        for w in (self._refresh_btn, self._copy_btn):
            toolbar.addWidget(w)
        toolbar.addWidget(self._search, 1)
        toolbar.addWidget(self._status_label)
        layout.addLayout(toolbar)

        self._policy_label = QLabel("")
        set_role(self._policy_label, "muted")
        layout.addWidget(self._policy_label)

        chip_row = QHBoxLayout()
        for c in acc.CHIPS:
            b = QPushButton(c)
            b.setCheckable(True)
            b.setChecked(c == "All")
            b.clicked.connect(lambda _=False, name=c: self._set_chip(name))
            self._chips[c] = b
            chip_row.addWidget(b)
        chip_row.addStretch()
        layout.addLayout(chip_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        split = QSplitter(Qt.Orientation.Vertical)
        self._user_table = self._make_table(_USER_COLS, stretch=[6, 7], content=[0, 1, 2, 3, 4, 5])
        self._user_table.itemSelectionChanged.connect(self._on_user_selected)
        split.addWidget(self._user_table)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        set_role(self._detail, "mono")
        split.addWidget(self._detail)
        split.setSizes([380, 170])
        tabs.addTab(split, "Users")

        self._group_table = self._make_table(_GROUP_COLS, stretch=[2, 3], content=[0, 1])
        tabs.addTab(self._group_table, "Groups")

        self._findings_table = self._make_table(["Severity", "Account", "Finding"], stretch=[2], content=[0, 1])
        self._findings_tab_index = tabs.addTab(self._findings_table, "Findings")

        self._refresh_btn.clicked.connect(self._do_refresh)
        self._lu_tabs = tabs
        self._widget = outer
        return outer

    @staticmethod
    def _make_table(cols, stretch, content) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        fit_table(t, stretch=stretch, content=content)
        center_header(t)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        t.verticalHeader().setVisible(False)
        return t

    # ── loading ──────────────────────────────────────────────────────────

    def _do_refresh(self):
        if self._widget is None:
            return
        self._refresh_btn.setEnabled(False)
        self._status_label.setText("Loading...")
        self._progress.show()
        worker = Worker(lambda _w: acc.read_snapshot())
        worker.signals.result.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        worker.signals.cancelled.connect(self._on_cancelled)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _on_result(self, snap: acc.Snapshot):
        if self._widget is None:
            return
        self._snap = snap
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        counts = acc.chip_counts(snap.accounts)
        for c, b in self._chips.items():
            b.setText(f"{c} ({counts[c]})")
        self._render_users()
        self._render_groups()
        self._render_findings()
        if snap.policy is not None:
            self._policy_label.setText(f"Local policy: {snap.policy.summary}")
        else:
            self._policy_label.setText(f"Local policy: could not be read ({snap.policy_error})")

    def _on_cancelled(self):
        if self._widget is None:
            return
        self._refresh_btn.setEnabled(True)
        self._progress.hide()

    def _on_error(self, err: str):
        if self._widget is None:
            return
        self._refresh_btn.setEnabled(True)
        self._progress.hide()
        self._status_label.setText(f"Could not read accounts: {err}")

    # ── rendering ────────────────────────────────────────────────────────

    def _set_chip(self, name: str) -> None:
        self._chip = name
        for c, b in self._chips.items():
            b.setChecked(c == name)
        self._render_users()

    def _render_users(self, *_):
        if self._snap is None:
            return
        q = self._search.text().strip().lower()
        rows = [a for a in self._snap.accounts
                if acc.matches_chip(a, self._chip) and (not q or q in acc.search_text(a))]
        self._rows = rows
        t = self._user_table
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for r, a in enumerate(rows):
            idle = acc.days_since(a.last_logon)
            age = a.password_age_days
            cells = [
                centered_item(a.name), centered_item(a.full_name),
                centered_item("Yes" if a.enabled else "No"),
                _NumItem(_fmt_time(a.last_logon), a.last_logon),
                _NumItem("" if idle is None else str(idle), -1 if idle is None else idle),
                _NumItem(format_password_age(a.password_age_s), -1 if age is None else age),
                centered_item(", ".join(a.groups) if not a.groups_error else "unreadable"),
                centered_item(flag_text(a)),
            ]
            for c, item in enumerate(cells):
                t.setItem(r, c, item)
        t.setSortingEnabled(True)
        self._status_label.setText(f"{len(rows)} of {len(self._snap.accounts)} account(s), "
                                   f"{len(self._snap.group_members)} group(s)")

    def _render_groups(self):
        snap = self._snap
        t = self._group_table
        t.setSortingEnabled(False)
        names = sorted(snap.group_members, key=str.lower)
        t.setRowCount(len(names))
        for r, g in enumerate(names):
            members, why = snap.group_members[g]
            count = _NumItem("unreadable" if members is None else str(len(members)),
                             -1 if members is None else len(members))
            text = f"Could not read: {why}" if members is None else ", ".join(members)
            for c, item in enumerate([centered_item(g), count, centered_item(text),
                                      centered_item(snap.group_comments.get(g, ""))]):
                t.setItem(r, c, item)
        t.setSortingEnabled(True)

    def _render_findings(self):
        fs = acc.findings(self._snap)
        t = self._findings_table
        t.setSortingEnabled(False)
        t.setRowCount(len(fs))
        colours = {"warning": "warning", "unknown": "info", "info": "info"}
        for r, f in enumerate(fs):
            for c, txt in enumerate((f.severity, f.account, f.message)):
                item = centered_item(txt)
                if c == 0:
                    item.setForeground(QColor(semantic(colours.get(f.severity, "info"))))
                t.setItem(r, c, item)
        t.setSortingEnabled(True)
        self._lu_tabs.setTabText(self._findings_tab_index, f"Findings ({len(fs)})")

    def _selected_account(self) -> Optional[acc.Account]:
        row = self._user_table.currentRow()
        item = self._user_table.item(row, 0) if row >= 0 else None
        if item is None or self._snap is None:
            return None
        return next((a for a in self._snap.accounts if a.name == item.text()), None)

    def _on_user_selected(self):
        a = self._selected_account()
        self._detail.setPlainText(detail_text(a, self._snap) if a else "")

    def _copy_details(self):
        text = self._detail.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)

    # ── lifecycle ────────────────────────────────────────────────────────

    def on_activate(self):
        if not self._loaded and self._widget is not None:
            self._loaded = True
            self._do_refresh()

    def refresh_data(self):
        if self._widget is not None and self._loaded:
            self._do_refresh()

    def on_start(self, app): self.app = app
    def on_stop(self): self.cancel_all_workers()
    def on_deactivate(self): self.cancel_all_workers()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000
