"""Connections: every TCP/UDP endpoint and the process that owns it.

Live, filterable and sortable, with the two things an admin does next one
click away: end the owning process, or copy the remote address to look it up.
"""
import logging
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMenu, QPushButton, QTableWidget,
                             QVBoxLayout)

from core.confirm import confirm_destructive
from core.procengine import actions

from . import connections as cn
from .tab_base import DashModule, DashTab, make_chips, numeric_item, set_chip_counts

logger = logging.getLogger(__name__)

HEADERS = ("Proto", "Local address", "Remote address", "State", "PID", "Process")
PROTO, LOCAL, REMOTE, STATE, PID, PROCESS = range(6)


class ConnectionsTab(DashTab):
    REFRESH_MS = 3000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._conns: List[cn.Connection] = []
        self._filter = "all"
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Filter by process, address, port, PID or state…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._repopulate)
        top.addWidget(self._search, 1)
        self._end_btn = QPushButton("End process", self)
        self._end_btn.setEnabled(False)
        self._end_btn.clicked.connect(self._end_selected)
        top.addWidget(self._end_btn)
        layout.addLayout(top)
        chips, self._chips = make_chips(self, cn.FILTERS, self._set_filter)
        layout.addLayout(chips)

        self._table = QTableWidget(0, len(HEADERS), self)
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setWordWrap(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_menu)
        self._table.itemSelectionChanged.connect(
            lambda: self._end_btn.setEnabled(self._selected() is not None))
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((60, 230, 230, 110, 70, 200)):
            header.resizeSection(column, width)
        header.setStretchLastSection(True)
        self._sort = (PROCESS, Qt.SortOrder.AscendingOrder)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._sort_by)
        layout.addWidget(self._table, 1)
        self.status = QLabel("", self)
        layout.addWidget(self.status)

    # ---- reading ------------------------------------------------------------------

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.run(lambda _w: cn.read_connections(), self._apply)

    def _apply(self, conns) -> None:
        self._busy = False
        if conns is None:
            self.status.setText("Windows refused the connection table (try running as administrator).")
            return
        self._conns = list(conns)
        set_chip_counts(self._chips, cn.filter_counts(self._conns))
        self._repopulate()

    def _default_error(self, message) -> None:
        super()._default_error(message)
        self.status.setText(f"Could not read connections: {message}")

    # ---- table --------------------------------------------------------------------

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._repopulate()

    def _sort_by(self, column: int) -> None:
        order = Qt.SortOrder.DescendingOrder
        if self._sort[0] == column and self._sort[1] == Qt.SortOrder.DescendingOrder:
            order = Qt.SortOrder.AscendingOrder
        self._sort = (column, order)
        self._repopulate()

    def _repopulate(self, *_a) -> None:
        keep = self._selected()
        keep_key = keep.key if keep else None
        shown = cn.visible(self._conns, self._filter, self._search.text())
        table = self._table
        # Filling a sorting-enabled table row by row scrambles it (CLAUDE.md:
        # Driver Manager). Sort by hand instead, and fill in final order.
        table.setSortingEnabled(False)
        table.blockSignals(True)
        shown = self._ordered(shown)
        table.setRowCount(len(shown))
        for row, c in enumerate(shown):
            cells = (c.proto, c.local, c.remote or "—", c.state or "—")
            for column, text in enumerate(cells):
                table.setItem(row, column, numeric_item(text, text))
            table.setItem(row, PID, numeric_item(c.pid, str(c.pid)))
            table.setItem(row, PROCESS, numeric_item(c.process.lower(), c.process or "(unknown)"))
            table.item(row, PROTO).setData(Qt.ItemDataRole.UserRole, c)
        if keep_key is not None:
            for row in range(table.rowCount()):
                item = table.item(row, PROTO)
                if item.data(Qt.ItemDataRole.UserRole).key == keep_key:
                    table.selectRow(row)
                    break
        table.blockSignals(False)
        self._end_btn.setEnabled(self._selected() is not None)
        self._table.horizontalHeader().setSortIndicator(*self._sort)
        self.status.setText(f"{len(shown):,} of {len(self._conns):,} endpoints")

    def _ordered(self, conns: List[cn.Connection]) -> List[cn.Connection]:
        column, order = self._sort
        keys = {
            PROTO: lambda c: c.proto, LOCAL: lambda c: (c.local_ip, c.local_port),
            REMOTE: lambda c: (c.remote_ip, c.remote_port), STATE: lambda c: c.state,
            PID: lambda c: c.pid, PROCESS: lambda c: c.process.lower(),
        }
        return sorted(conns, key=keys[column],
                      reverse=order == Qt.SortOrder.DescendingOrder)

    def _selected(self) -> Optional[cn.Connection]:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        item = self._table.item(rows[0].row(), PROTO)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ---- actions ------------------------------------------------------------------

    def _show_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        self._table.selectRow(row)
        conn = self._selected()
        if conn is None:
            return
        menu = QMenu(self)
        end = menu.addAction(f"End process ({conn.process or conn.pid})")
        end.setEnabled(conn.pid > 4)
        end.triggered.connect(self._end_selected)
        menu.addSeparator()
        copy_remote = menu.addAction("Copy remote address")
        copy_remote.setEnabled(bool(conn.remote))
        copy_remote.triggered.connect(lambda: QApplication.clipboard().setText(conn.remote))
        menu.addAction("Copy local address").triggered.connect(
            lambda: QApplication.clipboard().setText(conn.local))
        menu.addAction("Copy row").triggered.connect(
            lambda: QApplication.clipboard().setText(
                f"{conn.proto}  {conn.local}  {conn.remote}  {conn.state}  "
                f"{conn.pid}  {conn.process}"))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _end_selected(self) -> None:
        conn = self._selected()
        if conn is None or conn.pid <= 4:
            return
        others = sum(1 for c in self._conns if c.pid == conn.pid)
        if not confirm_destructive(
                self, "End process",
                f"End {conn.process or 'PID ' + str(conn.pid)} (PID {conn.pid})?\n\n"
                f"It owns {others} endpoint(s). Unsaved work in it is lost.",
                irreversible=True):
            return
        result = actions.end_process(conn.pid)
        self.status.setText(getattr(result, "message", "") or
                            ("Process ended." if getattr(result, "ok", False) else "Could not end the process."))
        self.refresh()


class ConnectionsModule(DashModule):
    name = "Connections"
    icon = "🔌"
    description = "Every TCP/UDP endpoint and the process that owns it"
    tab_class = ConnectionsTab
