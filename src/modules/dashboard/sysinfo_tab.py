"""System Info: the whole machine in one readable page, copyable for a ticket."""
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import sysinfo
from .tab_base import DashModule, DashTab

logger = logging.getLogger(__name__)


class SystemInfoTab(DashTab):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sections = []
        self._loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(self.refresh)
        self._copy_btn = QPushButton("Copy all", self)
        self._copy_btn.setToolTip("Copy the whole page as plain text")
        self._copy_btn.clicked.connect(self._copy_all)
        top.addWidget(self._refresh_btn)
        top.addWidget(self._copy_btn)
        top.addStretch(1)
        layout.addLayout(top)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Item", "Value"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, 1)
        self.status = QLabel("", self)
        layout.addWidget(self.status)

    def start(self) -> None:
        # Hardware does not change while you watch: read once, then on demand.
        if not self._loaded:
            self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.status.setText("Reading…")
        self._refresh_btn.setEnabled(False)
        self.run(lambda _w: sysinfo.collect_sections(), self._apply, com=True)

    def _apply(self, sections) -> None:
        self._busy = False
        self._loaded = True
        self._sections = sections
        self._refresh_btn.setEnabled(True)
        self.tree.clear()
        for title, rows in sections:
            top = QTreeWidgetItem([title])
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            self.tree.addTopLevelItem(top)
            for label, value in rows:
                QTreeWidgetItem(top, [str(label), str(value)])
            top.setExpanded(True)
        self.status.setText(f"{len(sections)} sections")

    def _default_error(self, message) -> None:
        super()._default_error(message)
        self._refresh_btn.setEnabled(True)
        self.status.setText(f"Could not read system information: {message}")

    def _copy_all(self) -> None:
        if self._sections:
            QApplication.clipboard().setText(sysinfo.to_text(self._sections))
            self.status.setText("Copied.")


class SystemInfoModule(DashModule):
    name = "System Info"
    icon = "🖥"
    description = "OS, board, BIOS, CPU, memory, graphics, storage and adapters"
    tab_class = SystemInfoTab
