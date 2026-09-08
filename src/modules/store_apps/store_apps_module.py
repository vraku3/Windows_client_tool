"""AppX/Microsoft Store App Manager — list and uninstall Store apps."""
import csv
import datetime
import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QItemSelectionModel, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QProgressBar, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.formatting import human_size
from core.appx_service import (
    _version_key, dedupe_by_name, dir_size_detailed, fetch_packages,
)
from core.backup_service import StepRecord
from core.base_module import BaseModule
from core.events import DEBLOAT_ITEMS_REMOVED
from core.module_groups import ModuleGroup
from core.search_provider import SearchProvider
from core.semantic_colors import semantic
from core.table_ui import (
    fit_columns_once, NumericSortItem, restore_column_widths, save_column_widths,
)
from core.worker import Worker
from core.windows_utils import ps_quote, system_root
from modules.store_apps.store_apps_search_provider import StoreAppsSearchProvider
from ui.empty_state import EmptyState

logger = logging.getLogger(__name__)
from core.widget_life import widget_is_valid

# Known system packages that should NOT be removable
SYSTEM_PACKAGES = {
    "Microsoft.Windows", "Microsoft.WindowsStore", "Microsoft.WindowsAppRuntime",
    "Microsoft.UI", "Microsoft.VCLibs", "Microsoft.NET", "Microsoft.DesktopAppInstaller"
}

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SID_RE = re.compile(r"^S-\d+(-\d+)+$")
_LOCATION_SUFFIX_RE = re.compile(r"_\w{13}$")

_IN_USE_MARKERS = (
    "0x80073cfb", "in use", "being used", "is running", "is open",
    "currently running", "is still running",
)


def is_opaque_identifier(name: str) -> bool:
    """True when a package name carries no readable meaning (GUID/SID/number)."""
    if not name:
        return False
    return bool(_GUID_RE.match(name) or _SID_RE.match(name) or name.isdigit())


def friendly_name_from_location(location: str) -> str:
    """Derive a readable name from the package's InstallLocation folder.

    Some system packages are registered under an opaque GUID name but live in
    a folder named '<RealName>_<publisherHash>', e.g.
    '1527c705-839a-4832-9118-54d4Bd6a0c89' -> 'Microsoft.Windows.FilePicker'.
    """
    if not location:
        return ""
    base = os.path.basename(location.rstrip("\\/"))
    base = _LOCATION_SUFFIX_RE.sub("", base)
    if not base or is_opaque_identifier(base):
        return ""
    return base


def resolve_sid_to_name(sid_str: str) -> str:
    """Translate a Windows SID to an account name using only local APIs."""
    try:
        import win32security
        sid = win32security.ConvertStringSidToSid(sid_str)
        name, domain, _ = win32security.LookupAccountSid(None, sid)
        return f"{domain}\\{name}" if domain else name
    except Exception as exc:                             # noqa: BLE001
        logger.warning("Could not resolve SID %s to an account name: %s",
                       sid_str, exc)
        return ""


def resolve_package_name(name: str, location: str) -> str:
    """Turn an opaque package identifier into a readable name, or return it unchanged."""
    if not is_opaque_identifier(name):
        return name
    if _SID_RE.match(name):
        resolved = resolve_sid_to_name(name)
        if resolved:
            return resolved
    return friendly_name_from_location(location) or name


def shorten_app_name(name: str) -> str:
    """Strip vendor prefixes so the meaningful tail is shown.

    Store packages follow 'Vendor.AppName', so drop the first segment and then
    trim any leftover generic 'Microsoft'/'Windows' sub-prefixes:
    'Microsoft.WindowsCalculator'  -> 'Calculator'
    'Microsoft.Windows.FilePicker' -> 'FilePicker'
    'Microsoft.BingWeather'        -> 'BingWeather'
    'SpotifyAB.SpotifyMusic'       -> 'SpotifyMusic'
    """
    short = name
    if "." in short:
        short = short.split(".", 1)[1]
    while True:
        lowered = short.lower()
        matched = None
        for prefix in ("windows.", "windows", "microsoft.", "microsoft"):
            if lowered.startswith(prefix):
                matched = prefix
                break
        if matched is None:
            break
        short = short[len(matched):].lstrip(".")
        if not short:
            break
    return short or name


def short_publisher(publisher: str) -> str:
    """Trim a DN like 'CN=X, O=Microsoft Corporation, L=Redmond, ...' to its org."""
    if not publisher:
        return ""
    for part in publisher.split(","):
        key, _, value = part.partition("=")
        if key.strip().upper() == "O" and value.strip():
            return value.strip()
    return publisher


