"""History: what each clean REALLY did -- measured bytes, files left in use and
by whom, refusals with their reason. Reads cleanup_history; computes nothing
that the log does not already say.

Layout, top to bottom: a toolbar (filter, category, Export HTML, Refresh), four
KPI tiles, the trend chart (freed per clean, total freed, reclaimable at each
scan), the table of cleans, and below it the selected clean's paths as a TREE
grouped by what happened to them. Open folder / Copy path act on the selected
path row and are disabled until one is selected -- before, they acted on
whatever line the text cursor of a read-only text box sat on, and silently did
nothing on the summary line (defect D8). The plain-text account of the clean
is still there, on the "Full text" tab, for copying whole.
"""
import logging
import os
import subprocess
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFrame,
                             QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                             QPushButton, QSplitter, QStackedWidget, QTableWidget,
                             QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from core.formatting import format_size
from core.table_ui import set_role
from modules.cleanup import cleanup_history

logger = logging.getLogger(__name__)

COLUMNS = ("When", "Freed", "Files", "Left in use", "Refused", "Source", "Summary")
COL_WHEN, COL_FREED, COL_FILES, COL_IN_USE, COL_REFUSED, COL_SOURCE, COL_SUMMARY = range(7)

COLUMN_TIPS = (
    "When the clean finished (local time).",
    "Bytes the clean MEASURABLY freed -- what the deleter removed, never the scanned size.",
    "Files removed. Old entries, written before files were counted, count items instead.",
    "Files left behind because a running program held them open.",
    "Paths Cleanup refused to touch on purpose (sign-in stores, Outlook data, exclusions).",
    "What ran the clean, and with which preset, when the clean recorded it.",
    "The clean in one sentence. Select the row for every path it touched.",
)

PATH_COLUMNS = ("Path", "Size", "What happened", "Reason")

#: How many cleans the tab reads (the log itself keeps 200).
HISTORY_LIMIT = 200

#: The roles path rows carry.
PATH_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = Qt.ItemDataRole.UserRole + 1

#: Short names for the catalog categories a clean can record.
CATEGORY_LABELS = {
    "system": "System", "browsers": "Browsers", "apps": "Apps", "games": "Games",
    "dev": "Developer tools", "cloud": "Cloud & sync", "comms": "Chat & calls",
    "media": "Media", "windows_update": "Windows Update", "logs": "Logs & reports",
    "large": "Large items", "duplicates": "Duplicates",
}


def category_label(key: str) -> str:
    return CATEGORY_LABELS.get(key) or key.replace("_", " ").strip().capitalize() or key


def _path_of(line: str) -> str:
    """The path on a detail line, or "" -- drops the '--  reason' and '(size)' tails."""
    line = line.strip()
    if line[1:3] != ":\\":
        return ""
    return line.split("  --  ")[0].split("  (")[0]


def _nearest_existing_dir(path: str) -> str:
    """The path itself if a directory, else its nearest existing ancestor, else ""."""
    cur = path
    while True:
        if os.path.isdir(cur):
            return cur
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            return ""
        cur = parent


def _detail_text(entry: dict) -> str:
    lines = [cleanup_history.summarise(entry), ""]
    if entry.get("source") or entry.get("preset"):
        lines.append("Run from: " + _source_text(entry))
    owners = entry.get("owners") or {}
    if owners:
        lines.append("Held by: " + ", ".join(f"{n} ({c})" for n, c in owners.items()))
    cats = entry.get("categories")
    if isinstance(cats, dict) and cats:
        lines.append("By category: " + ", ".join(
            f"{category_label(k)} {format_size(cleanup_history.freed_of(entry, k))}"
            for k in sorted(cats, key=lambda k: -cleanup_history.freed_of(entry, k))))
    largest = entry.get("largest") or []
    if largest:
        lines.append("Removed (largest):")
        lines += [f"  {p}  ({format_size(s)})" for p, s in largest]
    for title, key in (("In use", "locked"), ("Access denied", "denied")):
        paths = entry.get(key) or []
        if paths:
            lines.append(f"{title}:")
            lines += [f"  {p}" for p in paths]
    if entry.get("cloud_only"):
        lines.append(f"Cloud-only files left: {entry['cloud_only']:,}")
    refused = entry.get("refused") or []
    if refused:
        lines.append("Refused:")
        for row in refused:
            path, reason = (row if isinstance(row, list) else [row, "refused"])
            lines.append(f"  {path}  --  {reason}")
    return "\n".join(lines)


