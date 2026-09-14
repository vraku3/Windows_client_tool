"""QuickCleanupTab — dashboard-style cleanup with pie chart and category groups.

Provides:
- Summary pie chart showing reclaimable space by category
- Per-category expandable group cards with scan/clean
- Batch "Clean All" across all categories
- Advanced expandable section with additional categories
- One-click system maintenance actions
- Background scanning via Worker threads
- Auto-refresh (external control via start/stop)
"""
import logging
import os
import subprocess
from typing import Dict, List

from PyQt6.QtCore import Qt, QTimer, QThreadPool, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QScrollArea, QFrame,
    QSizePolicy, QMessageBox,
)

from core.long_op_pool import get_long_op_pool
from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup.components.category_group import CategoryGroup
from core.semantic_colors import semantic

CREATE_NO_WINDOW = 0x08000000

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


# ── Advanced Categories ────────────────────────────────────────────────────────

ADVANCED_CATEGORIES = [
    # Core
    ("recent",    "Recent Files",       "#90caf9"),
    ("games",     "Game Caches",        "#ce93d8"),
    ("adobe",     "Adobe Cache",        "#ef9a9a"),
    ("office",    "Office Temp",        "#80deea"),
    ("jets",      "IDE Caches",         "#fff59d"),
    ("spooler",   "Print Spooler",     "#a5d6a7"),
    ("winsat",    "WinSAT Cache",       "#ffcc80"),
    ("etl",       "ETL Logs",          "#b0bec5"),
    ("telemetry", "Telemetry Data",    "#ef9a9a"),
    ("delivery",  "Delivery Optim.",    "#fff176"),
    ("clipboard", "Clipboard",          "#80cbc4"),
    ("xbox",      "Xbox Cache",         "#c5e1a5"),
    ("onedrive",  "OneDrive Logs",     "#64b5f6"),
    ("maps",      "Maps Cache",         "#80d8ff"),
    ("sticky",    "Sticky Notes",       "#f8bbd0"),
    ("defender",  "Defender History",   "#d1c4e9"),
    # Cloud Storage
    ("dropbox",   "Dropbox Cache",     "#aed6f1"),
    ("gdrive",    "Google Drive",      "#76d7c4"),
    ("mega",      "MEGA Cache",        "#f1948a"),
    ("pcloud",    "pCloud Cache",       "#85c1e9"),
    ("icloud",    "iCloud Cache",       "#82e0aa"),
    ("box",       "Box Cache",          "#bb8fce"),
    # Virtualization
    ("docker",    "Docker Desktop",    "#f8c471"),
    ("vbox",      "VirtualBox VMs",      "#73c6b6"),
    ("vmware",    "VMware VMs",          "#85c1e9"),
    ("wsl2",      "WSL2 Distros",        "#58d68d"),
    ("hyperv",    "Hyper-V VMs",         "#ecf0f1"),
    # Media Production
    ("obs",       "OBS Cache",            "#f9e79f"),
    ("davinci",   "DaVinci Cache",        "#abebc6"),
    ("premiere",  "Premiere Cache",       "#d2b4de"),
    ("blender",   "Blender Cache",        "#fad7a0"),
    ("audacity",  "Audacity Cache",       "#d5dbdb"),
    # Communication
    ("telegram",  "Telegram Cache",       "#85c1e9"),
    ("signal",    "Signal Cache",         "#58d68d"),
    ("teams",     "Teams Cache",          "#76d7c4"),
    ("slack",     "Slack Cache",          "#f1948a"),
    ("discord",   "Discord Cache",        "#aed6f1"),
    # Development
    ("jetbrains", "JetBrains Cache",     "#f39c12"),
    ("eclipse",   "Eclipse Cache",        "#5dade2"),
    ("gitlfs",    "Git LFS Cache",        "#f1948a"),
    ("npm",       "npm Cache",            "#73c6b6"),
    ("pip",       "pip Cache",            "#85c1e9"),
    ("nuget",     "NuGet Cache",          "#82e0aa"),
    ("vscode",    "VSCode Cache",         "#00bfff"),
    ("unity",     "Unity Cache",          "#f8c471"),
    # Games
    ("epic",      "Epic Games Cache",    "#f7dc6f"),
    ("battlenet", "Battle.net Cache",     "#3498db"),
    ("rockstar",  "Rockstar Cache",       "#e74c3c"),
    ("minecraft", "Minecraft Cache",      "#58d68d"),
    ("lol",       "LoL Cache",            "#f39c12"),
    ("rust",      "Rust Game Cache",      "#e67e22"),
    # More Browsers
    ("brave",     "Brave Cache",         "#f39c12"),
    ("vivaldi",   "Vivaldi Cache",       "#9b59b6"),
    ("opera",     "Opera Cache",         "#e74c3c"),
    ("yandex",    "Yandex Cache",        "#f39c12"),
    ("edge",      "Edge Cache",          "#3498db"),
    ("firefox",   "Firefox Cache",       "#e67e22"),
    ("chrome",    "Chrome Cache",        "#2980b9"),
    # System
    ("iis",       "IIS Logs",             "#bdc3c7"),
    ("dockerimg", "Docker Images",        "#f5b041"),
    ("vpn",       "VPN Cache",            "#5dade2"),
    ("putty",     "PuTTY Cache",          "#f0b27a"),
    ("rdp",       "RDP Cache",            "#85c1e9"),
    # PC-Specific (auto-discovered)
    ("vscode_ext",   "VSCode Ext VSIXs",   "#00bfff"),
    ("vscode_dawn",  "VSCode Dawn Cache",  "#80d8ff"),
    ("vscode_web",   "VSCode WebStorage",  "#b3e5fc"),
    ("steam_logs",   "Steam Logs",         "#1f618d"),
    ("steam_web",    "Steam WebCache",     "#2874a6"),
    ("chrome_full",  "Chrome Full Cache",  "#2980b9"),
    ("edge_full",     "Edge Full Cache",   "#3498db"),
    ("brave_full",   "Brave Full Cache",   "#f39c12"),
    ("uwp_all",      "UWP Apps Cache",     "#aed6f1"),
    ("lm_studio",    "LM Studio Cache",   "#58d68d"),
    ("teams_npc",    "MS Teams NPC",      "#76d7c4"),
    ("photos_cache",  "Windows Photos",    "#80deea"),
    ("ms_store",      "MS Store Cache",   "#64b5f6"),
    ("discord_logs",  "Discord Logs",      "#9b59b6"),
    ("notifications", "Notif. History",   "#80cbc4"),
    ("spotify_app",   "Spotify Cache",    "#1db954"),
    ("cbs_logs",      "CBS Logs",         "#bdc3c7"),
    ("panther_logs",  "Panther Logs",     "#ffcc80"),
    ("inetcache",     "INetCache",        "#90caf9"),
    ("game_bar",      "Game Bar Cache",   "#c5e1a5"),
    ("yarn",          "Yarn Cache",      "#73c6b6"),
    ("pnpm",          "pnpm Cache",       "#82e0aa"),
    # Smart Finders
    ("empty_folders",  "Empty Folders",      "#bdc3c7"),
]


