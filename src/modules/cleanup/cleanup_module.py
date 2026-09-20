"""
Cleanup module — one scrollable page.

Quick Cleanup's dashboard (pie chart, totals, Scan All / Clean All Safe) is
an always-visible header; System Junk, Browser Caches, App & Game Caches,
Windows Update, Logs & Reports, Large Items and Dev Tools are collapsible
sections below it (see docs/superpowers/specs/
2026-09-20-cleanup-single-tab-merge-design.md — merged from the former
8-tab QTabWidget layout).

Cross-cutting: auto-scan on first section expand, safety colour-coding,
age filter per section, running-process guard, >500 MB confirmation,
error panel, freed-session counter, DISM button on Large Items.
"""
import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.collapsible_section import _CollapsibleSection
from modules.cleanup.tabs import (
    _ScanTab,
    _BrowserCleanupTab,
    _LargeItemsTab,
)
from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab, ADVANCED_CATEGORIES

logger = logging.getLogger(__name__)


# Alias LARGE_SCANNERS so the main module still has access for reference


# ── The scanners no tab named ──────────────────────────────────────────
#
# 404 of 537 scanners were unreachable: defined, loaded, exported, and
# offered by nothing. The catalog-backed ones are now surfaced by
# `_with_catalog` below; these are the hand-written remainder — the ones
# that walk a tree, read the registry or shell out, which the conversion
# to data deliberately left as code.
#
# Each label and safety level is read from the scanner's own
# `_make_item(safety=...)`, not invented here, so nothing becomes more
# deletable than its author made it. Where a scanner uses more than one
# level, the most cautious wins: this tuple is what gates "Clean All Safe".
#
# The split is by what the scanner RETURNS, not by where it looks.
# scan_large_files finds 42 GB of the user's own documents on this
# machine — it belongs on Large Items with the other user-data scanners,
# never on a cache tab.

SYSTEM_EXTRA = {
    # camelCase, so every `scan_[a-z0-9_]+` sweep missed it.
    cs.scan_winSxS_temp: ('WinSxS Temp', 'caution'),
    cs.scan_bits_transfers: ('Bits Transfers', 'safe'),
    cs.scan_brackets_cache: ('Brackets Cache', 'safe'),
    cs.scan_crash_dumps_system: ('Crash Dumps System', 'caution'),
    cs.scan_delivery_optimization_do: ('Delivery Optimization Do', 'safe'),
    cs.scan_dns_cache: ('Dns Cache', 'caution'),
    cs.scan_font_files_temp: ('Font Files Temp', 'caution'),
    cs.scan_install_temp: ('Install Temp', 'caution'),
    cs.scan_maps_offline_cache: ('Maps Offline Cache', 'safe'),
    cs.scan_msp_patches: ('Msp Patches', 'caution'),
    cs.scan_ndis_cache: ('Ndis Cache', 'safe'),
    cs.scan_novatrons_cache: ('Novatrons Cache', 'safe'),
    cs.scan_old_av_quarantine: ('Old Av Quarantine', 'caution'),
    cs.scan_powershell_ise_cache: ('Powershell Ise Cache', 'safe'),
    cs.scan_powershell_modules_cache: ('Powershell Modules Cache', 'safe'),
    cs.scan_printer_driver_cache: ('Printer Driver Cache', 'caution'),
    cs.scan_search_index: ('Search Index', 'caution'),
    cs.scan_orphaned_installer_packages: (
        'Orphaned Installer Packages', 'caution'),
    cs.scan_triumph_cache: ('Triumph Cache', 'safe'),
    cs.scan_userprofile_temp: ('Userprofile Temp', 'safe'),
    cs.scan_winSxS_temp: ('Winsxs Temp', 'caution'),
    cs.scan_windows_app_extensions_cache: ('Windows App Extensions Cache', 'safe'),
    cs.scan_windows_compatibility_cache: ('Windows Compatibility Cache', 'safe'),
    cs.scan_windows_connected_accounts_cache: ('Windows Connected Accounts Cache', 'safe'),
    cs.scan_windows_inbox_apps_cache: ('Windows Inbox Apps Cache', 'safe'),
    cs.scan_windows_insider_preview_cache: ('Windows Insider Preview Cache', 'caution'),
    cs.scan_windows_installer_cache: ('Windows Installer Cache', 'caution'),
    cs.scan_windows_installer_rollback: ('Windows Installer Rollback', 'caution'),
    cs.scan_windows_optional_features: ('Windows Optional Features', 'caution'),
    cs.scan_windows_printer_migration_cache: ('Windows Printer Migration Cache', 'caution'),
    cs.scan_windows_recovery_env_cache: ('Windows Recovery Env Cache', 'safe'),
    cs.scan_windows_shell_cache: ('Windows Shell Cache', 'safe'),
    cs.scan_windows_terminal_cache: ('Windows Terminal Cache', 'safe'),
    cs.scan_windows_terminal_settings_cache: ('Windows Terminal Settings Cache', 'safe'),
    # No scan_winsxs_cleanup here, deliberately: it ran
    # `Dism /Online /Cleanup-Image /AnalyzeComponentStore`, which is 25-30s
    # elevated (0.03s unelevated, because DISM refuses — which is how it hid)
    # and takes the Windows servicing lock. Large Items has an explicit
    # "Analyze WinSxS" button for that, on its own long-operation pool.
}

