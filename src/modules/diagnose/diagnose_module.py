"""Diagnose — the six diagnostic log readers under one search bar.

Diagnose used to own all six panes itself, with the six standalone modules
sitting beside it as dead source nothing imported. Now it is a
`CompositeModule`: each reader is a real module again, and Diagnose keeps what
was only ever its own — the unified search across all of them.

That search is why this class overrides `wrap()`. Its widget is a search box
and a results tree ABOVE the tabs, not a bare tab widget, and the results tree
replaces the tabs on screen while a search is showing.
"""
import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout, QLineEdit, QProgressBar, QPushButton, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup
from core.search_provider import SearchQuery, SearchResult
from core.types import LogEntry
from core.worker import Worker, retire
from ui.event_detail_dialog import EventDetailDialog
from modules.diagnose.help_ui import explain
from modules.diagnose.help_text import INTRO, CONTROLS, HEADERS, READERS

logger = logging.getLogger(__name__)


class DiagnoseModule(CompositeModule):
    """Unified diagnostic hub — six log readers plus a search across all of them."""

    name = "Diagnose"
    icon = "🩺"
    description = "Unified diagnostic hub — search across all logs, events, and crash reports"
    group = ModuleGroup.DIAGNOSE

    def __init__(self):
        super().__init__()
        from modules.cbs_log.cbs_module import CBSLogModule
        from modules.crash_dumps.crash_dump_module import CrashDumpModule
        from modules.dism_log.dism_module import DISMLogModule
        from modules.event_viewer.event_viewer_module import EventViewerModule
        from modules.reliability.reliability_module import ReliabilityModule
        from modules.windows_update.wu_module import WindowsUpdateModule

        self.children = [
            EventViewerModule(),
            CBSLogModule(),
            DISMLogModule(),
            WindowsUpdateModule(),
            ReliabilityModule(),
            CrashDumpModule(),
        ]

        self._widget: Optional[QWidget] = None
        self._search_input: Optional[QLineEdit] = None
        self._search_progress: Optional[QProgressBar] = None
        self._results_tree: Optional[QTreeWidget] = None
        self._tab_widget: Optional[QTabWidget] = None
        self._search_timer: Optional[QTimer] = None
        self._active_search: Optional[Worker] = None

    # ------------------------------------------------------------------
    # Chrome around the tabs
    # ------------------------------------------------------------------

    def wrap(self, tabs: QTabWidget) -> QWidget:
        self._tab_widget = tabs
        self._widget = QWidget()
        root = QVBoxLayout(self._widget)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Unified search bar ─────────────────────────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search across all diagnostic logs…")
        self._search_input.setMinimumHeight(32)
        self._search_input.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_input, 1)

        self._search_progress = QProgressBar()
        self._search_progress.setMaximumWidth(120)
        self._search_progress.setMaximum(0)
        self._search_progress.setVisible(False)
        search_row.addWidget(self._search_progress)

        clear_btn = QPushButton("Clear")
        clear_btn.setMaximumWidth(60)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_search)
        search_row.addWidget(clear_btn)

        root.addLayout(search_row)

        # Debounce timer for live search
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self._search_input.textChanged.connect(
            lambda text: self._search_timer.start(300) if text else self._clear_search()
        )

        # ── Results tree (unified search output) ───────────────────────
        self._results_tree = QTreeWidget()
        self._results_tree.setHeaderLabels(["Time", "Source", "Summary"])
        self._results_tree.setColumnWidth(0, 160)
        self._results_tree.setColumnWidth(1, 120)
        self._results_tree.setAlternatingRowColors(True)
        self._results_tree.setRootIsDecorated(True)
        self._results_tree.setStyleSheet("""
            QTreeWidget {
                background: #2d2d2d;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
            }
            QTreeWidget::item { padding: 4px 0; }
            QTreeWidget::item:selected { background: #3c3c3c; }
            QHeaderView::section {
                background: #3c3c3c;
                color: #b0b0b0;
                padding: 4px;
                border: none;
            }
        """)
        self._results_tree.setVisible(False)
        self._results_tree.itemDoubleClicked.connect(self._on_tree_result_activated)
        root.addWidget(self._results_tree, 1)

        root.addWidget(tabs, 1)

        # A row double-clicked in any child's pane opens the detail dialog.
        for child in self.children:
            connect = getattr(child, "connect_activated", None)
            if callable(connect):
                connect(self._open_event_dialog)

        # Each reader owns its intro; permanent host pages only need tab tooltips.
        for index in range(tabs.count()):
            tabs.setTabToolTip(index, READERS.get(tabs.tabText(index), "Read diagnostic evidence."))
        explain(self._widget, INTRO, CONTROLS, HEADERS, READERS)
        self._search_input.setToolTip(
            "Search entries already loaded by the diagnostic tabs. Type to search or press Enter; "
            "unopened tabs may have no cached entries. This does not scan files, clear logs or change Windows.")
        return self._widget

    # ------------------------------------------------------------------
    # Detail dialog
    # ------------------------------------------------------------------

    def _open_event_dialog(self, entry: LogEntry) -> None:
        dlg = EventDetailDialog(entry, self._widget)
        dlg.exec()

    def _on_tree_result_activated(self, item, column) -> None:
        """Open detail dialog when a unified search result is double-clicked."""
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
        """Reset search UI and show the tab widget."""
        if self._search_timer:
            self._search_timer.stop()
        if self._active_search:
            retire([self._active_search])  # held until its finished is delivered
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
        """Run unified search across every child's provider."""
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
        # Bind (tab name, provider) here, on the GUI thread — the worker must
        # not walk self.children while the shell may be mutating them.
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
                        # Prefix source with tab name
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

        # Group by tab name (source before "/")
        by_tab: Dict[str, List[SearchResult]] = {}
        for r in results:
            tab = r.source.split(" / ", 1)[0] if " / " in r.source else r.source
            by_tab.setdefault(tab, []).append(r)

        # Sort tabs by result count descending
        for tab_name, tab_results in sorted(by_tab.items(), key=lambda x: -len(x[1])):
            count = len(tab_results)
            parent = QTreeWidgetItem(self._results_tree)
            parent.setText(0, f"{tab_name}  [{count} result{'s' if count != 1 else ''}]")
            parent.setExpanded(False)
            for r in tab_results[:200]:  # Cap at 200 per tab
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
            retire([self._active_search])  # held until its finished is delivered
            self._active_search = None
        super().on_stop()
