"""File Forensics -- find a file, see who has it open, guess who created
it. A redesigned, professional rebuild of the user's own
Find-FileCreator.ps1, hosted as the first tab of the new Scripts hub.

Built entirely on this app's existing process-forensics engine
(core.procengine) rather than reimplementing any of it -- see
docs/superpowers/specs/2026-09-18-scripts-file-forensics-design.md.
"""
import fnmatch
import os
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from core.base_module import BaseModule
from core.events import NOTIFY_BALLOON, BalloonNotifyData
from core.module_groups import ModuleGroup
from core.procengine.actions import end_process
from core.procengine.signatures import COULD_NOT_VERIFY, INVALID, NOT_SIGNED
from core.widget_life import widget_is_valid as _widget_valid
from core.worker import Worker
from ui.empty_state import EmptyState
from ui.error_banner import ErrorBanner

from . import history_log
from .engine.analysis import FileAnalysis, analyze
from .engine.folder_watcher import FolderWatcher

_COLUMNS = ["Name", "Path", "Size", "Created", "Owner", "Locked By", "Likely Creator"]

_LOCKED_COLOR = QColor("#5c4a1a")        # amber -- in use
_CREATOR_HIGH_COLOR = QColor("#1a5c2a")  # green -- confident single match
_CREATOR_LOW_COLOR = QColor("#5c4a1a")   # amber -- ambiguous/multiple


class _WatchBridge(QObject):
    """Marshals a live-watch detection back onto the UI thread.

    `FolderWatcher.run()` executes on a background Worker thread, and its
    `on_created` callback runs there too -- so the heavy part (`analyze()`,
    including a real VirusTotal network call) stays off the UI thread, but
    the result must cross back via a signal before anything touches a Qt
    widget or QApplication. See CLAUDE.md's "Cross-thread widget access --
    CRITICAL" rule. A signal needs a QObject to live on, and BaseModule
    is not one, hence this small helper rather than a signal on the module
    itself."""
    detection_ready = pyqtSignal(object, str)  # (FileAnalysis, creator)


