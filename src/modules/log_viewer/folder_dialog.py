r"""Choosing which of a folder tree's logs to open.

The flat scan is the default because pointing at a parent directory should
not open everything beneath it. This is the deliberate version, and it shows
what it found before opening any of it.

Measured on the real `C:/Windows/Logs`: 90 logs, 106 MB, twelve subfolders,
85 of them under 1 MB and one of them 84.5 MB. So the list is ticked by size
rather than wholesale -- the big archive is offered, not assumed.
"""

from modules.log_viewer.help_widgets import (
    describe_control, explain_widget,
)
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QListWidget,
    QListWidgetItem, QVBoxLayout,
)


def _size(count: int) -> str:
    step = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if step < 1024:
            return f"{step:.0f} {unit}"
        step /= 1024
    return f"{step:.1f} TB"


class FolderPickDialog(QDialog):
    """Lists what the scan found, ticked by size, and returns the choice."""

    def __init__(self, folder: str, paths, ticked, capped: bool,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open logs from a folder")
        self._folder = folder
        self._paths = list(paths)

        layout = QVBoxLayout(self)
        self._note = QLabel(self._build_note(capped), self)
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        self._list = QListWidget(self)
        for path in self._paths:
            item = QListWidgetItem(self._label(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if path in ticked
                               else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        layout.addWidget(self._list)

        self._all = QCheckBox("Tick everything", self)
        describe_control(self._all, '_all')
        self._all.toggled.connect(self.select_all)
        layout.addWidget(self._all)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        explain_widget(self)

    # ---- what it says ---------------------------------------------------

    def _build_note(self, capped: bool) -> str:
        note = (f"{len(self._paths)} log(s) under {self._folder}. "
                "Large ones are listed but not ticked.")
        if capped:
            # Saying so matters: a silently truncated list reads as "this is
            # everything there is".
            note += " The scan stopped at its limit, so there may be more."
        return note

    def note(self) -> str:
        return self._note.text()

    def _label(self, path: str) -> str:
        try:
            shown = os.path.relpath(path, self._folder)
        except ValueError:
            shown = path
        try:
            return f"{shown}   ({_size(os.path.getsize(path))})"
        except OSError:
            return shown

    def label_for(self, row: int) -> str:
        return self._list.item(row).text()

    def count(self) -> int:
        return self._list.count()

    # ---- the choice -----------------------------------------------------

    def select_all(self, ticked: bool) -> None:
        state = Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked
        for row in range(self._list.count()):
            self._list.item(row).setCheckState(state)

    def chosen(self) -> list:
        return [self._paths[row] for row in range(self._list.count())
                if self._list.item(row).checkState() == Qt.CheckState.Checked]
