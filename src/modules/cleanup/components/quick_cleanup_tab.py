"""QuickCleanupTab — the Cleanup overview dashboard (the "Quick Cleanup" tab).

Top to bottom, in one scroll area (``.optimise-facts/ui-plan.md`` section 2.2):

- **Toolbar**: Scan all, Stop, the preset and the preset clean. The status
  sits next to the buttons that caused it, with a progress bar that counts
  locations, and names the slow scanners it is still waiting for.
- **Row A, key figures** (``kpi_tiles``): reclaimable, safe to clean now
  (what the selected preset would remove), needs review, not offered, and the
  free space C: would have after the clean (labelled an estimate).
- **Row B, charts** (``charts``): space by category (the donut's slices and
  its legend are the SAME list, every location included, "Other (N)" last),
  the ten largest locations (bars coloured AND labelled by safety), and the
  trend of cleans and scans.
- **Row C, recommendations** (``recommendations_panel``). A card navigates;
  nothing here cleans by itself.
- **Row D, locations**: every category as one compact row
  (``category_group``), largest first; the long tail sits under "More
  locations (N)", sorted by size, empty ones hidden.

Safety is never relaxed by anything shown here: a row's badge is the more
cautious of the catalog's rating and the research's (``knowledge``), and the
preset clean still decides from the catalog, exactly as before.

Public contract (cleanup_module and the tests depend on it): ``build()``,
``scan()``, ``auto_scan()``, ``clean_now()``, ``cancel()``,
``start_auto_refresh()``/``stop_auto_refresh()``, ``scan_done(int, int)``,
``freed_bytes(int)`` (MEASURED bytes). New: ``snapshot_ready(ScanSnapshot)``
after every complete scan, ``navigate(tab_key, ids, age_days)`` from a
recommendation card, and ``set_theme(theme)``.

The scan summary for the trend (``ScanSnapshot.append_summary``) is NOT
written here; whoever owns ``snapshot_ready`` decides that, so a test that
scans never writes to the real %APPDATA%.
"""
from __future__ import annotations

import logging
import ntpath
import time
from typing import Dict, List, Optional

from PyQt6.QtCore import QEvent, Qt, QTimer, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QGridLayout, QHeaderView, QHBoxLayout,
    QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from core.widget_life import widget_is_valid
from core.worker import Worker, retire
from modules.cleanup.components.category_group import (
    CategoryGroup, ColumnHeader, BADGE, most_cautious,
)
from modules.cleanup.components.kpi_tiles import (
    DASH, KpiStrip, KpiTile, compute_figures, not_offered_tooltip,
)

logger = logging.getLogger(__name__)


# A worker's result can land after this tab is gone. Qt auto-disconnects a
# BOUND METHOD when the receiving QObject is destroyed, but the scan
# callbacks below are closures, and nothing tells Qt what a closure's
# receiver is — so the connection outlives the widget and the callback
# reaches into a deleted C++ object. Found by a hard crash (exit -1073740791),
# not an exception.
#
# `from PyQt6 import sip`, not `import sip`: the bare form does not exist
# under PyQt6, so a guard written that way silently falls back to
# "always valid" and protects nothing.
_alive = widget_is_valid


# ── Categories ───────────────────────────────────────────────────────────
#
# (id, display name, legacy colour slot). The third element is kept so the
# tuple shape every caller and test uses does not change; it is IGNORED.
# Chart colours come from core.semantic_colors.chart(i) by size rank, read at
# paint time, so they follow the theme.

ADVANCED_CATEGORIES = [
    # Core
    ("recent", "Recent Files", ""),
    ("games", "Game Caches", ""),
    ("adobe", "Adobe Cache", ""),
    ("office", "Office Temp", ""),
    ("jets", "IDE Caches", ""),
    ("spooler", "Print Spooler", ""),
    ("winsat", "WinSAT Cache", ""),
    ("etl", "ETL Logs", ""),
    ("telemetry", "Telemetry Data", ""),
    ("delivery", "Delivery Optimization", ""),
    ("clipboard", "Clipboard", ""),
    ("xbox", "Xbox Cache", ""),
    ("onedrive", "OneDrive Logs", ""),
    ("maps", "Maps Cache", ""),
    ("sticky", "Sticky Notes", ""),
    ("defender", "Defender History", ""),
    # Cloud Storage
    ("dropbox", "Dropbox Cache", ""),
    ("gdrive", "Google Drive", ""),
    ("mega", "MEGA Cache", ""),
    ("pcloud", "pCloud Cache", ""),
    ("icloud", "iCloud Cache", ""),
    ("box", "Box Cache", ""),
    # Virtualization
    ("docker", "Docker Desktop", ""),
    ("vbox", "VirtualBox VMs", ""),
    ("vmware", "VMware VMs", ""),
    ("wsl2", "WSL2 Distros", ""),
    ("hyperv", "Hyper-V VMs", ""),
    # Media Production
    ("obs", "OBS Cache", ""),
    ("davinci", "DaVinci Cache", ""),
    ("premiere", "Premiere Cache", ""),
    ("blender", "Blender Cache", ""),
    ("audacity", "Audacity Cache", ""),
    # Communication
    ("telegram", "Telegram Cache", ""),
    ("signal", "Signal Cache", ""),
    ("teams", "Teams Cache", ""),
    ("slack", "Slack Cache", ""),
    ("discord", "Discord Cache", ""),
    # Development
    ("jetbrains", "JetBrains Cache", ""),
    ("eclipse", "Eclipse Cache", ""),
    ("gitlfs", "Git LFS Cache", ""),
    ("npm", "npm Cache", ""),
    ("pip", "pip Cache", ""),
    ("nuget", "NuGet Cache", ""),
    ("vscode", "VS Code Cache", ""),
    ("unity", "Unity Cache", ""),
    # Games
    ("epic", "Epic Games Cache", ""),
    ("battlenet", "Battle.net Cache", ""),
    ("rockstar", "Rockstar Cache", ""),
    ("minecraft", "Minecraft Cache", ""),
    ("lol", "League of Legends Cache", ""),
    ("rust", "Rust Game Cache", ""),
    # More Browsers
    ("brave", "Brave Cache", ""),
    ("vivaldi", "Vivaldi Cache", ""),
    ("opera", "Opera Cache", ""),
    ("yandex", "Yandex Cache", ""),
    ("edge", "Edge Cache", ""),
    ("firefox", "Firefox Cache", ""),
    ("chrome", "Chrome Cache", ""),
    # System
    ("iis", "IIS Logs", ""),
    ("dockerimg", "Docker Images", ""),
    ("vpn", "VPN Cache", ""),
    ("putty", "PuTTY Cache", ""),
    ("rdp", "RDP Cache", ""),
    # PC-Specific (auto-discovered)
    ("vscode_ext", "VS Code extension packages", ""),
    ("vscode_dawn", "VS Code Dawn Cache", ""),
    ("vscode_web", "VS Code WebStorage", ""),
    ("steam_logs", "Steam Logs", ""),
    ("steam_web", "Steam WebCache", ""),
    ("chrome_full", "Chrome Full Cache", ""),
    ("edge_full", "Edge Full Cache", ""),
    ("brave_full", "Brave Full Cache", ""),
    ("uwp_all", "UWP Apps Cache", ""),
    ("lm_studio", "LM Studio Cache", ""),
    ("teams_npc", "Microsoft Teams Cache", ""),
    ("photos_cache", "Windows Photos Cache", ""),
    ("ms_store", "Microsoft Store Cache", ""),
    ("discord_logs", "Discord Logs", ""),
    ("notifications", "Notification History", ""),
    ("spotify_app", "Spotify Cache", ""),
    ("cbs_logs", "CBS Logs", ""),
    ("panther_logs", "Panther Logs", ""),
    ("inetcache", "INetCache", ""),
    ("game_bar", "Game Bar Cache", ""),
    ("yarn", "Yarn Cache", ""),
    ("pnpm", "pnpm Cache", ""),
    # Smart Finders
    ("empty_folders", "Empty Folders", ""),
    # Catalog-backed (present on this machine, previously reachable only
    # via System Junk/App & Game Caches' own bulk category inclusion --
    # featured here individually for visibility. Each id is the catalog's
    # own spec id (catalog.py's scanner_for(id) -> cs.scan_<id>), not a
    # hand-picked short name, so it can never collide with one above.
    # DELIBERATELY EXCLUDED from this batch: "driver_store" and
    # "application_manifest_cache" -- both carry their own catalog labels
    # reading "DANGER, system-critical" / "can break hardware", and
    # CLAUDE.md's Cleanup section documents a deliberate existing rule
    # that driver-store deletion must never go through a plain checkbox
    # (only pnputil /delete-driver via _driver_panel.py's dedicated,
    # confirmed panel -- deleting the folder directly is how a machine
    # loses a driver it still believes it has).
    ("complus_cache", "COM+ App Cache", ""),
    ("office_cache_extended", "Office Extended Cache", ""),
    ("onedrive_cache", "OneDrive Mask Cache", ""),
    ("onedrive_commercial_cache", "OneDrive for Business Cache", ""),
    ("onedrive_full_cache", "OneDrive Full Cache", ""),
    ("claude_cli_cache", "Claude CLI Cache", ""),
    ("npp_cache", "Notepad++ Cache", ""),
    ("vscode_cached_data", "VS Code Cached Data", ""),
    ("vscode_settings_sync", "VS Code Settings Sync", ""),
    ("xbox_app_cache", "Xbox App Cache", ""),
    ("amd_radeon_cache", "AMD Radeon Cache", ""),
    ("browser_caches", "Browser Cache (bulk)", ""),
    ("d3d_shader_cache", "Direct3D Shader Cache", ""),
    ("diag_logs", "Diagnostic Logs", ""),
    ("directx_shader_cache", "DirectX Shader Cache", ""),
    ("dxgi_cache", "DXGI Cache", ""),
    ("event_logs", "Event Log Files", ""),
    ("font_cache", "Font Cache Service", ""),
    ("language_packs", "Language Pack Cache", ""),
    ("memory_dumps", "Memory Dumps", ""),
    ("minidump", "Kernel Minidumps", ""),
    ("per_drive_recycle_bin", "Recycle Bin (all drives)", ""),
    ("per_drive_temp", "Temp (all drives)", ""),
    ("sfc_logs", "SFC/DISM Logs", ""),
    ("sysprep_logs", "Sysprep Logs", ""),
    ("thumbnail_cache_central", "Explorer Thumbcache", ""),
    ("update_cleanup", "Windows Update Cleanup", ""),
    ("wer_reports", "Error Reporting Archive", ""),
    ("windows_backup_catalog", "Windows Backup Catalog", ""),
    ("windows_backup_logs", "Server Backup Logs", ""),
    ("windows_setup_diags", "Setup Diag Logs", ""),
    ("windows_tweaker_logs", "This App's Own Logs", ""),
    ("windowsupdate_orch_cache", "WU Orchestrator Cache", ""),
    ("wu_history_cache", "WU Download History", ""),
    # Confirmed real on THIS machine specifically (cross-referenced against
    # its own installed-software list, not guessed): CurseForge and AMD
    # ReLive's DVR buffer had no catalog entry at all until now.
    ("curseforge_cache", "CurseForge Cache", ""),
    ("amd_dvr_cache", "AMD ReLive Buffer", ""),
    # Found via a direct AppData folder census (registry Uninstall keys
    # miss portable/Electron-updater-style installs like these).
    ("cyberghost_cache", "CyberGhost VPN Cache", ""),
    ("opencode_desktop_cache", "OpenCode Cache", ""),
    ("amd_comgr_cache", "AMD Compute Cache", ""),
    ("amd_install_manager_cache", "AMD Installer Cache", ""),
]

