"""History: what each clean REALLY did -- measured bytes, files left in use and
by whom, refusals with their reason. Reads cleanup_history; computes nothing."""
import logging
import os
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QHeaderView, QLabel,
                             QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core.formatting import format_size
from modules.cleanup import cleanup_history

logger = logging.getLogger(__name__)

COLUMNS = ("When", "Freed", "Files", "Left in use", "Refused", "Summary")


def _path_of(line: str) -> str:
    """The path on a detail line, or "" -- drops the '--  reason' and '(size)' tails."""
    line = line.strip()
    if line[1:3] != ":\\":
        return ""
    return line.split("  --  ")[0].split("  (")[0]


def _nearest_existing_dir(path: str) -> str:
    """The path itself if a directory, else its nearest existing ancestor, else ""."""
    cur = path
    while True:
        if os.path.isdir(cur):
            return cur
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            return ""
        cur = parent


def _detail_text(entry: dict) -> str:
    lines = [cleanup_history.summarise(entry), ""]
    owners = entry.get("owners") or {}
    if owners:
        lines.append("Held by: " + ", ".join(f"{n} ({c})" for n, c in owners.items()))
    largest = entry.get("largest") or []
    if largest:
        lines.append("Removed (largest):")
        lines += [f"  {p}  ({format_size(s)})" for p, s in largest]
    for title, key in (("In use", "locked"), ("Access denied", "denied")):
        paths = entry.get(key) or []
        if paths:
            lines.append(f"{title}:")
            lines += [f"  {p}" for p in paths]
    if entry.get("cloud_only"):
        lines.append(f"Cloud-only files left: {entry['cloud_only']:,}")
    refused = entry.get("refused") or []
    if refused:
        lines.append("Refused:")
        for row in refused:
            path, reason = (row if isinstance(row, list) else [row, "refused"])
            lines.append(f"  {path}  --  {reason}")
    return "\n".join(lines)


class _HistoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.status = QLabel()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        top.addWidget(self.status, 1)
        top.addWidget(refresh)
        layout.addLayout(top)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        layout.addWidget(split, 1)
        buttons = QHBoxLayout()
        for text, slot in (("Open folder", self._open_folder), ("Copy path", self._copy_path)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def refresh(self) -> None:
        self._entries = cleanup_history.recent_deleted(limit=200)
        self.table.setRowCount(len(self._entries))
        for row, e in enumerate(self._entries):
            cells = (e.get("at", "").replace("T", " "), format_size(e.get("bytes", 0)),
                     f"{e.get('count', 0):,}", f"{e.get('locked_count', 0):,}",
                     str(len(e.get("refused") or [])), cleanup_history.summarise(e))
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.status.setText(f"{len(self._entries)} cleans recorded." if self._entries
                            else "No cleans recorded yet.")
        self.detail.clear()

    def _show_detail(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self._entries):
            self.detail.setPlainText(_detail_text(self._entries[rows[0].row()]))

    def _selected_path(self) -> str:
        return _path_of(self.detail.textCursor().block().text())

    def _open_folder(self) -> None:
        path = self._selected_path()
        if not path:
            return
        folder = _nearest_existing_dir(path)
        if not folder:
            self.status.setText("That folder no longer exists.")
            return
        try:
            subprocess.Popen(["explorer", folder])
        except OSError as e:
            logger.warning("could not open %s: %s", folder, e)
            self.status.setText(f"Could not open the folder: {e}")

    def _copy_path(self) -> None:
        path = self._selected_path()
        if path:
            QApplication.clipboard().setText(path)
