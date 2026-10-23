"""
Cleanup module — 11 tabs, including explicit duplicate discovery.

Tabs: Quick Cleanup · System Junk · Browser Caches · App & Game Caches ·
      Windows Update · Logs & Reports · Large Items · Dev Tools · Discover · Duplicates · History

Cross-cutting: auto-scan on first tab switch, safety colour-coding,
age filter per tab, running-process guard, >500 MB confirmation,
error panel, freed-session counter, DISM button on Large Items.
"""
import logging
import ntpath
import os
from dataclasses import replace
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QShortcut, QKeySequence, QActionGroup
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QDialog, QListWidget, QPushButton, QFileDialog, QMessageBox,
    QMenu, QLineEdit,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.widget_life import widget_is_valid as _alive
from core.table_ui import set_role
from modules.cleanup import cleanup_history
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.cleanup_scanner import exclusions
from modules.cleanup.cleanup_scanner import recommend
from modules.cleanup.cleanup_scanner.knowledge import id_for_scanner
from modules.cleanup.components.item_rows import FACTS_ROLE, RowFacts
from modules.cleanup.tabs import (
    _ScanTab,
    _BrowserCleanupTab,
    _LargeItemsTab,
)
from modules.cleanup.tabs._discover_tab import _DiscoverTab
from modules.cleanup.components.drive_summary_bar import DriveSummaryBar
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
    cs.scan_bits_transfers: ('BITS transfers', 'safe'),
    cs.scan_brackets_cache: ('Brackets Cache', 'safe'),
    cs.scan_crash_dumps_system: ('Crash Dumps System', 'caution'),
    cs.scan_delivery_optimization_do: ('Delivery Optimization cache', 'safe'),
    cs.scan_dns_cache: ('DNS cache', 'caution'),
    cs.scan_font_files_temp: ('Font Files Temp', 'caution'),
    cs.scan_install_temp: ('Install Temp', 'caution'),
    cs.scan_maps_offline_cache: ('Maps Offline Cache', 'safe'),
    cs.scan_ndis_cache: ('NDIS cache', 'safe'),
    cs.scan_novatrons_cache: ('Novatrons Cache', 'safe'),
    cs.scan_old_av_quarantine: ('Old antivirus quarantine', 'caution'),
    cs.scan_powershell_ise_cache: ('PowerShell ISE cache', 'safe'),
    cs.scan_powershell_modules_cache: ('PowerShell command history', 'caution'),
    cs.scan_printer_driver_cache: ('Printer Driver Cache', 'caution'),
    cs.scan_search_index: ('Search Index', 'caution'),
    cs.scan_orphaned_installer_packages: (
        'Orphaned Installer Packages', 'caution'),
    cs.scan_triumph_cache: ('Triumph Cache', 'safe'),
    cs.scan_userprofile_temp: ('User profile temp', 'safe'),
    cs.scan_windows_app_extensions_cache: ('Windows App Extensions Cache', 'safe'),
    cs.scan_windows_compatibility_cache: ('Windows Compatibility Cache', 'safe'),
    cs.scan_windows_inbox_apps_cache: ('Windows Inbox Apps Cache', 'safe'),
    cs.scan_windows_insider_preview_cache: ('Windows Insider Preview Cache', 'caution'),
    cs.scan_windows_installer_cache: ('Windows Installer Cache', 'caution'),
    cs.scan_windows_printer_migration_cache: ('Windows Printer Migration Cache', 'caution'),
    cs.scan_windows_recovery_env_cache: ('Windows Recovery Env Cache', 'safe'),
    cs.scan_windows_shell_cache: ('Windows Shell Cache', 'safe'),
    cs.scan_windows_terminal_cache: ('Windows Terminal Cache', 'safe'),
    # No scan_winsxs_cleanup here, deliberately: it ran
    # `Dism /Online /Cleanup-Image /AnalyzeComponentStore`, which is 25-30s
    # elevated (0.03s unelevated, because DISM refuses — which is how it hid)
    # and takes the Windows servicing lock. Large Items has an explicit
    # "Analyze WinSxS" button for that, on its own long-operation pool.
}