CLEANUP_CATEGORIES = [
    ("temp", "Temp Files", ""),
    ("prefetch", "Prefetch", ""),
    ("thumb", "Thumbnail Cache", ""),
    ("crash", "Crash Dumps", ""),
    ("browser", "Browser Caches", ""),
    ("app", "App Caches", ""),
    ("logs", "Windows Logs", ""),
    ("wu", "Windows Update", ""),
    ("large", "Large Items", ""),
    ("dev", "Dev Tools", ""),
]

#: Where "Review on the … tab" goes for a main category (display names; the
#: owner resolves them). Advanced categories have no single tab.
_AREA_TAB = {
    "temp": "System Junk", "prefetch": "System Junk", "thumb": "System Junk",
    "crash": "System Junk", "browser": "Browser Caches", "app": "App & Game Caches",
    "logs": "Logs & Reports", "wu": "Windows Update", "large": "Large Items",
    "dev": "Dev Tools",
}

#: The recommendation engine's tab keys (recommend.TAB_*), per category, so a
#: snapshot row says which area tab shows it.
_RECO_TAB = {
    "temp": "system", "prefetch": "system", "thumb": "system", "crash": "system",
    "browser": "browser", "app": "apps", "logs": "logs", "wu": "wu", "large": "large",
    "dev": "dev",
}
_RECO_TAB_BY_CATALOG = {
    "system": "system", "browsers": "browser", "dev": "dev", "apps": "apps",
    "cloud": "apps", "comms": "apps", "games": "apps", "media": "apps",
}

#: The browser category has no scanner function; its research and catalog id.
BROWSER_KB_ID = "browser_caches"

#: Charts sit side by side from this width; below it they stack.
CHARTS_SIDE_BY_SIDE = 1100
CHARTS_TWO_COLUMNS = 720

#: After this long, the progress line names the scanners still running.
SLOW_NOTE_AFTER_S = 8


def largest_slice_text(slices, format_size) -> str:
    """"Largest: Package Cache — 32.1 GB", or "" with nothing to show.

    ``slices`` are (label, size, colour) tuples (the colour is ignored)."""
    if not slices:
        return ""
    label, size, _color = max(slices, key=lambda s: s[1])
    if size <= 0:
        return ""
    return f"Largest: {label} — {format_size(size)}"


def short_path(path: str, parts: int = 3) -> str:
    """"…\\AppData\\Local\\Temp" -- the end of a path is what says what it is."""
    p = str(path).rstrip("\\/")
    pieces = [x for x in p.replace("/", "\\").split("\\") if x]
    if len(pieces) <= parts:
        return p
    return "…\\" + "\\".join(pieces[-parts:])


