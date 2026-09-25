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
import time
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QButtonGroup, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMenu, QPlainTextEdit, QPushButton,
                             QSplitter, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from core.worker import Worker

from core.procengine.columns import fmt_bytes, fmt_percent, fmt_rate
from core.procengine.grouping import group_processes, totals
from core.procengine.snapshot import SnapshotSource
from ui.error_banner import ErrorBanner
from . import process_view as pv
from .recycle_watch import RecycleWatch
from .process_menu import ProcessMenu

logger = logging.getLogger(__name__)

REFRESH_MS = 1000

BASE_COLUMNS = ("Name", "CPU", "Memory", "Disk", "PID")
#: The optional columns follow the five Task Manager ones, hidden until asked
#: for -- the default view stays as readable as it always was.
COLUMNS = BASE_COLUMNS + tuple(c.title for c in pv.COLUMNS)
FIRST_EXTRA = len(BASE_COLUMNS)
CPU, MEMORY, DISK = 1, 2, 3
CONFIG_COLUMNS = "modules.dashboard.process_columns"
SERVICES_TTL_S = 15.0

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
        self._recycle = RecycleWatch()
        self._net_wanted = False
        self._filter = "all"
        self._sort = None            # (column, descending) or None
        self._visible_extra = set(pv.DEFAULT_VISIBLE)
        self._services = None
        self._services_at = 0.0
        self._services_busy = False
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
        self.filter_box.setPlaceholderText(
            "Filter by name, PID, user, path or command line…")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self._rebuild)
        top.addWidget(self.filter_box, 1)

        self.net_button = QPushButton("Track network", self)
        self.net_button.setCheckable(True)
        self.net_button.setToolTip(
            "Show each process's network throughput. Uses a kernel event trace, which "
            "needs administrator rights; results arrive about 2 seconds behind the traffic.")
        self.net_button.toggled.connect(self._toggle_network)
        top.addWidget(self.net_button)
        self.end_button = QPushButton("End task", self)
        self.end_button.setEnabled(False)
        self.end_button.clicked.connect(self._end_selected)
        top.addWidget(self.end_button)
        layout.addLayout(top)
        layout.addLayout(self._build_chips())

        split = QSplitter(Qt.Orientation.Vertical, self)
        split.addWidget(self._build_tree())
        split.addWidget(self._build_detail())
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)

        self.status = QLabel("", self)
        layout.addWidget(self.status)

        self.menu = ProcessMenu(self)
        self.menu.changed.connect(self.refresh)
        self._snapshot = None

    def _build_chips(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        self._chips = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label, _fn in pv.FILTERS:
            chip = QPushButton(label, self)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            group.addButton(chip)
            self._chips[key] = (chip, label)
            row.addWidget(chip)
        row.addStretch(1)
        columns = QPushButton("Columns…", self)
        columns.setToolTip("Choose which extra columns to show")
        columns.clicked.connect(
            lambda: self._choose_columns(columns.mapToGlobal(columns.rect().bottomLeft())))
        row.addWidget(columns)
        return row

    def _build_tree(self) -> QTreeWidget:
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
        # Interactive, not Stretch: with Path and Command line showing, a
        # stretched Name was squeezed to ~100px and every app read "Google Ch…".
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 280)
        header.setStretchLastSection(False)
        for section in range(1, len(COLUMNS)):
            header.setSectionResizeMode(
                section, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(section, 100)
        for offset, column in enumerate(pv.COLUMNS):
            header.resizeSection(FIRST_EXTRA + offset, column.width)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._sort_by)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos: self._choose_columns(header.mapToGlobal(pos)))
        self._apply_visible_columns()
        return self.tree

    def _build_detail(self) -> QPlainTextEdit:
        self.detail = QPlainTextEdit(self)
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.detail.setFont(mono)
        self.detail.setMinimumHeight(190)
        self.detail.setPlaceholderText(
            "Select a process to see its path, command line, user, parent, "
            "memory and hosted services.")
        return self.detail

    # ---- columns, filter chips and sorting -------------------------------

    def _apply_visible_columns(self) -> None:
        header = self.tree.header()
        for offset, column in enumerate(pv.COLUMNS):
            header.setSectionHidden(FIRST_EXTRA + offset,
                                    column.key not in self._visible_extra)

    def _choose_columns(self, position) -> None:
        menu = QMenu(self)
        for column in pv.COLUMNS:
            action = menu.addAction(column.title)
            action.setCheckable(True)
            action.setChecked(column.key in self._visible_extra)
            action.toggled.connect(
                lambda on, k=column.key: self._toggle_column(k, on))
        menu.exec(position)

    def _toggle_column(self, key: str, shown: bool) -> None:
        if shown:
            self._visible_extra.add(key)
        else:
            self._visible_extra.discard(key)
        self._apply_visible_columns()
        config = getattr(self._app, "config", None)
        if config is not None:
            config.set(CONFIG_COLUMNS, sorted(self._visible_extra))

    def _load_saved_columns(self) -> None:
        config = getattr(self._app, "config", None)
        saved = config.get(CONFIG_COLUMNS, None) if config is not None else None
        if isinstance(saved, list):
            self._visible_extra = {k for k in saved if k in pv.BY_KEY}
            self._apply_visible_columns()

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._rebuild()

    def _refresh_chip_counts(self) -> None:
        if self._snapshot is None:
            return
        counts = pv.filter_counts(self._snapshot.by_pid.values())
        for key, (chip, label) in self._chips.items():
            chip.setText(label if key == "all" else f"{label} ({counts[key]})")

    def _sort_by(self, column: int) -> None:
        descending = True
        if self._sort is not None and self._sort[0] == column:
            descending = not self._sort[1]
        self._sort = (column, descending)
        header = self.tree.header()
        header.setSortIndicatorShown(True)
        header.setSortIndicator(
            column, Qt.SortOrder.DescendingOrder if descending
            else Qt.SortOrder.AscendingOrder)
        self._rebuild()

    def _sorted(self, rows: List) -> List:
        if self._sort is None:
            return rows
        column, descending = self._sort
        return sorted(rows, key=lambda row: _row_value(row, column),
                      reverse=descending)

    # ---- lifecycle ------------------------------------------------------

    def set_app(self, app) -> None:
        self._app = app
        self.menu.set_app(app)
        self._load_saved_columns()

    def start(self) -> None:
        if self._net_wanted:
            self._begin_network()          # left the tab with it on: resume
        self.refresh()
        self._timer.start(REFRESH_MS)

    def stop(self) -> None:
        self._timer.stop()
        self.cancel_all()
        from . import net_trace
        net_trace.shared().stop()          # a kernel trace nobody is looking at is pure cost

    # ---- per-process network (ETW) ---------------------------------------

    def _toggle_network(self, on: bool) -> None:
        self._net_wanted = on
        if on:
            self._begin_network()
        else:
            from . import net_trace
            net_trace.shared().stop()
            self._set_column_visible("network", False)
            self.status.setText("Network tracking stopped.")

    def _begin_network(self) -> None:
        from . import net_trace
        ok, message = net_trace.shared().start()
        if not ok:
            self._net_wanted = False
            self.net_button.blockSignals(True)
            self.net_button.setChecked(False)
            self.net_button.blockSignals(False)
            self.status.setText(f"Network tracking not started: {message}")
            return
        self._set_column_visible("network", True)
        self.status.setText("Tracking network per process (about 2 s behind the traffic).")

    def _set_column_visible(self, key: str, shown: bool) -> None:
        if (key in self._visible_extra) != shown:
            self._toggle_column(key, shown)
            self._rebuild()

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
        self._recycle.update(snapshot)
        from . import net_trace
        if net_trace.shared().running:
            net_trace.shared().sample_rates()
        self._refresh_chip_counts()
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
        # clear() and the restore below each fire selection-changed; letting
        # them through blanked the detail panel and reset its scroll every
        # second. One notification, after the tree is whole again.
        self.tree.blockSignals(True)
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
        self._restore_selection(selected)
        self.tree.blockSignals(False)
        self.tree.setUpdatesEnabled(True)
        self._selection_changed()
        self._update_status(shown)

    def _rows_for(self, group, needle: str) -> List:
        kept = []
        for row in group.rows:
            if needle and not _matches(row, needle):
                continue
            if self._filter != "all" and not _passes_filter(row, self._filter):
                continue
            kept.append(row)
        return self._sorted(kept)

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
        self._fill_extra(item, members)
        if len(members) > 1:
            for member in sorted(members, key=lambda info: info.pid):
                self._process_item(item, member, peak)
            item.setExpanded(row.pid in self._expanded)
        return len(members)

    def _process_item(self, parent, info, peak) -> None:
        label = info.details.description or info.name
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, PID_ROLE, info.pid)
        flags = pv.badges(info)
        if self._recycle.recently_reused(info.pid, 120.0, self._snapshot.taken_at):
            flags = flags + ["PID reused"]
        item.setToolTip(0, (info.details.path or info.name)
                        + (f"\n[{', '.join(flags)}]" if flags else ""))
        if "elevated" in flags:
            item.setForeground(0, QBrush(QColor(semantic("info"))))
        self._set_value(item, CPU, info.rates.cpu_percent, fmt_percent,
                        peak["cpu"])
        self._set_value(item, MEMORY, info.raw.working_set_private,
                        fmt_bytes, peak["memory"])
        disk = _disk_of(info)
        self._set_value(item, DISK, disk, fmt_rate, peak["disk"])
        item.setText(4, str(info.pid))
        self._fill_extra(item, [info])

    def _fill_extra(self, item, infos) -> None:
        """The optional columns; skipped for any column that is hidden."""
        for offset, column in enumerate(pv.COLUMNS):
            if column.key not in self._visible_extra:
                continue
            index = FIRST_EXTRA + offset
            item.setText(index, column.text(infos[0]) if len(infos) == 1
                         else pv.aggregate_text(column, infos))

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
        pids = self._selected_pids()
        self.end_button.setEnabled(bool(pids))
        self._show_detail(pids[0] if len(pids) == 1 else None)

    def _show_detail(self, pid) -> None:
        info = (self._snapshot.by_pid.get(pid)
                if pid is not None and self._snapshot is not None else None)
        if info is None:
            self.detail.setPlainText("")
            return
        hosted = None
        if info.name.lower() == "svchost.exe":
            hosted = self._services_for(pid)
        scroll = self.detail.verticalScrollBar().value()
        self.detail.setPlainText(pv.detail_text(info, self._snapshot, hosted))
        self.detail.verticalScrollBar().setValue(scroll)

    def _services_for(self, pid: int):
        """Services a svchost hosts. Read on a worker and cached; the first
        call answers None and the detail re-renders when the map lands."""
        now = time.monotonic()
        stale = now - self._services_at > SERVICES_TTL_S
        pool = getattr(self._app, "thread_pool", None) if self._app else None
        if stale and not self._services_busy and pool is not None:
            self._services_busy = True
            worker = Worker(lambda _w: pv.services_by_pid())
            worker.signals.result.connect(self._on_services)
            worker.signals.error.connect(self._on_services_error)
            self._workers.append(worker)
            pool.start(worker)
        return (self._services or {}).get(pid)

    def _on_services(self, mapping) -> None:
        self._services_busy = False
        self._services_at = time.monotonic()
        self._services = mapping
        self._selection_changed()

    def _on_services_error(self, message) -> None:
        self._services_busy = False
        logger.warning("service map unreadable: %s", message)

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
        if self._recycle.total:
            last = self._recycle.events[-1]
            parts.append(f"{self._recycle.total} PID reuse(s) seen (latest: {last.pid} "
                         f"{last.old_name} -> {last.new_name})")
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
    details = row.details
    return (needle in row.name.lower()
            or needle in (details.description or "").lower()
            or needle in (details.user or "").lower()
            or needle in (details.path or "").lower()
            or needle in (details.cmdline or "").lower()
            or needle == str(row.pid))


def _passes_filter(row, key: str) -> bool:
    """An app row passes when any process inside it does."""
    members = getattr(row, "members", None)
    return any(pv.passes(key, m) for m in (members if members is not None else [row]))


def _row_value(row, column: int):
    """The sort key for one row: an app row sorts on its members' total."""
    members = getattr(row, "members", None)
    infos = list(members) if members is not None else [row]
    if column == 0:
        return (row.title if members is not None
                else (row.details.description or row.name)).lower()
    if column == CPU:
        return sum(i.rates.cpu_percent or 0.0 for i in infos)
    if column == MEMORY:
        return sum(i.raw.working_set_private for i in infos)
    if column == DISK:
        return sum(_disk_of(i) or 0.0 for i in infos)
    if column == 4:
        return row.pid
    spec = pv.COLUMNS[column - FIRST_EXTRA]
    values = [spec.value(i) for i in infos]
    return sum(values) if spec.summable else max(values)


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
