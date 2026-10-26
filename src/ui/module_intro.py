"""Compact, expandable introductions and one idempotent insertion seam."""
import hashlib
import logging
import re
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QToolButton, QHBoxLayout, QVBoxLayout, QGridLayout, QWidget, QScrollArea, QSizePolicy, QTabWidget, QStackedWidget

INTRO_NAME = "moduleIntro"


class ModuleIntro(QLabel):
    """One elided sentence, with the full explanation available on demand."""

    def __init__(self, text, parent=None, *, summary=None, config=None, key=None):
        super().__init__(parent)
        self.setObjectName(INTRO_NAME)
        self.full_text = text
        self.summary = summary or re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
        self.config = config
        if self.config is None:
            from app import App
            self.config = getattr(App.instance, "config", None)
        self.key = key or "ui.intros." + hashlib.sha256(text.encode()).hexdigest()[:16]
        self.setToolTip(text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.label = QLabel(self.summary)
        self.label.setObjectName("introSummary")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setToolTip(text)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.label, 1)
        self.toggle = QToolButton()
        self.toggle.setObjectName("introToggle")
        self.toggle.setText("ⓘ More")
        self.toggle.setCheckable(True)
        self.toggle.setToolTip("Expand or collapse the full explanation of this pane.")
        row.addWidget(self.toggle)
        layout.addLayout(row)
        self.details = QLabel(text)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        self.details.setToolTip(text)
        self.details.setObjectName("introDetails")
        layout.addWidget(self.details)
        expanded = bool(self.config.get(self.key, False)) if self.config is not None else False
        self.details.setVisible(expanded)
        self.toggle.setChecked(expanded)
        self.toggle.setText("ⓘ Less" if expanded else "ⓘ More")
        self.toggle.toggled.connect(self.set_expanded)

    def set_expanded(self, expanded):
        self.details.setVisible(expanded)
        self.toggle.setChecked(expanded)
        self.toggle.setText("ⓘ Less" if expanded else "ⓘ More")
        if self.config is not None:
            self.config.set(self.key, bool(expanded))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.label.setText(self.label.fontMetrics().elidedText(
            self.summary, Qt.TextElideMode.ElideRight, max(0, self.label.contentsRect().width())))


def module_intro(text: str, parent=None, *, summary=None, config=None, key=None):
    return ModuleIntro(text, parent, summary=summary, config=config, key=key)


def _layout_intro(layout, through_wrappers=False):
    """Search nested layouts, never descend into another pane's widgets."""
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if isinstance(widget, ModuleIntro) or (isinstance(widget, QLabel) and widget.objectName() == INTRO_NAME):
            return widget
        # Wrappers and scroll areas are the same hosting level; tabs and
        # stacks own independent panes and must keep their own introductions.
        if through_wrappers and widget is not None and not isinstance(widget, (QTabWidget, QStackedWidget, ModuleIntro)):
            content = widget.widget() if isinstance(widget, QScrollArea) else widget
            if content is not None and content.layout() is not None:
                found = _layout_intro(content.layout(), True)
                if found is not None:
                    return found
        if item.layout() is not None:
            found = _layout_intro(item.layout(), through_wrappers)
            if found is not None:
                return found
    return None


def add_module_intro(target, text, *, hosting_page=False, **kwargs):
    """Insert at most one intro into this pane; child tabs remain independent."""
    if isinstance(target, QScrollArea):
        target = target.widget()
    layout = target.layout() if target is not None else None
    if layout is None or (hosting_page and layout.count() == 0):
        return None
    existing = _layout_intro(layout, hosting_page)
    if existing is not None:
        return existing
    intro = module_intro(text, target, **kwargs)
    if isinstance(layout, QGridLayout):
        items = []
        while layout.count():
            items.append((layout.getItemPosition(0), layout.takeAt(0)))
        for (row, column, rows, columns), item in items:
            layout.addItem(item, row + 1, column, rows, columns)
        layout.addWidget(intro, 0, 0, 1, max(1, layout.columnCount()))
    elif hasattr(layout, "insertWidget"):
        layout.insertWidget(0, intro)
    else:
        intro.deleteLater()
        logging.getLogger(__name__).warning("Cannot insert introduction into %s", type(layout).__name__)
        return None
    return intro