def _source_text(entry: dict) -> str:
    source = str(entry.get("source") or "")
    preset = str(entry.get("preset") or "")
    if source and preset:
        return f"{source} ({preset.capitalize()})"
    return source or (preset.capitalize() + " preset" if preset else "")


def _when_text(entry: dict) -> str:
    return str(entry.get("at", "") or "").replace("T", " ") or "Unknown time"


class _SortItem(QTableWidgetItem):
    """Sorts on the number in SORT_ROLE, never the formatted text ("9 B" must
    not land above "10 GB" -- the TreeSize rule)."""

    def __lt__(self, other):
        mine, theirs = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if mine is not None and theirs is not None:
            return mine < theirs
        return super().__lt__(other)


def _charts():
    """The charts module (WP2), or None if it is not importable -- the tab
    then shows a sentence where the chart would be, never a crash."""
    try:
        from modules.cleanup.components import charts
        return charts
    except ImportError as e:
        logger.warning("cleanup history: trend chart unavailable: %s", e)
        return None


def _read_summaries() -> list:
    """scan_summaries.json for the reclaimable line, or [] (logged)."""
    try:
        from modules.cleanup.cleanup_scanner import scan_snapshot
    except ImportError as e:
        logger.warning("cleanup history: scan summaries unavailable: %s", e)
        return []
    return scan_snapshot.read_summaries()


class _KpiTile(QFrame):
    def __init__(self, caption: str, tip: str, parent=None):
        super().__init__(parent)
        self.setObjectName("kpiTile")
        self.setToolTip(tip)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        self.caption = QLabel(caption)
        self.caption.setObjectName("kpiCaption")
        self.value = QLabel("--")
        self.value.setObjectName("kpiValue")
        self.sub = QLabel("")
        self.sub.setObjectName("kpiCaption")
        for w in (self.caption, self.value, self.sub):
            lay.addWidget(w)
        self.setAccessibleName(caption)

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        self.setAccessibleDescription(f"{value}. {sub}".strip())