# ── Pie Chart ────────────────────────────────────────────────────────────────

class _PieChart(QWidget):
    """Pure-Qt donut chart showing reclaimable space by category."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slices: List[tuple] = []   # (label, size_bytes, color)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_slices(self, slices: List[tuple]) -> None:
        """Set slices: list of (label, size_bytes, css_color_string)."""
        self._slices = slices
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        size = min(w, h)

        # Donut: outer radius = half the widget, inner radius = 40%
        cx = w / 2
        cy = h / 2
        outer_r = size / 2 - 4
        inner_r = outer_r * 0.45

        total = sum(s[1] for s in self._slices)
        if total == 0:
            # Draw empty grey ring
            painter.setPen(QPen(QColor("#555"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(int(cx - outer_r), int(cy - outer_r),
                                int(outer_r * 2), int(outer_r * 2))
            painter.setPen(QPen(QColor("#888")))
            painter.drawText(int(cx), int(cy), "No data")
            return

        start_angle = 0
        for label, size_bytes, color in self._slices:
            span = int(size_bytes / total * 360 * 16)
            painter.setPen(QPen(QColor(color), 2))
            painter.setBrush(QBrush(QColor(color)))
            rect_x = int(cx - outer_r)
            rect_y = int(cy - outer_r)
            rect_w = int(outer_r * 2)
            rect_h = int(outer_r * 2)
            painter.drawPie(rect_x, rect_y, rect_w, rect_h,
                           int(start_angle), int(span))
            start_angle += span

        # Inner circle (donut hole)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#2d2d2d")))
        painter.drawEllipse(int(cx - inner_r), int(cy - inner_r),
                            int(inner_r * 2), int(inner_r * 2))

        # Centre text: total size
        from modules.cleanup.cleanup_scanner import format_size
        painter.setPen(QColor("#ffffff"))
        font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(int(cx), int(cy - 6), format_size(total))
        font2 = QFont("Segoe UI", 7)
        painter.setFont(font2)
        painter.setPen(QColor("#aaaaaa"))
        painter.drawText(int(cx), int(cy + 8), "reclaimable")


# ── Category slice card ───────────────────────────────────────────────────────

class _SliceCard(QFrame):
    """Small legend card shown below the pie chart for each category."""

    clicked = pyqtSignal()

    def __init__(self, label: str, size_bytes: int, color: str, parent=None,
                 clickable: bool = True):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # QLabel derives from QFrame, so an unscoped `QFrame { ... }` rule set
        # on this card also styled every label inside it -- each one drew its
        # own copy of the accent stripe. The rule has to name the card.
        self.setObjectName("sliceCard")
        # The ~91 advanced cards can emit `clicked` but nothing ever
        # connects to it (they don't map 1:1 onto a single tab) -- giving
        # them a pointing-hand cursor advertised a click that did nothing.
        self._clickable = clickable
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color = color
        self._update_style(size_bytes)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        self._dot = QLabel(f"<span style='color:{color};font-size:14px'>●</span>")
        # No colour in the markup -- a stylesheet cannot reach inside rich
        # text, so this grey outlived every theme change.
        self._lbl = QLabel(label)
        self._lbl.setStyleSheet("font-size:12px")
        self._sz = QLabel()
        self._sz.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        from modules.cleanup.cleanup_scanner import format_size
        # No colour in the markup: a QSS rule cannot reach inside rich text, so a
        # colour written here would survive the theme switch like the rest did.
        self._sz.setText(f"<span style='font-size:11px'>{format_size(size_bytes)}</span>")
        lay.addWidget(self._dot)
        lay.addWidget(self._lbl, 1)
        lay.addWidget(self._sz)

    def _update_style(self, size_bytes: int):
        # Only the category's own accent stripe is declared here. An inline
        # sheet overrides just the properties it names, so leaving `background`
        # out lets the active theme keep painting it -- naming it was what
        # pinned this row to #3c3c3c in both themes.
        self.setStyleSheet(f"""
            QFrame#sliceCard {{
                border-left: 3px solid {self._color};
                border-radius: 4px;
                padding: 4px 8px;
            }}
        """)

    def set_size(self, size_bytes: int) -> None:
        """Update the displayed size and opacity."""
        from modules.cleanup.cleanup_scanner import format_size
        # No colour in the markup: a QSS rule cannot reach inside rich text, so a
        # colour written here would survive the theme switch like the rest did.
        self._sz.setText(f"<span style='font-size:11px'>{format_size(size_bytes)}</span>")
        self._update_style(size_bytes)
        self.setVisible(size_bytes > 0)

    def mousePressEvent(self, event) -> None:
        if self._clickable:
            self.clicked.emit()
        super().mousePressEvent(event)


# ── QuickCleanupTab ──────────────────────────────────────────────────────────

# Category definitions: (id, display_name, color)
# scanner_fn is resolved in build() from the _id_map
CLEANUP_CATEGORIES = [
    ("temp",      "Temp Files",       "#4fc3f7"),
    ("prefetch",  "Prefetch",         "#ffb74d"),
    ("thumb",     "Thumbnail Cache",  "#81c784"),
    ("crash",     "Crash Dumps",     "#ce93d8"),
    ("browser",   "Browser Caches",   "#4dd0e1"),
    ("app",       "App Caches",       "#a5d6a7"),
    ("logs",      "Windows Logs",     "#fff59d"),
    ("wu",        "Windows Update",   "#ff8a65"),
    ("large",     "Large Items",      "#ef9a9a"),
    ("dev",       "Dev Tools",        "#b0bec5"),
]


class QuickCleanupTab(QWidget):
    """
    Dashboard-style cleanup view with a donut chart and per-category groups.

    Lifecycle:
        - Construct, then call build() to create category groups.
        - Auto-refresh: call start_auto_refresh() / stop_auto_refresh().
        - scan() triggers a background rescan of all categories.
        - cancel() cancels any in-flight workers.
    """

    # Emitted when all scans complete: (total_items, total_size)
    scan_done = pyqtSignal(int, int)

    # Emitted after a successful Clean All Safe -- feeds CleanupModule's
    # shared "Freed this session" counter, the same signal every other
    # tab in the module already emits.
    freed_bytes = pyqtSignal(int)

    #: _OverviewTab used to have the same constant, before it was deleted
    #: in this merge. This sweep covers MORE categories than Overview's
    #: did, so give it real headroom above whatever this machine's own
    #: scan measures, not a number copied from a different, smaller sweep.
    SCAN_WATCHDOG_MS = 300_000

    def __init__(self, parent=None, on_category_clicked=None):
        super().__init__(parent)
        self._on_category_clicked = on_category_clicked
        self._categories: List[tuple] = []   # (id, label, color, scanner_fn)
        self._group_widgets: Dict[str, CategoryGroup] = {}
        self._results: Dict[str, object] = {}   # id -> ScanResult
        self._scanning = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_timer_refresh)
        self._refresh_interval_ms = 30_000
        self._workers: List[Worker] = []
        self._scanned = False
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_scan_watchdog)

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self, categories: List[tuple] = None, advanced_categories: List[tuple] = None) -> None:
        """Build the UI. Call once after construction.

        categories: list of (id, display_name, color).
                    Defaults to CLEANUP_CATEGORIES.
        advanced_categories: list of (id, display_name, color).
                    Defaults to ADVANCED_CATEGORIES.
        """
        if categories is None:
            categories = CLEANUP_CATEGORIES
        if advanced_categories is None:
            advanced_categories = ADVANCED_CATEGORIES
        self._categories = categories
        self._advanced_categories = advanced_categories

        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs
        _id_map = {
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
            "recent":   (cs.scan_recent_files,            "safe"),
            "games":    (cs.scan_game_caches,              "safe"),
            "adobe":    (cs.scan_adobe_cache,              "safe"),
            "office":   (cs.scan_office_temp,             "safe"),
            "jets":     (cs.scan_ide_caches,              "safe"),
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
        }

        self._scanner_map = {}
        self._browser_scanner = bs.detect_browsers  # for browser category

        for cid, clabel, ccolor in categories:
            fn_safety = _id_map.get(cid, (None, "safe"))
            self._scanner_map[cid] = (fn_safety[0], clabel, ccolor)

        self._adv_scanner_map = {}
        for cid, clabel, ccolor in advanced_categories:
            fn_safety = _id_map.get(cid, (None, "safe"))
            self._adv_scanner_map[cid] = (fn_safety[0], clabel, ccolor)

        self._setup_ui()

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
        """Scan once, the first time this tab is shown -- CleanupModule's
        _on_tab_changed calls this on every tab switch, the same as every
        other tab in the module (_ScanTab, _OverviewTab before it)."""
        if not self._scanned:
            self._do_scan_all()

    def cancel(self) -> None:
        self._reset_after_cancel()

    # ── Setup ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Top toolbar ──
        toolbar = QHBoxLayout()
        self._scan_all_btn = QPushButton("🔍  Scan All")
        self._scan_all_btn.clicked.connect(self.scan)
        self._clean_all_btn = QPushButton("🗑️  Clean All Safe")
        self._clean_all_btn.setEnabled(False)
        self._clean_all_btn.clicked.connect(self._do_clean_all_safe)
        self._status_lbl = QLabel("Click Scan All to analyze your system")
        self._status_lbl.setObjectName("muted")
        self._show_adv_btn = QPushButton("Show Advanced ▼")
        self._show_adv_btn.setStyleSheet("font-size: 12px; padding: 4px 10px;")
        self._show_adv_btn.clicked.connect(self._toggle_advanced)
        self._adv_shown = False
        toolbar.addWidget(self._scan_all_btn)
        toolbar.addWidget(self._clean_all_btn)
        toolbar.addWidget(self._show_adv_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)

        # Progress bar
        self._progress = QLabel()
        self._progress.setStyleSheet(f"color: {semantic('warning')}; font-size: 11px;")
        self._progress.hide()
        layout.addWidget(self._progress)

        # ── Dashboard row: pie chart + legend ──
        dash_frame = QFrame()
        dash_frame.setFrameShape(QFrame.Shape.StyledPanel)
        dash_frame.setObjectName("card")
        dash_lay = QHBoxLayout(dash_frame)
        dash_lay.setContentsMargins(12, 12, 12, 12)

        self._pie_chart = _PieChart()
        self._pie_chart.setFixedSize(160, 160)
        dash_lay.addWidget(self._pie_chart)

        # Legend
        self._legend_layout = QVBoxLayout()
        self._legend_layout.setSpacing(4)
        self._legend_cards: List[_SliceCard] = []
        for cid, clabel, ccolor in self._categories:
            card = _SliceCard(clabel, 0, ccolor, clickable=True)
            card.clicked.connect(lambda cid=cid: self._handle_category_clicked(cid))
            self._legend_cards.append(card)
            self._legend_layout.addWidget(card)
        self._legend_layout.addStretch()
        dash_lay.addLayout(self._legend_layout, 1)

        # Summary stats on the right
        stats_lay = QVBoxLayout()
        stats_lay.setSpacing(6)
        self._total_lbl = QLabel("Total: —")
        self._total_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        self._safe_lbl = QLabel("Safe to clean: —")
        self._safe_lbl.setStyleSheet(f"color: {semantic('success')}; font-size: 13px;")
        self._item_lbl = QLabel("Items found: —")
        self._item_lbl.setObjectName("muted")
        self._item_lbl.setStyleSheet("font-size: 12px;")
        self._cat_lbl = QLabel(f"Categories: {len(self._categories)}")
        self._cat_lbl.setObjectName("muted")
        self._cat_lbl.setStyleSheet("font-size: 12px;")
        stats_lay.addWidget(self._total_lbl)
        stats_lay.addWidget(self._safe_lbl)
        stats_lay.addWidget(self._item_lbl)
        stats_lay.addWidget(self._cat_lbl)
        stats_lay.addStretch()
        dash_lay.addLayout(stats_lay)
        dash_lay.addStretch()

        layout.addWidget(dash_frame)

        # ── Scrollable category groups ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(8)

        self._groups_container: List[CategoryGroup] = []
        self._group_by_cid: Dict[str, CategoryGroup] = {}
        for cid, clabel, ccolor in self._categories:
            fn_info = self._scanner_map.get(cid)
            if fn_info is None or fn_info[0] is None:
                continue
            scanner_fn, label, color = fn_info
            group = CategoryGroup(label, scanner_fn, auto_refresh=False)
            group.scan_done.connect(self._on_group_scan_done)
            self._groups_container.append(group)
            self._group_by_cid[cid] = group
            content_lay.addWidget(group)

        content_lay.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # ── One-click actions (always visible -- moved out of the
        # Advanced section during the Cleanup/Quick Cleanup merge, so a
        # fresh user reaches these without ever clicking "Show Advanced") ──
        self._build_one_click_panel(layout)

        # ── Advanced panel (hidden by default) ──
        self._adv_widget = QWidget()
        adv_lay = QVBoxLayout(self._adv_widget)
        adv_lay.setContentsMargins(0, 8, 0, 0)
        adv_lay.setSpacing(8)

        adv_header = QLabel("Advanced Cleanup")
        adv_header.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px 0;")
        adv_lay.addWidget(adv_header)

        # Advanced legend cards
        self._adv_legend_layout = QGridLayout()
        self._adv_legend_layout.setSpacing(6)
        self._adv_cards: List[_SliceCard] = []
        for idx, (cid, clabel, ccolor) in enumerate(self._advanced_categories):
            card = _SliceCard(clabel, 0, ccolor, clickable=False)
            card.setVisible(False)
            self._adv_cards.append(card)
            row = idx // 3
            col = idx % 3
            self._adv_legend_layout.addWidget(card, row, col)
        adv_lay.addLayout(self._adv_legend_layout)

        self._adv_widget.setVisible(False)
        layout.addWidget(self._adv_widget)

    # ── Auto-refresh ────────────────────────────────────────────────────────

    def _on_timer_refresh(self):
        if self._scanning:
            return
        self.scan()

    # ── Scan All ────────────────────────────────────────────────────────────

    def _do_scan_all(self):
        if self._scanning:
            return
        self._scanning = True
        self._watchdog.start(self.SCAN_WATCHDOG_MS)
        self._scan_all_btn.setEnabled(False)
        self._clean_all_btn.setEnabled(False)
        self._progress.setText("🔍  Scanning all categories...")
        self._progress.show()
        self._results.clear()
        self._total_scanned = 0

        from modules.cleanup import cleanup_scanner as cs

        # Build complete scan target list: main + advanced (excl. browser handled specially)
        scan_targets = [cid for cid, _, _ in self._categories]
        scan_targets += [cid for cid, _, _ in self._advanced_categories]
        # Browser will be added below
        browser_target = "browser" in [c[0] for c in self._categories]

        def _start_worker(cid: str, scanner_fn, category_list: list):
            """Launch a worker for a scanner function."""
            def _run(_worker):
                from modules.cleanup.cleanup_scanner import scan_cache
                return scan_cache.cached_scan(scanner_fn, 0)

            def _done(result):
                if not _alive(self):
                    return
                self._results[cid] = result
                self._total_scanned += 1
                if self._total_scanned == len(scan_targets):
                    self._on_all_scanned()

            def _err(_e):
                if not _alive(self):
                    return
                self._results[cid] = cs.ScanResult()
                self._total_scanned += 1
                if self._total_scanned == len(scan_targets):
                    self._on_all_scanned()

            w = Worker(_run)
            w.signals.result.connect(_done)
            w.signals.error.connect(_err)
            self._workers.append(w)
            QThreadPool.globalInstance().start(w)

        # Main categories
        for cid, clabel, ccolor in self._categories:
            if cid == "browser":
                continue
            fn_info = self._scanner_map.get(cid)
            if fn_info is None or fn_info[0] is None:
                scan_targets.remove(cid)
                continue
            scanner_fn = fn_info[0]
            _start_worker(cid, scanner_fn, self._categories)

        # Advanced categories
        for cid, clabel, ccolor in self._advanced_categories:
            fn_info = self._adv_scanner_map.get(cid)
            if fn_info is None or fn_info[0] is None:
                scan_targets.remove(cid)
                continue
            scanner_fn = fn_info[0]
            _start_worker(cid, scanner_fn, self._advanced_categories)

        self._scan_targets = scan_targets

        # Browser as separate worker
        if browser_target:
            def _run_browser(_worker):
                return self._browser_scanner()

            def _done_browser(results):
                if not _alive(self):
                    return
                self._results["browser"] = results
                self._total_scanned += 1
                if self._total_scanned == len(scan_targets):
                    self._on_all_scanned()

            def _err_browser(_e):
                if not _alive(self):
                    return
                self._results["browser"] = []
                self._total_scanned += 1
                if self._total_scanned == len(scan_targets):
                    self._on_all_scanned()

            wb = Worker(_run_browser)
            wb.signals.result.connect(_done_browser)
            wb.signals.error.connect(_err_browser)
            self._workers.append(wb)
            QThreadPool.globalInstance().start(wb)

    def _on_all_scanned(self):
        self._watchdog.stop()
        self._scanned = True
        self._scanning = False
        self._scan_all_btn.setEnabled(True)
        self._progress.hide()
        self._workers = [w for w in self._workers if not w.is_cancelled]

        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs

        # Categories overlap, so their results are deduplicated ACROSS
        # categories before anything is totalled. Measured here before the
        # fix: 2.10 GB claimed against 1.39 GB really present — 50% too
        # high — because two categories can point at the same directory
        # (%TEMP% and %LOCALAPPDATA%\Temp are one place). Each unique path
        # is attributed to the first category that claims it, in the order
        # they are displayed, so the pie slices still sum to the total.
        self._results = self._deduplicate_across_categories(self._results)

        # Build pie chart slices
        slices: List[tuple] = []
        total_size = 0
        total_safe = 0
        total_items = 0
        categories_with_data = 0

        # Main categories
        for i, (cid, clabel, ccolor) in enumerate(self._categories):
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = self._results.get(cid, [])
                bsize = sum(r.total_bytes for r in browser_results)
                bitems = sum(len(r.profiles) for r in browser_results)
                if bsize > 0:
                    slices.append((clabel, bsize, ccolor))
                    total_size += bsize
                    total_items += bitems
                    categories_with_data += 1
                    if i < len(self._legend_cards):
                        self._legend_cards[i].set_size(bsize)
                else:
                    if i < len(self._legend_cards):
                        self._legend_cards[i].set_size(0)
            else:
                result: cs.ScanResult = self._results.get(cid, cs.ScanResult())
                # Feed the result into this category's dropdown so expanding it shows
                # the paths immediately (no separate per-category scan needed).
                group = self._group_by_cid.get(cid)
                if group is not None:
                    group.set_result(result)
                if result.items:
                    slices.append((clabel, result.total_size, ccolor))
                    total_size += result.total_size
                    total_safe += sum(item.size for item in result.items if item.safety == "safe")
                    total_items += len(result.items)
                    categories_with_data += 1
                    if i < len(self._legend_cards):
                        self._legend_cards[i].set_size(result.total_size)
                else:
                    if i < len(self._legend_cards):
                        self._legend_cards[i].set_size(0)

        # Advanced categories
        for i, (cid, clabel, ccolor) in enumerate(self._advanced_categories):
            result: cs.ScanResult = self._results.get(cid, cs.ScanResult())
            if result.items:
                slices.append((clabel, result.total_size, ccolor))
                total_size += result.total_size
                total_safe += sum(item.size for item in result.items if item.safety == "safe")
                total_items += len(result.items)
                categories_with_data += 1
                if i < len(self._adv_cards):
                    self._adv_cards[i].set_size(result.total_size)
            else:
                if i < len(self._adv_cards):
                    self._adv_cards[i].set_size(0)

        self._pie_chart.set_slices(slices)
        self._total_lbl.setText(f"Total: {cs.format_size(total_size)}")
        self._safe_lbl.setText(f"Safe to clean: {cs.format_size(total_safe)}")
        self._item_lbl.setText(f"Items found: {total_items}")
        self._cat_lbl.setText(f"Categories: {len(self._categories)} + {len(self._advanced_categories)} advanced")
        self._clean_all_btn.setEnabled(total_safe > 0)
        self._status_lbl.setText(
            f"Found {total_items} item(s) across {categories_with_data} categories"
            if categories_with_data
            else "No reclaimable space found"
        )
        self.scan_done.emit(total_items, total_size)

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
            message=f"Scan timed out — stuck on: {stuck_desc} (click Scan All to retry)")

    def _reset_after_cancel(self, message: str = None) -> None:
        """Put the tab back in a state the user can act on -- shared by
        the watchdog above and cancel() below. A cancelled Worker emits
        `cancelled`, never `result` or `error` (core/worker.py), so
        _total_scanned would never reach the target count and
        _on_all_scanned would never run on its own.

        `self._workers` also holds one-click-action workers (they are
        appended to this same list by `_run_action_command`/
        `_compact_winsxs`), so cancelling every worker here can cancel one
        of THOSE mid-run too -- and a cancelled Worker never fires the
        `_done`/`_err` closure that is the only thing that re-enables that
        action's own button. Without the loop below, switching away from
        Cleanup while e.g. "Compact WinSxS" was running left that button
        disabled for the rest of the process."""
        self._watchdog.stop()
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._scanning = False
        # Nothing was measured, so the tab has NOT been scanned: let
        # auto_scan() run again the next time the module is activated.
        self._scanned = False
        if hasattr(self, "_scan_all_btn"):
            self._scan_all_btn.setEnabled(True)
            # Gated the same way _on_all_scanned gates it: nothing to
            # clean (a fresh tab, or a scan that never produced a result)
            # must not leave this button looking clickable.
            self._clean_all_btn.setEnabled(self._has_safe_items())
        if hasattr(self, "_progress"):
            self._progress.hide()
        if hasattr(self, "_status_lbl") and message:
            self._status_lbl.setText(message)
        if hasattr(self, "_action_buttons"):
            # Worker.cancel() only sets a flag the subprocess call never
            # checks, so the command may genuinely still be running --
            # this re-enables the button without claiming the action
            # actually stopped.
            for action_id, btn in self._action_buttons.items():
                if btn.isEnabled():
                    continue
                btn.setEnabled(True)
                status_lbl = self._action_status.get(action_id)
                if status_lbl is not None:
                    status_lbl.setText(
                        "cancelled — may still be running in the background")
                    status_lbl.setStyleSheet(
                        f"color: {semantic('warning')}; font-size: 11px;")

    def _has_safe_items(self) -> bool:
        """Is there at least one "safe" item anywhere in the last scan
        results? Mirrors the condition _on_all_scanned uses to enable
        _clean_all_btn, so a cancelled/timed-out scan and a completed one
        agree on when there is actually something to clean."""
        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs

        for cid, result in self._results.items():
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = result or []
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                return True
            elif isinstance(result, cs.ScanResult):
                if any(item.safety == "safe" for item in result.items):
                    return True
        return False

    def _deduplicate_across_categories(self, results: dict) -> dict:
        """Drop items already claimed by an earlier category.

        `dedupe_items` removes a repeated or nested path from ONE list;
        this applies the same rule across the whole scan, so a directory
        counted under "Temp Files" is not counted again under "Crash
        Dumps". First claim wins, in display order, which keeps every slice
        attributable to exactly one category.
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
        """Forward from individual category groups."""
        pass  # Individual group scans don't update the dashboard summary

    def _handle_category_clicked(self, cid: str) -> None:
        if self._on_category_clicked is not None:
            self._on_category_clicked(cid)

    def _toggle_advanced(self):
        """Show/hide the advanced cleanup section."""
        self._adv_shown = not self._adv_shown
        self._adv_widget.setVisible(self._adv_shown)
        self._show_adv_btn.setText("Hide Advanced ▲" if self._adv_shown else "Show Advanced ▼")

    def _build_one_click_panel(self, parent_lay: QVBoxLayout):
        """One row per action: its own button, its own status label, its
        own busy-guard. Before this, all 12 actions shared one QLabel and
        none of them disabled their own button while running."""
        sep = QLabel("One-Click Maintenance")
        sep.setObjectName("muted")
        sep.setStyleSheet("font-size: 13px; font-weight: bold; padding-top: 8px;")
        parent_lay.addWidget(sep)

        actions = [
            ("flush_dns", "Flush DNS", self._flush_dns),
            ("clear_event_logs", "Clear Event Logs", self._clear_event_logs),
            ("compact_winsxs", "Compact WinSxS", self._compact_winsxs),
            ("rebuild_icon_cache", "Rebuild Icons", self._rebuild_icon_cache),
            ("wu_deep_clean", "WU Deep Clean", self._wu_deep_clean),
            ("network_repair", "Network Repair", self._network_repair),
            ("clear_thumbnails", "Clear Thumbnails", self._clear_thumbnails),
            ("clear_clipboard", "Clear Clipboard", self._clear_clipboard),
            ("reset_search", "Reset Search", self._reset_search),
            ("clear_font_cache", "Clear Font Cache", self._clear_font_cache),
            ("flush_wu_store", "Flush WinUpdate", self._flush_wu_store),
            ("reset_tcpip", "Reset TCP/IP", self._reset_tcpip),
            ("resize_hibernation", "Right-size Hibernation", self._resize_hibernation),
            ("clear_print_queue", "Clear Stuck Print Jobs", self._clear_print_queue),
        ]

        self._action_buttons: Dict[str, QPushButton] = {}
        self._action_status: Dict[str, QLabel] = {}
        for action_id, label, handler in actions:
            row = QHBoxLayout()
            btn = QPushButton(label)
            btn.setStyleSheet("font-size: 11px; padding: 4px 8px;")
            btn.clicked.connect(handler)
            status = QLabel("")
            status.setObjectName("muted")
            status.setStyleSheet("font-size: 11px;")
            row.addWidget(btn)
            row.addWidget(status, 1)
            parent_lay.addLayout(row)
            self._action_buttons[action_id] = btn
            self._action_status[action_id] = status

    def _run_action_command(self, action_id: str, cmd: str, status_prefix: str,
                              need_confirm: bool = False,
                              long_running: bool = False,
                              confirm_text: str = ""):
        """Run a system command as a one-click action."""
        btn = self._action_buttons.get(action_id)
        if btn is not None and not btn.isEnabled():
            return  # already running
        status_lbl = self._action_status[action_id]
        if need_confirm:
            mb = QMessageBox(self)
            mb.setWindowTitle("Confirm Action")
            mb.setIcon(QMessageBox.Icon.Warning)
            default_text = confirm_text or "This action cannot be undone. Continue?"
            mb.setText(default_text)
            mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if mb.exec() != QMessageBox.StandardButton.Ok:
                return

        status_lbl.setText(f"{status_prefix}...")
        status_lbl.setStyleSheet(f"color: {semantic('warning')}; font-size: 11px;")

        def _run(_worker):
            try:
                if long_running:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        shell=True,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    # Wait up to 5 minutes for long-running commands
                    stdout, stderr = proc.communicate(timeout=300)
                    success = proc.returncode == 0
                    output = stdout if success else (stderr or "Command failed")
                else:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        shell=True,
                        creationflags=CREATE_NO_WINDOW,
                        timeout=60,
                    )
                    success = result.returncode == 0
                    output = result.stdout.strip() if result.stdout else result.stderr.strip() or "Done"
            except subprocess.TimeoutExpired:
                return "timeout", "Command timed out after 5 minutes"
            except Exception as e:
                return "error", str(e)
            return "ok" if success else "error", output

        def _done(result):
            if btn is not None:
                btn.setEnabled(True)
            status, msg = result
            if status == "ok":
                status_lbl.setText(f"✅ {status_prefix}: {msg}")
                status_lbl.setStyleSheet(f"color: {semantic('success')}; font-size: 11px;")
            elif status == "timeout":
                status_lbl.setText(f"⏱ {status_prefix}: {msg}")
                status_lbl.setStyleSheet(f"color: {semantic('warning')}; font-size: 11px;")
            else:
                status_lbl.setText(f"❌ {status_prefix}: {msg}")
                status_lbl.setStyleSheet(f"color: {semantic('error')}; font-size: 11px;")

        def _err(e: str):
            if btn is not None:
                btn.setEnabled(True)
            status_lbl.setText(f"❌ {status_prefix}: {e}")
            status_lbl.setStyleSheet(f"color: {semantic('error')}; font-size: 11px;")

        if btn is not None:
            btn.setEnabled(False)
        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        if long_running:
            # Can run for minutes (e.g. WU deep clean) — bounded pool, not
            # the global one everything else shares.
            get_long_op_pool().start(w)
        else:
            QThreadPool.globalInstance().start(w)

    def _flush_dns(self):
        self._run_action_command("flush_dns", "ipconfig /flushdns", "DNS cache flushed", need_confirm=False)

    def _clear_event_logs(self):
        self._run_action_command(
            "clear_event_logs",
            "wevtutil cl System && wevtutil cl Application && wevtutil cl Security",
            "Event logs cleared",
            need_confirm=True,
            confirm_text="This will clear System, Application, and Security event logs. They cannot be recovered. Continue?"
        )

    def _compact_winsxs(self):
        btn = self._action_buttons.get("compact_winsxs")
        if btn is not None and not btn.isEnabled():
            return  # already running

        mb = QMessageBox(self)
        mb.setWindowTitle("Compact WinSxS")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText(
            "This runs <b>DISM /StartComponentCleanup /ResetBase</b> which can take "
            "<b>10–30 minutes</b>. The system will remain usable. Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return

        status_lbl = self._action_status["compact_winsxs"]
        status_lbl.setText("⏳ WinSxS cleanup running (may take 10–30 min)...")
        status_lbl.setStyleSheet(f"color: {semantic('warning')}; font-size: 11px;")
        if btn is not None:
            btn.setEnabled(False)

        def _run(_worker):
            try:
                proc = subprocess.Popen(
                    ["Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup", "/ResetBase"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                stdout, stderr = proc.communicate(timeout=3600)
                success = proc.returncode == 0
                output = stdout if success else (stderr or "Command failed")
            except subprocess.TimeoutExpired:
                return "timeout", "Operation timed out after 60 minutes"
            except Exception as e:
                return "error", str(e)
            return "ok" if success else "error", output

        def _done(result):
            if btn is not None:
                btn.setEnabled(True)
            outcome, msg = result
            if outcome == "ok":
                status_lbl.setText("✅ WinSxS cleanup complete")
                status_lbl.setStyleSheet(f"color: {semantic('success')}; font-size: 11px;")
            elif outcome == "timeout":
                status_lbl.setText(f"⏱ WinSxS cleanup: {msg}")
                status_lbl.setStyleSheet(f"color: {semantic('warning')}; font-size: 11px;")
            else:
                status_lbl.setText(f"❌ WinSxS cleanup: {msg[:100]}")
                status_lbl.setStyleSheet(f"color: {semantic('error')}; font-size: 11px;")

        def _err(e: str):
            if btn is not None:
                btn.setEnabled(True)
            status_lbl.setText(f"❌ WinSxS cleanup: {e}")
            status_lbl.setStyleSheet(f"color: {semantic('error')}; font-size: 11px;")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._workers.append(w)
        # 10-30 min DISM run — bounded pool, not the global one everything else shares.
        get_long_op_pool().start(w)

    def _rebuild_icon_cache(self):
        self._run_action_command(
            "rebuild_icon_cache",
            "taskkill /f /im explorer.exe && timeout /t 2 /nobreak >nul && del /q \"%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\iconcache_*\" 2>nul && start explorer",
            "Icon cache rebuilt",
            need_confirm=False
        )

    def _wu_deep_clean(self):
        self._run_action_command(
            "wu_deep_clean",
            "dism /Online /Cleanup-Image /StartComponentCleanup /SuppressDefaultActions",
            "WU deep clean started",
            need_confirm=True,
            long_running=True,  # confirm text says 10-20 min; the default path's 60s timeout would kill it early
            confirm_text="This runs a deep Windows Update cleanup which may take 10–20 minutes. Continue?"
        )

    def _network_repair(self):
        mb = QMessageBox(self)
        mb.setWindowTitle("Network Repair")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            "This will <b>reset Winsock and TCP/IP stack</b>. "
            "Your network connection will briefly drop. "
            "<b>This cannot be undone.</b> Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return
        self._run_action_command(
            "network_repair", "netsh winsock reset && netsh int ip reset",
            "Network stack reset", need_confirm=False
        )

    def _clear_thumbnails(self):
        """Delete all thumbnail cache files (.db) in Explorer thumbnail directories."""
        self._run_action_command(
            "clear_thumbnails",
            'del /q /f "%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\thumbcache_*.db" 2>nul',
            "Thumbnail cache cleared",
            need_confirm=False
        )

    def _clear_clipboard(self):
        """Clear the Windows clipboard content."""
        self._run_action_command(
            "clear_clipboard", "cmd /c echo off | clip", "Clipboard cleared", need_confirm=False
        )

    def _reset_search(self):
        mb = QMessageBox(self)
        mb.setWindowTitle("Reset Windows Search")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText(
            "This will <b>restart the Windows Search service</b> and clear its database. "
            "Search may be briefly unavailable. Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return
        self._run_action_command(
            "reset_search", "net stop WSearch && net start WSearch",
            "Windows Search reset", need_confirm=False
        )

    def _clear_font_cache(self):
        """Flush the Windows Font Cache service (FNTCACHE.DAT)."""
        mb = QMessageBox(self)
        mb.setWindowTitle("Clear Font Cache")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            "This will <b>flush the Windows Font Cache</b> by stopping the FontCache service. "
            "Applications may briefly re-render text. Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return
        self._run_action_command(
            "clear_font_cache", "net stop FontCache && net start FontCache",
            "Font cache cleared", need_confirm=False
        )

    def _flush_wu_store(self):
        mb = QMessageBox(self)
        mb.setWindowTitle("Flush Windows Update Store")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            "This will <b>reset the Windows Update client</b>, clear the SoftwareDistribution\\Download "
            "folder, and restart the WUAUSERV service. "
            "<b>This cannot be undone.</b> Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return
        cmd = (
            "net stop wuauserv && "
            "del /q /f %SystemRoot%\\SoftwareDistribution\\Download\\* 2>nul && "
            "net start wuauserv"
        )
        self._run_action_command("flush_wu_store", cmd, "Windows Update store flushed", need_confirm=False)

    def _reset_tcpip(self):
        mb = QMessageBox(self)
        mb.setWindowTitle("Reset TCP/IP Stack")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            "This will <b>reset all network adapter TCP/IP configurations</b>. "
            "Network adapters may briefly disconnect. <b>This cannot be undone.</b> Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return
        self._run_action_command(
            "reset_tcpip", "netsh int ip reset", "TCP/IP stack reset", need_confirm=False
        )

    def _resize_hibernation(self):
        # Gate on hibernation actually being enabled -- powercfg refuses
        # /hibernate /size on a machine where it's off, and the raw error
        # text is not obviously "hibernation is off" to a fresh user
        # reading a one-line status label.
        #
        # This used to parse "has not been enabled" out of `powercfg /a`'s
        # stdout, which only matches on English Windows and fails open (a
        # confusing raw-command status) on any other locale. hiberfil.sys
        # existing or not is the locale-independent, and more direct,
        # signal: it is literally the file this whole action resizes, and
        # a plain os.path.exists() call cannot raise the way a subprocess
        # call can, so there is nothing here left needing a try/except.
        system_drive = os.environ.get("SystemDrive", "C:")
        hiberfil = os.path.join(system_drive + "\\", "hiberfil.sys")
        if not os.path.exists(hiberfil):
            self._action_status["resize_hibernation"].setText(
                "Hibernation is off on this machine — nothing to resize")
            return

        mb = QMessageBox(self)
        mb.setWindowTitle("Right-size Hibernation File")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText(
            "This shrinks hiberfil.sys to 50% of RAM (Windows' own "
            "default since Windows 10) without disabling hibernation. "
            "Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return

        self._run_action_command(
            "resize_hibernation", "powercfg /hibernate /size 50",
            "Hibernation file resized", need_confirm=False)

    def _clear_print_queue(self):
        mb = QMessageBox(self)
        mb.setWindowTitle("Clear Stuck Print Jobs")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            "This will <b>stop the Print Spooler</b>, clear all queued "
            "print jobs, and restart it. Any job currently printing or "
            "queued will be lost. Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return
        # `net start spooler` must run even when the `del` fails (e.g. a
        # spool file is locked) -- an all-&& chain would leave the spooler
        # stopped and printing broken with just a generic error status.
        # `&` between del and the restart makes that step unconditional;
        # the parens keep it grouped so the leading `&&` still gates the
        # whole group on `net stop spooler` actually succeeding.
        cmd = (
            "net stop spooler && "
            "(del /q /f %SystemRoot%\\System32\\spool\\PRINTERS\\* 2>nul & "
            "net start spooler)"
        )
        self._run_action_command("clear_print_queue", cmd, "Print queue cleared", need_confirm=False)

    # ── Clean All Safe ─────────────────────────────────────────────────────

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

        for cid in self._results:
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = self._results.get(cid, [])
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                browser_cats.append(cat)
                                total += cat.size_bytes
            else:
                result: cs.ScanResult = self._results.get(cid, cs.ScanResult())
                for item in result.items:
                    if item.safety == "safe":
                        item.selected = True
                        all_safe.append(item)
                        total += item.size
                if cid == "wu":
                    needs_wu = True

        if not all_safe and not browser_cats:
            return

        def _on_done(deleted, errors):
            self._scanning = False
            self._scan_all_btn.setEnabled(True)
            self._progress.hide()
            msg = f"Cleaned {deleted} item(s)"
            if errors:
                msg += f" — {errors} could not be deleted"
            self._status_lbl.setText(msg)
            self.freed_bytes.emit(total)
            self.scan()

        def _on_error(e: str):
            self._scanning = False
            self._scan_all_btn.setEnabled(True)
            self._progress.hide()
            self._status_lbl.setText(f"Clean error: {e}")

        worker = csr.run_clean_safe(
            self, all_safe, browser_cats=browser_cats, stop_wuauserv=needs_wu,
            confirm="always", on_done=_on_done, on_error=_on_error)
        if worker is None:
            return  # user declined the confirm -- nothing was disabled yet

        self._scanning = True
        self._scan_all_btn.setEnabled(False)
        self._clean_all_btn.setEnabled(False)
        self._progress.setText("🗑️  Cleaning safe items...")
        self._progress.show()
        self._workers.append(worker)
