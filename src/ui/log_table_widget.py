import csv
import html
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Set

from core.widget_life import widget_is_valid as _widget_is_valid

from PyQt6.QtCore import QEvent, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.types import LogEntry
from core.table_ui import center_header, set_role
from core.semantic_colors import semantic, chrome
from core.casebook import (
    compute_entry_fingerprint,
    get_casebook_manager,
    CasebookManager,
    TAG_ROOT_CAUSE,
    TAG_FOLLOW_UP,
    TAG_BENIGN,
)

logger = logging.getLogger(__name__)

# Row colors by level
LEVEL_COLORS = {
    "Error": QColor(semantic("error")),
    "Warning": QColor(semantic("warning")),
    "Critical": QColor(semantic("error")),
}

_COLUMN_WIDTHS = (150, 230, 70)
_EXTRA_WIDTH = 64


def sanitize_log_text(text: str) -> str:
    """Mask IPv4 addresses, user paths, and domain accounts for safe external sharing."""
    if not text:
        return ""
    text = re.sub(r"([A-Za-z]:\\Users\\)[a-zA-Z0-9_\-\.]+", r"\g<1><REDACTED>", text)
    text = re.sub(r"\b[A-Za-z0-9\-_]{2,15}\\[A-Za-z0-9\-_.]+\b", r"<DOMAIN>\\<USER>", text)
    text = re.sub(r"\b(?!127\.0\.0\.1|0\.0\.0\.0)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", r"10.x.x.x", text)
    text = re.sub(r"S-1-5-21-\d+-\d+-\d+-\d+", r"S-1-5-21-<REDACTED>", text)
    return text


