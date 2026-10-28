from PyQt6.QtCore import QDate, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.blocklist import (add_exclusion, expired_exclusions, is_blocked, load_exclusions,
                            matching_offers, save_exclusions)
from core.semantic_colors import chrome, semantic
from ui.table_style import apply_table_style


class ExclusionDialog(QDialog):
    def __init__(self, entry=None, offers=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit exclusion" if entry else "Add exclusion")
        self.entry = dict(entry or {})
        self.offers = list(offers or [])
        layout = QFormLayout(self)
        self.pattern = QLineEdit(self.entry.get("pattern", ""))
        self.pattern.setToolTip("Package ID, KB number or title text; * matches any characters.")
        self.reason = QLineEdit(self.entry.get("reason", ""))
        self.reason.setToolTip("Why this update should be excluded.")
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        layout.addRow("Pattern", self.pattern)
        layout.addRow("", self.preview)
        self.pattern.textChanged.connect(self._update_preview)
        self._update_preview(self.pattern.text())
        layout.addRow("Reason", self.reason)
        self.dates = {}
        for key, label in (("review_by", "Review by"), ("expires", "Expires")):
            row = QHBoxLayout()
            enabled = QCheckBox(label)
            enabled.setToolTip(f"Enable an optional {label.lower()} date.")
            editor = QDateEdit()
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd")
            editor.setToolTip("Choose a date. Exclusions remain active through their expiry date.")
            value = QDate.fromString(self.entry.get(key) or "", "yyyy-MM-dd")
            editor.setDate(value if value.isValid() else QDate.currentDate())
            enabled.setChecked(value.isValid())
            editor.setEnabled(enabled.isChecked())
            enabled.toggled.connect(editor.setEnabled)
            row.addWidget(enabled)
            row.addWidget(editor)
            layout.addRow(row)
            self.dates[key] = (enabled, editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _update_preview(self, text: str) -> None:
        if not self.offers:
            self.preview.setText("No current offers to preview; scan App Updates or Windows Updates first.")
            self.preview.setStyleSheet(f"color: {chrome('text_muted')};")
            return
        if not text.strip() or text.strip().startswith("#"):
            self.preview.setText("")
            return
        matches = matching_offers(text, self.offers)
        if not matches:
            self.preview.setText("Matches none of the current offers.")
            self.preview.setStyleSheet(f"color: {chrome('text_muted')};")
            return
        names = ", ".join(name for name, _ in matches[:4])
        more = f" and {len(matches) - 4} more" if len(matches) > 4 else ""
        self.preview.setText(f"Would hide {len(matches)} current offer(s): {names}{more}.")
        self.preview.setStyleSheet(f"color: {semantic('warning')};")

    def accept(self):
        pattern = self.pattern.text().strip()
        if not pattern or pattern.startswith("#"):
            QMessageBox.warning(self, "Invalid pattern", "Enter a non-empty pattern that does not start with #.")
            return
        self.entry.update(pattern=pattern, reason=self.reason.text().strip())
        for key, (enabled, editor) in self.dates.items():
            self.entry[key] = editor.date().toString("yyyy-MM-dd") if enabled.isChecked() else None
        super().accept()


class ExclusionsEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = None
        self.offers = []
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Pattern", "Reason", "Origin", "Added", "Review By", "Expires"])
        for col, tip in enumerate(("Package ID, KB or title pattern; plain text matches substrings and * is a wildcard.",
                                   "Why this exclusion was created.", "Created automatically by WinClientTool or entered manually.",
                                   "Date this exclusion was created.", "Optional date to review whether this exclusion is still needed.",
                                   "Optional last day this exclusion blocks updates.")):
            self.table.horizontalHeaderItem(col).setToolTip(tip)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(180)
        apply_table_style(self.table, date_columns=(3, 4, 5))
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for text, callback, tip in (
            ("Add...", lambda: self.edit(), "Create an exclusion with a reason and optional review and expiry dates."),
            ("Edit...", lambda: self.edit(True), "Edit the selected exclusion, preserving its author and origin."),
            ("Remove", self.remove, "Remove the selected exclusion so matching offers can appear again."),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel()
        layout.addWidget(self.status)

    def set_offers(self, offers) -> None:
        """Current scan offers (name, id) used to preview a pattern's effect."""
        self.offers = list(offers or [])

    def load(self, app):
        self.app = app
        entries = load_exclusions(app)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            expired = bool(expired_exclusions([entry]))
            values = [entry["pattern"], entry["reason"], "(auto)" if entry["origin"] == "auto" else "manual",
                      entry["added"], entry["review_by"], entry["expires"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value or "")
                item.setToolTip(f"Added by: {entry['added_by'] or 'Unknown'}" + ("; expired" if expired else ""))
                if expired:
                    item.setForeground(QColor(chrome("text_muted")))
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                elif entry["origin"] == "auto":
                    item.setForeground(QColor(semantic("info")))
                self.table.setItem(row, col, item)
        self.status.setText(f"{len(entries)} exclusion(s); {len(expired_exclusions(entries))} expired.")

    def persist(self, entries):
        save_exclusions(self.app, entries)
        saved = self.app.config.save()
        self.load(self.app)
        if saved is False:
            self.status.setText("NOT saved to disk; exclusions apply to this session only. See the log.")
        self.changed.emit()

    def edit(self, selected=False):
        if self.app is None:
            return
        entries = load_exclusions(self.app)
        row = self.table.currentRow()
        if selected and not 0 <= row < len(entries):
            return
        dialog = ExclusionDialog(entries[row] if selected else None,
                                 offers=self.offers, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if any(e["pattern"].casefold() == dialog.entry["pattern"].casefold()
               for i, e in enumerate(entries) if not selected or i != row):
            QMessageBox.warning(self, "Duplicate pattern", "An exclusion already uses this pattern.")
            return
        if selected:
            entries[row] = dialog.entry
        else:
            add_exclusion(self.app, dialog.entry["pattern"], dialog.entry["reason"])
            entries = load_exclusions(self.app)
            entries[-1].update(dialog.entry)
        self.persist(entries)

    def remove(self):
        if self.app is None:
            return
        entries = load_exclusions(self.app)
        row = self.table.currentRow()
        if not 0 <= row < len(entries):
            return
        if QMessageBox.question(self, "Remove exclusion", f"Remove exclusion {entries[row]['pattern']}?",
                                defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        del entries[row]
        self.persist(entries)


class ExcludedOffers(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Excluded offers (0)", parent)
        self.setCheckable(True)
        self.setChecked(False)
        self.setToolTip("Expand to see offers suppressed by active exclusions in the latest scan.")
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Package name", "Matched pattern", "Reason"])
        for col, tip in enumerate(("Currently offered update suppressed by an active exclusion.",
                                   "The active exclusion pattern matching this offer.", "Why the matching exclusion was created.")):
            self.table.horizontalHeaderItem(col).setToolTip(tip)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMaximumHeight(160)
        apply_table_style(self.table)
        layout.addWidget(self.table)
        self.table.hide()
        self.toggled.connect(self.table.setVisible)

    def set_offers(self, offers, entries):
        rows = [(name, e["pattern"], e["reason"]) for name, id_ in offers
                for e in entries if is_blocked(name, id_, [e])]
        self.setTitle(f"Excluded offers ({len(set(name for name, _, _ in rows))})")
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
