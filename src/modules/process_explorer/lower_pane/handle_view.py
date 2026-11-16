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
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QTableWidget, QVBoxLayout, QWidget)

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
        top_row = QHBoxLayout()
        self._label = QLabel("Select a process to view handles")
        self._label.setWordWrap(True)
        top_row.addWidget(self._label, 1)
        self._search = QLineEdit()
        self._search.setObjectName("handle_search")
        self._search.setPlaceholderText("Filter handles by name or type...")
        self._search.setMaximumWidth(260)
        self._search.textChanged.connect(self._apply_filter)
        top_row.addWidget(self._search)
        layout.addLayout(top_row)
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self._table.setColumnWidth(0, 140)
        self._table.setColumnWidth(1, 380)
        self._table.setColumnWidth(2, 120)
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
        self._rows: list = []
        self._note = ""
        self._search.clear()
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
        self._rows = rows
        self._note = note
        self._apply_filter()
        self._table.show()

    def _apply_filter(self) -> None:
        rows = getattr(self, "_rows", None) or []
        query = self._search.text().strip().lower()
        if query:
            filtered = [
                r for r in rows
                if query in (r.name or "").lower()
                or query in (r.type_name or "").lower()
            ]
        else:
            filtered = rows

        from core.procengine.handles import access_flags
        self._table.setRowCount(len(filtered))
        for index, row in enumerate(filtered):
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
                    item.setForeground(Qt.GlobalColor.gray)
                    item.setToolTip(row.unavailable or "")
                self._table.setItem(index, column, item)

        if query:
            self._label.setText(f"{len(filtered):,} of {len(rows):,} handles matching filter")
        else:
            self._label.setText(self._summary(rows, self._note))

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
