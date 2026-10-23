"""The lower pane's Handles tab.

Reads `procengine/handles.py`, which is where the enumeration and naming
live and where the reasons they can fail are documented. This file is the
table and the banner that says what could not be read.

The banner matters more here than anywhere else in the pane. Without a
kernel driver, and unelevated, a great many handles in other users'
processes cannot be duplicated and so cannot be named -- and a table full
of blank Name cells reads as "these objects have no names", which is a
different and wrong statement. The count of what could not be read is
always shown.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (QHeaderView, QLabel, QTableWidget,
                             QVBoxLayout, QWidget)

from core.table_ui import centered_item, center_header
from ui.help.dashboard_ui import apply_help

logger = logging.getLogger(__name__)

_HEADERS = ["Type", "Name", "Handle", "Object address", "Access"]


class HandleView(QWidget):
    _ready = pyqtSignal(int, object, object)   # pid, rows, note

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready.connect(self._populate)
        self._pid: int = -1
        self._thread: Optional[threading.Thread] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("Select a process to view handles")
        self._label.setWordWrap(True)
        layout.addWidget(self._label)
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # Name takes the slack. It is the column worth reading, and the
        # default widths left a third of the table empty while truncating
        # "\REGISTRY\MACHINE\SOFTWARE\Microsoft\..." to nothing useful.
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        center_header(self._table)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.hide()
        layout.addWidget(self._table)
        apply_help(self, 'Handles')

    def cancel(self) -> None:
        # The pid is the guard: a result for a pid we are no longer showing
        # is dropped rather than painted over the new selection.
        self._pid = -1
        self._label.setText("Select a process to view handles")
        self._table.hide()
        self._thread = None

    def load_pid(self, pid: int) -> None:
        self.cancel()
        self._pid = pid
        self._label.setText(f"Reading handles for PID {pid}…")
        self._thread = threading.Thread(target=self._load, args=(pid,),
                                        daemon=True)
        self._thread.start()

    def _load(self, pid: int) -> None:
        from core.procengine.handles import (
            HandleNamer, system_handles,
        )

        try:
            entries = system_handles(pid)
            rows, note = HandleNamer().describe(entries)
        except Exception as error:  # noqa: BLE001
            logger.warning("Reading handles for pid %d failed: %s", pid, error)
            rows, note = [], f"The handle table could not be read: {error}"
        self._ready.emit(pid, rows, note)

    @pyqtSlot(int, object, object)
    def _populate(self, pid: int, rows: list, note) -> None:
        if pid != self._pid:
            return
        from core.procengine.handles import access_flags

        self._table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            entry = row.entry
            name = row.name if row.name else f"— {row.unavailable or ''}".strip()
            cells = [
                row.type_name or f"type {entry.type_index}",
                name,
                f"0x{entry.value:X}",
                f"0x{entry.object_address:X}" if entry.object_address else "—",
                access_flags(entry.granted_access, row.type_name),
            ]
            for column, text in enumerate(cells):
                item = centered_item(text)
                if column == 1 and not row.name:
                    # An unresolved name is greyed, so a glance separates
                    # "this object is called X" from "we could not ask".
                    item.setForeground(Qt.GlobalColor.gray)
                    item.setToolTip(row.unavailable or "")
                self._table.setItem(index, column, item)

        self._label.setText(self._summary(rows, note))
        self._table.show()

    @staticmethod
    def _summary(rows: List, note) -> str:
        if not rows:
            return note or "This process has no open handles."
        named = sum(1 for row in rows if row.name)
        unnamed = len(rows) - named
        text = f"{len(rows):,} handles · {named:,} named"
        if unnamed:
            # Said explicitly rather than left to the blank cells, which
            # would read as "these objects have no names".
            text += f" · {unnamed:,} could not be named"
        if note:
            text += f"\n{note}"
        return text
