"""QuickCleanupTab — dashboard-style cleanup with pie chart and category groups.

Provides:
- Summary pie chart showing reclaimable space by category
- Per-category expandable group cards with scan/clean
- Batch "Clean All" across all categories
- Advanced expandable section with additional categories
- Background scanning via Worker threads
- Auto-refresh (external control via start/stop)

One-click system maintenance actions used to live here too; they moved to
Quick Fix's card catalog (`modules/quick_fix/fix_actions.py`, "Cleanup"
category) during the module-consolidation merge, and this tab's one-click
panel was deleted entirely.
"""
import logging
from typing import Dict, List

from PyQt6.QtCore import Qt, QTimer, QThreadPool, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QScrollArea, QFrame,
    QSizePolicy, QComboBox,
)

from core.widget_life import widget_is_valid
from core.worker import Worker
from modules.cleanup.components.category_group import CategoryGroup
from core.semantic_colors import semantic

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

_CATEGORY_COLORS = [
    "#4fc3f7", "#ffb74d", "#81c784", "#ce93d8", "#4dd0e1", "#a5d6a7",
    "#fff59d", "#ff8a65", "#ef9a9a", "#b0bec5", "#90caf9", "#80deea",
    "#ffcc80", "#fff176", "#80cbc4", "#c5e1a5", "#64b5f6", "#80d8ff",
    "#f8bbd0", "#d1c4e9", "#aed6f1", "#76d7c4", "#f1948a", "#85c1e9",
    "#82e0aa", "#bb8fce", "#f8c471", "#73c6b6", "#58d68d", "#ecf0f1",
    "#f9e79f", "#abebc6", "#d2b4de", "#fad7a0", "#d5dbdb", "#f39c12",
    "#5dade2", "#00bfff", "#f7dc6f", "#3498db", "#e74c3c", "#e67e22",
    "#9b59b6", "#2980b9", "#bdc3c7", "#f5b041", "#f0b27a", "#b3e5fc",
    "#1f618d", "#2874a6", "#1db954", "#2ecc71",
]

