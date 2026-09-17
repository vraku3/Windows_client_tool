"""Task Manager's Details tab.

A table of every process on the machine, forty columns available, refreshing
once a second. The engine underneath it (`procengine/`) reads all 275
processes in 2.6 ms, which is what makes a refresh this cheap.

Three things here are deliberate and worth not undoing:

- **Refresh runs on a Worker, not the UI thread.** The bulk syscall is fast,
  but the cold resolution of a newly started process is not free, and a burst
  of new processes would otherwise stutter the window.
- **The table updates rows in place.** A reset once a second makes the table
  unclickable -- see `details_model`.
- **The status line says how much of the machine it can actually see.**
  Unelevated, about half the processes refuse their details. A pane that
  quietly shows half a machine is worse than one that says so.
"""
import csv
import logging
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMenu, QPushButton,
                             QTableView, QVBoxLayout, QWidget)

from core.worker import Worker

from .details_model import DetailsModel, DetailsProxy
from .process_menu import ProcessMenu
from core.procengine.columns import COLUMNS, DEFAULT_KEYS, GROUPS, cell_text
from core.procengine.snapshot import SnapshotSource
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)

CONFIG_KEY = "dashboard.details_columns"
#: Where the user's column WIDTHS are kept, keyed per column. Widths are a
#: layout fact (the eye finds a column by where its edge is), so they
#: persist next to the shown-column choice, not inside it.
WIDTHS_KEY = "dashboard.details_widths"
#: Where the user's DRAG ORDER is kept, as a list of column keys in the
#: order they sit on screen. The header is movable, so the order someone
#: drags it into should survive a restart like the widths do.
ORDER_KEY = "dashboard.details_order"

#: Task Manager's own default. Fast enough to feel live, slow enough that
#: the numbers are readable rather than flickering.
REFRESH_MS = 1000


