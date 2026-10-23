# src/modules/process_explorer/lower_pane/network_view.py
from __future__ import annotations
import logging
import socket
import threading
from typing import Optional

import psutil
from PyQt6.QtCore import pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QHeaderView

from core.table_ui import centered_item, center_header
from core.widget_life import widget_is_valid
from ui.help.dashboard_ui import apply_help

logger = logging.getLogger(__name__)

_HEADERS = ["Protocol", "Local Address", "Local Port", "Remote Address", "Remote Port", "State"]

# Connection states worth flagging — pile-ups here often indicate a stuck or
# misbehaving process (lingering half-closed sockets, retried connects, etc.)
_WARN_STATES = {"CLOSE_WAIT", "TIME_WAIT", "LAST_ACK", "CLOSING", "SYN_SENT"}
_WARN_BRUSH = QBrush(QColor("#ffcc66"))

_widget_valid = widget_is_valid


class NetworkView(QWidget):
    _data_ready = pyqtSignal(int, object, str)  # (pid, rows, error_msg)

    REFRESH_INTERVAL_MS = 3000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_ready.connect(self._populate)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        summary_row = QHBoxLayout()
        self._summary_label = QLabel("")
        summary_row.addWidget(self._summary_label)
        summary_row.addStretch()
        layout.addLayout(summary_row)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        center_header(self._table)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)
        self._pid: Optional[int] = None
        self._thread: Optional[threading.Thread] = None

        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._reload)
        apply_help(self, 'Network')

    def cancel(self) -> None:
        self._pid = None
        self._timer.stop()
        self._table.setRowCount(0)

    def load_pid(self, pid: int):
        self.cancel()
        self._pid = pid
        self._table.setRowCount(0)
        self._reload()
        self._timer.start()

    def _reload(self) -> None:
        if self._pid is None:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._load, args=(self._pid,), daemon=True)
        self._thread.start()

    def _load(self, pid: int):
        error = ""
        rows = []
        try:
            rows = psutil.Process(pid).connections(kind="inet")
        except psutil.NoSuchProcess:
            error = "Process no longer running"
        except psutil.AccessDenied:
            error = "Access denied — run as Administrator to see connections for this process"
        except Exception as e:
            logger.warning("connections() failed for pid %d: %s", pid, e)
            error = f"Error: {e}"
        self._data_ready.emit(pid, rows, error)

    @pyqtSlot(int, object, str)
    def _populate(self, pid: int, rows: list, error: str):
        if pid != self._pid:
            return  # stale result from a previous selection
        if not _widget_valid(self._table):
            return

        if error:
            self._table.setRowCount(0)
            if _widget_valid(self._summary_label):
                self._summary_label.setText(error)
            return

        self._table.setRowCount(len(rows))
        warn_count = 0
        for r, conn in enumerate(rows):
            proto = "TCP" if conn.type == socket.SOCK_STREAM else "UDP"
            laddr = conn.laddr.ip if conn.laddr else ""
            lport = str(conn.laddr.port) if conn.laddr else ""
            raddr = conn.raddr.ip if conn.raddr else ""
            rport = str(conn.raddr.port) if conn.raddr else ""
            state = conn.status or ""
            is_warn = state in _WARN_STATES
            if is_warn:
                warn_count += 1
            for c, val in enumerate([proto, laddr, lport, raddr, rport, state]):
                item = centered_item(val)
                if is_warn:
                    item.setForeground(_WARN_BRUSH)
                self._table.setItem(r, c, item)

        if _widget_valid(self._summary_label):
            text = f"{len(rows)} connection(s)"
            if warn_count:
                text += f" — {warn_count} in a lingering state (TIME_WAIT/CLOSE_WAIT/…)"
            self._summary_label.setText(text)
