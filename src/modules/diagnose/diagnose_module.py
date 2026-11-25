"""Diagnose — Unified Diagnostic & Log Analysis Workspace.

Unifies seven diagnostic system readers plus the CMTrace arbitrary log viewer
into one cohesive workspace under a single search bar and cross-log correlation
engine.

Tabs:
0. Event Viewer
1. CBS Log
2. DISM Log
3. Windows Update
4. Reliability
5. Crash Dumps
6. Timeline
7. Log Viewer (CMTrace File & Folder Viewer)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QMessageBox, QProgressBar, QPushButton, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.composite_module import CompositeModule
from core.incident_correlation import correlate_incident_window, IncidentCorrelation
from core.module_groups import ModuleGroup
from core.search_provider import SearchQuery, SearchResult
from core.table_ui import set_role
from core.types import LogEntry
from core.worker import Worker, retire
from modules.diagnose.help_text import CONTROLS, HEADERS, INTRO, READERS
from modules.diagnose.help_ui import explain
from ui.event_detail_dialog import EventDetailDialog

logger = logging.getLogger(__name__)


class DiagnoseModule(CompositeModule):
    """Unified diagnostic hub — 8 diagnostic log readers, cross-log correlation, and incident presets."""

    name = "Diagnose"
    icon = "🩺"
    description = "Unified diagnostic hub — search across all logs, events, crash reports, and arbitrary log files"
    group = ModuleGroup.DIAGNOSE

    def __init__(self):
        super().__init__()
        from modules.cbs_log.cbs_module import CBSLogModule
        from modules.crash_dumps.crash_dump_module import CrashDumpModule
        from modules.dism_log.dism_module import DISMLogModule
        from modules.event_viewer.event_viewer_module import EventViewerModule
        from modules.log_viewer.log_viewer_module import LogViewerModule
        from modules.reliability.reliability_module import ReliabilityModule
        from modules.timeline.timeline_module import TimelineModule
        from modules.windows_update.wu_module import WindowsUpdateModule

        self.children = [
            EventViewerModule(),
            CBSLogModule(),
            DISMLogModule(),
            WindowsUpdateModule(),
            ReliabilityModule(),
            CrashDumpModule(),
            TimelineModule(),
            LogViewerModule(),
        ]

        self._widget: Optional[QWidget] = None
        self._search_input: Optional[QLineEdit] = None
        self._search_progress: Optional[QProgressBar] = None
        self._results_tree: Optional[QTreeWidget] = None
        self._tab_widget: Optional[QTabWidget] = None
        self._search_timer: Optional[QTimer] = None
        self._active_search: Optional[Worker] = None

        # Cross-log sync state
        self._sync_banner: Optional[QFrame] = None
        self._sync_label: Optional[QLabel] = None
        self._sync_anchor_time: Optional[datetime] = None
        self._sync_window_minutes: int = 5
        self._last_correlation: Optional[IncidentCorrelation] = None

    # ------------------------------------------------------------------
    # Chrome around the tabs
    # ------------------------------------------------------------------

    def wrap(self, tabs: QTabWidget) -> QWidget:
        self._tab_widget = tabs
        self._widget = QWidget()
        root = QVBoxLayout(self._widget)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._build_search_row(root)
        self._build_action_row(root)
        self._build_sync_banner(root)
        self._build_results_tree(root, tabs)
        self._wire_children_and_tooltips(tabs)

        return self._widget

    def _build_search_row(self, root: QVBoxLayout) -> None:
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search across all diagnostic logs…")
        self._search_input.setMinimumHeight(30)
        self._search_input.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_input, 1)

        self._search_progress = QProgressBar()
        self._search_progress.setMaximumWidth(120)
        self._search_progress.setMaximum(0)
        self._search_progress.setVisible(False)
        search_row.addWidget(self._search_progress)

        clear_btn = QPushButton("Clear")
        clear_btn.setMaximumWidth(55)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_search)
        search_row.addWidget(clear_btn)

        self._refresh_all_btn = QPushButton("Refresh All")
        self._refresh_all_btn.setToolTip("Reload all open diagnostic log readers simultaneously.")
        self._refresh_all_btn.clicked.connect(self._refresh_all_readers)
        search_row.addWidget(self._refresh_all_btn)

        self._casebook_btn = QPushButton("📋 Casebook")
        self._casebook_btn.setToolTip("Open Investigation Casebook (persistent notes, tags, and case briefs across all tabs).")
        self._casebook_btn.clicked.connect(self._open_casebook_dialog)
        search_row.addWidget(self._casebook_btn)

        self._export_bundle_btn = QPushButton("📦 Export Incident Bundle…")
        self._export_bundle_btn.setToolTip(
            "Package diagnostic logs, executive HTML report, PII-sanitized CSV, and system telemetry into a ZIP bundle."
        )
        self._export_bundle_btn.clicked.connect(self._export_incident_bundle)
        search_row.addWidget(self._export_bundle_btn)

        root.addLayout(search_row)

        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self._search_input.textChanged.connect(
            lambda text: self._search_timer.start(300) if text else self._clear_search()
        )

    def _build_action_row(self, root: QVBoxLayout) -> None:
        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        self._triage_btn = QPushButton("⚡ Incident Triage Presets ▾")
        self._triage_btn.setToolTip("One-click diagnostic workflow presets for common Windows break-fix scenarios.")
        triage_menu = QMenu(self._widget)
        triage_menu.addAction("Windows Update Failure Triage").triggered.connect(lambda: self.apply_triage_preset("wu"))
        triage_menu.addAction("BSOD / Kernel Crash Dumps Triage").triggered.connect(lambda: self.apply_triage_preset("bsod"))
        triage_menu.addAction("CBS & Servicing Corruption Triage").triggered.connect(lambda: self.apply_triage_preset("cbs"))
        triage_menu.addAction("Application Crash & Hang Triage").triggered.connect(lambda: self.apply_triage_preset("app"))
        triage_menu.addAction("Event Storm & Service Crash Triage").triggered.connect(lambda: self.apply_triage_preset("storm"))
        triage_menu.addSeparator()
        triage_menu.addAction("Reset All Filters & Sync Window").triggered.connect(lambda: self.apply_triage_preset("reset"))

        self._triage_btn.setMenu(triage_menu)
        action_row.addWidget(self._triage_btn)

        self._open_file_btn = QPushButton("📂 Open Log…")
        self._open_file_btn.setToolTip("Open an arbitrary log file (CMTrace, TXT, CAB, ZIP) in the Log Viewer.")
        self._open_file_btn.clicked.connect(self.open_log_file)
        action_row.addWidget(self._open_file_btn)

        self._open_folder_btn = QPushButton("📁 Open Folder…")
        self._open_folder_btn.setToolTip("Open a directory of logs into a merged timeline.")
        self._open_folder_btn.clicked.connect(self.open_log_folder)
        action_row.addWidget(self._open_folder_btn)

        self._correlate_btn = QPushButton("⏱️ Correlate Incident…")
        self._correlate_btn.setToolTip("Analyze cross-log causal chains, velocity spikes, and precursor faults around the current timestamp.")
        self._correlate_btn.clicked.connect(self.correlate_current_window)
        action_row.addWidget(self._correlate_btn)

        self._error_lookup_btn = QPushButton("🔍 Error Code Lookup…")
        self._error_lookup_btn.setToolTip("Translate HRESULT, NTSTATUS, or MSI error codes into plain English with causes and remedies.")
        self._error_lookup_btn.clicked.connect(self.open_error_lookup)
        action_row.addWidget(self._error_lookup_btn)

        action_row.addStretch()
        root.addLayout(action_row)

    def _build_sync_banner(self, root: QVBoxLayout) -> None:
        self._sync_banner = QFrame()
        self._sync_banner.setObjectName("card")
        self._sync_banner.setVisible(False)
        sbl = QHBoxLayout(self._sync_banner)
        sbl.setContentsMargins(10, 6, 10, 6)
        sbl.setSpacing(10)

        self._sync_label = QLabel()
        sbl.addWidget(self._sync_label, 1)

        sync_analyze_btn = QPushButton("📊 Analyze Incident Window")
        sync_analyze_btn.setToolTip("Open full root-cause correlation report for this synchronized window.")
        sync_analyze_btn.clicked.connect(self.correlate_current_window)
        sbl.addWidget(sync_analyze_btn)

        sync_clear_btn = QPushButton("✕ Clear Sync")
        sync_clear_btn.setToolTip("Restore all diagnostic tabs to their full unfiltered view.")
        sync_clear_btn.clicked.connect(self.clear_sync_window)
        sbl.addWidget(sync_clear_btn)

        root.addWidget(self._sync_banner)

    def _build_results_tree(self, root: QVBoxLayout, tabs: QTabWidget) -> None:
        self._results_tree = QTreeWidget()
        self._results_tree.setHeaderLabels(["Time", "Source", "Summary"])
        hdr = self._results_tree.header()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self._results_tree.setColumnWidth(0, 160)
        self._results_tree.setColumnWidth(1, 140)
        self._results_tree.setAlternatingRowColors(True)
        self._results_tree.setRootIsDecorated(True)
        self._results_tree.setVisible(False)
        self._results_tree.itemDoubleClicked.connect(self._on_tree_result_activated)
        root.addWidget(self._results_tree, 1)
        root.addWidget(tabs, 1)

    def _wire_children_and_tooltips(self, tabs: QTabWidget) -> None:
        for child in self.children:
            connect = getattr(child, "connect_activated", None)
            if callable(connect):
                connect(self._open_event_dialog)
            pane = getattr(child, "_pane", None)
            if pane is not None:
                tbl = getattr(pane, "_table", None)
                if tbl is not None and hasattr(tbl, "sync_panes_requested"):
                    tbl.sync_panes_requested.connect(self.sync_all_panes_to_time)

        for index in range(tabs.count()):
            tabs.setTabToolTip(index, READERS.get(tabs.tabText(index), "Read diagnostic evidence."))
        explain(self._widget, INTRO, CONTROLS, HEADERS, READERS)
        self._search_input.setToolTip(
            "Search entries already loaded by the diagnostic tabs. Type to search or press Enter; "
            "unopened tabs may have no cached entries. This does not scan files, clear logs or change Windows.")

    # ------------------------------------------------------------------
    # Cross-Log Jump-to-Time / Sync across Panes
    # ------------------------------------------------------------------

    def sync_all_panes_to_time(self, anchor_time: datetime, window_minutes: int = 5) -> None:
        """Align all tabs to records within `±window_minutes` of `anchor_time`."""
        if not anchor_time:
            return

        self._sync_anchor_time = anchor_time
        self._sync_window_minutes = window_minutes

        if self._sync_banner and self._sync_label:
            ts_str = anchor_time.strftime("%Y-%m-%d %H:%M:%S")
            self._sync_label.setText(
                f"<b>⏱️ Synchronized Incident Window:</b> {ts_str} (±{window_minutes} min) — All diagnostic tabs filtered to this window"
            )
            set_role(self._sync_label, "statusInfo")
            self._sync_banner.setVisible(True)

        start_dt = anchor_time - timedelta(minutes=window_minutes)
        end_dt = anchor_time + timedelta(minutes=window_minutes)

        for child in self.children:
            pane = getattr(child, "_pane", None)
            if pane is not None:
                if hasattr(pane, "filter_time_range"):
                    pane.filter_time_range(start_dt, end_dt)
                if hasattr(pane, "_on_jump_to_time"):
                    pane._on_jump_to_time(anchor_time)
            elif child.name == "Log Viewer":
                widget = getattr(child, "_widget", None)
                if widget is not None and hasattr(widget, "go_to_time"):
                    widget.go_to_time(anchor_time)

    def clear_sync_window(self) -> None:
        """Clear synchronized time window and restore standard view."""
        self._sync_anchor_time = None
        if self._sync_banner:
            self._sync_banner.setVisible(False)

        for child in self.children:
            pane = getattr(child, "_pane", None)
            if pane is not None:
                if hasattr(pane, "_search_input"):
                    pane._search_input.clear()
                if hasattr(pane, "refresh_view"):
                    pane.refresh_view()

    # ------------------------------------------------------------------
    # Incident Correlation Analysis
    # ------------------------------------------------------------------

    def correlate_current_window(self) -> None:
        """Run multi-log correlation analysis around current anchor or latest error."""
        entries_by_source, all_entries = self._collect_loaded_entries()

        if not all_entries:
            QMessageBox.information(
                self._widget,
                "Incident Correlation",
                "No diagnostic records are currently loaded to correlate. Click 'Refresh All' to load evidence first.",
            )
            return

        anchor = self._sync_anchor_time
        if not anchor:
            errors = [e for e in all_entries if "err" in (e.level or "").lower() or "crit" in (e.level or "").lower()]
            anchor = max(errors, key=lambda e: e.timestamp).timestamp if errors else max(all_entries, key=lambda e: e.timestamp).timestamp

        correlation = correlate_incident_window(
            anchor_time=anchor,
            entries_by_source=entries_by_source,
            window_minutes=self._sync_window_minutes,
        )
        self._last_correlation = correlation

        from modules.diagnose.incident_correlation_dialog import IncidentCorrelationDialog
        dlg = IncidentCorrelationDialog(correlation, parent=self._widget)
        dlg.exec()

    def _collect_loaded_entries(self) -> tuple[Dict[str, List[LogEntry]], List[LogEntry]]:
        entries_by_source: Dict[str, List[LogEntry]] = {}
        all_entries: List[LogEntry] = []

        for child in self.children:
            pane = getattr(child, "_pane", None)
            if pane is not None and hasattr(pane, "source_entries"):
                es = [e for e in pane.source_entries() if isinstance(e, LogEntry)]
                if es:
                    entries_by_source[child.name] = es
                    all_entries.extend(es)
            elif child.name == "Log Viewer":
                widget = getattr(child, "_widget", None)
                if widget is not None and hasattr(widget, "model") and hasattr(widget.model, "_entries"):
                    lv_entries = [
                        LogEntry(
                            timestamp=getattr(e, "time", None),
                            source=f"LogViewer:{getattr(e, 'component', 'file')}",
                            level=getattr(e, "level", "Info"),
                            message=getattr(e, "message", ""),
                        )
                        for e in widget.model._entries if getattr(e, "time", None)
                    ]
                    if lv_entries:
                        entries_by_source["Log Viewer"] = lv_entries
                        all_entries.extend(lv_entries)

        return entries_by_source, all_entries

    # ------------------------------------------------------------------
    # Enterprise Triage Presets
    # ------------------------------------------------------------------

    def apply_triage_preset(self, preset_key: str) -> None:
        """Apply an enterprise incident triage scenario across diagnostic tabs."""
        if not self._tab_widget:
            return

        if preset_key == "wu":
            self._tab_widget.setCurrentIndex(3)
            pane = getattr(self.children[3], "_pane", None)
            if pane is not None:
                pane._preset_combo.setCurrentIndex(1)
                pane._on_preset_selected(1)

        elif preset_key == "bsod":
            self._tab_widget.setCurrentIndex(5)
            ev_pane = getattr(self.children[0], "_pane", None)
            if ev_pane is not None:
                ev_pane._preset_combo.setCurrentIndex(6)
                ev_pane._on_preset_selected(6)

        elif preset_key == "cbs":
            self._tab_widget.setCurrentIndex(1)
            pane = getattr(self.children[1], "_pane", None)
            if pane is not None:
                pane._search_input.setText("0x800f or 0x8007 or corrupt or fail")

        elif preset_key == "app":
            self._tab_widget.setCurrentIndex(0)
            pane = getattr(self.children[0], "_pane", None)
            if pane is not None:
                pane._preset_combo.setCurrentIndex(4)
                pane._on_preset_selected(4)

        elif preset_key == "storm":
            self._handle_storm_preset()

        elif preset_key == "reset":
            self.clear_sync_window()
            self._clear_search()

    def _handle_storm_preset(self) -> None:
        cur_idx = self._tab_widget.currentIndex() if self._tab_widget else 0
        if 0 <= cur_idx < len(self.children):
            child = self.children[cur_idx]
            pane = getattr(child, "_pane", None)
            if pane is not None:
                from core.event_storm import detect_event_storms
                entries = [e for e in pane.source_entries() if isinstance(e, LogEntry)]
                storms = detect_event_storms(entries)
                if storms:
                    top = storms[0]
                    QMessageBox.warning(
                        self._widget,
                        "Event Storm Detected",
                        f"Detected high-velocity burst of {top.total_count} events from '{top.source}' "
                        f"({top.events_per_second:.1f} events/sec).\n\n"
                        "Toggling duplicate collapse to suppress noise.",
                    )
                    pane._collapse_dups_btn.setChecked(True)
                else:
                    QMessageBox.information(
                        self._widget,
                        "Event Storm Analysis",
                        "No runaway event storms or high-frequency loops were detected in the active tab.",
                    )

    # ------------------------------------------------------------------
    # Arbitrary Log File / Folder Operations
    # ------------------------------------------------------------------

    def open_log_file(self, file_path: Optional[str] = None) -> None:
        """Open a file in the integrated Log Viewer tab (tab 7)."""
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self._widget,
                "Open Log File in CMTrace Viewer",
                "",
                "Logs and bundles (*.log *.lo_ *.txt *.cab *.zip);;All files (*)",
            )
        if not file_path:
            return

        if self._tab_widget:
            self._tab_widget.setCurrentIndex(7)

        lv_child = self.children[7]
        widget = getattr(lv_child, "_widget", None)
        if widget is not None and hasattr(widget, "open"):
            widget.open(file_path)

    def open_log_folder(self, folder_path: Optional[str] = None) -> None:
        """Open a directory of logs in the integrated Log Viewer tab (tab 7)."""
        if not folder_path:
            folder_path = QFileDialog.getExistingDirectory(
                self._widget,
                "Open Log Directory into Merged Timeline",
                "",
            )
        if not folder_path:
            return

        if self._tab_widget:
            self._tab_widget.setCurrentIndex(7)

        lv_child = self.children[7]
        widget = getattr(lv_child, "_widget", None)
        if widget is not None and hasattr(widget, "open_folder"):
            widget.open_folder(folder_path)

    def open_error_lookup(self, text: str = "") -> None:
        """Open the Error Code Lookup dialog (HRESULT / NTSTATUS / MSI)."""
        from modules.log_viewer.error_lookup_dialog import ErrorLookupDialog
        dlg = ErrorLookupDialog(parent=self._widget)
        if text:
            dlg.input.setText(text)
            dlg._look_up_input()
        dlg.exec()

    # ------------------------------------------------------------------
    # Detail dialog
    # ------------------------------------------------------------------

    def _open_event_dialog(self, entry: LogEntry) -> None:
        dlg = EventDetailDialog(entry, self._widget)
        dlg.exec()

    def _on_tree_result_activated(self, item, column) -> None:
        search_result = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(search_result, SearchResult):
            parent = item.parent()
            if parent:
                search_result = parent.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(search_result, SearchResult):
                return
        entry = LogEntry(
            timestamp=search_result.timestamp,
            source=search_result.source,
            level=search_result.type,
            message=search_result.summary,
            raw=search_result.detail if isinstance(search_result.detail, dict) else {},
        )
        self._open_event_dialog(entry)

    # ------------------------------------------------------------------
    # Unified search
    # ------------------------------------------------------------------

    def _clear_search(self) -> None:
        if self._search_timer:
            self._search_timer.stop()
        if self._active_search:
            retire([self._active_search])
            self._active_search = None

        if self._search_input:
            self._search_input.blockSignals(True)
            self._search_input.clear()
            self._search_input.blockSignals(False)

        if self._search_progress:
            self._search_progress.setVisible(False)
        if self._results_tree:
            self._results_tree.clear()
            self._results_tree.setVisible(False)
        if self._tab_widget:
            self._tab_widget.setVisible(True)

    def _do_search(self) -> None:
        query_text = self._search_input.text().strip() if self._search_input else ""
        if not query_text:
            self._clear_search()
            return

        if self._search_progress:
            self._search_progress.setVisible(True)
        if self._results_tree:
            self._results_tree.clear()
            self._results_tree.setVisible(True)
        if self._tab_widget:
            self._tab_widget.setVisible(False)

        query = SearchQuery(text=query_text)
        targets = [
            (child.name, provider)
            for child in self.children
            for provider in child.get_search_providers()
        ]

        def work(worker):
            all_results: List[SearchResult] = []
            for tab_name, provider in targets:
                try:
                    results = provider.search(query)
                    for r in results:
                        r.source = f"{tab_name} / {r.source}"
                    all_results.extend(results)
                except Exception as ex:
                    logger.warning("Search provider '%s' failed: %s", tab_name, ex)
            return all_results

        if self._active_search:
            self._active_search.cancel()

        self._active_search = Worker(work)
        self._active_search.signals.result.connect(self._on_search_done)
        self._active_search.signals.error.connect(self._on_search_error)

        if self.app:
            self.app.thread_pool.start(self._active_search)

    def _on_search_done(self, results: List[SearchResult]) -> None:
        if self._search_progress:
            self._search_progress.setVisible(False)

        if not self._results_tree:
            return

        by_tab: Dict[str, List[SearchResult]] = {}
        for r in results:
            tab = r.source.split(" / ", 1)[0] if " / " in r.source else r.source
            by_tab.setdefault(tab, []).append(r)

        for tab_name, tab_results in sorted(by_tab.items(), key=lambda x: -len(x[1])):
            count = len(tab_results)
            parent = QTreeWidgetItem(self._results_tree)
            parent.setText(0, f"{tab_name}  [{count} result{'s' if count != 1 else ''}]")
            parent.setExpanded(len(by_tab) <= 4)
            for r in tab_results[:200]:
                child = QTreeWidgetItem(parent)
                ts = r.timestamp.strftime("%Y-%m-%d %H:%M") if r.timestamp else "—"
                child.setText(0, ts)
                child.setText(1, r.type or "—")
                child.setText(2, r.summary[:200] if r.summary else "—")
                child.setData(0, Qt.ItemDataRole.UserRole, r)

        if self._results_tree.topLevelItemCount() == 0:
            empty = QTreeWidgetItem(self._results_tree)
            empty.setText(0, "(no results)")

    def _on_search_error(self, error) -> None:
        if self._search_progress:
            self._search_progress.setVisible(False)
        logger.error("Unified search error: %s", error)

    def on_stop(self) -> None:
        if self._active_search is not None:
            retire([self._active_search])
            self._active_search = None
        super().on_stop()

    def _refresh_all_readers(self) -> None:
        for child in self.children:
            pane = getattr(child, "_pane", None)
            if pane is not None:
                pane.load(force=True)

    def _open_casebook_dialog(self) -> None:
        from ui.investigation_casebook_dialog import InvestigationCasebookDialog
        dlg = InvestigationCasebookDialog(parent=self._widget)
        dlg.exec()
        for child in self.children:
            pane = getattr(child, "_pane", None)
            if pane is not None and getattr(pane, "loaded", False):
                pane.refresh_view()

    def _export_incident_bundle(self) -> None:
        from modules.diagnose.incident_export_dialog import IncidentExportDialog
        current_idx = self._tab_widget.currentIndex() if self._tab_widget else 0
        dlg = IncidentExportDialog(self.children, current_index=current_idx, parent=self._widget)
        dlg.exec()
