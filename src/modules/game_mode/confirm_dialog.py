"""A confirmation that fits on screen: lead sentence, scrollable details, OK / Cancel.

QMessageBox grows with its text and cannot be resized, so a long list of
changes ran off the bottom of the screen with the buttons below it. This
dialog keeps the details in a read-only scrollable QTextBrowser, opens at the
size of its content but never above 70% of the screen the parent is on, can
be resized, and never grows past that screen. Cancel is the default button,
so Enter does nothing destructive.
"""
from __future__ import annotations

from ui.help.debloat_widgets import explain
from . import help_text as help_info

import html

from collections import Counter

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel, QTextBrowser,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.semantic_colors import chrome, semantic
from .engine.preflight import PreflightItem

#: The share of the available screen the dialog may open at.
SCREEN_SHARE = 0.7
MIN_WIDTH = 520


def details_html(details: str) -> str:
    """Plain text -> escaped HTML: blank lines separate paragraphs, a line
    starting with two spaces is an indented row of a list."""
    blocks = []
    for para in details.strip().split("\n\n"):
        lines = []
        for line in para.splitlines():
            text = html.escape(line.strip())
            lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;{text}" if line.startswith("  ") else text)
        blocks.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(blocks)


class ConfirmDialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str, lead: str, details: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setSizeGripEnabled(True)
        lay = QVBoxLayout(self)
        self.lead = QLabel(lead, self)
        self.lead.setWordWrap(True)
        lay.addWidget(self.lead)
        self.details = QTextBrowser(self)
        self.details.setReadOnly(True)
        self.details.setOpenLinks(False)
        self.details.setHtml(details_html(details))
        lay.addWidget(self.details, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel, self)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        ok.setAutoDefault(False)
        ok.setDefault(False)
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._fit(parent)
        self.cancel_button.setFocus()
        explain(self, help_info.CONTROLS)

    def _available(self, parent):
        screen = (parent.screen() if parent is not None else None) or self.screen()
        return screen.availableGeometry() if screen is not None else None

    def _fit(self, parent) -> None:
        """Content size, capped at SCREEN_SHARE of the screen; never larger than it."""
        area = self._available(parent)
        if area is None:
            self.resize(720, 480)
            return
        self.setMaximumSize(area.size())
        cap = QSize(int(area.width() * SCREEN_SHARE), int(area.height() * SCREEN_SHARE))
        doc = self.details.document()
        natural = int(doc.idealWidth())                 # unwrapped: the widest line
        margins = self.layout().contentsMargins()
        frame = 2 * self.details.frameWidth() + self.details.verticalScrollBar().sizeHint().width()
        width = min(cap.width(), max(MIN_WIDTH, natural + frame + margins.left() + margins.right()))
        doc.setTextWidth(width - frame - margins.left() - margins.right())
        inner = width - margins.left() - margins.right()
        chrome = (self.lead.heightForWidth(inner) + self.buttons.sizeHint().height()
                  + 2 * self.layout().spacing() + margins.top() + margins.bottom() + frame)
        height = min(cap.height(), int(doc.size().height()) + chrome)
        self.resize(min(width, area.width()), min(max(height, 180), area.height()))


def confirm(parent: QWidget | None, title: str, lead: str, details: str) -> bool:
    return ConfirmDialog(parent, title, lead, details).exec() == QDialog.DialogCode.Accepted


class PreflightDialog(QDialog):
    def __init__(self, parent: QWidget | None, items: list[PreflightItem]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Game Mode Preflight")
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        lead = QLabel("Review the selected actions before turning Game Mode ON. "
                      "Checks reflect the current state; Windows may still refuse an action.", self)
        lead.setWordWrap(True)
        layout.addWidget(lead)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Action", "Status", "Privilege", "Details"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setMinimumSize(0, 100)
        categories = {"service": "Services", "app": "Apps", "task": "Scheduled tasks",
                      "power": "Power plan", "registry": "Windows settings", "priority": "Priorities"}
        statuses = {"ready": ("✓ Ready", "success"),
                    "already_done": ("✓ Already done — skipped", None),
                    "needs_admin": ("⚠ Needs admin — skipped", "warning"),
                    "not_found": ("✕ Not found — skipped", "error"),
                    "protected": ("✕ Protected — skipped", "error"),
                    "unknown": ("? Unknown", "warning")}
        parents = {}
        for item in items:
            if item.category not in parents:
                parents[item.category] = QTreeWidgetItem(self.tree, [categories[item.category]])
            text, color = statuses[item.status]
            row = QTreeWidgetItem(parents[item.category],
                                  [f"{text.split()[0]} {item.action}", text,
                                   "Administrator" if item.requires_admin else "Current user",
                                   item.detail])
            foreground = QColor(semantic(color) if color else chrome("text_muted"))
            row.setForeground(0, foreground)
            row.setForeground(1, foreground)
            for col in range(4):
                row.setToolTip(col, f"{item.name}\n{item.action}\n{text}\n{item.detail}")
        self.tree.expandAll()
        layout.addWidget(self.tree, 1)
        counts = Counter(item.status for item in items)
        self.summary = QLabel(", ".join(f"{counts[status]} {label}" for status, label in (
            ("ready", "actions ready"), ("already_done", "already done"), ("needs_admin", "need admin"),
            ("not_found", "not found"), ("protected", "protected"), ("unknown", "unknown"))), self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.warning = QLabel("Programs that do not close within 8 seconds are ended; unsaved work in them "
                              "is lost. Save your work before continuing. Game Mode OFF puts the settings "
                              "back and reopens the closed apps.", self)
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel, self)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        ok.setAutoDefault(False)
        ok.setDefault(False)
        cancel.setAutoDefault(True)
        cancel.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        area = self.screen().availableGeometry()
        self.setMaximumSize(area.size())
        self.resize(min(900, int(area.width() * SCREEN_SHARE)),
                    min(650, int(area.height() * SCREEN_SHARE)))
        cancel.setFocus()


def confirm_preflight(parent: QWidget | None, items: list[PreflightItem]) -> bool:
    return PreflightDialog(parent, items).exec() == QDialog.DialogCode.Accepted
