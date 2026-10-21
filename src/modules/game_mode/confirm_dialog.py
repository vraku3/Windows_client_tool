"""A confirmation that fits on screen: lead sentence, scrollable details, OK / Cancel.

QMessageBox grows with its text and cannot be resized, so a long list of
changes ran off the bottom of the screen with the buttons below it. This
dialog keeps the details in a read-only scrollable QTextBrowser, opens at the
size of its content but never above 70% of the screen the parent is on, can
be resized, and never grows past that screen. Cancel is the default button,
so Enter does nothing destructive.
"""
from __future__ import annotations

import html

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout, QWidget

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
