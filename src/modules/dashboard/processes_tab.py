"""All processes by default, with an optional app grouping.

The grouped view rolls each app up to a summary and labels how many real
process rows are inside it. Counts distinguish listed from collapsed rows.

The heat tint is not decoration. Task Manager shades the value cells so the
eye finds the expensive row without reading the numbers, and the shade is
scaled against the busiest row currently on screen rather than against a
fixed ceiling -- on an idle machine the worst offender should still stand
out.
"""
import logging
import time
from dataclasses import replace
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMenu, QPlainTextEdit, QPushButton,
                             QSplitter, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from core.worker import Worker, retire

from core.procengine.columns import fmt_bytes, fmt_percent, fmt_rate
from core.procengine.grouping import group_processes, totals
from core.procengine.snapshot import SnapshotSource
from ui.error_banner import ErrorBanner
from . import process_view as pv
from .recycle_watch import RecycleWatch
from .process_menu import ProcessMenu
from ui.help.dashboard_ui import explain_menu

logger = logging.getLogger(__name__)

REFRESH_MS = 1000

BASE_COLUMNS = ("Name", "CPU", "Memory", "Disk", "PID")
#: The optional columns follow the five Task Manager ones, hidden until asked
#: for -- the default view stays as readable as it always was.
COLUMNS = BASE_COLUMNS + tuple(c.title for c in pv.COLUMNS)
FIRST_EXTRA = len(BASE_COLUMNS)
CPU, MEMORY, DISK = 1, 2, 3
CONFIG_COLUMNS = "modules.dashboard.process_columns"
CONFIG_VIEW = "modules.dashboard.process_view_mode"
SERVICES_TTL_S = 15.0

#: Roles carrying the sortable value and the pid behind a row.
VALUE_ROLE = Qt.ItemDataRole.UserRole + 1
PID_ROLE = Qt.ItemDataRole.UserRole + 2


