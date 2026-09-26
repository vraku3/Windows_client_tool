"""One log-reading pane: toolbar, table, detail panel, error banner.

Six diagnostic tabs and six standalone modules were two implementations of
this widget. The Diagnose one had a Refresh button, an empty state, lazy
loading and an error banner; the module one had none of them. This is the
Diagnose one, extracted, so there is exactly one.

The pane knows nothing about what it is reading. `loader` is an ordinary
worker function — it receives the `Worker` and returns a list of `LogEntry` —
so a caller supplies a parser and nothing else.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget,
)

from core.table_ui import set_role
from core.worker import Worker
from ui.detail_panel import DetailPanel
from ui.error_banner import ErrorBanner
from ui.log_table_widget import LogTableWidget

logger = logging.getLogger(__name__)

_PAGE_TABLE = 0
_PAGE_EMPTY = 1


class LogPane(QWidget):
    """A table of log entries with a detail panel, fed by `loader`."""

    entry_selected = pyqtSignal(object)
    entry_activated = pyqtSignal(object)
    entries_loaded = pyqtSignal(object)

    def __init__(
        self,
        loader: Callable[[Worker], list],
        *,
        empty_text: str = "No data — click Refresh",
        extra_controls: Optional[Callable[[QHBoxLayout, dict], None]] = None,
        extra_columns: Optional[List[str]] = None,
        extra_values: Optional[Callable[[object], list]] = None,
        detail_enricher: Optional[Callable[[object], str]] = None,
        summarizer: Optional[Callable[[list], str]] = None,
        thread_pool=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._loader = loader
        self._thread_pool = thread_pool
        self._worker: Optional[Worker] = None
        self._entries: List[object] = []      # what the table shows
        self._source: List[object] = []       # everything the loader returned
        self._view_transform: Optional[Callable[[list], list]] = None
        self._detail_enricher = detail_enricher
        self._summarizer = summarizer
        self.extra: dict = {}
        self.loaded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        if extra_controls is not None:
            extra_controls(toolbar, self.extra)
        toolbar.addStretch()

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(200)
        self._progress.setVisible(False)
        toolbar.addWidget(self._progress)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setObjectName("refreshBtn")
        self._refresh_btn.clicked.connect(lambda: self.load(force=True))
        self._copy_btn = QPushButton("Copy row")
        self._copy_btn.setToolTip("Copy the selected row to the clipboard")
        self._copy_btn.clicked.connect(lambda: self._table.copy_selected_to_clipboard())
        toolbar.addWidget(self._copy_btn)
        self._export_btn = QPushButton("Export CSV...")
        self._export_btn.setToolTip("Export the rows currently shown")
        self._export_btn.clicked.connect(lambda: self._table.export_csv())
        toolbar.addWidget(self._export_btn)
        toolbar.addWidget(self._refresh_btn)
        root.addLayout(toolbar)

        # A one-paragraph read of what was loaded (counts, findings, caveats).
        self._note = QLabel()
        self._note.setWordWrap(True)
        set_role(self._note, "statusInfo")
        self._note.setVisible(False)
        root.addWidget(self._note)

        self._error_banner = ErrorBanner(parent=self)
        root.addWidget(self._error_banner)

        splitter = QSplitter()
        self._table = LogTableWidget(extra_columns=extra_columns, extra_values=extra_values)
        splitter.addWidget(self._table)
        self._detail = DetailPanel()
        splitter.addWidget(self._detail)
        splitter.setSizes([700, 300])

        self._stack = QStackedWidget()
        self._stack.addWidget(splitter)
        empty = QLabel(empty_text)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(empty, "muted")
        self._stack.addWidget(empty)
        self._stack.setCurrentIndex(_PAGE_EMPTY)
        root.addWidget(self._stack, 1)

        self._table.row_selected.connect(self._on_row_selected)
        self._table.row_double_clicked.connect(self.entry_activated.emit)

    # -- loading ---------------------------------------------------------
    def load(self, force: bool = False) -> None:
        """Run `loader` on a worker and fill the table with what it returns."""
        if self.loaded and not force:
            return
        if self._worker is not None:
            self._worker.cancel()

        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._error_banner.clear()

        worker = Worker(self._loader)
        worker.signals.progress.connect(self._progress.setValue)
        worker.signals.result.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        self._worker = worker
        pool = self._thread_pool
        if pool is None:
            from PyQt6.QtCore import QThreadPool
            pool = QThreadPool.globalInstance()
        pool.start(worker)

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None

    def _on_result(self, entries) -> None:
        self._worker = None
        self.loaded = True
        self.set_entries(entries or [])
        self._update_note()
        self.entries_loaded.emit(self._source)

    def _on_error(self, error_info) -> None:
        self._worker = None
        self.show_error(str(error_info))

    # -- content ---------------------------------------------------------
    def set_entries(self, entries) -> None:
        self._source = list(entries or [])
        self._progress.setVisible(False)
        self.refresh_view()

    def refresh_view(self) -> None:
        """Re-derive what the table shows from everything that was loaded."""
        shown = self._source
        if self._view_transform is not None:
            try:
                shown = list(self._view_transform(list(self._source)))
            except Exception:
                logger.warning("The view transform failed; showing every entry", exc_info=True)
                shown = self._source
        self._entries = list(shown)
        self._table.set_entries(self._entries)
        # The empty page is for "nothing loaded"; a filter that matches nothing
        # must still show the (empty) table and its count.
        self._stack.setCurrentIndex(_PAGE_TABLE if self._source else _PAGE_EMPTY)

    def set_view_transform(self, transform: Optional[Callable[[list], list]]) -> None:
        """Filter/group what is shown without touching what was loaded."""
        self._view_transform = transform
        self.refresh_view()

    def source_entries(self) -> List[object]:
        """Every entry the loader returned, before any view transform."""
        return list(self._source)

    def set_note(self, text: str) -> None:
        self._note.setText(text or "")
        self._note.setVisible(bool(text))

    def note_text(self) -> str:
        return self._note.text()

    def _update_note(self) -> None:
        if self._summarizer is None:
            return
        try:
            self.set_note(self._summarizer(list(self._source)))
        except Exception:
            logger.warning("The summariser failed", exc_info=True)
            self.set_note("")

    def selected_entry(self):
        return self._table.selected_entry()

    def show_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._error_banner.set_error(message)

    def clear_error(self) -> None:
        self._error_banner.clear()

    def _on_row_selected(self, entry) -> None:
        extra = ""
        if self._detail_enricher is not None:
            try:
                extra = self._detail_enricher(entry) or ""
            except Exception:
                logger.warning("The detail enricher failed", exc_info=True)
        self._detail.show_entry(entry, extra)
        self.entry_selected.emit(entry)

    # -- introspection, for tests and for get_status_info ----------------
    def entries(self) -> List[object]:
        return list(self._entries)

    def row_count(self) -> int:
        return len(self._entries)

    def is_showing_empty_state(self) -> bool:
        return self._stack.currentIndex() == _PAGE_EMPTY

    def is_showing_error(self) -> bool:
        # isHidden(), NOT isVisible(). A child of a parent that has never been
        # shown is not "visible" no matter what you call on it, so isVisible()
        # is False even right after set_error(). show() clears the explicit
        # hide flag, which is what isHidden() reports.
        return not self._error_banner.isHidden()

    def error_text(self) -> str:
        return self._error_banner.text()
