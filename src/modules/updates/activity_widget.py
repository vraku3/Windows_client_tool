import logging

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.semantic_colors import semantic
from .activity_ledger import load_activity

logger = logging.getLogger(__name__)


class ActivityWidget(QGroupBox):
    def __init__(self, parent=None, *, package=False):
        super().__init__("Package activity" if package else "Activity", parent)
        self.app = None
        self._package = package
        self._package_id = None
        self._entries = []
        layout = QVBoxLayout(self)
        if not package:
            filters = QHBoxLayout()
            self._search = QLineEdit()
            self._search.setPlaceholderText("Search package name or ID…")
            self._outcome = QComboBox()
            self._outcome.addItems(["All outcomes", "Success", "Failure", "Needs attention"])
            refresh = QPushButton("Refresh activity")
            filters.addWidget(self._search, 1)
            filters.addWidget(self._outcome)
            filters.addWidget(refresh)
            layout.addLayout(filters)
            self._search.textChanged.connect(self._render)
            self._outcome.currentIndexChanged.connect(self._render)
            refresh.clicked.connect(self.refresh)
        headers = ["Date", "Version change", "Outcome", "Context"]
        if not package:
            headers = ["Date", "Package", "Source", "Version change", "Outcome", "Context"]
        self._table = QTableWidget(0, len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)
        self._status = QLabel("Select a package to view activity." if package else "No package activity recorded.")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def set_app(self, app):
        self.app = app
        self.refresh()

    def set_package(self, package_id):
        self._package_id = package_id
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        self._entries = []
        if self.app is None or (self._package and not self._package_id):
            self._render()
            return
        try:
            self._entries = load_activity(self.app.app_data_dir, self._package_id if self._package else None,
                                          limit=10 if self._package else None)
        except OSError as exc:
            logger.warning("Could not load package activity", exc_info=True)
            self._render()
            self._status.setText(f"Could not load package activity: {exc}")
            return
        self._render()

    def _render(self, *_args):
        entries = self._entries
        if not self._package:
            query = self._search.text().casefold().strip()
            selected = self._outcome.currentText()
            entries = [entry for entry in entries
                       if query in f"{entry.get('name', '')} {entry.get('id', '')}".casefold()
                       and (selected == "All outcomes"
                            or selected == "Success" and entry.get("outcome") == "UPDATED"
                            or selected == "Failure" and entry.get("outcome") in ("FAILED", "BLOCKED")
                            or selected == "Needs attention" and entry.get("outcome") in
                            ("UPDATED_UNVERIFIED", "NEEDS_USER", "DIFF_TECH"))][:20]
        self._table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            outcome = str(entry.get("outcome", ""))
            change = f"{entry.get('version_before') or 'Unknown'} → {entry.get('version_after') or 'Unknown'}"
            values = [str(entry.get("ts", "")).replace("T", " "), change, outcome, entry.get("context", "")]
            if not self._package:
                values = [values[0], entry.get("name") or entry["id"], entry.get("source", ""), *values[1:]]
            meaning = ("success" if outcome == "UPDATED" else "error" if outcome in ("FAILED", "BLOCKED")
                       else "warning" if outcome in ("UPDATED_UNVERIFIED", "NEEDS_USER", "DIFF_TECH") else "info")
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(f"{entry['id']}\nOffered: {entry.get('version_offered') or 'Unknown'}\n{entry.get('detail', '')}")
                if column == (2 if self._package else 4):
                    item.setForeground(QColor(semantic(meaning)))
                self._table.setItem(row, column, item)
        if self._package and not self._package_id:
            text = "Select a package to view activity."
        else:
            text = f"Latest {len(entries)} activities." if entries else "No matching package activity recorded."
        self._status.setText(text)