class _HistoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all: List[dict] = []        # newest first, as the log reads
        self._entries: List[dict] = []    # the ones the filter shows, newest first
        self._summaries: list = []
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._build_toolbar(layout)

        self._build_kpi_tiles(layout)

        self._build_trend()

        self._build_results()

        self._build_details()

        self._build_splitter(layout)
        self._update_path_buttons()

    # -- data ------------------------------------------------------------------

    def refresh(self) -> None:
        self._all = cleanup_history.recent_deleted(limit=HISTORY_LIMIT)
        self._summaries = _read_summaries()
        self._fill_categories()
        self._apply_filter()

    def _fill_categories(self) -> None:
        current = self.category.currentData()
        self.category.blockSignals(True)
        try:
            self.category.clear()
            self.category.addItem("All categories", None)
            totals = cleanup_history.category_totals(self._all)
            for key in cleanup_history.categories_seen(self._all):
                t = totals[key]
                self.category.addItem(category_label(key), key)
                self.category.setItemData(
                    self.category.count() - 1,
                    f"{category_label(key)}: {format_size(t['bytes'])} freed over "
                    f"{t['cleans']} clean{'s' if t['cleans'] != 1 else ''}.",
                    Qt.ItemDataRole.ToolTipRole)
            idx = self.category.findData(current)
            self.category.setCurrentIndex(idx if idx >= 0 else 0)
            self.category.setVisible(self.category.count() > 1)
        finally:
            self.category.blockSignals(False)

    def _filter_key(self) -> str:
        return self.filter.currentData() or "all"

    def _category_key(self) -> Optional[str]:
        return self.category.currentData()

    def _apply_filter(self) -> None:
        key, cat = self._filter_key(), self._category_key()
        self._entries = [e for e in self._all if cleanup_history.matches(e, key, cat)]
        self._fill_table()
        self._fill_tiles()
        self._fill_trend()
        if not self._all:
            self.status.setText("No cleans recorded yet.")
        elif len(self._entries) == len(self._all):
            self.status.setText(f"{len(self._all)} clean{'s' if len(self._all) != 1 else ''} recorded.")
        else:
            self.status.setText(f"{len(self._all)} cleans recorded; showing {len(self._entries)}.")
        set_role(self.status, "")
        self.export_btn.setEnabled(bool(self._entries))
        if not self._all:
            self.empty.setText("No cleans recorded yet.\nWhat each clean removes, leaves in use "
                               "or refuses is listed here once one has run.")
        else:
            self.empty.setText("No clean matches this filter.")
        self.stack.setCurrentIndex(0 if self._entries else 1)
        self.detail_tabs.setVisible(bool(self._entries))
        self._clear_detail()

    def _fill_table(self) -> None:
        table = self.table
        table.setSortingEnabled(False)       # the Driver Manager trap: never fill while sorting
        table.clearContents()
        table.setRowCount(len(self._entries))
        n = len(self._entries)
        cat = self._category_key()
        head = table.horizontalHeaderItem(COL_FREED)
        if head is not None:
            head.setText(f"Freed ({category_label(cat)})" if cat else COLUMNS[COL_FREED])
        for row, e in enumerate(self._entries):
            summary = cleanup_history.summarise(e)
            freed = cleanup_history.freed_of(e, cat)
            cells = (
                (_when_text(e), n - row),       # chronological position: unique, so ties never swap
                (format_size(freed), freed),
                (f"{cleanup_history.kind_count(e, 'removed'):,}", cleanup_history.kind_count(e, "removed")),
                (f"{cleanup_history.kind_count(e, 'locked'):,}", cleanup_history.kind_count(e, "locked")),
                (str(len(e.get("refused") or [])), len(e.get("refused") or [])),
                (_source_text(e) or "--", None),
                (summary, None),
            )
            for col, (text, key) in enumerate(cells):
                item = _SortItem(text)
                if key is not None:
                    item.setData(SORT_ROLE, key)
                if col in (COL_FREED, COL_FILES, COL_IN_USE, COL_REFUSED):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(summary if col != COL_SOURCE or not _source_text(e)
                                else f"Run from {_source_text(e)}.")
                if col == COL_WHEN:
                    item.setData(PATH_ROLE, row)       # the entry index travels with the row
                table.setItem(row, col, item)
        table.setSortingEnabled(True)
        table.sortItems(COL_WHEN, Qt.SortOrder.DescendingOrder)

    def _fill_tiles(self) -> None:
        cat = self._category_key()
        shown = self._entries
        self.tile_cleans.set(f"{len(shown):,}",
                             f"of {len(self._all):,} recorded" if len(shown) != len(self._all)
                             else "recorded")
        freed = sum(cleanup_history.freed_of(e, cat) for e in shown)
        self.tile_freed.set(format_size(freed),
                            f"in {category_label(cat)}" if cat else "measured, all cleans shown")
        if shown:
            self.tile_last.set(_when_text(shown[0]).split(" ")[0],
                               f"freed {format_size(cleanup_history.freed_of(shown[0], cat))}")
        else:
            self.tile_last.set("--", "no clean shown")
        owner = cleanup_history.top_owner(shown)
        if owner:
            self.tile_holder.set(owner[0], f"in {owner[1]} clean{'s' if owner[1] != 1 else ''}")
        else:
            self.tile_holder.set("None", "no clean left files in use")

    def _fill_trend(self) -> None:
        if self.trend is None:
            return
        charts = _charts()
        if charts is None:
            return
        cat = self._category_key()
        cleans = [charts.TrendPoint(w, v)
                  for w, v in cleanup_history.trend_points(self._entries, cat)]
        scans = [charts.TrendPoint(w, v)
                 for w, v in cleanup_history.scan_points(self._summaries, cat)]
        self.trend.set_data(cleans=cleans, scans=scans)
        self.trend.setAccessibleDescription(self.trend.accessible_summary())

    # -- selected clean ---------------------------------------------------------

    def _selected_entry(self) -> Optional[dict]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), COL_WHEN)
        idx = item.data(PATH_ROLE) if item is not None else None
        if isinstance(idx, int) and 0 <= idx < len(self._entries):
            return self._entries[idx]
        return None

    def _clear_detail(self) -> None:
        self.detail.clear()
        self.paths.clear()
        self._update_path_buttons()

    def _show_detail(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self._clear_detail()
            return
        self.paths.clear()
        self._fill_paths(entry)
        self.detail.setPlainText(_detail_text(entry))
        self._update_path_buttons()

    def _fill_paths(self, entry: dict) -> None:
        rows = cleanup_history.path_rows(entry)
        owners = entry.get("owners") or {}
        for kind in ("removed", "locked", "denied", "refused"):
            mine = [r for r in rows if r[0] == kind]
            if not mine:
                continue
            title, tip = cleanup_history.PATH_KINDS[kind]
            total = cleanup_history.kind_count(entry, kind)
            label = f"{title} ({total:,})"
            if total > len(mine):
                label = f"{title} ({total:,}, {'largest' if kind == 'removed' else 'first'} "\
                        f"{len(mine):,} listed)"
            group = QTreeWidgetItem([label, "", "", ""])
            group.setToolTip(0, tip)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            if kind == "removed":
                group.setText(1, format_size(sum(max(0, r[2]) for r in mine)))
                group.setToolTip(1, "What the listed paths freed. The clean's measured total "
                                    "is in the table above; only the largest paths are kept.")
            if kind == "locked" and owners:
                held = ", ".join(f"{n} ({c})" for n, c in
                                 sorted(owners.items(), key=lambda kv: -kv[1]))
                group.setText(3, f"Held by {held}")
                group.setToolTip(3, f"Programs that held files open: {held}. "
                                    "Close them before the next clean.")
            self.paths.addTopLevelItem(group)
            for _kind, path, size, reason in mine:
                child = QTreeWidgetItem([path, format_size(size) if size >= 0 else "",
                                         title, reason])
                child.setData(0, PATH_ROLE, path)
                child.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tip_lines = [path, "", f"{title}: {tip}"]
                if reason:
                    tip_lines.append(f"Reason: {reason}")
                child.setToolTip(0, "\n".join(tip_lines))
                child.setToolTip(2, tip)
                if reason:
                    child.setToolTip(3, reason)
                group.addChild(child)
            group.setExpanded(True)

    def _path_selection_changed(self) -> None:
        self._update_path_buttons()

    def _update_path_buttons(self) -> None:
        has = bool(self._selected_path())
        self.open_btn.setEnabled(has)
        self.copy_btn.setEnabled(has)
        self.path_hint.setVisible(not has)

    def _sync_tree_from_cursor(self) -> None:
        """A path line clicked in "Full text" selects the same path in the tree,
        so the buttons (which act on the tree) follow what was clicked."""
        path = _path_of(self.detail.textCursor().block().text())
        if not path:
            return
        root = self.paths.invisibleRootItem()
        for g in range(root.childCount()):
            group = root.child(g)
            for c in range(group.childCount()):
                child = group.child(c)
                if child.data(0, PATH_ROLE) == path:
                    self.paths.setCurrentItem(child)
                    return

    def _selected_path(self) -> str:
        item = self.paths.currentItem()
        if item is None or not item.isSelected():
            return ""
        path = item.data(0, PATH_ROLE)
        return path if isinstance(path, str) else ""

    def _open_folder(self) -> None:
        path = self._selected_path()
        if not path:
            return
        folder = _nearest_existing_dir(path)
        if not folder:
            self.status.setText("That folder no longer exists.")
            set_role(self.status, "statusWarning")
            return
        try:
            subprocess.Popen(["explorer", folder])
        except OSError as e:
            logger.warning("could not open %s: %s", folder, e)
            self.status.setText(f"Could not open the folder: {e}")
            set_role(self.status, "statusError")

    def _copy_path(self) -> None:
        path = self._selected_path()
        if path:
            QApplication.clipboard().setText(path)
            self.status.setText("Path copied.")
            set_role(self.status, "")

    # -- export -----------------------------------------------------------------

    def _export_html(self) -> None:
        if not self._entries:
            return
        try:
            from modules.cleanup import cleanup_reports
        except ImportError as e:
            logger.warning("cleanup history: reports unavailable: %s", e)
            self.status.setText("HTML export is not available in this build.")
            set_role(self.status, "statusError")
            return
        # The report reads oldest first, like the trend it draws.
        entries = list(reversed(self._entries))
        try:
            path = cleanup_reports.export_history(entries, summaries=self._summaries)
        except OSError as e:
            logger.warning("cleanup history: export failed: %s", e)
            self.status.setText(f"Could not write the report: {e}")
            set_role(self.status, "statusError")
            return
        self.status.setText(f"Report saved: {path}")
        set_role(self.status, "statusSuccess")
        try:
            os.startfile(str(path))
        except OSError as e:
            logger.warning("cleanup history: could not open %s: %s", path, e)
            self.status.setText(f"Report saved to {path}, but it could not be opened: {e}")
            set_role(self.status, "statusWarning")

    def _build_toolbar(self, layout):
        # -- toolbar ----------------------------------------------------------
        top = QHBoxLayout()
        self.status = QLabel()
        self.status.setToolTip("How many cleans the deletion log holds, and what the filter shows.")
        self.filter = QComboBox()
        for key, (text, tip) in cleanup_history.FILTERS.items():
            self.filter.addItem(text, key)
            self.filter.setItemData(self.filter.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.filter.setToolTip("Show only some cleans: cancelled ones, ones with refusals, "
                               "or ones that left files in use.")
        self.filter.setAccessibleName("History filter")
        self.filter.currentIndexChanged.connect(self._apply_filter)
        self.category = QComboBox()
        self.category.setToolTip("Per-category history: show only cleans that recorded this "
                                 "category, and chart what it freed each time. Cleans recorded "
                                 "before categories were logged have no split and are left out.")
        self.category.setAccessibleName("History category")
        self.category.currentIndexChanged.connect(self._apply_filter)
        self.export_btn = QPushButton("Export HTML")
        self.export_btn.setToolTip("Write the cleans shown, with the trend chart, to an HTML report "
                                   "under %APPDATA%\\WindowsTweaker\\cleanup\\reports and open it. "
                                   "It opens offline and contains no script.")
        self.export_btn.setAccessibleName("Export history as HTML")
        self.export_btn.clicked.connect(self._export_html)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Read the deletion log again.")
        refresh.setAccessibleName("Refresh history")
        refresh.clicked.connect(self.refresh)
        top.addWidget(self.status, 1)
        top.addWidget(QLabel("Show:"))
        top.addWidget(self.filter)
        top.addWidget(self.category)
        top.addWidget(self.export_btn)
        top.addWidget(refresh)
        layout.addLayout(top)

    def _build_kpi_tiles(self, layout):
        # -- KPI tiles --------------------------------------------------------
        tiles = QHBoxLayout()
        self.tile_cleans = _KpiTile("Cleans", "How many cleans the filter shows.")
        self.tile_freed = _KpiTile("Freed", "Measured bytes freed by the cleans shown, added up.")
        self.tile_last = _KpiTile("Last clean", "When the newest clean shown ran.")
        self.tile_holder = _KpiTile(
            "Held files most often",
            "The program that held files open in the most cleans shown. Close it before "
            "cleaning and those files can go too.")
        for t in (self.tile_cleans, self.tile_freed, self.tile_last, self.tile_holder):
            tiles.addWidget(t, 1)
        layout.addLayout(tiles)

    def _build_trend(self):
        # -- trend chart ------------------------------------------------------
        charts = _charts()
        self.trend = None
        if charts is not None:
            self.trend = charts.TrendChart(title="Freed over time")
            self.trend.setToolTip("Columns: what each clean freed. Line: total freed. Dashed "
                                  "line: what a scan found reclaimable. Hover a point for its value.")
            self.trend.setMinimumHeight(170)
            self.trend_card = charts.ChartCard("Freed over time", self.trend)
        else:
            self.trend_card = QFrame()
            self.trend_card.setObjectName("chartCard")
            lay = QVBoxLayout(self.trend_card)
            msg = QLabel("The trend chart is not available in this build.")
            msg.setObjectName("emptyState")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(msg)

    def _build_results(self):
        # -- table of cleans --------------------------------------------------
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        for col, tip in enumerate(COLUMN_TIPS):
            self.table.horizontalHeaderItem(col).setToolTip(tip)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setAccessibleName("Cleans")
        header = self.table.horizontalHeader()
        for col in range(len(COLUMNS) - 1):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SUMMARY, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_detail)

        self.empty = QLabel("No cleans recorded yet.\nWhat each clean removes, leaves in use "
                            "or refuses is listed here once one has run.")
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)    # page 0: content
        self.stack.addWidget(self.empty)    # page 1: empty state

    def _build_details(self):
        # -- the selected clean: path tree + full text ------------------------
        self.paths = QTreeWidget()
        self.paths.setHeaderLabels(PATH_COLUMNS)
        self.paths.setAccessibleName("Paths of the selected clean")
        self.paths.setToolTip("Every path the selected clean kept a record of. Select a path "
                              "to open its folder or copy it.")
        self.paths.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.paths.setUniformRowHeights(True)
        self.paths.setAlternatingRowColors(True)
        ph = self.paths.header()
        ph.setStretchLastSection(False)
        ph.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            ph.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.paths.itemSelectionChanged.connect(self._path_selection_changed)
        self.paths.itemDoubleClicked.connect(lambda *_: self._open_folder())

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setToolTip("The selected clean as plain text, for copying whole.")
        self.detail.setAccessibleName("Selected clean as text")
        self.detail.cursorPositionChanged.connect(self._sync_tree_from_cursor)

        self.detail_tabs = QTabWidget()
        paths_page = QWidget()
        pl = QVBoxLayout(paths_page)
        pl.setContentsMargins(0, 4, 0, 0)
        pl.addWidget(self.paths, 1)
        buttons = QHBoxLayout()
        self.open_btn = QPushButton("Open folder")
        self.open_btn.setToolTip("Open the selected path's folder in Explorer -- or, when it is "
                                 "gone, the nearest folder above it that still exists.")
        self.copy_btn = QPushButton("Copy path")
        self.copy_btn.setToolTip("Copy the selected path to the clipboard.")
        self.open_btn.clicked.connect(self._open_folder)
        self.copy_btn.clicked.connect(self._copy_path)
        self.path_hint = QLabel("Select a path to open its folder or copy it.")
        set_role(self.path_hint, "muted")
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(self.path_hint, 1)
        pl.addLayout(buttons)
        self.detail_tabs.addTab(paths_page, "Paths")
        self.detail_tabs.addTab(self.detail, "Full text")
        self.detail_tabs.setTabToolTip(0, "The selected clean's paths, grouped by what happened.")
        self.detail_tabs.setTabToolTip(1, "The selected clean as plain text.")

    def _build_splitter(self, layout):
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.trend_card)
        split.addWidget(self.stack)
        split.addWidget(self.detail_tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 3)
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)
        self._split = split
