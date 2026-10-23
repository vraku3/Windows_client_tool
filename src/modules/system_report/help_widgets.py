"""Apply this pane's explanations without changing signals or data."""
from html import escape
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractButton, QComboBox, QAbstractSpinBox, QLineEdit, QSlider,
    QLabel, QMenu, QTableWidget, QTreeWidget, QTabWidget, QTabBar, QWidget,
)
from ui.module_intro import module_intro
from .help_text import INTRO, control_help, header_help, tab_help


def describe_control(control, key=''):
    if control is None or not hasattr(control, 'setToolTip'):
        return control
    if key:
        control.setProperty('explanationKey', key)
    else:
        key = control.property('explanationKey') or ''
    text = control.text() if hasattr(control, 'text') else ''
    if not text and hasattr(control, 'placeholderText'):
        text = control.placeholderText()
    if not text and hasattr(control, 'currentText'):
        text = control.currentText()
    tip = control_help(text, key or control.objectName())
    # Existing curated explanations take precedence over fallback text.
    if not control.toolTip() or control.property('explanationGenerated') == control.toolTip() or control.toolTip().replace('&', '').replace('…', '').replace('...', '') == str(text).replace('&', '').replace('…', '').replace('...', ''):
        control.setToolTip(tip)
        control.setProperty('explanationGenerated', tip)
    elif 'choose or enter the value' not in tip and tip not in control.toolTip():
        control.setToolTip(control.toolTip() + '\n' + tip)
    if hasattr(control, 'setStatusTip') and not control.statusTip():
        control.setStatusTip(control.toolTip())
    return control


def describe_headers(table):
    if isinstance(table, QTableWidget):
        for column in range(table.columnCount()):
            item = table.horizontalHeaderItem(column)
            if item is not None and not item.toolTip():
                item.setToolTip(header_help(item.text()))
    elif isinstance(table, QTreeWidget):
        item = table.headerItem()
        for column in range(table.columnCount()):
            if not item.toolTip(column):
                item.setToolTip(column, header_help(item.text(column)))


def describe_cell(table, column, item):
    if item is not None and not item.toolTip():
        header = table.horizontalHeaderItem(column)
        item.setToolTip('<qt>' + escape((header_help(header.text()) + '\n' if header else '') + str(item.text())).replace('\n', '<br>') + '</qt>')


def add_intro(root, text=INTRO):
    layout = root.layout()
    if layout is not None and hasattr(layout, 'insertWidget'):
        if not any(isinstance(root_child, QLabel) and root_child.objectName() == 'moduleIntro'
                   for root_child in root.findChildren(QLabel, options=Qt.FindChildOption.FindDirectChildrenOnly)):
            layout.insertWidget(0, module_intro(text))


def explain_widget(root, introduction=False):
    if not isinstance(root, QWidget):
        return root
    if introduction:
        add_intro(root)
    widgets = [root] + root.findChildren(QWidget)
    for widget in widgets:
        if isinstance(widget, (QAbstractButton, QComboBox, QAbstractSpinBox, QLineEdit, QSlider)):
            describe_control(widget)
        if isinstance(widget, (QTableWidget, QTreeWidget)):
            describe_headers(widget)
        if isinstance(widget, (QTabWidget, QTabBar)):
            for index in range(widget.count()):
                if not widget.tabToolTip(index):
                    widget.setTabToolTip(index, tab_help(widget.tabText(index)))
                if isinstance(widget, QTabWidget):
                    add_intro(widget.widget(index), tab_help(widget.tabText(index)) + ' ' + INTRO)
        if isinstance(widget, QMenu):
            for action in widget.actions():
                if not action.isSeparator():
                    describe_control(action)
    return root