LOGS_EXTRA = {
    cs.scan_appx_logs: ('Appx Logs', 'caution'),
    cs.scan_bitlocker_logs: ('Bitlocker Logs', 'safe'),
    cs.scan_dbg_logs: ('Dbg Logs', 'safe'),
    cs.scan_diagnostic_data: ('Diagnostic Data', 'caution'),
    cs.scan_network_debug_logs: ('Network Debug Logs', 'safe'),
    cs.scan_perflogs: ('Perflogs', 'safe'),
    cs.scan_print_nightmare_logs: ('Print Nightmare Logs', 'caution'),
    cs.scan_sysinternals_logs: ('Sysinternals Logs', 'safe'),
    cs.scan_windows_defender_logs: ('Windows Defender Logs', 'safe'),
    cs.scan_windows_insider_logs: ('Windows Insider Logs', 'caution'),
    cs.scan_windows_reliability_logs: ('Windows Reliability Logs', 'safe'),
}

LARGE_EXTRA = {
    # No scan_old_restore_points: it claimed to measure System Restore
    # snapshots and shadow storage, and actually scanned the Win+X
    # shortcuts folder. Restore points are the Restore Manager module's.
    #
    # No scan_recycle_bin_drive either: it was byte-identical to
    # scan_recycle_bin, so the same bytes were reported under two names.
    # The catalog's per_drive_recycle_bin covers both, on every fixed
    # drive rather than just the ones os.path.exists happened to find.
    cs.scan_backup_files: ('Backup Files', 'caution'),
    cs.scan_downloads_folder_old: ('Downloads Folder Old', 'caution'),
    cs.scan_duplicate_files: ('Duplicate Files', 'caution'),
    cs.scan_iso_vhd_files: ('Iso Vhd Files', 'caution'),
    cs.scan_large_files: ('Large Files', 'caution'),
    # Reported, never pre-selected: deleting one destroys a VM. The way
    # to reclaim this space is to COMPACT the image.
    cs.scan_virtual_disk_images: ('Virtual Disk Images', 'danger'),
    # Nothing installed here can open these, so there is no VM to
    # destroy and nothing to compact them with. Still not pre-selected.
    cs.scan_orphaned_virtual_disks: (
        'Orphaned Virtual Disks (no hypervisor installed)', 'caution'),
    cs.scan_old_files: ('Old Files', 'caution'),
    cs.scan_usb_shadow_copies: ('Usb Shadow Copies', 'safe'),
    cs.scan_virtual_drives: ('Virtual Drives', 'caution'),
    cs.scan_orphaned_user_profiles: (
        'Orphaned User Profiles (no matching Windows account)', 'danger'),
}


# Quick Cleanup's 10 main category ids -> the tab name each one's own
# deep-dive lives on. Only the main categories map cleanly onto one tab
# each; the ~90 advanced categories don't (see quick_cleanup_tab.py's
# own _handle_category_clicked wiring, which only connects these 10).
_CATEGORY_TAB_NAMES = {
    "temp": "System Junk", "prefetch": "System Junk",
    "thumb": "System Junk", "crash": "System Junk",
    "browser": "Browser Caches",
    "app": "App & Game Caches",
    "logs": "Logs & Reports",
    "wu": "Windows Update",
    "large": "Large Items",
    "dev": "Dev Tools",
}


