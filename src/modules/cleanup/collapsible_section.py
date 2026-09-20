"""A header bar with a disclosure arrow over a body that shows one child
widget. Visual style mirrors components/category_group.py's own arrow-
toggle gesture, but this is a generic container for an arbitrary pre-built
widget rather than something that owns its own scanner -- CleanupModule
uses it to wrap its seven existing tab widgets unchanged (see
docs/superpowers/specs/2026-09-20-cleanup-single-tab-merge-design.md).

QWidget.setVisible() toggling, not a QPropertyAnimation -- this app has no
existing animated-disclosure precedent and one is not worth introducing
for this.
"""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class _CollapsibleSection(QWidget):
    #: Fires ONLY on a collapsed -> expanded transition. CleanupModule
    #: connects this directly to a tab's own auto_scan(), whose own
    #: idempotency (`if not self._scanned`) makes a second connect-through
    #: safe -- but firing on every click (including collapsing, or
    #: re-clicking while already expanded) would be pointless busywork
    #: every time someone closes a section they already scanned.
    expanded = pyqtSignal()

    def __init__(self, title: str, body: QWidget, parent=None):
        super().__init__(parent)
        self._expanded = False
        self._title = title
        self._body = body

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QHBoxLayout()
        self._header_btn = QPushButton(f"▸ {title}")
        self._header_btn.setFlat(True)
        self._header_btn.setStyleSheet("text-align: left; font-weight: bold;")
        self._header_btn.clicked.connect(self._toggle)
        header.addWidget(self._header_btn, 1)
        self._summary_lbl = QLabel("")
        header.addWidget(self._summary_lbl)
        outer.addLayout(header)

        body.setVisible(False)
        outer.addWidget(body)

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        was_expanded = self._expanded
        self._expanded = expanded
        self._body.setVisible(expanded)
        arrow = "▾" if expanded else "▸"
        self._header_btn.setText(f"{arrow} {self._title}")
        if expanded and not was_expanded:
            self.expanded.emit()

    def set_summary(self, text: str) -> None:
        self._summary_lbl.setText(text)
