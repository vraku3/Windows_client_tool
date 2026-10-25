"""Explanations for Scripts panes. Visit visible menu/toolbar actions only."""
from PyQt6.QtWidgets import (QAbstractButton, QLineEdit, QMenu, QToolBar, QTabWidget, QWidget)

INTERNAL = {'qt_tableview_cornerbutton': 'Select all rows; no file or setting is changed.',
            'ScrollLeftButton': 'Scroll left to reveal more tabs.',
            'ScrollRightButton': 'Scroll right to reveal more tabs.'}
LIBRARY = {
    'Collect support bundle…': 'Collect diagnostic files into an archive you can review and share; choose what to include before collecting.',
    'New script…': 'Create a new script in your own scripts folder for editing.',
    'Required modules…': 'Inspect PowerShell module dependencies and choose whether to install missing ones.',
    'Open my scripts folder': 'Open Explorer at the folder containing your own scripts.',
    'Reload': 'Read built-in and personal scripts again and refresh the list.',
    'Change…': 'Choose where personal scripts are stored; existing files are not moved.',
    'Reset to default': 'Use the default personal scripts folder; existing files stay where they are.',
    'Run': 'Execute the selected script. Read its description and body first; scripts may change this PC.',
    'Stop': 'Request that the running script stop; changes already made are not undone.',
    'View script': 'Read the selected script body before deciding to run it.',
    'Copy output': 'Copy the displayed script output to the clipboard.',
    'Save output…': 'Save the current output to a text file you choose.',
    'Console colors…': 'Choose the output console appearance; script behavior is unchanged.',
    'Install required module…': 'Install the selected script’s missing PowerShell dependency after confirmation.',
    '×': 'Dismiss this message. No file or setting is changed.',
}


def explain_scripts(root, library=False):
    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        tip = INTERNAL.get(widget.objectName())
        if isinstance(widget, QAbstractButton):
            tip = tip or (LIBRARY.get(widget.text()) if library else LIBRARY.get(widget.text()) if widget.text() == '×' else None)
            if isinstance(widget.parentWidget(), QLineEdit):
                tip = 'Clear this text filter and show the full script list.'
        if library and isinstance(widget, QLineEdit) and widget.isClearButtonEnabled():
            tip = 'Filter the script list by name or description; no script is run.'
        if tip and not widget.toolTip():
            widget.setToolTip(tip)
        if isinstance(widget, (QToolBar, QMenu)):
            for action in widget.actions():
                if action.isSeparator():
                    continue
                if action.text().startswith('Edit in '):
                    action.setToolTip('Open the selected script in this editor; a built-in is copied to your personal folder first.')
                    action.setStatusTip(action.toolTip())
