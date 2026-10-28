"""Browse the shared maintenance timeline."""
import json
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

from .tab_base import DashModule, DashTab

SOURCES = ((None, "All", "◷"), ("updates", "Updates", "↻"),
           ("game_mode", "Game Mode", "▶"), ("cleanup", "Cleanup", "⌫"),
           ("optimise", "Optimise", "⚙"), ("security", "Security", "◆"),
           ("quick_fix", "Quick Fix", "✚"), ("system", "System", "▣"))


class TimelineTab(DashTab):
    REFRESH_MS = 15000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events = []
        self._source = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Maintenance timeline"))
        layout.addWidget(QLabel("Changes and recovery attempts across modules, newest first."))
        chips = QHBoxLayout()
        self._sources = QButtonGroup(self)
        self._sources.setExclusive(True)
        for source, label, icon in SOURCES:
            button = QPushButton(f"{icon} {label}")
            button.setCheckable(True)
            button.setChecked(source is None)
            self._sources.addButton(button)
            button.clicked.connect(lambda checked, value=source: self._select_source(value))
            chips.addWidget(button)
        layout.addLayout(chips)
        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search summaries…")
        self._search.textChanged.connect(self._render)
        filters.addWidget(self._search, 1)
        self._dates = QComboBox()
        for label, days in (("Last 24 hours", 1), ("Last 7 days", 7), ("Last 30 days", 30), ("All", None)):
            self._dates.addItem(label, days)
        self._dates.currentIndexChanged.connect(self._render)
        filters.addWidget(self._dates)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        filters.addWidget(refresh)
        layout.addLayout(filters)
        self._status = QLabel("No events yet.")
        layout.addWidget(self._status)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["When", "Source", "Summary"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._show_detail)
        layout.addWidget(self._table, 3)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select an event to view its details.")
        layout.addWidget(self._detail, 1)

    def refresh(self):
        if self._app is None or self._busy:
            return
        self._busy = True
        folder = self._app.app_data_dir
        from core.timeline import load_timeline
        self.run(lambda worker: load_timeline(folder, limit=5500), self._received,
                 on_error=self._failed)

    def _received(self, events):
        self._busy = False
        self._events = events
        self._render()

    def _failed(self, message):
        self._default_error(message)
        self._status.setText(f"Could not read timeline: {message}")

    def _select_source(self, source):
        self._source = source
        self._render()

    def _render(self, *_):
        days = self._dates.currentData()
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).timestamp() if days else None
        query = self._search.text().casefold()
        self._visible = [e for e in self._events
                         if (self._source is None or e["source"] == self._source)
                         and query in e["summary"].casefold()
                         and (cutoff is None or datetime.fromisoformat(e["ts"]).timestamp() >= cutoff)]
        self._table.setRowCount(0)
        self._detail.clear()
        labels = {source: f"{icon} {label}" for source, label, icon in SOURCES}
        for row, event in enumerate(self._visible):
            self._table.insertRow(row)
            when = datetime.fromisoformat(event["ts"]).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            for column, text in enumerate((when, labels.get(event["source"], event["source"]), event["summary"])):
                item = QTableWidgetItem(text)
                item.setToolTip(f"{event['event']} · {event['severity']}\n{event['ts']}")
                color = {"warning": "#f1c40f", "error": "#ff6b6b"}.get(event["severity"])
                if color:
                    item.setForeground(QColor(color))
                self._table.setItem(row, column, item)
        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(1)
        self._status.setText(f"{len(self._visible)} event(s)" if self._visible else "No events match these filters.")

    def _show_detail(self):
        row = self._table.currentRow()
        if 0 <= row < len(getattr(self, "_visible", [])):
            self._detail.setPlainText(json.dumps(self._visible[row], indent=2, ensure_ascii=False))


class TimelineModule(DashModule):
    name = "Timeline"
    icon = "◷"
    description = "Maintenance, changes, recovery attempts and reboots"
    tab_class = TimelineTab
