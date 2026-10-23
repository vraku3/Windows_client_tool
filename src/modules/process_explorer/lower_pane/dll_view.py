"""The lower pane's DLLs tab.

Reads `procengine/modinfo.py`. This file is the table and the line that
says what could not be read -- which matters because the previous version
showed an empty table for a process it could not open, and an empty DLL
list is not a possible state for a running process. It reads as an answer
and is not one.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from PyQt6.QtCore import pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (QHeaderView, QLabel, QTableWidget,
                             QVBoxLayout, QWidget)

from core.table_ui import centered_item, center_header
from ui.help.dashboard_ui import apply_help

logger = logging.getLogger(__name__)

_HEADERS = ["Name", "Description", "Company", "Version", "Base address",
            "Size", "Full path", "Signed"]


def _bytes(size: int) -> str:
    if not size:
        return "—"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024.0 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


def _signed(status) -> str:
    """The Signed column's text.

    The engine's statuses are the truth here: a file that could not be
    verified says so -- a dash would read as "unsigned", which is a claim
    about a file the machine did not get to examine.
    """
    if status == "valid":
        return "Yes"
    if status == "not_signed":
        return "No"
    if status == "invalid":
        return "Invalid"
    return "—"


class DllView(QWidget):
    _ready = pyqtSignal(int, object, object)   # pid, modules, reason

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready.connect(self._populate)
        self._pid: int = -1
        self._thread: Optional[threading.Thread] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("Select a process to view its modules")
        self._label.setWordWrap(True)
        layout.addWidget(self._label)
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # The full path takes the slack -- it is the widest and the one a
        # truncation makes useless.
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)
        apply_help(self, 'DLLs')

    def cancel(self) -> None:
        self._pid = -1

    def load_pid(self, pid: int) -> None:
        self.cancel()
        self._pid = pid
        self._table.setRowCount(0)
        self._label.setText(f"Reading modules for PID {pid}…")
        self._thread = threading.Thread(target=self._load, args=(pid,),
                                        daemon=True)
        self._thread.start()

    def _load(self, pid: int) -> None:
        from dataclasses import replace

        from core.procengine.modinfo import loaded_modules
        from core.procengine.signatures import verify_signature

        try:
            modules, reason = loaded_modules(pid)
        except Exception as error:  # noqa: BLE001
            logger.warning("Reading modules for pid %d failed: %s", pid, error)
            modules, reason = None, str(error)
        if modules is not None:
            # A signature is a fact about a FILE, cached per path, so a
            # machine that loads the same hundred DLLs in every process
            # pays the verification once. Still worth doing on the worker.
            modules = [replace(module, signature=verify_signature(module.path).status)
                       for module in modules]
        self._ready.emit(pid, modules, reason)

    @pyqtSlot(int, object, object)
    def _populate(self, pid: int, modules, reason) -> None:
        if pid != self._pid:
            return
        if modules is None:
            # NOT an empty table: a running process has loaded libraries by
            # definition, so "none" would be a claim we cannot make.
            self._table.setRowCount(0)
            # Win32 reasons already end in a full stop ("Access is
            # denied."), so appending one gives "denied.. Running".
            why = (reason or "access denied").rstrip(".")
            self._label.setText(
                f"The modules of PID {pid} could not be read — {why}. "
                f"Running elevated resolves most of these.")
            return

        self._table.setRowCount(len(modules))
        for index, module in enumerate(modules):
            cells = [
                module.name,
                module.description or "—",
                module.company or "—",
                module.version or "—",
                f"0x{module.base:X}",
                _bytes(module.size),
                module.path,
                _signed(module.signature),
            ]
            for column, text in enumerate(cells):
                self._table.setItem(index, column, centered_item(text))
        signed = sum(1 for module in modules if module.signature == "valid")
        self._label.setText(
            f"{len(modules):,} modules · {signed:,} signed")
