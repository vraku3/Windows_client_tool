"""Task Manager's Users tab.

The grouping is by account, read from each process token -- not guessed
from a name or a path. Every user is a row that expands into the processes
running under that account, with CPU, memory and disk summed across them.

The row that matters is the one unelevated users meet most: "Accounts not
readable". Roughly half this machine's processes refuse their token, and
this pane refuses to charge them to the person looking at the screen. The
row says what it is instead.
"""
import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QHeaderView, QLabel, QTableWidget,
                             QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from core.worker import Worker

from core.procengine.columns import fmt_bytes, fmt_percent, fmt_rate
from core.procengine.snapshot import SnapshotSource
from core.procengine.users import UNKNOWN, group_by_user
from .sessions import list_sessions

logger = logging.getLogger(__name__)

REFRESH_MS = 1000

COLUMNS = ("Name", "CPU", "Memory", "Disk", "PID")
CPU, MEMORY, DISK = 1, 2, 3

VALUE_ROLE = Qt.ItemDataRole.UserRole + 1
PID_ROLE = Qt.ItemDataRole.UserRole + 2


class UsersTab(QWidget):
    """Per-account process view. Owns its workers, per CLAUDE.md."""

    snapshot_taken = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers: list = []
        self._source = SnapshotSource()
        self._app = None
        self._busy = False
        self._expanded: set = set()
        self._snapshot = None
        self._groups = None
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        heading = QLabel("Logged-on sessions", self)
        heading.setStyleSheet("font-weight: bold; padding: 4px 4px 0 4px;")
        layout.addWidget(heading)
        self.sessions_table = QTableWidget(0, 5, self)
        self.sessions_table.setHorizontalHeaderLabels(
            ["Session", "User", "State", "Type", "Client"])
        self.sessions_table.verticalHeader().setVisible(False)
        self.sessions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sessions_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.sessions_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.sessions_table.setMaximumHeight(120)
        layout.addWidget(self.sessions_table)
        self.sessions_note = QLabel("", self)
        self.sessions_note.setStyleSheet("color: gray; padding: 0 4px;")
        layout.addWidget(self.sessions_note)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(False)
        self.tree.itemExpanded.connect(
            lambda item: self._remember(item, True))
        self.tree.itemCollapsed.connect(
            lambda item: self._remember(item, False))
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for section in range(1, len(COLUMNS)):
            header.setSectionResizeMode(
                section, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(section, 100)
        layout.addWidget(self.tree, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)

    def set_app(self, app) -> None:
        self._app = app

    def start(self) -> None:
        self.refresh()
        self._timer.start(REFRESH_MS)

    def stop(self) -> None:
        self._timer.stop()
        self.cancel_all()

    def cancel_all(self) -> None:
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()

    # ---- reading --------------------------------------------------------

    def refresh(self) -> None:
        if self._busy:
            return
        pool = getattr(self._app, "thread_pool", None) if self._app else None
        if pool is None:
            self._apply(self._read())
            return
        self._busy = True
        worker = Worker(lambda _worker: self._read())
        worker.signals.result.connect(self._apply)
        worker.signals.error.connect(self._failed)
        self._workers.append(worker)
        pool.start(worker)

    def _read(self):
        snapshot = self._source.read()
        return snapshot, group_by_user(snapshot), list_sessions()

    def _apply(self, result) -> None:
        self._busy = False
        snapshot, groups, *rest = result
        self._snapshot = snapshot
        self._groups = groups
        if rest:
            self._show_sessions(rest[0])
        self._rebuild()
        self.snapshot_taken.emit(snapshot)

    def _show_sessions(self, sessions) -> None:
        """A failed read says so; it is never shown as an empty table."""
        table = self.sessions_table
        if sessions is None:
            table.setRowCount(0)
            self.sessions_note.setText(
                "Could not ask Windows who is logged on (Terminal Services API refused).")
            return
        table.setRowCount(len(sessions))
        for row, sess in enumerate(sessions):
            values = (str(sess.id), sess.account, sess.state,
                      "Remote (RDP)" if sess.is_remote else "Console",
                      sess.client or "")
            for column, text in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(text))
        remote = sum(1 for sess in sessions if sess.is_remote)
        self.sessions_note.setText(
            f"{len(sessions)} session(s)" + (f", {remote} remote" if remote else "")
            + ". A Disconnected session keeps its processes running.")

    def _failed(self, message) -> None:
        self._busy = False
        logger.error("Users refresh failed: %s", message)
        self.status.setText(f"Could not read the process list: {message}")

    # ---- building the tree ----------------------------------------------

    def _rebuild(self) -> None:
        if self._groups is None:
            return
        selected = self._selected_pids()

        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        peak = self._peaks()
        for group in self._groups:
            if not group.rows:
                continue
            self._user_item(group, peak)
        self.tree.setUpdatesEnabled(True)

        self._restore_selection(selected)
        self._update_status()

    def _user_item(self, group, peak) -> None:
        item = QTreeWidgetItem(self.tree, [group.name])
        item.setData(0, PID_ROLE, None)
        self._set_value(item, CPU, group.totals()["cpu"], fmt_percent,
                        peak["cpu"])
        self._set_value(item, MEMORY, group.totals()["memory"], fmt_bytes,
                        peak["memory"])
        self._set_value(item, DISK, group.totals()["disk"], fmt_rate,
                        peak["disk"])
        if group.is_unknown:
            item.setText(0, f"{UNKNOWN} ({len(group.rows)})")
            tip = ("The accounts these processes run as could not be read "
                   "(access is denied). Running as administrator resolves "
                   "most of them.")
            item.setToolTip(0, tip)
        else:
            item.setText(0, group.name)
            item.setToolTip(0, f"{group.count} process(es)")
        for info in sorted(group.rows, key=lambda info: info.name.lower()):
            self._process_item(item, info, peak)
        if group.name in self._expanded:
            item.setExpanded(True)

    def _process_item(self, parent, info, peak) -> None:
        label = info.details.description or info.name
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, PID_ROLE, info.pid)
        item.setToolTip(0, info.details.path or info.name)
        self._set_value(item, CPU, info.rates.cpu_percent, fmt_percent,
                        peak["cpu"])
        self._set_value(item, MEMORY, info.raw.working_set_private,
                        fmt_bytes, peak["memory"])
        disk = _disk_of(info)
        self._set_value(item, DISK, disk, fmt_rate, peak["disk"])
        item.setText(4, str(info.pid))

    def _set_value(self, item, column: int, value, render, ceiling) -> None:
        item.setText(column, render(value))
        item.setTextAlignment(column, int(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter))
        item.setData(column, VALUE_ROLE, value)
        tint = _heat(value, ceiling)
        if tint is not None:
            item.setBackground(column, QBrush(tint))

    def _peaks(self) -> Dict[str, float]:
        """The busiest value on screen, per column -- the shade must scale
        against what is actually here, or an idle machine is one flat wash."""
        peak = {"cpu": 1.0, "memory": 1.0, "disk": 1.0}
        if self._snapshot is None:
            return peak
        for info in self._snapshot.by_pid.values():
            if info.rates.cpu_percent:
                peak["cpu"] = max(peak["cpu"], info.rates.cpu_percent)
            peak["memory"] = max(peak["memory"],
                                 info.raw.working_set_private)
            peak["disk"] = max(peak["disk"], _disk_of(info) or 0)
        return peak

    # ---- expansion memory ----------------------------------------------

    def _remember(self, item, opened: bool) -> None:
        name = item.data(0, PID_ROLE)
        if name is not None:
            return
        # The top-level rows are accounts; remember them by their text.
        label = item.text(0).split(" (")[0]
        if opened:
            self._expanded.add(label)
        else:
            self._expanded.discard(label)

    # ---- selection ------------------------------------------------------

    def _selected_pids(self) -> List[int]:
        pids = []
        for item in self.tree.selectedItems():
            pid = item.data(0, PID_ROLE)
            if pid is not None:
                pids.append(pid)
        return pids

    def _restore_selection(self, pids) -> None:
        if not pids:
            return
        wanted = set(pids)
        for item in _walk(self.tree):
            if item.data(0, PID_ROLE) in wanted:
                item.setSelected(True)

    def _update_status(self) -> None:
        if self._snapshot is None:
            return
        total = len(self._snapshot.by_pid)
        parts = [f"{total:,} processes"]
        if self._snapshot.refused:
            parts.append(
                f"{self._snapshot.refused:,} could not be read "
                f"(run as administrator to see them)")
        self.status.setText("   ·   ".join(parts))


def _disk_of(info) -> Optional[float]:
    """Read plus write, or None if neither has been measured yet."""
    read, write = info.rates.read_bps, info.rates.write_bps
    if read is None and write is None:
        return None
    return (read or 0) + (write or 0)


def _heat(value, ceiling) -> Optional[QColor]:
    if not value or not ceiling:
        return None
    share = min(1.0, float(value) / float(ceiling))
    if share < 0.08:
        return None
    tint = QColor(semantic("warning"))
    tint.setAlpha(int(28 + share * 90))
    return tint


def _walk(tree):
    stack = [tree.topLevelItem(index)
             for index in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item is None:
            continue
        yield item
        stack.extend(item.child(index) for index in range(item.childCount()))
