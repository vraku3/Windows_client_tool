"""Apply the module's curated explanations without replacing existing help."""
from PyQt6.QtWidgets import (QAbstractButton, QComboBox, QLabel, QLineEdit, QMenu, QTableWidget,
                             QTabWidget, QToolBar, QTreeWidget, QWidget)
from ui.module_intro import add_module_intro


def explain(root, controls, headers, fields=None):
    """Decorate existing controls; unknown controls are deliberately left visible to audits."""
    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        text = widget.text() if isinstance(widget, (QAbstractButton, QLabel)) else ''
        tip = controls.get(text.replace('&&', '&'))
        if not tip:
            tip = {'qt_tableview_cornerbutton': 'Select all rows in this table; this only changes the selection.',
                   'ScrollLeftButton': 'Scroll the tab bar left to reveal more tabs.',
                   'ScrollRightButton': 'Scroll the tab bar right to reveal more tabs.'}.get(widget.objectName())
        if not tip and isinstance(widget, QLineEdit) and isinstance(widget.parentWidget(), QComboBox):
            continue
        if not tip and isinstance(widget, QLineEdit) and widget.isClearButtonEnabled():
            tip = 'Filter the displayed rows by the text you enter; no files or settings are changed.'
        if not tip and isinstance(widget, QAbstractButton) and isinstance(widget.parentWidget(), QLineEdit):
            tip = 'Clear the search text and show rows hidden by this text filter; no files or settings are changed.'
        if tip and not widget.toolTip():
            widget.setToolTip(tip)
        if isinstance(widget, QTabWidget):
            for i in range(widget.count()):
                label = widget.tabText(i).replace('&&', '&')
                tip = controls.get(label)
                if tip and not widget.tabToolTip(i):
                    widget.setTabToolTip(i, tip)
        if isinstance(widget, (QTableWidget, QTreeWidget)):
            for i in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(i) if isinstance(widget, QTableWidget) else widget.headerItem()
                if item is None:
                    continue
                label = item.text() if isinstance(widget, QTableWidget) else item.text(i)
                tip = headers.get(label)
                old = item.toolTip() if isinstance(widget, QTableWidget) else item.toolTip(i)
                if tip and not old:
                    if isinstance(widget, QTableWidget):
                        item.setToolTip(tip)
                    else:
                        item.setToolTip(i, tip)
    # Only actions a person can see: those on toolbars and menus. root.findChildren(QAction)
    # also returned Qt's INTERNAL actions, and reading their text crashed the process
    # natively (access violation / "Negative size passed to PyUnicode_New") in 4 of 8
    # whole-module smoke runs, 2026-10-23; 10 of 10 passed without that walk.
    shown = {id(a): a for w in widgets if isinstance(w, (QToolBar, QMenu)) for a in w.actions()}
    for action in shown.values():
        if action.isSeparator():
            continue
        tip = controls.get(action.text().replace('&&', '&'))
        if not tip and type(action).__name__ == 'QWidgetAction':
            child = action.defaultWidget()
            tip = child.toolTip() if child is not None else None
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        elif action.toolTip() and action.toolTip() != action.text() and not action.statusTip():
            action.setStatusTip(action.toolTip())
    for owner in widgets:
        for name, tip in (fields or {}).items():
            widget = getattr(owner, name, None)
            if isinstance(widget, QWidget) and not widget.toolTip():
                widget.setToolTip(tip)


def intro(page, text):
    add_module_intro(page, text)


def explain_panes(root, controls, headers, fields, panes):
    """Explain each existing tab, including the overflow buttons supplied by Qt."""
    from PyQt6.QtWidgets import QSplitter
    explain(root, controls, headers, fields)
    tabs = ([root] if isinstance(root, QTabWidget) else []) + root.findChildren(QTabWidget)
    for tab in tabs:
        for i in range(tab.count()):
            name = tab.tabText(i).replace('&&', '&')
            text = panes.get(name)
            if text:
                tab.setTabToolTip(i, text)
                page = tab.widget(i)
                if isinstance(page, QSplitter):
                    page = page.widget(0)
                intro(page, text)