def _with_catalog(curated: dict, *categories: str) -> dict:
    """The hand-picked scanners, then everything else in `categories`.

    Curated entries come FIRST and win on collision: they carry labels and
    safety levels someone chose ("GPU Shader Cache" reads better than the
    catalog's derived label), and dedupe_items keeps the first occurrence
    of a path, so ordering is what preserves them.

    Everything after them is the rest of the catalog. 404 of 537 scanners
    were unreachable before this — defined, loaded, exported, and offered
    by no tab — which is why a glob bug could stop 62 of them matching
    anything without anyone noticing.
    """
    from modules.cleanup.cleanup_scanner.catalog import scanners_for

    merged = dict(curated)
    skipped = 0
    for category in categories:
        offered = scanners_for(category, present_only=True)
        skipped += len(scanners_for(category)) - len(offered)
        for fn, meta in offered.items():
            names = {f.__name__ for f in merged}
            if fn.__name__ not in names:
                merged[fn] = meta
    if skipped:
        # Not a warning: "Steam is not installed" is the normal, correct
        # answer for most of this catalog. Logged so the number is visible
        # when someone wonders where the other 289 rows went.
        logger.info("Cleanup tab (%s): offering %d scanners, %d skipped as "
                    "not present on this machine",
                    ", ".join(categories), len(merged), skipped)
    return merged


