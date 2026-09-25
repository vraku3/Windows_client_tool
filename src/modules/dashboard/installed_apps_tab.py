"""Installed Apps: what is on the machine, when it arrived, and how big it is."""
import logging
from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMenu, QPushButton, QTableWidget,
                             QVBoxLayout)

from core.events import NAV_REQUEST_MODULE, NavRequestData

from . import installed_apps as ia
from .tab_base import DashModule, DashTab, fmt_size, make_chips, numeric_item, set_chip_counts

logger = logging.getLogger(__name__)

HEADERS = ("Name", "Version", "Publisher", "Installed", "Size", "Type")


class InstalledAppsTab(DashTab):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: List = []
        self._filter = "all"
        self._loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Filter by name, publisher or version…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._repopulate)
        top.addWidget(self._search, 1)
        refresh = QPushButton("Refresh", self)
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        manage = QPushButton("Uninstall / manage…", self)
        manage.setToolTip("Opens Software Inventory, where apps can be uninstalled")
        manage.clicked.connect(self._open_inventory)
        top.addWidget(manage)
        layout.addLayout(top)
        chips, self._chips = make_chips(self, ia.FILTERS, self._set_filter)
        layout.addLayout(chips)

        self._table = QTableWidget(0, len(HEADERS), self)
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setWordWrap(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_menu)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((320, 120, 220, 100, 90, 70)):
            header.resizeSection(column, width)
        header.setStretchLastSection(True)
        self._sort = (0, Qt.SortOrder.AscendingOrder)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._sort_by)
        layout.addWidget(self._table, 1)
        self.status = QLabel("", self)
        layout.addWidget(self.status)

    def start(self) -> None:
        if not self._loaded:
            self.refresh()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.status.setText("Reading installed software…")
        from modules.software_inventory.software_module import fetch_software
        self.run(lambda _w: fetch_software(), self._apply)

    def _apply(self, entries) -> None:
        self._busy = False
        self._loaded = True
        self._entries = list(entries)
        set_chip_counts(self._chips, ia.counts(self._entries))
        self._repopulate()

    def _default_error(self, message) -> None:
        super()._default_error(message)
        self.status.setText(f"Could not read installed software: {message}")

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._repopulate()

    def _sort_by(self, column: int) -> None:
        asc = Qt.SortOrder.AscendingOrder
        order = asc if (self._sort[0] != column or self._sort[1] != asc) else Qt.SortOrder.DescendingOrder
        # first click on a fresh column: ascending for text, descending for numbers
        if self._sort[0] != column and column in (3, 4):
            order = Qt.SortOrder.DescendingOrder
        self._sort = (column, order)
        self._repopulate()

    def _key(self, entry, column):
        if column == 4:
            return ia.size_bytes(entry.size_mb) or -1
        if column == 3:
            when = ia.installed_on(entry)
            return when.toordinal() if when else 0
        return (getattr(entry, ("name", "version", "publisher", "", "", "type_")[column]) or "").lower()

    def _repopulate(self, *_a) -> None:
        shown = ia.visible(self._entries, self._filter, self._search.text())
        column, order = self._sort
        shown.sort(key=lambda e: self._key(e, column),
                   reverse=order == Qt.SortOrder.DescendingOrder)
        table = self._table
        table.setRowCount(len(shown))
        for row, e in enumerate(shown):
            size = ia.size_bytes(e.size_mb)
            cells = (e.name, e.version, e.publisher, e.install_date or "",
                     fmt_size(size) if size is not None else "", e.type_)
            for col, text in enumerate(cells):
                table.setItem(row, col, numeric_item(text, text))
            table.item(row, 0).setData(Qt.ItemDataRole.UserRole, e)
            table.item(row, 0).setToolTip(e.uninstall_string or "")
        table.horizontalHeader().setSortIndicator(*self._sort)
        total, unknown = ia.total_size(shown)
        self.status.setText(
            f"{len(shown):,} of {len(self._entries):,} apps   ·   {fmt_size(total)} known"
            + (f"   ·   {unknown:,} with no size reported" if unknown else ""))

    def _entry_at(self, row):
        item = self._table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_menu(self, pos) -> None:
        entry = self._entry_at(self._table.rowAt(pos.y()))
        if entry is None:
            return
        menu = QMenu(self)
        menu.addAction("Copy name and version").triggered.connect(
            lambda: QApplication.clipboard().setText(f"{entry.name} {entry.version}".strip()))
        uninstall = menu.addAction("Copy uninstall command")
        uninstall.setEnabled(bool(entry.uninstall_string))
        uninstall.triggered.connect(
            lambda: QApplication.clipboard().setText(entry.uninstall_string))
        menu.addSeparator()
        menu.addAction("Open Software Inventory").triggered.connect(self._open_inventory)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _open_inventory(self) -> None:
        if self._app is not None:
            self._app.event_bus.publish(
                NAV_REQUEST_MODULE, NavRequestData(module_name="Software Inventory"))


class InstalledAppsModule(DashModule):
    name = "Installed Apps"
    icon = "📦"
    description = "Installed software, install dates and sizes"
    tab_class = InstalledAppsTab