class _AccentButton(QPushButton):
    """The primary action: accent-coloured only while it can be pressed. The
    accent role has no disabled look of its own, so a disabled accent button
    would still look like the thing to click."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._sync_role()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._sync_role()

    def _sync_role(self) -> None:
        want = "accentButton" if self.isEnabled() else ""
        if self.objectName() != want:
            self.setObjectName(want)
            style = self.style()
            style.unpolish(self)
            style.polish(self)
            self.update()


class _ChartsRow(QWidget):
    """Three chart cards: side by side when wide, two-up, or stacked."""

    def __init__(self, cards, parent=None):
        super().__init__(parent)
        self.cards = list(cards)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        self._mode = ""
        self._available_width = None
        self._place("wide")

    def mode(self) -> str:
        return self._mode

    def _place(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        for c in self.cards:
            self._grid.removeWidget(c)
        for i in range(3):
            self._grid.setColumnStretch(i, 0)
        a, b, c = self.cards
        if mode == "wide":
            self._grid.addWidget(a, 0, 0)
            self._grid.addWidget(b, 0, 1)
            self._grid.addWidget(c, 0, 2)
            for i, s in enumerate((10, 12, 10)):
                self._grid.setColumnStretch(i, s)
        elif mode == "two":
            self._grid.addWidget(a, 0, 0)
            self._grid.addWidget(b, 0, 1)
            self._grid.addWidget(c, 1, 0, 1, 2)
            self._grid.setColumnStretch(0, 1)
            self._grid.setColumnStretch(1, 1)
        else:
            self._grid.addWidget(a, 0, 0)
            self._grid.addWidget(b, 1, 0)
            self._grid.addWidget(c, 2, 0)
            self._grid.setColumnStretch(0, 1)

    def resizeEvent(self, event):
        w = self._available_width or event.size().width()
        self._place("wide" if w >= CHARTS_SIDE_BY_SIDE
                    else "two" if w >= CHARTS_TWO_COLUMNS else "stacked")
        super().resizeEvent(event)

    def set_available_width(self, width):
        self._available_width = width
        self._place("wide" if width >= CHARTS_SIDE_BY_SIDE
                    else "two" if width >= CHARTS_TWO_COLUMNS else "stacked")


class QuickCleanupTab(QWidget):
    """
    The overview dashboard.

    Lifecycle:
        - Construct, then call build() to create the categories.
        - Auto-refresh: call start_auto_refresh() / stop_auto_refresh().
        - scan() triggers a background rescan of all categories.
        - cancel() cancels any in-flight workers.
    """

    # Emitted when all scans complete: (total_items, total_size)
    scan_done = pyqtSignal(int, object)

    # Emitted after a clean -- the MEASURED bytes, feeding CleanupModule's
    # shared "Freed this session" counter like every other tab.
    freed_bytes = pyqtSignal(object)

    # A complete scan, as a ScanSnapshot (scan_snapshot.py).
    snapshot_ready = pyqtSignal(object)

    # A recommendation card asked to open another tab: (tab_key, ids, age_days).
    navigate = pyqtSignal(str, list, int)

    #: This sweep covers ~137 categories; generous headroom above what this
    #: machine measures (the whole sweep is ~45 s here).
    SCAN_WATCHDOG_MS = 300_000

    #: One pathologically slow scanner (a huge personal folder, a slow share,
    #: a OneDrive placeholder needing hydration) must not block the whole
    #: dashboard: a category stuck past this is counted as reported (timed
    #: out) so the others render; its own worker keeps running and corrects
    #: the result in place if it finishes later.
    PER_SCANNER_TIMEOUT_MS = 45_000

    def __init__(self, parent=None, on_category_clicked=None):
        super().__init__(parent)
        self._on_category_clicked = on_category_clicked
        self._categories: List[tuple] = []
        self._advanced_categories: List[tuple] = []
        self._group_widgets: Dict[str, CategoryGroup] = {}
        self._results: Dict[str, object] = {}   # id -> ScanResult (browser: raw list)
        self._display: Dict[str, object] = {}   # id -> ScanResult, browser converted
        self._scanning = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_timer_refresh)
        self._refresh_interval_ms = 30_000
        self._workers: List[Worker] = []
        self._timed_out_cids: set = set()
        self._per_category_timers: List[QTimer] = []
        self._scanned = False
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_scan_watchdog)
        self._clean_after_scan = False
        self._total_scanned = 0
        self._scan_targets: List[str] = []
        self._scan_started = 0.0
        self._snapshot = None
        self._reco_pending = False
        self._not_offered = None
        self._free_c: Optional[int] = None
        self._free_c_error = ""
        self._figures = None
        self._scan_errors = {}
        self._scan_generation = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self, categories: List[tuple] = None, advanced_categories: List[tuple] = None) -> None:
        """Build the UI. Call once after construction.

        categories / advanced_categories: lists of (id, display_name, colour);
        the colour is ignored. Default to CLEANUP_CATEGORIES /
        ADVANCED_CATEGORIES.
        """
        if categories is None:
            categories = CLEANUP_CATEGORIES
        if advanced_categories is None:
            advanced_categories = ADVANCED_CATEGORIES
        self._categories = list(categories)
        self._advanced_categories = list(advanced_categories)

        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs
        _id_map = self._main_scanner_definitions(cs)
        _id_map.update(self._extra_scanner_definitions(cs))

        # A category alias must not turn a reviewed caution entry back into safe.
        for cid, (fn, safety) in list(_id_map.items()):
            spec = getattr(fn, "spec", None)
            if spec is not None:
                _id_map[cid] = (fn, spec.safety)

        self._scanner_map = {}
        self._browser_scanner = bs.detect_browsers  # for browser category
        self._base_safety: Dict[str, str] = {}

        for cid, clabel, ccolor in self._categories:
            fn_safety = _id_map.get(cid, (None, "safe"))
            self._scanner_map[cid] = (fn_safety[0], clabel, ccolor)
            self._base_safety[cid] = fn_safety[1]

        self._adv_scanner_map = {}
        for cid, clabel, ccolor in self._advanced_categories:
            fn_safety = _id_map.get(cid, (None, "safe"))
            self._adv_scanner_map[cid] = (fn_safety[0], clabel, ccolor)
            self._base_safety[cid] = fn_safety[1]

        self._id_to_scanner_name = {
            cid: (fn.__name__ if fn else None)
            for cid, (fn, _label, _color) in
            {**self._scanner_map, **self._adv_scanner_map}.items()
        }

        self._setup_ui()
        self._connect_theme()

    def start_auto_refresh(self, interval_ms: int = 30_000) -> None:
        self._refresh_interval_ms = interval_ms
        self._refresh_timer.start(interval_ms)

    def stop_auto_refresh(self) -> None:
        self._refresh_timer.stop()

    def scan(self) -> None:
        """Trigger background scan of all categories. Idempotent."""
        if self._scanning:
            return
        self._do_scan_all()

    def auto_scan(self) -> None:
        """Scan once, the first time this tab is shown (CleanupModule calls
        this on every tab switch, as for every other tab)."""
        if not self._scanned:
            self._do_scan_all()

    def cancel(self) -> None:
        self._reset_after_cancel()
        reco = getattr(self, "_reco", None)
        if reco is not None and _alive(reco):
            reco.cancel()

    _cancel_all = cancel

    def clean_now(self) -> None:
        """The module-level "Clean Now" button's entry point. Scans first if
        this tab has never been scanned (or was reset by a cancel); once a
        scan has nothing cleanable this is a no-op."""
        if self._scanning:
            return
        if self._scanned:
            self._do_clean_all_safe()
            return
        self._clean_after_scan = True
        self._do_scan_all()

    def set_theme(self, _theme: str = "") -> None:
        """Repaint everything painted from the palette (charts, rows, tiles)."""
        if not _alive(self):
            return
        for chart in (getattr(self, "_donut", None), getattr(self, "_bars", None),
                      getattr(self, "_trend", None)):
            if chart is not None:
                chart.set_theme(_theme)
        for g in self._all_groups():
            g.update()
            g._header.update()
        for t in getattr(self, "_tiles", {}).values():
            t.glyph.update()
        if hasattr(self, "_col_header"):
            self._col_header.update()

    def snapshot(self):
        """The last complete scan as a ScanSnapshot, or None."""
        return self._snapshot

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Base breakpoints on the viewport, not the content's current minimum
        # width: a wide grid must be able to shrink back into a narrow window.
        width = max(1, event.size().width() - 40)
        if hasattr(self, "_charts"):
            self._charts.set_available_width(width)
            self._kpi.set_available_width(width)

    def _track_worker(self, worker):
        def finished():
            if _alive(self) and worker in self._workers:
                self._workers.remove(worker)
        worker.signals.finished.connect(finished)
        self._workers.append(worker)

    def _add_chart_table(self, card, headers):
        """A keyboard-readable equivalent of the painted chart, from the same data."""
        card.table_toggle = QToolButton(card)
        card.table_toggle.setText("Show as table")
        card.table_toggle.setCheckable(True)
        card.table_toggle.setToolTip("Switch between the chart and its measured values in a table.")
        card.table_toggle.setAccessibleName(f"Show {card.title_label.text()} as table")
        card.table = QTableWidget(0, len(headers), card)
        card.table.setHorizontalHeaderLabels(headers)
        card.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        card.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        card.table.setAccessibleName(f"{card.title_label.text()} values")
        card.table.setToolTip("Read-only values from the same scan or history as the chart.")
        card.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        card.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        card.table.verticalHeader().hide()
        card.table.hide()
        card.layout().insertWidget(1, card.table_toggle, 0, Qt.AlignmentFlag.AlignRight)
        card.layout().insertWidget(3, card.table, 1)

        def toggle(checked):
            if not _alive(self):
                return
            card.chart.setVisible(not checked)
            card.table.setVisible(checked)
            card.table_toggle.setText("Show chart" if checked else "Show as table")
        card.table_toggle.toggled.connect(toggle)

    @staticmethod
    def _set_chart_table(card, rows):
        card.table.setRowCount(len(rows))
        for row, (values, tooltip) in enumerate(rows):
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(tooltip)
                card.table.setItem(row, column, cell)

    def group(self, cid: str) -> Optional[CategoryGroup]:
        return self._group_widgets.get(cid)

    # ── Setup ────────────────────────────────────────────────────────────────

    def _connect_theme(self) -> None:
        try:
            from app import App
        except ImportError:
            return
        inst = getattr(App, "instance", None)
        theme = getattr(inst, "theme", None)
        if theme is not None and hasattr(theme, "theme_changed"):
            theme.theme_changed.connect(self.set_theme)

    @staticmethod
    def _app_config():
        try:
            from app import App
        except ImportError:
            return None
        inst = getattr(App, "instance", None)
        return getattr(inst, "config", None)

    def _setup_ui(self):
        from modules.cleanup.cleanup_presets import PRESETS, preset_names
        from modules.cleanup.components.charts import BarChart, ChartCard, DonutChart, TrendChart

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._build_toolbar(outer, PRESETS, preset_names)

        self._build_progress(outer)

        # ── Scrolled page ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll = scroll
        page = QWidget()
        page.setObjectName("overviewPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 6, 12, 14)
        lay.setSpacing(12)

        self._build_kpi_tiles(lay)

        self._build_charts(lay, BarChart, ChartCard, DonutChart, TrendChart)

        self._build_recommendations(lay)

        self._build_locations(lay)
        lay.addStretch()

        scroll.setWidget(page)
        outer.addWidget(scroll, 1)
        self._group_widgets = dict(self._group_by_cid)

    def _make_group(self, cid: str, label: str, fn, *, main: bool) -> CategoryGroup:
        group = CategoryGroup(label, fn, auto_refresh=False,
                              base_safety=self._base_safety.get(cid, ""),
                              tab_name=_AREA_TAB.get(cid, "") if main else "",
                              catalog_id=BROWSER_KB_ID if cid == "browser" else "")
        group.freed_bytes.connect(self.freed_bytes)
        group.freed_bytes.connect(self._after_group_clean)
        group.scan_done.connect(
            lambda count, size, cid=cid: self._category_updated(cid) if _alive(self) else None)
        if main:
            group.clicked.connect(lambda cid=cid: self._handle_category_clicked(cid))
        return group

    def _all_groups(self) -> List[CategoryGroup]:
        return list(getattr(self, "_group_by_cid", {}).values())

    def _reset_tiles(self) -> None:
        t = self._tiles
        t["total"].set(DASH, "Scan to measure",
                       "<b>Reclaimable</b><p>Everything the scan found that Cleanup can offer, "
                       "every path counted once even when two categories list it.</p>")
        t["safe"].set(DASH, "What the preset would remove",
                      "<b>Safe to clean now</b><p>What “Clean” would remove with the selected "
                      "preset. Light takes only locations rated Safe.</p>")
        t["review"].set(DASH, "Rated Review or Risky",
                        "<b>Needs review</b><p>Locations rated Review or Risky: the more cautious "
                        "of the catalog's rating and the research's. They start unticked and the "
                        "Light preset never takes them.</p>")
        t["blocked"].set(DASH, "Kept out on purpose", not_offered_tooltip(None))
        t["drive"].set(DASH, "Estimate after a clean",
                       "<b>Free after clean (C:)</b><p>C:'s free space now plus what the selected "
                       "preset would remove from C:. An estimate: files in use, cloud-only "
                       "files and newer-than-24-hour temp files are kept, so the measured "
                       "figure after a clean is usually lower.</p>")

    def _on_preset_changed(self, _index: int) -> None:
        from modules.cleanup.cleanup_presets import PRESETS
        pid = self._preset_combo.currentData()
        label = PRESETS[pid]["label"] if pid in PRESETS else "Custom"
        self._clean_all_btn.setText(f"Clean ({label})…")
        if self._scanned:
            self._clean_all_btn.setEnabled(self._has_cleanable_items())
            self._refresh_figures()

    # ── Auto-refresh ────────────────────────────────────────────────────────

    def _on_timer_refresh(self):
        if self._scanning:
            return
        self.scan()

    # ── Scan All ────────────────────────────────────────────────────────────

    def _start_timeout_guard(self, cid: str, scan_targets: list) -> QTimer:
        """A per-category watchdog: if `cid` hasn't reported within
        PER_SCANNER_TIMEOUT_MS it is counted as done (timed out) so one slow
        scanner cannot block every other category from being shown."""
        generation = self._scan_generation
        def _on_timeout():
            if not _alive(self) or generation != self._scan_generation or cid in self._results:
                return
            logger.warning(
                "Quick Cleanup: %s did not report within %.0fs -- "
                "continuing without it (its own worker keeps running "
                "and will correct this if it finishes later)",
                cid, self.PER_SCANNER_TIMEOUT_MS / 1000)
            self._timed_out_cids.add(cid)
            self._total_scanned += 1
            if self._total_scanned == len(scan_targets):
                self._on_all_scanned()

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(_on_timeout)
        timer.start(self.PER_SCANNER_TIMEOUT_MS)
        self._per_category_timers.append(timer)
        return timer

    def _count_scanner_result(self, cid: str, scan_targets: list) -> None:
        """Count `cid` toward completion -- unless its timeout guard already
        counted it, in which case this is a late, corrected result."""
        already_counted = cid in self._timed_out_cids
        if not already_counted:
            self._total_scanned += 1
        if self._total_scanned == len(scan_targets):
            self._on_all_scanned()
        elif already_counted and not self._scanning and self._scanned:
            # A slow scanner finished after the dashboard rendered without it:
            # put its real figures in.
            self._render_results()

    def _do_scan_all(self):
        if self._scanning:
            return
        self._scanning = True
        self._scan_generation += 1
        generation = self._scan_generation
        self._scan_started = time.monotonic()
        self._watchdog.start(self.SCAN_WATCHDOG_MS)
        self._scan_all_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._clean_all_btn.setEnabled(False)
        self._results.clear()
        self._scan_errors.clear()
        self._total_scanned = 0
        self._timed_out_cids = set()
        for t in self._per_category_timers:
            t.stop()
        self._per_category_timers = []

        from modules.cleanup import cleanup_scanner as cs

        scan_targets = [cid for cid, _, _ in self._categories]
        scan_targets += [cid for cid, _, _ in self._advanced_categories]
        browser_target = "browser" in [c[0] for c in self._categories]

        def _start_worker(cid: str, scanner_fn):
            def _run(_worker):
                from modules.cleanup.cleanup_scanner import scan_cache
                return scan_cache.cached_scan(scanner_fn, 0)

            timer = self._start_timeout_guard(cid, scan_targets)

            def _done(result):
                if not _alive(self) or generation != self._scan_generation:
                    return
                timer.stop()
                self._results[cid] = result
                self._count_scanner_result(cid, scan_targets)

            def _err(_e):
                if not _alive(self) or generation != self._scan_generation:
                    return
                timer.stop()
                logger.warning("Quick Cleanup scanner %s failed: %s", cid, _e)
                self._scan_errors[cid] = str(_e)
                self._results[cid] = cs.ScanResult()
                self._count_scanner_result(cid, scan_targets)

            def _cancelled():
                if _alive(self) and generation == self._scan_generation:
                    self._reset_after_cancel("Scan stopped. Click Scan all to start again.")

            w = Worker(_run)
            w.signals.result.connect(_done)
            w.signals.error.connect(_err)
            w.signals.cancelled.connect(_cancelled)
            self._track_worker(w)
            QThreadPool.globalInstance().start(w)

        for cid, _clabel, _c in self._categories:
            if cid == "browser":
                continue
            fn_info = self._scanner_map.get(cid)
            if fn_info is None or fn_info[0] is None:
                scan_targets.remove(cid)
                continue
            _start_worker(cid, fn_info[0])

        for cid, _clabel, _c in self._advanced_categories:
            fn_info = self._adv_scanner_map.get(cid)
            if fn_info is None or fn_info[0] is None:
                scan_targets.remove(cid)
                continue
            _start_worker(cid, fn_info[0])

        self._scan_targets = scan_targets

        if browser_target:
            def _run_browser(_worker):
                return self._browser_scanner()

            browser_timer = self._start_timeout_guard("browser", scan_targets)

            def _done_browser(results):
                if not _alive(self) or generation != self._scan_generation:
                    return
                browser_timer.stop()
                self._results["browser"] = results
                self._count_scanner_result("browser", scan_targets)

            def _err_browser(_e):
                if not _alive(self) or generation != self._scan_generation:
                    return
                browser_timer.stop()
                logger.warning("Quick Cleanup browser scan failed: %s", _e)
                self._scan_errors["browser"] = str(_e)
                self._results["browser"] = []
                self._count_scanner_result("browser", scan_targets)

            def _cancelled_browser():
                if _alive(self) and generation == self._scan_generation:
                    self._reset_after_cancel("Scan stopped. Click Scan all to start again.")

            wb = Worker(_run_browser)
            wb.signals.result.connect(_done_browser)
            wb.signals.error.connect(_err_browser)
            wb.signals.cancelled.connect(_cancelled_browser)
            self._track_worker(wb)
            QThreadPool.globalInstance().start(wb)

        if hasattr(self, "_progress_row"):
            self._progress.setRange(0, max(1, len(scan_targets)))
            self._progress.setValue(0)
            self._progress_row.show()
            self._status_lbl.setText("Scanning… nothing is deleted by a scan.")
            self._update_progress()
            self._progress_timer.start()

        if not scan_targets:
            self._on_all_scanned()

    def _update_progress(self) -> None:
        if not _alive(self) or not self._scanning:
            return
        total = max(1, len(self._scan_targets))
        done = min(total, self._total_scanned)
        self._progress.setMaximum(total)
        self._progress.setValue(done)
        text = f"Scanning {done} of {total} locations"
        elapsed = time.monotonic() - self._scan_started
        if elapsed >= SLOW_NOTE_AFTER_S:
            label_by_id = {cid: label for cid, label, _ in
                           self._categories + self._advanced_categories}
            waiting = [label_by_id.get(c, c) for c in self._scan_targets
                       if c not in self._results and c not in self._timed_out_cids]
            if waiting:
                shown = ", ".join(waiting[:3]) + (f" and {len(waiting) - 3} more"
                                                  if len(waiting) > 3 else "")
                text += f" · still waiting on {shown} ({elapsed:.0f} s)"
        self._progress_lbl.setText(text)
        self._progress_lbl.setToolTip(text)

    def _on_all_scanned(self):
        self._watchdog.stop()
        self._scanned = True
        self._scanning = False
        self._scan_all_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress_timer.stop()
        self._progress_row.hide()
        self._workers = [w for w in self._workers if not w.is_cancelled]

        total_items, total_size = self._render_results()

        categories_with_data = self._figures.categories_with_data if self._figures else 0
        status = (
            f"Found {total_items} item(s) across {categories_with_data} categories"
            if categories_with_data else "No reclaimable space found"
        )
        if self._timed_out_cids:
            label_by_id = {cid: label for cid, label, _ in
                           self._categories + self._advanced_categories}
            names = ", ".join(
                label_by_id.get(cid, cid) for cid in sorted(self._timed_out_cids))
            status += f" ({len(self._timed_out_cids)} slow and still scanning: {names})"
        if self._scan_errors:
            status += f"; {len(self._scan_errors)} scanner(s) failed — expand the category for details"
        self._status_lbl.setText(status)
        self._status_lbl.setToolTip(status)
        self.scan_done.emit(total_items, total_size)

        self._publish_snapshot()
        self._start_facts_job()
        self._refresh_trend()

        if self._clean_after_scan:
            self._clean_after_scan = False
            self._do_clean_all_safe()

    # ── Rendering a finished scan ───────────────────────────────────────

    def _browser_scan_result(self, browser_results):
        """The browser scan as a ScanResult, so it can be listed, charted and
        de-duplicated like every other category. The raw list stays in
        ``_results["browser"]`` for the clean, which takes it as is."""
        from modules.cleanup import cleanup_scanner as cs
        out = cs.ScanResult()
        for r in browser_results or []:
            for profile in getattr(r, "profiles", None) or []:
                for cat in getattr(profile, "categories", None) or []:
                    size = int(getattr(cat, "size_bytes", 0) or 0)
                    if size > 0:
                        out.items.append(cs.ScanItem(path=str(cat.path), size=size,
                                                     is_dir=True, safety="safe"))
                        out.total_size += size
        return out

    def _render_results(self):
        """Feed every view from ``self._results``. Returns (items, bytes)."""
        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup.components.charts import Slice

        # Categories overlap, so results are de-duplicated ACROSS categories
        # before anything is totalled (measured before the fix: 2.10 GB
        # claimed against 1.39 GB really present). Each unique path goes to the
        # first category that claims it, in display order.
        merged = dict(self._results)
        if "browser" in merged and not isinstance(merged["browser"], cs.ScanResult):
            merged["browser"] = self._browser_scan_result(merged["browser"])
        display = self._deduplicate_across_categories(merged)
        raw_browser = self._results.get("browser")
        self._results = dict(display)
        if raw_browser is not None and not isinstance(raw_browser, cs.ScanResult):
            self._results["browser"] = raw_browser
        self._display = display

        total_size = 0
        total_items = 0
        with_data = 0
        for cid, group in self._group_by_cid.items():
            result = display.get(cid)
            if not isinstance(result, cs.ScanResult):
                if cid in self._timed_out_cids:
                    group._set_state("Still scanning…")
                    continue
                result = cs.ScanResult()
            group.set_result(result)
            group._err_lbl.setText(self._scan_errors.get(cid, ""))
            group._err_lbl.setVisible(cid in self._scan_errors)
            if result.items:
                total_size += result.total_size
                total_items += len(result.items)
                with_data += 1
        for group in self._all_groups():
            group.set_share(group.total_size() / total_size if total_size else 0.0)

        # Main rows: largest first. Advanced: largest first, empty ones hidden.
        mains = sorted(self._legend_cards, key=lambda g: -g.total_size())
        for i, g in enumerate(mains):
            self._main_box.insertWidget(i, g)
        advs = sorted(self._adv_cards, key=lambda g: -g.total_size())
        for i, g in enumerate(advs):
            self._adv_box.insertWidget(i, g)
            g.setVisible(g.total_size() > 0)
        self._update_adv_button()

        # Donut: every category, so slice and legend are one list.
        slices = []
        for cid, group in self._group_by_cid.items():
            size = group.total_size()
            if size > 0:
                slices.append(Slice(label=group.name(), bytes=size, key=cid,
                                    safety_mix=group.safety_mix()))
        self._donut.set_data(slices)

        # Bars: the ten largest single locations.
        bars = []
        for cid, group in self._group_by_cid.items():
            for item in group.items():
                if item.size > 0:
                    bars.append(Slice(label=short_path(item.path), bytes=int(item.size),
                                      key=f"{cid}\n{item.path}", safety=group.item_safety(item),
                                      detail=item.path))
        self._bars.set_data(bars)
        from modules.cleanup.components.charts import percent_text
        self._set_chart_table(self._donut_card, [
            ((s.label, cs.format_size(s.bytes), percent_text(s.bytes, self._donut.total())),
             self._donut_tooltip(s)) for s in self._donut.slices()])
        self._set_chart_table(self._bars_card, [
            ((s.label, cs.format_size(s.bytes), BADGE[s.safety]), self._bar_tooltip(s))
            for s in self._bars.bars()])

        self._figures_with_data = with_data
        self._refresh_figures()
        self._clean_all_btn.setEnabled(self._has_cleanable_items())
        return total_items, total_size

    def _preset_items(self) -> list:
        """(path, size) the selected preset would remove -- the same selection
        _do_clean_all_safe makes, read-only."""
        from modules.cleanup import cleanup_presets
        from modules.cleanup import cleanup_scanner as cs
        preset_id = self._preset_combo.currentData() if hasattr(self, "_preset_combo") else "light"
        chosen = []
        scan_results = {k: v for k, v in self._results.items()
                        if k != "browser" and isinstance(v, cs.ScanResult)}
        if preset_id == "custom":
            for cid, result in scan_results.items():
                if self._id_to_scanner_name.get(cid) in cleanup_presets.NEVER_INCLUDED:
                    continue
                chosen.extend((i.path, i.size) for i in result.items if i.safety == "safe")
        else:
            try:
                chosen.extend((i.path, i.size) for i in cleanup_presets.items_for_preset(
                    preset_id, scan_results, self._id_to_scanner_name))
            except ValueError as exc:
                logger.warning("preset %s could not be resolved: %s", preset_id, exc)
        browser = self._display.get("browser")
        if isinstance(browser, cs.ScanResult):
            chosen.extend((i.path, i.size) for i in browser.items)
        return chosen

    def _refresh_figures(self) -> None:
        from modules.cleanup.cleanup_scanner import format_size
        from modules.cleanup.cleanup_presets import PRESETS
        rows = []
        self._effective_by_path = {}
        for group in self._group_by_cid.values():
            for item in group.items():
                level = group.item_safety(item)
                rows.append((item.path, item.size, level))
                self._effective_by_path[ntpath.normcase(item.path)] = level
        fig = compute_figures(rows, self._preset_items(),
                              getattr(self, "_figures_with_data", 0))
        self._figures = fig
        pid = self._preset_combo.currentData()
        plabel = PRESETS[pid]["label"] if pid in PRESETS else "Custom"
        t = self._tiles
        cats = fig.categories_with_data
        t["total"].set(format_size(fig.total),
                       f"{fig.locations} locations in {cats} categor{'y' if cats == 1 else 'ies'}")
        reviewed = sum(size for path, size in self._preset_items()
                       if self._effective_by_path.get(ntpath.normcase(path), "safe") != "safe")
        safe_sub = f"{fig.preset_count} locations · {plabel} preset"
        safe_tip = ("<b>Safe to clean now</b><p>What “Clean” would remove with the "
                    f"<b>{plabel}</b> preset: {fig.preset_count} locations, "
                    f"{format_size(fig.preset_bytes)}. The preset decides from the catalog's "
                    "own rating, exactly as before.</p>")
        if reviewed:
            safe_sub = f"{plabel} preset · {format_size(reviewed)} of it rated Review"
            safe_tip += (f"<p>{format_size(reviewed)} of it is rated Review or Risky by the "
                         "research, which is more cautious than the catalog there. Read those "
                         "rows (ⓘ) before you clean, or pick them by hand on their tab.</p>")
        t["safe"].set(format_size(fig.preset_bytes), safe_sub, safe_tip)
        t["review"].set(format_size(fig.review_bytes),
                        f"{fig.review_count} locations · start unticked")
        self._refresh_drive_tile()

    def _refresh_drive_tile(self) -> None:
        from modules.cleanup.cleanup_scanner import format_size
        tile = self._tiles["drive"]
        if self._free_c is None:
            if self._free_c_error:
                tile.set(DASH, "C: could not be read")
            return
        add = (self._figures.preset_on_drive.get("C:", 0) if self._figures else 0)
        tile.set(format_size(self._free_c + add),
                 f"now {format_size(self._free_c)} free · estimate")

    # ── Snapshot, recommendations, facts, trend ─────────────────────────

    def _snapshot_per_tab(self) -> dict:
        from modules.cleanup import cleanup_scanner as cs
        from dataclasses import replace
        per_tab: Dict[str, dict] = {}
        for cid, result in self._display.items():
            if (cid in self._scan_errors or cid in self._timed_out_cids
                    or not isinstance(result, cs.ScanResult) or not result.items):
                continue
            group = self._group_by_cid.get(cid)
            selected_paths = {item.path for item in group._items_to_clean()} if group else set()
            result = replace(result, items=[replace(item, selected=item.path in selected_paths)
                                            for item in result.items])
            fn = self._scanner_map.get(cid, self._adv_scanner_map.get(cid, (None,)))[0]
            if cid in _RECO_TAB:
                tab = _RECO_TAB[cid]
            else:
                cat = getattr(getattr(fn, "spec", None), "category", "")
                tab = _RECO_TAB_BY_CATALOG.get(cat, "overview")
            bucket = per_tab.setdefault(tab, {})
            key = fn if fn is not None else (BROWSER_KB_ID if cid == "browser" else cid)
            if key in bucket:
                from modules.cleanup.cleanup_scanner.knowledge import id_for_scanner
                key = id_for_scanner(fn) if fn is not None else cid
            bucket[key] = result
        return per_tab

    def _publish_snapshot(self) -> None:
        try:
            from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot
            not_scanned = []
            for cid, (_fn, label, _color) in {**self._scanner_map, **self._adv_scanner_map}.items():
                if cid in self._scan_errors:
                    reason = self._scan_errors[cid]
                elif cid in self._timed_out_cids or (self._scanning and cid not in self._results):
                    reason = "Still scanning; no completed result yet."
                elif cid not in self._results:
                    reason = "Not scanned yet."
                else:
                    continue
                not_scanned.append({'label': label, 'reason': reason})
            snap = ScanSnapshot.from_results(self._snapshot_per_tab(),
                                             not_scanned=not_scanned,
                                             preset=str(self._preset_combo.currentData() or ""))
        except (ImportError, AttributeError, TypeError, ValueError, KeyError, OSError) as exc:
            logger.warning("Quick Cleanup: could not build the scan snapshot: %s", exc)
            return
        self._snapshot = snap
        self.snapshot_ready.emit(snap)
        self._reco_pending = True
        if self.isVisible():
            self._refresh_recommendations()

    def _refresh_recommendations(self) -> None:
        if self._reco is None or self._snapshot is None or not self._reco_pending:
            return
        self._reco_pending = False
        self._reco.refresh(self._snapshot)

    def showEvent(self, event):
        super().showEvent(event)
        if self._reco_pending:
            QTimer.singleShot(0, self._refresh_recommendations)
        if not getattr(self, "_trend_loaded", False):
            self._refresh_trend()

    def _start_facts_job(self) -> None:
        """Free space on C: and the not-offered census, off the UI thread."""
        from modules.cleanup.components.kpi_tiles import not_offered_on_this_pc

        def _run(_worker):
            import shutil
            free, err = None, ""
            try:
                free = shutil.disk_usage("C:\\").free
            except OSError as exc:
                err = str(exc)
            try:
                from modules.cleanup.cleanup_scanner.catalog import load_catalog
                info = not_offered_on_this_pc(load_catalog().values())
            except (ImportError, OSError, ValueError) as exc:
                logger.warning("not-offered census failed: %s", exc)
                info = None
            return free, err, info

        def _done(res):
            if not _alive(self):
                return
            free, err, info = res
            self._free_c, self._free_c_error = free, err
            self._not_offered = info
            if info is not None:
                self._tiles["blocked"].set(
                    str(info.count), "locations kept out on purpose",
                    not_offered_tooltip(info))
            else:
                self._tiles["blocked"].set(DASH, "could not be checked",
                                           not_offered_tooltip(None))
            self._refresh_drive_tile()

        def _err(msg):
            if not _alive(self):
                return
            logger.warning("Quick Cleanup facts job failed: %s", msg)

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._track_worker(w)
        QThreadPool.globalInstance().start(w)

    def _refresh_trend(self) -> None:
        """Cleans from the deletion log and scans from the scan summaries."""
        self._trend_loaded = True

        def _run(_worker):
            from modules.cleanup import cleanup_history as ch
            from modules.cleanup.cleanup_scanner.scan_snapshot import read_summaries
            cleans = ch.trend_points(ch.recent_deleted(500))
            scans = ch.scan_points(read_summaries())
            return cleans, scans

        def _done(res):
            if not _alive(self):
                return
            from modules.cleanup.components.charts import TrendPoint
            cleans, scans = res
            self._trend.set_data([TrendPoint(w, v) for w, v in cleans],
                                 [TrendPoint(w, v) for w, v in scans])
            from datetime import datetime
            from modules.cleanup.cleanup_scanner import format_size
            rows = [(w, v, None) for w, v in cleans] + [(w, None, v) for w, v in scans]
            self._set_chart_table(self._trend_card, [
                ((datetime.fromtimestamp(w).strftime("%Y-%m-%d %H:%M"),
                  format_size(freed) if freed is not None else DASH,
                  format_size(reclaimable) if reclaimable is not None else DASH),
                 "Measured clean history and saved scan summaries.")
                for w, freed, reclaimable in sorted(rows, key=lambda row: row[0])])

        def _err(msg):
            if not _alive(self):
                return
            logger.warning("Quick Cleanup trend could not be read: %s", msg)
            self._trend.set_empty_text("History could not be read", str(msg)[:120])

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._track_worker(w)
        QThreadPool.globalInstance().start(w)

    # ── Chart and recommendation interaction ────────────────────────────

    def _donut_tooltip(self, mark) -> str:
        from html import escape
        from modules.cleanup.components.charts import OTHER_KEY, percent_text, safety_mix_text
        from modules.cleanup.cleanup_scanner import format_size
        total = self._donut.total()
        head = (f"<b>{escape(mark.label)}</b> · {escape(format_size(mark.bytes))} · "
                f"{escape(percent_text(mark.bytes, total))}")
        mix = safety_mix_text(mark.safety_mix)
        body = f"<p>{escape(mix)}</p>" if mix else ""
        if mark.key == OTHER_KEY:
            names = ", ".join(mark.members[:12]) + ("…" if len(mark.members) > 12 else "")
            return f"<div style='max-width:420px'>{head}{body}<p>{escape(names)}</p>" \
                   "<p>Click to open More locations.</p></div>"
        group = self._group_by_cid.get(mark.key)
        if group is None:
            return head + body
        return group.row_tooltip().replace("</div>", f"{body}<p>Click to find it below.</p></div>", 1)

    def _bar_tooltip(self, mark) -> str:
        from html import escape
        cid = mark.key.split("\n", 1)[0]
        group = self._group_by_cid.get(cid)
        path = f"<p><b>Path:</b> {escape(mark.detail)}</p>"
        if group is None:
            return path
        return group.row_tooltip().replace("</div>", path + "</div>", 1)

    def _reveal(self, cid: str) -> None:
        group = self._group_by_cid.get(cid)
        if group is None:
            return
        if group in self._adv_cards and not self._adv_shown:
            self._toggle_advanced()
        group.set_expanded(True)
        QTimer.singleShot(0, lambda g=group: self._scroll.ensureWidgetVisible(g, 0, 40)
                          if _alive(g) else None)
        group._header.setFocus()

    def _on_donut_clicked(self, key: str) -> None:
        from modules.cleanup.components.charts import OTHER_KEY
        self._donut.set_selected(key)
        if key == OTHER_KEY:
            if not self._adv_shown:
                self._toggle_advanced()
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(self._show_adv_btn, 0, 40)
                              if _alive(self) else None)
            return
        self._reveal(key)

    def _on_bar_clicked(self, key: str) -> None:
        self._reveal(key.split("\n", 1)[0])

    def _on_reco_navigate(self, tab: str, ids: list, age_days: int) -> None:
        """An overview target is shown here; any other tab is the owner's."""
        if tab == "overview":
            wanted = set(ids)
            hits = [cid for cid, g in self._group_by_cid.items()
                    if g.catalog_id() in wanted and g.total_size() > 0]
            for cid in hits:
                self._reveal(cid)
            if hits:
                self._reveal(hits[0])
            return
        self.navigate.emit(tab, list(ids), int(age_days))

    def _expand_groups(self, expanded: bool) -> None:
        for g in self._all_groups():
            if g.isVisible() or not expanded:
                if not expanded or g.total_size() > 0:
                    g.set_expanded(expanded)

    def _update_adv_button(self) -> None:
        if not hasattr(self, "_show_adv_btn"):
            return
        arrow = "▾" if self._adv_shown else "▸"
        if not self._scanned:
            n = len(self._adv_cards)
            text = f"{arrow}  More locations ({n})"
            self._adv_empty.setText("Scan to see which of these exist on this PC.")
            self._adv_empty.setVisible(True)
        else:
            found = sum(1 for g in self._adv_cards if g.total_size() > 0)
            size = sum(g.total_size() for g in self._adv_cards)
            from modules.cleanup.cleanup_scanner import format_size
            text = f"{arrow}  More locations ({found})  ·  {format_size(size)}"
            self._adv_empty.setText("None of these found anything on this PC."
                                    if found == 0 else "")
            self._adv_empty.setVisible(found == 0)
        self._show_adv_btn.setText(text)
        self._show_adv_btn.setAccessibleName(text.replace(arrow, "").strip())

    # ── Cancel and watchdog ──────────────────────────────────────────────

    def _on_stop_clicked(self) -> None:
        if self._scanning:
            self._reset_after_cancel(message="Scan stopped. Nothing was deleted; click Scan all "
                                             "to start again.")

    def _on_scan_watchdog(self) -> None:
        if not self._scanning:
            return
        label_by_id = {cid: label for cid, label, _ in self._categories + self._advanced_categories}
        missing = [label_by_id.get(cid, cid) for cid in self._scan_targets
                   if cid not in self._results]
        stuck_desc = ", ".join(missing) if missing else "unknown"
        logger.warning(
            "Quick Cleanup scan timed out after %.0fs with %d/%d "
            "categories reported; still waiting on: %s",
            self.SCAN_WATCHDOG_MS / 1000, self._total_scanned,
            len(self._scan_targets), stuck_desc)
        self._reset_after_cancel(
            message=f"Scan timed out — stuck on: {stuck_desc} (click Scan all to retry)")

    def _reset_after_cancel(self, message: str = None) -> None:
        """Put the tab back in a state the user can act on -- shared by the
        watchdog, Stop and cancel(). A cancelled Worker emits `cancelled`,
        never `result` or `error`, so _on_all_scanned would never run."""
        self._watchdog.stop()
        self._scan_generation += 1
        retire(self._workers)       # held until delivered: see core.worker.retire
        for t in self._per_category_timers:
            t.stop()
        self._per_category_timers = []
        self._scanning = False
        # A cancelled scan must not leave clean_now()'s deferred action armed.
        self._clean_after_scan = False
        # Nothing was measured, so the tab has NOT been scanned.
        self._scanned = False
        for g in self._all_groups():
            g.cancel()
        if hasattr(self, "_scan_all_btn"):
            self._scan_all_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._clean_all_btn.setEnabled(self._has_cleanable_items())
        if hasattr(self, "_progress_row"):
            self._progress_timer.stop()
            self._progress_row.hide()
        if hasattr(self, "_status_lbl") and message:
            self._status_lbl.setText(message)
            self._status_lbl.setToolTip(message)

    def _has_cleanable_items(self) -> bool:
        """Is there at least one item the CURRENT preset would clean?
        Mirrors what _do_clean_all_safe actually does."""
        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs
        from modules.cleanup import cleanup_presets

        preset_id = self._preset_combo.currentData() if hasattr(self, "_preset_combo") else "light"

        for cid, result in self._results.items():
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = result or []
                if isinstance(browser_results, cs.ScanResult):
                    if browser_results.items:
                        return True
                    continue
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                return True
            elif isinstance(result, cs.ScanResult):
                if preset_id == "custom":
                    if self._id_to_scanner_name.get(cid) in cleanup_presets.NEVER_INCLUDED:
                        continue
                    if any(item.safety == "safe" for item in result.items):
                        return True
                else:
                    items = cleanup_presets.items_for_preset(
                        preset_id, {cid: result}, self._id_to_scanner_name)
                    if items:
                        return True
        return False

    def _deduplicate_across_categories(self, results: dict) -> dict:
        """Drop items already claimed by an earlier category.

        `dedupe_items` removes a repeated or nested path from ONE list; this
        applies the same rule across the whole scan. First claim wins, in
        display order, which keeps every slice attributable to one category.
        """
        from modules.cleanup import cleanup_scanner as cs

        order = [cid for cid, _label, _colour in self._categories]
        order += [cid for cid, _label, _colour in self._advanced_categories]

        claimed: list = []
        deduped = dict(results)
        for cid in order:
            result = results.get(cid)
            if not isinstance(result, cs.ScanResult) or not result.items:
                continue
            merged = cs.dedupe_items(claimed + list(result.items))
            already = {id(i) for i in claimed}
            kept = [i for i in merged if id(i) not in already]
            fresh = cs.ScanResult()
            fresh.items = kept
            fresh.total_size = sum(i.size for i in kept)
            deduped[cid] = fresh
            claimed = merged
        return deduped

    def _on_group_scan_done(self, item_count: int, total_size: int):
        """Kept for callers of the old API; a single category's rescan does
        not change the dashboard's totals."""

    def _category_updated(self, cid: str) -> None:
        """Reconcile a category's rescan, including a post-clean measurement."""
        if self._scanning:
            return
        group = self._group_by_cid[cid]
        self._results[cid] = group._results
        self._render_results()
        self._publish_snapshot()

    def _after_group_clean(self, _freed: int) -> None:
        if _alive(self):
            self._refresh_trend()

    def _handle_category_clicked(self, cid: str) -> None:
        if self._on_category_clicked is not None:
            self._on_category_clicked(cid)

    def _toggle_advanced(self):
        """Show/hide the More locations list."""
        self._adv_shown = not self._adv_shown
        self._adv_widget.setVisible(self._adv_shown)
        self._update_adv_button()

    # ── Clean with the preset ───────────────────────────────────────────

    def _do_clean_all_safe(self):
        if self._scanning:
            return
        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs
        from modules.cleanup import clean_safe_runner as csr

        all_safe: List[cs.ScanItem] = []
        needs_wu = False
        total = 0
        browser_cats: List[bs.CacheCategory] = []

        from modules.cleanup import cleanup_presets
        preset_id = self._preset_combo.currentData()

        for cid in self._results:
            if cid == "browser":
                browser_results = self._results.get(cid, [])
                if isinstance(browser_results, cs.ScanResult):
                    continue
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                browser_cats.append(cat)
                                total += cat.size_bytes
            elif cid == "wu":
                needs_wu = True

        scan_results = {k: v for k, v in self._results.items()
                        if k != "browser" and isinstance(v, cs.ScanResult)}
        if preset_id == "custom":
            for cid, result in scan_results.items():
                if self._id_to_scanner_name.get(cid) in cleanup_presets.NEVER_INCLUDED:
                    continue
                for item in result.items:
                    if item.safety == "safe":
                        item.selected = True
                        all_safe.append(item)
                        total += item.size
        else:
            selected_items = cleanup_presets.items_for_preset(
                preset_id, scan_results, self._id_to_scanner_name)
            for item in selected_items:
                item.selected = True
                all_safe.append(item)
                total += item.size

        if not all_safe and not browser_cats:
            return

        def _on_done(deleted, errors, freed):
            if not _alive(self):
                return
            self._scanning = False
            self._scan_all_btn.setEnabled(True)
            self._progress_row.hide()
            from modules.cleanup.cleanup_scanner import format_size
            msg = f"Cleaned {deleted} item(s) · {format_size(freed)} freed (measured)"
            if errors:
                msg += f" — {errors} could not be deleted; see the History tab"
            self._status_lbl.setText(msg)
            self._status_lbl.setToolTip(msg)
            self.freed_bytes.emit(freed)        # measured, never the scanned total
            self._refresh_trend()
            self.scan()

        def _on_error(e: str):
            if not _alive(self):
                return
            self._scanning = False
            self._scan_all_btn.setEnabled(True)
            self._progress_row.hide()
            self._status_lbl.setText(f"Clean error: {e}")

        preset_label = (
            cleanup_presets.PRESETS[preset_id]["label"]
            if preset_id != "custom" else None)

        worker = csr.run_clean_safe(
            self, all_safe, browser_cats=browser_cats, stop_wuauserv=needs_wu,
            confirm="always", preset_label=preset_label,
            on_done=_on_done, on_error=_on_error)
        if worker is None:
            return  # user declined the confirm -- nothing was disabled yet

        self._scanning = True
        self._scan_all_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._clean_all_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress_lbl.setText("Cleaning… files in use are skipped and reported.")
        self._progress_row.show()
        self._track_worker(worker)

    def _main_scanner_definitions(self, cs):
        return {
            # Main categories
            "temp":     (cs.scan_temp_files,              "safe"),
            "prefetch": (cs.scan_prefetch,                "caution"),
            "thumb":    (cs.scan_thumbnail_cache,         "safe"),
            "crash":    (cs.scan_user_crash_dumps,        "caution"),
            "browser":   (None,                            "safe"),
            "app":      (cs.scan_app_caches,               "safe"),
            "logs":     (cs.scan_windows_logs,            "caution"),
            "wu":       (cs.scan_wu_cache,                 "caution"),
            "large":    (cs.scan_windows_old,              "caution"),
            "dev":      (cs.scan_dev_tool_caches,           "safe"),
            # Advanced categories (existing)
            "recent":   (cs.scan_recent_files,            "caution"),
            "games":    (cs.scan_game_caches,              "caution"),
            "adobe":    (cs.scan_adobe_cache,              "safe"),
            "office":   (cs.scan_office_temp,             "safe"),
            "jets":     (cs.scan_ide_caches,              "caution"),
            "spooler":  (cs.scan_print_spooler,           "caution"),
            "winsat":   (cs.scan_winsat_cache,            "safe"),
            "etl":      (cs.scan_etl_logs,               "caution"),
            "telemetry":(cs.scan_telemetry,               "caution"),
            "delivery": (cs.scan_delivery_opt_user,       "safe"),
            "clipboard":(cs.scan_clipboard,               "safe"),
            "xbox":     (cs.scan_xbox_cache,              "safe"),
            "maps":     (cs.scan_maps_cache,              "safe"),
            "sticky":   (cs.scan_sticky_notes,            "safe"),
            "defender": (cs.scan_defender_history,        "safe"),
            "onedrive": (cs.scan_onedrive_logs,           "safe"),
            # Cloud Storage
            "dropbox":   (cs.scan_dropbox_cache,          "safe"),
            "gdrive":    (cs.scan_google_drive_cache,     "safe"),
            "mega":      (cs.scan_mega_cache,             "safe"),
            "pcloud":    (cs.scan_pcloud_cache,           "safe"),
            "icloud":    (cs.scan_icloud_cache,            "safe"),
            "box":       (cs.scan_box_cache,               "safe"),
            # Virtualization
            "docker":    (cs.scan_docker_desktop_cache,   "caution"),
            "vbox":      (cs.scan_virtualbox_cache,        "caution"),
            "vmware":    (cs.scan_vmware_cache,            "caution"),
            "wsl2":      (cs.scan_wsl2_cache,             "caution"),
            "hyperv":    (cs.scan_hyperv_cache,            "caution"),
            # Media Production
            "obs":       (cs.scan_obs_cache,               "safe"),
            "davinci":   (cs.scan_davinci_cache,          "safe"),
            "premiere":  (cs.scan_premiere_cache,         "safe"),
            "blender":   (cs.scan_blender_full_cache,      "safe"),
            "audacity":  (cs.scan_audacity_cache,         "safe"),
            # Communication
            "telegram":  (cs.scan_telegram_cache,          "safe"),
            "signal":    (cs.scan_signal_cache,            "safe"),
            "teams":     (cs.scan_teams_cache,             "caution"),
            "slack":     (cs.scan_slack_cache_full,        "safe"),
            "discord":   (cs.scan_discord_full_cache,      "safe"),
            # Development
            "jetbrains": (cs.scan_jetbrains_cache,         "safe"),
            "eclipse":   (cs.scan_eclipse_cache,           "safe"),
            "gitlfs":    (cs.scan_git_lfs_cache,           "safe"),
            "npm":       (cs.scan_npm_cache,               "safe"),
            "pip":       (cs.scan_pip_cache,              "safe"),
            "nuget":     (cs.scan_nuget_cache,             "safe"),
            "vscode":    (cs.scan_vscode_cache,            "safe"),
            "unity":     (cs.scan_unity_hub_cache,         "caution"),
            # Games
            "epic":      (cs.scan_epic_games_cache,       "safe"),
            "battlenet": (cs.scan_battlenet_cache,         "safe"),
            "rockstar":  (cs.scan_rockstar_cache,          "safe"),
            "minecraft": (cs.scan_minecraft_cache,          "safe"),
            "lol":       (cs.scan_lol_cache,               "safe"),
            "rust":      (cs.scan_rust_game_cache,         "safe"),
            # More Browsers
            "brave":     (cs.scan_brave_cache,             "safe"),
            "vivaldi":   (cs.scan_vivaldi_cache,           "safe"),
            "opera":     (cs.scan_opera_cache,             "safe"),
            "yandex":    (cs.scan_yandex_cache,            "safe"),
            "edge":      (cs.scan_edge_cache,              "safe"),
            "firefox":   (cs.scan_firefox_cache,          "safe"),
            "chrome":    (cs.scan_chrome_cache,            "safe"),
        }

    def _extra_scanner_definitions(self, cs):
        return {
            # System
            "iis":       (cs.scan_iis_logs,               "safe"),
            "dockerimg": (cs.scan_docker_desktop_cache,    "caution"),
            "vpn":       (cs.scan_openvpn_cache,          "safe"),
            "putty":     (cs.scan_putty_cache,            "safe"),
            "rdp":       (cs.scan_rdp_cache,              "safe"),
            # PC-Specific
            "vscode_ext":   (cs.scan_vscode_cached_extensions, "safe"),
            "vscode_dawn":  (cs.scan_vscode_dawn_cache,        "safe"),
            "vscode_web":   (cs.scan_vscode_webstorage,        "safe"),
            "steam_logs":   (cs.scan_steam_logs,                "safe"),
            "steam_web":    (cs.scan_steam_webhelper_cache,    "safe"),
            "chrome_full":  (cs.scan_chrome_cache_full,        "safe"),
            "edge_full":    (cs.scan_edge_cache_full,          "safe"),
            "brave_full":   (cs.scan_brave_cache_full,         "safe"),
            "uwp_all":      (cs.scan_uwp_all_apps_cache,       "safe"),
            "lm_studio":    (cs.scan_lm_studio_cache,          "safe"),
            "teams_npc":    (cs.scan_ms_teams_npc_cache,       "safe"),
            "photos_cache": (cs.scan_windows_photos_cache,     "safe"),
            "ms_store":     (cs.scan_ms_store_cache,           "safe"),
            "discord_logs": (cs.scan_discord_developer_logs,   "safe"),
            "notifications":(cs.scan_notifications_cache,      "safe"),
            "spotify_app":  (cs.scan_spotify_app_cache,        "safe"),
            "cbs_logs":     (cs.scan_windows_cbs_logs,         "safe"),
            "panther_logs": (cs.scan_windows_panther_logs,     "safe"),
            "inetcache":     (cs.scan_inetcache_ietlc,          "safe"),
            "game_bar":     (cs.scan_windows_game_bar_cache,   "safe"),
            "yarn":         (cs.scan_yarn_cache,               "safe"),
            "pnpm":         (cs.scan_pnpm_cache,               "safe"),
            # Smart Finders
            "empty_folders": (cs.scan_empty_folders,          "safe"),
            # Catalog-backed (see the matching comment on ADVANCED_CATEGORIES
            # above) -- each function is generated by catalog.py's
            # scanner_for(id) and bound onto this module at import time via
            # cleanup_scanner/__init__.py's `globals().update(all_scanners())`,
            # the exact same mechanism every hand-picked entry above already
            # resolves through; the safety tier here is read from the
            # catalog spec itself (catalog.load_catalog()[id].safety), not
            # re-guessed.
            "complus_cache":             (cs.scan_complus_cache,             "caution"),
            "office_cache_extended":     (cs.scan_office_cache_extended,     "safe"),
            "onedrive_cache":            (cs.scan_onedrive_cache,            "safe"),
            "onedrive_commercial_cache": (cs.scan_onedrive_commercial_cache, "safe"),
            "onedrive_full_cache":       (cs.scan_onedrive_full_cache,       "safe"),
            "claude_cli_cache":          (cs.scan_claude_cli_cache,          "safe"),
            "npp_cache":                 (cs.scan_npp_cache,                 "safe"),
            "vscode_cached_data":        (cs.scan_vscode_cached_data,        "safe"),
            "vscode_settings_sync":      (cs.scan_vscode_settings_sync,      "caution"),
            "xbox_app_cache":            (cs.scan_xbox_app_cache,            "safe"),
            "amd_radeon_cache":          (cs.scan_amd_radeon_cache,          "safe"),
            "browser_caches":            (cs.scan_browser_caches,            "safe"),
            "d3d_shader_cache":          (cs.scan_d3d_shader_cache,          "safe"),
            "diag_logs":                 (cs.scan_diag_logs,                 "caution"),
            "directx_shader_cache":      (cs.scan_directx_shader_cache,      "safe"),
            "dxgi_cache":                (cs.scan_dxgi_cache,                "safe"),
            "event_logs":                (cs.scan_event_logs,                "caution"),
            "font_cache":                (cs.scan_font_cache,                "caution"),
            "language_packs":            (cs.scan_language_packs,            "caution"),
            "memory_dumps":              (cs.scan_memory_dumps,              "caution"),
            "minidump":                  (cs.scan_minidump,                  "caution"),
            "per_drive_recycle_bin":     (cs.scan_per_drive_recycle_bin,     "safe"),
            "per_drive_temp":            (cs.scan_per_drive_temp,            "caution"),
            "sfc_logs":                  (cs.scan_sfc_logs,                  "caution"),
            "sysprep_logs":              (cs.scan_sysprep_logs,              "caution"),
            "thumbnail_cache_central":   (cs.scan_thumbnail_cache_central,   "safe"),
            "update_cleanup":            (cs.scan_update_cleanup,            "caution"),
            "wer_reports":               (cs.scan_wer_reports,               "caution"),
            "windows_backup_catalog":    (cs.scan_windows_backup_catalog,    "caution"),
            "windows_backup_logs":       (cs.scan_windows_backup_logs,       "caution"),
            "windows_setup_diags":       (cs.scan_windows_setup_diags,       "caution"),
            "windows_tweaker_logs":      (cs.scan_windows_tweaker_logs,      "safe"),
            "windowsupdate_orch_cache":  (cs.scan_windowsupdate_orch_cache,  "caution"),
            "wu_history_cache":          (cs.scan_wu_history_cache,          "caution"),
            "curseforge_cache":          (cs.scan_curseforge_cache,          "safe"),
            "amd_dvr_cache":             (cs.scan_amd_dvr_cache,             "caution"),
            "cyberghost_cache":          (cs.scan_cyberghost_cache,          "safe"),
            "opencode_desktop_cache":    (cs.scan_opencode_desktop_cache,    "safe"),
            "amd_comgr_cache":           (cs.scan_amd_comgr_cache,           "safe"),
            "amd_install_manager_cache": (cs.scan_amd_install_manager_cache, "safe"),
        }

    def _build_toolbar(self, outer, PRESETS, preset_names):
        # ── Toolbar ──
        bar = QWidget()
        bar.setObjectName("overviewToolbar")
        toolbar = QHBoxLayout(bar)
        toolbar.setContentsMargins(12, 10, 12, 4)
        toolbar.setSpacing(8)

        self._scan_all_btn = QPushButton("Scan all")
        self._scan_all_btn.setToolTip(
            "Measure every location in the catalog that exists on this PC (read-only; "
            "nothing is deleted). F5.")
        self._scan_all_btn.setAccessibleName("Scan all locations")
        self._scan_all_btn.clicked.connect(self.scan)
        toolbar.addWidget(self._scan_all_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setToolTip("Stop the scan. Locations already measured are discarded "
                                  "and the tab goes back to its unscanned state.")
        self._stop_btn.setAccessibleName("Stop scanning")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        toolbar.addWidget(self._stop_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("toolbarSeparator")
        toolbar.addWidget(sep)

        preset_lbl = QLabel("Preset")
        preset_lbl.setObjectName("muted")
        toolbar.addWidget(preset_lbl)

        self._preset_combo = QComboBox()
        self._preset_combo.setToolTip(
            "<b>What “Clean” removes</b><p><b>Light</b>: only locations rated Safe.<br>"
            "<b>Thorough</b>: Safe and Review.<br><b>Aggressive</b>: everything, Risky "
            "included (never orphaned profiles or virtual disks).<br><b>Custom</b>: here, "
            "the same as Light (this dashboard has no per-item ticks for Custom to use).</p>"
            "<p>Every clean asks first and lists what it will do.</p>")
        self._preset_combo.setAccessibleName("Cleanup preset")
        for pid in preset_names():
            label = PRESETS[pid]["label"] if pid in PRESETS else "Custom"
            self._preset_combo.addItem(label, pid)
        self._preset_combo.setCurrentIndex(self._preset_combo.findData("light"))
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        toolbar.addWidget(self._preset_combo)

        self._clean_all_btn = _AccentButton("Clean (Light)…")
        self._clean_all_btn.setEnabled(False)
        self._clean_all_btn.setToolTip(
            "Clean what the selected preset includes, after a confirmation that names it. "
            "Freed space is what the deleter measured, not what the scan estimated.")
        self._clean_all_btn.setAccessibleName("Clean with the selected preset")
        self._clean_all_btn.clicked.connect(self._do_clean_all_safe)
        toolbar.addWidget(self._clean_all_btn)

        self._status_lbl = QLabel("Click Scan all to measure what this PC can reclaim.")
        self._status_lbl.setObjectName("muted")
        self._status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._status_lbl.setMinimumWidth(80)
        toolbar.addWidget(self._status_lbl, 1)
        outer.addWidget(bar)

    def _build_progress(self, outer):
        prog = QWidget()
        prog_lay = QHBoxLayout(prog)
        prog_lay.setContentsMargins(12, 0, 12, 4)
        prog_lay.setSpacing(10)
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setMaximumWidth(360)
        self._progress.setAccessibleName("Scan progress")
        prog_lay.addWidget(self._progress)
        self._progress_lbl = QLabel("")
        self._progress_lbl.setObjectName("muted")
        self._progress_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        prog_lay.addWidget(self._progress_lbl, 1)
        self._progress_row = prog
        prog.hide()
        outer.addWidget(prog)
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(400)
        self._progress_timer.timeout.connect(self._update_progress)

    def _build_kpi_tiles(self, lay):
        # Row A — key figures
        self._tiles = {
            "total": KpiTile("Reclaimable", "total"),
            "safe": KpiTile("Safe to clean now", "safe"),
            "review": KpiTile("Needs review", "review"),
            "blocked": KpiTile("Not offered", "blocked"),
            "drive": KpiTile("Free after clean (C:)", "drive"),
        }
        self._kpi = KpiStrip(list(self._tiles.values()))
        lay.addWidget(self._kpi)
        self._reset_tiles()

    def _build_charts(self, lay, BarChart, ChartCard, DonutChart, TrendChart):
        # Row B — charts
        self._donut = DonutChart(title="Space by category", top_n=8)
        self._donut.slice_clicked.connect(self._on_donut_clicked)
        self._donut.set_tooltip_provider(self._donut_tooltip)
        self._bars = BarChart(title="Largest locations", top_n=10)
        self._bars.slice_clicked.connect(self._on_bar_clicked)
        self._bars.set_tooltip_provider(self._bar_tooltip)
        self._trend = TrendChart(title="Trend")
        self._donut_card = ChartCard("Space by category", self._donut,
                                     "Every location, largest first. Click a slice to find it below.")
        self._bars_card = ChartCard("Largest locations", self._bars,
                                    "Top 10 across every category; each path counted once.")
        self._trend_card = ChartCard("Trend", self._trend,
                                     "Freed per clean, total freed, and reclaimable at each scan.")
        self._add_chart_table(self._donut_card, ["Category", "Size", "Share"])
        self._add_chart_table(self._bars_card, ["Location", "Size", "Safety"])
        self._add_chart_table(self._trend_card, ["When", "Freed", "Reclaimable"])
        for card, tip in ((self._donut_card, "Hover a slice for its size, share and safety mix."),
                          (self._bars_card, "Hover a bar for what the location is and its full path."),
                          (self._trend_card, "From the clean history and earlier scans.")):
            card.title_label.setToolTip(tip)
        for c in (self._donut_card, self._bars_card, self._trend_card):
            c.setMinimumHeight(300)
        self._charts = _ChartsRow([self._donut_card, self._bars_card, self._trend_card])
        lay.addWidget(self._charts)

    def _build_recommendations(self, lay):
        # Row C — recommendations
        self._reco_card = QFrame()
        self._reco_card.setObjectName("chartCard")
        self._reco_card.setStyleSheet("QStackedWidget, QStackedWidget > QWidget "
                                      "{ background: transparent; }")
        reco_lay = QVBoxLayout(self._reco_card)
        reco_lay.setContentsMargins(14, 12, 14, 12)
        self._reco = None
        try:
            from modules.cleanup.components.recommendations_panel import RecommendationsPanel
            self._reco = RecommendationsPanel(self._reco_card, config=self._app_config(),
                                              thread_pool=QThreadPool.globalInstance())
            self._reco.navigate.connect(self._on_reco_navigate)
            reco_lay.addWidget(self._reco)
        except ImportError as exc:
            logger.warning("recommendations unavailable: %s", exc)
            self._reco_card.hide()
        lay.addWidget(self._reco_card)

    def _build_locations(self, lay):
        # Row D — locations
        loc = QFrame()
        loc.setObjectName("chartCard")
        # The rows are painted on the card itself; without this every plain
        # QWidget inside it would fill itself with the pane colour.
        loc.setStyleSheet(
            "QWidget#categoryGroup, QWidget#categoryRow, QWidget#categoryPathsBox, "
            "QWidget#categoryRows, QWidget#categoryColumns, QTreeWidget#categoryPaths "
            "{ background: transparent; }")
        loc_lay = QVBoxLayout(loc)
        loc_lay.setContentsMargins(6, 12, 6, 8)
        loc_lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 8, 8)
        title = QLabel("Locations")
        title.setObjectName("chartTitle")
        title.setToolTip("Every category this dashboard scans. Click a row to see and tick its "
                         "paths; ⓘ explains it; ⋯ has its actions.")
        head.addWidget(title)
        head.addSpacing(10)
        self._loc_hint = QLabel("Largest first · click a row to see its paths")
        self._loc_hint.setObjectName("muted")
        head.addWidget(self._loc_hint)
        head.addStretch()
        self._expand_all_btn = QToolButton()
        self._expand_all_btn.setText("Expand all")
        self._expand_all_btn.setToolTip("Show the paths of every category that found something.")
        self._expand_all_btn.clicked.connect(lambda: self._expand_groups(True))
        head.addWidget(self._expand_all_btn)
        self._collapse_all_btn = QToolButton()
        self._collapse_all_btn.setText("Collapse all")
        self._collapse_all_btn.setToolTip("Close every category's path list.")
        self._collapse_all_btn.clicked.connect(lambda: self._expand_groups(False))
        head.addWidget(self._collapse_all_btn)
        loc_lay.addLayout(head)

        self._col_header = ColumnHeader()
        self._col_header.setObjectName("categoryColumns")
        loc_lay.addWidget(self._col_header)

        self._main_box = QVBoxLayout()
        self._main_box.setContentsMargins(0, 0, 0, 0)
        self._main_box.setSpacing(0)
        loc_lay.addLayout(self._main_box)

        self._groups_container: List[CategoryGroup] = []
        self._group_by_cid: Dict[str, CategoryGroup] = {}
        self._legend_cards: List[CategoryGroup] = []
        for cid, clabel, _colour in self._categories:
            fn = self._scanner_map.get(cid, (None,))[0]
            group = self._make_group(cid, clabel, fn, main=True)
            self._legend_cards.append(group)
            self._groups_container.append(group)
            self._group_by_cid[cid] = group
            self._main_box.addWidget(group)

        self._show_adv_btn = QToolButton()
        self._show_adv_btn.setObjectName("disclosure")
        self._show_adv_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._show_adv_btn.setToolTip(
            "Smaller and more specific locations: app, game, cloud, developer and Windows "
            "caches. Only the ones that found something are listed, largest first.")
        self._show_adv_btn.clicked.connect(self._toggle_advanced)
        self._adv_shown = False
        adv_row = QHBoxLayout()
        adv_row.setContentsMargins(8, 8, 8, 4)
        adv_row.addWidget(self._show_adv_btn)
        adv_row.addStretch()
        loc_lay.addLayout(adv_row)

        self._adv_widget = QWidget()
        self._adv_widget.setObjectName("categoryRows")
        self._adv_box = QVBoxLayout(self._adv_widget)
        self._adv_box.setContentsMargins(0, 0, 0, 0)
        self._adv_box.setSpacing(0)
        self._adv_cards: List[CategoryGroup] = []
        for cid, clabel, _colour in self._advanced_categories:
            fn = self._adv_scanner_map.get(cid, (None,))[0]
            group = self._make_group(cid, clabel, fn, main=False)
            group.setVisible(False)
            self._adv_cards.append(group)
            self._group_by_cid[cid] = group
            self._adv_box.addWidget(group)
        self._adv_empty = QLabel("")
        self._adv_empty.setObjectName("muted")
        self._adv_empty.setContentsMargins(34, 8, 8, 8)
        self._adv_box.addWidget(self._adv_empty)
        self._adv_widget.setVisible(False)
        loc_lay.addWidget(self._adv_widget)
        self._update_adv_button()
        lay.addWidget(loc)
