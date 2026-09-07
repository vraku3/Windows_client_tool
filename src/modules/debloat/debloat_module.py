"""DebloatModule — bloatware removal, privacy hardening, AI feature disabling."""
import datetime
import json
import logging
import os
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QApplication, QProgressBar, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget, QMessageBox,
)
from PyQt6.QtGui import QColor, QKeySequence, QShortcut

from core.appx_service import dir_size, fetch_packages
from core.base_module import BaseModule
from core.composite_module import CompositeModule
from core.confirm import confirm_destructive
from core.formatting import human_size
from core.module_groups import ModuleGroup
from core.search_provider import SearchProvider
from core.worker import Worker
from modules.debloat import debloat_presets as dp
from modules.debloat import debloat_scanner
from modules.debloat.debloat_scanner import (
    get_installed_packages, PROTECTED_APPS, PROTECTED_REASONS,
)
from modules.debloat.debloat_search_provider import DebloatSearchProvider
from modules.tweaks.tweak_engine import TweakEngine
from modules.tweaks import tweak_engine as te
from core.semantic_colors import chrome, semantic

logger = logging.getLogger(__name__)

#: One glyph and one semantic color per TweakEngine status. Five entries
#: for five real values -- `.get(..., status_map["unknown"])` (the old
#: code) is exactly how `partial` and `not_applicable` both silently
#: rendered as "Unknown" (see the audit's V04).
_STATUS_GLYPH = {
    te.APPLIED: "●", te.NOT_APPLIED: "○", te.PARTIAL: "◑",
    te.NOT_APPLICABLE: "–", te.UNKNOWN: "❓",
}
_STATUS_COLOR = {
    te.APPLIED: semantic("success"), te.NOT_APPLIED: "#e0e0e0",
    te.PARTIAL: semantic("warning"), te.NOT_APPLICABLE: "#888888",
    te.UNKNOWN: semantic("error"),
}