class ProcessesTab(QWidget):
    """Flat or grouped process view. Owns its workers, per CLAUDE.md."""

    snapshot_taken = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers: list = []
        self._source = SnapshotSource()
        self._app = None
        self._busy = False
        self._read_worker = None
        self._services_worker = None
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
        self.view_mode = QComboBox(self)
        self.view_mode.addItem("All processes", "flat")
        self.view_mode.addItem("Grouped by app", "grouped")
        self.view_mode.currentIndexChanged.connect(self._view_changed)
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
        self._view_changed()

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

    def _build_chips(self) -> QVBoxLayout:
        rows = QVBoxLayout()
        controls = QHBoxLayout()
        controls.addWidget(self.view_mode)
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
        self.reset_button = QPushButton("Reset filters", self)
        self.reset_button.clicked.connect(self._reset_filters)
        controls.addWidget(self.reset_button)
        self.expand_button = QPushButton("Expand all", self)
        self.collapse_button = QPushButton("Collapse all", self)
        self.expand_button.clicked.connect(lambda: self.tree_expand_all(True))
        self.collapse_button.clicked.connect(lambda: self.tree_expand_all(False))
        controls.addWidget(self.expand_button)
        controls.addWidget(self.collapse_button)
        controls.addStretch(1)
        columns = QPushButton("Columns…", self)
        columns.setToolTip("Choose which extra columns to show")
        columns.clicked.connect(
            lambda: self._choose_columns(columns.mapToGlobal(columns.rect().bottomLeft())))
        controls.addWidget(columns)
        rows.addLayout(controls)
        rows.addLayout(row)
        return rows

    def _build_tree(self) -> QTreeWidget:
        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setRootIsDecorated(self.view_mode.currentData() == "grouped")
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
        explain_menu(menu)
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
        self._chips.get(key, self._chips["all"])[0].setChecked(True)
        self._rebuild()

    def _reset_filters(self) -> None:
        self.filter_box.blockSignals(True)
        self.filter_box.clear()
        self.filter_box.blockSignals(False)
        self._set_filter("all")

    def _view_changed(self) -> None:
        grouped = self.view_mode.currentData() == "grouped"
        self.expand_button.setVisible(grouped)
        self.collapse_button.setVisible(grouped)
        if hasattr(self, "tree"):
            self.tree.setRootIsDecorated(grouped)
        config = getattr(self._app, "config", None)
        if config is not None:
            config.set(CONFIG_VIEW, self.view_mode.currentData())
        self._rebuild()

    def tree_expand_all(self, opened=True) -> None:
        for item in _walk(self.tree):
            if item.childCount():
                item.setExpanded(opened if item.data(0, PID_ROLE) is not None else True)

    def _refresh_chip_counts(self) -> None:
        if self._snapshot is None:
            return
        needle = self.filter_box.text().strip().lower()
        counts = pv.filter_counts(i for i in self._snapshot.by_pid.values()
                                  if not needle or _matches(i, needle))
        for key, (chip, label) in self._chips.items():
            chip.setText(f"{label} ({counts[key]})")

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
        config = getattr(app, "config", None)
        mode = config.get(CONFIG_VIEW, "flat") if config is not None else "flat"
        self.view_mode.setCurrentIndex(1 if mode == "grouped" else 0)

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
        retire(self._workers)

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
        self._read_worker = worker
        worker.signals.result.connect(
            lambda result: self._apply(result) if self._current(worker, "_read_worker") else None)
        worker.signals.error.connect(
            lambda message: self._failed(message) if self._current(worker, "_read_worker") else None)
        worker.signals.finished.connect(lambda: self._finished(worker, "_read_worker", "_busy"))
        self._workers.append(worker)
        pool.start(worker)

    def _current(self, worker, slot) -> bool:
        return getattr(self, slot) is worker and not worker.is_cancelled

    def _finished(self, worker, slot, busy) -> None:
        if getattr(self, slot) is worker:
            setattr(self, slot, None)
            setattr(self, busy, False)
        if worker in self._workers:
            self._workers.remove(worker)

    def _read(self):
        snapshot = self._source.read()
        # Grouped on the worker too: EnumWindows plus the rollup is the
        # expensive half, and doing it on the UI thread would stutter.
        return snapshot, group_processes(snapshot)

    def _apply(self, result) -> None:
        snapshot, groups = result
        self._snapshot = snapshot
        self.menu.set_snapshot(snapshot)
        self._groups = groups
        self._recycle.update(snapshot)
        from . import net_trace
        if net_trace.shared().running:
            net_trace.shared().sample_rates()
        self._refresh_chip_counts()
        self._rebuild()
        self.snapshot_taken.emit(snapshot)

    def _failed(self, message) -> None:
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
        collapsed = 0
        peak = self._peaks()
        self._refresh_chip_counts()
        grouped = self.view_mode.currentData() == "grouped"
        if not grouped:
            rows = [i for i in self._snapshot.by_pid.values()
                    if (not needle or _matches(i, needle)) and pv.passes(self._filter, i)]
            for info in self._sorted(rows):
                self._process_item(self.tree, info, peak)
            shown = len(rows)
        for group in self._groups if grouped else []:
            rows = self._rows_for(group, needle)
            if not rows:
                continue
            count = sum(len(getattr(row, "members", [row])) for row in rows)
            label = (f"{group.name} ({len(rows)} apps, {count} processes)"
                     if group.name == "Apps" else f"{group.name} ({count} processes)")
            header = QTreeWidgetItem([label])
            header.setFirstColumnSpanned(True)
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(header)
            header.setExpanded(True)
            for row in rows:
                listed = self._add_row(header, row, peak)
                shown += listed
                collapsed += len(getattr(row, "members", [row])) - listed
        self._restore_selection(selected)
        self.tree.blockSignals(False)
        self.tree.setUpdatesEnabled(True)
        self._selection_changed()
        self._update_status(shown, collapsed)

    def _rows_for(self, group, needle: str) -> List:
        kept = []
        for row in group.rows:
            members = getattr(row, "members", None)
            matches = [m for m in (members if members is not None else [row])
                       if (not needle or _matches(m, needle)) and pv.passes(self._filter, m)]
            if matches:
                kept.append(replace(row, members=matches,
                                    pid=row.pid if any(m.pid == row.pid for m in matches) else matches[0].pid)
                            if members is not None else row)
        return self._sorted(kept)

    def _add_row(self, parent, row, peak) -> int:
        """One row, which is either an app (with children) or a process."""
        members = getattr(row, "members", None)
        if members is None:
            self._process_item(parent, row, peak)
            return 1

        summed = totals(members)
        item = QTreeWidgetItem(parent, [f"{row.title} ({len(members)} processes)"])
        item.setData(0, PID_ROLE, row.pid)
        self._set_value(item, CPU, summed["cpu"], fmt_percent, peak["cpu"])
        self._set_value(item, MEMORY, summed["memory"], fmt_bytes,
                        peak["memory"])
        self._set_value(item, DISK, summed["disk"], fmt_rate, peak["disk"])
        item.setText(4, str(row.pid))
        self._fill_extra(item, members)
        for member in self._sorted(members):
            self._process_item(item, member, peak)
        item.setExpanded(row.pid in self._expanded or bool(self.filter_box.text().strip())
                         or self._filter != "all")
        if len(members) == 1:
            # Keep the representative's badges on its app summary as well.
            self._apply_row_flags(item, members[0])
        return len(members) if item.isExpanded() else 0

    def _process_item(self, parent, info, peak) -> None:
        label = info.details.description or info.name
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, PID_ROLE, info.pid)
        self._apply_row_flags(item, info)
        self._set_value(item, CPU, info.rates.cpu_percent, fmt_percent,
                        peak["cpu"])
        self._set_value(item, MEMORY, info.raw.working_set_private,
                        fmt_bytes, peak["memory"])
        disk = _disk_of(info)
        self._set_value(item, DISK, disk, fmt_rate, peak["disk"])
        item.setText(4, str(info.pid))
        self._fill_extra(item, [info])

    def _apply_row_flags(self, item, info) -> None:
        """The tooltip and foreground tint for one real process's row.

        Shared by `_process_item` (a member of a multi-process app, or a
        top-level process) and `_add_row`'s single-member-app branch, which
        would otherwise show none of these -- see the comment there.
        """
        flags = pv.badges(info)
        if self._recycle.recently_reused(info.pid, 120.0, self._snapshot.taken_at):
            flags = flags + ["PID reused"]
        item.setToolTip(0, (info.details.path or info.name)
                        + (f"\n[{', '.join(flags)}]" if flags else ""))
        if "elevated" in flags:
            item.setForeground(0, QBrush(QColor(semantic("info"))))
        if "this app" in flags:
            # "match" (violet): distinct from elevated's info blue and the
            # high-cpu/high-mem heat tints, so the app's own row reads as an
            # identity marker rather than a severity.
            item.setForeground(0, QBrush(QColor(semantic("match"))))

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
            self._update_visible_status()
            return
        if opened:
            self._expanded.add(pid)
        else:
            self._expanded.discard(pid)
        self._update_visible_status()

    def _update_visible_status(self) -> None:
        shown = collapsed = 0
        for item in _walk(self.tree):
            if item.data(0, PID_ROLE) is None:
                continue
            if item.childCount():
                if not item.isExpanded():
                    collapsed += item.childCount()
            else:
                ancestor = item.parent()
                visible = True
                while ancestor is not None:
                    visible = visible and ancestor.isExpanded()
                    ancestor = ancestor.parent()
                shown += int(visible)
        self._update_status(shown, collapsed)

    def _selected_pids(self) -> List[int]:
        pids = []
        for item in self.tree.selectedItems():
            pid = item.data(0, PID_ROLE)
            if pid is not None and pid not in pids:
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
            self._services_worker = worker
            worker.signals.result.connect(
                lambda value: self._on_services(value) if self._current(worker, "_services_worker") else None)
            worker.signals.error.connect(
                lambda error: self._on_services_error(error) if self._current(worker, "_services_worker") else None)
            worker.signals.finished.connect(
                lambda: self._finished(worker, "_services_worker", "_services_busy"))
            self._workers.append(worker)
            pool.start(worker)
        return (self._services or {}).get(pid)

    def _on_services(self, mapping) -> None:
        self._services_at = time.monotonic()
        self._services = mapping
        self._selection_changed()

    def _on_services_error(self, message) -> None:
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

    def _update_status(self, shown: int, collapsed: int = 0) -> None:
        if self._snapshot is None:
            return
        parts = [f"{shown:,} processes listed"]
        if self.view_mode.currentData() == "grouped":
            parts.append(f"{collapsed:,} inside collapsed apps")
        if self._recycle.total:
            last = self._recycle.events[-1]
            parts.append(f"{self._recycle.total} PID reuse(s) seen (latest: {last.pid} "
                         f"{last.old_name} -> {last.new_name})")
        needle = self.filter_box.text().strip().lower()
        refused = sum(pv.passes("unreadable", i) for i in self._snapshot.by_pid.values()
                      if (not needle or _matches(i, needle)) and pv.passes(self._filter, i))
        if refused:
            from core.admin_utils import is_admin
            hint = " (run as administrator for more)" if not is_admin() else ""
            parts.append(f"{refused:,} processes' details could not be read{hint}")
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
        return any(_matches(member, needle) for member in members)
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
