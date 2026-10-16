"""The "Required modules" dialog: every module/tool the Script Library's
server scripts need, whether this PC has it, how many scripts use it, and an
Install button that runs Windows' own installer in an elevated window."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from core.table_ui import set_role

from . import library, modules_catalog as mc


def confirm_install(parent: QWidget, req: mc.Requirement, command: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Install required module")
    box.setText(f"Install {req.label}?")
    box.setInformativeText(
        "Windows installs it with its own installer, in an administrator PowerShell window "
        "(you will see a UAC prompt). It downloads from Windows Update and can take a few "
        "minutes.\n\nIf this PC gets updates from WSUS, it may fail with 0x800F0954 until the "
        "'optional component installation' policy allows Windows Update.\n\nThe exact command "
        "is under Show Details.")
    box.setDetailedText(command)
    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    return box.exec() == QMessageBox.StandardButton.Ok


def start_install(parent: QWidget, req: mc.Requirement, server: bool) -> Optional[str]:
    """Confirm and start the install; returns the status message, or None if
    the person cancelled."""
    command = mc.install_command(req, server)
    if command is None:
        QMessageBox.information(parent, "Not installable here", mc.why_not_installable(req, server))
        return None
    if not confirm_install(parent, req, command):
        return None
    _started, message = mc.elevated_install(command)
    return message


class ModulesDialog(QDialog):
    def __init__(self, status: Dict[str, bool], server: bool,
                 scripts: List[library.Script], on_status: Callable[[str], None],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Required modules")
        self.setMinimumSize(900, 480)
        self._server = server
        self._on_status = on_status
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Server scripts need Microsoft's management modules (RSAT on Windows 10/11, features "
            "on Windows Server). They are Windows components, so they are installed by Windows -- "
            "not bundled into this app. Scripts whose module is missing print NOT AVAILABLE.",
            self)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        uses = mc.scripts_needing(scripts)
        reqs = sorted(mc.REQUIREMENTS.values(), key=lambda r: -len(uses.get(r.key, [])))
        table = QTableWidget(len(reqs), 4, self)
        table.setHorizontalHeaderLabels(["Module / tool", "On this PC", "Scripts", ""])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for row, req in enumerate(reqs):
            have = status.get(req.key)
            text, meaning = (("Installed", "success") if have else
                             ("Not installed", "warning") if have is False else ("Unknown", "info"))
            table.setItem(row, 0, QTableWidgetItem(req.label))
            state = QTableWidgetItem(text)
            state.setForeground(QColor(semantic(meaning)))
            table.setItem(row, 1, state)
            count = QTableWidgetItem(str(len(uses.get(req.key, []))))
            count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 2, count)
            if not have:
                table.setCellWidget(row, 3, self._install_button(req))
        table.resizeRowsToContents()
        lay.addWidget(table, 1)
        self._status = QLabel("", self)
        set_role(self._status, "statusInfo")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _install_button(self, req: mc.Requirement) -> QWidget:
        command = mc.install_command(req, self._server)
        b = QPushButton("Install…" if command else "Server only", self)
        b.setEnabled(command is not None)
        if command is None:
            b.setToolTip(mc.why_not_installable(req, self._server))
        b.clicked.connect(lambda: self._install(req))
        return b

    def _install(self, req: mc.Requirement) -> None:
        message = start_install(self, req, self._server)
        if message:
            self._status.setText(message)
            self._on_status(message)