#: T01's color coding extended to Risk. Every risk field across all six
#: tweak-definition files this module loads (privacy/telemetry/services/
#: network/ai_features/navigation.json) is lowercase with no exceptions --
#: capitalized keys here would never match and every cell would silently
#: stay the default gray forever.
_RISK_COLOR = {
    "low": semantic("success"), "medium": semantic("warning"), "high": semantic("error"),
}


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem that compares case-insensitively for alpha sorting."""

    def __lt__(self, other) -> bool:
        return self.text().lower() < other.text().lower()


def _step_type_badge(tweak: dict) -> str:
    """T03: flag tweaks whose steps are opaque (command/script) rather
    than the common, safely-reversible registry/service/scheduled_task
    kinds -- a `command`/`script` step is only reversible if the tweak
    itself declares a revert_command."""
    types = {s.get("type", "") for s in tweak.get("steps", [])}
    if types <= {"registry", "registry_delete", "service", "scheduled_task"}:
        return ""  # the common, safely-reversible case: no badge needed
    if "command" in types or "script" in types:
        return "⚠ "  # opaque, reversible only if the tweak declares revert_command
    return ""


class _Signals(QObject):
    """Cross-thread bridge for TweakEngine.detect_many()'s per-result
    callback -- see CLAUDE.md's "Cross-thread widget access" rule.
    on_result runs on detect_many's internal worker threads; a pyqtSignal
    is the one thread-safe way to get that back to the widgets."""
    tweak_detected = pyqtSignal(str, str, str, str)  # tab_type, tweak_id, status, reason


class DebloatToolsModule(BaseModule):
    """Debloat's own three tabs. A child of `DebloatModule` below."""

    name = "Debloat"
    icon = "\u26a1"
    description = "Remove bloatware, disable telemetry, and harden privacy"
    requires_admin = True
    #: Scanning and viewing states is safe unelevated; every Apply action is
    #: gated via `require_admin()`.
    read_only_unelevated = True
    group = ModuleGroup.OPTIMIZE

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._engine: Optional[TweakEngine] = None
        self._tab_widget: Optional[QTabWidget] = None
        self._apps_table: Optional[QTableWidget] = None
        self._show_all_checkbox: Optional[QCheckBox] = None
        self._apply_worker: Optional[Worker] = None
        self._apply_tweaks_workers: Dict[str, Worker] = {}
        self._tweaks_table: Optional[QTableWidget] = None
        self._ai_table: Optional[QTableWidget] = None
        self._installed_apps: List[str] = []
        self._auto_scanned: bool = False
        self._debloat_entries: Dict[str, dict] = {}
        self._all_tweaks: List[dict] = []
        self._ai_tweaks: List[dict] = []
        self._tweak_defs_cache: Dict[tuple, tuple] = {}
        self._signals = _Signals()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(4, 4, 4, 4)

        self._tab_widget = QTabWidget()
        self._tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #3c3c3c; border-radius: 4px; background: #252525; }
            QTabBar::tab { background: #2d2d2d; color: #b0b0b0; padding: 6px 12px; margin-right: 2px; border: 1px solid #3c3c3c; border-bottom: none; border-radius: 4px 4px 0 0; }
            QTabBar::tab:selected { background: #252525; font-weight: bold; }
            QTabBar::tab:hover { background: #3c3c3c; }
        """)

        self._tab_widget.addTab(self._build_apps_tab(), "Apps")
        self._tab_widget.addTab(self._build_tweaks_tab("tweak"), "Privacy & Telemetry")
        self._tab_widget.addTab(self._build_tweaks_tab("ai"), "AI & Navigation")
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        self._signals.tweak_detected.connect(self._on_tweak_detected)

        layout.addWidget(self._tab_widget)
        return self._widget

    def _build_apps_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._apps_status = QLabel("Click 'Scan Apps' to detect installed bloatware")
        self._apps_status.setStyleSheet("font-size: 13px; padding: 4px;")
        layout.addWidget(self._apps_status)

        btn_layout = QGridLayout()
        self._scan_btn = QPushButton("Scan Apps")
        self._scan_btn.clicked.connect(self._on_scan)
        self._apply_selected_btn = QPushButton("Apply Selected")
        self._apply_selected_btn.clicked.connect(self._on_apply_selected)
        self._apply_selected_btn.setEnabled(False)
        self._apply_all_btn = QPushButton("Apply All Safe")
        self._apply_all_btn.clicked.connect(self._on_apply_all_safe)
        self._apply_all_btn.setEnabled(False)
        self._cancel_apply_btn = QPushButton("✕ Cancel")
        self._cancel_apply_btn.setVisible(False)
        self._cancel_apply_btn.clicked.connect(self._on_cancel_apply)
        export_btn = QPushButton("Export")
        export_btn.clicked.connect(self._on_export_apps)
        btn_layout.addWidget(self._scan_btn, 0, 0)
        btn_layout.addWidget(self._apply_selected_btn, 0, 1)
        btn_layout.addWidget(self._apply_all_btn, 0, 2)
        btn_layout.addWidget(self._cancel_apply_btn, 0, 3)
        btn_layout.addWidget(export_btn, 0, 4)
        layout.addLayout(btn_layout)

        filter_row = QHBoxLayout()
        self._apps_search = QLineEdit()
        self._apps_search.setPlaceholderText("Search apps…")
        self._apps_search.textChanged.connect(self._apply_apps_filter)
        filter_row.addWidget(self._apps_search, 1)
        self._apps_category_combo = QComboBox()
        self._apps_category_combo.addItem("All Categories")
        self._apps_category_combo.currentIndexChanged.connect(self._apply_apps_filter)
        filter_row.addWidget(self._apps_category_combo)
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._on_apps_select_all)
        filter_row.addWidget(select_all_btn)
        select_none_btn = QPushButton("Select None")
        select_none_btn.clicked.connect(self._on_apps_select_none)
        filter_row.addWidget(select_none_btn)
        self._apps_selected_lbl = QLabel("0 selected")
        filter_row.addWidget(self._apps_selected_lbl)
        self._show_all_checkbox = QCheckBox("Show all catalogued apps")
        self._show_all_checkbox.toggled.connect(
            lambda _c: self._populate_apps_table(self._installed_apps))
        filter_row.addWidget(self._show_all_checkbox)
        layout.addLayout(filter_row)

        apps_preset_layout = QGridLayout()
        for col, (key, label) in enumerate((
                ("light", "Light Debloat"), ("full", "Full Debloat"),
                ("privacy", "Privacy-Focused"), ("custom", "Custom"))):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, k=key: self._on_apps_preset(k))
            apps_preset_layout.addWidget(btn, 0, col)
        save_custom_btn = QPushButton("Save Selection as Custom")
        save_custom_btn.clicked.connect(self._on_save_apps_as_custom)
        apps_preset_layout.addWidget(save_custom_btn, 0, 4)
        layout.addLayout(apps_preset_layout)

        self._apps_progress = QProgressBar()
        self._apps_progress.setVisible(False)
        layout.addWidget(self._apps_progress)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)

        self._apps_table = QTableWidget()
        self._apps_table.setColumnCount(4)
        self._apps_table.setHorizontalHeaderLabels(["\u2610", "App Name", "Category", "Status"])
        header = self._apps_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        # Checkbox and Status size to content; App Name and Category share the width.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._apps_table.setSortingEnabled(True)
        self._apps_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._apps_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._apps_table.itemChanged.connect(self._on_item_changed)
        self._apps_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._apps_table.customContextMenuRequested.connect(self._on_apps_context_menu)
        table_layout.addWidget(self._apps_table)

        scroll.setWidget(table_container)
        layout.addWidget(scroll)

        for seq, slot in (("Ctrl+F", self._apps_search.setFocus),
                          ("Ctrl+A", self._on_apps_select_all),
                          ("Ctrl+E", self._on_export_apps)):
            sc = QShortcut(QKeySequence(seq), widget)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        return widget

    def _build_tweaks_table_widget(self, tab_type: str) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["☐", "Tweak", "Category", "Risk", "Status"])
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        # Tweak and Category share the width; the rest size to content.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        table.setSortingEnabled(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setObjectName(f"_table_{tab_type}")
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, tt=tab_type: self._on_tweaks_context_menu(pos, tt))
        table.itemChanged.connect(
            lambda item, tt=tab_type: self._on_tweaks_item_changed(item, tt))
        return table

    def _build_tweaks_tab(self, tab_type: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        status_lbl = QLabel("Loading...")
        status_lbl.setStyleSheet("font-size: 13px; padding: 4px;")
        status_lbl.setObjectName(f"_status_{tab_type}")
        status_row = QHBoxLayout()
        status_row.addWidget(status_lbl)
        # No setStyleSheet() here on purpose: the objectName is claimed for
        # findChild lookup by _populate_tweaks_table, and a new inline sheet
        # would trip tests/test_no_inline_stylesheets.py's ratchet (every
        # existing call, including status_lbl's identical one two lines up,
        # already counts against that falling-only budget).
        catalog_lbl = QLabel("")
        catalog_lbl.setObjectName(f"_catalog_{tab_type}")
        status_row.addWidget(catalog_lbl)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        preset_layout = QGridLayout()
        light_btn = QPushButton("Light Debloat")
        full_btn = QPushButton("Full Debloat")
        privacy_btn = QPushButton("Privacy-Focused")
        custom_btn = QPushButton("Custom")
        light_btn.clicked.connect(lambda: self._on_preset("light", tab_type))
        full_btn.clicked.connect(lambda: self._on_preset("full", tab_type))
        privacy_btn.clicked.connect(lambda: self._on_preset("privacy", tab_type))
        custom_btn.clicked.connect(lambda: self._on_preset("custom", tab_type))
        preset_layout.addWidget(light_btn, 0, 0)
        preset_layout.addWidget(full_btn, 0, 1)
        preset_layout.addWidget(privacy_btn, 0, 2)
        preset_layout.addWidget(custom_btn, 0, 3)
        layout.addLayout(preset_layout)

        legend = QLabel(
            "● Applied &nbsp;&nbsp; ○ Not Applied &nbsp;&nbsp; "
            "◑ Partially Applied &nbsp;&nbsp; – Not Applicable "
            "&nbsp;&nbsp; ❓ Unknown — hover a row for why")
        # setObjectName("muted"), not an inline setStyleSheet: an inline
        # sheet beats the app stylesheet and never changes again, so it
        # would survive a theme switch unchanged (see the "muted" role in
        # dark.qss / light.qss and tests/test_no_inline_stylesheets.py).
        legend.setObjectName("muted")
        layout.addWidget(legend)

        filter_row = QHBoxLayout()
        search = QLineEdit()
        search.setObjectName(f"_search_{tab_type}")
        search.setPlaceholderText("Search tweaks…")
        search.textChanged.connect(lambda _t, tt=tab_type: self._apply_tweaks_filter(tt))
        filter_row.addWidget(search, 1)
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(lambda _c=False, tt=tab_type: self._on_tweaks_select_all(tt))
        filter_row.addWidget(select_all_btn)
        select_none_btn = QPushButton("Select None")
        select_none_btn.clicked.connect(lambda _c=False, tt=tab_type: self._on_tweaks_select_none(tt))
        filter_row.addWidget(select_none_btn)
        selected_lbl = QLabel("0 selected")
        selected_lbl.setObjectName(f"_selected_{tab_type}")
        filter_row.addWidget(selected_lbl)
        layout.addLayout(filter_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self._build_tweaks_table_widget(tab_type))

        scroll.setWidget(table_container)
        layout.addWidget(scroll)

        progress = QProgressBar()
        progress.setObjectName(f"_progress_{tab_type}")
        progress.setVisible(False)
        layout.addWidget(progress)

        apply_row = QHBoxLayout()
        apply_btn = QPushButton("Apply Selected Tweaks")
        apply_btn.clicked.connect(lambda: self._on_apply_tweaks(tab_type))
        apply_row.addWidget(apply_btn)
        cancel_btn = QPushButton("✕ Cancel")
        cancel_btn.setObjectName(f"_cancel_apply_{tab_type}")
        cancel_btn.setVisible(False)
        cancel_btn.clicked.connect(lambda _c=False, tt=tab_type: self._on_cancel_tweaks_apply(tt))
        apply_row.addWidget(cancel_btn)
        layout.addLayout(apply_row)

        save_custom_btn = QPushButton("Save as Custom")
        save_custom_btn.clicked.connect(
            lambda _checked=False, tt=tab_type: self._on_save_tweaks_as_custom(tt))
        layout.addWidget(save_custom_btn)

        revert_btn = QPushButton("Revert Applied…")
        revert_btn.clicked.connect(self._on_revert_tweaks)
        layout.addWidget(revert_btn)

        return widget

    def on_start(self, app) -> None:
        self.app = app
        self._engine = TweakEngine(app.backup)

    def on_activate(self) -> None:
        # First-load guard (CLAUDE.md pattern): trigger the scan once, on
        # the first activation, not on every navigation back to this tab.
        # Gated on a dedicated flag rather than `self._installed_apps` --
        # an empty scan result (a clean machine) is a legitimate outcome
        # and must not be indistinguishable from "never scanned".
        #
        # `self._widget is None` guards a composite-hosted child whose tab
        # was never opened: CompositeModule.on_stop and the refresh timer
        # can both reach a child before create_widget() ever ran, so
        # `_scan_btn` and friends don't exist yet (see
        # test_every_composite_child_survives_a_tick_it_was_not_built_for).
        if self._widget is None:
            return
        if not self._auto_scanned:
            self._auto_scanned = True
            self._on_scan()

    def on_deactivate(self) -> None:
        self._persist_sort()
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_refresh_interval(self) -> Optional[int]:
        return None

    def get_search_provider(self) -> Optional[SearchProvider]:
        return DebloatSearchProvider()

    # ------------------------------------------------------------------
    # Apps tab
    # ------------------------------------------------------------------

    def _load_debloat_entries(self) -> Dict[str, dict]:
        if not self._debloat_entries:
            path = os.path.join(
                os.path.dirname(__file__), "..", "tweaks", "definitions", "debloat.json"
            )
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    entries = json.load(f)
                self._debloat_entries = {e["id"]: e for e in entries}
        return self._debloat_entries

    def _on_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._apps_status.setText("Scanning installed apps...")
        w = Worker(self._do_scan)
        w.signals.result.connect(self._on_scanned)
        w.signals.error.connect(self._on_scan_error)
        self._workers.append(w)
        self.app.thread_pool.start(w)

    def _do_scan(self, worker: Worker) -> Dict:
        installed = get_installed_packages()
        return {"installed": list(installed.keys())}

    def _on_scanned(self, result: Dict) -> None:
        self._scan_btn.setEnabled(True)
        installed: List[str] = result.get("installed", [])
        self._installed_apps = installed
        logger.info("Debloat scan complete \u2014 %d bloatware app(s) detected", len(installed))
        entries = self._load_debloat_entries()
        by_category: Dict[str, int] = {}
        for entry in entries.values():
            if entry.get("package") in installed:
                by_category[entry.get("category", "")] = \
                    by_category.get(entry.get("category", ""), 0) + 1
        breakdown = ", ".join(f"{c}: {n}" for c, n in sorted(by_category.items()))
        self._apps_status.setText(
            f"Scan complete \u2014 {len(installed)} bloatware app(s) detected"
            + (f" ({breakdown})" if breakdown else "")
        )
        self._populate_apps_table(installed)
        self._apply_selected_btn.setEnabled(len(installed) > 0)
        self._apply_all_btn.setEnabled(len(installed) > 0)

    def _install_location_by_package(self) -> Dict[str, str]:
        return {p.get("Name", ""): p.get("InstallLocation", "")
               for p in fetch_packages(use_cache=True)}

    def _populate_apps_table(self, installed: List[str]) -> None:
        # Keep in sync with what's actually on screen -- the Apply methods
        # read self._installed_apps to tell an installed row from a
        # catalogued-but-not-installed one (Show All can put both in the
        # table), and a caller populating directly (a test, or the Show All
        # toggle re-populating with the same list) must not leave it stale.
        self._installed_apps = installed
        self._apps_table.setSortingEnabled(False)
        self._apps_table.setRowCount(0)
        entries = self._load_debloat_entries()
        locations = self._install_location_by_package()

        for entry in sorted(entries.values(), key=lambda e: e.get("name", "").lower()):
            pkg = entry.get("package", "")
            present = pkg in installed
            if not present and not self._show_all_checkbox.isChecked():
                continue
            row = self._apps_table.rowCount()
            self._apps_table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, entry["id"])
            chk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._apps_table.setItem(row, 0, chk)
            name_item = _SortableItem(entry.get("name", pkg))
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tooltip_lines = []
            if pkg in PROTECTED_APPS:
                reason = PROTECTED_REASONS.get(pkg, "")
                if reason:
                    tooltip_lines.append(reason)
            tooltip_lines.append("Removed for all users on this machine")
            name_item.setToolTip("\n".join(tooltip_lines))
            self._apps_table.setItem(row, 1, name_item)
            cat_item = QTableWidgetItem(entry.get("category", ""))
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._apps_table.setItem(row, 2, cat_item)
            if present:
                size_bytes = dir_size(locations.get(pkg, ""))
                status_item = QTableWidgetItem(human_size(size_bytes))
            else:
                status_item = QTableWidgetItem("Not installed")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            if pkg in PROTECTED_APPS:
                status_item.setForeground(QColor(semantic("warning")))
            self._apps_table.setItem(row, 3, status_item)

        self._apps_table.setSortingEnabled(True)
        # T23: restore the last sort column/order this session saved in
        # on_deactivate, rather than always resetting to name-ascending.
        sort_col = int(self.app.config.get(f"{self._CONFIG_PREFIX}.apps.sort_column", 1) or 1)
        sort_order = Qt.SortOrder(int(self.app.config.get(
            f"{self._CONFIG_PREFIX}.apps.sort_order", int(Qt.SortOrder.AscendingOrder.value)) or 0))
        self._apps_table.sortItems(sort_col, sort_order)
        self._apps_table.setAlternatingRowColors(True)
        categories = sorted({self._apps_table.item(r, 2).text()
                            for r in range(self._apps_table.rowCount())})
        current = self._apps_category_combo.currentText()
        self._apps_category_combo.blockSignals(True)
        self._apps_category_combo.clear()
        self._apps_category_combo.addItem("All Categories")
        self._apps_category_combo.addItems(categories)
        idx = self._apps_category_combo.findText(current)
        self._apps_category_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._apps_category_combo.blockSignals(False)

    def _apply_apps_filter(self) -> None:
        query = self._apps_search.text().strip().lower()
        category = self._apps_category_combo.currentText()
        for r in range(self._apps_table.rowCount()):
            name = self._apps_table.item(r, 1).text().lower()
            row_category = self._apps_table.item(r, 2).text()
            visible = (not query or query in name) and \
                (category == "All Categories" or category == row_category)
            self._apps_table.setRowHidden(r, not visible)

    def _on_apps_select_all(self) -> None:
        for r in range(self._apps_table.rowCount()):
            if not self._apps_table.isRowHidden(r):
                self._apps_table.item(r, 0).setCheckState(Qt.CheckState.Checked)

    def _on_apps_select_none(self) -> None:
        for r in range(self._apps_table.rowCount()):
            self._apps_table.item(r, 0).setCheckState(Qt.CheckState.Unchecked)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            checked = sum(
                1 for r in range(self._apps_table.rowCount())
                if self._apps_table.item(r, 0).checkState() == Qt.CheckState.Checked
            )
            self._apply_selected_btn.setEnabled(checked > 0)
            self._apps_selected_lbl.setText(f"{checked} selected")

    def _preview(self, names: List[str], limit: int = 15) -> str:
        shown = names[:limit]
        text = "\n".join(f"  • {n}" for n in shown)
        extra = len(names) - len(shown)
        return text + (f"\n  …and {extra} more" if extra > 0 else "")

    def _on_apply_selected(self) -> None:
        if not self.require_admin():
            return
        entries = self._load_debloat_entries()
        selected_ids, protected = [], []
        for r in range(self._apps_table.rowCount()):
            if self._apps_table.item(r, 0).checkState() != Qt.CheckState.Checked:
                continue
            entry_id = self._apps_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            pkg = self._find_package(entry_id)
            if pkg not in self._installed_apps:
                continue
            if pkg in PROTECTED_APPS:
                protected.append((entry_id, pkg))
            else:
                selected_ids.append(entry_id)
        if protected:
            reasons = "\n".join(
                f"• {pkg} — {PROTECTED_REASONS.get(pkg, 'This app may be required.')}"
                for _, pkg in protected)
            all_ids = selected_ids + [eid for eid, _ in protected]
            all_names = [entries.get(eid, {}).get("name", eid) for eid in all_ids]
            if confirm_destructive(
                    self._widget, "Protected Apps Selected",
                    f"{len(protected)} of your selected app(s) are "
                    f"protected. Remove them anyway?",
                    detail=reasons + "\n\n" + self._preview(all_names)):
                selected_ids.extend(eid for eid, _ in protected)
        if selected_ids:
            self._do_apply_apps(selected_ids)

    def _on_apply_all_safe(self) -> None:
        if not self.require_admin():
            return
        entries = self._load_debloat_entries()
        selected_ids = []
        for r in range(self._apps_table.rowCount()):
            entry_id = self._apps_table.item(r, 3).data(Qt.ItemDataRole.UserRole)
            pkg = self._find_package(entry_id)
            if pkg in self._installed_apps and pkg not in PROTECTED_APPS:
                selected_ids.append(entry_id)
        if not selected_ids:
            return
        names = [entries.get(eid, {}).get("name", eid) for eid in selected_ids]
        if not confirm_destructive(
                self._widget, "Remove Bloatware",
                f"Remove {len(selected_ids)} app(s)?",
                detail=self._preview(names)):
            return
        self._do_apply_apps(selected_ids)

    def _do_apply_apps(self, entry_ids: List[str]) -> None:
        self._apply_selected_btn.setEnabled(False)
        self._apply_all_btn.setEnabled(False)
        self._apps_progress.setVisible(True)
        self._apps_progress.setRange(0, len(entry_ids))
        self._apps_progress.setValue(0)
        self._cancel_apply_btn.setVisible(True)
        self._cancel_apply_btn.setEnabled(True)

        def work(w: Worker):
            backup = self.app.backup
            engine = TweakEngine(backup)
            rp_id = backup.create_restore_point(
                f"Debloat apps {datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}", "Debloat")
            entries = self._load_debloat_entries()
            success, targeted = 0, []
            logger.info("Debloat: removing %d app(s)", len(entry_ids))
            for i, eid in enumerate(entry_ids):
                if w.is_cancelled:
                    break
                entry = entries.get(eid)
                if entry:
                    pkg = entry.get("package", eid)
                    targeted.append(pkg)
                    logger.info("Debloat: removing %s", pkg)
                    if engine.apply_tweak(entry, rp_id):
                        success += 1
                w.signals.progress.emit(i + 1)
            logger.info("Debloat: removed %d/%d app(s)", success, len(entry_ids))
            return {"success": success, "total": len(entry_ids), "targeted": targeted}

        w = Worker(work)
        w.signals.progress.connect(self._apps_progress.setValue)
        w.signals.result.connect(self._on_apps_applied)
        w.signals.error.connect(self._on_apply_error)
        self._workers.append(w)
        self._apply_worker = w
        self.app.thread_pool.start(w)

    def _on_cancel_apply(self) -> None:
        if self._apply_worker is not None:
            self._apply_worker.cancel()
        self._cancel_apply_btn.setEnabled(False)

    def _on_apps_applied(self, result: Dict) -> None:
        self._apps_progress.setVisible(False)
        self._cancel_apply_btn.setVisible(False)
        self._apply_selected_btn.setEnabled(True)
        self._apply_all_btn.setEnabled(True)
        now_installed = set(debloat_scanner.get_installed_packages())
        actually_gone = sum(1 for pkg in result.get("targeted", [])
                            if pkg not in now_installed)
        logger.info(
            "Debloat complete: confirmed %d/%d app(s) removed",
            actually_gone, result["total"],
        )
        box = QMessageBox(self._widget)
        box.setWindowTitle("Debloat Complete")
        box.setText(f"{actually_gone} of {result['total']} app(s) confirmed "
                    f"removed.\nA restore point has been created.")
        restore_btn = box.addButton("Open Restore Manager…",
                                    QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is restore_btn:
            from ui.restore_manager import RestoreManagerDialog
            RestoreManagerDialog(self.app, self._widget).exec()
        self._apps_status.setText(
            f"{actually_gone} of {result['total']} app(s) confirmed removed")
        self._on_scan()

    def _on_scan_error(self, err: str) -> None:
        self._scan_btn.setEnabled(True)
        self._apps_status.setText(f"Scan failed: {err}")
        logger.error("Debloat scan error: %s", err)

    def _on_apply_error(self, err: str) -> None:
        self._apps_progress.setVisible(False)
        self._cancel_apply_btn.setVisible(False)
        self._apply_selected_btn.setEnabled(True)
        self._apply_all_btn.setEnabled(True)
        logger.error("Debloat apply error: %s", err)

    def _find_package(self, entry_id: str) -> str:
        entries = self._load_debloat_entries()
        entry = entries.get(entry_id, {})
        return entry.get("package", "")

    def _on_apps_context_menu(self, pos) -> None:
        index = self._apps_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        pkg = self._find_package(
            self._apps_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self._apps_table)
        act_copy = menu.addAction("Copy package name")
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(pkg))
        menu.exec(self._apps_table.viewport().mapToGlobal(pos))

    def _on_export_apps(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Export Apps", "debloat_apps.csv", "CSV (*.csv)")
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Package", "Category"])
            entries = self._load_debloat_entries()
            for r in range(self._apps_table.rowCount()):
                entry_id = self._apps_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
                entry = entries.get(entry_id, {})
                writer.writerow([entry.get("name", ""), entry.get("package", ""),
                                entry.get("category", "")])

    def _on_apps_preset(self, preset_name: str) -> None:
        if preset_name == "custom":
            self._load_custom_apps_preset()
            return
        preset = dp.load_preset(preset_name)
        catalog = self._load_debloat_entries()
        selected_ids = dp.resolve_app_entry_ids(preset, catalog)
        for r in range(self._apps_table.rowCount()):
            item_id = self._apps_table.item(r, 3).data(Qt.ItemDataRole.UserRole)
            self._apps_table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if item_id in selected_ids
                else Qt.CheckState.Unchecked)

    def _load_custom_apps_preset(self) -> None:
        try:
            preset = dp.load_preset("custom")
        except (OSError, ValueError) as exc:
            logger.warning("Could not load the Custom preset: %s", exc)
            QMessageBox.information(
                self._widget, "No Custom preset saved yet",
                "Check the apps you want removed, then use “Save as "
                "Custom” first.")
            return
        catalog = self._load_debloat_entries()
        selected_ids = dp.resolve_app_entry_ids(preset, catalog)
        for r in range(self._apps_table.rowCount()):
            item_id = self._apps_table.item(r, 3).data(Qt.ItemDataRole.UserRole)
            self._apps_table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if item_id in selected_ids
                else Qt.CheckState.Unchecked)

    def _on_save_apps_as_custom(self) -> None:
        checked_ids = [
            self._apps_table.item(r, 3).data(Qt.ItemDataRole.UserRole)
            for r in range(self._apps_table.rowCount())
            if self._apps_table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]
        dp.save_custom_apps(checked_ids, self._load_debloat_entries())
        self._apps_status.setText(
            f"Saved {len(checked_ids)} app(s) to the Custom preset")

    # ------------------------------------------------------------------
    # Tab lazy-loading
    # ------------------------------------------------------------------

    _TAB_TYPES = ["apps", "tweak", "ai"]
    #: T23 sort-column persistence key prefix. Mirrors StoreAppsModule's
    #: `_CONFIG_PREFIX` -- see `_persist_sort` below. Applies only to
    #: `_apps_table`: the tweak tables keep sorting permanently off (see
    #: `_populate_tweaks_table`'s comment on the source-file grouping), so
    #: there is no sort state to persist for them.
    _CONFIG_PREFIX = "debloat"

    def _on_tab_changed(self, index: int) -> None:
        tab_type = self._TAB_TYPES[index] if index < len(self._TAB_TYPES) else None
        if not tab_type or tab_type == "apps":
            return
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if table and table.rowCount() == 0:
            self._populate_tweaks_table(tab_type)
        elif table:
            self._redetect_tweaks_table(tab_type)

    def _persist_sort(self) -> None:
        """T23: mirrors StoreAppsModule._persist_sort exactly. Applies only
        to the Apps tab's table -- the tweak tables have sorting
        permanently disabled (see _populate_tweaks_table), so there is no
        sort indicator on them worth saving."""
        if self._apps_table is None:
            return
        header = self._apps_table.horizontalHeader()
        self.app.config.set(f"{self._CONFIG_PREFIX}.apps.sort_column",
                            int(header.sortIndicatorSection()))
        self.app.config.set(f"{self._CONFIG_PREFIX}.apps.sort_order",
                            int(header.sortIndicatorOrder()))

    # ------------------------------------------------------------------
    # Tweaks tabs
    # ------------------------------------------------------------------

    def _load_tweak_definitions(self, tab_type: str) -> List[dict]:
        files = (["privacy.json", "telemetry.json", "services.json", "network.json"]
                 if tab_type == "tweak" else ["ai_features.json", "navigation.json"])
        base = os.path.join(os.path.dirname(__file__), "..", "tweaks", "definitions")
        cache_key = tuple(files)
        cached = self._tweak_defs_cache.get(cache_key)
        mtimes = tuple(os.path.getmtime(os.path.join(base, f))
                      for f in files if os.path.exists(os.path.join(base, f)))
        if cached is not None and cached[0] == mtimes:
            return cached[1]

        all_tweaks = []
        for fname in files:
            path = os.path.join(base, fname)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    for tweak in json.load(f):
                        tweak["_source"] = fname
                        all_tweaks.append(tweak)
        self._tweak_defs_cache[cache_key] = (mtimes, all_tweaks)
        return all_tweaks

    def _catalog_freshness(self, tab_type: str) -> str:
        """T10: a "Catalog: <date>" label next to the tab's status label,
        so a stale bundled tweak catalog is visible rather than silent."""
        files = (["privacy.json", "telemetry.json", "services.json", "network.json"]
                 if tab_type == "tweak" else ["ai_features.json", "navigation.json"])
        base = os.path.join(os.path.dirname(__file__), "..", "tweaks", "definitions")
        mtimes = [os.path.getmtime(os.path.join(base, f))
                 for f in files if os.path.exists(os.path.join(base, f))]
        if not mtimes:
            return ""
        newest = datetime.datetime.fromtimestamp(max(mtimes))
        return f"Catalog: {newest.strftime('%Y-%m-%d')}"

    def _insert_tweak_rows(self, table: QTableWidget, tweaks: List[dict]) -> None:
        """T07/T08: grouped by source file rather than a flat alpha sort --
        a QTableWidget doesn't support nested rows cleanly, so a
        non-selectable, styled full-width divider row per group is the
        established lightweight pattern here. Sorting stays OFF for the
        life of this table (see `_populate_tweaks_table`): re-enabling it
        and calling sortItems() would re-sort every row, including header
        rows, purely on column 1's text -- scattering "Privacy
        (privacy.json)" wherever it falls alphabetically among the tweak
        names and destroying the grouping this loop just built."""
        last_source = None
        for tweak in sorted(tweaks, key=lambda t: (t.get("_source", ""), t.get("name", "").lower())):
            if tweak.get("_source") != last_source:
                last_source = tweak.get("_source")
                header_row = table.rowCount()
                table.insertRow(header_row)
                label = f"{tweak.get('category', '')} ({last_source})"
                header_item = QTableWidgetItem(label)
                header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                # chrome("surface_inactive"), not a literal hex: this file's
                # frozen-colour budget (tests/test_no_frozen_colours.py) is a
                # ratchet that only ever falls, and the theme-aware helper is
                # also just correct here -- a hardcoded dark-theme grey would
                # freeze the divider for the light theme too.
                header_item.setBackground(QColor(chrome("surface_inactive")))
                table.setItem(header_row, 1, header_item)
                table.setSpan(header_row, 1, 1, 4)

            row = table.rowCount()
            table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, tweak.get("id", ""))
            chk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 0, chk)
            name_item = _SortableItem(_step_type_badge(tweak) + tweak.get("name", ""))
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 1, name_item)
            cat_item = QTableWidgetItem(tweak.get("category", ""))
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 2, cat_item)
            risk_item = QTableWidgetItem(tweak.get("risk", ""))
            risk_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            risk_item.setForeground(QColor(_RISK_COLOR.get(tweak.get("risk", ""), chrome("text"))))
            table.setItem(row, 3, risk_item)

            # Placeholder -- detect_many fills this in via _on_tweak_detected
            # as results arrive (or, in this synchronous call, all at once
            # by the time this method returns).
            si = QTableWidgetItem("… Checking")
            si.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            si.setData(Qt.ItemDataRole.UserRole, tweak.get("id", ""))
            table.setItem(row, 4, si)

    def _populate_tweaks_table(self, tab_type: str) -> None:
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        status_lbl: QLabel = self._widget.findChild(QLabel, f"_status_{tab_type}")
        if not table:
            return

        tweaks = self._load_tweak_definitions(tab_type)
        if tab_type == "tweak":
            self._all_tweaks = tweaks
        else:
            self._ai_tweaks = tweaks

        table.setRowCount(0)
        table.setSortingEnabled(False)
        self._insert_tweak_rows(table, tweaks)

        catalog_lbl: QLabel = self._widget.findChild(QLabel, f"_catalog_{tab_type}")
        if catalog_lbl:
            catalog_lbl.setText(self._catalog_freshness(tab_type))

        if not self._engine:
            self._engine = TweakEngine(self.app.backup)
        engine = self._engine

        # Collected thread-safely -- list.append() is a single, GIL-atomic
        # bytecode op, safe to call concurrently from detect_many's worker
        # threads with no lock needed -- so the table can be filled in
        # directly, back on this (the UI) thread, once detect_many() has
        # returned. This deliberately does NOT lean on Qt's cross-thread
        # signal-queue delivery to get the results back synchronously (see
        # `on_result` below for why): a bare `QApplication.processEvents()`
        # measured a reproducible native crash (0x80010108) in this test
        # file after ~8 real detect_many() calls in one process, from
        # draining unrelated stale widget events that had built up; the
        # obvious narrower fix, `QCoreApplication.sendPostedEvents(self._signals,
        # MetaCall)`, was measured to silently deliver NOTHING instead
        # (confirmed with a QObject receiver too, not just a plain one) --
        # PyQt6 does not expose the actual internal receiver object a
        # signal-to-plain-method connection posts its QMetaCallEvent to,
        # so only `sendPostedEvents(None, ...)` (process-wide) works, and
        # that is exactly the reentrancy risk this design avoids: forcing
        # a process-wide flush mid-call could run another module's queued
        # Worker callback (e.g. a 60s auto-refresh result) synchronously,
        # ahead of when the running event loop would have gotten to it.
        results: List[tuple] = []

        def on_result(tweak: dict, result) -> None:
            # Runs on detect_many's internal worker threads -- touch
            # NOTHING here but this thread-safe append and the signal
            # emit below (see CLAUDE.md's "Cross-thread widget access"
            # rule and _Signals' docstring) -- never a widget. The emit
            # keeps this callback correct on its own terms if detect_many
            # is ever driven from a real background Worker instead of
            # called synchronously as it is here; this method's own use
            # of the results does not depend on that delivery happening.
            payload = (tab_type, tweak.get("id", ""), result.status, result.reason or "")
            results.append(payload)
            self._signals.tweak_detected.emit(*payload)

        engine.detect_many(tweaks, on_result)
        # detect_many() blocks until every probe has landed, so `results`
        # is already complete and we are back on this (the UI) thread --
        # apply them directly, the same update `_on_tweak_detected` makes
        # when reached through the signal, with no event-queue involved.
        counts: Dict[str, int] = {}
        for payload in results:
            counts[payload[2]] = counts.get(payload[2], 0) + 1
            self._on_tweak_detected(*payload)

        # T19: the label reflects real detected statuses, not just the
        # count loaded -- set after detection completes (detect_many above
        # blocks until it has), not before.
        if status_lbl:
            status_lbl.setText(self._format_status_breakdown(counts, len(results)))

    def _format_status_breakdown(self, counts: Dict[str, int], total: int) -> str:
        """T19: a live "N tweak(s) -- Applied: x, Not Applied: y, ..."
        label, shared by _populate_tweaks_table and _redetect_tweaks_table."""
        if not counts:
            return f"{total} tweak(s) loaded"
        parts = ", ".join(f"{te.STATUS_LABELS[s]}: {n}" for s, n in sorted(counts.items()))
        return f"{total} tweak(s) — {parts}"

    def _redetect_tweaks_table(self, tab_type: str) -> None:
        """T24: re-run detection over already-built rows when returning to
        a tab, instead of a full _populate_tweaks_table rebuild.

        `on_result` here touches NOTHING but the thread-safe list append --
        it runs on detect_many's internal worker threads (see
        `TweakEngine.detect_many`), and calling a Qt widget method from
        there is exactly the "Cross-thread widget access" bug CLAUDE.md
        documents and an earlier task on this branch already fixed once in
        `_populate_tweaks_table` above. Row updates happen only after
        detect_many() has returned (it blocks until every probe lands), by
        calling `_on_tweak_detected` -- the SAME method
        `_populate_tweaks_table` uses -- rather than duplicating its
        row-writing logic here."""
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        status_lbl: QLabel = self._widget.findChild(QLabel, f"_status_{tab_type}")
        if not table:
            return
        tweaks = self._all_tweaks if tab_type == "tweak" else self._ai_tweaks
        by_id = {t.get("id", ""): t for t in tweaks}

        if not self._engine:
            self._engine = TweakEngine(self.app.backup)
        engine = self._engine

        results: List[tuple] = []

        def on_result(tweak, result) -> None:
            # Runs on detect_many's internal worker threads -- touch
            # NOTHING here but this thread-safe append (same pattern as
            # _populate_tweaks_table above).
            results.append((tweak.get("id", ""), result.status, result.reason or ""))

        engine.detect_many(list(by_id.values()), on_result)
        # detect_many() blocks until every probe has landed -- results is
        # complete and we are back on the UI thread.
        counts: Dict[str, int] = {}
        for tweak_id, status, reason in results:
            counts[status] = counts.get(status, 0) + 1
            self._on_tweak_detected(tab_type, tweak_id, status, reason)

        if status_lbl:
            status_lbl.setText(self._format_status_breakdown(counts, len(results)))

    def _on_tweak_detected(self, tab_type: str, tweak_id: str, status: str, reason: str) -> None:
        """The only place that touches a status cell. Reached two ways,
        both safe: via the `tweak_detected` signal (thread-safe delivery,
        for a hypothetical future caller that drives detect_many from a
        background Worker), or via the direct call in
        `_populate_tweaks_table` above -- which only ever happens back on
        this (the UI) thread, after detect_many() has already returned.
        Never called from `on_result`, which runs on a worker thread."""
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return
        for r in range(table.rowCount()):
            item = table.item(r, 4)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == tweak_id:
                item.setText(_STATUS_GLYPH[status] + " " + te.STATUS_LABELS[status])
                item.setForeground(QColor(_STATUS_COLOR[status]))
                # Never blank: a status with nothing behind it is the bug
                # this design exists to prevent (CLAUDE.md, TweakEngine.detect).
                item.setToolTip(reason or te.STATUS_LABELS[status])
                break

    def _apply_tweaks_filter(self, tab_type: str) -> None:
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        search: QLineEdit = self._widget.findChild(QLineEdit, f"_search_{tab_type}")
        if not table or not search:
            return
        query = search.text().strip().lower()
        for r in range(table.rowCount()):
            name = table.item(r, 1).text().lower()
            table.setRowHidden(r, bool(query) and query not in name)

    def _on_tweaks_select_all(self, tab_type: str) -> None:
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item is None or table.isRowHidden(r):
                continue
            item.setCheckState(Qt.CheckState.Checked)

    def _on_tweaks_select_none(self, tab_type: str) -> None:
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item is None:
                continue
            item.setCheckState(Qt.CheckState.Unchecked)

    def _on_tweaks_item_changed(self, item: QTableWidgetItem, tab_type: str) -> None:
        if item.column() != 0:
            return
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        lbl: QLabel = self._widget.findChild(QLabel, f"_selected_{tab_type}")
        if not table or not lbl:
            return
        checked = sum(
            1 for r in range(table.rowCount())
            if table.item(r, 0) is not None
            and table.item(r, 0).checkState() == Qt.CheckState.Checked
        )
        lbl.setText(f"{checked} selected")

    def _registry_path_for_tweak(self, tweak: dict) -> str:
        for step in tweak.get("steps", []):
            if step.get("type") in ("registry", "registry_delete"):
                key, value = step.get("key", ""), step.get("value", "")
                return f"{key}\\{value}" if value else key
        return ""

    def _on_tweaks_context_menu(self, pos, tab_type: str) -> None:
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return
        index = table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        id_item = table.item(row, 0)
        if id_item is None:
            return
        tweak_id = id_item.data(Qt.ItemDataRole.UserRole)
        tweaks = self._all_tweaks if tab_type == "tweak" else self._ai_tweaks
        tweak = next((t for t in tweaks if t.get("id") == tweak_id), None)
        if not tweak:
            return
        menu = QMenu(table)
        reg_path = self._registry_path_for_tweak(tweak)
        if reg_path:
            act = menu.addAction("Copy registry path")
            act.triggered.connect(lambda: QApplication.clipboard().setText(reg_path))
        else:
            steps = tweak.get("steps", [])
            cmd = (steps[0].get("cmd") or steps[0].get("command", "")) if steps else ""
            act = menu.addAction("Copy command")
            act.triggered.connect(lambda: QApplication.clipboard().setText(cmd))
        menu.exec(table.viewport().mapToGlobal(pos))

    def _on_preset(self, preset_name: str, tab_type: str) -> None:
        """Check every row this preset's JSON file selects. Replaces the
        old hardcoded category sets (audit V03) — `debloat_presets` reads
        the same curated files the Light/Full/Privacy names promise."""
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return
        tweaks = self._all_tweaks if tab_type == "tweak" else self._ai_tweaks

        if preset_name == "custom":
            self._load_custom_preset_into(table, tweaks)
            return

        preset = dp.load_preset(preset_name)
        selected_ids = dp.resolve_tweak_ids(preset, tweaks)
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item is None:
                continue
            item_id = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked if item_id in selected_ids
                else Qt.CheckState.Unchecked)

    def _load_custom_preset_into(self, table: QTableWidget,
                                 tweaks: List[dict]) -> None:
        try:
            preset = dp.load_preset("custom")
        except (OSError, ValueError) as exc:
            logger.warning("Could not load the Custom preset: %s", exc)
            QMessageBox.information(
                self._widget, "No Custom preset saved yet",
                "Check the tweaks you want, then use “Save as "
                "Custom” before Custom has anything to load.")
            return
        selected_ids = dp.resolve_tweak_ids(preset, tweaks)
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item is None:
                continue
            item_id = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked if item_id in selected_ids
                else Qt.CheckState.Unchecked)

    def _on_save_tweaks_as_custom(self, tab_type: str) -> None:
        """P03: Custom was a silent no-op. Save the checked rows as the
        Custom preset, grouped by each tweak's own category so
        resolve_tweak_ids can find them again."""
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return
        tweaks = self._all_tweaks if tab_type == "tweak" else self._ai_tweaks
        by_id = {t.get("id", ""): t for t in tweaks}
        checked_ids = [
            table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            for r in range(table.rowCount())
            if table.item(r, 0) is not None
            and table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]
        by_category: Dict[str, List[str]] = {}
        for tid in checked_ids:
            category = by_id.get(tid, {}).get("category", "")
            by_category.setdefault(category, []).append(tid)

        existing = {}
        try:
            existing = dp.load_preset("custom")
        except (OSError, ValueError):
            logger.debug("No existing Custom preset to merge with", exc_info=True)
        merged_tweaks = dict(existing.get("tweaks", {}))
        merged_tweaks.update(by_category)

        dp.save_custom_tweaks_and_apps(
            merged_tweaks, existing.get("apps", {"remove": []}))
        status_lbl = self._status_lbl_for(tab_type)
        if status_lbl:
            status_lbl.setText(
                f"Saved {len(checked_ids)} tweak(s) to the Custom preset")

    def _on_revert_tweaks(self) -> None:
        """T20: same RestoreManagerDialog Task 13 wired for the Apps tab's
        post-apply dialog (see _on_apps_applied above) -- constructed with
        (self.app, self._widget), never self.app.backup."""
        from ui.restore_manager import RestoreManagerDialog
        RestoreManagerDialog(self.app, self._widget).exec()

    def _status_lbl_for(self, tab_type: str) -> Optional[QLabel]:
        return self._widget.findChild(QLabel, f"_status_{tab_type}")

    def _on_cancel_tweaks_apply(self, tab_type: str) -> None:
        worker = self._apply_tweaks_workers.get(tab_type)
        if worker is not None:
            worker.cancel()
        btn: QPushButton = self._widget.findChild(QPushButton, f"_cancel_apply_{tab_type}")
        if btn:
            btn.setEnabled(False)

    def _on_apply_tweaks(self, tab_type: str) -> None:
        if not self.require_admin():
            return
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return

        selected_ids = []
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected_ids.append(item.data(Qt.ItemDataRole.UserRole))

        if not selected_ids:
            return

        if tab_type == "tweak":
            tweaks = self._all_tweaks
        else:
            tweaks = self._ai_tweaks

        names = [next((t.get("name", eid) for t in tweaks if t.get("id") == eid), eid)
                for eid in selected_ids]
        if not confirm_destructive(
                self._widget, "Apply Tweaks",
                f"Apply {len(names)} tweak(s)?", detail=self._preview(names)):
            return

        progress: QProgressBar = self._widget.findChild(QProgressBar, f"_progress_{tab_type}")
        cancel_btn: QPushButton = self._widget.findChild(QPushButton, f"_cancel_apply_{tab_type}")
        if progress:
            progress.setVisible(True)
            progress.setRange(0, len(selected_ids))
            progress.setValue(0)
        if cancel_btn:
            cancel_btn.setVisible(True)
            cancel_btn.setEnabled(True)

        def work(w: Worker):
            backup = self.app.backup
            engine = TweakEngine(backup)
            rp_id = backup.create_restore_point(
                f"Debloat tweaks {datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}", "Debloat")
            success, failures = 0, []
            logger.info("Debloat tweaks: applying %d tweak(s) [%s tab]", len(selected_ids), tab_type)
            for i, eid in enumerate(selected_ids):
                if w.is_cancelled:
                    break
                tweak = next((t for t in tweaks if t.get("id") == eid), None)
                if tweak:
                    logger.info("Applying tweak: %s", tweak.get("name", eid))
                    if engine.apply_tweak(tweak, rp_id):
                        success += 1
                    else:
                        failures.append((tweak.get("name", eid), "apply_tweak returned False"))
                w.signals.progress.emit(i + 1)
            logger.info("Debloat tweaks: applied %d/%d", success, len(selected_ids))
            return {"success": success, "total": len(selected_ids), "failures": failures}

        w = Worker(work)
        if progress:
            w.signals.progress.connect(progress.setValue)
        w.signals.result.connect(lambda result, tt=tab_type: self._on_tweaks_applied(result, tt))
        w.signals.error.connect(lambda err, tt=tab_type: self._on_tweaks_apply_error(err, tt))
        self._workers.append(w)
        self._apply_tweaks_workers[tab_type] = w
        self.app.thread_pool.start(w)

    def _on_tweaks_applied(self, result: Dict, tab_type: str = "tweak") -> None:
        progress: QProgressBar = self._widget.findChild(QProgressBar, f"_progress_{tab_type}")
        cancel_btn: QPushButton = self._widget.findChild(QPushButton, f"_cancel_apply_{tab_type}")
        if progress:
            progress.setVisible(False)
        if cancel_btn:
            cancel_btn.setVisible(False)
        logger.info(
            "Debloat tweaks complete: applied %d/%d tweak(s)", result["success"], result["total"]
        )
        text = f"Applied {result['success']} of {result['total']} tweak(s)."
        if result.get("failures"):
            text += "\n\nDid not apply:\n" + "\n".join(
                f"• {name} — {reason}" for name, reason in result["failures"][:10])
        QMessageBox.information(self._widget, "Tweaks Applied", text)
        self._populate_tweaks_table("tweak")
        self._populate_tweaks_table("ai")

    def _on_tweaks_apply_error(self, err: str, tab_type: str) -> None:
        progress: QProgressBar = self._widget.findChild(QProgressBar, f"_progress_{tab_type}")
        cancel_btn: QPushButton = self._widget.findChild(QPushButton, f"_cancel_apply_{tab_type}")
        if progress:
            progress.setVisible(False)
        if cancel_btn:
            cancel_btn.setVisible(False)
        logger.error("Debloat tweaks apply error [%s tab]: %s", tab_type, err)


class DebloatModule(CompositeModule):
    """Debloat's own tools plus the full AppX package list beside them.

    Store Apps used to be its own sidebar entry two groups away, which is a
    strange place to keep the list you want open while deciding what a curated
    blocklist should contain.
    """

    name = "Debloat"
    icon = "⚡"
    description = "Remove bloatware and manage installed Store apps"
    group = ModuleGroup.OPTIMIZE

    def __init__(self):
        super().__init__()
        from modules.store_apps.store_apps_module import StoreAppsModule

        self.children = [DebloatToolsModule(), StoreAppsModule()]
