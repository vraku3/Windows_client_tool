"""Typed confirmation for the operations with nothing to undo them (spec 7.2).

Recycle and move get a plain confirmation. Permanent delete and secure erase
get this: the preflight summary, an optional caveat, and a box the user has to
type a word into before OK becomes clickable.

The point is not ceremony. It is that a muscle-memory Enter on a dialog nobody
read cannot reach an operation that destroys data — which is why Cancel is the
default button and why a dry run bypasses the typing entirely.
"""

from modules.treesize.help_widgets import (
    describe_control, explain_widget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout,
)


class TypedConfirmDialog(QDialog):
    def __init__(self, title: str, summary: str, phrase: str = "DELETE",
                 caveat: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self._phrase = phrase
        layout = QVBoxLayout(self)

        self.summary_label = QLabel(summary, self)
        self.summary_label.setWordWrap(True)
        # Selectable so a user can copy a path out of the summary before
        # deciding -- which is exactly what someone unsure of a path does.
        self.summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary_label)

        self.caveat_label = QLabel(caveat, self)
        self.caveat_label.setObjectName("confirmCaveat")
        self.caveat_label.setWordWrap(True)
        self.caveat_label.setVisible(bool(caveat))
        layout.addWidget(self.caveat_label)

        self.prompt = QLabel(f"Type {phrase} to confirm:", self)
        layout.addWidget(self.prompt)
        self.entry = QLineEdit(self)
        describe_control(self.entry, 'entry')
        layout.addWidget(self.entry)

        self.dry_run = QCheckBox(
            "Dry run — log what would happen, change nothing", self)
        describe_control(self.dry_run, 'dry_run')
        layout.addWidget(self.dry_run)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setDefault(False)
        ok.setAutoDefault(False)

        self.entry.textChanged.connect(self._retest)
        self.dry_run.toggled.connect(self._retest)
        self._retest()
        explain_widget(self)

    def is_satisfied(self) -> bool:
        # Case and stray spaces are forgiven: the gate exists to stop a
        # reflex, not to test typing accuracy.
        return (self.dry_run.isChecked()
                or self.entry.text().strip().casefold()
                == self._phrase.strip().casefold())

    def _retest(self) -> None:
        typing_needed = not self.dry_run.isChecked()
        self.entry.setEnabled(typing_needed)
        self.prompt.setEnabled(typing_needed)
        self.buttons.button(
            QDialogButtonBox.StandardButton.Ok).setEnabled(self.is_satisfied())
