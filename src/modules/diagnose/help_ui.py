"""Apply the Batch D panes' explicit help text, including Qt's small controls.

Only descriptions are assigned here. No defaults, connections or actions change.
Call once after constructing a pane; runtime row explanations belong to its renderer.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLabel,
    QLineEdit, QMenu, QTabBar, QTabWidget, QTableWidget, QTreeWidget, QVBoxLayout, QWidget,
)
from ui.module_intro import module_intro


COMMON = {
    "Refresh": "Read this pane's data again. This does not apply changes to Windows; a slow system query can take a minute.",
    "Copy details": "Replace the clipboard with the selected item's details. No Windows setting changes and no confirmation is needed.",
    "Export CSV": "Choose a CSV file and save the displayed inventory. This writes a report, not a backup that can restore settings.",
    "Clear": "Clear the search and return to the tab view. This changes only what is displayed, not Windows or its logs.",
    "×": "Dismiss this error message. The underlying problem is not repaired and no Windows setting changes.",
    "OK": "Accept the values in this dialog and continue with the action described above. This closes the dialog.",
    "Cancel": "Close this dialog without accepting its values. Already completed work is not undone.",
    "Close": "Close this information window. No Windows setting changes.",
}


def _key(text):
    return text.replace("&&", "&").strip().lstrip("🔄⏱️🔌🚀⚡📋📝🔧🪟📊💥📅⚙️🧩🔍🗑️ ")


def _control_help(widget, controls):
    text = widget.text() if hasattr(widget, "text") else ""
    if isinstance(widget, QComboBox):
        text = widget.currentText()
    tip = controls.get(_key(text)) or COMMON.get(_key(text))
    if tip:
        return tip
    if isinstance(widget, QLineEdit):
        return ("Filter the displayed records as you type. " + widget.placeholderText()
                + " Clearing this field restores the view; no Windows settings change.")
    if isinstance(widget.parentWidget(), QLineEdit):
        return "Clear the text filter. This changes only the view; no Windows records are deleted."
    if isinstance(widget.parentWidget(), QTabBar):
        return "Scroll the tab strip to reach more panes. This changes only navigation, not Windows settings."
    if isinstance(widget, QAbstractButton) and not text:
        return "Select all rows in this table. Selection alone does not change Windows; use an action button to act on it."
    return None


def _headers(view, headers):
    if not (hasattr(view, "horizontalHeader") or hasattr(view, "header")):
        return
    model = view.model()
    if model is None:
        return
    for col in range(model.columnCount()):
        label = str(model.headerData(col, Qt.Orientation.Horizontal) or "")
        tip = headers.get(label)
        if not tip:
            continue
        if isinstance(view, QTableWidget):
            item = view.horizontalHeaderItem(col)
            if item and not item.toolTip():
                item.setToolTip(tip)
        elif isinstance(view, QTreeWidget):
            if not view.headerItem().toolTip(col):
                view.headerItem().setToolTip(col, tip)
        elif not model.headerData(col, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole):
            model.setHeaderData(col, Qt.Orientation.Horizontal, tip, Qt.ItemDataRole.ToolTipRole)


def explain(root, intro, controls=None, headers=None, tabs=None):
    """Add a themed introduction and missing descriptions from explicit maps."""
    controls, headers, tabs = controls or {}, headers or {}, tabs or {}
    if intro and root.layout() is not None:
        root.layout().insertWidget(0, module_intro(intro))
    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        if isinstance(widget, (QAbstractButton, QComboBox, QAbstractSpinBox, QLineEdit)):
            if not widget.toolTip():
                tip = _control_help(widget, controls)
                if tip:
                    widget.setToolTip(tip)
        if isinstance(widget, QAbstractItemView):
            _headers(widget, headers)
        if isinstance(widget, QTabWidget):
            _tabs(widget, tabs)
        if isinstance(widget, QMenu):
            explain_menu(widget, controls)


def _tabs(widget, tabs):
    for index in range(widget.count()):
        tip = tabs.get(_key(widget.tabText(index)))
        if tip and not widget.tabToolTip(index):
            widget.setTabToolTip(index, tip)
            page = widget.widget(index)
            if page.layout() is not None and not page.findChild(QLabel, "moduleIntro"):
                page.layout().insertWidget(0, module_intro(tip))


def explain_menu(menu, controls):
    for action in menu.actions():
        if action.isSeparator():
            continue
        tip = controls.get(_key(action.text())) or COMMON.get(_key(action.text()))
        if tip:
            current = action.toolTip().replace("...", "").replace("…", "")
            label = action.text().replace("...", "").replace("…", "")
            if not current or current == label:
                action.setToolTip(tip)
            action.setStatusTip(action.toolTip())
        elif action.toolTip() != action.text():
            action.setStatusTip(action.toolTip())


def hub(tabs, intro, descriptions):
    """CompositeModule.wrap implementation for the two otherwise bare hubs."""
    root = QWidget()
    layout = QVBoxLayout(root)
    layout.addWidget(tabs)
    explain(root, intro, tabs=descriptions)
    return root