class TimeItem(QStandardItem):
    """QStandardItem for timestamps keeping display text formatted and sorting numerically."""

    def __init__(self, text: str, timestamp: Optional[datetime] = None):
        super().__init__(text)
        self.dt = timestamp

    def data(self, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.EditRole and self.dt:
            try:
                return self.dt.timestamp()
            except Exception as ex:
                logger.debug("Could not convert datetime to timestamp: %s", ex)
        return super().data(role)

    def __lt__(self, other: QStandardItem) -> bool:
        if isinstance(other, TimeItem) and self.dt and other.dt:
            return self.dt < other.dt
        return self.text() < other.text()


SEVERITY_WEIGHTS = {
    "critical": 4,
    "error": 3,
    "warning": 2,
    "warn": 2,
    "information": 1,
    "info": 1,
    "verbose": 0,
    "debug": 0,
}


class SeverityItem(QStandardItem):
    """QStandardItem for log severity levels with operational weight sorting:
    Critical (4) > Error (3) > Warning (2) > Info (1) > Verbose/Debug (0).
    """

    SEVERITY_WEIGHTS = SEVERITY_WEIGHTS

    def __init__(self, level: str, source: str = ""):
        level_str = str(level or "").strip()
        super().__init__(level_str)
        self.level_str = level_str
        self.source_str = str(source or "").strip()
        self.weight = self.SEVERITY_WEIGHTS.get(level_str.lower(), -1)

    def data(self, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.EditRole:
            return self.weight
        return super().data(role)

    def __lt__(self, other: QStandardItem) -> bool:
        w1 = self.weight
        if isinstance(other, SeverityItem):
            w2 = other.weight
            s2 = other.source_str
        else:
            w2 = self.SEVERITY_WEIGHTS.get(str(other.text() or "").strip().lower(), -1)
            s2 = ""
        if w1 != w2:
            return w1 < w2
        if self.source_str and s2 and self.source_str != s2:
            return self.source_str < s2
        return self.text() < other.text()


class EventIdItem(QStandardItem):
    """QStandardItem for Event IDs supporting numeric comparison."""

    def __init__(self, raw_id):
        text = str(raw_id) if raw_id is not None and str(raw_id) != "" else ""
        super().__init__(text)
        self.raw_id = raw_id
        try:
            self.num_val = int(raw_id) if raw_id is not None and str(raw_id).strip() != "" else None
        except (ValueError, TypeError):
            self.num_val = None

    def data(self, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.EditRole and self.num_val is not None:
            return self.num_val
        return super().data(role)

    def __lt__(self, other: QStandardItem) -> bool:
        v1 = self.num_val
        v2 = getattr(other, "num_val", None)
        if v2 is None:
            try:
                txt = other.text().strip()
                v2 = int(txt) if txt != "" else None
            except (ValueError, TypeError):
                v2 = None
        if v1 is not None and v2 is not None:
            return v1 < v2
        if v1 is not None:
            return False
        if v2 is not None:
            return True
        return self.text() < other.text()


class DeltaTimeItem(QStandardItem):
    """QStandardItem for Delta Time (Δt) between log entries with float comparison."""

    def __init__(self, diff_seconds: Optional[float]):
        self.diff_seconds = diff_seconds
        if diff_seconds is None:
            text = "—"
        elif diff_seconds == 0.0:
            text = "0.0s"
        else:
            sign = "+" if diff_seconds > 0 else ""
            if abs(diff_seconds) < 1.0:
                text = f"{sign}{diff_seconds:.2f}s"
            else:
                text = f"{sign}{diff_seconds:.1f}s"
        super().__init__(text)

    def data(self, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.EditRole and self.diff_seconds is not None:
            return self.diff_seconds
        return super().data(role)

    def __lt__(self, other: QStandardItem) -> bool:
        v1 = self.diff_seconds
        v2 = getattr(other, "diff_seconds", None)
        if v2 is None:
            try:
                txt = other.text().strip().rstrip("s").lstrip("+")
                v2 = float(txt) if txt and txt != "—" else None
            except (ValueError, TypeError):
                v2 = None
        if v1 is not None and v2 is not None:
            return v1 < v2
        if v1 is not None:
            return False
        if v2 is not None:
            return True
        return self.text() < other.text()


class ColumnPickerDialog(QDialog):
    """Column picker dialog for toggling visibility of log table columns."""

    def __init__(self, columns: List[str], hidden_map: dict[str, bool], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Customize Columns")
        self.resize(340, 420)
        self.setMinimumWidth(300)

        self._columns = list(columns)
        self._checkboxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("<b>Select Visible Columns</b>")
        layout.addWidget(title)
        desc = QLabel("Check the columns you want to display in the log table:")
        set_role(desc, "muted")
        layout.addWidget(desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(6)
        scroll_layout.setContentsMargins(4, 4, 4, 4)

        for col_name in self._columns:
            cb = QCheckBox(col_name)
            is_hidden = hidden_map.get(col_name, False)
            cb.setChecked(not is_hidden)
            self._checkboxes[col_name] = cb
            scroll_layout.addWidget(cb)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        btn_row.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(self._deselect_all)
        btn_row.addWidget(deselect_all_btn)

        reset_defaults_btn = QPushButton("Reset Defaults")
        reset_defaults_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(reset_defaults_btn)

        layout.addLayout(btn_row)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    def _select_all(self) -> None:
        self.select_all()

    def select_all(self) -> None:
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _deselect_all(self) -> None:
        self.deselect_all()

    def deselect_all(self) -> None:
        for name, cb in self._checkboxes.items():
            if name == "Time":
                cb.setChecked(True)  # Keep Time selected
            else:
                cb.setChecked(False)

    def _reset_defaults(self) -> None:
        self.reset_defaults()

    def reset_defaults(self) -> None:
        default_names = {"Time", "Level", "Source", "Message"}
        for name, cb in self._checkboxes.items():
            cb.setChecked(name in default_names)

    def set_column_state(self, col_name: str, visible: bool) -> None:
        """Sets the checked state for a given column checkbox."""
        for name, cb in self._checkboxes.items():
            if name.lower() == col_name.lower():
                cb.setChecked(bool(visible))
                break

    def get_visibility(self) -> dict[str, bool]:
        """Returns mapping of {column_name: is_visible}."""
        return {col: cb.isChecked() for col, cb in self._checkboxes.items()}


class LogTableWidget(QWidget):
    """Reusable table for displaying LogEntry items with sorting, coloring, bookmarks, find bar, and export."""

    row_selected = pyqtSignal(object)                       # emits LogEntry
    row_double_clicked = pyqtSignal(object)                 # emits LogEntry
    filter_requested = pyqtSignal(str, str)                 # emits (field, value) e.g. ("source", "Service Control Manager")
    zoom_window_requested = pyqtSignal(object, object)       # emits (datetime_start, datetime_end)
    sync_panes_requested = pyqtSignal(object, int)          # emits (anchor_datetime, window_minutes)
    bookmarks_changed = pyqtSignal()
    columns_changed = pyqtSignal()
    follow_mode_changed = pyqtSignal(bool)
    follow_paused_changed = pyqtSignal(bool)

    COLUMNS = ["Time", "Source", "Level", "Message"]
    SUPPORTED_COLUMNS = [
        "Time",
        "Level",
        "Source",
        "Event ID",
        "Category",
        "Message",
        "Delta Time (Δt)",
        "Note",
    ]

    def __init__(self, parent: QWidget = None, extra_columns: list = None,
                 extra_values: Optional[Callable[[LogEntry], list]] = None,
                 copy_formatter: Optional[Callable[[LogEntry], str]] = None,
                 casebook_manager: Optional[CasebookManager] = None,
                 visible_columns: Optional[List[str]] = None):
        super().__init__(parent)
        self._casebook_mgr = casebook_manager or get_casebook_manager()
        self._entries: List[LogEntry] = []
        self._extra_values = extra_values
        self._copy_formatter = copy_formatter
        self._extra_columns = list(extra_columns or [])
        self._n_extra = len(self._extra_columns)

        self._init_column_list(extra_columns)
        self._sized = False
        self._bookmarked_entry_ids: Set[int] = set()
        self._bookmarked_indices: Set[int] = set()
        self._relative_time: bool = False
        self._utc_time: bool = False

        # Live Follow / Auto-Tail state & ergonomics
        self._follow_mode: bool = False
        self._follow_paused: bool = False
        self._new_events_count: int = 0
        self._is_programmatic_scrolling: bool = False
        self._last_scroll_time: float = 0.0
        self._scroll_throttle_ms: int = 40
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._do_auto_scroll)

        # In-table Find state
        self._find_matches: List[int] = []
        self._find_current_idx: int = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._setup_table_view(layout, visible_columns)
        self._setup_find_bar(layout)
        self._setup_shortcuts()
        self._setup_status_bar(layout)

    def _init_column_list(self, extra_columns: list) -> None:
        cols = ["Time", "Source", "Level"]
        cols.extend(self._extra_columns)

        extra_norm = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in self._extra_columns}
        if "id" not in extra_norm and "eventid" not in extra_norm:
            cols.append("Event ID")
        if "category" not in extra_norm:
            cols.append("Category")
        if "message" not in extra_norm and "msg" not in extra_norm:
            cols.append("Message")
        if "deltatime" not in extra_norm and "dt" not in extra_norm:
            cols.append("Delta Time (Δt)")
        if "note" not in extra_norm and "notes" not in extra_norm:
            cols.append("Note")
        self._columns = cols

    def _setup_table_view(self, layout: QVBoxLayout, visible_columns: Optional[List[str]]) -> None:
        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(self._columns)

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        center_header(self._table)
        self._apply_widths()
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.selectionModel().currentRowChanged.connect(self._on_current_changed)
        self._table.doubleClicked.connect(self._on_double_clicked)

        # Initial column visibility
        if visible_columns is not None:
            vis_set = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in visible_columns}
            for idx, col in enumerate(self._columns):
                is_vis = re.sub(r"[^a-z0-9]", "", col.lower()) in vis_set
                self._table.setColumnHidden(idx, not is_vis)
        else:
            default_vis = {"Time", "Source", "Level", "Message", *self._extra_columns}
            for idx, col in enumerate(self._columns):
                self._table.setColumnHidden(idx, col not in default_vis)

        # Scroll position detection for live follow
        v_bar = self._table.verticalScrollBar()
        v_bar.valueChanged.connect(self._on_vertical_scroll)
        v_bar.rangeChanged.connect(self._on_vertical_range_changed)

        # Floating "New events" follow pill
        self._follow_pill = QPushButton("⬇ New events (0) — Click to follow", self._table.viewport())
        set_role(self._follow_pill, "statusInfo")
        self._follow_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._follow_pill.setToolTip("Auto-scrolling is paused because you scrolled up. Click to jump to newest events and resume auto-following.")
        self._follow_pill.clicked.connect(self.resume_follow)
        self._follow_pill.setVisible(False)
        self.follow_pill = self._follow_pill
        self._table.viewport().installEventFilter(self)

        # Table Context menu
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        # Header Context menu (Column visibility)
        header = self._table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_context_menu)

        layout.addWidget(self._table)
        self._apply_widths()

    def _setup_find_bar(self, layout: QVBoxLayout) -> None:
        self._find_bar = QWidget()
        self._find_bar.setVisible(False)
        find_layout = QHBoxLayout(self._find_bar)
        find_layout.setContentsMargins(4, 2, 4, 2)
        find_layout.setSpacing(6)

        find_lbl = QLabel("Find in view:")
        find_layout.addWidget(find_lbl)

        self._find_input = QLineEdit()
        self._find_input.setPlaceholderText("Search text in table rows...")
        self._find_input.setToolTip("Search text across loaded table rows")
        self._find_input.setMaximumWidth(250)
        self._find_input.textChanged.connect(self._on_find_text_changed)
        self._find_input.returnPressed.connect(self._find_next)
        find_layout.addWidget(self._find_input)

        self._find_prev_btn = QPushButton("◀ Prev")
        self._find_prev_btn.setToolTip("Find previous match (Shift+F3)")
        self._find_prev_btn.setMaximumWidth(60)
        self._find_prev_btn.clicked.connect(self._find_prev)
        find_layout.addWidget(self._find_prev_btn)

        self._find_next_btn = QPushButton("Next ▶")
        self._find_next_btn.setToolTip("Find next match (F3)")
        self._find_next_btn.setMaximumWidth(60)
        self._find_next_btn.clicked.connect(self._find_next)
        find_layout.addWidget(self._find_next_btn)

        self._find_matches_lbl = QLabel("0 matches")
        find_layout.addWidget(self._find_matches_lbl)

        find_layout.addStretch()

        self._find_close_btn = QPushButton("✕")
        self._find_close_btn.setMaximumWidth(26)
        self._find_close_btn.setToolTip("Close find bar (Esc)")
        self._find_close_btn.clicked.connect(self._hide_find_bar)
        find_layout.addWidget(self._find_close_btn)

        layout.addWidget(self._find_bar)

    def _setup_shortcuts(self) -> None:
        copy_shortcut = QShortcut(QKeySequence("Ctrl+C"), self)
        copy_shortcut.activated.connect(self.copy_selected_to_clipboard)

        copy_msg_shortcut = QShortcut(QKeySequence("Ctrl+Shift+C"), self)
        copy_msg_shortcut.activated.connect(self._copy_selected_message_only)

        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        find_shortcut.activated.connect(self._show_find_bar)

        esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        esc_shortcut.activated.connect(self._on_escape_pressed)

        home_shortcut = QShortcut(QKeySequence("Home"), self._table)
        home_shortcut.activated.connect(self.scroll_to_top)

        end_shortcut = QShortcut(QKeySequence("End"), self._table)
        end_shortcut.activated.connect(self.scroll_to_bottom)

        space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self._table)
        space_shortcut.activated.connect(self._toggle_bookmark_selected)

        casebook_shortcut = QShortcut(QKeySequence("Ctrl+B"), self._table)
        casebook_shortcut.activated.connect(self._open_casebook_dialog)

    def _setup_status_bar(self, layout: QVBoxLayout) -> None:
        status_bar = QHBoxLayout()
        status_bar.setContentsMargins(2, 2, 2, 2)
        status_bar.setSpacing(6)

        self._status = QLabel("0 entries")
        status_bar.addWidget(self._status)
        status_bar.addStretch()

        self._columns_btn = QPushButton("Columns...")
        self._columns_btn.setObjectName("columnsBtn")
        self._columns_btn.setToolTip("Customize visible table columns (Time, Level, Source, Event ID, Category, Message, etc.)")
        self._columns_btn.clicked.connect(self.open_column_picker_dialog)
        status_bar.addWidget(self._columns_btn)

        self._jump_top_btn = QPushButton("▲ Top")
        self._jump_top_btn.setMaximumWidth(55)
        self._jump_top_btn.setToolTip("Scroll to top of log view (Home)")
        self._jump_top_btn.clicked.connect(self.scroll_to_top)
        status_bar.addWidget(self._jump_top_btn)

        self._jump_bottom_btn = QPushButton("▼ End")
        self._jump_bottom_btn.setMaximumWidth(55)
        self._jump_bottom_btn.setToolTip("Scroll to bottom of log view (End)")
        self._jump_bottom_btn.clicked.connect(self.scroll_to_bottom)
        status_bar.addWidget(self._jump_bottom_btn)

        layout.addLayout(status_bar)

    def _show_find_bar(self) -> None:
        self._find_bar.setVisible(True)
        self._find_input.setFocus()
        self._find_input.selectAll()

    def _hide_find_bar(self) -> None:
        self._find_bar.setVisible(False)
        self._find_matches = []
        self._find_current_idx = -1
        self._table.setFocus()

    def _on_escape_pressed(self) -> None:
        if self._find_bar.isVisible():
            self._hide_find_bar()

    def _on_find_text_changed(self, text: str) -> None:
        q = text.strip().lower()
        if not q:
            self._find_matches = []
            self._find_current_idx = -1
            self._find_matches_lbl.setText("0 matches")
            return

        matches = []
        for r in range(self._model.rowCount()):
            row_text = []
            for c in range(self._model.columnCount()):
                item = self._model.item(r, c)
                if item:
                    row_text.append(item.text().lower())
            if any(q in t for t in row_text):
                matches.append(r)

        self._find_matches = matches
        if matches:
            self._find_current_idx = 0
            self._find_matches_lbl.setText(f"1 of {len(matches)}")
            self._select_table_row(matches[0])
        else:
            self._find_current_idx = -1
            self._find_matches_lbl.setText("0 matches")

    def _find_next(self) -> None:
        if not self._find_matches:
            return
        self._find_current_idx = (self._find_current_idx + 1) % len(self._find_matches)
        row = self._find_matches[self._find_current_idx]
        self._find_matches_lbl.setText(f"{self._find_current_idx + 1} of {len(self._find_matches)}")
        self._select_table_row(row)

    def _find_prev(self) -> None:
        if not self._find_matches:
            return
        self._find_current_idx = (self._find_current_idx - 1) % len(self._find_matches)
        row = self._find_matches[self._find_current_idx]
        self._find_matches_lbl.setText(f"{self._find_current_idx + 1} of {len(self._find_matches)}")
        self._select_table_row(row)

    def _select_table_row(self, row: int) -> None:
        idx = self._model.index(row, 0)
        self._table.setCurrentIndex(idx)
        self._table.scrollTo(idx)

    def _show_header_context_menu(self, pos) -> None:
        menu = QMenu(self)
        title_act = menu.addAction("Visible Columns:")
        title_act.setEnabled(False)

        for col, col_name in enumerate(self._columns):
            is_hidden = self._table.isColumnHidden(col)
            act = menu.addAction(f"✓ {col_name}" if not is_hidden else f"   {col_name}")
            act.triggered.connect(lambda _checked, c=col: self._toggle_column_from_menu(c))

        menu.addSeparator()
        picker_act = menu.addAction("Columns... (Customize)")
        picker_act.triggered.connect(self.open_column_picker_dialog)
        reset_act = menu.addAction("Reset Default Columns")
        reset_act.triggered.connect(self._reset_columns_visibility)
        menu.exec(self._table.horizontalHeader().mapToGlobal(pos))

    def _toggle_column_from_menu(self, col: int) -> None:
        self._table.setColumnHidden(col, not self._table.isColumnHidden(col))
        self.columns_changed.emit()

    def _reset_columns_visibility(self) -> None:
        default_vis = {"Time", "Source", "Level", "Message", *getattr(self, "_extra_columns", [])}
        for idx, col in enumerate(self._columns):
            self._table.setColumnHidden(idx, col not in default_vis)
        self._apply_widths()
        self.columns_changed.emit()

    def _apply_widths(self) -> None:
        width_defaults = {
            "Time": 150,
            "Source": 170,
            "Level": 75,
            "Event ID": 75,
            "Category": 120,
            "Message": 350,
            "Delta Time (Δt)": 85,
            "Note": 150,
        }
        for col, name in enumerate(self._columns):
            w = width_defaults.get(name, _EXTRA_WIDTH)
            self._table.setColumnWidth(col, w)

    def _find_column_index(self, name_or_alias: str) -> Optional[int]:
        if not name_or_alias:
            return None
        target = str(name_or_alias).strip()
        for idx, col in enumerate(self._columns):
            if col == target:
                return idx
        target_lower = target.lower()
        for idx, col in enumerate(self._columns):
            if col.lower() == target_lower:
                return idx
        norm_target = re.sub(r"[^a-z0-9]", "", target_lower)
        alias_map = {
            "time": "Time",
            "timestamp": "Time",
            "date": "Time",
            "level": "Level",
            "severity": "Level",
            "source": "Source",
            "provider": "Source",
            "eventid": "Event ID",
            "id": "Event ID",
            "event": "Event ID",
            "category": "Category",
            "taskcategory": "Category",
            "message": "Message",
            "msg": "Message",
            "deltatime": "Delta Time (Δt)",
            "deltatimet": "Delta Time (Δt)",
            "delta": "Delta Time (Δt)",
            "dt": "Delta Time (Δt)",
            "note": "Note",
            "notes": "Note",
            "usernote": "Note",
        }
        resolved = alias_map.get(norm_target)
        if resolved:
            for idx, col in enumerate(self._columns):
                if col.lower() == resolved.lower():
                    return idx
        for idx, col in enumerate(self._columns):
            norm_col = re.sub(r"[^a-z0-9]", "", col.lower())
            if norm_target in norm_col or norm_col in norm_target:
                return idx
        return None

    def set_column_visibility(self, column: str, visible: bool) -> bool:
        idx = self._find_column_index(column)
        if idx is not None:
            self._table.setColumnHidden(idx, not visible)
            self.columns_changed.emit()
            return True
        return False

    def set_column_hidden(self, column: str, hidden: bool) -> bool:
        return self.set_column_visibility(column, not hidden)

    def is_column_visible(self, column: str) -> bool:
        idx = self._find_column_index(column)
        if idx is not None:
            return not self._table.isColumnHidden(idx)
        return False

    def is_column_hidden(self, column: str) -> bool:
        return not self.is_column_visible(column)

    def get_visible_columns(self) -> List[str]:
        return [col for idx, col in enumerate(self._columns) if not self._table.isColumnHidden(idx)]

    def get_all_columns(self) -> List[str]:
        return list(self._columns)

    def get_column_index(self, name_or_alias: str) -> Optional[int]:
        """Returns the model column index for the given name or alias."""
        return self._find_column_index(name_or_alias)

    def sort_by_column(self, column_name: str, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> bool:
        idx = self._find_column_index(column_name)
        if idx is not None:
            self._table.sortByColumn(idx, order)
            return True
        return False

    def _apply_column_picker(self, dlg: ColumnPickerDialog) -> None:
        vis_map = dlg.get_visibility()
        for col_name, visible in vis_map.items():
            idx = self._find_column_index(col_name)
            if idx is not None:
                self._table.setColumnHidden(idx, not visible)
        self.columns_changed.emit()

    def create_column_picker_dialog(self) -> ColumnPickerDialog:
        hidden_map = {col: self._table.isColumnHidden(i) for i, col in enumerate(self._columns)}
        dlg = ColumnPickerDialog(self._columns, hidden_map, self)
        dlg.accepted.connect(lambda: self._apply_column_picker(dlg))
        return dlg

    def open_column_picker_dialog(self) -> bool:
        dlg = self.create_column_picker_dialog()
        return dlg.exec() == QDialog.DialogCode.Accepted

    def add_entries(self, entries: List[LogEntry]) -> None:
        """Alias for append_entries for backwards compatibility."""
        self.append_entries(entries)

    def eventFilter(self, obj, event):
        if hasattr(self, "_table") and obj is self._table.viewport():
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                self._update_pill_geometry()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_pill_geometry()

    def _update_pill_geometry(self) -> None:
        if not hasattr(self, "_follow_pill") or not hasattr(self, "_table"):
            return
        vp = self._table.viewport()
        if not vp:
            return
        vw = vp.width()
        vh = vp.height()
        text_w = self._follow_pill.fontMetrics().horizontalAdvance(self._follow_pill.text()) + 36
        pw = min(max(text_w, 220), max(60, vw - 20))
        ph = 30
        px = max(10, (vw - pw) // 2)
        py = max(5, vh - ph - 10)
        self._follow_pill.setGeometry(px, py, pw, ph)
        self._follow_pill.raise_()

    def is_at_bottom(self) -> bool:
        """Check if the table viewport is currently scrolled to the bottom."""
        v_bar = self._table.verticalScrollBar()
        if not v_bar or v_bar.maximum() <= 0:
            return True
        return v_bar.value() >= v_bar.maximum() - 1

    def _on_vertical_scroll(self, value: int) -> None:
        if self._is_programmatic_scrolling:
            return
        if not self._follow_mode:
            return
        if self.is_at_bottom():
            if self._follow_paused:
                self.resume_follow()
        else:
            if not self._follow_paused:
                self._follow_paused = True
                self._update_follow_pill()
                self.follow_paused_changed.emit(True)

    def _on_vertical_range_changed(self, min_val: int, max_val: int) -> None:
        if self._follow_mode and not self._follow_paused:
            self._schedule_auto_scroll()
        if hasattr(self, "_follow_pill") and self._follow_pill.isVisible():
            self._update_pill_geometry()

    def set_follow_mode(self, enabled: bool) -> None:
        """Enable or disable Auto-Tail / Live Follow mode."""
        self._follow_mode = bool(enabled)
        self._follow_paused = False
        self._new_events_count = 0
        if self._scroll_timer.isActive():
            self._scroll_timer.stop()
        if hasattr(self, "_follow_pill"):
            self._follow_pill.setVisible(False)
        if self._follow_mode:
            self.scroll_to_bottom()
        self.follow_mode_changed.emit(self._follow_mode)

    def is_follow_mode(self) -> bool:
        return self._follow_mode

    def is_follow_paused(self) -> bool:
        return self._follow_paused

    def follow_new_events_count(self) -> int:
        return self._new_events_count

    def get_follow_pill(self) -> QPushButton:
        return self._follow_pill

    def resume_follow(self) -> None:
        """Clear counter, resume auto-scrolling, and scroll to bottom."""
        self._follow_paused = False
        self._new_events_count = 0
        if hasattr(self, "_follow_pill"):
            self._follow_pill.setVisible(False)
        self.scroll_to_bottom()
        self.follow_paused_changed.emit(False)

    def _update_follow_pill(self) -> None:
        if not hasattr(self, "_follow_pill"):
            return
        self._follow_pill.setText(f"⬇ New events ({self._new_events_count}) — Click to follow")
        self._update_pill_geometry()
        self._follow_pill.setVisible(self._follow_paused)

    def _schedule_auto_scroll(self) -> None:
        """Throttle/debounce auto-scrolling to prevent UI freezes during rapid bursts."""
        if not self._follow_mode or self._follow_paused:
            return
        now = time.monotonic()
        elapsed = now - self._last_scroll_time
        throttle_sec = self._scroll_throttle_ms / 1000.0

        if elapsed >= throttle_sec:
            self._do_auto_scroll()
        else:
            if not self._scroll_timer.isActive():
                remaining_ms = max(1, int((throttle_sec - elapsed) * 1000))
                self._scroll_timer.start(remaining_ms)

    def _do_auto_scroll(self) -> None:
        if not self._follow_mode or self._follow_paused:
            return
        self.scroll_to_bottom()
        self._last_scroll_time = time.monotonic()

    def flush_auto_scroll(self) -> None:
        """Immediately execute any pending debounced auto-scroll."""
        if self._scroll_timer.isActive():
            self._scroll_timer.stop()
        self._do_auto_scroll()

    def set_entries(self, entries: List[LogEntry]) -> None:
        """Replace all entries in the table."""
        if not _widget_is_valid(self._status):
            return
        prev_count = len(self._entries)
        prev_scroll = self._table.verticalScrollBar().value()
        self._entries = list(entries)
        self._sync_with_casebook(self._entries)
        self._fill(self._entries)
        self._update_status_text()
        if self._find_bar.isVisible() and self._find_input.text():
            self._on_find_text_changed(self._find_input.text())

        if self._follow_mode:
            if self._follow_paused:
                if len(self._entries) > prev_count:
                    self._new_events_count += (len(self._entries) - prev_count)
                    self._update_follow_pill()
                was_prog = self._is_programmatic_scrolling
                self._is_programmatic_scrolling = True
                try:
                    self._table.verticalScrollBar().setValue(prev_scroll)
                finally:
                    self._is_programmatic_scrolling = was_prog
            else:
                self._schedule_auto_scroll()

    def append_entries(self, entries: List[LogEntry]) -> None:
        """Add entries to existing table data."""
        if not _widget_is_valid(self._status):
            return
        if not entries:
            return
        self._entries.extend(entries)
        self._sync_with_casebook(entries)
        self._fill(entries, replace=False)
        self._update_status_text()

        if self._follow_mode:
            if self._follow_paused:
                self._new_events_count += len(entries)
                self._update_follow_pill()
            else:
                self._schedule_auto_scroll()

    def _sync_with_casebook(self, entries: List[LogEntry]) -> None:
        mgr = getattr(self, "_casebook_mgr", None) or get_casebook_manager()
        stored = mgr.get_entries_by_fingerprint()
        if not stored:
            return
        for entry in entries:
            fp = compute_entry_fingerprint(entry)
            if fp in stored:
                cb = stored[fp]
                if cb.bookmarked:
                    self._bookmarked_entry_ids.add(id(entry))
                if cb.note:
                    if entry.raw is None:
                        entry.raw = {}
                    entry.raw["_user_note"] = cb.note
                if cb.tags:
                    if entry.raw is None:
                        entry.raw = {}
                    entry.raw["_user_tags"] = list(cb.tags)
                if entry.raw is not None:
                    entry.raw["_casebook_id"] = cb.id

    def scroll_to_top(self) -> None:
        """Scroll to the first row in the table."""
        if self._model.rowCount() > 0:
            idx = self._model.index(0, 0)
            self._table.setCurrentIndex(idx)
            self._table.scrollTo(idx)

    def scroll_to_bottom(self) -> None:
        """Scroll to the last row in the table."""
        if self._follow_mode and self._follow_paused:
            self._follow_paused = False
            self._new_events_count = 0
            if hasattr(self, "_follow_pill"):
                self._follow_pill.setVisible(False)
            self.follow_paused_changed.emit(False)

        count = self._model.rowCount()
        if count > 0:
            was_prog = self._is_programmatic_scrolling
            self._is_programmatic_scrolling = True
            try:
                idx = self._model.index(count - 1, 0)
                self._table.setCurrentIndex(idx)
                self._table.scrollTo(idx)
                v_bar = self._table.verticalScrollBar()
                if v_bar:
                    v_bar.setValue(v_bar.maximum())
            finally:
                self._is_programmatic_scrolling = was_prog

    def set_word_wrap(self, wrap: bool) -> None:
        """Toggle multi-line cell text wrapping in the table view."""
        self._table.setWordWrap(wrap)
        if wrap:
            self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        else:
            self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            self._table.verticalHeader().setMinimumSectionSize(22)
            self._table.verticalHeader().setDefaultSectionSize(26)

    def _update_status_text(self) -> None:
        selected = self.selected_entry()
        b_count = len(self._bookmarked_entry_ids)
        b_info = f" | 📌 {b_count} bookmarked" if b_count else ""
        delta_info = ""
        row_info = ""
        rows = self._table.selectionModel().selectedRows()
        if rows:
            curr_row = rows[0].row()
            if curr_row > 0:
                prev_item = self._model.item(curr_row - 1, 0)
                if prev_item is not None:
                    prev_entry = prev_item.data(Qt.ItemDataRole.UserRole)
                    if prev_entry and selected and getattr(prev_entry, "timestamp", None) and getattr(selected, "timestamp", None):
                        diff_sec = (selected.timestamp - prev_entry.timestamp).total_seconds()
                        sign = "+" if diff_sec >= 0 else ""
                        delta_info = f" | Δt: {sign}{diff_sec:.1f}s"
            row_info = f" | Row {curr_row + 1} of {len(self._entries)}"

        if selected is not None:
            source = selected.source or "Unknown"
            self._status.setText(f"{len(self._entries)} entries{b_info}{row_info}{delta_info} | Selected: [{selected.level}] {source}")
        else:
            self._status.setText(f"{len(self._entries)} entries{b_info}")

    def _fill(self, entries: List[LogEntry], replace: bool = True) -> None:
        header = self._table.horizontalHeader()
        section = header.sortIndicatorSection()
        order = header.sortIndicatorOrder()
        was_sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        try:
            if replace:
                self._model.removeRows(0, self._model.rowCount())
                prev_entry = None
            else:
                prev_idx = len(self._entries) - len(entries) - 1
                prev_entry = self._entries[prev_idx] if 0 <= prev_idx < len(self._entries) else None

            for i, entry in enumerate(entries):
                if prev_entry and getattr(prev_entry, "timestamp", None) and getattr(entry, "timestamp", None):
                    diff_sec = (entry.timestamp - prev_entry.timestamp).total_seconds()
                else:
                    diff_sec = 0.0 if (i == 0 and replace) else None
                self._model.appendRow(self._make_row(entry, diff_seconds=diff_sec))
                prev_entry = entry
        finally:
            self._table.setSortingEnabled(was_sorting)
            if was_sorting:
                self._model.sort(section, order)

    def clear(self) -> None:
        if not _widget_is_valid(self._status):
            return
        self._model.removeRows(0, self._model.rowCount())
        self._entries.clear()
        self._bookmarked_entry_ids.clear()
        self._bookmarked_indices.clear()
        self._new_events_count = 0
        self._follow_paused = False
        if self._scroll_timer.isActive():
            self._scroll_timer.stop()
        if hasattr(self, "_follow_pill"):
            self._follow_pill.setVisible(False)
        self._status.setText("0 entries")

    def get_entries(self) -> List[LogEntry]:
        return list(self._entries)

    def is_bookmarked(self, entry: LogEntry) -> bool:
        if entry is None:
            return False
        if id(entry) in self._bookmarked_entry_ids:
            return True
        mgr = getattr(self, "_casebook_mgr", None) or get_casebook_manager()
        return mgr.is_bookmarked(compute_entry_fingerprint(entry))

    def set_time_mode(self, relative: Optional[bool] = None, utc: Optional[bool] = None) -> None:
        """Switch timestamp presentation between local absolute, relative, and UTC."""
        if relative is not None:
            self._relative_time = relative
        if utc is not None:
            self._utc_time = utc
        for row in range(self._model.rowCount()):
            item = self._model.item(row, 0)
            if item is not None:
                entry = item.data(Qt.ItemDataRole.UserRole)
                if entry is not None and getattr(entry, "timestamp", None):
                    prefix = "★ " if id(entry) in self._bookmarked_entry_ids else ""
                    item.setText(prefix + self._format_time(entry.timestamp))

    def _format_time(self, dt: datetime) -> str:
        if self._utc_time and dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        if self._relative_time:
            now = datetime.now(dt.tzinfo)
            secs = (now - dt).total_seconds()
            if secs < 60:
                return "Just now"
            if secs < 3600:
                return f"{int(secs // 60)}m ago"
            if secs < 86400:
                return f"{int(secs // 3600)}h ago"
            return f"{int(secs // 86400)}d ago"
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _make_row(self, entry: LogEntry, diff_seconds: Optional[float] = None) -> list:
        raw = getattr(entry, "raw", {}) or {}
        tags = raw.get("_user_tags", [])
        tag_prefix = ""
        if TAG_ROOT_CAUSE in tags:
            tag_prefix = "🎯 "
        elif TAG_FOLLOW_UP in tags:
            tag_prefix = "📌 "
        elif TAG_BENIGN in tags:
            tag_prefix = "🟢 "
        note_text = str(raw.get("_user_note") or getattr(entry, "note", "") or "")
        note_prefix = "📝 " if note_text else ""
        bm_prefix = "★ " if id(entry) in self._bookmarked_entry_ids else ""
        time_item = TimeItem(
            tag_prefix + note_prefix + bm_prefix + self._format_time(entry.timestamp),
            timestamp=getattr(entry, "timestamp", None),
        )
        time_item.setData(entry, Qt.ItemDataRole.UserRole)

        # Boot / Shutdown markers
        eid = raw.get("event_id") if "event_id" in raw else raw.get("id")
        src_prefix = ""
        try:
            eid_int = int(eid) if eid is not None else None
            if eid_int in (12, 6005):
                src_prefix = "🚀 "
            elif eid_int in (13, 6006, 1074):
                src_prefix = "🛑 "
            elif eid_int in (41, 6008):
                src_prefix = "💥 "
        except (ValueError, TypeError) as ex:
            logger.debug("Non-integer event_id '%s': %s", eid, ex)

        source_item = QStandardItem(src_prefix + (entry.source or ""))
        level_item = SeverityItem(entry.level, source=entry.source or "")
        one_line = " ".join((entry.message or "").split())
        try:
            from modules.log_viewer.error_codes import find_codes, describe
            detected_codes = find_codes(one_line)
            if detected_codes:
                first_c = detected_codes[0]
                c_desc = describe(first_c)
                if c_desc:
                    hex_tag = f"0x{first_c:08X}" if first_c > 0xFFFF else str(first_c)
                    if f"[{hex_tag}:" not in one_line:
                        one_line = f"[{hex_tag}: {c_desc}] {one_line}"
        except Exception as ex:
            logger.debug("Error code lookup failed for '%s': %s", one_line[:40], ex)
        msg_item = QStandardItem(one_line[:500])  # Truncate long messages

        cat_text = str(raw.get("category") or raw.get("task_category") or "")
        category_item = QStandardItem(cat_text)
        event_id_item = EventIdItem(eid)
        delta_item = DeltaTimeItem(diff_seconds)
        note_item = QStandardItem(note_text)

        items_by_name = {
            "Time": time_item,
            "Source": source_item,
            "Level": level_item,
            "Message": msg_item,
            "Event ID": event_id_item,
            "Category": category_item,
            "Delta Time (Δt)": delta_item,
            "Note": note_item,
        }
        if self._n_extra and self._extra_columns:
            extra_items = self._extra_items(entry)
            for c_name, itm in zip(self._extra_columns, extra_items):
                items_by_name[c_name] = itm

        row = [items_by_name.get(col, QStandardItem("")) for col in self._columns]

        # Pre-attach contextual help tooltip directly on row creation
        try:
            from modules.event_viewer.help_text import event_help
            tip = event_help(raw.get("provider") or entry.source, eid)
            tip = (tip + "\n" if tip else "") + str(entry.message or "")
            tip_html = "<qt>" + html.escape(tip).replace("\n", "<br>") + "</qt>"
            for item in row:
                item.setToolTip(tip_html)
        except Exception as ex:
            logger.debug("Failed building event help tooltip for '%s': %s", entry.source, ex)

        # Color coding
        bg = LEVEL_COLORS.get(entry.level)
        if bg:
            brush = QBrush(bg)
            for item in row:
                item.setBackground(brush)
                item.setForeground(QBrush(QColor("white")))

        for item in row:
            item.setEditable(False)

        return row

    def _extra_items(self, entry: LogEntry) -> list:
        if not self._n_extra:
            return []
        values = list(self._extra_values(entry)) if self._extra_values else []
        values += [""] * (self._n_extra - len(values))
        items = []
        for value in values[:self._n_extra]:
            item = QStandardItem()
            if isinstance(value, int):
                item.setData(value, Qt.ItemDataRole.EditRole)
            else:
                item.setText(str(value))
            items.append(item)
        return items

    def _entry_at(self, index) -> Optional[LogEntry]:
        if not index.isValid():
            return None
        first = self._model.item(index.row(), 0)
        entry = first.data(Qt.ItemDataRole.UserRole) if first is not None else None
        return entry if isinstance(entry, LogEntry) else None

    def selected_entry(self) -> Optional[LogEntry]:
        sm = self._table.selectionModel()
        if sm:
            rows = sm.selectedRows()
            if rows:
                return self._entry_at(rows[0])
            idx = self._table.currentIndex()
            if idx.isValid():
                return self._entry_at(idx)
            indexes = sm.selectedIndexes()
            if indexes:
                return self._entry_at(indexes[0])
        return None

    def _on_current_changed(self, current, _previous):
        entry = self._entry_at(current)
        if entry is not None:
            self._update_status_text()
            self.row_selected.emit(entry)

    def _on_double_clicked(self, index):
        entry = self._entry_at(index)
        if entry is not None:
            self.row_double_clicked.emit(entry)

    def _extra_text(self, entry: LogEntry) -> list:
        return [str(v) for v in (self._extra_values(entry) if self._extra_values else [])]

    def _open_casebook_dialog(self) -> None:
        """Open or show the casebook management dialog."""
        from ui.investigation_casebook_dialog import InvestigationCasebookDialog
        mgr = getattr(self, "_casebook_mgr", None) or get_casebook_manager()
        dlg = InvestigationCasebookDialog(manager=mgr, parent=self)
        dlg.exec()
        if self._entries:
            self._sync_with_casebook(self._entries)
            self._fill(self._entries)
            self._update_status_text()
            self.bookmarks_changed.emit()

    def _toggle_bookmark_selected(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        entry = self.selected_entry()
        if not entry:
            return

        eid = id(entry)
        first_item = self._model.item(row, 0)
        mgr = getattr(self, "_casebook_mgr", None) or get_casebook_manager()
        fp = compute_entry_fingerprint(entry)

        if eid in self._bookmarked_entry_ids:
            self._bookmarked_entry_ids.remove(eid)
            self._bookmarked_indices.discard(row)
            has_note = bool((entry.raw or {}).get("_user_note"))
            has_tags = bool((entry.raw or {}).get("_user_tags"))
            if not has_note and not has_tags:
                mgr.remove_by_fingerprint(fp)
            else:
                existing = mgr.get_by_fingerprint(fp)
                if existing:
                    existing.bookmarked = False
                    mgr.add_or_update(existing)
        else:
            self._bookmarked_entry_ids.add(eid)
            self._bookmarked_indices.add(row)
            note = (entry.raw or {}).get("_user_note", "")
            tags = (entry.raw or {}).get("_user_tags", [])
            channel = (entry.raw or {}).get("log_name") or (entry.raw or {}).get("channel") or ""
            mgr.add_from_log_entry(entry, note=note, tags=tags, channel=channel, bookmarked=True)

        raw = getattr(entry, "raw", {}) or {}
        tags = raw.get("_user_tags", [])
        tag_p = "🎯 " if TAG_ROOT_CAUSE in tags else ("📌 " if TAG_FOLLOW_UP in tags else ("🟢 " if TAG_BENIGN in tags else ""))
        note_p = "📝 " if "_user_note" in raw else ""
        bm_p = "★ " if eid in self._bookmarked_entry_ids else ""
        if first_item:
            first_item.setText(tag_p + note_p + bm_p + self._format_time(entry.timestamp))
        self._update_status_text()
        self.bookmarks_changed.emit()

    def _add_or_edit_note_selected(self) -> None:
        entry = self.selected_entry()
        if not entry:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        current_note = (entry.raw or {}).get("_user_note", "")
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Event Note", "Enter note or annotation for this record:", text=current_note)
        if ok:
            text = text.strip()
            if entry.raw is None:
                entry.raw = {}
            mgr = getattr(self, "_casebook_mgr", None) or get_casebook_manager()
            fp = compute_entry_fingerprint(entry)
            is_bm = id(entry) in self._bookmarked_entry_ids

            if text:
                entry.raw["_user_note"] = text
                tags = entry.raw.get("_user_tags", [])
                channel = entry.raw.get("log_name") or entry.raw.get("channel") or ""
                mgr.add_from_log_entry(entry, note=text, tags=tags, channel=channel, bookmarked=is_bm)
            else:
                entry.raw.pop("_user_note", None)
                has_tags = bool(entry.raw.get("_user_tags"))
                if not is_bm and not has_tags:
                    mgr.remove_by_fingerprint(fp)
                else:
                    existing = mgr.get_by_fingerprint(fp)
                    if existing:
                        existing.note = ""
                        mgr.add_or_update(existing)

            item = self._model.item(row, 0)
            if item:
                tags = entry.raw.get("_user_tags", [])
                tag_p = "🎯 " if TAG_ROOT_CAUSE in tags else ("📌 " if TAG_FOLLOW_UP in tags else ("🟢 " if TAG_BENIGN in tags else ""))
                note_p = "📝 " if "_user_note" in entry.raw else ""
                bm_p = "★ " if is_bm else ""
                item.setText(tag_p + note_p + bm_p + self._format_time(entry.timestamp))
            note_col = self._find_column_index("Note")
            if note_col is not None:
                n_item = self._model.item(row, note_col)
                if n_item:
                    n_item.setText(text)
            self._update_status_text()
            self.row_selected.emit(entry)

    def _toggle_tag_selected(self, tag: str) -> None:
        entry = self.selected_entry()
        if not entry:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if entry.raw is None:
            entry.raw = {}
        tags = list(entry.raw.get("_user_tags", []))
        if tag in tags:
            tags.remove(tag)
        else:
            tags.append(tag)
        entry.raw["_user_tags"] = tags

        mgr = getattr(self, "_casebook_mgr", None) or get_casebook_manager()
        is_bm = id(entry) in self._bookmarked_entry_ids
        note = entry.raw.get("_user_note", "")
        channel = entry.raw.get("log_name") or entry.raw.get("channel") or ""
        mgr.add_from_log_entry(entry, note=note, tags=tags, channel=channel, bookmarked=is_bm)

        item = self._model.item(row, 0)
        if item:
            tag_p = "🎯 " if TAG_ROOT_CAUSE in tags else ("📌 " if TAG_FOLLOW_UP in tags else ("🟢 " if TAG_BENIGN in tags else ""))
            note_p = "📝 " if "_user_note" in entry.raw else ""
            bm_p = "★ " if is_bm else ""
            item.setText(tag_p + note_p + bm_p + self._format_time(entry.timestamp))
        self._update_status_text()
        self.row_selected.emit(entry)

    def copy_as_powershell_query(self) -> None:
        entry = self.selected_entry()
        if not entry:
            return
        log_name = (entry.raw or {}).get("log_name") or "System"
        provider = entry.source or ""
        eid = (entry.raw or {}).get("event_id")
        parts = [f"LogName='{log_name}'"]
        if provider:
            parts.append(f"ProviderName='{provider}'")
        if eid:
            parts.append(f"Id={eid}")
        filter_str = "; ".join(parts)
        cmd = f"Get-WinEvent -FilterHashtable @{{{filter_str}}} -MaxEvents 50 | Format-Table TimeCreated, Id, LevelDisplayName, Message -AutoSize"
        QApplication.clipboard().setText(cmd)

    def trace_burst_trigger(self) -> None:
        """Find the earliest error in the current error burst (likely root cause)."""
        selected = self.selected_entry()
        if not selected or selected.level.lower() not in ("error", "critical"):
            return
        t_sel = selected.timestamp
        candidates = []
        for row in range(self._model.rowCount()):
            item = self._model.item(row, 0)
            entry = item.data(Qt.ItemDataRole.UserRole) if item else None
            if entry and entry.level.lower() in ("error", "critical") and getattr(entry, "timestamp", None):
                dt = (t_sel - entry.timestamp).total_seconds()
                if 0 <= dt <= 120:
                    candidates.append((entry.timestamp, row, entry))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            _time, first_row, _ent = candidates[0]
            idx = self._model.index(first_row, 0)
            self._table.setCurrentIndex(idx)
            self._table.scrollTo(idx)

    def _compare_with_adjacent(self) -> None:
        entry = self.selected_entry()
        if not entry:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        curr_row = rows[0].row()
        target_row = curr_row + 1 if curr_row + 1 < self._model.rowCount() else (curr_row - 1 if curr_row > 0 else None)
        if target_row is None:
            return
        entry_b = self._entry_at(self._model.index(target_row, 0))
        if entry_b is not None:
            from ui.event_diff_dialog import EventDiffDialog
            dlg = EventDiffDialog(entry, entry_b, self)
            dlg.exec()

    def _open_in_eventvwr(self) -> None:
        try:
            subprocess.Popen(["eventvwr.msc"], shell=True)
        except Exception as exc:
            logger.warning("Could not launch eventvwr.msc: %s", exc)

    def _open_log_folder(self) -> None:
        from core import windows_utils
        entry = self.selected_entry()
        folder = os.path.join(windows_utils.system32(), "Winevt", "Logs")
        if entry and entry.raw:
            path = entry.raw.get("file_path") or entry.raw.get("path")
            if path and os.path.exists(os.path.dirname(str(path))):
                folder = os.path.dirname(str(path))
        try:
            subprocess.Popen(["explorer.exe", folder])
        except Exception as exc:
            logger.warning("Could not open log folder: %s", exc)

    def _add_entry_context_actions(self, menu: QMenu, entry: LogEntry) -> None:
        if getattr(entry, "timestamp", None):
            t = entry.timestamp
            zoom_act = menu.addAction("🔍 Zoom to Window around this Event (± 5 min)")
            zoom_act.triggered.connect(
                lambda: self.zoom_window_requested.emit(t - timedelta(minutes=5), t + timedelta(minutes=5))
            )
            sync_act = menu.addAction("⏱️ Sync All Diagnostic Tabs to this Time (± 5 min)")
            sync_act.triggered.connect(lambda: self.sync_panes_requested.emit(t, 5))

        is_bm = id(entry) in self._bookmarked_entry_ids
        bm_label = "📌 Remove Bookmark (★)" if is_bm else "📌 Bookmark Event (★)"
        bookmark_act = menu.addAction(bm_label)
        bookmark_act.triggered.connect(self._toggle_bookmark_selected)

        note_act = menu.addAction("📝 Add / Edit Note on Event...")
        note_act.triggered.connect(self._add_or_edit_note_selected)

        tag_menu = menu.addMenu("🏷️ Assign Diagnostic Tag")
        t_root = tag_menu.addAction("🎯 Toggle 'Root Cause'")
        t_root.triggered.connect(lambda: self._toggle_tag_selected(TAG_ROOT_CAUSE))
        t_follow = tag_menu.addAction("📌 Toggle 'Follow-up'")
        t_follow.triggered.connect(lambda: self._toggle_tag_selected(TAG_FOLLOW_UP))
        t_benign = tag_menu.addAction("🟢 Toggle 'Benign'")
        t_benign.triggered.connect(lambda: self._toggle_tag_selected(TAG_BENIGN))

        casebook_act = menu.addAction("📋 Open Investigation Casebook... (Ctrl+B)")
        casebook_act.triggered.connect(self._open_casebook_dialog)

        if entry.level and entry.level.lower() in ("error", "critical"):
            rca_act = menu.addAction("🎯 Trace Root Cause (Earliest Error in Burst)")
            rca_act.triggered.connect(self.trace_burst_trigger)

        diff_act = menu.addAction("⚖️ Compare with Adjacent Event...")
        diff_act.triggered.connect(self._compare_with_adjacent)

    def _add_filter_context_actions(self, menu: QMenu, entry: LogEntry) -> None:
        if entry.source:
            filter_source_act = menu.addAction(f"🔍 Filter by Source: '{entry.source}'")
            filter_source_act.triggered.connect(lambda: self.filter_requested.emit("source", str(entry.source)))
            ex_src_act = menu.addAction(f"🚫 Exclude Source: '{entry.source}'")
            ex_src_act.triggered.connect(lambda: self.filter_requested.emit("-source", str(entry.source)))
        if entry.level:
            filter_level_act = menu.addAction(f"🔍 Filter by Level: '{entry.level}'")
            filter_level_act.triggered.connect(lambda: self.filter_requested.emit("level", str(entry.level)))
        eid = (entry.raw or {}).get("event_id")
        if eid:
            filter_id_act = menu.addAction(f"🔍 Filter by Event ID: '{eid}'")
            filter_id_act.triggered.connect(lambda: self.filter_requested.emit("id", str(eid)))
            ex_id_act = menu.addAction(f"🚫 Exclude Event ID: '{eid}'")
            ex_id_act.triggered.connect(lambda: self.filter_requested.emit("-id", str(eid)))

    def _show_context_menu(self, pos) -> None:
        entry = self.selected_entry()
        menu = QMenu(self)

        copy_row_act = menu.addAction("📋 Copy Row (Ctrl+C)")
        copy_row_act.triggered.connect(self.copy_selected_to_clipboard)

        copy_msg_act = menu.addAction("📋 Copy Message (Ctrl+Shift+C)")
        copy_msg_act.triggered.connect(self._copy_selected_message_only)

        copy_md_act = menu.addAction("📑 Copy as Markdown")
        copy_md_act.triggered.connect(self._copy_selected_markdown)

        if entry is not None:
            field_menu = menu.addMenu("📋 Copy Specific Field")
            f_time = field_menu.addAction(f"Time: {entry.timestamp}")
            f_time.triggered.connect(lambda: QApplication.clipboard().setText(str(entry.timestamp)))
            f_src = field_menu.addAction(f"Source: {entry.source}")
            f_src.triggered.connect(lambda: QApplication.clipboard().setText(str(entry.source)))
            f_lvl = field_menu.addAction(f"Level: {entry.level}")
            f_lvl.triggered.connect(lambda: QApplication.clipboard().setText(str(entry.level)))
            eid = (entry.raw or {}).get("event_id")
            if eid:
                f_id = field_menu.addAction(f"Event ID: {eid}")
                f_id.triggered.connect(lambda: QApplication.clipboard().setText(str(eid)))

            ps_act = menu.addAction("📋 Copy as PowerShell (Get-WinEvent)")
            ps_act.triggered.connect(self.copy_as_powershell_query)

        menu.addSeparator()

        find_act = menu.addAction("🔎 Find in View... (Ctrl+F)")
        find_act.triggered.connect(self._show_find_bar)

        if entry is not None:
            self._add_entry_context_actions(menu, entry)
            menu.addSeparator()
            self._add_filter_context_actions(menu, entry)
            menu.addSeparator()

            web_act = menu.addAction("🌐 Search Event Online")
            web_act.triggered.connect(self._search_selected_on_web)

            ev_act = menu.addAction("🖥️ Open in Windows Event Viewer (eventvwr.msc)")
            ev_act.triggered.connect(self._open_in_eventvwr)

            folder_act = menu.addAction("📂 Open Containing Log Folder in Explorer")
            folder_act.triggered.connect(self._open_log_folder)

            menu.addSeparator()

        time_menu = menu.addMenu("⏱️ Time Format")
        tf_local = time_menu.addAction("Local Time (Absolute)")
        tf_local.triggered.connect(lambda: self.set_time_mode(relative=False, utc=False))
        tf_rel = time_menu.addAction("Relative Time (e.g. 5m ago)")
        tf_rel.triggered.connect(lambda: self.set_time_mode(relative=True, utc=False))
        tf_utc = time_menu.addAction("UTC Time")
        tf_utc.triggered.connect(lambda: self.set_time_mode(relative=False, utc=True))

        export_menu = menu.addMenu("💾 Export...")
        csv_act = export_menu.addAction("Export to CSV...")
        csv_act.triggered.connect(lambda: self.export_csv())
        json_act = export_menu.addAction("Export to JSON...")
        json_act.triggered.connect(lambda: self.export_json())
        html_act = export_menu.addAction("Export to HTML Report...")
        html_act.triggered.connect(lambda: self.export_html())
        xml_act = export_menu.addAction("Export to XML...")
        xml_act.triggered.connect(lambda: self.export_xml())
        san_act = export_menu.addAction("Export Sanitized CSV (Masked)...")
        san_act.triggered.connect(lambda: self.export_sanitized_csv())

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_selected_message_only(self) -> None:
        entry = self.selected_entry()
        if entry and entry.message:
            QApplication.clipboard().setText(str(entry.message))

    def _copy_selected_markdown(self) -> None:
        entry = self.selected_entry()
        if not entry:
            return
        lines = [
            f"**Timestamp:** `{entry.timestamp}`",
            f"**Source:** `{entry.source}`",
            f"**Level:** `{entry.level}`",
        ]
        if entry.raw:
            for k, v in entry.raw.items():
                lines.append(f"**{k}:** `{v}`")
        lines.append("")
        lines.append("```")
        lines.append(str(entry.message))
        lines.append("```")
        QApplication.clipboard().setText("\n".join(lines))

    def _search_selected_on_web(self) -> None:
        entry = self.selected_entry()
        if not entry:
            return
        event_id = (entry.raw or {}).get("event_id", "")
        terms = [str(entry.source)]
        if event_id:
            terms.append(f"Event ID {event_id}")
        else:
            terms.append((entry.message or "").splitlines()[0][:80])
        query = "Windows " + " ".join(terms)
        url = f"https://learn.microsoft.com/en-us/search/?terms={urllib.parse.quote_plus(query)}"
        try:
            webbrowser.open(url)
        except Exception as ex:
            logger.debug("Failed building event help tooltip for '%s': %s", entry.source, ex)

    def _get_entry_row_values(self, entry: LogEntry, diff_seconds: Optional[float] = None) -> dict[str, str]:
        raw = getattr(entry, "raw", {}) or {}
        eid = raw.get("event_id") if "event_id" in raw else raw.get("id")
        eid_str = str(eid) if eid is not None else ""
        cat_str = str(raw.get("category") or raw.get("task_category") or "")
        note_str = str(raw.get("_user_note") or getattr(entry, "note", "") or "")
        msg_str = str(entry.message or "")

        delta_str = ""
        if diff_seconds is not None:
            if diff_seconds == 0.0:
                delta_str = "0.0s"
            else:
                sign = "+" if diff_seconds > 0 else ""
                delta_str = f"{sign}{diff_seconds:.1f}s"

        vals = {
            "Time": entry.timestamp.strftime("%Y-%m-%d %H:%M:%S") if getattr(entry, "timestamp", None) else "",
            "Source": str(entry.source or ""),
            "Level": str(entry.level or ""),
            "Message": msg_str,
            "Event ID": eid_str,
            "Category": cat_str,
            "Delta Time (Δt)": delta_str,
            "Note": note_str,
        }
        if self._extra_values and self._extra_columns:
            extra_vals = list(self._extra_values(entry))
            for col_name, v in zip(self._extra_columns, extra_vals):
                vals[col_name] = str(v)
        return vals

    def export_csv(self, file_path: str = None) -> None:
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self.window() or self, "Export CSV", "", "CSV Files (*.csv)"
            )
        if not file_path:
            return
        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                visible_indices = [c for c in range(len(self._columns)) if not self._table.isColumnHidden(c)]
                visible_headers = [self._columns[c] for c in visible_indices]
                writer.writerow(visible_headers)
                prev = None
                for i, entry in enumerate(self._entries):
                    if prev and getattr(prev, "timestamp", None) and getattr(entry, "timestamp", None):
                        d_sec = (entry.timestamp - prev.timestamp).total_seconds()
                    else:
                        d_sec = 0.0 if i == 0 else None
                    val_map = self._get_entry_row_values(entry, diff_seconds=d_sec)
                    writer.writerow([val_map.get(self._columns[c], "") for c in visible_indices])
                    prev = entry
            logger.info("Exported %d entries to %s", len(self._entries), file_path)
        except OSError as e:
            logger.error("Export failed: %s", e)

    def export_sanitized_csv(self, file_path: str = None) -> None:
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self.window() or self, "Export Sanitized CSV", "", "CSV Files (*.csv)"
            )
        if not file_path:
            return
        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                visible_indices = [c for c in range(len(self._columns)) if not self._table.isColumnHidden(c)]
                visible_headers = [self._columns[c] for c in visible_indices]
                writer.writerow(visible_headers)
                prev = None
                for i, entry in enumerate(self._entries):
                    if prev and getattr(prev, "timestamp", None) and getattr(entry, "timestamp", None):
                        d_sec = (entry.timestamp - prev.timestamp).total_seconds()
                    else:
                        d_sec = 0.0 if i == 0 else None
                    val_map = self._get_entry_row_values(entry, diff_seconds=d_sec)
                    row_data = []
                    for c in visible_indices:
                        col_name = self._columns[c]
                        val = val_map.get(col_name, "")
                        if col_name in ("Message", "Source", "Note"):
                            val = sanitize_log_text(str(val))
                        row_data.append(val)
                    writer.writerow(row_data)
                    prev = entry
            logger.info("Exported %d sanitized entries to %s", len(self._entries), file_path)
        except OSError as e:
            logger.error("Sanitized export failed: %s", e)

    def export_html(self, file_path: str = None) -> None:
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self.window() or self, "Export HTML Report", "", "HTML Files (*.html)"
            )
        if not file_path:
            return
        try:
            esc = html.escape
            n_crit = sum(1 for e in self._entries if str(e.level).lower() == "critical")
            n_err = sum(1 for e in self._entries if str(e.level).lower() == "error")
            n_warn = sum(1 for e in self._entries if str(e.level).lower() == "warning")
            n_info = sum(1 for e in self._entries if str(e.level).lower() in ("info", "information"))

            visible_indices = [c for c in range(len(self._columns)) if not self._table.isColumnHidden(c)]
            visible_headers = [self._columns[c] for c in visible_indices]
            th_cells = "".join(f"<th>{esc(h)}</th>" for h in visible_headers)

            rows_html = []
            prev = None
            for i, e in enumerate(self._entries):
                if prev and getattr(prev, "timestamp", None) and getattr(e, "timestamp", None):
                    d_sec = (e.timestamp - prev.timestamp).total_seconds()
                else:
                    d_sec = 0.0 if i == 0 else None
                val_map = self._get_entry_row_values(e, diff_seconds=d_sec)
                cells = []
                for c in visible_indices:
                    col_name = self._columns[c]
                    val = esc(str(val_map.get(col_name, "")))
                    if col_name == "Time":
                        cells.append(f"<td><code>{val}</code></td>")
                    elif col_name == "Source":
                        cells.append(f"<td><b>{val}</b></td>")
                    elif col_name == "Level":
                        lvl = val.lower()
                        badge_bg = (
                            semantic("error") if lvl in ("error", "critical")
                            else semantic("warning") if lvl == "warning"
                            else semantic("info")
                        )
                        cells.append(f"<td><span style='background:{badge_bg};color:#fff;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:bold;'>{val}</span></td>")
                    else:
                        cells.append(f"<td>{val}</td>")
                rows_html.append(f"<tr>{''.join(cells)}</tr>")
                prev = e

            table_body = "\n".join(rows_html)
            sem_err = semantic("error")
            sem_warn = semantic("warning")
            sem_info = semantic("info")
            chr_bg = chrome("surface_inactive")
            chr_surf = chrome("surface")
            chr_text = chrome("text")
            chr_muted = chrome("text_muted")
            chr_outline = chrome("outline")

            html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Diagnostic Log Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: {chr_bg}; color: {chr_text}; margin: 24px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; color: {chr_text}; }}
  .summary {{ font-size: 13px; color: {chr_muted}; margin-bottom: 18px; }}
  .metrics {{ display: flex; gap: 12px; margin-bottom: 20px; }}
  .card {{ background: {chr_surf}; padding: 12px 18px; border-radius: 6px; flex: 1; border-left: 4px solid {chr_outline}; }}
  .card.danger {{ border-color: {sem_err}; }}
  .card.error {{ border-color: {sem_err}; }}
  .card.warning {{ border-color: {sem_warn}; }}
  .card.info {{ border-color: {sem_info}; }}
  .card span {{ font-size: 11px; text-transform: uppercase; color: {chr_muted}; display: block; margin-bottom: 4px; }}
  .card b {{ font-size: 20px; color: {chr_text}; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; background: {chr_surf}; border-radius: 6px; overflow: hidden; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid {chr_outline}; }}
  th {{ background: {chr_bg}; color: {chr_muted}; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
  tr:hover {{ background: {chr_bg}; }}
  code {{ font-family: Consolas, monospace; font-size: 12px; color: {sem_info}; }}
</style>
</head>
<body>
<h1>Windows Client Tool — Diagnostic Log Report</h1>
<div class="summary">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Total Loaded Records: {len(self._entries):,}</div>
<div class="metrics">
  <div class="card danger"><span>🔴 Critical</span><b>{n_crit}</b></div>
  <div class="card error"><span>🟠 Errors</span><b>{n_err}</b></div>
  <div class="card warning"><span>🟡 Warnings</span><b>{n_warn}</b></div>
  <div class="card info"><span>🔵 Information</span><b>{n_info}</b></div>
</div>
<table>
<thead><tr>{th_cells}</tr></thead>
<tbody>
{table_body}
</tbody>
</table>
</body>
</html>"""
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_doc)
            logger.info("Exported %d entries to HTML: %s", len(self._entries), file_path)
        except OSError as e:
            logger.error("HTML export failed: %s", e)

    def export_json(self, file_path: str = None) -> None:
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self.window() or self, "Export JSON", "", "JSON Files (*.json)"
            )
        if not file_path:
            return
        try:
            records = []
            for entry in self._entries:
                records.append({
                    "timestamp": entry.timestamp.isoformat(),
                    "source": str(entry.source),
                    "level": str(entry.level),
                    "extra": self._extra_text(entry),
                    "message": str(entry.message),
                    "raw": entry.raw or {},
                })
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, default=str)
            logger.info("Exported %d entries to JSON: %s", len(records), file_path)
        except OSError as e:
            logger.error("JSON export failed: %s", e)

    def export_xml(self, file_path: str = None) -> None:
        if not file_path:
            file_path, _ = QFileDialog.getSaveFileName(
                self.window() or self, "Export XML", "", "XML Files (*.xml)"
            )
        if not file_path:
            return
        try:
            esc = html.escape
            lines = ['<?xml version="1.0" encoding="utf-8"?>', '<Events>']
            for entry in self._entries:
                raw = entry.raw or {}
                lines.append('  <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">')
                lines.append('    <System>')
                lines.append(f'      <Provider Name="{esc(str(entry.source))}" />')
                lines.append(f'      <TimeCreated SystemTime="{entry.timestamp.isoformat()}" />')
                lines.append(f'      <Level>{esc(str(entry.level))}</Level>')
                if "event_id" in raw:
                    lines.append(f'      <EventID>{raw["event_id"]}</EventID>')
                if "computer" in raw:
                    lines.append(f'      <Computer>{esc(str(raw["computer"]))}</Computer>')
                if "channel" in raw:
                    lines.append(f'      <Channel>{esc(str(raw["channel"]))}</Channel>')
                lines.append('    </System>')
                lines.append('    <EventData>')
                lines.append(f'      <Data Name="Message">{esc(str(entry.message))}</Data>')
                for k, v in raw.items():
                    if not k.startswith("_"):
                        lines.append(f'      <Data Name="{esc(str(k))}">{esc(str(v))}</Data>')
                lines.append('    </EventData>')
                lines.append('  </Event>')
            lines.append('</Events>')
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            logger.info("Exported %d entries to XML: %s", len(self._entries), file_path)
        except OSError as e:
            logger.error("XML export failed: %s", e)

    def _open_casebook_dialog(self) -> None:
        try:
            from ui.investigation_casebook_dialog import InvestigationCasebookDialog
            dlg = InvestigationCasebookDialog(manager=self._casebook_mgr, parent=self)
            dlg.exec()
        except Exception as exc:
            logger.warning("Could not open casebook dialog: %s", exc)

    def copy_selected_to_clipboard(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        if self._copy_formatter is not None:
            text = self._copy_formatter(entry)
        else:
            extra = " ".join(self._extra_text(entry))
            text = f"{entry.timestamp} [{entry.level}] {entry.source}: {entry.message}"
            if extra:
                text += f"  ({extra})"
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text)
            QApplication.processEvents()