class CleanupModule(BaseModule):
    name = "Cleanup"
    icon = "🗑️"
    description = "Scan and remove junk files, caches, logs, and more"
    requires_admin = True
    group = ModuleGroup.OPTIMIZE

    def create_widget(self) -> QWidget:
        outer = QWidget()
        main_lay = QVBoxLayout(outer)
        main_lay.setContentsMargins(4, 4, 4, 4)
        main_lay.setSpacing(4)

        # ── Module-level toolbar ──
        header = QHBoxLayout()
        self._freed_lbl = QLabel("Freed this session: 0 B")
        self._freed_lbl.setStyleSheet("color: #4caf50; font-weight: bold; padding: 2px 6px;")
        self._freed_bytes = 0
        header.addStretch()
        header.addWidget(self._freed_lbl)
        main_lay.addLayout(header)

        # ── Scrollable page: Quick Cleanup header + collapsible sections ──
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        self._scroll_area.setWidget(page)
        main_lay.addWidget(self._scroll_area, 1)

        # Quick Cleanup's dashboard is the page header, always visible --
        # it already scans everything and shows the pie chart + totals, so
        # it is not wrapped in a section (see docs/superpowers/specs/
        # 2026-09-20-cleanup-single-tab-merge-design.md).
        self._quick = QuickCleanupTab(on_category_clicked=self._on_category_clicked)
        self._quick.build(advanced_categories=ADVANCED_CATEGORIES)
        page_lay.addWidget(self._quick)

        self._sections: dict = {}

        # System Junk
        sys_scanners = {
            cs.scan_temp_files:       ("Temp Files",       "safe"),
            cs.scan_prefetch:         ("Prefetch",          "caution"),
            cs.scan_thumbnail_cache:  ("Thumbnail Cache",   "safe"),
            cs.scan_user_crash_dumps: ("User Crash Dumps",  "caution"),
        }
        self._sys_tab = _ScanTab(
            _with_catalog({**sys_scanners, **SYSTEM_EXTRA}, "system"))
        self._add_section(page_lay, "System Junk", self._sys_tab)

        # Browser Caches
        self._browser = _BrowserCleanupTab()
        self._add_section(page_lay, "Browser Caches", self._browser)

        # App & Game Caches
        app_scanners = {
            cs.scan_app_caches:           ("App Caches",             "safe"),
            cs.scan_store_app_caches:     ("Store / UWP Caches",     "safe"),
            cs.scan_d3d_shader_cache:     ("GPU Shader Cache",        "safe"),
            cs.scan_appdata_autodiscover: ("Auto-discovered Caches",  "caution"),
            cs.scan_steam_cache:          ("Steam Cache",             "safe"),
            cs.scan_stremio_cache:        ("Stremio Cache",           "safe"),
            cs.scan_outlook_cache:        ("Outlook Cache",           "safe"),
            cs.scan_winget_packages:      ("WinGet Packages",         "safe"),
        }
        # "browsers" is here rather than on the Browser Caches section: that
        # section runs EnhancedBrowserScanner, which enumerates live profiles
        # and takes no scanner dict at all, so the catalog's per-browser
        # path scanners had nowhere to appear. Overlap between the two is
        # handled by dedupe_items, which counts a path once.
        self._app_tab = _ScanTab(_with_catalog(
            app_scanners, "apps", "games", "media", "comms", "cloud",
            "browsers"))
        self._add_section(page_lay, "App & Game Caches", self._app_tab)

        # Windows Update
        wu_scanners = {
            cs.scan_wu_cache:              ("WU Download Cache",   "caution"),
            cs.scan_delivery_optimization: ("Delivery Opt. Cache", "safe"),
        }
        self._wu_tab = _ScanTab(wu_scanners, wu_cache=True)
        self._add_section(page_lay, "Windows Update", self._wu_tab)

        # Logs & Reports
        log_scanners = {
            cs.scan_windows_logs:     ("Windows Logs",      "caution"),
            cs.scan_event_logs:       ("Event Log Files",   "caution"),
            cs.scan_wer_reports:      ("WER Crash Reports", "caution"),
            cs.scan_memory_dumps:     ("Memory Dumps",      "caution"),
            cs.scan_panther_logs:     ("Panther Logs",       "caution"),
            cs.scan_dmf_logs:         ("DMF Logs",           "caution"),
            cs.scan_onedrive_logs:    ("OneDrive Logs",      "safe"),
            cs.scan_defender_history: ("Defender History",   "safe"),
        }
        self._logs_tab = _ScanTab({**log_scanners, **LOGS_EXTRA})
        self._add_section(page_lay, "Logs & Reports", self._logs_tab)

        # Large Items + DISM
        self._large = _LargeItemsTab()
        self._add_section(page_lay, "Large Items", self._large)

        # Dev Tools
        dev_scanners = {
            cs.scan_dev_tool_caches: ("Dev Tool Caches", "safe"),
        }
        self._dev_tab = _ScanTab(_with_catalog(dev_scanners, "dev"))
        self._add_section(page_lay, "Dev Tools", self._dev_tab)

        page_lay.addStretch()

        # ── Wire signals ──
        for tab in (
            self._quick, self._sys_tab, self._browser, self._app_tab,
            self._wu_tab, self._logs_tab, self._large, self._dev_tab,
        ):
            tab.freed_bytes.connect(self._on_freed)

        return outer

    def _add_section(self, page_lay, title: str, body: QWidget) -> None:
        section = _CollapsibleSection(title, body)
        if hasattr(body, "auto_scan"):
            section.expanded.connect(body.auto_scan)
        self._sections[title] = section
        page_lay.addWidget(section)

    # ── Freed-session counter ──

    def _on_freed(self, nbytes: int):
        self._freed_bytes += nbytes
        self._freed_lbl.setText(f"Freed this session: {cs.format_size(self._freed_bytes)}")

    def _on_category_clicked(self, category_id: str) -> None:
        section_name = _CATEGORY_TAB_NAMES.get(category_id)
        if section_name is None:
            return
        section = self._sections.get(section_name)
        if section is None:
            return
        section.set_expanded(True)
        self._scroll_area.ensureWidgetVisible(section)

    # ── BaseModule lifecycle ──

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self._cancel_all_tabs()
        self.cancel_all_workers()

    def on_activate(self) -> None:
        """Auto-scan the Quick Cleanup tab when the module is first opened."""
        if getattr(self, "_quick", None) is None:
            return
        self._quick.auto_scan()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        """Quick Cleanup's dashboard is always on screen now (the page
        header, not a tab that can be navigated away from), so its 60s
        auto-refresh is unconditional. The seven collapsible sections are
        unaffected -- nothing about them was ever on this timer."""
        if getattr(self, "_quick", None) is None:
            return
        self._quick.scan()

    def on_deactivate(self) -> None:
        self._cancel_all_tabs()

    def _cancel_all_tabs(self) -> None:
        # Every one of these is created in create_widget(), and on_stop() runs
        # even for a module whose widget was never built.
        for name in (
            "_quick", "_sys_tab", "_browser", "_app_tab",
            "_wu_tab", "_logs_tab", "_large", "_dev_tab",
        ):
            tab = getattr(self, name, None)
            if hasattr(tab, "_cancel_all"):
                tab._cancel_all()
            elif hasattr(tab, "cancel"):
                tab.cancel()

    def get_status_info(self) -> str:
        return "Cleanup"
