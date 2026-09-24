# src/modules/process_explorer/lower_pane/thread_view.py
from __future__ import annotations
import logging
import threading
import time
from typing import Dict, Optional

import psutil
from PyQt6.QtCore import pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTableWidget, QHeaderView

from core.table_ui import centered_item, center_header

logger = logging.getLogger(__name__)

_HEADERS = ["TID", "CPU%", "User Time", "System Time"]

#: How long the two samples behind CPU% are apart. Short enough that the
#: table still feels immediate, long enough that a busy thread reads clearly.
SAMPLE_SECONDS = 0.5


def thread_cpu_percent(before, after, elapsed: float) -> Dict[int, float]:
    """CPU% per thread id from two samples of `psutil` thread tuples.

    A thread present only in `after` has no baseline, so it is left out
    rather than reported as 0 -- "started during the sample" and "idle" are
    different answers. One thread cannot exceed 100%, so the result is
    clamped: the clock and the CPU counters are read at slightly different
    moments.
    """
    if elapsed <= 0:
        return {}
    used_before = {t.id: t.user_time + t.system_time for t in before}
    result: Dict[int, float] = {}
    for t in after:
        if t.id not in used_before:
            continue
        delta = (t.user_time + t.system_time) - used_before[t.id]
        result[t.id] = max(0.0, min(100.0, delta / elapsed * 100.0))
    return result


class ThreadView(QWidget):
    _data_ready = pyqtSignal(int, object)  # (pid, threads)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_ready.connect(self._populate)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        center_header(self._table)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)
        self._pid: int = -1
        self._thread: Optional[threading.Thread] = None

    def cancel(self) -> None:
        self._pid = -1

    def load_pid(self, pid: int):
        self.cancel()
        self._pid = pid
        self._table.setRowCount(0)
        self._thread = threading.Thread(target=self._load, args=(pid,), daemon=True)
        self._thread.start()

    def _refresh(self):
        if self._pid != -1:
            self.load_pid(self._pid)

    def _load(self, pid: int):
        percent: Dict[int, float] = {}
        try:
            proc = psutil.Process(pid)
            first = proc.threads()
            started = time.monotonic()
            time.sleep(SAMPLE_SECONDS)
            if self._pid != pid:
                return  # the user moved on; nobody is waiting for this
            threads = proc.threads()
            percent = thread_cpu_percent(first, threads,
                                         time.monotonic() - started)
        except psutil.NoSuchProcess:
            threads = []
        except psutil.AccessDenied:
            logger.debug("Thread list denied for pid %d", pid)
            threads = []
        self._data_ready.emit(pid, (threads, percent))

    @pyqtSlot(int, object)
    def _populate(self, pid: int, payload):
        if pid != self._pid:
            return  # stale result
        threads, percent = payload
        self._table.setRowCount(len(threads))
        for r, t in enumerate(threads):
            for c, val in enumerate([
                str(t.id),
                f"{percent[t.id]:.1f}" if t.id in percent else "—",
                f"{t.user_time:.3f}s",
                f"{t.system_time:.3f}s",
            ]):
                self._table.setItem(r, c, centered_item(val))
