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
from collections import Counter
from datetime import datetime
from html import escape
from typing import Callable, List, Optional

from PyQt6 import sip
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget,
)

from core.table_ui import set_role
from core.types import LogReadResult
from core.worker import Worker, retire, track
from ui.chip_bar import ChipBar
from ui.empty_state import EmptyState
from ui.glance import GlanceStrip, GlanceTile
from ui.table_style import apply_table_style
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
        empty_text: str = "No records were returned from this source. Refresh to check again.",
        extra_controls: Optional[Callable[[QHBoxLayout, dict], None]] = None,
        extra_columns: Optional[List[str]] = None,
        extra_values: Optional[Callable[[object], list]] = None,
        detail_enricher: Optional[Callable[[object], str]] = None,
        summarizer: Optional[Callable[[list], str]] = None,
        copy_formatter: Optional[Callable[[object], str]] = None,
        glance_tiles=(),
        healthy_empty: bool = False,
        read_problems: Optional[Callable[[], list]] = None,
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
        self._healthy_empty = healthy_empty
        self._read_problems = read_problems
        self._result_problems = []
        self._empty_text = empty_text
        self.extra: dict = {}
        self.loaded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        self._build_glance(root, glance_tiles)

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
        self._refresh_btn.setToolTip(
            "Read this source again, replacing the displayed records. This does not clear "
            "Windows logs or repair faults; restricted files or channels may need administrator rights.")
        self._refresh_btn.setObjectName("refreshBtn")
        self._refresh_btn.clicked.connect(lambda: self.load(force=True))
        self._copy_btn = QPushButton("Copy row")
        self._copy_btn.setToolTip("Copy the selected row to the clipboard, replacing its previous contents. No log or Windows settings change.")
        self._copy_btn.clicked.connect(lambda: self._table.copy_selected_to_clipboard())
        toolbar.addWidget(self._copy_btn)
        self._export_btn = QPushButton("Export CSV...")
        self._export_btn.setToolTip("Choose a CSV file and export the rows currently shown. This writes a report without changing Windows logs; active filters affect which rows are included.")
        self._export_btn.clicked.connect(lambda: self._table.export_csv())
        toolbar.addWidget(self._export_btn)
        toolbar.addWidget(self._refresh_btn)
        root.addLayout(toolbar)

        self._chips = ChipBar([
            ("all", "All loaded", 0), ("critical", "Critical", 0),
            ("error", "Errors", 0), ("warning", "Warnings", 0),
        ])
        self._chips.selection_changed.connect(lambda _keys: self.refresh_view())
        self._chips.setEnabled(False)
        root.addWidget(self._chips)

        # A one-paragraph read of what was loaded (counts, findings, caveats).
        self._note = QLabel()
        self._note.setToolTip("Summary of the loaded evidence and any read limits. Counts cover this source's loaded records, not necessarily its complete history; correlation does not prove a cause.")
        self._note.setWordWrap(True)
        self._note.setTextFormat(Qt.TextFormat.PlainText)
        set_role(self._note, "muted")
        self._note.setVisible(False)
        self._note_btn = QPushButton("Read findings and read limits")
        self._note_btn.setCheckable(True)
        self._note_btn.setToolTip("Expand source-specific findings, explanations and any sources that could not be read.")
        self._note_btn.toggled.connect(self._note.setVisible)
        self._note_btn.setVisible(False)
        root.addWidget(self._note_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._note)

        self._error_banner = ErrorBanner(parent=self)
        root.addWidget(self._error_banner)

        splitter = QSplitter()
        self._table = LogTableWidget(extra_columns=extra_columns, extra_values=extra_values,
                                      copy_formatter=copy_formatter)
        apply_table_style(self._table._table,
                          numeric_columns=range(3, 3 + len(extra_columns or [])),
                          date_columns=(0,))
        splitter.addWidget(self._table)
        self._detail = DetailPanel()
        splitter.addWidget(self._detail)
        splitter.setSizes([700, 300])

        self._stack = QStackedWidget()
        self._stack.addWidget(splitter)
        self._empty = EmptyState('log', "Ready to read this source",
                                 "Read-only records load when this tab opens. Refresh to try again.",
                                 "Refresh")
        self._empty.action_triggered.connect(lambda: self.load(force=True))
        self._stack.addWidget(self._empty)
        self._stack.setCurrentIndex(_PAGE_EMPTY)
        root.addWidget(self._stack, 1)

        self._table.row_selected.connect(self._on_row_selected)
        self._table.row_double_clicked.connect(self.entry_activated.emit)

    def _build_glance(self, root, extra_tiles) -> None:
        self._glance_tiles = {}
        for key, caption in (("critical", "Critical"), ("error", "Errors"),
                             ("warning", "Warnings"), ("sources", "Problem sources"),
                             ("newest", "Newest problem"), ("window", "Loaded timestamps")):
            tile = GlanceTile(caption, tooltip=f"{caption} within the loaded window, before filters. This is not the complete Windows history.")
            tile.set_unreadable("Not read yet")
            tile.setMinimumHeight(128)
            self._glance_tiles[key] = tile
        self._glance = GlanceStrip([*extra_tiles, *self._glance_tiles.values()])
        root.addWidget(self._glance)
        for key in ("critical", "error", "warning"):
            self._glance_tiles[key].clicked.connect(lambda k=key: self._filter_level(k))
        self._glance_tiles["sources"].clicked.connect(lambda: self._filter_level("all"))
        self._glance_tiles["newest"].clicked.connect(self._select_newest_problem)

    def _filter_level(self, key) -> None:
        self._chips.chips[key].setChecked(True)

    def _select_newest_problem(self) -> None:
        self._filter_level("all")
        problems = [e for e in self._entries if e.level.lower() in ("critical", "error", "warning")]
        if not problems:
            return
        newest = max(problems, key=lambda e: e.timestamp)
        model = self._table._model
        for row in range(model.rowCount()):
            if model.item(row, 0).data(Qt.ItemDataRole.UserRole) is newest:
                index = model.index(row, 0)
                self._table._table.setCurrentIndex(index)
                self._table._table.scrollTo(index)
                break

    def _update_glance(self) -> None:
        self._chips.setEnabled(True)
        counts = Counter(e.level.lower() for e in self._source)
        self._chips.set_counts({"all": len(self._source), **{key: counts[key] for key in ("critical", "error", "warning")}})
        limits = self._problems()
        if limits:
            self._unknown_glance("; ".join(limits))
            return
        dated = [e.timestamp for e in self._source if e.timestamp.year >= 2000]
        self._glance_tiles["window"].set(
            f"{min(dated):%Y-%m-%d}" if dated else None,
            f"Oldest: {min(dated):%H:%M}\nNewest: {max(dated):%Y-%m-%d %H:%M}" if dated else "",
            tooltip=(f"Oldest: {min(dated):%Y-%m-%d %H:%M:%S}\nNewest: {max(dated):%Y-%m-%d %H:%M:%S}\nBefore filters."
                     if dated else "No usable loaded timestamps"),
            reason="No usable loaded timestamps")
        for key, role in (("critical", "danger"), ("error", "danger"), ("warning", "warning")):
            self._glance_tiles[key].set(f"{counts[key]:,}", "In loaded window",
                                        severity=role if counts[key] else "ok")
        sources = Counter(e.source or "Unknown source" for e in self._source).most_common(3)
        text = "\n".join(f"{name} ({count:,})" for name, count in sources)
        self._glance_tiles["sources"].set(
            f"{len(set(e.source for e in self._source)):,} sources",
            "\n".join(f"{name[:28]} ({count:,})" for name, count in sources) or "No loaded records",
            tooltip=f"Top three sources by record count in the loaded window:\n{text}\nSelect to clear the severity filter.")
        problems = [e for e in self._source if e.level.lower() in ("critical", "error", "warning")]
        ranked = Counter(e.source or "Unknown source" for e in problems).most_common(3)
        self._glance_tiles["sources"].set(
            f"{len(set(e.source for e in problems)):,}",
            "\n".join(f"{name[:28]} ({count:,})" for name, count in ranked) or "No loaded problems")
        self._glance_tiles["sources"].set_tooltip(
            "Problem sources (critical, errors and warnings):\n"
            + ("\n".join(f"{name} ({count:,})" for name, count in ranked) or "None")
            + "\nAll sources:\n" + text + "\nSelect to clear the severity filter.")
        tile = self._glance_tiles["newest"]
        if not problems:
            tile.set("None", "In loaded window", severity="ok")
            return
        newest = max(problems, key=lambda e: e.timestamp)
        if newest.timestamp.year < 2000:
            tile.set_unreadable("Problem records have no usable timestamp")
            return
        now = datetime.now(newest.timestamp.tzinfo)
        seconds = (now - newest.timestamp).total_seconds()
        age = ("Just now" if seconds < 60 else f"{int(seconds // 60)} min ago") if seconds < 3600 else (
            f"{int(seconds // 3600)} h ago" if seconds < 86400 else f"{int(seconds // 86400)} d ago")
        tile.set(age if seconds >= 0 else "Future dated", newest.level,
                 tooltip=f"{newest.timestamp:%Y-%m-%d %H:%M:%S %Z}\n{newest.source}\nSelect to inspect the newest problem allowed by the current source filters.",
                 severity="warning" if newest.level.lower() == "warning" else "danger")

    def _unknown_glance(self, reason) -> None:
        for tile in self._glance.tiles:
            tile.set_unreadable(reason)
            status = "Read cancelled" if "cancelled" in reason.lower() else "Could not read"
            tile.sub.setText("Reading..." if reason == "Reading this source" else status)
        self._chips.setEnabled(False)

    # -- loading ---------------------------------------------------------
    def load(self, force: bool = False) -> None:
        """Run `loader` on a worker and fill the table with what it returns."""
        if self.loaded and not force:
            return
        self.cancel()

        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._error_banner.clear()
        self._unknown_glance("Reading this source")
        if not self._source:
            self._empty.set_variant("loading", progress_text="Reading this source; restricted records may require administrator rights.")

        worker = Worker(self._loader)
        track(worker)
        worker.signals.progress.connect(lambda value: self._deliver(worker, self._progress.setValue, value))
        worker.signals.result.connect(lambda entries: self._deliver(worker, self._on_result, entries))
        worker.signals.error.connect(lambda error: self._deliver(worker, self._on_error, error))
        worker.signals.cancelled.connect(lambda: self._deliver(worker, self.cancel))
        self._worker = worker
        pool = self._thread_pool
        if pool is None:
            from PyQt6.QtCore import QThreadPool
            pool = QThreadPool.globalInstance()
        pool.start(worker)

    def _deliver(self, worker, callback, *args) -> None:
        if not sip.isdeleted(self) and self._worker is worker:
            callback(*args)

    def cancel(self) -> None:
        was_running = self._worker is not None
        if self._worker is not None:
            retire([self._worker])
            self._worker = None
        if not sip.isdeleted(self):
            self._progress.setVisible(False)
            if not self.loaded:
                self._unknown_glance("Read cancelled or not started; Refresh to retry")
                self._empty.set_variant("empty", reason="Refresh to read this source.")
            elif was_running:
                self._unknown_glance("Refresh cancelled; previous records are still displayed")
                self._chips.setEnabled(bool(self._source))

    def _on_result(self, entries) -> None:
        self._worker = None
        self.loaded = True
        if isinstance(entries, LogReadResult):
            self.set_entries(entries.entries, problems=entries.problems)
        else:
            self.set_entries(entries or [])
        self._update_note()
        self.entries_loaded.emit(self._source)

    def _on_error(self, error_info) -> None:
        self._worker = None
        self.show_error(str(error_info))

    # -- content ---------------------------------------------------------
    def set_entries(self, entries, *, problems=()) -> None:
        if sip.isdeleted(self):
            return
        self._source = list(entries or [])
        self._result_problems = list(problems)
        self._progress.setVisible(False)
        self._update_glance()
        self._update_empty()
        self.refresh_view()

    def _update_empty(self) -> None:
        problems = self._problems()
        title = "No records in this window"
        hint = self._empty_text
        role = "muted"
        if problems:
            title, hint = "Read incomplete", "\n".join(problems)
            role = "statusWarning"
            self._note_btn.setText("Read findings and read limits (incomplete coverage)")
            if not self._source:
                self._unknown_glance(hint)
        else:
            self._note_btn.setText("Read findings and read limits")
        if not problems and self._healthy_empty:
            title = "No crash evidence found"
            hint = "No crash dumps or bugcheck events found in the checked sources. This does not prove the system has never crashed."
            role = "statusSuccess"
        self._empty.title_label.setText(title)
        self._empty.set_variant("empty", reason=hint)
        set_role(self._empty.title_label, role)

    def _problems(self):
        return [*self._result_problems, *(self._read_problems() if self._read_problems else [])]

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
        selected = self._chips.selected()
        if selected and selected != ["all"]:
            self._entries = [e for e in self._entries if e.level.lower() in selected]
        self._table.set_entries(self._entries)
        self._explain_rows()
        # The empty page is for "nothing loaded"; a filter that matches nothing
        # must still show the (empty) table and its count.
        self._stack.setCurrentIndex(_PAGE_TABLE if self._source else _PAGE_EMPTY)

    def _explain_rows(self) -> None:
        from modules.event_viewer.help_text import event_help
        model = self._table._model
        for row in range(model.rowCount()):
            first = model.item(row, 0)
            entry = first.data(Qt.ItemDataRole.UserRole)
            raw = entry.raw or {}
            tip = event_help(raw.get("provider") or entry.source, raw.get("event_id"))
            tip = (tip + "\n" if tip else "") + str(entry.message or "")
            # Windows messages can contain markup-like text: keep them plain.
            tip = "<qt>" + escape(tip).replace("\n", "<br>") + "</qt>"
            for col in range(model.columnCount()):
                model.item(row, col).setToolTip(tip)

    def set_view_transform(self, transform: Optional[Callable[[list], list]]) -> None:
        """Filter/group what is shown without touching what was loaded."""
        self._view_transform = transform
        self.refresh_view()

    def source_entries(self) -> List[object]:
        """Every entry the loader returned, before any view transform."""
        return list(self._source)

    def set_note(self, text: str) -> None:
        self._note.setText(text or "")
        self._note_btn.setVisible(bool(text))
        self._note.setVisible(bool(text) and self._note_btn.isChecked())
        if text:
            self._note_btn.setToolTip(text)

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
        self._unknown_glance(message)
        if not self._source:
            self._empty.title_label.setText("Could not read this source")
            self._empty.set_variant("error", reason=message)

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
