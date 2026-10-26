"""Apply explicit help text to this batch's widgets without changing behaviour."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractButton, QComboBox, QLabel, QLineEdit, QMenu,
                             QScrollArea, QTabBar, QTabWidget, QTableWidget,
                             QTreeWidget, QWidget)
from ui.module_intro import add_module_intro
from ui.help import debloat_text as help_text


def key(text):
    return text.replace('&&', '&').replace('&', '').strip().split('\t')[0].lstrip('🔄💾⌨⛭☑☐🗑️✕⚡🧹📦 ').strip()


def add_intro(root, text, *, hosting_page=False):
    add_module_intro(root, text, hosting_page=hosting_page)


def action_help(action, controls):
    tip = controls.get(key(action.text()))
    if tip:
        if not action.statusTip():
            action.setStatusTip(tip)
        if not action.toolTip() or key(action.toolTip()).rstrip('? .') == key(action.text()).rstrip('? .'):
            action.setToolTip(tip)


def explain(root, controls=None, headers=None, panes=None, intro=None):
    controls = {**help_text.CONTROLS, **(controls or {})}
    headers = {**help_text.HEADERS, **(headers or {})}
    panes = {**help_text.INTRO, **(panes or {})}
    controls = {key(label): text for label, text in controls.items()}
    panes = {key(label): text for label, text in panes.items()}
    if intro:
        add_intro(root, intro)
    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                name = key(widget.tabText(index))
                text = panes.get(name)
                if text:
                    widget.setTabToolTip(index, text)
                    add_intro(widget.widget(index), text, hosting_page=True)
        if isinstance(widget, QTabBar):
            for index in range(widget.count()):
                if not widget.tabToolTip(index):
                    widget.setTabToolTip(index, 'Filter the displayed app list to this package type. No app is changed.')
        if isinstance(widget, QAbstractButton) and not widget.toolTip():
            tip = controls.get(key(widget.text()))
            if tip:
                widget.setToolTip(tip)
            elif widget.objectName() in ('ScrollLeftButton', 'ScrollRightButton'):
                widget.setToolTip('Scroll the tab strip to reveal more tabs; this changes no Windows settings.')
            elif widget.objectName() == 'qt_clear_button' or isinstance(widget.parentWidget(), QLineEdit):
                widget.setToolTip('Clear this search filter and show matching rows again. No Windows setting is changed.')
            elif type(widget).__name__ == 'QAbstractButton':
                widget.setToolTip('Select rows in this table. Selection alone does not apply changes.')
            elif widget.text() == '×':
                widget.setToolTip('Dismiss this message. This does not undo any completed operation.')
        if isinstance(widget, QLineEdit) and not widget.toolTip():
            widget.setToolTip('Filter the displayed rows as you type. Clear the text to show them again; this changes no Windows settings.')
        if isinstance(widget, QComboBox) and not widget.toolTip():
            widget.setToolTip(controls.get('__combo__', 'Choose which catalog category to display. This filters the list without applying changes.'))
        if isinstance(widget, QMenu):
            for action in widget.actions():
                action_help(action, controls)
        if isinstance(widget, (QTableWidget, QTreeWidget)):
            for col in range(widget.columnCount()):
                item = widget.headerItem() if isinstance(widget, QTreeWidget) else widget.horizontalHeaderItem(col)
                if item is None:
                    continue
                label = item.text(col) if isinstance(widget, QTreeWidget) else item.text()
                tip = headers.get(label)
                if tip:
                    if isinstance(widget, QTreeWidget):
                        if not item.toolTip(col):
                            item.setToolTip(col, tip)
                    elif not item.toolTip():
                        item.setToolTip(tip)
