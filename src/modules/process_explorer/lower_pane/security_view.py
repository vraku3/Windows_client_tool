"""The Security view: token, groups, privileges, protection, mitigations.

Process Explorer puts the token on its Properties > Security tab and the
mitigations in columns. Here both are one view, shown in the lower pane
for the selected process AND as the Properties dialog's Security tab --
the dialog's old tab printed the user and SID and nothing else, opened the
process with a right fewer processes grant, and told every refusal
"(Requires elevated privileges)" whatever the real reason was.

The reads live in `core/procengine/tokeninfo.py` and `mitigations.py`;
this file is tables and labels. Both reads together cost a few ms, but
they still run on a thread, like the DLL view, because SID name lookups
can stall on a domain-joined machine with an unreachable DC.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QGridLayout, QLabel, QSplitter, QTableWidget,
                             QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from core.table_ui import centered_item, fit_table, set_role

logger = logging.getLogger(__name__)

_HEADER_ROWS = ("User", "User SID", "Session", "Elevation", "Virtualization",
                "AppContainer", "Powerful privileges enabled",
                "Protection", "DEP", "ASLR", "CFG")


def load_security(pid: int, image_path: Optional[str] = None,
                  architecture: Optional[str] = None):
    """`(token_report, mitigation_report)` for one process. Never raises."""
    from core.procengine.mitigations import read_mitigations
    from core.procengine.tokeninfo import read_token

    if architecture is None or image_path is None:
        from core.procengine.details import resolve
        details = resolve(pid)
        image_path = image_path or details.path
        architecture = architecture or details.architecture
    return read_token(pid), read_mitigations(pid, image_path, architecture)


class SecurityView(QWidget):
    _ready = pyqtSignal(int, object, object)   # pid, token, mitigations

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready.connect(self._populate)
        self._pid: int = -1
        self._thread: Optional[threading.Thread] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("Select a process to view its token and "
                             "mitigations")
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._facts = {}
        facts = QWidget(self)
        grid = QGridLayout(facts)
        grid.setContentsMargins(4, 0, 4, 0)
        for index, name in enumerate(_HEADER_ROWS):
            caption = QLabel(f"<b>{name}:</b>", facts)
            value = QLabel("—", facts)
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            # Three pairs a row: at two, the facts took six of the lower
            # pane's ~250px and left the tables one row tall (screenshot).
            row, column = divmod(index, 3)
            grid.addWidget(caption, row, column * 2)
            grid.addWidget(value, row, column * 2 + 1)
            self._facts[name] = value
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(5, 1)
        layout.addWidget(facts)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        # The SID takes the slack: with the name stretched, a long group
        # name pushed SID and Flags off the right edge behind a scrollbar.
        self._groups = self._table(["Group", "SID", "Flags"], stretch=[1])
        self._privileges = self._table(["Privilege", "Status"], stretch=[0])
        self._mitigations = self._table(["Mitigation policy"], stretch=[0])
        for table in (self._groups, self._privileges, self._mitigations):
            split.addWidget(table)
        split.setSizes([500, 300, 260])
        layout.addWidget(split, 1)

    @staticmethod
    def _table(headers, stretch) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        fit_table(table, stretch=stretch,
                  content=[c for c in range(len(headers)) if c not in stretch])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(False)
        return table

    def cancel(self) -> None:
        self._pid = -1

    def load_pid(self, pid: int, image_path: Optional[str] = None,
                 architecture: Optional[str] = None) -> None:
        self.cancel()
        self._pid = pid
        self._label.setText(f"Reading the token of PID {pid}…")
        set_role(self._label, "")
        self._thread = threading.Thread(
            target=self._load, args=(pid, image_path, architecture),
            daemon=True)
        self._thread.start()

    def _load(self, pid, image_path, architecture) -> None:
        try:
            token, mitigations = load_security(pid, image_path or None,
                                               architecture)
        except Exception as error:  # noqa: BLE001 - shown, not swallowed
            logger.warning("Security read for pid %d failed: %s", pid, error)
            token = mitigations = None
        self._ready.emit(pid, token, mitigations)

    @pyqtSlot(int, object, object)
    def _populate(self, pid: int, token, mitigations) -> None:
        if pid != self._pid:
            return
        show(self, pid, token, mitigations)


def show(view: SecurityView, pid: int, token, mitigations) -> None:
    """Fill `view` from the two reports. Module-level so a test can call it
    with synthetic reports and no thread."""
    from core.procengine import mitigations as mit
    from core.procengine import tokeninfo

    for value in view._facts.values():
        value.setText("—")
    rows = []
    if token is not None:
        rows += tokeninfo.summarize(token)
    if mitigations is not None:
        rows += mit.summarize(mitigations)
    for label, text in rows:
        if label in view._facts:
            view._facts[label].setText(text)

    _fill_groups(view._groups, token)
    _fill_privileges(view._privileges, token)
    _fill_mitigations(view._mitigations, mitigations)
    _status_line(view, pid, token, mitigations)


def _status_line(view, pid, token, mitigations) -> None:
    if token is None or not token.readable:
        why = (token.error if token is not None else "the read failed")
        view._label.setText(
            f"The token of PID {pid} could not be read — {why}. That is a "
            f"refusal, not an empty token; running elevated reads most of "
            f"these.")
        set_role(view._label, "statusWarning")
        return
    powerful = token.enabled_powerful()
    if powerful:
        view._label.setText(
            f"PID {pid} has {len(powerful)} powerful privilege"
            f"{'s' if len(powerful) != 1 else ''} ENABLED: "
            f"{', '.join(powerful)}.")
        set_role(view._label, "statusWarning")
        return
    gaps = list(token.gaps)
    if mitigations is not None and not mitigations.readable:
        gaps.append(f"mitigations: {mitigations.error}")
    view._label.setText(
        f"{len(token.groups or ())} groups · {len(token.privileges or ())} "
        f"privileges" + (f" · not read: {'; '.join(gaps)}" if gaps else ""))
    set_role(view._label, "")


def _fill_groups(table: QTableWidget, token) -> None:
    groups = (token.groups if token is not None else None) or []
    table.setRowCount(len(groups))
    for row, group in enumerate(groups):
        cells = [group.name, group.sid, group.flags()]
        for column, text in enumerate(cells):
            item = centered_item(text)
            if group.deny_only:
                # A deny-only Administrators membership is the whole of
                # what UAC filtering is; it must not read as membership.
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
                item.setToolTip("Deny-only: this group can only DENY "
                                "access here, never grant it")
            table.setItem(row, column, item)


def _fill_privileges(table: QTableWidget, token) -> None:
    privileges = (token.privileges if token is not None else None) or []
    table.setRowCount(len(privileges))
    for row, privilege in enumerate(privileges):
        status = "Enabled" if privilege.enabled else "Disabled"
        if privilege.default_enabled and privilege.enabled:
            status = "Default Enabled"
        name_item = centered_item(privilege.name)
        status_item = centered_item(status)
        if privilege.powerful and privilege.enabled:
            for item in (name_item, status_item):
                item.setForeground(QColor(semantic("warning")))
                item.setToolTip("Powerful privilege, switched on: reaches "
                                "past this process's own account")
        table.setItem(row, 0, name_item)
        table.setItem(row, 1, status_item)


def _fill_mitigations(table: QTableWidget, mitigations) -> None:
    if mitigations is None or not mitigations.readable:
        lines = []
    else:
        lines = mitigations.active()
        if mitigations.refused:
            lines.append(f"({len(mitigations.refused)} policies refused)")
    table.setRowCount(len(lines))
    for row, text in enumerate(lines):
        table.setItem(row, 0, centered_item(text))
