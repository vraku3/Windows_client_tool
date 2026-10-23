"""Editor for the highlight rules.

Order matters and is visible: `matching_rule` takes the FIRST rule that
matches, so moving a rule up changes what a row looks like.
"""

from modules.log_viewer.help_widgets import (
    describe_control, describe_headers, describe_cell, explain_widget,
)
import re

from PyQt6.QtWidgets import (QAbstractItemView, QColorDialog, QDialog,
                             QDialogButtonBox, QHBoxLayout, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt

from core.table_ui import centered_item, center_header
from .highlight import HighlightRule

_COLUMNS = ("Pattern", "Colour", "Regex", "On")


class HighlightDialog(QDialog):
    def __init__(self, rules, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Highlight rules")
        self.resize(560, 320)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(_COLUMNS), self)
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        describe_headers(self.table)
        center_header(self.table)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        add = QPushButton("Add", self)
        describe_control(add, 'add')
        add.clicked.connect(self._add_blank)
        buttons.addWidget(add)
        remove = QPushButton("Remove", self)
        describe_control(remove, 'remove')
        remove.clicked.connect(self._remove_selected)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        for rule in rules or ():
            self.add_rule(rule)
        explain_widget(self)

    def add_rule(self, rule: HighlightRule) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, centered_item(rule.pattern))
        describe_cell(self.table, 0, self.table.item(row, 0))
        colour = QTableWidgetItem(rule.colour)
        colour.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        colour.setBackground(QColor(rule.colour))
        # Colour is chosen through QColorDialog (see _add_blank), never
        # typed. QTableWidgetItem is editable by default and the table uses
        # default edit triggers, so a double-click here would otherwise let
        # someone type "red" -- which is not "#" + six hex digits and raises
        # out of a reimplemented Qt virtual when the row is next painted.
        colour.setFlags(colour.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 1, colour)
        describe_cell(self.table, 1, self.table.item(row, 1))
        for column, value in ((2, rule.regex), (3, rule.enabled)):
            item = QTableWidgetItem()
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if value
                               else Qt.CheckState.Unchecked)
            self.table.setItem(row, column, item)
            describe_cell(self.table, column, self.table.item(row, column))

    def _add_blank(self) -> None:
        colour = QColorDialog.getColor(QColor("#00ff00"), self)
        if colour.isValid():
            self.add_rule(HighlightRule("", colour.name()))

    def _remove_selected(self) -> None:
        for index in sorted(
                {i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(index)

    def rules(self) -> list:
        out = []
        for row in range(self.table.rowCount()):
            out.append(HighlightRule(
                pattern=self.table.item(row, 0).text(),
                colour=self.table.item(row, 1).text(),
                regex=self.table.item(row, 2).checkState()
                == Qt.CheckState.Checked,
                enabled=self.table.item(row, 3).checkState()
                == Qt.CheckState.Checked,
            ))
        return out

    def invalid_rows(self) -> list:
        """Rows whose pattern will not compile. Flagged, never refused --
        half a regex is a work in progress."""
        bad = []
        for row, rule in enumerate(self.rules()):
            if not rule.regex:
                continue
            try:
                re.compile(rule.pattern)
            except re.error:
                bad.append(row)
        return bad