LOGS_EXTRA = {
    cs.scan_appx_logs: ('AppX logs', 'caution'),
    cs.scan_bitlocker_logs: ('BitLocker logs', 'safe'),
    cs.scan_dbg_logs: ('Debug logs', 'safe'),
    cs.scan_diagnostic_data: ('Diagnostic Data', 'caution'),
    cs.scan_network_debug_logs: ('Network Debug Logs', 'safe'),
    cs.scan_perflogs: ('Performance diagnostic logs', 'caution'),
    cs.scan_print_nightmare_logs: ('Print Nightmare Logs', 'caution'),
    cs.scan_sysinternals_logs: ('Sysinternals Logs', 'safe'),
    cs.scan_windows_defender_logs: ('Windows Defender Logs', 'safe'),
    cs.scan_windows_insider_logs: ('Windows Insider Logs', 'caution'),
    cs.scan_windows_reliability_logs: ('Windows Reliability Logs', 'safe'),
}

LARGE_EXTRA = {
    cs.scan_superseded_app_versions: ('Superseded App Versions', 'caution'),
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
    cs.scan_iso_vhd_files: ('ISO / VHD files', 'caution'),
    cs.scan_large_files: ('Large Files', 'caution'),
    # Reported, never pre-selected: deleting one destroys a VM. The way
    # to reclaim this space is to COMPACT the image.
    cs.scan_virtual_disk_images: ('Virtual Disk Images', 'danger'),
    # Nothing installed here can open these, so there is no VM to
    # destroy and nothing to compact them with. Still not pre-selected.
    cs.scan_orphaned_virtual_disks: (
        'Orphaned Virtual Disks (no hypervisor installed)', 'caution'),
    cs.scan_old_files: ('Old Files', 'caution'),
    cs.scan_usb_shadow_copies: ('USB shadow copies', 'safe'),
    cs.scan_virtual_drives: ('Virtual Drives', 'caution'),
    cs.scan_orphaned_user_profiles: (
        'Orphaned User Profiles (no matching Windows account)', 'danger'),
}