ADVANCED_CATEGORIES = [
    # Core
    ("recent", "Recent Files", _CATEGORY_COLORS[10]),
    ("games", "Game Caches", _CATEGORY_COLORS[3]),
    ("adobe", "Adobe Cache", _CATEGORY_COLORS[8]),
    ("office", "Office Temp", _CATEGORY_COLORS[11]),
    ("jets", "IDE Caches", _CATEGORY_COLORS[6]),
    ("spooler", "Print Spooler", _CATEGORY_COLORS[5]),
    ("winsat", "WinSAT Cache", _CATEGORY_COLORS[12]),
    ("etl", "ETL Logs", _CATEGORY_COLORS[9]),
    ("telemetry", "Telemetry Data", _CATEGORY_COLORS[8]),
    ("delivery", "Delivery Optim.", _CATEGORY_COLORS[13]),
    ("clipboard", "Clipboard", _CATEGORY_COLORS[14]),
    ("xbox", "Xbox Cache", _CATEGORY_COLORS[15]),
    ("onedrive", "OneDrive Logs", _CATEGORY_COLORS[16]),
    ("maps", "Maps Cache", _CATEGORY_COLORS[17]),
    ("sticky", "Sticky Notes", _CATEGORY_COLORS[18]),
    ("defender", "Defender History", _CATEGORY_COLORS[19]),
    # Cloud Storage
    ("dropbox", "Dropbox Cache", _CATEGORY_COLORS[20]),
    ("gdrive", "Google Drive", _CATEGORY_COLORS[21]),
    ("mega", "MEGA Cache", _CATEGORY_COLORS[22]),
    ("pcloud", "pCloud Cache", _CATEGORY_COLORS[23]),
    ("icloud", "iCloud Cache", _CATEGORY_COLORS[24]),
    ("box", "Box Cache", _CATEGORY_COLORS[25]),
    # Virtualization
    ("docker", "Docker Desktop", _CATEGORY_COLORS[26]),
    ("vbox", "VirtualBox VMs", _CATEGORY_COLORS[27]),
    ("vmware", "VMware VMs", _CATEGORY_COLORS[23]),
    ("wsl2", "WSL2 Distros", _CATEGORY_COLORS[28]),
    ("hyperv", "Hyper-V VMs", _CATEGORY_COLORS[29]),
    # Media Production
    ("obs", "OBS Cache", _CATEGORY_COLORS[30]),
    ("davinci", "DaVinci Cache", _CATEGORY_COLORS[31]),
    ("premiere", "Premiere Cache", _CATEGORY_COLORS[32]),
    ("blender", "Blender Cache", _CATEGORY_COLORS[33]),
    ("audacity", "Audacity Cache", _CATEGORY_COLORS[34]),
    # Communication
    ("telegram", "Telegram Cache", _CATEGORY_COLORS[23]),
    ("signal", "Signal Cache", _CATEGORY_COLORS[28]),
    ("teams", "Teams Cache", _CATEGORY_COLORS[21]),
    ("slack", "Slack Cache", _CATEGORY_COLORS[22]),
    ("discord", "Discord Cache", _CATEGORY_COLORS[20]),
    # Development
    ("jetbrains", "JetBrains Cache", _CATEGORY_COLORS[35]),
    ("eclipse", "Eclipse Cache", _CATEGORY_COLORS[36]),
    ("gitlfs", "Git LFS Cache", _CATEGORY_COLORS[22]),
    ("npm", "npm Cache", _CATEGORY_COLORS[27]),
    ("pip", "pip Cache", _CATEGORY_COLORS[23]),
    ("nuget", "NuGet Cache", _CATEGORY_COLORS[24]),
    ("vscode", "VSCode Cache", _CATEGORY_COLORS[37]),
    ("unity", "Unity Cache", _CATEGORY_COLORS[26]),
    # Games
    ("epic", "Epic Games Cache", _CATEGORY_COLORS[38]),
    ("battlenet", "Battle.net Cache", _CATEGORY_COLORS[39]),
    ("rockstar", "Rockstar Cache", _CATEGORY_COLORS[40]),
    ("minecraft", "Minecraft Cache", _CATEGORY_COLORS[28]),
    ("lol", "LoL Cache", _CATEGORY_COLORS[35]),
    ("rust", "Rust Game Cache", _CATEGORY_COLORS[41]),
    # More Browsers
    ("brave", "Brave Cache", _CATEGORY_COLORS[35]),
    ("vivaldi", "Vivaldi Cache", _CATEGORY_COLORS[42]),
    ("opera", "Opera Cache", _CATEGORY_COLORS[40]),
    ("yandex", "Yandex Cache", _CATEGORY_COLORS[35]),
    ("edge", "Edge Cache", _CATEGORY_COLORS[39]),
    ("firefox", "Firefox Cache", _CATEGORY_COLORS[41]),
    ("chrome", "Chrome Cache", _CATEGORY_COLORS[43]),
    # System
    ("iis", "IIS Logs", _CATEGORY_COLORS[44]),
    ("dockerimg", "Docker Images", _CATEGORY_COLORS[45]),
    ("vpn", "VPN Cache", _CATEGORY_COLORS[36]),
    ("putty", "PuTTY Cache", _CATEGORY_COLORS[46]),
    ("rdp", "RDP Cache", _CATEGORY_COLORS[23]),
    # PC-Specific (auto-discovered)
    ("vscode_ext", "VSCode Ext VSIXs", _CATEGORY_COLORS[37]),
    ("vscode_dawn", "VSCode Dawn Cache", _CATEGORY_COLORS[17]),
    ("vscode_web", "VSCode WebStorage", _CATEGORY_COLORS[47]),
    ("steam_logs", "Steam Logs", _CATEGORY_COLORS[48]),
    ("steam_web", "Steam WebCache", _CATEGORY_COLORS[49]),
    ("chrome_full", "Chrome Full Cache", _CATEGORY_COLORS[43]),
    ("edge_full", "Edge Full Cache", _CATEGORY_COLORS[39]),
    ("brave_full", "Brave Full Cache", _CATEGORY_COLORS[35]),
    ("uwp_all", "UWP Apps Cache", _CATEGORY_COLORS[20]),
    ("lm_studio", "LM Studio Cache", _CATEGORY_COLORS[28]),
    ("teams_npc", "MS Teams NPC", _CATEGORY_COLORS[21]),
    ("photos_cache", "Windows Photos", _CATEGORY_COLORS[11]),
    ("ms_store", "MS Store Cache", _CATEGORY_COLORS[16]),
    ("discord_logs", "Discord Logs", _CATEGORY_COLORS[42]),
    ("notifications", "Notif. History", _CATEGORY_COLORS[14]),
    ("spotify_app", "Spotify Cache", _CATEGORY_COLORS[50]),
    ("cbs_logs", "CBS Logs", _CATEGORY_COLORS[44]),
    ("panther_logs", "Panther Logs", _CATEGORY_COLORS[12]),
    ("inetcache", "INetCache", _CATEGORY_COLORS[10]),
    ("game_bar", "Game Bar Cache", _CATEGORY_COLORS[15]),
    ("yarn", "Yarn Cache", _CATEGORY_COLORS[27]),
    ("pnpm", "pnpm Cache", _CATEGORY_COLORS[24]),
    # Smart Finders
    ("empty_folders", "Empty Folders", _CATEGORY_COLORS[44]),
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
    ("complus_cache", "COM+ App Cache", _CATEGORY_COLORS[10]),
    ("office_cache_extended", "Office Ext. Cache", _CATEGORY_COLORS[3]),
    ("onedrive_cache", "OneDrive Mask Cache", _CATEGORY_COLORS[8]),
    ("onedrive_commercial_cache", "OneDrive Biz Cache", _CATEGORY_COLORS[11]),
    ("onedrive_full_cache", "OneDrive Full Cache", _CATEGORY_COLORS[6]),
    ("claude_cli_cache", "Claude CLI Cache", _CATEGORY_COLORS[5]),
    ("npp_cache", "Notepad++ Cache", _CATEGORY_COLORS[12]),
    ("vscode_cached_data", "VSCode Cached Data", _CATEGORY_COLORS[9]),
    ("vscode_settings_sync", "VSCode Settings Sync", _CATEGORY_COLORS[13]),
    ("xbox_app_cache", "Xbox App Cache", _CATEGORY_COLORS[14]),
    ("amd_radeon_cache", "AMD Radeon Cache", _CATEGORY_COLORS[15]),
    ("browser_caches", "Browser Cache (bulk)", _CATEGORY_COLORS[17]),
    ("d3d_shader_cache", "Direct3D Shader Cache", _CATEGORY_COLORS[18]),
    ("diag_logs", "Diagnostic Logs", _CATEGORY_COLORS[19]),
    ("directx_shader_cache", "DirectX Shader Cache", _CATEGORY_COLORS[20]),
    ("dxgi_cache", "DXGI Cache", _CATEGORY_COLORS[22]),
    ("event_logs", "Event Log Files", _CATEGORY_COLORS[23]),
    ("font_cache", "Font Cache Service", _CATEGORY_COLORS[24]),
    ("language_packs", "Language Pack Cache", _CATEGORY_COLORS[25]),
    ("memory_dumps", "Memory Dumps", _CATEGORY_COLORS[26]),
    ("minidump", "Kernel Minidumps", _CATEGORY_COLORS[27]),
    ("package_cache", "Installer Payload Cache", _CATEGORY_COLORS[28]),
    ("per_drive_recycle_bin", "Recycle Bin (all drives)", _CATEGORY_COLORS[29]),
    ("per_drive_temp", "Temp (all drives)", _CATEGORY_COLORS[10]),
    ("sfc_logs", "SFC/DISM Logs", _CATEGORY_COLORS[3]),
    ("sysprep_logs", "Sysprep Logs", _CATEGORY_COLORS[8]),
    ("thumbnail_cache_central", "Explorer Thumbcache", _CATEGORY_COLORS[11]),
    ("update_cleanup", "Windows Update Cleanup", _CATEGORY_COLORS[6]),
    ("wer_reports", "Error Reporting Archive", _CATEGORY_COLORS[5]),
    ("windows_backup_catalog", "Windows Backup Catalog", _CATEGORY_COLORS[12]),
    ("windows_backup_logs", "Server Backup Logs", _CATEGORY_COLORS[9]),
    ("windows_setup_diags", "Setup Diag Logs", _CATEGORY_COLORS[13]),
    ("windows_tweaker_logs", "This App's Own Logs", _CATEGORY_COLORS[14]),
    ("windowsupdate_orch_cache", "WU Orchestrator Cache", _CATEGORY_COLORS[15]),
    ("wu_history_cache", "WU Download History", _CATEGORY_COLORS[16]),
    # Confirmed real on THIS machine specifically (cross-referenced against
    # its own installed-software list, not guessed): CurseForge and AMD
    # ReLive's DVR buffer had no catalog entry at all until now.
    ("curseforge_cache", "CurseForge Cache", _CATEGORY_COLORS[35]),
    ("amd_dvr_cache", "AMD ReLive Buffer", _CATEGORY_COLORS[40]),
    # Found via a direct AppData folder census (registry Uninstall keys
    # miss portable/Electron-updater-style installs like these).
    ("cyberghost_cache", "CyberGhost VPN Cache", _CATEGORY_COLORS[51]),
    ("opencode_desktop_cache", "OpenCode Cache", _CATEGORY_COLORS[39]),
    ("amd_comgr_cache", "AMD Compute Cache", _CATEGORY_COLORS[41]),
    ("amd_install_manager_cache", "AMD Installer Cache", _CATEGORY_COLORS[42]),
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
    ("temp", "Temp Files", _CATEGORY_COLORS[0]),
    ("prefetch", "Prefetch", _CATEGORY_COLORS[1]),
    ("thumb", "Thumbnail Cache", _CATEGORY_COLORS[2]),
    ("crash", "Crash Dumps", _CATEGORY_COLORS[3]),
    ("browser", "Browser Caches", _CATEGORY_COLORS[4]),
    ("app", "App Caches", _CATEGORY_COLORS[5]),
    ("logs", "Windows Logs", _CATEGORY_COLORS[6]),
    ("wu", "Windows Update", _CATEGORY_COLORS[7]),
    ("large", "Large Items", _CATEGORY_COLORS[8]),
    ("dev", "Dev Tools", _CATEGORY_COLORS[9]),
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
            "package_cache":             (cs.scan_package_cache,             "caution"),
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

        self._scanner_map = {}
        self._browser_scanner = bs.detect_browsers  # for browser category

        for cid, clabel, ccolor in categories:
            fn_safety = _id_map.get(cid, (None, "safe"))
            self._scanner_map[cid] = (fn_safety[0], clabel, ccolor)

        self._adv_scanner_map = {}
        for cid, clabel, ccolor in advanced_categories:
            fn_safety = _id_map.get(cid, (None, "safe"))
            self._adv_scanner_map[cid] = (fn_safety[0], clabel, ccolor)

        self._id_to_scanner_name = {
            cid: (fn.__name__ if fn else None)
            for cid, (fn, _label, _color) in
            {**self._scanner_map, **self._adv_scanner_map}.items()
        }

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

        from modules.cleanup.cleanup_presets import PRESETS, preset_names
        self._preset_combo = QComboBox()
        self._preset_combo.setToolTip(
            "Which items \"Clean\" includes when you click it -- Light is "
            "the original behavior, Aggressive includes danger-level items "
            "(except orphaned profiles/virtual disks, never included by any preset). "
            "Custom behaves the same as Light here -- this dashboard has no "
            "per-item checkboxes for Custom to mean anything else."
        )
        for pid in preset_names():
            label = PRESETS[pid]["label"] if pid in PRESETS else "Custom"
            self._preset_combo.addItem(label, pid)
        self._preset_combo.setCurrentIndex(self._preset_combo.findData("light"))
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        self._clean_all_btn = QPushButton("🗑️  Clean (Light)")
        self._clean_all_btn.setEnabled(False)
        self._clean_all_btn.clicked.connect(self._do_clean_all_safe)
        self._status_lbl = QLabel("Click Scan All to analyze your system")
        self._status_lbl.setObjectName("muted")
        self._show_adv_btn = QPushButton("Show Advanced ▼")
        self._show_adv_btn.setStyleSheet("font-size: 12px; padding: 4px 10px;")
        self._show_adv_btn.clicked.connect(self._toggle_advanced)
        self._adv_shown = False
        toolbar.addWidget(self._scan_all_btn)
        toolbar.addWidget(self._preset_combo)
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

    def _on_preset_changed(self, _index: int) -> None:
        from modules.cleanup.cleanup_presets import PRESETS
        pid = self._preset_combo.currentData()
        label = PRESETS[pid]["label"] if pid in PRESETS else "Custom"
        self._clean_all_btn.setText(f"🗑️  Clean ({label})")

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
        self._clean_all_btn.setEnabled(self._has_cleanable_items())
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
        _on_all_scanned would never run on its own."""
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
            self._clean_all_btn.setEnabled(self._has_cleanable_items())
        if hasattr(self, "_progress"):
            self._progress.hide()
        if hasattr(self, "_status_lbl") and message:
            self._status_lbl.setText(message)

    def _has_cleanable_items(self) -> bool:
        """Is there at least one item the CURRENT preset would clean?
        Mirrors what _do_clean_all_safe actually does, so a cancelled/
        timed-out scan and a completed one agree on when there is
        genuinely something to clean under whatever preset is selected."""
        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs
        from modules.cleanup import cleanup_presets

        preset_id = self._preset_combo.currentData() if hasattr(self, "_preset_combo") else "light"

        for cid, result in self._results.items():
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = result or []
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

        from modules.cleanup import cleanup_presets
        preset_id = self._preset_combo.currentData()

        for cid in self._results:
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = self._results.get(cid, [])
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                browser_cats.append(cat)
                                total += cat.size_bytes
            elif cid == "wu":
                needs_wu = True

        if preset_id == "custom":
            for cid, result in self._results.items():
                if cid == "browser":
                    continue
                if self._id_to_scanner_name.get(cid) in cleanup_presets.NEVER_INCLUDED:
                    continue
                for item in result.items:
                    if item.safety == "safe":
                        item.selected = True
                        all_safe.append(item)
                        total += item.size
        else:
            selected_items = cleanup_presets.items_for_preset(
                preset_id, {k: v for k, v in self._results.items() if k != "browser"},
                self._id_to_scanner_name)
            for item in selected_items:
                item.selected = True
                all_safe.append(item)
                total += item.size

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
        self._clean_all_btn.setEnabled(False)
        self._progress.setText("🗑️  Cleaning safe items...")
        self._progress.show()
        self._workers.append(worker)
