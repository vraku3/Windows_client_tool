"""One log-reading pane: toolbar, table, detail panel, error banner.

Six diagnostic tabs and six standalone modules were two implementations of
this widget. The Diagnose one had a Refresh button, an empty state, lazy
loading and an error banner; the module one had none of them. This is the
Diagnose one, extracted, so there is exactly one.

The pane knows nothing about what it is reading. `loader` is an ordinary
worker function — it receives the `Worker` and returns a list of `LogEntry` —
so a caller supplies a parser and nothing else.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from html import escape
from typing import Callable, List, Optional

from PyQt6 import sip
from PyQt6.QtCore import QStringListModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox, QCompleter, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QProgressBar, QPushButton, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.table_ui import set_role
from core.types import LogEntry, LogReadResult
from core.worker import Worker, retire, track
from ui.chip_bar import ChipBar
from ui.empty_state import EmptyState
from ui.glance import GlanceStrip, GlanceTile
from ui.log_density_bar import LogDensityBar
from ui.table_style import apply_table_style
from ui.detail_panel import DetailPanel
from ui.error_banner import ErrorBanner
from ui.log_table_widget import LogTableWidget

logger = logging.getLogger(__name__)

_PAGE_TABLE = 0
_PAGE_EMPTY = 1


class LogPane(QWidget):
    """A table of log entries with a detail panel, fed by `loader`."""

    entry_selected = pyqtSignal(object)
    entry_activated = pyqtSignal(object)
    entries_loaded = pyqtSignal(object)

    def __init__(
        self,
        loader: Callable[[Worker], list],
        *,
        empty_text: str = "No records were returned from this source. Refresh to check again.",
        extra_controls: Optional[Callable[[QHBoxLayout, dict], None]] = None,
        extra_columns: Optional[List[str]] = None,
        extra_values: Optional[Callable[[object], list]] = None,
        detail_enricher: Optional[Callable[[object], str]] = None,
        summarizer: Optional[Callable[[list], str]] = None,
        copy_formatter: Optional[Callable[[object], str]] = None,
        glance_tiles=(),
        healthy_empty: bool = False,
        read_problems: Optional[Callable[[], list]] = None,
        thread_pool=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._loader = loader
        self._thread_pool = thread_pool
        self._worker: Optional[Worker] = None
        self._entries: List[object] = []      # what the table shows
        self._source: List[object] = []       # everything the loader returned
        self._view_transform: Optional[Callable[[list], list]] = None
        self._detail_enricher = detail_enricher
        self._summarizer = summarizer
        self._healthy_empty = healthy_empty
        self._read_problems = read_problems
        self._result_problems = []
        self._empty_text = empty_text
        self._filter_text = ""
        self._hud_collapsed = False
        self._time_mode_index = 0
        self.extra: dict = {}
        self.loaded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Glance strip & Mini HUD ────────────────────────────────────
        self._build_glance(root, glance_tiles)
        self._build_mini_hud(root)

        # ── Toolbar ───────────────────────────────────────────────────
        self._build_toolbar(root, extra_controls)
        self._setup_shortcuts()

        # ── Interactive Time-Density Histogram Bar ────────────────────
        self._density_bar = LogDensityBar(self)
        self._density_bar.bucket_clicked.connect(self._on_bucket_clicked)
        self._density_bar.jump_requested.connect(self._on_jump_to_time)
        self._density_bar.time_range_selected.connect(self._on_time_range_selected)
        self._peak_burst_btn = self._density_bar.peak_burst_btn
        self._timeline_toggle_btn.toggled.connect(self._density_bar.setVisible)
        root.addWidget(self._density_bar)

        # ── Severity Chips & Bookmarks Toggle ──────────────────────────
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)

        self._chips = ChipBar([
            ("all", "All loaded", 0), ("critical", "Critical", 0),
            ("error", "Errors", 0), ("warning", "Warnings", 0),
        ])
        self._chips.selection_changed.connect(lambda _keys: self.refresh_view())
        self._chips.setEnabled(False)
        chips_row.addWidget(self._chips)

        self._bookmarks_filter_btn = QPushButton("★ Bookmarks (0)")
        self._bookmarks_filter_btn.setCheckable(True)
        self._bookmarks_filter_btn.setToolTip("Filter view to only show bookmarked rows.")
        self._bookmarks_filter_btn.toggled.connect(lambda _t: self.refresh_view())
        chips_row.addWidget(self._bookmarks_filter_btn)

        self._casebook_btn = QPushButton("📋 Casebook")
        self._casebook_btn.setToolTip("Open Investigation Casebook (persistent notes, tags, and case briefs across all tabs).")
        self._casebook_btn.clicked.connect(self._open_casebook_dialog)
        chips_row.addWidget(self._casebook_btn)

        chips_row.addStretch()

        root.addLayout(chips_row)

        # A one-paragraph read of what was loaded (counts, findings, caveats).
        self._note = QLabel()
        self._note.setToolTip("Summary of the loaded evidence and any read limits. Counts cover this source's loaded records, not necessarily its complete history; correlation does not prove a cause.")
        self._note.setWordWrap(True)
        self._note.setTextFormat(Qt.TextFormat.PlainText)
        set_role(self._note, "muted")
        self._note.setVisible(False)
        self._note_btn = QPushButton("Read findings and read limits")
        self._note_btn.setCheckable(True)
        self._note_btn.setToolTip("Expand source-specific findings, explanations and any sources that could not be read.")
        self._note_btn.toggled.connect(self._note.setVisible)
        self._note_btn.setVisible(False)
        root.addWidget(self._note_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._note)

        self._error_banner = ErrorBanner(parent=self)
        root.addWidget(self._error_banner)

        # ── Main Content Splitter (Table + Inspector) ──────────────────
        self._build_table_and_detail(root, extra_columns, extra_values, copy_formatter)

    def _build_table_and_detail(
        self,
        root: QVBoxLayout,
        extra_columns: Optional[List[str]],
        extra_values: Optional[Callable[[object], list]],
        copy_formatter: Optional[Callable[[object], str]],
    ) -> None:
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._table = LogTableWidget(extra_columns=extra_columns, extra_values=extra_values,
                                      copy_formatter=copy_formatter)
        apply_table_style(self._table._table,
                          numeric_columns=range(3, 3 + len(extra_columns or [])),
                          date_columns=(0,))
        self._table.filter_requested.connect(self._on_table_filter_requested)
        self._table.zoom_window_requested.connect(self._zoom_time_window)
        self._table.bookmarks_changed.connect(self._on_bookmarks_changed)
        self._table.follow_mode_changed.connect(self._on_table_follow_mode_changed)
        self._splitter.addWidget(self._table)

        self._detail = DetailPanel()
        self._detail.zoom_window_requested.connect(self._zoom_time_window)
        self._detail.entry_focus_requested.connect(self._select_entry)
        self._splitter.addWidget(self._detail)
        self._splitter.setSizes([700, 320])

        self._stack = QStackedWidget()
        self._stack.addWidget(self._splitter)
        self._empty = EmptyState('log', "Ready to read this source",
                                 "Read-only records load when this tab opens. Refresh to try again.",
                                 "Refresh")
        self._empty.action_triggered.connect(lambda: self.load(force=True))
        self._stack.addWidget(self._empty)
        self._stack.setCurrentIndex(_PAGE_EMPTY)
        root.addWidget(self._stack, 1)

        self._table.row_selected.connect(self._on_row_selected)
        self._table.row_double_clicked.connect(self.entry_activated.emit)

    def _build_glance(self, root, extra_tiles) -> None:
        self._glance_tiles = {}
        for key, caption in (("critical", "Critical"), ("error", "Errors"),
                             ("warning", "Warnings"), ("sources", "Problem sources"),
                             ("newest", "Newest problem"), ("window", "Loaded timestamps")):
            tile = GlanceTile(caption, tooltip=f"{caption} within the loaded window, before filters. This is not the complete Windows history.")
            tile.set_unreadable("Not read yet")
            tile.setMinimumHeight(128)
            self._glance_tiles[key] = tile
        self._glance = GlanceStrip([*extra_tiles, *self._glance_tiles.values()])
        root.addWidget(self._glance)
        for key in ("critical", "error", "warning"):
            self._glance_tiles[key].clicked.connect(lambda k=key: self._filter_level(k))
        self._glance_tiles["sources"].clicked.connect(lambda: self._filter_level("all"))
        self._glance_tiles["newest"].clicked.connect(self._select_newest_problem)

    def _build_mini_hud(self, root) -> None:
        """Compact 28px single-line HUD used when glance cards are collapsed."""
        self._mini_hud = QFrame()
        self._mini_hud.setFrameShape(QFrame.Shape.StyledPanel)
        self._mini_hud.setVisible(False)
        hud_layout = QHBoxLayout(self._mini_hud)
        hud_layout.setContentsMargins(8, 2, 8, 2)
        hud_layout.setSpacing(12)

        self._hud_crit = QLabel("🔴 Critical: 0")
        self._hud_crit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud_crit.mousePressEvent = lambda _e: self._filter_level("critical")
        hud_layout.addWidget(self._hud_crit)

        self._hud_err = QLabel("🟠 Errors: 0")
        self._hud_err.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud_err.mousePressEvent = lambda _e: self._filter_level("error")
        hud_layout.addWidget(self._hud_err)

        self._hud_warn = QLabel("🟡 Warnings: 0")
        self._hud_warn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud_warn.mousePressEvent = lambda _e: self._filter_level("warning")
        hud_layout.addWidget(self._hud_warn)

        self._hud_info = QLabel("Total: 0")
        set_role(self._hud_info, "muted")
        hud_layout.addWidget(self._hud_info)

        hud_layout.addStretch()

        self._hud_sources = QLabel("Sources: —")
        set_role(self._hud_sources, "muted")
        hud_layout.addWidget(self._hud_sources)

        self._hud_newest = QLabel("Newest: —")
        set_role(self._hud_newest, "muted")
        hud_layout.addWidget(self._hud_newest)

        root.addWidget(self._mini_hud)

    def _toggle_hud_collapse(self) -> None:
        self._hud_collapsed = not self._hud_collapsed
        self._glance.setVisible(not self._hud_collapsed)
        self._mini_hud.setVisible(self._hud_collapsed)
        self._hud_toggle_btn.setText("▼ HUD" if self._hud_collapsed else "▲ HUD")

    def _build_toolbar(self, root: QVBoxLayout, extra_controls: Optional[Callable]) -> None:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self._build_search_controls(toolbar)
        if extra_controls is not None:
            extra_controls(toolbar, self.extra)
        self._build_action_controls(toolbar)
        self._build_toggle_controls(toolbar)
        root.addLayout(toolbar)

    def _build_search_controls(self, toolbar: QHBoxLayout) -> None:
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Filter (e.g. source:disk, id:7000..7050, re:error, last:24h)...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumWidth(240)
        self._search_input.setToolTip(
            "Omni-Filter: match text, 'source:...', 'level:...', 'id:...', 'last:1h', 'after:2026-11-01', "
            "or use 'AND', 'OR', 'NOT', and 're:<regex>'."
        )
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._apply_filter_from_input)
        self._search_input.textChanged.connect(self._validate_search_syntax)
        self._search_input.textChanged.connect(lambda: self._filter_timer.start(150))

        self._search_history: List[str] = [
            "source:", "level:", "id:", "msg:", "re:", "last:1h", "last:24h", "after:", "before:"
        ]
        self._completer_model = QStringListModel(self._search_history, self)
        self._search_completer = QCompleter(self._completer_model, self)
        self._search_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._search_input.setCompleter(self._search_completer)
        toolbar.addWidget(self._search_input, 1)

        self._case_btn = QPushButton("Aa")
        self._case_btn.setCheckable(True)
        self._case_btn.setMaximumWidth(28)
        self._case_btn.setToolTip("Toggle Case-Sensitive Search Mode")
        self._case_btn.toggled.connect(lambda _c: self.refresh_view())
        toolbar.addWidget(self._case_btn)

        self._regex_btn = QPushButton(".*")
        self._regex_btn.setCheckable(True)
        self._regex_btn.setMaximumWidth(28)
        self._regex_btn.setToolTip("Toggle Regular Expression (Regex) Mode")
        self._regex_btn.toggled.connect(lambda _c: self.refresh_view())
        toolbar.addWidget(self._regex_btn)

        self._preset_combo = QComboBox()
        self._preset_combo.addItems([
            "Presets: Custom",
            "Presets: Errors & Critical",
            "Presets: Warnings & Above",
            "Presets: Service Failures",
            "Presets: App Crashes (1000..1002)",
            "Presets: Disk & Storage Alerts",
            "Presets: Boot / Shutdown (12, 13, 41, 6005..)",
            "Presets: Hide Benign Noise (Noise Filter)",
            "Presets: Last 1 Hour",
            "Presets: Last 24 Hours",
            "Presets: Reset All",
        ])
        self._preset_combo.setToolTip("Quick diagnostic filter presets.")
        self._preset_combo.activated.connect(self._on_preset_selected)
        toolbar.addWidget(self._preset_combo)

    def _build_action_controls(self, toolbar: QHBoxLayout) -> None:
        self._auto_refresh_combo = QComboBox()
        self._auto_refresh_combo.addItems([
            "Auto: Off", "Auto: 5s", "Auto: 15s", "Auto: 30s", "Auto: 60s"
        ])
        self._auto_refresh_combo.setToolTip("Periodically refresh this log source while this tab is active.")
        self._auto_refresh_combo.currentTextChanged.connect(self._on_auto_refresh_changed)
        toolbar.addWidget(self._auto_refresh_combo)

        self._tail_btn = QPushButton("⚡ Follow / Tail")
        self._tail_btn.setCheckable(True)
        self._tail_btn.setObjectName("tailBtn")
        self._tail_btn.setToolTip("Auto-Tail / Live Follow: automatically scroll to the bottom as new records arrive.")
        self._tail_btn.toggled.connect(self._on_tail_toggled)
        toolbar.addWidget(self._tail_btn)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._on_auto_refresh_tick)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(100)
        self._progress.setVisible(False)
        toolbar.addWidget(self._progress)

        self._copy_btn = QPushButton("Copy row")
        self._copy_btn.setToolTip("Copy the selected row to the clipboard.")
        self._copy_btn.clicked.connect(lambda: self._table.copy_selected_to_clipboard())
        toolbar.addWidget(self._copy_btn)

        self._export_btn = QPushButton("Export ▾")
        self._export_btn.setToolTip("Export the current view to CSV, JSON, HTML, or Sanitized CSV.")
        self._export_menu = QMenu(self)
        csv_act = self._export_menu.addAction("Export to CSV...")
        csv_act.triggered.connect(lambda: self._table.export_csv())
        json_act = self._export_menu.addAction("Export to JSON...")
        json_act.triggered.connect(lambda: self._table.export_json())
        html_act = self._export_menu.addAction("Export to HTML Report...")
        html_act.triggered.connect(lambda: self._table.export_html())
        san_act = self._export_menu.addAction("Export Sanitized CSV (Masked)...")
        san_act.triggered.connect(lambda: self._table.export_sanitized_csv())
        self._export_btn.setMenu(self._export_menu)
        toolbar.addWidget(self._export_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip(
            "Read this source again, replacing the displayed records. This does not clear "
            "Windows logs or repair faults; restricted files or channels may need administrator rights.")
        self._refresh_btn.setObjectName("refreshBtn")
        self._refresh_btn.clicked.connect(lambda: self.load(force=True))
        toolbar.addWidget(self._refresh_btn)

    def _build_toggle_controls(self, toolbar: QHBoxLayout) -> None:
        self._time_mode_btn = QPushButton("⏱️ Local")
        self._time_mode_btn.setToolTip("Cycle timestamp presentation: Local Time -> Relative Time -> UTC Time")
        self._time_mode_btn.clicked.connect(self._cycle_time_mode)
        toolbar.addWidget(self._time_mode_btn)

        self._collapse_dups_btn = QPushButton("🗜️ Dups")
        self._collapse_dups_btn.setCheckable(True)
        self._collapse_dups_btn.setToolTip("Collapse consecutive identical log events into a single row with [xN] count badge.")
        self._collapse_dups_btn.toggled.connect(lambda _c: self.refresh_view())
        toolbar.addWidget(self._collapse_dups_btn)

        self._wrap_rows_btn = QPushButton("≡ Wrap")
        self._wrap_rows_btn.setCheckable(True)
        self._wrap_rows_btn.setToolTip("Toggle multi-line row wrapping in the log table.")
        self._wrap_rows_btn.toggled.connect(lambda c: self._table.set_word_wrap(c))
        toolbar.addWidget(self._wrap_rows_btn)

        self._columns_btn = QPushButton("Columns...")
        self._columns_btn.setObjectName("columnsBtn")
        self._columns_btn.setToolTip("Customize visible columns in the log table.")
        self._columns_btn.clicked.connect(lambda: self._table.open_column_picker_dialog())
        toolbar.addWidget(self._columns_btn)

        self._timeline_toggle_btn = QPushButton("📊 Timeline")
        self._timeline_toggle_btn.setToolTip("Toggle time-density histogram bar.")
        self._timeline_toggle_btn.setCheckable(True)
        self._timeline_toggle_btn.setChecked(True)
        toolbar.addWidget(self._timeline_toggle_btn)

        self._hud_toggle_btn = QPushButton("▲ HUD")
        self._hud_toggle_btn.setToolTip("Toggle between full glance cards and compact HUD strip to save vertical space.")
        self._hud_toggle_btn.setMaximumWidth(65)
        self._hud_toggle_btn.clicked.connect(self._toggle_hud_collapse)
        toolbar.addWidget(self._hud_toggle_btn)

        self._shortcuts_btn = QPushButton("?")
        self._shortcuts_btn.setToolTip("Show Keyboard Shortcuts (?)")
        self._shortcuts_btn.setMaximumWidth(28)
        self._shortcuts_btn.clicked.connect(self._show_shortcuts_dialog)
        toolbar.addWidget(self._shortcuts_btn)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("?"), self).activated.connect(self._show_shortcuts_dialog)
        QShortcut(QKeySequence("/"), self).activated.connect(self._focus_search_input)
        QShortcut(QKeySequence("1"), self).activated.connect(lambda: self._filter_level("critical"))
        QShortcut(QKeySequence("2"), self).activated.connect(lambda: self._filter_level("error"))
        QShortcut(QKeySequence("3"), self).activated.connect(lambda: self._filter_level("warning"))
        QShortcut(QKeySequence("0"), self).activated.connect(lambda: self._filter_level("all"))
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(lambda: self.load(force=True))
        QShortcut(QKeySequence("Shift+F"), self).activated.connect(self._tail_btn.toggle)

    def _on_table_filter_requested(self, field: str, value: str) -> None:
        if field == "level":
            val_lower = value.lower()
            if val_lower in self._chips.chips:
                self._chips.chips[val_lower].setChecked(True)
                return
        clause = f"{field}:{value}"
        current = self._search_input.text().strip()
        if current and field.startswith("-"):
            self._search_input.setText(f"{current} and {clause}")
        else:
            self._search_input.setText(clause)

    def _on_bookmarks_changed(self) -> None:
        count = len(getattr(self._table, "_bookmarked_entry_ids", []))
        self._bookmarks_filter_btn.setText(f"★ Bookmarks ({count})")
        if self._bookmarks_filter_btn.isChecked():
            self.refresh_view()

    def _open_casebook_dialog(self) -> None:
        """Open the Investigation Casebook dialog."""
        from ui.investigation_casebook_dialog import InvestigationCasebookDialog
        dlg = InvestigationCasebookDialog(parent=self)
        dlg.exec()
        self.refresh_view()

    def _validate_search_syntax(self, text: str) -> None:
        """Provide real-time visual cue if regex or query pattern is malformed."""
        q = text.strip()
        if not q:
            set_role(self._search_input, "")
            return
        for part in q.split():
            if part.startswith("re:"):
                pattern = part[3:]
                try:
                    re.compile(pattern)
                except re.error:
                    set_role(self._search_input, "statusError")
                    return
        set_role(self._search_input, "")

    def _apply_filter_from_input(self) -> None:
        self._filter_text = self._search_input.text()
        q = self._filter_text.strip()
        if q and hasattr(self, "_search_history") and q not in self._search_history:
            self._search_history.insert(0, q)
            if len(self._search_history) > 30:
                self._search_history.pop()
            self._completer_model.setStringList(self._search_history)
        self.refresh_view()

    def _on_preset_selected(self, index: int) -> None:
        presets = {
            1: "level:error or level:critical",
            2: "level:error or level:critical or level:warning",
            3: "id:7000..7050 or source:service",
            4: "id:1000..1002 or msg:crash or msg:hang",
            5: "source:disk or source:storahci or source:ntfs or id:7 or id:11 or id:55 or id:153",
            6: "id:12 or id:13 or id:41 or id:6005 or id:6006 or id:6008 or id:1074",
            7: "-id:10016 and -id:5379 and -source:Customer",
            8: "last:1h",
            9: "last:24h",
            10: "",
        }
        query = presets.get(index, "")
        self._search_input.setText(query)

    def _on_tail_toggled(self, checked: bool) -> None:
        if checked:
            self._table.scroll_to_bottom()

    def _open_casebook_dialog(self) -> None:
        if hasattr(self._table, "_open_casebook_dialog"):
            self._table._open_casebook_dialog()

    def _cycle_time_mode(self) -> None:
        self._time_mode_index = (self._time_mode_index + 1) % 3
        if self._time_mode_index == 0:
            self._table.set_time_mode(relative=False, utc=False)
            self._time_mode_btn.setText("⏱️ Local")
        elif self._time_mode_index == 1:
            self._table.set_time_mode(relative=True, utc=False)
            self._time_mode_btn.setText("⏱️ Rel")
        else:
            self._table.set_time_mode(relative=False, utc=True)
            self._time_mode_btn.setText("⏱️ UTC")

    def _show_shortcuts_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Log Pane Keyboard Shortcuts")
        dlg.resize(480, 420)
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(12)
        vbox.setContentsMargins(16, 16, 16, 16)

        title = QLabel("⌨️ <b>Log Navigation & Diagnostic Shortcuts</b>")
        vbox.addWidget(title)

        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        shortcuts = [
            ("?", "Show this shortcuts cheatsheet"),
            ("/", "Focus search bar (Omni-Filter)"),
            ("Ctrl+F", "Open in-table search / Find bar"),
            ("F3 / Shift+F3", "Find Next / Previous match in view"),
            ("Ctrl+C", "Copy selected table row"),
            ("Ctrl+Shift+C", "Copy selected event message only"),
            ("Ctrl+R", "Force refresh diagnostic log data"),
            ("Shift+F", "Toggle Auto-Tail / Live Follow mode (⚡)"),
            ("Space", "Toggle bookmark on selected event (★)"),
            ("Enter / Double Click", "Open full Event Detail Inspector dialog"),
            ("Ctrl+D", "Compare selected event with adjacent event (Diff)"),
            ("0", "Filter: Show All loaded events"),
            ("1", "Filter: Show Critical events only"),
            ("2", "Filter: Show Error events only"),
            ("3", "Filter: Show Warning events only"),
            ("Esc", "Dismiss Find bar / Close active inspector dialog"),
        ]
        table.setRowCount(len(shortcuts))
        for row, (key, desc) in enumerate(shortcuts):
            k_item = QTableWidgetItem(key)
            k_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            table.setItem(row, 0, k_item)
            table.setItem(row, 1, QTableWidgetItem(desc))
        table.resizeColumnToContents(0)
        vbox.addWidget(table)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(dlg.reject)
        vbox.addWidget(btn_box)

        dlg.exec()

    def _focus_search_input(self) -> None:
        """Hotkey action to jump to and focus the search bar."""
        self._search_input.setFocus()
        self._search_input.selectAll()

    def _collapse_consecutive_duplicates(self, entries: List[object]) -> List[object]:
        if not entries:
            return []
        result = []
        current = entries[0]
        count = 1
        for next_e in entries[1:]:
            c_src = getattr(current, "source", None)
            c_lvl = getattr(current, "level", None)
            c_msg = getattr(current, "message", None)
            n_src = getattr(next_e, "source", None)
            n_lvl = getattr(next_e, "level", None)
            n_msg = getattr(next_e, "message", None)

            if c_src == n_src and c_lvl == n_lvl and c_msg == n_msg:
                count += 1
            else:
                if count > 1 and isinstance(current, LogEntry):
                    collapsed = LogEntry(
                        timestamp=current.timestamp,
                        source=current.source,
                        level=current.level,
                        message=f"[x{count}] {current.message}",
                        raw=dict(current.raw or {}, repeat_count=count),
                    )
                    result.append(collapsed)
                else:
                    result.append(current)
                current = next_e
                count = 1

        if count > 1 and isinstance(current, LogEntry):
            collapsed = LogEntry(
                timestamp=current.timestamp,
                source=current.source,
                level=current.level,
                message=f"[x{count}] {current.message}",
                raw=dict(current.raw or {}, repeat_count=count),
            )
            result.append(collapsed)
        else:
            result.append(current)
        return result

    def _zoom_time_window(self, start_dt: datetime, end_dt: datetime) -> None:
        t1 = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_dt_adj = end_dt
        if end_dt.microsecond > 0 or end_dt.second == 0:
            end_dt_adj = end_dt + timedelta(seconds=1)
        t2 = end_dt_adj.strftime("%Y-%m-%d %H:%M:%S")
        self._search_input.setText(f"after:{t1} and before:{t2}")

    def filter_time_range(self, start_dt: datetime, end_dt: datetime) -> None:
        """Filter visible entries to the specified time window."""
        self._zoom_time_window(start_dt, end_dt)
        self._apply_filter_from_input()

    def _on_bucket_clicked(self, start_dt: datetime, end_dt: datetime) -> None:
        self.filter_time_range(start_dt, end_dt)

    def _on_time_range_selected(self, start_dt: datetime, end_dt: datetime) -> None:
        self.filter_time_range(start_dt, end_dt)

    def jump_to_peak_burst(self) -> None:
        """Scroll directly to the peak error burst bucket."""
        self._density_bar.jump_to_peak_burst()

    def _on_jump_to_time(self, dt: datetime) -> None:
        model = self._table._model
        best_row = -1
        min_diff = None
        error_best_row = -1
        error_min_diff = None

        for row in range(model.rowCount()):
            item = model.item(row, 0)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry and getattr(entry, "timestamp", None):
                diff = abs((entry.timestamp - dt).total_seconds())
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    best_row = row
                lvl = str(getattr(entry, "level", "")).lower()
                if lvl in ("critical", "error"):
                    if error_min_diff is None or diff < error_min_diff:
                        error_min_diff = diff
                        error_best_row = row

        target_row = best_row
        if error_best_row >= 0 and error_min_diff is not None:
            if min_diff is not None and error_min_diff <= max(min_diff * 2, 300.0):
                target_row = error_best_row

        if target_row >= 0:
            index = model.index(target_row, 0)
            self._table._table.setCurrentIndex(index)
            self._table._table.selectRow(target_row)
            self._table._table.scrollTo(index)

    def _select_entry(self, target_entry: object) -> None:
        model = self._table._model
        for row in range(model.rowCount()):
            item = model.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole) is target_entry:
                index = model.index(row, 0)
                self._table._table.setCurrentIndex(index)
                self._table._table.selectRow(row)
                self._table._table.scrollTo(index)
                break

    def _on_auto_refresh_changed(self, text: str) -> None:
        interval_map = {
            "Auto: 5s": 5000,
            "Auto: 15s": 15000,
            "Auto: 30s": 30000,
            "Auto: 60s": 60000,
        }
        ms = interval_map.get(text, 0)
        if ms > 0:
            self._auto_refresh_timer.start(ms)
        else:
            self._auto_refresh_timer.stop()

    def _on_auto_refresh_tick(self) -> None:
        if not sip.isdeleted(self) and self.isVisible() and self._worker is None:
            self.load(force=True)

    def _on_tail_toggled(self, checked: bool) -> None:
        if checked and self._table._model.rowCount() > 0:
            last_idx = self._table._model.index(self._table._model.rowCount() - 1, 0)
            self._table._table.scrollTo(last_idx)

    def _filter_level(self, key) -> None:
        if key in self._chips.chips:
            self._chips.chips[key].setChecked(True)

    def _select_newest_problem(self) -> None:
        self._filter_level("all")
        problems = [e for e in self._entries if e.level.lower() in ("critical", "error", "warning")]
        if not problems:
            return
        newest = max(problems, key=lambda e: e.timestamp)
        model = self._table._model
        for row in range(model.rowCount()):
            if model.item(row, 0).data(Qt.ItemDataRole.UserRole) is newest:
                index = model.index(row, 0)
                self._table._table.setCurrentIndex(index)
                self._table._table.scrollTo(index)
                break

    def _update_glance(self) -> None:
        self._chips.setEnabled(True)
        counts = Counter(e.level.lower() for e in self._source)
        self._chips.set_counts({"all": len(self._source), **{key: counts[key] for key in ("critical", "error", "warning")}})

        # Update mini HUD
        self._hud_crit.setText(f"🔴 Critical: {counts['critical']:,}")
        self._hud_err.setText(f"🟠 Errors: {counts['error']:,}")
        self._hud_warn.setText(f"🟡 Warnings: {counts['warning']:,}")
        self._hud_info.setText(f"Total: {len(self._source):,}")

        # Update density bar
        if hasattr(self, "_density_bar"):
            self._density_bar.set_entries(self._source)

        # Update bookmark button count
        self._on_bookmarks_changed()

        limits = self._problems()
        if limits:
            self._unknown_glance("; ".join(limits))
            return
        dated = [e.timestamp for e in self._source if e.timestamp.year >= 2000]
        self._glance_tiles["window"].set(
            f"{min(dated):%Y-%m-%d}" if dated else None,
            f"Oldest: {min(dated):%H:%M}\nNewest: {max(dated):%Y-%m-%d %H:%M}" if dated else "",
            tooltip=(f"Oldest: {min(dated):%Y-%m-%d %H:%M:%S}\nNewest: {max(dated):%Y-%m-%d %H:%M:%S}\nBefore filters."
                     if dated else "No usable loaded timestamps"),
            reason="No usable loaded timestamps")
        for key, role in (("critical", "danger"), ("error", "danger"), ("warning", "warning")):
            self._glance_tiles[key].set(f"{counts[key]:,}", "In loaded window",
                                        severity=role if counts[key] else "ok")
        sources = Counter(e.source or "Unknown source" for e in self._source).most_common(3)
        text = "\n".join(f"{name} ({count:,})" for name, count in sources)
        self._glance_tiles["sources"].set(
            f"{len(set(e.source for e in self._source)):,} sources",
            "\n".join(f"{name[:28]} ({count:,})" for name, count in sources) or "No loaded records",
            tooltip=f"Top three sources by record count in the loaded window:\n{text}\nSelect to clear the severity filter.")
        problems = [e for e in self._source if e.level.lower() in ("critical", "error", "warning")]
        ranked = Counter(e.source or "Unknown source" for e in problems).most_common(3)
        self._glance_tiles["sources"].set(
            f"{len(set(e.source for e in problems)):,}",
            "\n".join(f"{name[:28]} ({count:,})" for name, count in ranked) or "No loaded problems")
        self._glance_tiles["sources"].set_tooltip(
            "Problem sources (critical, errors and warnings):\n"
            + ("\n".join(f"{name} ({count:,})" for name, count in ranked) or "None")
            + "\nAll sources:\n" + text + "\nSelect to clear the severity filter.")

        if ranked:
            self._hud_sources.setText(f"Top Problem: {ranked[0][0]} ({ranked[0][1]})")
        elif sources:
            self._hud_sources.setText(f"Top Source: {sources[0][0]} ({sources[0][1]})")

        tile = self._glance_tiles["newest"]
        if not problems:
            tile.set("None", "In loaded window", severity="ok")
            self._hud_newest.setText("Newest Problem: None")
            return
        newest = max(problems, key=lambda e: e.timestamp)
        if newest.timestamp.year < 2000:
            tile.set_unreadable("Problem records have no usable timestamp")
            self._hud_newest.setText("Newest Problem: No timestamp")
            return
        now = datetime.now(newest.timestamp.tzinfo)
        seconds = (now - newest.timestamp).total_seconds()
        age = ("Just now" if seconds < 60 else f"{int(seconds // 60)} min ago") if seconds < 3600 else (
            f"{int(seconds // 3600)} h ago" if seconds < 86400 else f"{int(seconds // 86400)} d ago")
        tile.set(age if seconds >= 0 else "Future dated", newest.level,
                 tooltip=f"{newest.timestamp:%Y-%m-%d %H:%M:%S %Z}\n{newest.source}\nSelect to inspect the newest problem allowed by the current source filters.",
                 severity="warning" if newest.level.lower() == "warning" else "danger")
        self._hud_newest.setText(f"Newest: {newest.source} ({age})")

    def _unknown_glance(self, reason) -> None:
        for tile in self._glance.tiles:
            tile.set_unreadable(reason)
            status = "Read cancelled" if "cancelled" in reason.lower() else "Could not read"
            tile.sub.setText("Reading..." if reason == "Reading this source" else status)
        self._chips.setEnabled(False)
        self._hud_crit.setText("🔴 Critical: —")
        self._hud_err.setText("🟠 Errors: —")
        self._hud_warn.setText("🟡 Warnings: —")
        self._hud_info.setText("Total: —")

    # -- loading ---------------------------------------------------------
    def load(self, force: bool = False) -> None:
        """Run `loader` on a worker and fill the table with what it returns."""
        if self.loaded and not force:
            return
        self.cancel()

        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._error_banner.clear()
        self._unknown_glance("Reading this source")
        if not self._source:
            self._empty.set_variant("loading", progress_text="Reading this source; restricted records may require administrator rights.")

        worker = Worker(self._loader)
        track(worker)
        worker.signals.progress.connect(lambda value: self._deliver(worker, self._progress.setValue, value))
        worker.signals.result.connect(lambda entries: self._deliver(worker, self._on_result, entries))
        worker.signals.error.connect(lambda error: self._deliver(worker, self._on_error, error))
        worker.signals.cancelled.connect(lambda: self._deliver(worker, self.cancel))
        self._worker = worker
        pool = self._thread_pool
        if pool is None:
            from PyQt6.QtCore import QThreadPool
            pool = QThreadPool.globalInstance()
        pool.start(worker)

    def _deliver(self, worker, callback, *args) -> None:
        if not sip.isdeleted(self) and self._worker is worker:
            callback(*args)

    def cancel(self) -> None:
        was_running = self._worker is not None
        if self._worker is not None:
            retire([self._worker])
            self._worker = None
        if not sip.isdeleted(self):
            self._progress.setVisible(False)
            if not self.loaded:
                self._unknown_glance("Read cancelled or not started; Refresh to retry")
                self._empty.set_variant("empty", reason="Refresh to read this source.")
            elif was_running:
                self._unknown_glance("Refresh cancelled; previous records are still displayed")
                self._chips.setEnabled(bool(self._source))

    def _on_result(self, entries) -> None:
        self._worker = None
        self.loaded = True
        if isinstance(entries, LogReadResult):
            self.set_entries(entries.entries, problems=entries.problems)
        else:
            self.set_entries(entries or [])
        self._update_note()
        self.entries_loaded.emit(self._source)

    def _on_error(self, error_info) -> None:
        self._worker = None
        self.show_error(str(error_info))

    # -- content ---------------------------------------------------------
    def set_entries(self, entries, *, problems=()) -> None:
        if sip.isdeleted(self):
            return
        self._source = list(entries or [])
        self._result_problems = list(problems)
        self._progress.setVisible(False)
        self._update_glance()
        self._update_empty()
        self.refresh_view()

    def _update_empty(self) -> None:
        problems = self._problems()
        title = "No records in this window"
        hint = self._empty_text
        role = "muted"
        if problems:
            title, hint = "Read incomplete", "\n".join(problems)
            role = "statusWarning"
            self._note_btn.setText("Read findings and read limits (incomplete coverage)")
            if not self._source:
                self._unknown_glance(hint)
        else:
            self._note_btn.setText("Read findings and read limits")
        if not problems and self._healthy_empty:
            title = "No crash evidence found"
            hint = "No crash dumps or bugcheck events found in the checked sources. This does not prove the system has never crashed."
            role = "statusSuccess"
        self._empty.title_label.setText(title)
        self._empty.set_variant("empty", reason=hint)
        set_role(self._empty.title_label, role)

    def _problems(self):
        return [*self._result_problems, *(self._read_problems() if self._read_problems else [])]

    def _matches_filter(self, entry: object, query: str) -> bool:
        if not query:
            return True
        # Check for OR operator
        if " or " in query:
            parts = [p.strip() for p in query.split(" or ") if p.strip()]
            return any(self._matches_single_clause(entry, p) for p in parts)
        # Check for AND operator
        if " and " in query:
            parts = [p.strip() for p in query.split(" and ") if p.strip()]
            return all(self._matches_single_clause(entry, p) for p in parts)
        return self._matches_single_clause(entry, query)

    def _matches_time_clause(self, entry: object, clause: str) -> Optional[bool]:
        t = getattr(entry, "timestamp", None)
        if not t:
            if clause.startswith(("last:", "after:", "before:")):
                return False
            return None

        if clause.startswith("last:"):
            spec = clause.split(":", 1)[1].strip().lower()
            now = datetime.now(t.tzinfo)
            delta = None
            if spec.endswith("m"):
                try:
                    delta = timedelta(minutes=int(spec[:-1]))
                except ValueError as ex:
                    logger.debug("Invalid minutes spec '%s': %s", spec, ex)
            elif spec.endswith("h"):
                try:
                    delta = timedelta(hours=int(spec[:-1]))
                except ValueError as ex:
                    logger.debug("Invalid hours spec '%s': %s", spec, ex)
            elif spec.endswith("d"):
                try:
                    delta = timedelta(days=int(spec[:-1]))
                except ValueError as ex:
                    logger.debug("Invalid days spec '%s': %s", spec, ex)
            if delta is not None:
                return t >= (now - delta)
            return False

        if clause.startswith("after:"):
            date_str = clause.split(":", 1)[1].strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(date_str, fmt).replace(tzinfo=t.tzinfo)
                    return t >= dt
                except ValueError as ex:
                    logger.debug("after: date parse '%s' with '%s' failed: %s", date_str, fmt, ex)
                    continue
            return False

        if clause.startswith("before:"):
            date_str = clause.split(":", 1)[1].strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(date_str, fmt).replace(tzinfo=t.tzinfo)
                    if fmt == "%Y-%m-%d %H:%M:%S" and dt.microsecond == 0:
                        dt = dt.replace(microsecond=999999)
                    elif fmt == "%Y-%m-%d":
                        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                    return t <= dt
                except ValueError as ex:
                    logger.debug("before: date parse '%s' with '%s' failed: %s", date_str, fmt, ex)
                    continue
            return False

        return None

    def _matches_field_clause(self, entry: object, clause: str) -> Optional[bool]:
        if ":" not in clause:
            return None
        field, val = clause.split(":", 1)
        field = field.strip().lower()
        val = val.strip().lower()
        if field in ("source", "src"):
            return val in str(getattr(entry, "source", "")).lower()
        if field in ("level", "lvl"):
            return val in str(getattr(entry, "level", "")).lower()
        if field in ("id", "eventid", "event_id"):
            raw = getattr(entry, "raw", {}) or {}
            eid = raw.get("event_id")
            if eid is None or eid == "":
                return False
            eid_str = str(eid).lower()
            if ".." in val:
                parts = val.split("..", 1)
                try:
                    low = int(parts[0])
                    high = int(parts[1])
                    return low <= int(eid) <= high
                except ValueError:
                    return False
            return val == eid_str
        if field in ("message", "msg"):
            return val in str(getattr(entry, "message", "")).lower()
        return None

    def _matches_single_clause(self, entry: object, clause: str) -> bool:
        clause = clause.strip()
        if not clause:
            return True

        # Negation
        if clause.startswith("-"):
            return not self._matches_single_clause(entry, clause[1:])
        if clause.startswith("not "):
            return not self._matches_single_clause(entry, clause[4:])

        # Regex
        if clause.startswith("re:") or clause.startswith("regex:"):
            pattern = clause.split(":", 1)[1]
            try:
                rx = re.compile(pattern, re.IGNORECASE)
                msg = str(getattr(entry, "message", ""))
                src = str(getattr(entry, "source", ""))
                return bool(rx.search(msg) or rx.search(src))
            except Exception:
                return False

        # Time range clauses
        time_res = self._matches_time_clause(entry, clause)
        if time_res is not None:
            return time_res

        # Field-specific clauses
        field_res = self._matches_field_clause(entry, clause)
        if field_res is not None:
            return field_res

        is_case = getattr(self, "_case_btn", None) is not None and self._case_btn.isChecked()
        is_regex = getattr(self, "_regex_btn", None) is not None and self._regex_btn.isChecked()

        if is_regex and not clause.startswith("re:") and not clause.startswith("regex:"):
            try:
                flags = 0 if is_case else re.IGNORECASE
                rx = re.compile(clause, flags)
                return bool(rx.search(str(getattr(entry, "message", ""))) or rx.search(str(getattr(entry, "source", ""))))
            except Exception:
                return False

        if is_case:
            msg = str(getattr(entry, "message", ""))
            src = str(getattr(entry, "source", ""))
            lvl = str(getattr(entry, "level", ""))
            raw = getattr(entry, "raw", {}) or {}
            eid = str(raw.get("event_id", ""))
            return (clause in msg) or (clause in src) or (clause in lvl) or (clause == eid)

        # General substring match across fields
        clause_lower = clause.lower()
        if clause_lower in str(getattr(entry, "message", "")).lower():
            return True
        if clause_lower in str(getattr(entry, "source", "")).lower():
            return True
        if clause_lower in str(getattr(entry, "level", "")).lower():
            return True
        raw = getattr(entry, "raw", {}) or {}
        if str(raw.get("event_id", "")).lower() == clause_lower:
            return True
        return False

    def refresh_view(self) -> None:
        """Re-derive what the table shows from everything that was loaded."""
        shown = self._source
        if self._view_transform is not None:
            try:
                shown = list(self._view_transform(list(self._source)))
            except Exception:
                logger.warning("The view transform failed; showing every entry", exc_info=True)
                shown = self._source
        self._entries = list(shown)
        selected = self._chips.selected()
        if selected and selected != ["all"]:
            self._entries = [e for e in self._entries if e.level.lower() in selected]

        # Bookmarks only filter toggle
        if hasattr(self, "_bookmarks_filter_btn") and self._bookmarks_filter_btn.isChecked():
            self._entries = [e for e in self._entries if self._table.is_bookmarked(e)]

        # In-pane text filter
        is_case = getattr(self, "_case_btn", None) is not None and self._case_btn.isChecked()
        q = (self._filter_text or "").strip()
        if not is_case:
            q = q.lower()
        if q:
            self._entries = [e for e in self._entries if self._matches_filter(e, q)]

        # Duplicate event collapse toggle
        if hasattr(self, "_collapse_dups_btn") and self._collapse_dups_btn.isChecked():
            self._entries = self._collapse_consecutive_duplicates(self._entries)

        self._table.set_entries(self._entries)
        self._explain_rows()
        # The empty page is for "nothing loaded"; a filter that matches nothing
        # must still show the (empty) table and its count.
        self._stack.setCurrentIndex(_PAGE_TABLE if self._source else _PAGE_EMPTY)

    def _explain_rows(self) -> None:
        """Explanations are pre-attached directly in LogTableWidget._make_row."""
        pass

    def set_view_transform(self, transform: Optional[Callable[[list], list]]) -> None:
        """Filter/group what is shown without touching what was loaded."""
        self._view_transform = transform
        self.refresh_view()

    def source_entries(self) -> List[object]:
        """Every entry the loader returned, before any view transform."""
        return list(self._source)

    def set_note(self, text: str) -> None:
        self._note.setText(text or "")
        self._note_btn.setVisible(bool(text))
        self._note.setVisible(bool(text) and self._note_btn.isChecked())
        if text:
            self._note_btn.setToolTip(text)

    def note_text(self) -> str:
        return self._note.text()

    def _update_note(self) -> None:
        if self._summarizer is None:
            return
        try:
            self.set_note(self._summarizer(list(self._source)))
        except Exception:
            logger.warning("The summariser failed", exc_info=True)
            self.set_note("")

    def selected_entry(self):
        return self._table.selected_entry()

    def show_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._error_banner.set_error(message)
        self._unknown_glance(message)
        if not self._source:
            self._empty.title_label.setText("Could not read this source")
            self._empty.set_variant("error", reason=message)

    def clear_error(self) -> None:
        self._error_banner.clear()

    def _on_row_selected(self, entry) -> None:
        extra = ""
        if self._detail_enricher is not None:
            try:
                extra = self._detail_enricher(entry) or ""
            except Exception:
                logger.warning("The detail enricher failed", exc_info=True)
        self._detail.show_entry(entry, extra, all_entries=self._source)
        self.entry_selected.emit(entry)

    # -- introspection, for tests and for get_status_info ----------------
    def entries(self) -> List[object]:
        return list(self._entries)

    def row_count(self) -> int:
        return len(self._entries)

    def is_showing_empty_state(self) -> bool:
        return self._stack.currentIndex() == _PAGE_EMPTY

    def is_showing_error(self) -> bool:
        return not self._error_banner.isHidden()

    def error_text(self) -> str:
        return self._error_banner.text()

    # -- Live Follow / Auto-Tail helpers ---------------------------------
    def _on_tail_toggled(self, checked: bool) -> None:
        self._table.set_follow_mode(checked)

    def _on_table_follow_mode_changed(self, enabled: bool) -> None:
        if self._tail_btn.isChecked() != enabled:
            self._tail_btn.blockSignals(True)
            self._tail_btn.setChecked(enabled)
            self._tail_btn.blockSignals(False)

    def is_tail_mode(self) -> bool:
        return self._tail_btn.isChecked()

    def set_tail_mode(self, enabled: bool) -> None:
        self._tail_btn.setChecked(enabled)

    def append_entries(self, entries: list) -> None:
        """Append new records to the pane and table (streaming / tail mode)."""
        if sip.isdeleted(self) or not entries:
            return
        new_items = list(entries)
        self._source.extend(new_items)
        self._update_glance()
        self._update_note()

        if hasattr(self, "_collapse_dups_btn") and self._collapse_dups_btn.isChecked():
            self.refresh_view()
            return

        matching = []
        for e in new_items:
            selected = self._chips.selected()
            if selected and selected != ["all"] and getattr(e, "level", "").lower() not in selected:
                continue
            if hasattr(self, "_bookmarks_filter_btn") and self._bookmarks_filter_btn.isChecked():
                if not self._table.is_bookmarked(e):
                    continue
            q = (self._filter_text or "").strip()
            is_case = getattr(self, "_case_btn", None) is not None and self._case_btn.isChecked()
            if not is_case:
                q = q.lower()
            if q and not self._matches_filter(e, q):
                continue
            matching.append(e)

        if matching:
            self._entries.extend(matching)
            self._table.append_entries(matching)
        elif self._table.is_follow_mode() and not self._table.is_follow_paused():
            self._table.scroll_to_bottom()

        self._stack.setCurrentIndex(_PAGE_TABLE if self._source else _PAGE_EMPTY)