# Quick Cleanup's 10 main category ids -> the tab name each one's own
# deep-dive lives on. Only the main categories map cleanly onto one tab
# each; the ~90 advanced categories don't (see quick_cleanup_tab.py's
# own _handle_category_clicked wiring, which only connects these 10).
_CATEGORY_TAB_KEYS = {
    "temp": recommend.TAB_SYSTEM, "prefetch": recommend.TAB_SYSTEM,
    "thumb": recommend.TAB_SYSTEM, "crash": recommend.TAB_SYSTEM,
    "browser": recommend.TAB_BROWSER, "app": recommend.TAB_APPS,
    "logs": recommend.TAB_LOGS, "wu": recommend.TAB_WU,
    "large": recommend.TAB_LARGE, "dev": recommend.TAB_DEV,
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
    return _short_labels(merged)


def _short_labels(scanners):
    """Use catalog wording without relaxing any curated safety level."""
    from modules.cleanup.cleanup_scanner.catalog import load_catalog
    catalog = load_catalog()
    out = {}
    for fn, (label, safety) in scanners.items():
        spec = getattr(fn, 'spec', None) or catalog.get(id_for_scanner(fn))
        rank = {'safe': 0, 'caution': 1, 'danger': 2}
        safety = max((safety, getattr(spec, 'safety', safety)), key=lambda level: rank.get(level, 2))
        out[fn] = (getattr(spec, 'short_label', '') or label, safety)
    return out


class _CleanupExclusionsDialog(QDialog):
    """Add/remove paths the user never wants offered by any scan tab again.

    A QListWidget over a small
    JSON file under the app data dir) but editable, since an exclusion is
    a rule the user actively maintains rather than a read-only log.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cleanup Exclusions")
        self.resize(480, 380)
        root = QVBoxLayout(self)
        from ui.module_intro import module_intro
        root.addWidget(module_intro("Manage paths excluded from Cleanup scans and removal. Adding or removing a rule changes future Cleanup coverage immediately, without deleting files; the opposite action reverses the rule. Administrator rights are not needed for your local exclusion list."))

        info = QLabel(
            "Paths listed here (and everything inside them) are never "
            "shown or deleted by any Cleanup tab, regardless of which "
            "scanner would otherwise find them.")
        info.setWordWrap(True)
        set_role(info, "muted")
        root.addWidget(info)

        self._list = QListWidget()
        root.addWidget(self._list, 1)
        self._reload()

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Folder…")
        add_btn.setToolTip("Choose a folder and immediately exclude it and its contents from future Cleanup scans. No files are deleted; Remove Selected reverses this rule.")
        add_btn.clicked.connect(self._add)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.setToolTip("Remove the selected exclusion rule immediately. Future scans may offer this path again; this does not delete its files and Add Folder can exclude it again.")
        remove_btn.clicked.connect(self._remove_selected)
        close_btn = QPushButton("Close")
        close_btn.setToolTip("Close the exclusions window; rule changes already made remain saved.")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _reload(self) -> None:
        self._list.clear()
        rules = exclusions.list_exclusions()
        if not rules:
            self._list.addItem("(no exclusions -- every scanner is unfiltered)")
            return
        for path in rules:
            self._list.addItem(path)

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to exclude")
        if not folder:
            return
        if exclusions.add_exclusion(folder):
            self._reload()
        else:
            QMessageBox.information(self, "Already Excluded",
                                     f"“{folder}” is already excluded.")

    def _remove_selected(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        path = item.text()
        if exclusions.remove_exclusion(path):
            self._reload()


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

        exclusions_btn = self._build_header(main_lay)

        # ── Tabs ──
        self._tabs = QTabWidget()
        main_lay.addWidget(self._tabs, 1)

        self._build_tabs()

        self._tab_widgets = {
            recommend.TAB_OVERVIEW: self._quick, recommend.TAB_SYSTEM: self._sys_tab,
            recommend.TAB_BROWSER: self._browser, recommend.TAB_APPS: self._app_tab,
            recommend.TAB_WU: self._wu_tab, recommend.TAB_LOGS: self._logs_tab,
            recommend.TAB_LARGE: self._large, recommend.TAB_DEV: self._dev_tab,
            recommend.TAB_DISCOVER: self._discover,
        }
        self._configure_overview()
        self._setup_shortcuts(outer)
        self._configure_tab_navigation(exclusions_btn)

        self._wire_tab_signals()

        from modules.cleanup.help_ui import explain_panes
        from .help_text import CONTROLS, HEADERS, FIELDS, PANES
        explain_panes(outer, CONTROLS, HEADERS, FIELDS, PANES)
        return outer

    def _configure_overview(self):
        from modules.cleanup.cleanup_presets import PRESETS, preset_names
        from modules.cleanup.components.recommendations_panel import RecommendationsPanel
        previous = self._quick._reco
        if previous is not None:
            self._quick._reco_card.layout().removeWidget(previous)
            previous.cancel()
            previous.deleteLater()
        self._recommendations = RecommendationsPanel(
            self._quick._reco_card, config=getattr(getattr(self, 'app', None), 'config', None))
        self._quick._reco = self._recommendations
        self._quick._reco_card.layout().addWidget(self._recommendations)
        self._recommendations.navigate.connect(self._navigate_recommendation)
        self._quick.navigate.connect(self._navigate_recommendation)
        self._quick.snapshot_ready.connect(self._on_snapshot_ready)
        menu = QMenu(self._clean_now_btn)
        actions = QActionGroup(menu)
        actions.setExclusive(True)
        for preset in preset_names():
            caption = PRESETS[preset]['label'] if preset in PRESETS else 'Custom'
            action = menu.addAction(caption)
            action.setCheckable(True)
            action.setData(preset)
            action.setToolTip(f"Use {caption}; review its included locations before cleaning.")
            actions.addAction(action)
            action.triggered.connect(lambda checked=False, p=preset: self._quick._preset_combo.setCurrentIndex(
                self._quick._preset_combo.findData(p)))
        # QPushButton's attached menu owns its click. A separate Run action keeps
        # both the primary action and preset selection keyboard accessible.
        menu.addSeparator()
        run = menu.addAction('Clean with this preset…')
        run.setToolTip(self._clean_now_btn.toolTip())
        run.triggered.connect(self._clean_now)
        self._preset_actions = actions
        self._clean_now_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._clean_now_btn.customContextMenuRequested.connect(
            lambda pos: menu.exec(self._clean_now_btn.mapToGlobal(pos)))
        # A menu button beside the primary keeps ordinary clicks running Clean Now.
        from PyQt6.QtWidgets import QToolButton
        self._preset_menu_btn = QToolButton(self._clean_now_btn.parentWidget())
        self._preset_menu_btn.setText('▾')
        self._preset_menu_btn.setAccessibleName('Choose cleanup preset')
        self._preset_menu_btn.setToolTip('Choose the cleanup preset; changing it does not clean anything.')
        self._preset_menu_btn.setMenu(menu)
        self._preset_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._clean_now_btn.parentWidget().layout().itemAt(0).layout().insertWidget(1, self._preset_menu_btn)
        self._quick._preset_combo.currentIndexChanged.connect(self._sync_preset)
        self._sync_preset()
        for key, widget in self._tab_widgets.items():
            scan = getattr(widget, '_scan_tab', widget)
            if isinstance(scan, _ScanTab):
                scan.also_on_provider = lambda path, k=key: self._also_on(path, k)

    def _sync_preset(self, *_):
        combo = self._quick._preset_combo
        label = combo.currentText()
        # The combo's display labels may include an explanation after a dash.
        from modules.cleanup.cleanup_presets import PRESETS
        pid = combo.currentData()
        label = PRESETS.get(pid, {}).get('label', label)
        self._clean_now_btn.setText(f"Clean safe items ({label})")
        self._clean_now_btn.setAccessibleName(self._clean_now_btn.text())
        for action in self._preset_actions.actions():
            action.setChecked(action.data() == pid)

    def _on_snapshot_ready(self, snapshot):
        if not _alive(self._tabs):
            return
        from core.admin_utils import is_admin
        self._snapshot = replace(snapshot, elevated=is_admin())
        self._remember_memberships(self._snapshot)
        self._snapshot.append_summary()
        self._export_btn.setEnabled(True)
        # QuickCleanupTab also refreshes on a visible scan. Its pending flag
        # coalesces this queued refresh, and ensures hidden scans refresh too.
        generation = self._navigation_generation
        QTimer.singleShot(0, lambda: self._quick._refresh_recommendations()
                          if _alive(self._quick) and generation == self._navigation_generation else None)

    def _remember_memberships(self, snapshot):
        memberships = {}
        for row in snapshot.rows:
            key = ntpath.normcase(ntpath.normpath(row.path))
            tabs = memberships.setdefault(key, [])
            if row.tab not in tabs:
                tabs.append(row.tab)
        self._path_tabs = memberships

    def _also_on(self, path, current_key):
        keys = self._path_tabs.get(ntpath.normcase(ntpath.normpath(path)), ())
        return tuple(self._tabs.tabText(self._tabs.indexOf(self._tab_widgets[key])).replace('&&', '&')
                     for key in keys
                     if key != current_key and key in self._tab_widgets)

    def _current_snapshot(self):
        """Combine existing rows only; totals remain path-deduplicated by WP8."""
        from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot, SnapshotRow
        rows = list(self._snapshot.rows) if self._snapshot is not None else []
        for key, widget in getattr(self, '_tab_widgets', {}).items():
            tab = getattr(widget, '_scan_tab', widget)
            if not isinstance(tab, _ScanTab) or not tab._scanned or tab._scanning:
                continue
            results = {}
            for i in range(tab._tree.topLevelItemCount()):
                for node, _ in tab._location_rows(tab._tree.topLevelItem(i)):
                    facts = node.data(0, FACTS_ROLE)
                    item = node.data(0, Qt.ItemDataRole.UserRole)
                    if item is not None and isinstance(facts, RowFacts):
                        result = results.setdefault(facts.catalog_id, cs.ScanResult())
                        result.items.append(replace(item, selected=node.checkState(0) == Qt.CheckState.Checked))
            rows = [row for row in rows if row.tab != key]
            rows.extend(ScanSnapshot.from_results({key: results}).rows)
        if self._browser._scanned and not self._browser._scanning:
            rows = [row for row in rows if row.tab != recommend.TAB_BROWSER]
            def browser_rows(node):
                facts = node.data(0, FACTS_ROLE)
                if isinstance(facts, RowFacts) and facts.path:
                    rows.append(SnapshotRow(
                        facts.catalog_id, facts.label, recommend.TAB_BROWSER, 'browsers',
                        facts.catalog_safety, facts.safety, facts.path, facts.size_bytes,
                        node.checkState(0) == Qt.CheckState.Checked, facts.is_dir,
                        facts.keep_root, facts.keep_newer_than_hours, facts.description))
                for i in range(node.childCount()):
                    browser_rows(node.child(i))
            for i in range(self._browser._tree.topLevelItemCount()):
                browser_rows(self._browser._tree.topLevelItem(i))
        if not rows and self._snapshot is None:
            return None
        if self._snapshot is not None:
            return replace(self._snapshot, rows=tuple(rows), preset=str(self._quick._preset_combo.currentData()))
        return ScanSnapshot(rows=tuple(rows), preset=str(self._quick._preset_combo.currentData()))

    def _export_report(self):
        from modules.cleanup import cleanup_reports
        snapshot = self._current_snapshot()
        if snapshot is None:
            QMessageBox.information(self._tabs, 'Scan first', 'Run a scan before exporting its report.')
            return
        try:
            not_offered = getattr(self._quick, '_not_offered', None)
            options = {}
            if not_offered is not None:
                options['not_offered'] = [{'label': label, 'reason': reason}
                                          for label, reason in not_offered.reasons]
            path, _csv_path = cleanup_reports.export_scan(
                snapshot, recommendations=self._recommendations.recommendations(), **options)
            os.startfile(str(path))
        except (OSError, ValueError) as exc:
            logger.warning('Cannot export Cleanup report: %s', exc)
            QMessageBox.warning(self._tabs, 'Export report', f'Could not save or open the report: {exc}')

    def _navigate_recommendation(self, tab_key, ids, age_days):
        target = self._tab_widgets.get(tab_key)
        if target is None:
            return
        self._navigation_generation += 1
        generation = self._navigation_generation
        scan = getattr(target, '_scan_tab', target)
        age_changed = isinstance(scan, _ScanTab) and scan._age_spin.value() != age_days
        if age_changed:
            scan._cancel_all()
            scan._age_spin.setValue(age_days)
        self._tabs.setCurrentWidget(target)
        if isinstance(scan, _ScanTab):
            if age_changed and not scan._scanning:
                scan._do_scan()
            def select(*_):
                if _alive(scan) and generation == self._navigation_generation:
                    self._select_recommendation_rows(scan, ids)
            if scan._scan_worker is not None:
                scan._scan_worker.signals.result.connect(select)
            else:
                select()
        elif target is self._quick:
            self._quick._on_reco_navigate(tab_key, ids, age_days)
            for group in self._quick._all_groups():
                tree = group._tree
                wanted = group.catalog_id() in set(ids)
                for i in range(tree.topLevelItemCount()):
                    root = tree.topLevelItem(i)
                    for j in range(root.childCount()):
                        child = root.child(j)
                        item = child.data(0, Qt.ItemDataRole.UserRole)
                        tick = wanted and item is not None and not recommend.is_protected(item.path)
                        child.setCheckState(0, Qt.CheckState.Checked if tick else Qt.CheckState.Unchecked)
        elif target is self._browser:
            def select_browser(*_):
                if _alive(target) and generation == self._navigation_generation:
                    self._select_recommendation_rows(target, ids)
            if target._scan_worker is not None:
                target._scan_worker.signals.result.connect(select_browser)
            else:
                select_browser()

    @staticmethod
    def _select_recommendation_rows(tab, ids):
        wanted = set(ids)
        tree = getattr(tab, '_tree', None)
        if tree is None:
            return
        if hasattr(tab, '_filter'):
            tab._filter.clear()
        if isinstance(tab, _ScanTab):
            tab._set_chip('all')
        def visit(node):
            facts = node.data(0, FACTS_ROLE)
            hit = isinstance(facts, RowFacts) and (facts.catalog_id in wanted or
                  (isinstance(tab, _BrowserCleanupTab) and 'browser_caches' in wanted))
            if isinstance(facts, RowFacts) and facts.path:
                hit = (hit and not recommend.is_protected(facts.path)
                       and bool(node.flags() & Qt.ItemFlag.ItemIsEnabled))
                node.setCheckState(0, Qt.CheckState.Checked if hit else Qt.CheckState.Unchecked)
            children = [visit(node.child(i)) for i in range(node.childCount())]
            visible = hit or any(children)
            node.setHidden(not visible)
            if visible:
                node.setExpanded(True)
            return visible
        for i in range(tree.topLevelItemCount()):
            visit(tree.topLevelItem(i))

    def _setup_shortcuts(self, outer):
        # Area tabs own their editing shortcuts. Only supply the missing keys
        # on other pages, avoiding ambiguous duplicate shortcuts in Qt.
        self._shortcuts = []
        def add(key, callback, parent=outer):
            shortcut = QShortcut(QKeySequence(key), parent)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        add('Ctrl+E', self._export_report)
        for key, target in (('Alt+1', self._quick), ('Alt+2', self._sys_tab), ('Alt+3', self._discover)):
            add(key, lambda t=target: self._tabs.setCurrentWidget(t))
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            scan = getattr(tab, '_scan_tab', tab)
            if isinstance(scan, _ScanTab):
                continue
            existing = {s.key().toString() for s in tab.findChildren(QShortcut)}
            if 'F5' not in existing:
                callback = next((getattr(tab, n) for n in ('scan', '_do_scan', '_scan', 'refresh') if hasattr(tab, n)), None)
                if callback:
                    add('F5', callback, tab)
            if 'Ctrl+F' not in existing:
                add('Ctrl+F', lambda t=tab: self._focus_filter(t), tab)

    @staticmethod
    def _focus_filter(tab):
        fields = tab.findChildren(QLineEdit)
        if fields:
            fields[0].setFocus()
            fields[0].selectAll()

    # ── Freed-session counter ──

    def _on_duplicates_freed(self, nbytes: int):
        self._on_freed(nbytes, record_history=False)

    def _on_freed(self, nbytes: int, record_history: bool = True):
        if not _alive(self._tabs):
            return
        self._freed_bytes += nbytes
        self._freed_lbl.setText(f"{cs.format_size(self._freed_bytes)} session")
        if record_history:
            cleanup_history.record(nbytes)
        self._all_time_lbl.setText(
            f"{cs.format_size(cleanup_history.total_freed_all_time())} all time")
        self._history.refresh()
        self._summary.refresh()

    def _clean_now(self) -> None:
        """Switch to Quick Cleanup and run its Clean All Safe action --
        the confirmation dialog and the actual deletion are entirely
        QuickCleanupTab's own already-tested code; this button only makes
        that action reachable without first navigating to that tab."""
        self._tabs.setCurrentWidget(self._quick)
        self._quick.clean_now()

    def _show_history(self) -> None:
        self._tabs.setCurrentWidget(self._history)

    def _show_exclusions(self) -> None:
        """Open the exclusions editor, then re-scan whatever tab is
        showing -- `scan_cache.cached_scan` enforces a new exclusion on
        its very next call, but a tab's own tree only redraws when it is
        told to scan again."""
        _CleanupExclusionsDialog(self._tabs.window()).exec()
        current = self._tabs.currentWidget()
        if hasattr(current, "_do_scan") and getattr(current, "_scanned", False):
            current._do_scan()

    # ── Auto-scan on tab switch ──

    def _on_tab_changed(self, index: int):
        tab = self._tabs.widget(index)
        if tab is self._history:
            self._history.refresh()
        if hasattr(tab, "auto_scan"):
            tab.auto_scan()
        scan = getattr(tab, '_scan_tab', tab)
        worker = getattr(scan, '_scan_worker', None)
        if worker is not None:
            worker.signals.result.connect(lambda *_: self._area_scan_finished()
                                          if _alive(self._tabs) else None)

    def _area_scan_finished(self):
        snapshot = self._current_snapshot()
        if snapshot is not None:
            self._remember_memberships(snapshot)
            self._export_btn.setEnabled(True)

    def _on_category_clicked(self, category_id: str) -> None:
        target = self._tab_widgets.get(_CATEGORY_TAB_KEYS.get(category_id))
        if target is not None:
            self._tabs.setCurrentWidget(target)

    # ── BaseModule lifecycle ──

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self._cancel_all_tabs()
        self.cancel_all_workers()

    def on_activate(self) -> None:
        """Auto-scan the Quick Cleanup tab when the module is first opened."""
        if getattr(self, "_summary", None) is not None:
            self._summary.refresh()
        if getattr(self, "_quick", None) is None:
            return
        self._quick.auto_scan()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        """Only the Quick Cleanup tab auto-refreshes, and only while it's
        the one actually visible -- every other tab in this module has
        never auto-refreshed on a timer, and blindly rescanning whichever
        tab happens to be open would silently re-run something like Large
        Items' full-machine scan every 60s while someone is reading it."""
        if getattr(self, "_quick", None) is None:
            return
        if self._tabs.currentWidget() is self._quick:
            self._quick.scan()

    def on_deactivate(self) -> None:
        self._cancel_all_tabs()

    def _cancel_all_tabs(self) -> None:
        self._navigation_generation = getattr(self, "_navigation_generation", 0) + 1
        # Every one of these is created in create_widget(), and on_stop() runs
        # even for a module whose widget was never built.
        for name in (
            "_quick", "_sys_tab", "_browser", "_app_tab",
            "_wu_tab", "_logs_tab", "_large", "_dev_tab", "_discover", "_duplicates", "_summary", "_recommendations", "_history", "_overview",
        ):
            tab = getattr(self, name, None)
            if hasattr(tab, "_cancel_all"):
                tab._cancel_all()
            elif hasattr(tab, "cancel"):
                tab.cancel()

    def get_status_info(self) -> str:
        return "Cleanup"

    def _build_header(self, main_lay):
        # One compact header; the full last-clean account stays in History.
        header = QHBoxLayout()
        header.setSpacing(8)
        self._snapshot = None
        self._path_tabs = {}
        self._freed_bytes = 0
        self._navigation_generation = 0
        self._clean_now_btn = QPushButton("Clean safe items (Light)")
        self._clean_now_btn.setObjectName("accentButton")
        self._clean_now_btn.setToolTip(
            "Scan if needed, then clean the active preset after confirmation. "
            "Light includes safe locations only; review the preset before changing it.")
        self._clean_now_btn.clicked.connect(self._clean_now)
        header.addWidget(self._clean_now_btn)
        self._summary = DriveSummaryBar()
        header.addWidget(self._summary)
        header.addStretch(1)
        self._freed_lbl = QLabel("0 B session")
        set_role(self._freed_lbl, "statusSuccess")
        self._freed_lbl.setToolTip("Measured bytes freed since this module was opened.")
        header.addWidget(self._freed_lbl)
        self._all_time_lbl = QLabel(
            f"{cs.format_size(cleanup_history.total_freed_all_time())} all time")
        set_role(self._all_time_lbl, "muted")
        self._all_time_lbl.setToolTip("Measured bytes freed across recorded cleanup runs.")
        header.addWidget(self._all_time_lbl)
        self._export_btn = QPushButton("Export report")
        self._export_btn.setEnabled(False)
        self._export_btn.setToolTip(
            "Save and open an offline HTML scan report and CSV from the latest scan, "
            "including explanations and recommendations. No new scan or cleanup runs.")
        self._export_btn.clicked.connect(self._export_report)
        header.addWidget(self._export_btn)
        exclusions_btn = QPushButton("Exclusions…")
        exclusions_btn.setToolTip("Manage folders that Cleanup must never offer or delete.")
        exclusions_btn.clicked.connect(self._show_exclusions)
        header.addWidget(exclusions_btn)
        main_lay.addLayout(header)
        main_lay.addWidget(self._summary.last)
        self._summary.last.clicked.connect(self._show_history)
        return exclusions_btn

    def _build_tabs(self):
        # 1. Quick Cleanup (merged from the former standalone
        # QuickCleanupModule -- see docs/superpowers/specs/
        # 2026-09-14-cleanup-quick-cleanup-merge-design.md)
        self._quick = QuickCleanupTab(on_category_clicked=self._on_category_clicked)
        self._quick.build(advanced_categories=ADVANCED_CATEGORIES)
        self._tabs.addTab(self._quick, "Quick Cleanup")

        # 2. System Junk
        sys_scanners = {
            cs.scan_temp_files:       ("Temp Files",       "safe"),
            cs.scan_prefetch:         ("Prefetch",          "caution"),
            cs.scan_thumbnail_cache:  ("Thumbnail Cache",   "safe"),
            cs.scan_user_crash_dumps: ("User Crash Dumps",  "caution"),
        }
        self._sys_tab = _ScanTab(
            _with_catalog({**sys_scanners, **SYSTEM_EXTRA}, "system"))
        self._tabs.addTab(self._sys_tab, "System Junk")

        # 3. Browser Caches
        self._browser = _BrowserCleanupTab()
        self._tabs.addTab(self._browser, "Browser Caches")

        # 4. App & Game Caches
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
        # "browsers" is here rather than on the Browser Caches tab: that
        # tab runs EnhancedBrowserScanner, which enumerates live profiles
        # and takes no scanner dict at all, so the catalog's per-browser
        # path scanners had nowhere to appear. Overlap between the two is
        # handled by dedupe_items, which counts a path once.
        self._app_tab = _ScanTab(_with_catalog(
            app_scanners, "apps", "games", "media", "comms", "cloud",
            "browsers"))
        self._tabs.addTab(self._app_tab, "App && Game Caches")

        # 5. Windows Update
        wu_scanners = {
            cs.scan_wu_cache:              ("WU Download Cache",   "caution"),
            cs.scan_delivery_optimization: ("Delivery Opt. Cache", "safe"),
        }
        self._wu_tab = _ScanTab(_short_labels(wu_scanners), wu_cache=True)
        self._tabs.addTab(self._wu_tab, "Windows Update")

        # 6. Logs & Reports
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
        self._logs_tab = _ScanTab(_short_labels({**log_scanners, **LOGS_EXTRA}))
        self._tabs.addTab(self._logs_tab, "Logs && Reports")

        # 7. Large Items + DISM
        self._large = _LargeItemsTab()
        self._large._scan_tab._scanners = _short_labels(self._large._scan_tab._scanners)
        self._tabs.addTab(self._large, "Large Items")

        # 8. Dev Tools
        dev_scanners = {
            cs.scan_dev_tool_caches: ("Dev Tool Caches", "safe"),
        }
        self._dev_tab = _ScanTab(_with_catalog(dev_scanners, "dev"))
        self._tabs.addTab(self._dev_tab, "Dev Tools")

        # 9. Read-only cache discovery
        self._discover = _DiscoverTab()
        self._tabs.addTab(self._discover, "Discover")

        # 10. Explicit, user-triggered duplicate discovery (never auto-scans).
        from modules.cleanup.tabs._duplicates_tab import _DuplicatesTab
        self._duplicates = _DuplicatesTab()
        self._tabs.addTab(self._duplicates, "Duplicates")
        self._duplicates.freed_bytes.connect(self._on_duplicates_freed)

        # 11. What every clean really did
        from modules.cleanup.tabs._history_tab import _HistoryTab
        self._history = _HistoryTab()
        self._tabs.addTab(self._history, "History")

    def _configure_tab_navigation(self, exclusions_btn):
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            zone = "Overview" if i == 0 else "Review by area" if i < 8 else "Tools and records"
            self._tabs.setTabToolTip(i, f"{zone}: {self._tabs.tabText(i).replace('&&', '&')}. "
                                     "Scan results explain what is safe and what needs review.")
            tab.setProperty("cleanupZone", zone)
            if i in (1, 8):
                separator = QWidget()
                separator.setFixedSize(12, 12)
                separator.setToolTip(zone)
                self._tabs.tabBar().setTabButton(i, self._tabs.tabBar().ButtonPosition.LeftSide, separator)
        self._tabs.tabBar().setExpanding(False)
        for button in (self._clean_now_btn, self._export_btn, exclusions_btn):
            button.setAccessibleName(button.text())
            button.setAccessibleDescription(button.toolTip())

    def _wire_tab_signals(self):
        # ── Wire signals ──
        for tab in (
            self._quick, self._sys_tab, self._browser, self._app_tab,
            self._wu_tab, self._logs_tab, self._large, self._dev_tab, self._discover,
        ):
            tab.freed_bytes.connect(self._on_freed)

        self._tabs.currentChanged.connect(self._on_tab_changed)