def is_system_package(name: str, location: str) -> bool:
    """True for core Windows packages that must not be uninstalled.

    Exact-name matches plus anything installed under C:\\Windows\\SystemApps.
    Regular Store apps (Calculator, Notepad, ...) live under Program Files and
    are removable, so a name prefix alone is too broad.
    """
    if name in SYSTEM_PACKAGES:
        return True
    loc = (location or "").replace("/", "\\").lower()
    return loc.startswith(r"c:\windows\systemapps")


def failure_hint(output: str) -> str:
    """Return a hint when an uninstall failed because the app is running."""
    low = output.lower()
    if any(marker in low for marker in _IN_USE_MARKERS):
        return "The app may be running. Close it and try again."
    return ""


def verify_uninstalled(name: str) -> Tuple[bool, str]:
    """Positive evidence, not an assumed exit code -- Remove-AppxPackage
    exits 0 while removing nothing (documented for the Tweaks Apps tab;
    Store Apps never had the same check). Re-reads the live package list
    rather than trusting the removal command's own return code."""
    still_there = any(p.get("Name") == name for p in fetch_packages(use_cache=False))
    if still_there:
        return False, f"{name} is still installed after the removal call"
    return True, ""


def _debloat_catalog_packages() -> Set[str]:
    """P08/S30: the package set Debloat's own catalog (`debloat.json`)
    names, read once and cheaply (126 entries) so Store Apps can flag a
    row as "also in the Debloat catalog" without importing DebloatModule."""
    import json
    path = os.path.join(os.path.dirname(__file__), "..", "tweaks",
                        "definitions", "debloat.json")
    try:
        with open(path, encoding="utf-8") as f:
            return {e["package"] for e in json.load(f) if e.get("package")}
    except (OSError, json.JSONDecodeError):
        return set()


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem that compares case-insensitively for alpha sorting."""

    def __lt__(self, other) -> bool:
        return self.text().lower() < other.text().lower()


class _SizeSignals(QObject):
    """Thread-safe channel for background size-scan results."""

    size_ready = pyqtSignal(str, int)  # package_name, bytes


class StoreAppsModule(BaseModule):
    name = "Store Apps"
    icon = "📦"
    description = "Manage Microsoft Store (AppX) applications"
    group = ModuleGroup.MANAGE
    #: Reading and every non-destructive action needs no elevation; a
    #: write is refused by require_admin() with a message pointing at the
    #: "Restart as Admin" banner, not a silent failure.
    requires_admin = True
    #: Listing is safe to read unelevated; uninstall needs elevation and is
    #: gated via `require_admin()`.
    read_only_unelevated = True

    _CONFIG_PREFIX = "modules.store_apps"
    _FILTERS = ["All Apps", "Removable", "System"]

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._apps: List[dict] = []
        self._worker: Optional[Worker] = None
        self._uninstall_worker: Optional[Worker] = None
        self._size_pool: Optional[ThreadPoolExecutor] = None
        self._busy = False
        self._load_error = ""
        self._show_pfn = False
        self._show_arch = False
        self._size_signals = _SizeSignals()
        self._size_signals.size_ready.connect(self._on_size_ready)
        self._debloat_packages: Set[str] = set()
        self._row_index: Dict[str, int] = {}
        #: C07 follow-up: has `_on_apps_loaded()` already run its one-time
        #: resizeColumnsToContents() fit this session? See `_on_apps_loaded`.
        self._columns_fitted_this_session = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        self._show_pfn = bool(self.app.config.get(f"{self._CONFIG_PREFIX}.show_pfn", False))
        self._show_arch = bool(self.app.config.get(f"{self._CONFIG_PREFIX}.show_arch", False))

        # Toolbar
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._load_apps)
        toolbar.addWidget(refresh_btn)
        self._last_refreshed_lbl = QLabel("")
        toolbar.addWidget(self._last_refreshed_lbl)
        toolbar.addWidget(self._build_auto_refresh_label())
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(200)
        self._progress.setVisible(False)
        toolbar.addWidget(self._progress)
        self._cancel_btn = QPushButton("✕ Cancel")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._cancel_batch)
        toolbar.addWidget(self._cancel_btn)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search apps…")
        self._search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._search)
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(self._FILTERS)
        saved_filter = int(self.app.config.get(f"{self._CONFIG_PREFIX}.filter", 0) or 0)
        saved_filter = max(0, min(saved_filter, len(self._FILTERS) - 1))
        self._filter_combo.setCurrentIndex(saved_filter)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_combo)
        toolbar.addWidget(self._build_columns_menu())
        export_btn = QPushButton("💾 Export")
        export_btn.clicked.connect(self._export)
        toolbar.addWidget(export_btn)
        shortcuts_btn = QPushButton("⌨ Shortcuts")
        shortcuts_btn.setObjectName("_shortcuts_btn")
        shortcuts_btn.clicked.connect(self._show_shortcuts_legend)
        toolbar.addWidget(shortcuts_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Table stacked with empty/error state
        self._table_stack = QStackedWidget()
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Name", "Publisher", "Version", "Size", "User-Removable",
            "Package Family", "Architecture",
        ])
        header = self._table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        # Name, Publisher and (when shown) Family share the free width.
        # Version/Size/User-Removable/Architecture are Interactive, not
        # ResizeToContents -- only Interactive can hold a manually-set or
        # persisted width (core/table_ui.py). The one-time content fit on
        # first real population (below, in _on_apps_loaded()) keeps a fresh
        # install looking exactly as it did under ResizeToContents.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self._table.setSortingEnabled(True)
        saved_col = int(self.app.config.get(f"{self._CONFIG_PREFIX}.sort_column", 0) or 0)
        saved_order = int(self.app.config.get(f"{self._CONFIG_PREFIX}.sort_order",
                                              Qt.SortOrder.AscendingOrder.value))
        header.setSortIndicator(saved_col, Qt.SortOrder(saved_order))
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setColumnHidden(5, not self._show_pfn)
        self._table.setColumnHidden(6, not self._show_arch)
        # C07: restore any column widths saved from a previous session.
        restore_column_widths(self._table, self.app.config.get, self._CONFIG_PREFIX)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table_stack.addWidget(self._table)

        self._empty = EmptyState(
            "📦", "No apps loaded",
            "Click Refresh to list installed Store apps.",
            "Refresh",
        )
        self._empty.action_triggered.connect(self._load_apps)
        self._table_stack.addWidget(self._empty)
        self._table_stack.setCurrentIndex(1)
        layout.addWidget(self._table_stack)

        # Bottom toolbar
        bottom = QHBoxLayout()
        self._uninstall_btn = QPushButton("🗑️ Uninstall Selected")
        self._uninstall_btn.setObjectName("_uninstall_btn")
        self._uninstall_btn.setStyleSheet(f"color: {semantic('error')}; font-weight: bold;")
        self._uninstall_btn.setToolTip(
            "Uninstall the selected app(s)  (Del)\n\n"
            "Removes for every user on this machine.")
        self._uninstall_btn.clicked.connect(self._uninstall)
        bottom.addWidget(self._uninstall_btn)
        select_btn = QPushButton("☑ Select Non-System")
        select_btn.clicked.connect(self._select_non_system)
        bottom.addWidget(select_btn)
        clear_btn = QPushButton("☐ Clear Selection")
        clear_btn.clicked.connect(self._table.clearSelection)
        bottom.addWidget(clear_btn)
        bottom.addStretch()
        layout.addLayout(bottom)

        self._install_shortcuts()
        return self._widget

    def _build_auto_refresh_label(self) -> QLabel:
        interval = self.get_refresh_interval()
        lbl = QLabel(f"Auto-refreshes every {interval // 1000}s")
        lbl.setObjectName("muted")
        return lbl

    def _build_columns_menu(self) -> QPushButton:
        btn = QPushButton("⛭ Columns")
        menu = QMenu(btn)
        act_pfn = menu.addAction("Package Family Name")
        act_pfn.setCheckable(True)
        act_pfn.setChecked(self._show_pfn)
        act_pfn.toggled.connect(self._toggle_pfn)
        act_arch = menu.addAction("Architecture")
        act_arch.setCheckable(True)
        act_arch.setChecked(self._show_arch)
        act_arch.toggled.connect(self._toggle_arch)
        btn.setMenu(menu)
        return btn

    def _toggle_pfn(self, checked: bool) -> None:
        self._show_pfn = checked
        self._table.setColumnHidden(5, not checked)
        self.app.config.set(f"{self._CONFIG_PREFIX}.show_pfn", checked)

    def _toggle_arch(self, checked: bool) -> None:
        self._show_arch = checked
        self._table.setColumnHidden(6, not checked)
        self.app.config.set(f"{self._CONFIG_PREFIX}.show_arch", checked)

    def on_start(self, app) -> None:
        self.app = app
        self._debloat_packages = _debloat_catalog_packages()

    def get_refresh_interval(self) -> Optional[int]:
        return 120_000

    def refresh_data(self) -> None:
        self._load_apps()

    def get_search_provider(self) -> Optional[SearchProvider]:
        # A fresh instance per call, same as DebloatSearchProvider -- but
        # unlike that one, this needs a LIVE handle: there is no catalog
        # file to fall back on, only whatever the last scan found. Passing
        # the module itself means every instance, however many are built,
        # reads self._apps fresh at search time rather than a snapshot
        # taken here.
        return StoreAppsSearchProvider(self)

    def on_deactivate(self) -> None:
        self._persist_sort()
        if self._widget_valid(getattr(self, "_table", None)):
            save_column_widths(self._table, self.app.config.set, self._CONFIG_PREFIX)
        self.cancel_all_workers()

    def on_stop(self) -> None:
        if self._size_pool is not None:
            self._size_pool.shutdown(wait=False)
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if rows:
            total = sum(NumericSortItem.value(self._table.item(r, 3)) or 0
                       for r in rows)
            return (f"Store Apps — {len(rows)} selected, "
                    f"~{human_size(int(total))}")
        return f"Store Apps — {len(self._apps)} installed"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_apps(self):
        # The auto-refresh timer can tick before this tab has ever been
        # opened, and a composite builds a child's widget only on first
        # show — there is then nothing to load into.
        if self._widget is None:
            return
        if self._busy:
            return
        state = self._capture_state()
        self._progress.setVisible(True)
        self._load_error = ""

        def do_load(worker):
            del worker
            # Shared AppX service: one query + -AllUsers fallback for
            # unelevated runs, cached briefly. Forced fresh on load so a
            # scan always reflects what is installed right now.
            return dedupe_by_name(fetch_packages(use_cache=False))

        self._worker = Worker(do_load)
        self._worker.signals.result.connect(lambda apps: self._on_apps_loaded(apps, state))
        self._worker.signals.error.connect(self._on_load_error)
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _on_apps_loaded(self, apps, state) -> None:
        if not self._widget_valid(self._table_stack):
            return
        self._progress.setVisible(False)

        # The shared service already dedupes -AllUsers rows by newest version.
        self._apps = apps

        if not apps:
            self._set_empty("📦", "No Store apps found",
                            "Nothing to show for this machine. Click Refresh to scan again.")
            return

        self._table_stack.setCurrentIndex(0)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._row_index = {}

        for app in sorted(apps, key=lambda a: a.get("Name", "").lower()):
            name = app.get("Name", "")
            location = app.get("InstallLocation", "")
            publisher = app.get("Publisher", "")
            version = app.get("Version", "")

            is_system = is_system_package(name, location)

            full_resolved = resolve_package_name(name, location)
            display_name = shorten_app_name(full_resolved)

            row = self._table.rowCount()
            self._table.insertRow(row)
            self._row_index[name] = row

            name_item = _SortableItem(display_name)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name_item.setData(Qt.ItemDataRole.UserRole, name)
            tip = []
            if display_name != full_resolved:
                tip.append(full_resolved)
            if full_resolved != name:
                tip.append(f"Package: {name}")
            if tip:
                name_item.setToolTip("\n".join(tip))
            if name in self._debloat_packages:
                name_item.setToolTip("Known bloatware — also in the Debloat catalog\n"
                                     + (name_item.toolTip() or ""))
            self._table.setItem(row, 0, name_item)

            pub_item = QTableWidgetItem(short_publisher(publisher))
            pub_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            pub_item.setToolTip(publisher)
            self._table.setItem(row, 1, pub_item)

            ver_text = version[:20] if version else ""
            ver_item = NumericSortItem(ver_text, _version_key(version) or (0,))
            ver_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, ver_item)

            size_item = NumericSortItem("…", 0)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            size_item.setData(Qt.ItemDataRole.UserRole, name)
            self._table.setItem(row, 3, size_item)

            removable = "✅ Yes" if not is_system else "❌ System"
            rem_item = QTableWidgetItem(removable)
            rem_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_system:
                rem_item.setForeground(QColor(semantic("warning")))
            else:
                rem_item.setForeground(QColor(semantic("success")))
            reason = ("an exact-match core Windows package"
                     if name in SYSTEM_PACKAGES else
                     f"installed under {system_root()}\\SystemApps")
            rem_item.setToolTip(
                f"System packages cannot be uninstalled without breaking "
                f"Windows ({reason}). Removes for every user on this machine.")
            self._table.setItem(row, 4, rem_item)

            pfn_item = QTableWidgetItem(app.get("PackageFamilyName", ""))
            pfn_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 5, pfn_item)

            arch_item = QTableWidgetItem(app.get("Architecture", ""))
            arch_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 6, arch_item)

        self._table.setSortingEnabled(True)

        # C07 follow-up: fit Interactive columns to content ONCE, on the
        # first real population this session, when nothing was persisted
        # for them -- never again after that. _on_apps_loaded runs on every
        # manual Refresh and every 120s auto-refresh tick; doing this every
        # time would silently undo an in-session column drag, the same
        # class of bug Task 35 fixed for sort order reverting.
        if not self._columns_fitted_this_session:
            self._columns_fitted_this_session = True
            fit_columns_once(self._table, self.app.config.get, self._CONFIG_PREFIX)

        self._restore_state(state)
        self._apply_filter()
        self._start_size_scan()
        self._last_refreshed_lbl.setText(f"Refreshed {datetime.datetime.now():%H:%M}")

    def _on_load_error(self, err: str) -> None:
        self._progress.setVisible(False)
        self._load_error = str(err)
        logger.error("Store Apps scan error: %s", err)
        self._set_empty("⚠️", "Scan failed", str(err))

    def _set_empty(self, glyph: str, title: str, hint: str) -> None:
        old = self._table_stack.widget(1)
        if old is not None:
            self._table_stack.removeWidget(old)
            old.deleteLater()
        empty = EmptyState(glyph, title, hint, "Refresh")
        empty.action_triggered.connect(self._load_apps)
        self._table_stack.addWidget(empty)
        self._table_stack.setCurrentIndex(1)

    # ------------------------------------------------------------------
    # Filtering / selection
    # ------------------------------------------------------------------

    def _apply_filter(self):
        raw = self._search.text().strip()
        field, _, value = raw.partition(":")
        scoped = field.lower() in ("publisher", "name") and bool(value)
        query = (value if scoped else raw).lower()
        mode = self._filter_combo.currentIndex()
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if name_item is None:
                continue
            display = name_item.text()
            real = name_item.data(Qt.ItemDataRole.UserRole) or display
            pub_item = self._table.item(row, 1)
            publisher = pub_item.text() if pub_item else ""
            removable = self._table.item(row, 4).text()
            is_system = "System" in removable

            visible = True
            if mode == 1 and is_system:
                visible = False
            elif mode == 2 and not is_system:
                visible = False
            if query:
                if scoped and field.lower() == "publisher":
                    haystack = publisher.lower()
                elif scoped:
                    haystack = " ".join([display, real]).lower()
                else:
                    haystack = " ".join([display, real, publisher]).lower()
                if query not in haystack:
                    visible = False
            self._table.setRowHidden(row, not visible)

    def _on_filter_changed(self, index: int) -> None:
        self._apply_filter()
        self.app.config.set(f"{self._CONFIG_PREFIX}.filter", int(index))

    def _select_non_system(self):
        self._table.clearSelection()
        selection = self._table.selectionModel()
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            item = self._table.item(row, 4)
            if item and "System" not in item.text():
                selection.select(
                    self._table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    def _clear(self):
        self._search.clear()
        self._table.clearSelection()

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        name_item = self._table.item(row, 0)
        if name_item is None:
            return
        package_name = name_item.data(Qt.ItemDataRole.UserRole) or name_item.text()
        app = self._app_for(package_name)

        menu = QMenu(self._table)
        act_uninstall = menu.addAction("🗑️ Uninstall")
        act_uninstall.triggered.connect(self._uninstall)
        menu.addSeparator()
        act_copy_name = menu.addAction("Copy package name")
        act_copy_name.triggered.connect(
            lambda: QApplication.clipboard().setText(package_name))
        act_copy_cmd = menu.addAction("Copy uninstall command")
        act_copy_cmd.triggered.connect(
            lambda: QApplication.clipboard().setText(
                f"Get-AppxPackage -Name '{ps_quote(package_name)}' | Remove-AppxPackage -AllUsers"))
        location = (app or {}).get("InstallLocation", "")
        pfn = (app or {}).get("PackageFamilyName", "")
        act_copy_pfn = menu.addAction("Copy Package Family Name")
        act_copy_pfn.setEnabled(bool(pfn))
        act_copy_pfn.triggered.connect(lambda: QApplication.clipboard().setText(pfn))
        act_copy_loc = menu.addAction("Copy install location")
        act_copy_loc.setEnabled(bool(location))
        act_copy_loc.triggered.connect(lambda: QApplication.clipboard().setText(location))
        menu.addSeparator()

        def _open_folder():
            if not location or not os.path.isdir(location):
                QMessageBox.information(
                    self._table, "Folder not found",
                    f"{location or '(no location recorded)'} no longer exists.")
                return
            os.startfile(location)
        act_open_folder = menu.addAction("Open install folder")
        act_open_folder.setEnabled(bool(location))
        act_open_folder.triggered.connect(_open_folder)
        act_store = menu.addAction("Open in Microsoft Store")
        act_store.setEnabled(bool(pfn))
        act_store.setToolTip("This package has no Package Family Name" if not pfn else "")
        act_store.triggered.connect(
            lambda: os.startfile(f"ms-windows-store://pdp/?PFN={pfn}"))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _app_for(self, package_name: str) -> Optional[dict]:
        for app in self._apps:
            if app.get("Name") == package_name:
                return app
        return None

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    def _selected_targets(self) -> Tuple[List[Tuple[str, str, str]], List[str]]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        targets, skipped_names = [], []
        for r in rows:
            name_cell = self._table.item(r, 0)
            if name_cell is None:
                continue
            display = name_cell.text()
            name = name_cell.data(Qt.ItemDataRole.UserRole) or display
            removable_cell = self._table.item(r, 4)
            if removable_cell is None:
                continue
            removable = removable_cell.text()
            if "System" in removable:
                skipped_names.append(display)
                continue
            pfn_item = self._table.item(r, 5)
            pfn = pfn_item.text() if pfn_item else ""
            targets.append((name, display, pfn))
        return targets, skipped_names

    def _row_size_bytes(self, package_name: str) -> int:
        row = self._row_of(package_name)
        if row < 0:
            return 0
        item = self._table.item(row, 3)
        value = NumericSortItem.value(item) if item else None
        return int(value) if value else 0

    def _uninstall_confirmation_text(self, names: List[str],
                                     skipped_names: List[str],
                                     total_bytes: int) -> str:
        message = f"Uninstall {len(names)} app(s)?\n\n{self._preview(names)}"
        if total_bytes > 0:
            message += f"\n\nThis will free approximately {human_size(total_bytes)}."
        if skipped_names:
            message += ("\n\nSkipped (system apps): "
                       + ", ".join(skipped_names[:10])
                       + ("…" if len(skipped_names) > 10 else ""))
        message += "\n\nThis cannot be undone."
        return message

    def _uninstall(self):
        if not self.require_admin():
            return
        targets, skipped_names = self._selected_targets()
        if not targets:
            QMessageBox.warning(self._widget, "No Selection",
                                "Select app(s) to uninstall.")
            return

        names = [d for _, d, _ in targets]
        total_bytes = sum(self._row_size_bytes(name) for name, _, _ in targets)
        message = self._uninstall_confirmation_text(names, skipped_names, total_bytes)
        reply = QMessageBox.warning(
            self._widget, "Uninstall Apps", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._busy = True
        self._progress.setVisible(True)
        self._cancel_btn.setVisible(True)
        self._progress.setRange(0, len(targets))
        self._progress.setValue(0)

        def do_uninstall(worker):
            backup = self.app.backup
            rp_id = backup.create_restore_point(
                f"Store Apps uninstall {datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "Store Apps",
            )
            results = []
            steps = []
            for i, (name, display, pfn) in enumerate(targets):
                if worker.is_cancelled:
                    break
                store_link = f"ms-windows-store://pdp/?PFN={ps_quote(pfn)}" if pfn else ""
                backup.backup_appx_package(name, rp_id, store_link=store_link)
                result = subprocess.run([
                    "powershell", "-Command",
                    f"Get-AppxPackage '{ps_quote(name)}' | Remove-AppxPackage -AllUsers"
                ], capture_output=True, text=True, timeout=120,
                   creationflags=subprocess.CREATE_NO_WINDOW)
                # The Store deep link rides on revert_command so restoring this
                # point opens the app's Store page when winget cannot find it.
                steps.append(StepRecord("appx", name, name, None,
                                        revert_command=store_link))
                verified_ok, verify_reason = verify_uninstalled(name)
                ok = result.returncode == 0 and verified_ok
                output = result.stdout + result.stderr
                if result.returncode == 0 and not verified_ok:
                    output += f"\n{verify_reason}"
                results.append((display, name, ok, output))
                worker.signals.progress.emit(i + 1)
            if steps:
                try:
                    backup.record_steps("store_apps_batch", steps, rp_id)
                except Exception as e:
                    logger.warning("Failed to record appx steps: %s", e)
            return results

        self._uninstall_worker = Worker(do_uninstall)
        self._uninstall_worker.signals.progress.connect(self._progress.setValue)
        self._uninstall_worker.signals.result.connect(self._on_uninstall_done)
        self._uninstall_worker.signals.error.connect(self._on_uninstall_error)
        self._workers.append(self._uninstall_worker)
        self.app.thread_pool.start(self._uninstall_worker)

    def _on_uninstall_done(self, results) -> None:
        self._busy = False
        self._progress.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)

        if not results:
            return

        failed = [(display, output) for _, display, ok, output in results if not ok]
        ok_count = len(results) - len(failed)

        succeeded = [display for _, display, ok, _ in results if ok]
        if succeeded:
            self.app.event_bus.publish(DEBLOAT_ITEMS_REMOVED, succeeded)

        if failed:
            lines = []
            for display, output in failed[:5]:
                hint = failure_hint(output)
                lines.append(f"• {display}" + (f" — {hint}" if hint else ""))
            QMessageBox.critical(
                self._widget, "Uninstall Failed",
                f"Uninstalled {ok_count} of {len(results)} app(s).\n\n"
                + "\n".join(lines),
            )
        else:
            QMessageBox.information(
                self._widget, "Uninstalled",
                f"Uninstalled {ok_count} app(s).\nA restore point was created.",
            )
        self._load_apps()

    def _on_uninstall_error(self, err: str) -> None:
        self._busy = False
        self._progress.setVisible(False)
        self._cancel_btn.setVisible(False)
        QMessageBox.critical(self._widget, "Uninstall Failed", str(err))

    def _cancel_batch(self):
        if self._uninstall_worker is not None:
            self._uninstall_worker.cancel()
        self._cancel_btn.setEnabled(False)

    def _preview(self, names: List[str], limit: int = 10) -> str:
        shown = names[:limit]
        text = "\n".join(f"  • {d}" for d in shown)
        extra = len(names) - len(shown)
        if extra > 0:
            text += f"\n  …and {extra} more"
        return text

    # ------------------------------------------------------------------
    # Sizes (background, best-effort)
    # ------------------------------------------------------------------

    def _start_size_scan(self):
        # Lazily create the pool once, but ALWAYS resubmit fresh scan tasks
        # for the current self._apps on every call -- this runs at the end
        # of every _on_apps_loaded (initial load AND every manual Refresh),
        # so refusing to run once the pool exists would silently stop size
        # scanning forever after the first successful call.
        if self._size_pool is None:
            self._size_pool = ThreadPoolExecutor(
                max_workers=8, thread_name_prefix="appx-size")
        for app in self._apps:
            self._size_pool.submit(self._scan_one_size, app.get("Name", ""),
                                   app.get("InstallLocation", ""))

    def _scan_one_size(self, name: str, location: str) -> None:
        size, approximate = self._dir_size_detailed(location)
        self._size_signals.size_ready.emit(
            name, size if not approximate else -abs(size) - 1)

    @staticmethod
    def _dir_size_detailed(path: str, max_entries: int = 30000) -> Tuple[int, bool]:
        return dir_size_detailed(path, max_entries)

    def _on_size_ready(self, name: str, size: int) -> None:
        if not self._widget_valid(self._table):
            return
        row = self._row_of(name)
        if row < 0:
            logger.debug("Size for %s arrived but it is no longer in the table "
                        "(removed or refreshed away)", name)
            return
        approximate = size < 0
        real_size = -size - 1 if approximate else size
        text = ("~" + human_size(real_size)) if approximate else human_size(real_size)
        self._table.setItem(row, 3, NumericSortItem(text, max(real_size, 0)))

    # ------------------------------------------------------------------
    # State preservation (selection / sort / scroll / filter)
    # ------------------------------------------------------------------

    def _capture_state(self) -> Optional[dict]:
        if not self._widget_valid(self._table):
            return None
        selected = []
        for idx in self._table.selectedIndexes():
            item = self._table.item(idx.row(), 0)
            if item:
                name = item.data(Qt.ItemDataRole.UserRole)
                if name and name not in selected:
                    selected.append(name)
        header = self._table.horizontalHeader()
        return {
            "selected": selected,
            "sort_col": header.sortIndicatorSection(),
            "sort_order": header.sortIndicatorOrder(),
            "scroll": self._table.verticalScrollBar().value(),
        }

    def _restore_state(self, state: Optional[dict]) -> None:
        if not state or not self._widget_valid(self._table):
            return
        header = self._table.horizontalHeader()
        col = int(state.get("sort_col", 0))
        order = state.get("sort_order", Qt.SortOrder.AscendingOrder)
        if 0 <= col < self._table.columnCount():
            header.setSortIndicator(col, order)
        self._table.sortItems(col if 0 <= col < self._table.columnCount() else 0, order)
        for name in state.get("selected", []):
            row = self._row_of(name)
            if row >= 0:
                self._table.selectionModel().select(
                    self._table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        self._table.verticalScrollBar().setValue(int(state.get("scroll", 0)))

    def _persist_sort(self) -> None:
        table = getattr(self, "_table", None)
        if table is None or not self._widget_valid(table):
            return
        header = table.horizontalHeader()
        self.app.config.set(f"{self._CONFIG_PREFIX}.sort_column",
                            int(header.sortIndicatorSection()))
        self.app.config.set(f"{self._CONFIG_PREFIX}.sort_order",
                            int(header.sortIndicatorOrder().value))

    def _row_of(self, package_name: str) -> int:
        cached = self._row_index.get(package_name, -1)
        if cached >= 0 and cached < self._table.rowCount() and \
                self._table.item(cached, 0) and \
                self._table.item(cached, 0).data(Qt.ItemDataRole.UserRole) == package_name:
            return cached
        for r in range(self._table.rowCount()):  # fallback: index stale/missing
            it = self._table.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == package_name:
                self._row_index[package_name] = r
                return r
        return -1

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export(self):
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            rows = list(range(self._table.rowCount()))

        targets = []
        for r in rows:
            display = self._table.item(r, 0).text()
            name = self._table.item(r, 0).data(Qt.ItemDataRole.UserRole) or display
            publisher = self._table.item(r, 1).text()
            if "System" in self._table.item(r, 4).text():
                continue
            targets.append((name, display, publisher))

        if not targets:
            QMessageBox.information(
                self._widget, "Nothing to Export",
                "No uninstallable apps to export.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Export Apps", "appx_export.ps1",
            "PowerShell script (*.ps1);;CSV file (*.csv)",
        )
        if not path:
            return

        try:
            if path.lower().endswith(".csv"):
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Package Name", "Display Name", "Publisher",
                                    "Version", "Size", "Architecture"])
                    for r in rows:
                        if "System" in self._table.item(r, 4).text():
                            continue
                        writer.writerow([
                            self._table.item(r, 0).data(Qt.ItemDataRole.UserRole)
                            or self._table.item(r, 0).text(),
                            self._table.item(r, 0).text(),
                            self._table.item(r, 1).text(),
                            self._table.item(r, 2).text(),
                            self._table.item(r, 3).text(),
                            self._table.item(r, 6).text(),
                        ])
            else:
                lines = [
                    "# Generated by Windows Client Tool",
                    "# Run this as Administrator to remove the listed apps for all users.",
                    "",
                ]
                for name, _, _ in targets:
                    lines.append(f"Get-AppxPackage -Name '{ps_quote(name)}' | Remove-AppxPackage -AllUsers")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
        except OSError as e:
            QMessageBox.critical(self._widget, "Export Failed", str(e))
            return

        QMessageBox.information(
            self._widget, "Exported",
            f"Exported {len(targets)} app(s) to:\n{path}\n\n"
            f"To remove them now instead of running the script later, "
            f"select the same apps here and use “Uninstall Selected.”")

    # ------------------------------------------------------------------
    # Shortcuts / helpers
    # ------------------------------------------------------------------

    def _show_shortcuts_legend(self):
        QMessageBox.information(
            self._widget, "Keyboard Shortcuts",
            "Ctrl+U / Delete — Uninstall selected app(s)\n"
            "Ctrl+E — Export\n"
            "Ctrl+F — Search\n"
            "Escape — Clear selection / search",
        )

    def _install_shortcuts(self):
        for seq, slot in (
            ("Ctrl+U", self._uninstall),
            ("Delete", self._uninstall),
            ("Ctrl+E", self._export),
            ("Ctrl+F", self._search.setFocus),
            ("Escape", self._clear),
        ):
            shortcut = QShortcut(QKeySequence(seq), self._widget)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    @staticmethod
    def _widget_valid(widget) -> bool:
        return widget_is_valid(widget)
