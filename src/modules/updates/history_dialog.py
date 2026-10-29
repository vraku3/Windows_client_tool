"""Searchable, date-filtered Windows Update history (dialect: dark theme)."""
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ui.table_style import apply_table_style

from .history_model import (detail_text, entries_to_csv, filter_entries, kb_of_title,
                            operation_label, paginate, result_label, uninstallable)

PAGE_SIZE = 100
RANGES = (("All entries", None), ("Last 7 days", 7), ("Last 30 days", 30), ("Last 90 days", 90))


class UpdateHistoryDialog(QDialog):
    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Windows Update history")
        self.resize(900, 560)
        self._entries = list(entries or [])
        self._page = 0
        self._rows = []
        self._page_rows = []
        self.uninstall_kb = ""
        self.uninstall_kbs = []

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search title, result or error detail…")
        self._search.setClearButtonEnabled(True)
        self._range = QComboBox()
        for label, _days in RANGES:
            self._range.addItem(label)
        self._count = QLabel()
        filters.addWidget(self._search, 1)
        filters.addWidget(self._range)
        filters.addWidget(self._count)
        layout.addLayout(filters)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Date", "Operation", "Update", "Result", "Detail"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        apply_table_style(self._table, date_columns=(0,))
        layout.addWidget(self._table, 1)

        pager = QHBoxLayout()
        self._prev = QPushButton("Previous")
        self._next = QPushButton("Next")
        self._uninstall_btn = QPushButton("Uninstall update…")
        self._uninstall_btn.setEnabled(False)
        self._uninstall_btn.setToolTip(
            "Remove the selected KB through Windows Update, after confirmation "
            "and a restore point. Only succeeded installations are offered.")
        self._page_lbl = QLabel()
        self._export_btn = QPushButton("Export CSV...")
        self._export_btn.setToolTip(
            "Write the filtered history to a CSV file, including decoded errors.")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        pager.addWidget(self._prev)
        pager.addWidget(self._next)
        pager.addWidget(self._page_lbl)
        pager.addStretch()
        pager.addWidget(self._export_btn)
        pager.addWidget(self._uninstall_btn)
        pager.addWidget(close)
        layout.addLayout(pager)

        self._search.textChanged.connect(self._reset)
        self._range.currentIndexChanged.connect(self._reset)
        self._prev.clicked.connect(lambda: self._go(-1))
        self._next.clicked.connect(lambda: self._go(1))
        self._uninstall_btn.clicked.connect(self._ask_uninstall)
        self._export_btn.clicked.connect(self._export_csv)
        self._table.itemSelectionChanged.connect(self._update_uninstall_button)
        self._render()

    def _days(self):
        return RANGES[self._range.currentIndex()][1]

    def _reset(self):
        self._page = 0
        self._render()

    def _go(self, delta):
        self._page += delta
        self._render()

    def _render(self):
        self._rows = filter_entries(self._entries, self._search.text(), self._days())
        rows, self._page, pages = paginate(self._rows, self._page, PAGE_SIZE)
        self._page_rows = rows
        self._table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            when = getattr(entry, "when", None)
            values = [
                when.astimezone().strftime("%Y-%m-%d %H:%M") if when else "—",
                operation_label(getattr(entry, "operation", 1)),
                str(getattr(entry, "title", "") or "—"),
                result_label(getattr(entry, "result_code", 0)),
                detail_text(entry) or "—",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 4:
                    item.setToolTip(value)
                self._table.setItem(row, col, item)
        self._count.setText(f"{len(self._rows)} of {len(self._entries)} entries")
        self._page_lbl.setText(f"Page {self._page + 1} of {pages}")
        self._prev.setEnabled(self._page > 0)
        self._next.setEnabled(self._page + 1 < pages)
        self._update_uninstall_button()

    def _export_csv(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export Windows Update history", "windows-update-history.csv",
            "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write(entries_to_csv(self._rows))
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", f"Could not write {path}:\n{exc}")
            return
        self._count.setText(f"Exported {len(self._rows)} entries to {path}")

    def _selected_entries(self):
        rows = sorted({index.row() for index in self._table.selectedIndexes()})
        return [self._page_rows[row] for row in rows
                if 0 <= row < len(self._page_rows)]

    def _update_uninstall_button(self) -> None:
        entries = self._selected_entries()
        self._uninstall_btn.setEnabled(any(uninstallable(entry) for entry in entries))

    def _ask_uninstall(self) -> None:
        entries = [entry for entry in self._selected_entries() if uninstallable(entry)]
        kbs = []
        for entry in entries:
            kb = kb_of_title(getattr(entry, "title", ""))
            if kb and kb not in kbs:
                kbs.append(kb)
        if not kbs:
            return
        shown = "\n".join(f"* {kb}" for kb in kbs[:10])
        if len(kbs) > 10:
            shown += f"\n* and {len(kbs) - 10} more"
        answer = QMessageBox.warning(
            self, "Uninstall updates",
            f"Remove {len(kbs)} update(s) from this PC?\n\n{shown}\n\n"
            "Windows may require a restart, and an update that is superseded or "
            "serviced by another channel cannot be removed. One System Restore "
            "point is created before the batch if that setting is on.\n\n"
            "Uninstall them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.uninstall_kbs = kbs
        self.uninstall_kb = kbs[0] if len(kbs) == 1 else ""
        self.accept()
