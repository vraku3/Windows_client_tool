"""Task Manager's Processes tab.

The readable one. Where Details lists 284 rows, this shows three groups and
rolls each app up to a single line -- Chrome's 21 processes become "Google
Chrome, 2.2 GB", which is a thing someone can actually decide about.

The heat tint is not decoration. Task Manager shades the value cells so the
eye finds the expensive row without reading the numbers, and the shade is
scaled against the busiest row currently on screen rather than against a
fixed ceiling -- on an idle machine the worst offender should still stand
out.
"""
import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPushButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from core.worker import Worker

from core.procengine.columns import fmt_bytes, fmt_percent, fmt_rate
from core.procengine.grouping import group_processes, totals
from core.procengine.snapshot import SnapshotSource
from ui.error_banner import ErrorBanner
from .process_menu import ProcessMenu

logger = logging.getLogger(__name__)

REFRESH_MS = 1000

COLUMNS = ("Name", "CPU", "Memory", "Disk", "PID")
CPU, MEMORY, DISK = 1, 2, 3

#: Roles carrying the sortable value and the pid behind a row.
VALUE_ROLE = Qt.ItemDataRole.UserRole + 1
PID_ROLE = Qt.ItemDataRole.UserRole + 2


class ProcessesTab(QWidget):
    """Grouped process view. Owns its workers, per CLAUDE.md."""

    snapshot_taken = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers: list = []
        self._source = SnapshotSource()
        self._app = None
        self._busy = False
        #: Which app rows the user had opened, so a refresh does not fold
        #: the tree shut once a second.
        self._expanded: set = set()
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.error_banner = ErrorBanner()
        self.error_banner.hide()
        layout.addWidget(self.error_banner)

        top = QHBoxLayout()
        self.filter_box = QLineEdit(self)
        self.filter_box.setPlaceholderText("Filter processes…")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self._rebuild)
        top.addWidget(self.filter_box, 1)

        self.end_button = QPushButton("End task", self)
        self.end_button.setEnabled(False)
        self.end_button.clicked.connect(self._end_selected)
        top.addWidget(self.end_button)
        layout.addLayout(top)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(False)
        self.tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemExpanded.connect(
            lambda item: self._remember(item, True))
        self.tree.itemCollapsed.connect(
            lambda item: self._remember(item, False))
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for section in range(1, len(COLUMNS)):
            header.setSectionResizeMode(
                section, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(section, 100)
        layout.addWidget(self.tree, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)

        self.menu = ProcessMenu(self)
        self.menu.changed.connect(self.refresh)
        self._snapshot = None

    # ---- lifecycle ------------------------------------------------------

    def set_app(self, app) -> None:
        self._app = app
        self.menu.set_app(app)

    def start(self) -> None:
        self.refresh()
        self._timer.start(REFRESH_MS)

    def stop(self) -> None:
        self._timer.stop()
        self.cancel_all()

    def cancel_all(self) -> None:
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()

    # ---- reading --------------------------------------------------------

    def refresh(self) -> None:
        if self._busy:
            return
        pool = getattr(self._app, "thread_pool", None) if self._app else None
        if pool is None:
            self._apply(self._read())
            return
        self._busy = True
        worker = Worker(lambda _worker: self._read())
        worker.signals.result.connect(self._apply)
        worker.signals.error.connect(self._failed)
        self._workers.append(worker)
        pool.start(worker)

    def _read(self):
        snapshot = self._source.read()
        # Grouped on the worker too: EnumWindows plus the rollup is the
        # expensive half, and doing it on the UI thread would stutter.
        return snapshot, group_processes(snapshot)

    def _apply(self, result) -> None:
        self._busy = False
        snapshot, groups = result
        self._snapshot = snapshot
        self._groups = groups
        self._rebuild()
        self.snapshot_taken.emit(snapshot)

    def _failed(self, message) -> None:
        self._busy = False
        logger.error("Processes refresh failed: %s", message)
        self.status.setText(f"Could not read the process list: {message}")

    # ---- building the tree ----------------------------------------------

    def _rebuild(self) -> None:
        if getattr(self, "_groups", None) is None:
            return
        needle = self.filter_box.text().strip().lower()
        selected = self._selected_pids()

        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        shown = 0
        peak = self._peaks()
        for group in self._groups:
            rows = self._rows_for(group, needle)
            if not rows:
                continue
            header = QTreeWidgetItem([f"{group.name} ({len(rows)})"])
            header.setFirstColumnSpanned(True)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(header)
            header.setExpanded(True)
            for row in rows:
                shown += self._add_row(header, row, peak)
        self.tree.setUpdatesEnabled(True)

        self._restore_selection(selected)
        self._update_status(shown)

    def _rows_for(self, group, needle: str) -> List:
        if not needle:
            return group.rows
        kept = []
        for row in group.rows:
            if _matches(row, needle):
                kept.append(row)
        return kept

    def _add_row(self, parent, row, peak) -> int:
        """One row, which is either an app (with children) or a process."""
        members = getattr(row, "members", None)
        if members is None:
            self._process_item(parent, row, peak)
            return 1

        summed = totals(members)
        item = QTreeWidgetItem(parent, [row.title])
        item.setData(0, PID_ROLE, row.pid)
        self._set_value(item, CPU, summed["cpu"], fmt_percent, peak["cpu"])
        self._set_value(item, MEMORY, summed["memory"], fmt_bytes,
                        peak["memory"])
        self._set_value(item, DISK, summed["disk"], fmt_rate, peak["disk"])
        item.setText(4, str(row.pid))
        if len(members) > 1:
            for member in sorted(members, key=lambda info: info.pid):
                self._process_item(item, member, peak)
            item.setExpanded(row.pid in self._expanded)
        return len(members)

    def _process_item(self, parent, info, peak) -> None:
        label = info.details.description or info.name
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, PID_ROLE, info.pid)
        item.setToolTip(0, info.details.path or info.name)
        self._set_value(item, CPU, info.rates.cpu_percent, fmt_percent,
                        peak["cpu"])
        self._set_value(item, MEMORY, info.raw.working_set_private,
                        fmt_bytes, peak["memory"])
        disk = _disk_of(info)
        self._set_value(item, DISK, disk, fmt_rate, peak["disk"])
        item.setText(4, str(info.pid))

    def _set_value(self, item, column: int, value, render, ceiling) -> None:
        item.setText(column, render(value))
        item.setTextAlignment(column, int(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter))
        item.setData(column, VALUE_ROLE, value)
        tint = _heat(value, ceiling)
        if tint is not None:
            item.setBackground(column, QBrush(tint))

    def _peaks(self) -> Dict[str, float]:
        """The busiest row on screen, per column.

        Scaled against what is actually here rather than a fixed ceiling: on
        an idle machine every cell would otherwise be the same flat colour
        and the tint would tell nobody anything.
        """
        peak = {"cpu": 1.0, "memory": 1.0, "disk": 1.0}
        if self._snapshot is None:
            return peak
        for info in self._snapshot.by_pid.values():
            if info.rates.cpu_percent:
                peak["cpu"] = max(peak["cpu"], info.rates.cpu_percent)
            peak["memory"] = max(peak["memory"], info.raw.working_set_private)
            peak["disk"] = max(peak["disk"], _disk_of(info) or 0)
        return peak

    # ---- selection and actions ------------------------------------------

    def _remember(self, item, opened: bool) -> None:
        pid = item.data(0, PID_ROLE)
        if pid is None:
            return
        if opened:
            self._expanded.add(pid)
        else:
            self._expanded.discard(pid)

    def _selected_pids(self) -> List[int]:
        pids = []
        for item in self.tree.selectedItems():
            pid = item.data(0, PID_ROLE)
            if pid is not None:
                pids.append(pid)
        return pids

    def _restore_selection(self, pids) -> None:
        """A rebuild once a second would otherwise drop the selection, and
        a row cannot be clicked while it keeps deselecting."""
        if not pids:
            return
        wanted = set(pids)
        iterator = _walk(self.tree)
        for item in iterator:
            if item.data(0, PID_ROLE) in wanted:
                item.setSelected(True)

    def _selection_changed(self) -> None:
        self.end_button.setEnabled(bool(self._selected_pids()))

    def _show_menu(self, position) -> None:
        pids = self._selected_pids()
        if not pids:
            return
        info = None
        if self._snapshot is not None:
            info = self._snapshot.by_pid.get(pids[0])
        self.menu.show(pids, info,
                       self.tree.viewport().mapToGlobal(position))

    def _end_selected(self) -> None:
        pids = self._selected_pids()
        if pids:
            self.menu._end(pids)

    def _update_status(self, shown: int) -> None:
        if self._snapshot is None:
            return
        total = len(self._snapshot.by_pid)
        parts = [f"{shown:,} of {total:,} processes"]
        if self._snapshot.refused:
            parts.append(f"{self._snapshot.refused:,} could not be read "
                         f"(run as administrator to see them)")
        self.status.setText("   ·   ".join(parts))


