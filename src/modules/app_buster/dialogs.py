"""App Buster's dialogs: removal, locked files, the result report, Properties.

Wording follows O&O AppBuster's manual where it names a control ("Remove
apps", "Review all ›", "Close these programs and remove", "Retry skipped",
"Save log…").
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFileDialog,
                             QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton,
                             QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView)

from core.table_ui import set_role

from .engine import actions as act
from .engine import model as m
from .engine import views

PREVIEW = 4


class RemovalDialog(QDialog):
    """What will be removed, how far (scope), and a plain warning."""

    def __init__(self, records: Sequence[m.AppRecord], elevated: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remove apps")
        self.setMinimumWidth(520)
        self.records = list(records)
        lay = QVBoxLayout(self)
        heading = QLabel(self._title())
        set_role(heading, "heading")
        lay.addWidget(heading)
        self._list = QListWidget(self)
        self._list.addItems([f"{r.name}   [{r.type}]" for r in self.records])
        self._list.setVisible(len(self.records) <= PREVIEW)
        self._preview = QLabel(", ".join(r.name for r in self.records[:PREVIEW]) +
                               (f" and {len(self.records) - PREVIEW} more" if len(self.records) > PREVIEW else ""))
        self._preview.setWordWrap(True)
        self._preview.setVisible(len(self.records) > PREVIEW)
        lay.addWidget(self._preview)
        if len(self.records) > PREVIEW:
            review = QPushButton("Review all ›", self)
            review.setFlat(True)
            review.clicked.connect(lambda: (self._list.setVisible(True), review.hide(), self._preview.hide()))
            lay.addWidget(review, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._list)
        self.scope = act.SCOPE_USER
        if any(r.type == m.WINDOWS for r in self.records):
            lay.addWidget(self._scope_box(elevated))
        notes = []
        if any(r.type == m.DESKTOP for r in self.records):
            notes.append("Desktop apps ignore the scope: each one runs its own uninstaller, which may "
                         "show its own windows you must confirm. They run one at a time.")
        if any(r.type in (m.ORPHANED, m.DEFECT) for r in self.records):
            notes.append("Orphaned and defect entries have no working uninstaller; App Buster removes "
                         "their leftovers directly (registry entries are backed up to a .reg file first).")
        notes.append("Removal cannot be undone within App Buster. Windows apps can be reinstalled, and a "
                     "system restore point can restore the whole system, but there is no per-app undo.")
        for text in notes:
            label = QLabel(text)
            label.setWordWrap(True)
            set_role(label, "statusWarning" if text.startswith("Removal") else "statusInfo")
            lay.addWidget(label)
        buttons = QDialogButtonBox(self)
        go = buttons.addButton("Remove apps", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        go.setDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _title(self) -> str:
        return (f"Remove {self.records[0].name}?" if len(self.records) == 1
                else f"Remove {len(self.records)} apps?")

    def _scope_box(self, elevated: bool):
        from PyQt6.QtWidgets import QGroupBox
        box = QGroupBox("Removal scope (Windows apps)", self)
        inner = QVBoxLayout(box)
        group = QButtonGroup(self)
        choices = (
            (act.SCOPE_USER, "Current user", "Removes it for your account only. Least invasive.", True),
            (act.SCOPE_ALL, "All users", "Removes it for every account, and stops new accounts getting it.",
             elevated),
            (act.SCOPE_PC, "Entire PC — including files  (Most thorough)",
             "Every account, plus every account's app data.", elevated),
        )
        for key, label, tip, enabled in choices:
            radio = QRadioButton(label, box)
            radio.setToolTip(tip if enabled else tip + " Needs administrator -- restart the app elevated.")
            radio.setEnabled(enabled)
            radio.setChecked(key == act.SCOPE_USER)
            radio.toggled.connect(lambda on, k=key: on and setattr(self, "scope", k))
            group.addButton(radio)
            inner.addWidget(radio)
            hint = QLabel(tip if enabled else tip + " (needs administrator)")
            set_role(hint, "statusInfo")
            hint.setContentsMargins(22, 0, 0, 4)
            inner.addWidget(hint)
        return box


class LockedDialog(QDialog):
    """A running program holds this app's files. Three ways forward."""

    CLOSE, RESTART, SKIP = "close", "restart", "skip"

    def __init__(self, outcome: act.Outcome, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Files in use")
        self.choice = self.SKIP
        lay = QVBoxLayout(self)
        names = ", ".join(sorted({n for _p, n in outcome.blockers})) or "an unidentified program"
        text = QLabel(f"<b>{outcome.rec.name}</b> cannot be removed right now: {names} "
                      f"is using its files.")
        text.setWordWrap(True)
        lay.addWidget(text)
        warn = QLabel("Closing ends those programs without asking them to save. Save your work first.")
        warn.setWordWrap(True)
        set_role(warn, "statusWarning")
        lay.addWidget(warn)
        row = QHBoxLayout()
        for key, label in ((self.CLOSE, "Close these programs and remove"),
                           (self.RESTART, "Remove on next restart"), (self.SKIP, "Skip this app")):
            btn = QPushButton(label, self)
            btn.setEnabled(key != self.CLOSE or bool(outcome.blockers))
            btn.clicked.connect(lambda _=False, k=key: self._pick(k))
            row.addWidget(btn)
        lay.addLayout(row)

    def _pick(self, key: str) -> None:
        self.choice = key
        self.accept()


class ResultDialog(QDialog):
    """Removed, skipped (with reasons), deferred; Retry skipped and Save log…"""

    def __init__(self, outcomes: Sequence[act.Outcome], scope: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Removal report")
        self.setMinimumSize(560, 360)
        self.retry = False
        self._outcomes, self._scope = list(outcomes), scope
        lay = QVBoxLayout(self)
        head = QLabel(act.summary(outcomes))
        set_role(head, "heading")
        lay.addWidget(head)
        table = QTableWidget(0, 3, self)
        table.setHorizontalHeaderLabels(["App", "Result", "Reason"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        order = {act.REMOVED: 0, act.DEFERRED: 1}
        for o in sorted(outcomes, key=lambda o: order.get(o.state, 2)):
            r = table.rowCount()
            table.insertRow(r)
            label = {act.REMOVED: "Removed", act.DEFERRED: "After restart"}.get(o.state, "Skipped")
            reason = o.reason + (" -- held by " + ", ".join(n for _p, n in o.blockers) if o.blockers else "")
            for c, text in enumerate((o.rec.name, label, reason)):
                table.setItem(r, c, QTableWidgetItem(text))
        table.resizeColumnsToContents()
        lay.addWidget(table)
        if any(o.state == act.DEFERRED for o in outcomes):
            note = QLabel("Restart the PC (or sign out and in) to finish removing the deferred apps.")
            set_role(note, "statusWarning")
            lay.addWidget(note)
        row = QHBoxLayout()
        skipped = [o for o in outcomes if not o.ok]
        retry = QPushButton("Retry skipped", self)
        retry.setEnabled(bool(skipped))
        retry.clicked.connect(self._retry)
        save = QPushButton("Save log…", self)
        save.clicked.connect(self._save)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        for b in (retry, save):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

    def _retry(self) -> None:
        self.retry = True
        self.accept()

    def _save(self) -> None:
        path, _f = QFileDialog.getSaveFileName(self, "Save removal log", "AppBuster-removal.txt",
                                               "Text files (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(act.report_text(self._outcomes, self._scope))


class PropertiesDialog(QDialog):
    def __init__(self, rec: m.AppRecord, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Properties — {rec.name}")
        self.setMinimumSize(560, 420)
        lay = QVBoxLayout(self)
        rows = views.properties(rec)
        table = QTableWidget(len(rows), 2, self)
        table.horizontalHeader().setVisible(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(True)
        for r, (label, value) in enumerate(rows):
            table.setItem(r, 0, QTableWidgetItem(label))
            table.setItem(r, 1, QTableWidgetItem(value))
        table.resizeColumnToContents(0)
        table.resizeRowsToContents()
        lay.addWidget(table)
        row = QHBoxLayout()
        copy = QPushButton("Copy to clipboard", self)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(views.properties_text(rec)))
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)


class LogDialog(QDialog):
    """Output of an install / update / modify run."""

    def __init__(self, title: str, lines: List[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 320)
        lay = QVBoxLayout(self)
        box = QPlainTextEdit(self)
        box.setReadOnly(True)
        box.setPlainText("\n".join(lines))
        lay.addWidget(box)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)


def ask_restore_point(parent, elevated: bool) -> Optional[bool]:
    """Before the first change in a session. True = make one, False = do not,
    None = cancel the whole action."""
    from PyQt6.QtWidgets import QMessageBox
    if not elevated:
        return False
    box = QMessageBox(parent)
    box.setWindowTitle("Restore point")
    box.setText("Create a system restore point before the first change?")
    box.setInformativeText("It is the one step that makes a larger clean-up reversible as a whole.")
    yes = box.addButton("Create restore point", QMessageBox.ButtonRole.YesRole)
    no = box.addButton("Continue without", QMessageBox.ButtonRole.NoRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(yes)
    box.exec()
    clicked = box.clickedButton()
    if clicked is yes:
        return True
    if clicked is no:
        return False
    return None
