"""Editor construction separated from worker/controller logic."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)
from core.table_ui import set_role
from .engine.model import DEFAULT_ICON
from .widgets import Preview

ICONS = [("Folder", DEFAULT_ICON), ("Documents", r"%SystemRoot%\system32\imageres.dll,-112"),
         ("Pictures", r"%SystemRoot%\system32\imageres.dll,-113"),
         ("Classic folder", r"%SystemRoot%\system32\shell32.dll,3")]


def button(text, tooltip, callback, layout):
    widget = QPushButton(text)
    widget.setToolTip(tooltip)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


def build_form(pane):
    page = QWidget()
    layout = QVBoxLayout(page)
    title = QLabel("Folder shortcut")
    set_role(title, "sectionTitle")
    layout.addWidget(title)
    fields = QFormLayout()
    pane.path = QLineEdit()
    pane.path.setPlaceholderText("Type or browse to a local folder")
    pane.path.setToolTip("The existing local folder to open. Network paths are not supported.")
    row = QHBoxLayout()
    row.addWidget(pane.path, 1)
    button("Browse…", "Choose an existing folder on this computer.", pane.browse, row)
    fields.addRow("Folder", row)
    pane.common = QComboBox()
    pane.common.addItem("Pick a common folder…", "")
    pane.common.setToolTip("Choose a Windows known folder, your user profile, or a detected OneDrive root.")
    pane.common.activated.connect(pane.pick_common)
    fields.addRow("Quick choice", pane.common)
    pane.validation = QLabel("Choose a folder to begin.")
    pane.validation.setWordWrap(True)
    pane.validation.setToolTip("Live folder validation; an unreadable path is reported as could not check.")
    fields.addRow("", pane.validation)
    pane.display_name = QLineEdit()
    pane.display_name.setToolTip("The name Explorer will show. It follows the folder name until you type here.")
    fields.addRow("Name", pane.display_name)
    pane.position = QComboBox()
    for name, value in [("First (above everything else)", 0), ("Above Home", 48), ("Below OneDrive", 80), ("Custom", None)]:
        pane.position.addItem(name, value)
    pane.position.setCurrentIndex(1)
    pane.position.setToolTip("Smaller position numbers appear higher. Home is 64, Gallery 65 and OneDrive 66.")
    pane.custom = QSpinBox()
    pane.custom.setRange(0, 2147483647)
    pane.custom.setValue(48)
    pane.custom.setEnabled(False)
    pane.custom.setToolTip("A custom SortOrderIndex. Smaller numbers appear higher in the navigation pane.")
    row = QHBoxLayout()
    row.addWidget(pane.position, 1)
    row.addWidget(pane.custom)
    fields.addRow("Position", row)
    pane.icons = QComboBox()
    for label, location in ICONS:
        pane.icons.addItem(label, location)
    pane.icons.setToolTip("Choose a real Windows folder icon for the shortcut.")
    row = QHBoxLayout()
    row.addWidget(pane.icons, 1)
    button("Choose from file…", "Choose an .ico, .dll or .exe file and its icon index.", pane.choose_icon, row)
    fields.addRow("Icon", row)
    layout.addLayout(fields)
    pane.cloud = QLabel("")
    pane.cloud.setWordWrap(True)
    pane.cloud.setToolTip("OneDrive folders use a hidden junction outside OneDrive to preserve your chosen display name.")
    set_role(pane.cloud, "statusInfo")
    layout.addWidget(pane.cloud)
    _build_preview(pane, layout)
    _build_details(pane, layout)
    _build_actions(pane, layout)
    layout.addStretch()
    return page


def _build_preview(pane, layout):
    group = QGroupBox("Live Explorer preview")
    inner = QVBoxLayout(group)
    pane.preview = Preview()
    inner.addWidget(pane.preview)
    pane.preview_note = QLabel("")
    pane.preview_note.setWordWrap(True)
    set_role(pane.preview_note, "muted")
    inner.addWidget(pane.preview_note)
    layout.addWidget(group)


def _build_details(pane, layout):
    pane.details_toggle = QCheckBox("What will be changed")
    pane.details_toggle.setToolTip("Expand the exact registry value list and hidden link plan before applying.")
    layout.addWidget(pane.details_toggle)
    pane.details = QLabel("")
    pane.details.setTextFormat(Qt.TextFormat.PlainText)
    pane.details.setWordWrap(True)
    pane.details.setVisible(False)
    pane.details_toggle.toggled.connect(pane.details.setVisible)
    layout.addWidget(pane.details)


def _build_actions(pane, layout):
    row = QHBoxLayout()
    pane.save_button = button("Add to navigation pane", "Create this shortcut; no files in the real folder are changed.", pane.save, row)
    set_role(pane.save_button, "accentButton")
    pane.remove_button = button("Remove", "Remove the selected shortcut after confirmation. The real folder stays untouched.", pane.remove, row)
    layout.addLayout(row)
    row = QHBoxLayout()
    pane.open_button = button("Open in Explorer", "Open the selected real folder through the signed-in user's shell.", pane.open_folder, row)
    pane.verify_button = button("Verify", "Check the name and target using a new PowerShell shell process.", pane.verify, row)
    button("Restart Explorer", "Close open Explorer windows and restart the shell unelevated, after confirmation.", pane.restart, row)
    layout.addLayout(row)
    row = QHBoxLayout()
    pane.finish_button = button("Finish interrupted change", "Retry the journal's remaining steps for the selected incomplete entry.", lambda: pane.repair(True), row)
    pane.rollback_button = button("Roll back interrupted change", "Restore the before-state recorded in the journal.", lambda: pane.repair(False), row)
    layout.addLayout(row)