class FileForensicsModule(BaseModule):
    name = "File Forensics"
    icon = "🔎"
    description = "Find a file, see who has it open, and who created it"
    group = ModuleGroup.TOOLS
    requires_admin = False

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._loaded = False
        self._last_skip_count = 0
        self._pending_banner_message: Optional[str] = None
        self._watcher: Optional[FolderWatcher] = None
        self._watch_worker: Optional[Worker] = None
        self._watch_bridge = _WatchBridge()
        self._watch_bridge.detection_ready.connect(self._on_watch_detection_ready)

    def on_start(self, app) -> None:
        self.app = app

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        self._stop_watch()
        if _widget_valid(self._watch_cb):
            # Reflect reality on the module's own primary control -- without
            # this the checkbox reads "Live Watch: ON" indefinitely after any
            # tab-away-and-back even though _stop_watch() just tore down the
            # actual watch (BaseModule's lifecycle calls on_deactivate() on
            # every navigation away, not just app shutdown).
            self._watch_cb.setChecked(False)
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self._stop_watch()
        self.cancel_all_workers()

    def get_refresh_interval(self):
        return None

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        layout.addLayout(self._build_toolbar())

        self._results_stack = QStackedWidget()
        self._table = self._build_table()
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._results_stack.addWidget(self._table)
        self._empty = EmptyState(
            "🔎", "No results yet",
            "Enter a folder and click Search, or enable Live Watch.",
        )
        self._results_stack.addWidget(self._empty)
        self._results_stack.setCurrentIndex(1)
        layout.addWidget(self._results_stack, 1)

        self._detail_label = QLabel("Select a row to see details.")
        self._detail_label.setWordWrap(True)
        layout.addWidget(self._detail_label)

        actions = QHBoxLayout()
        self._kill_btn = QPushButton("Kill Locking Process")
        self._kill_btn.setEnabled(False)
        self._kill_btn.clicked.connect(self._on_kill_locking_clicked)
        actions.addWidget(self._kill_btn)
        self._reveal_btn = QPushButton("Reveal in Explorer")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._on_reveal_clicked)
        actions.addWidget(self._reveal_btn)
        self._copy_btn = QPushButton("Copy Path")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._on_copy_path_clicked)
        actions.addWidget(self._copy_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self._current_results: List[FileAnalysis] = []
        return self._widget

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Folder:"))
        self._folder_edit = QLineEdit(os.environ.get("TEMP", ""))
        toolbar.addWidget(self._folder_edit, 1)
        toolbar.addWidget(QLabel("Name contains:"))
        self._filter_edit = QLineEdit()
        toolbar.addWidget(self._filter_edit)
        self._recurse_cb = QCheckBox("Recurse")
        self._recurse_cb.setChecked(True)
        toolbar.addWidget(self._recurse_cb)
        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self._on_search_clicked)
        toolbar.addWidget(self._search_btn)
        self._watch_cb = QCheckBox("Live Watch")
        self._watch_cb.toggled.connect(self._on_watch_toggled)
        toolbar.addWidget(self._watch_cb)
        toolbar.addWidget(QLabel("Ignore:"))
        self._ignore_edit = QLineEdit("*.tmp;~$*")
        self._ignore_edit.setMaximumWidth(120)
        toolbar.addWidget(self._ignore_edit)
        return toolbar

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(_COLUMNS))
        table.setHorizontalHeaderLabels(_COLUMNS)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        return table

    def _analyze_folder(self, folder: str, name_filter: str, recurse: bool
                        ) -> List[FileAnalysis]:
        """Walk `folder` for names containing `name_filter`, analyzing
        each match. Raises FileNotFoundError / NotADirectoryError for the
        UI to turn into an ErrorBanner -- never swallowed, for a refusal
        to list the TARGET folder itself.

        A refusal to list some OTHER directory encountered mid-walk (an
        ordinary occurrence on a real Windows volume --
        `System Volume Information`, `$RECYCLE.BIN`, another user's
        profile subfolder) is a different, expected case: it is skipped
        and counted, the same as a per-file refusal below, rather than
        aborting a search that has already found real results elsewhere
        in the tree. Only the top-level folder failing to list is treated
        as a refusal worth surfacing as an error -- the user explicitly
        pointed the tool there, and `isdir()` succeeding only proves it is
        traversable, not listable (a separate Windows ACL permission), so
        without this `os.walk`'s default `onerror=None` would otherwise
        silently swallow that one and report "nothing found".

        Either kind of refusal (a skipped subdirectory, or a skipped file
        that vanished or became unreadable between listing and analysis)
        adds to `self._last_skip_count`, which `_on_search_clicked`
        discloses rather than ever dropping on the floor."""
        if not os.path.isdir(folder):
            raise FileNotFoundError(f"'{folder}' is not a folder.")
        results = []
        skipped = 0
        if recurse:
            target = os.path.normcase(os.path.abspath(folder))

            def _on_walk_error(error: OSError) -> None:
                nonlocal skipped
                failed_dir = os.path.normcase(os.path.abspath(error.filename or ""))
                if failed_dir == target:
                    raise error  # the target folder itself can't be listed
                skipped += 1  # an ordinary unlistable subdirectory -- skip it

            walker = os.walk(folder, onerror=_on_walk_error)
        else:
            # os.listdir() returns subdirectories too; os.walk() already
            # separates them into _dirs, but the single-level case must
            # filter by hand or a subfolder gets analyzed as if it were a
            # file (os.stat() succeeds on a directory too).
            only_files = [f for f in os.listdir(folder)
                         if os.path.isfile(os.path.join(folder, f))]
            walker = [(folder, [], only_files)]
        for root, _dirs, files in walker:
            for filename in files:
                if name_filter and name_filter.lower() not in filename.lower():
                    continue
                path = os.path.join(root, filename)
                try:
                    results.append(analyze(path, vt_api_key=self._vt_api_key()))
                except (FileNotFoundError, PermissionError, OSError):
                    skipped += 1
                    continue  # gone or unreadable between listing and analysis
        self._last_skip_count = skipped
        return results

    def _vt_api_key(self) -> str:
        if self.app and self.app.config:
            return self.app.config.get("virustotal.api_key", "") or ""
        return ""

    def _on_search_clicked(self) -> None:
        folder = self._folder_edit.text().strip()
        name_filter = self._filter_edit.text().strip()
        recurse = self._recurse_cb.isChecked()
        self._last_skip_count = 0
        self._search_btn.setEnabled(False)
        worker = Worker(self._run_search, folder, name_filter, recurse)
        worker.signals.result.connect(self._on_search_result)
        worker.signals.error.connect(self._on_search_error)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _run_search(self, worker, folder: str, name_filter: str, recurse: bool
                    ) -> List[FileAnalysis]:
        """The Worker function. `FileNotFoundError`/`PermissionError`/`OSError`
        raised by `_analyze_folder` for an unlistable TARGET folder are
        deliberately NOT caught here -- they propagate out so `Worker.run()`'s
        own exception handling catches them and emits `signals.error`, which
        `_on_search_error` turns into the ErrorBanner. A per-file or
        per-subdirectory refusal mid-walk is a different, already-handled
        case inside `_analyze_folder` itself (see its own docstring)."""
        return self._analyze_folder(folder, name_filter, recurse)

    def _on_search_result(self, results: List[FileAnalysis]) -> None:
        if not _widget_valid(self._search_btn):
            return
        self._search_btn.setEnabled(True)
        if self._pending_banner_message:
            # A kill-locking-process failure queued a refresh via
            # _on_search_clicked() and is waiting for THIS refresh to land
            # before it can safely show its own message -- see
            # _on_kill_locking_clicked's comment on why a plain "clear the
            # banner on a clean search" can no longer run first now that the
            # search is asynchronous.
            self._error_banner.set_error(self._pending_banner_message)
            self._pending_banner_message = None
        elif self._last_skip_count:
            # A result set can be non-empty AND carry a disclosed refusal
            # at once -- showing the table is not a reason to hide that
            # some items could not be inspected. "item(s)" covers both a
            # file that vanished/became unreadable and a subdirectory
            # `os.walk` could not list along the way.
            self._error_banner.set_error(
                f"{self._last_skip_count} item(s) could not be analyzed and were skipped."
            )
        else:
            self._error_banner.clear()
        self._populate_table(results)

    def _on_search_error(self, message: str) -> None:
        if not _widget_valid(self._search_btn):
            return
        self._search_btn.setEnabled(True)
        if self._pending_banner_message:
            # A kill-locking-process failure was waiting for this refresh to
            # show it (see _on_kill_locking_clicked), and the refresh ITSELF
            # also failed -- neither refusal may be dropped in favor of the
            # other (the same rule this file already applies to skipped
            # files), so both are shown together and the pending one is only
            # cleared now that it has actually been displayed.
            message = f"{self._pending_banner_message} Also: {message}"
            self._pending_banner_message = None
        self._error_banner.set_error(message)

    def _populate_table(self, results: List[FileAnalysis]) -> None:
        self._current_results = results
        self._table.setRowCount(0)
        for analysis in results:
            row = self._table.rowCount()
            self._table.insertRow(row)
            meta = analysis.metadata
            self._table.setItem(row, 0, QTableWidgetItem(os.path.basename(meta.path)))
            self._table.setItem(row, 1, QTableWidgetItem(meta.path))
            self._table.setItem(row, 2, QTableWidgetItem(str(meta.size)))
            self._table.setItem(row, 3, QTableWidgetItem(str(meta.created)))
            self._table.setItem(row, 4, QTableWidgetItem(meta.owner))

            locked_text = ", ".join(p.process for p in analysis.locking_processes)
            locked_item = QTableWidgetItem(locked_text)
            if analysis.locking_processes:
                locked_item.setBackground(_LOCKED_COLOR)
            self._table.setItem(row, 5, locked_item)

            creator_text = (analysis.creator_candidates[0].name
                            if analysis.creator_candidates else "")
            creator_item = QTableWidgetItem(creator_text)
            if len(analysis.creator_candidates) == 1:
                creator_item.setBackground(_CREATOR_HIGH_COLOR)
            elif len(analysis.creator_candidates) > 1:
                creator_item.setBackground(_CREATOR_LOW_COLOR)
            self._table.setItem(row, 6, creator_item)
        if not _widget_valid(self._results_stack):
            return
        self._results_stack.setCurrentIndex(0 if results else 1)

    def _selected_analysis(self) -> Optional[FileAnalysis]:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._current_results):
            return None
        return self._current_results[row]

    def _on_row_selected(self) -> None:
        analysis = self._selected_analysis()
        has_lock = bool(analysis and analysis.locking_processes)
        self._kill_btn.setEnabled(has_lock)
        self._reveal_btn.setEnabled(analysis is not None)
        self._copy_btn.setEnabled(analysis is not None)
        if not analysis:
            self._detail_label.setText("Select a row to see details.")
            return

        lines = [f"Path: {analysis.metadata.path}", f"Owner: {analysis.metadata.owner}"]
        if analysis.locking_processes:
            lines.append("Locked by:")
            for p in analysis.locking_processes:
                lines.append(f"  {p.process} (PID {p.pid})")
        else:
            lines.append("Not currently open by any process.")
        lines.append(analysis.locking_summary)
        if analysis.creator_candidates:
            lines.append("Creator candidates:")
            for c in analysis.creator_candidates:
                lines.append(f"  {c.name} (PID {c.pid}, Δ{c.delta_seconds:+.1f}s)")
            sig = analysis.top_creator_signature
            # Three distinct answers, never collapsed into one -- see
            # analysis.py's own docstring on why this field carries the
            # whole SignatureFacts rather than just a bool.
            if sig is not None and sig.status == NOT_SIGNED:
                lines.append("⚠ Likely creator is UNSIGNED.")
            elif sig is not None and sig.status == INVALID:
                lines.append(f"⚠ Likely creator's signature is INVALID: {sig.reason or ''}".rstrip())
            elif sig is not None and sig.status == COULD_NOT_VERIFY:
                lines.append(f"Signature could not be verified: {sig.reason or ''}".rstrip())
        else:
            lines.append("No process started close enough to the file's creation time.")
        self._detail_label.setText("\n".join(lines))

    def _on_kill_locking_clicked(self) -> None:
        analysis = self._selected_analysis()
        if not analysis or not analysis.locking_processes:
            return
        proc = analysis.locking_processes[0]
        reply = QMessageBox.question(
            self._widget, "Kill Locking Process",
            f"Kill '{proc.process}' (PID {proc.pid})?\n\n"
            "This ends the process immediately and cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            result = end_process(proc.pid)
            # The refresh now runs on a Worker (search is no longer
            # synchronous), so there is no "call refresh, then overwrite its
            # banner" ordering to rely on -- the refresh's own result can
            # land after this method has already returned. Stash the
            # failure and let _on_search_result apply it once the refresh
            # actually completes, instead of a plain clear silently
            # overwriting it.
            if not result.ok:
                self._pending_banner_message = result.message
            self._on_search_clicked()

    def _on_reveal_clicked(self) -> None:
        analysis = self._selected_analysis()
        if analysis:
            import subprocess
            subprocess.Popen(["explorer", "/select,", analysis.metadata.path])

    def _on_copy_path_clicked(self) -> None:
        analysis = self._selected_analysis()
        if analysis:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(analysis.metadata.path)

    def _ignore_patterns(self) -> List[str]:
        raw = self._ignore_edit.text().strip()
        return [p.strip() for p in raw.split(";") if p.strip()]

    def _is_ignored(self, path: str) -> bool:
        name = os.path.basename(path)
        return any(fnmatch.fnmatch(name, pat) for pat in self._ignore_patterns())

    def _on_watch_toggled(self, checked: bool) -> None:
        if checked:
            if self._watcher is not None:
                return  # already watching -- e.g. setChecked() firing the
                        # toggled signal AND an explicit caller both landing here
            folder = self._folder_edit.text().strip()
            self._watcher = FolderWatcher(
                folder, on_created=self._on_watched_file_created,
                recursive=self._recurse_cb.isChecked(),
            )
            self._watch_worker = Worker(self._watcher.run)
            self._watch_worker.signals.error.connect(self._on_watch_error)
            self._workers.append(self._watch_worker)
            self.thread_pool.start(self._watch_worker)
        else:
            self._stop_watch()

    def _on_watch_error(self, message: str) -> None:
        """`FolderWatcher.run()` raised -- e.g. `CreateFile` failing on an
        empty, invalid, or removed folder, a disconnected network share, or
        a revoked ACL mid-watch (see engine/folder_watcher.py's own
        docstring). Nothing validates the folder before starting a watch
        (unlike search), so this is trivially reachable on the very first
        click with a bad path. Without this connected, `Worker.run()`'s own
        exception handling emits `signals.error` into the void: the
        checkbox stays checked and `self._watcher` stays non-None with no
        actual watch running, and the idempotency guard in
        `_on_watch_toggled` would then block a retry."""
        if _widget_valid(self._error_banner):
            self._error_banner.set_error(f"Live Watch stopped: {message}")
        if _widget_valid(self._watch_cb):
            # Reflects reality -- watching actually stopped -- and also
            # fires _on_watch_toggled(False) -> _stop_watch() via the
            # checkbox's own toggled signal.
            self._watch_cb.setChecked(False)
        self._stop_watch()  # always run directly too: harmless if the
                            # checkbox above already did it (idempotent),
                            # and the only path if the widget is gone.

    def _stop_watch(self) -> None:
        """Stops a running live watch, if any. Called on toggle-off,
        on_deactivate and on_stop alike.

        `FolderWatcher.stop()` must be called explicitly here --
        `worker.cancel()` alone only sets a flag `FolderWatcher.run()`'s
        blocking native wait never looks at; `.stop()` is what signals the
        real Win32 event that wait is actually listening on. See
        engine/folder_watcher.py's own docstring."""
        if self._watcher is not None:
            self._watcher.stop()
        if self._watch_worker is not None:
            self._watch_worker.cancel()
            if self._watch_worker in self._workers:
                self._workers.remove(self._watch_worker)
        self._watcher = None
        self._watch_worker = None

    def _on_watched_file_created(self, path: str) -> None:
        """`FolderWatcher`'s `on_created` callback -- runs on the watch
        Worker's background thread, not the UI thread. `analyze()` and
        `history_log.record()` are safe to do here (no Qt objects); the
        result is handed to `_watch_bridge` to cross back to the UI thread
        before anything Qt-related happens (`_on_watch_detection_ready`)."""
        if self._is_ignored(path):
            return
        try:
            result = analyze(path, vt_api_key=self._vt_api_key())
        except (FileNotFoundError, PermissionError, OSError):
            return  # gone or unreadable by the time we got to it
        creator = result.creator_candidates[0].name if result.creator_candidates else ""
        history_log.record({
            "path": result.metadata.path, "creator": creator,
            "locked_by": ", ".join(p.process for p in result.locking_processes),
        })
        self._watch_bridge.detection_ready.emit(result, creator)

    def _on_watch_detection_ready(self, result: FileAnalysis, creator: str) -> None:
        """Runs on the UI thread (queued there by `_watch_bridge` when the
        emit came from the watch worker thread; a direct call, as in tests,
        runs synchronously since sender and receiver share a thread)."""
        if not _widget_valid(self._table):
            return
        self._current_results = self._current_results + [result]
        self._populate_table(self._current_results)
        self._maybe_notify(result, creator)

    def _maybe_notify(self, result: FileAnalysis, creator: str) -> None:
        from PyQt6.QtWidgets import QApplication
        if QApplication.activeWindow() is not None:
            return  # app is in the foreground -- the table update is enough
        if not self.app:
            return
        message = f"New file: {os.path.basename(result.metadata.path)}"
        if creator:
            message += f" (likely by {creator})"
        self.app.event_bus.publish(NOTIFY_BALLOON, BalloonNotifyData(
            title="File Forensics", message=message))