class DetailsTab(QWidget):
    """The process table. A QWidget, not a BaseModule, so per CLAUDE.md it
    owns its own worker list and exposes `cancel_all`."""

    snapshot_taken = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers: list = []
        self._source = SnapshotSource()
        self._app = None
        self._busy = False
        self._config = None
        self._setup_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    # ---- construction ---------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.error_banner = ErrorBanner()
        self.error_banner.hide()
        layout.addWidget(self.error_banner)

        top = QHBoxLayout()
        self.filter_box = QLineEdit(self)
        self.filter_box.setPlaceholderText(
            "Filter by name, PID, user, description or command line…")
        self.filter_box.setClearButtonEnabled(True)
        top.addWidget(self.filter_box, 1)

        self.columns_button = QPushButton("Columns…", self)
        self.columns_button.setToolTip(
            "Choose which of the forty columns to show.")
        self.columns_button.clicked.connect(self._show_column_menu)
        top.addWidget(self.columns_button)

        self.export_button = QPushButton("Export…", self)
        self.export_button.setToolTip(
            "Save the process list as CSV — the columns currently shown, "
            "in their current order, with the current filter applied.")
        self.export_button.clicked.connect(self.export_csv)
        top.addWidget(self.export_button)
        layout.addLayout(top)

        self.model = DetailsModel(self)
        self.proxy = DetailsProxy(self)
        self.proxy.setSourceModel(self.model)
        self.filter_box.textChanged.connect(self.proxy.set_needle)

        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_row_menu)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda _pos: self._show_column_menu())
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # A user-dragged width or reorder is a layout fact worth keeping.
        # Saved on the event so nothing needs a separate "apply" step; the
        # config's autosave debounce absorbs the drag's many signals.
        self._applying_layout = False
        header.sectionResized.connect(self._on_section_resized)
        header.sectionMoved.connect(self._on_section_moved)
        layout.addWidget(self.table, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)

        self.menu = ProcessMenu(self)
        # An action changes the machine, so read it again rather than waiting
        # up to a second for the next tick to show what happened.
        self.menu.changed.connect(self.refresh)

    # ---- lifecycle ------------------------------------------------------

    def set_app(self, app) -> None:
        self._app = app
        self._config = getattr(app, "config", None)
        if self._config is not None:
            stored = self._config.get(CONFIG_KEY, None)
            if stored:
                self.model.set_columns(stored)
        self.menu.set_app(app)
        self._resize_columns()
        self._apply_saved_layout()

    def _apply_saved_layout(self) -> None:
        """Restore the widths and order the user last left them in.

        Guarded: applying widths re-fires `sectionResized` for every
        column, and nothing should be re-saved because of a restore.
        """
        if self._config is None:
            return
        self._applying_layout = True
        try:
            widths = self._config.get(WIDTHS_KEY, {}) or {}
            header = self.table.horizontalHeader()
            for section, column in enumerate(self.model.columns()):
                width = widths.get(column.key)
                if isinstance(width, int) and width > 0:
                    header.resizeSection(section, width)

            order = self._config.get(ORDER_KEY, None)
            if isinstance(order, list) and order:
                keys = [column.key for column in self.model.columns()]
                self._apply_order(keys, order, header)
        finally:
            self._applying_layout = False

    def _apply_order(self, keys, order, header) -> None:
        """Reorder sections to match the saved `order`.

        Walks the saved order left to right; each column not already in its
        place is dragged to the current front position, and that slot is
        then taken so the next move targets the slot after it.
        """
        by_key = {key: index for index, key in enumerate(keys)}
        wanted = [key for key in order if key in by_key]
        wanted += [key for key in keys if key not in set(wanted)]
        for target, key in enumerate(wanted):
            section = by_key[key]
            current = header.visualIndex(section)
            if current != target:
                header.moveSection(current, target)

    def start(self) -> None:
        self.refresh()
        self.sort_by("cpu")
        self._timer.start(REFRESH_MS)

    def sort_by(self, key: str, descending: bool = True) -> None:
        """Order the table by a column, indicator and all.

        Through `sortByColumn` rather than `proxy.sort` so the header shows
        which column it is sorted by -- sorting the data while the arrow
        points somewhere else is worse than not sorting.

        The pane opens on CPU descending, which is the question someone
        opens a process list to answer.
        """
        for section, column in enumerate(self.model.columns()):
            if column.key == key:
                self.table.sortByColumn(
                    section,
                    Qt.SortOrder.DescendingOrder if descending
                    else Qt.SortOrder.AscendingOrder)
                return

    def stop(self) -> None:
        self._timer.stop()
        self.cancel_all()

    def cancel_all(self) -> None:
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()

    # ---- refreshing -----------------------------------------------------

    def refresh(self) -> None:
        """One reading, off the UI thread.

        `_busy` matters: a machine under load can take longer than the tick,
        and stacking readings would queue work faster than it drains.
        """
        if self._busy:
            return
        pool = getattr(self._app, "thread_pool", None) if self._app else None
        if pool is None:
            # No app yet (tests, or before on_start): read inline rather than
            # showing nothing.
            self._apply(self._source.read())
            return

        self._busy = True
        worker = Worker(lambda _worker: self._source.read())
        worker.signals.result.connect(self._apply)
        worker.signals.error.connect(self._failed)
        self._workers.append(worker)
        pool.start(worker)

    def _apply(self, snapshot) -> None:
        self._busy = False
        self.model.set_snapshot(snapshot)
        self._update_status(snapshot)
        self.snapshot_taken.emit(snapshot)

    def _failed(self, message) -> None:
        self._busy = False
        logger.error("Details refresh failed: %s", message)
        self.status.setText(f"Could not read the process list: {message}")

    def _update_status(self, snapshot) -> None:
        """Say what is on screen, and say what could not be read.

        The second half is the point. Unelevated this machine refuses the
        details of 133 of its 275 processes; a pane that renders those as
        blanks and says nothing is claiming they have no path.
        """
        total = len(snapshot.by_pid)
        showing = self.proxy.rowCount()
        parts = [f"{showing:,} of {total:,} processes"]
        if snapshot.refused:
            parts.append(
                f"{snapshot.refused:,} could not be read (run as "
                f"administrator to see them)")
        self.status.setText("   ·   ".join(parts))

    def export_csv(self, file_path: str = "") -> Optional[str]:
        """Save the process list as CSV and return the path, or None.

        What is exported is what is on screen: the columns currently shown,
        in their current order, over the rows the filter leaves and the
        sort orders. A file's worth of numbers that do not match the table
        the user is looking at would be a trap.
        """
        from PyQt6.QtWidgets import QFileDialog

        if not file_path:
            file_path, _filter = QFileDialog.getSaveFileName(
                self, "Export processes", "processes.csv",
                "CSV files (*.csv)")
        if not file_path:
            return None
        columns = self.model.columns()
        rows = 0
        # utf-8-sig so Excel opens a value like "—" or an accented name
        # without mojibake -- the same choice treesize's exporters make.
        with open(file_path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([column.title for column in columns])
            for row in range(self.proxy.rowCount()):
                source = self.proxy.mapToSource(self.proxy.index(row, 0))
                info = self.model.info(source.row())
                if info is None:
                    continue
                writer.writerow([cell_text(column, info)
                                 for column in columns])
                rows += 1
        self.status.setText(f"Exported {rows:,} processes to {file_path}")
        return file_path

    # ---- columns --------------------------------------------------------

    def _show_column_menu(self) -> None:
        menu = QMenu(self)
        shown = {column.key for column in self.model.columns()}
        for group in GROUPS:
            menu.addSection(group)
            for column in COLUMNS:
                if column.group != group:
                    continue
                action = QAction(column.title, menu)
                action.setCheckable(True)
                action.setChecked(column.key in shown)
                action.setToolTip(column.description)
                action.triggered.connect(
                    lambda checked, key=column.key: self._toggle(key, checked))
                menu.addAction(action)
        menu.addSeparator()
        reset = QAction("Reset to defaults", menu)
        reset.triggered.connect(self._reset_columns)
        menu.addAction(reset)
        menu.exec(self.columns_button.mapToGlobal(
            self.columns_button.rect().bottomLeft()))

    def _toggle(self, key: str, checked: bool) -> None:
        keys = [column.key for column in self.model.columns()]
        if checked and key not in keys:
            # Inserted in the canonical order rather than appended, so the
            # table does not gradually become the order things were clicked.
            order = [column.key for column in COLUMNS]
            keys.append(key)
            keys.sort(key=order.index)
        elif not checked and key in keys:
            keys.remove(key)
        self.model.set_columns(keys)
        self._save_columns()
        self._resize_columns()
        # A toggle must not undo the widths of the columns that were
        # already there; only the newly shown one lacks a saved width.
        self._apply_saved_layout()

    def _reset_columns(self) -> None:
        self.model.set_columns(list(DEFAULT_KEYS))
        self._save_columns()
        self._resize_columns()
        self._apply_saved_layout()

    def _save_columns(self) -> None:
        if self._config is None:
            return
        self._config.set(CONFIG_KEY,
                         [column.key for column in self.model.columns()])
        self._config.save()

    def _resize_columns(self) -> None:
        """Sized once, then left alone.

        NOT ResizeToContents: it measures the widest value in the whole
        model, which on a Command line column is a 900-character string --
        the mistake the Log Viewer's Package column already made.

        Guarded: these are programmatic resizes, and nothing should be
        saved because of one -- `sectionResized` fires for every column.
        """
        self._applying_layout = True
        try:
            header = self.table.horizontalHeader()
            for section, column in enumerate(self.model.columns()):
                header.resizeSection(section, _width_for(column.key))
        finally:
            self._applying_layout = False

    # ---- persisting the layout ------------------------------------------

    def _on_section_resized(self, *_args) -> None:
        if self._applying_layout:
            return
        if self._config is None:
            return
        header = self.table.horizontalHeader()
        widths = {column.key: header.sectionSize(section)
                  for section, column in enumerate(self.model.columns())}
        self._config.set(WIDTHS_KEY, widths)

    def _on_section_moved(self, *_args) -> None:
        if self._applying_layout:
            return
        if self._config is None:
            return
        header = self.table.horizontalHeader()
        columns = self.model.columns()
        # The order the columns sit on screen: sorted by each one's visual
        # position in the header.
        by_visual = sorted(range(len(columns)),
                           key=lambda section: header.visualIndex(section))
        self._config.set(ORDER_KEY,
                         [columns[section].key for section in by_visual])

    def _show_row_menu(self, position) -> None:
        pids = self.selected_pids()
        if not pids:
            return
        self.menu.show(pids, self.selected_info(),
                       self.table.viewport().mapToGlobal(position))

    # ---- selection ------------------------------------------------------

    def selected_pids(self) -> list:
        rows = self.table.selectionModel().selectedRows()
        pids = []
        for index in rows:
            source = self.proxy.mapToSource(index)
            pid = self.model.pid_at(source.row())
            if pid is not None:
                pids.append(pid)
        return pids

    def selected_info(self) -> Optional[object]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        source = self.proxy.mapToSource(rows[0])
        return self.model.info(source.row())


#: Sensible starting widths. A column of bytes needs less room than a command
#: line, and starting every column at the same width means every table starts
#: wrong.
_WIDTHS = {
    "name": 220, "pid": 70, "ppid": 80, "user": 160, "session": 80,
    "description": 240, "company": 180, "path": 340, "cmdline": 420,
    "architecture": 80, "elevated": 80, "integrity": 90, "start_time": 150,
    "cpu": 60, "cpu_time": 90, "kernel_time": 90, "user_time": 90,
    "cycles": 130, "base_priority": 90, "threads": 70,
    "memory": 100, "working_set": 110, "peak_working_set": 130,
    "commit": 100, "peak_commit": 120, "private_bytes": 110,
    "paged_pool": 90, "nonpaged_pool": 90, "virtual_size": 110,
    "peak_virtual_size": 130, "page_faults": 100, "hard_faults": 100,
    "disk_read": 100, "disk_write": 100, "disk_other": 100,
    "read_bytes": 110, "write_bytes": 110, "other_bytes": 110,
    "read_ops": 90, "write_ops": 90, "other_ops": 90,
    "handles": 80,
}


def _width_for(key: str) -> int:
    return _WIDTHS.get(key, 110)