def _disk_of(info) -> Optional[float]:
    """Read plus write, or None if neither has been measured yet."""
    read, write = info.rates.read_bps, info.rates.write_bps
    if read is None and write is None:
        return None
    return (read or 0) + (write or 0)


def _matches(row, needle: str) -> bool:
    members = getattr(row, "members", None)
    if members is not None:
        if needle in row.title.lower():
            return True
        return any(needle in member.name.lower() for member in members)
    return (needle in row.name.lower()
            or needle in (row.details.description or "").lower()
            or needle == str(row.pid))


def _heat(value, ceiling) -> Optional[QColor]:
    """Task Manager's shading: the busier the cell, the warmer it reads.

    Returns None below a floor so a table of near-zero values is not a wash
    of faint colour -- the tint has to mean "look here".
    """
    if not value or not ceiling:
        return None
    share = min(1.0, float(value) / float(ceiling))
    if share < 0.08:
        return None
    # The theme's own "warning" hue rather than a frozen amber: a colour
    # picked for the dark pane reads as a pale smear on the light one, which
    # is the whole reason core.semantic_colors exists.
    tint = QColor(semantic("warning"))
    # Alpha rather than a solid fill, so the row's background and the
    # selection still read through it.
    tint.setAlpha(int(28 + share * 90))
    return tint


def _walk(tree):
    stack = [tree.topLevelItem(index)
             for index in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item is None:
            continue
        yield item
        stack.extend(item.child(index) for index in range(item.childCount()))
