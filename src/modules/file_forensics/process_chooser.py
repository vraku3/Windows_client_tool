"""A deliberate, single-holder selection with no destructive default."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)


class ProcessChooser(QDialog):
    def __init__(self, parent, path, choices, open_process=None):
        super().__init__(parent)
        self.choices = choices
        self.setWindowTitle('Choose a locking process')
        self.resize(1000, 380)
        layout = QVBoxLayout(self)
        label = QLabel('Processes observed holding ' + path + '\nChoose ONE process. Ending it requires a second confirmation.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QTableWidget(len(choices), 8)
        self.table.setHorizontalHeaderLabels(['Process', 'PID', 'Executable path', 'User', 'Started', 'Signature', 'Can end?', 'Reason'])
        self.table.setToolTip('All observed holders. Grey rows are critical, protected, or could not be safely identified. No process is selected by default.')
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for row, choice in enumerate(choices):
            for col, text in enumerate((choice.name, str(choice.pid), choice.path, choice.user,
                    choice.started, choice.signature, 'No' if choice.reason else 'Yes', choice.reason)):
                item = QTableWidgetItem(text)
                item.setToolTip(choice.reason or text)
                if choice.reason:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, item)
        layout.addWidget(self.table)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText('Choose process')
        self.ok.setToolTip('Choose this one process, then review the final end-process confirmation.')
        self.ok.setAutoDefault(False)
        self.ok.setEnabled(False)
        cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setToolTip('Close without ending any process.')
        cancel.setDefault(True)
        cancel.setFocus()
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.open_button = None
        if open_process is not None:
            self.open_button = QPushButton('Open in Process Explorer')
            self.open_button.setToolTip('Inspect the chosen PID in the Process Explorer pane without ending it.')
            self.open_button.setEnabled(False)
            self.open_button.setAutoDefault(False)
            self.open_button.clicked.connect(lambda: self._open(open_process))
            layout.addWidget(self.open_button)
        layout.addWidget(self.buttons)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.clearSelection()

    def selected(self):
        rows = self.table.selectionModel().selectedRows()
        choice = self.choices[rows[0].row()] if rows else None
        return choice if choice and not choice.reason else None

    def _selection_changed(self):
        chosen = self.selected() is not None
        self.ok.setEnabled(chosen)
        if self.open_button is not None:
            self.open_button.setEnabled(chosen)

    def _open(self, callback):
        choice = self.selected()
        if choice is not None:
            self.reject()
            callback(choice.pid)
