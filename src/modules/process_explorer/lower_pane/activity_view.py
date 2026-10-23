# src/modules/process_explorer/lower_pane/activity_view.py
from __future__ import annotations
import logging
import threading
import time
from typing import Optional, Set, Tuple

import psutil
from PyQt6.QtCore import pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QCheckBox, QTableWidget,
                              QHeaderView)

from core.table_ui import centered_item, center_header
from core.widget_life import widget_is_valid
from ui.help.dashboard_ui import apply_help

logger = logging.getLogger(__name__)

_HEADERS = ["Time", "Event", "Detail"]
_MAX_ROWS = 500

_OPEN_BRUSH = QBrush(QColor("#88cc88"))
_CLOSE_BRUSH = QBrush(QColor("#cc8888"))

_widget_valid = widget_is_valid

ConnKey = Tuple[str, str, str, str]  # laddr, raddr, type, status

# Sentinel emitted when the watched process no longer exists
_PROCESS_GONE = object()


class ActivityView(QWidget):
    """Lightweight Process-Monitor-style activity feed.

    Polls open files and network connections for the selected process and
    logs additions/removals as they happen. This is a polling approximation
    (no ETW/kernel driver involved) but gives a live view of what a process
    is touching without extra privileges.
    """

    POLL_INTERVAL_MS = 1500

    # generation guards stale results from previous selections;
    # files/conns may be _PROCESS_GONE sentinel
    _snapshot_ready = pyqtSignal(int, int, object, object)  # pid, gen, files, conns

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshot_ready.connect(self._on_snapshot)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self._label = QLabel("Select a process to view activity")
        toolbar.addWidget(self._label)
        toolbar.addStretch()
        self._pause_cb = QCheckBox("Pause")
        toolbar.addWidget(self._pause_cb)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(clear_btn)
        layout.addLayout(toolbar)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        center_header(self._table)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        self._pid: Optional[int] = None
        self._generation: int = 0          # incremented on every load_pid/cancel
        self._prev_files: Optional[Set[str]] = None
        self._prev_conns: Optional[Set[ConnKey]] = None
        self._thread: Optional[threading.Thread] = None

        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)
        apply_help(self, 'Activity')

    def cancel(self) -> None:
        self._generation += 1  # invalidate any in-flight snapshot
        self._pid = None
        self._prev_files = None
        self._prev_conns = None
        self._timer.stop()
        if _widget_valid(self._label):
            self._label.setText("Select a process to view activity")

    def load_pid(self, pid: int):
        self.cancel()
        self._pid = pid
        self._table.setRowCount(0)
        self._label.setText(f"Watching activity for PID {pid}…")
        self._poll()
        self._timer.start()

    def _clear(self) -> None:
        self._table.setRowCount(0)

    def _poll(self) -> None:
        if self._pid is None or self._pause_cb.isChecked():
            return
        # Skip this tick if the previous snapshot thread is still running to
        # avoid out-of-order delivery corrupting the diff baseline.
        if self._thread is not None and self._thread.is_alive():
            return
        pid = self._pid
        gen = self._generation
        self._thread = threading.Thread(target=self._snapshot, args=(pid, gen), daemon=True)
        self._thread.start()

    def _snapshot(self, pid: int, gen: int) -> None:
        files: Set[str] = set()
        conns: Set[ConnKey] = set()
        try:
            proc = psutil.Process(pid)
            for f in proc.open_files():
                files.add(f.path)
            for c in proc.connections(kind="inet"):
                laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
                raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
                ctype = "TCP" if c.type == 1 else "UDP"
                conns.add((laddr, raddr, ctype, c.status or ""))
        except psutil.NoSuchProcess:
            self._snapshot_ready.emit(pid, gen, _PROCESS_GONE, _PROCESS_GONE)
            return
        except psutil.AccessDenied as e:
            logger.debug("Activity snapshot access denied for %d: %s", pid, e)
        except Exception as e:
            logger.warning("Activity snapshot failed for %d: %s", pid, e)
        self._snapshot_ready.emit(pid, gen, files, conns)

    @pyqtSlot(int, int, object, object)
    def _on_snapshot(self, pid: int, gen: int, files: object, conns: object) -> None:
        if gen != self._generation or pid != self._pid:
            return  # stale result from a previous selection or cancelled generation
        if not _widget_valid(self._table):
            return

        if files is _PROCESS_GONE:
            self._timer.stop()
            if _widget_valid(self._label):
                self._label.setText(f"PID {pid} no longer running")
            return

        files_set: Set[str] = files  # type: ignore[assignment]
        conns_set: Set[ConnKey] = conns  # type: ignore[assignment]
        ts = time.strftime("%H:%M:%S")

        if self._prev_files is not None:
            for path in sorted(files_set - self._prev_files):
                self._add_row(ts, "File opened", path, _OPEN_BRUSH)
            for path in sorted(self._prev_files - files_set):
                self._add_row(ts, "File closed", path, _CLOSE_BRUSH)

        if self._prev_conns is not None:
            for laddr, raddr, ctype, status in sorted(conns_set - self._prev_conns):
                self._add_row(ts, "Connection opened", f"{ctype} {laddr} -> {raddr or '*'} [{status}]", _OPEN_BRUSH)
            for laddr, raddr, ctype, status in sorted(self._prev_conns - conns_set):
                self._add_row(ts, "Connection closed", f"{ctype} {laddr} -> {raddr or '*'} [{status}]", _CLOSE_BRUSH)

        self._prev_files = files_set
        self._prev_conns = conns_set
        if _widget_valid(self._label):
            self._label.setText(f"Watching PID {pid} — {self._table.rowCount()} event(s) logged")

    def _add_row(self, ts: str, event: str, detail: str, brush: QBrush) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for c, val in enumerate([ts, event, detail]):
            item = centered_item(val)
            item.setForeground(brush)
            self._table.setItem(row, c, item)
        if self._table.rowCount() > _MAX_ROWS:
            self._table.removeRow(0)
        self._table.scrollToBottom()
