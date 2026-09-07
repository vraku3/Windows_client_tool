"""AppX/Microsoft Store App Manager — list and uninstall Store apps."""
import csv
import datetime
import logging
import os
import re
import subprocess
import threading
from typing import List, Optional, Set, Tuple

from PyQt6.QtCore import QItemSelectionModel, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLineEdit, QMenu, QMessageBox,
    QProgressBar, QPushButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.formatting import human_size
from core.appx_service import _version_key, dedupe_by_name, dir_size, fetch_packages
from core.backup_service import StepRecord
from core.base_module import BaseModule
from core.events import DEBLOAT_ITEMS_REMOVED
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import NumericSortItem
from core.worker import Worker
from core.windows_utils import ps_quote
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
        self._size_thread: Optional[threading.Thread] = None
        self._busy = False
        self._load_error = ""
        self._show_pfn = False
        self._show_arch = False
        self._size_signals = _SizeSignals()
        self._size_signals.size_ready.connect(self._on_size_ready)
        self._debloat_packages: Set[str] = set()

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
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setColumnHidden(5, not self._show_pfn)
        self._table.setColumnHidden(6, not self._show_arch)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.setStyleSheet("""
            QTableWidget { background: #2d2d2d; border: 1px solid #3c3c3c; border-radius: 4px; }
            QTableWidget::item { padding: 3px; }
            QTableWidget::item:selected { background: #094771; }
            QHeaderView::section { background: #3c3c3c; color: #b0b0b0; padding: 4px; border: none; }
        """)
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
        uninstall_btn = QPushButton("🗑️ Uninstall Selected")
        uninstall_btn.setStyleSheet("color: #f48771; font-weight: bold;")
        uninstall_btn.clicked.connect(self._uninstall)
        bottom.addWidget(uninstall_btn)
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

    def on_deactivate(self) -> None:
        self._persist_sort()
        self.cancel_all_workers()

    def on_stop(self) -> None:
        if self._size_thread is not None:
            self._size_thread = None
        self.cancel_all_workers()

    def get_status_info(self) -> str:
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
            rem_item.setToolTip(
                "System packages cannot be uninstalled without breaking Windows"
            )
            self._table.setItem(row, 4, rem_item)

            pfn_item = QTableWidgetItem(app.get("PackageFamilyName", ""))
            pfn_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 5, pfn_item)

            arch_item = QTableWidgetItem(app.get("Architecture", ""))
            arch_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 6, arch_item)

        self._table.setSortingEnabled(True)
        self._restore_state(state)
        self._apply_filter()
        self._start_size_scan()

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
        query = self._search.text().lower()
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
        menu.addSeparator()
        location = (app or {}).get("InstallLocation", "")
        act_open_folder = menu.addAction("Open install folder")
        act_open_folder.setEnabled(bool(location))
        act_open_folder.triggered.connect(lambda: os.startfile(location))
        pfn = (app or {}).get("PackageFamilyName", "")
        act_store = menu.addAction("Open in Microsoft Store")
        act_store.setEnabled(bool(pfn))
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

    def _selected_targets(self) -> Tuple[List[Tuple[str, str, str]], int]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        targets = []
        skipped = 0
        for r in rows:
            display = self._table.item(r, 0).text()
            name = self._table.item(r, 0).data(Qt.ItemDataRole.UserRole) or display
            removable = self._table.item(r, 4).text()
            if "System" in removable:
                skipped += 1
                continue
            pfn_item = self._table.item(r, 5)
            pfn = pfn_item.text() if pfn_item else ""
            targets.append((name, display, pfn))
        return targets, skipped

    def _uninstall(self):
        if not self.require_admin():
            return
        targets, skipped = self._selected_targets()
        if not targets:
            QMessageBox.warning(self._widget, "No Selection",
                                "Select app(s) to uninstall.")
            return

        names = [d for _, d, _ in targets]
        message = f"Uninstall {len(names)} app(s)?\n\nThis cannot be undone."
        if len(names) > 10:
            message += f"\n\n{self._preview(names)}"
        if skipped:
            message += f"\n\n{skipped} system app(s) skipped."
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
        if self._size_thread is not None and self._size_thread.is_alive():
            return
        def scan():
            for app in self._apps:
                loc = app.get("InstallLocation", "")
                size = dir_size(loc)
                self._size_signals.size_ready.emit(app.get("Name", ""), size)
        self._size_thread = threading.Thread(target=scan, daemon=True)
        self._size_thread.start()

    def _on_size_ready(self, name: str, size: int) -> None:
        if not self._widget_valid(self._table):
            return
        row = self._row_of(name)
        if row < 0:
            return
        self._table.setItem(row, 3, NumericSortItem(human_size(size), max(size, 0)))

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
                            int(header.sortIndicatorOrder()))

    def _row_of(self, package_name: str) -> int:
        for r in range(self._table.rowCount()):
            it = self._table.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == package_name:
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
                    writer.writerow(["Package Name", "Display Name", "Publisher"])
                    writer.writerows(targets)
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
            f"Exported {len(targets)} app(s) to:\n{path}",
        )

    # ------------------------------------------------------------------
    # Shortcuts / helpers
    # ------------------------------------------------------------------

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
