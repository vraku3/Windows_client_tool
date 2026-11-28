"""Classic Windows Control Panel tab inside System Utilities."""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import set_role
from modules.control_panel import control_panel_catalog as cat
from ui.chip_bar import ChipBar

logger = logging.getLogger(__name__)


def _alive(widget) -> bool:
    return widget is not None and not sip.isdeleted(widget)


class _AppletCard(QFrame):
    """Single Classic Control Panel applet card."""

    def __init__(self, item: cat.ControlPanelItem, on_launch, on_copy, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("controlPanelCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QFrame#controlPanelCard {"
            "  border: 1px solid rgba(128, 128, 128, 0.28);"
            "  border-radius: 6px;"
            "  padding: 2px;"
            "}"
            "QFrame#controlPanelCard:hover {"
            "  border: 1px solid rgba(56, 139, 253, 0.7);"
            "}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        icon_lbl = QLabel(item.icon)
        icon_lbl.setFixedWidth(22)
        top.addWidget(icon_lbl)

        title = QLabel(f"<b>{item.name}</b>")
        title.setWordWrap(True)
        top.addWidget(title, 1)

        if item.requires_admin:
            badge = QLabel("Admin")
            badge.setToolTip("May require administrator privileges.")
            set_role(badge, "statusWarning")
            top.addWidget(badge)
        root.addLayout(top)

        desc = QLabel(item.description)
        desc.setWordWrap(True)
        desc.setMinimumHeight(32)
        set_role(desc, "muted")
        root.addWidget(desc)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        cmd_lbl = QLabel(item.command_label)
        set_role(cmd_lbl, "mono")
        cmd_lbl.setToolTip("Windows command / Control Panel applet name")
        footer.addWidget(cmd_lbl, 1)

        copy_btn = QPushButton("Copy")
        copy_btn.setFixedHeight(24)
        copy_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        copy_btn.setStyleSheet("padding: 2px 8px; min-height: 20px; max-height: 24px; font-size: 11px;")
        copy_btn.setToolTip(f"Copy '{item.command_label}' to clipboard")
        copy_btn.clicked.connect(lambda: on_copy(item))
        footer.addWidget(copy_btn)

        open_btn = QPushButton("Open")
        open_btn.setFixedHeight(24)
        open_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        open_btn.setStyleSheet("padding: 2px 10px; min-height: 20px; max-height: 24px; font-size: 11px;")
        open_btn.setToolTip(f"Launch {item.name} ({item.command_label})")
        open_btn.clicked.connect(lambda: on_launch(item))
        footer.addWidget(open_btn)
        root.addLayout(footer)


class _ControlPanelPane(QWidget):
    """Searchable, categorized Classic Control Panel launcher."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._category = "All"
        self._build()
        self._render()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Toolbar
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "Search applets, commands (ncpa.cpl, mmsys.cpl, powercfg.cpl), or keywords..."
        )
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _t: self._render())
        bar.addWidget(self.search_edit, 1)

        classic_btn = QPushButton("Open Classic Control Panel")
        classic_btn.setToolTip("Launch the native Windows Control Panel root window (control.exe)")
        classic_btn.clicked.connect(self._open_classic)
        bar.addWidget(classic_btn)

        god_btn = QPushButton("All Tasks (God Mode)")
        god_btn.setToolTip("Open Windows' built-in All Control Panel Tasks folder")
        god_btn.clicked.connect(self._open_god_mode)
        bar.addWidget(god_btn)
        layout.addLayout(bar)

        # Category filter chips
        counts = cat.category_counts()
        chip_defs = [("All", "All", counts["All"])] + [
            (c, c, counts.get(c, 0)) for c in cat.CATEGORIES
        ]
        self._chip_bar = ChipBar(chip_defs)
        self._chip_bar.chips["All"].setChecked(True)
        self._chip_bar.selection_changed.connect(
            lambda keys: self._set_category(keys[0]) if keys else None
        )
        layout.addWidget(self._chip_bar)

        # Status line
        self.status = QLabel("")
        set_role(self.status, "muted")
        layout.addWidget(self.status)

        # Scrollable grid of cards
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setSpacing(8)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

    def _set_category(self, category: str) -> None:
        self._category = category
        self._render()

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render(self) -> None:
        self._clear_grid()
        items: List[cat.ControlPanelItem] = cat.filter_applets(
            self._category, self.search_edit.text()
        )
        cols = 3
        if not items:
            empty = QLabel("No Control Panel applets match your filter.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_role(empty, "muted")
            self._grid.addWidget(empty, 0, 0, 1, cols)
        else:
            for idx, applet in enumerate(items):
                card = _AppletCard(applet, self._launch, self._copy_cmd)
                self._grid.addWidget(card, idx // cols, idx % cols)
            for c in range(cols):
                self._grid.setColumnStretch(c, 1)
            self._grid.setRowStretch((len(items) + cols - 1) // cols, 1)

        self.status.setText(
            f"Showing {len(items)} of {len(cat.ALL_APPLETS)} classic Control Panel applets."
        )

    def _launch(self, item: cat.ControlPanelItem) -> None:
        ok, msg = cat.launch_applet(item)
        self.status.setText(msg)
        set_role(self.status, "statusSuccess" if ok else "statusError")

    def _copy_cmd(self, item: cat.ControlPanelItem) -> None:
        QApplication.clipboard().setText(item.command_label)
        self.status.setText(f"Copied '{item.command_label}' to clipboard.")
        set_role(self.status, "muted")

    def _open_classic(self) -> None:
        ok, msg = cat.open_classic_control_panel()
        self.status.setText(msg)
        set_role(self.status, "statusSuccess" if ok else "statusError")

    def _open_god_mode(self) -> None:
        ok, msg = cat.open_god_mode()
        self.status.setText(msg)
        set_role(self.status, "statusSuccess" if ok else "statusError")


class ControlPanelModule(BaseModule):
    name = "Control Panel"
    icon = "🎛️"
    description = "Classic Windows Control Panel applets, MMC consoles, and legacy system tools."
    requires_admin = False
    group = ModuleGroup.TOOLS

    def __init__(self) -> None:
        super().__init__()
        self._pane: Optional[_ControlPanelPane] = None

    def create_widget(self) -> QWidget:
        self._pane = _ControlPanelPane()
        return self._pane

    def on_start(self, app=None) -> None:
        self.app = app

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        pass

    def on_stop(self) -> None:
        self.cancel_all_workers()
