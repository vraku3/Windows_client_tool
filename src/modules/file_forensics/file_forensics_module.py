"""File Forensics: streamed metadata search followed by one process/handle snapshot."""
import csv
import fnmatch
import os
from dataclasses import replace
from typing import List, Optional
from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget)
from core.base_module import BaseModule
from core.events import NOTIFY_BALLOON, BalloonNotifyData
from core.module_groups import ModuleGroup
from core.procengine.actions import end_process
from core.procengine.signatures import COULD_NOT_VERIFY, INVALID, NOT_SIGNED, VALID
from core.procengine.ntquery import system_processes
from core.widget_life import widget_is_valid as _widget_valid
from core.worker import Worker, retire
from core.formatting import format_size
from ui.error_banner import ErrorBanner
from ui.module_intro import module_intro
from . import history_log
from .engine.analysis import FileAnalysis, analyze
from .engine.creator_heuristic import find_creator_candidates
from .engine.bulk_locks import bulk_locks
from .engine.search import search_batches, RESULT_LIMIT
from .engine.selected_details import inspect_selected
from .engine.folder_watcher import FolderWatcher
from .results_model import matches_filter, signature_text
from .search_view import SearchView, FILTERS

_SIGNATURE_LABELS = {VALID: 'Signed', NOT_SIGNED: 'Unsigned', INVALID: 'INVALID', COULD_NOT_VERIFY: 'Could not verify'}


class _WatchBridge(QObject):
    detection_ready = pyqtSignal(object, str)
    search_event = pyqtSignal(object, object)


class FileForensicsModule(SearchView, BaseModule):
    name = 'File Forensics'
    icon = '🔎'
    description = 'Find which program is holding files open and investigate likely creators'
    group = ModuleGroup.TOOLS
    requires_admin = False

    def __init__(self):
        super().__init__()
        self._widget = None
        self._loaded = False
        self._last_skip_count = 0
        self._pending_banner_message = None
        self._watcher = self._watch_worker = self._watch_cb = None
        self._search_worker = None
        self._current_results = []
        self._last_batch = None
        self._detail_cache = {}
        self._detail_busy = set()
        self._watch_patterns = ()
        self._search_generation = 0
        self._watch_bridge = _WatchBridge()
        self._watch_bridge.detection_ready.connect(self._on_watch_detection_ready)
        self._watch_bridge.search_event.connect(self._on_search_event)

    def on_start(self, app):
        self.app = app

    def on_activate(self):
        pass

    def on_deactivate(self):
        self._stop_watch()
        if _widget_valid(self._watch_cb):
            self._watch_cb.setChecked(False)
        self.cancel_all_workers()

    def on_stop(self):
        self.on_deactivate()

    def get_refresh_interval(self) -> Optional[int]:
        return None

    def create_widget(self):
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(module_intro('Find which program created or is holding files in a folder such as %TEMP%. '
            'Searching reads metadata and never changes files. Creator matches are guesses from process start times. '
            'Kill ends a locking process and can lose unsaved work. Signatures and reputation are checked only on request.'))
        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)
        self._info_banner = QLabel()
        self._info_banner.setWordWrap(True)
        from core.table_ui import set_role
        set_role(self._info_banner, "statusInfo")
        self._info_banner.hide()
        layout.addWidget(self._info_banner)
        tabs = QTabWidget()
        for title, builder, tip in (('Search', self._build_search_tab, 'Find files and inspect readable open handles and likely creators.'),
                ('Live Watch', self._build_watch_tab, 'Observe new files without changing them; edit ignore patterns here.'),
                ('History', self._build_history_tab, 'Past searches with completed, stopped or truncated outcomes, and live detections.')):
            index = tabs.addTab(builder(), title)
            tabs.setTabToolTip(index, tip)
        layout.addWidget(tabs, 1)
        from .help_ui import explain_forensics
        explain_forensics(self._widget)
        return self._widget

    def _build_history_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self._history_search_edit = QLineEdit()
        self._history_search_edit.setPlaceholderText('Search history…')
        self._history_search_edit.setToolTip('Filter saved searches and live detections by path, outcome or creator.')
        self._history_search_edit.textChanged.connect(self._on_history_search)
        layout.addWidget(self._history_search_edit)
        self._history_table = QTableWidget(0, 4)
        self._history_table.setHorizontalHeaderLabels(['Path', 'Creator', 'When', 'Outcome'])
        self._history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._history_table.setAlternatingRowColors(True)
        self._history_table.setSortingEnabled(True)
        for i, tip in enumerate(('Searched folder or detected file.', 'Likely creator for a live detection; a heuristic, not proof.',
                                  'Local time this search ended or file was detected.', 'Completed, stopped or truncated at the result limit.')):
            self._history_table.horizontalHeaderItem(i).setToolTip(tip)
        layout.addWidget(self._history_table, 1)
        self._refresh_history()
        return tab

    def _vt_api_key(self):
        return self.app.config.get('virustotal.api_key', '') or '' if self.app and self.app.config else ''

    def _confirm_broad_search(self, folder, name_filter):
        target = os.path.normcase(os.path.abspath(folder))
        profile = os.path.normcase(os.path.expanduser('~'))
        if name_filter or (os.path.dirname(target) != target and target != profile):
            return True
        drive = os.path.splitdrive(target)[0] or target
        box = QMessageBox(QMessageBox.Icon.Question, 'Large folder search',
            f'This lists every file on {drive} (potentially over a million). Add a name filter?', parent=self._widget)
        anyway = box.addButton('Search anyway', QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is anyway

    def _on_search_clicked(self):
        if self._search_worker is not None:
            self._search_worker.cancel()
            self._progress_label.setText('Stopping — keeping matches found so far…')
            return
        folder = os.path.expandvars(self._folder_edit.text().strip())
        if not folder:
            self._on_search_error('Choose a folder first.')
            return
        name_filter = self._filter_edit.text().strip()
        if not self._confirm_broad_search(folder, name_filter):
            return
        self._search_generation += 1
        self._search_folder = folder
        self._last_batch = None
        self._last_skip_count = 0
        self._detail_cache.clear()
        self._error_banner.clear()
        self._info_banner.hide()
        self._populate_table([])
        self._on_row_selected()
        self._search_btn.setText('Stop')
        self._progress_label.setText('Scanning folders…')
        worker = Worker(self._run_search, folder, name_filter, self._recurse_cb.isChecked())
        self._search_worker = worker
        worker.signals.result.connect(lambda result, w=worker: self._search_done(w, result))
        worker.signals.error.connect(lambda error, w=worker: self._search_failed(w, error))
        worker.signals.cancelled.connect(lambda w=worker: self._search_cancelled(w))
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _analyze_folder(self, folder, name_filter, recurse, worker=None):
        rows = []
        cancelled = (lambda: worker.is_cancelled) if worker else (lambda: False)
        for batch in search_batches(folder, name_filter, recurse, cancelled=cancelled):
            rows.extend(batch.rows)
            if worker:
                self._watch_bridge.search_event.emit(worker, ('batch', batch))
        if rows and not cancelled():
            if worker:
                self._watch_bridge.search_event.emit(worker, ('phase', None))
            processes = list(system_processes())
            locks, summaries = bulk_locks([r.metadata.path for r in rows], processes, cancelled)
            cache = {}
            enriched = []
            for row in rows:
                if cancelled():
                    break
                enriched.append(replace(row, locking_processes=locks[row.metadata.path],
                    locking_summary=summaries[row.metadata.path],
                    creator_candidates=find_creator_candidates(row.metadata.created, processes=processes, details_cache=cache)))
                if len(enriched) == 128:
                    if worker:
                        self._watch_bridge.search_event.emit(worker, ('enriched', tuple(enriched)))
                    enriched.clear()
            if enriched and worker:
                self._watch_bridge.search_event.emit(worker, ('enriched', tuple(enriched)))
        return rows

    def _run_search(self, worker, folder, name_filter, recurse):
        return self._analyze_folder(folder, name_filter, recurse, worker=worker)

    def _on_search_event(self, worker, event):
        if worker is not self._search_worker or not _widget_valid(self._widget):
            return
        kind, payload = event
        if kind == 'batch':
            self._last_batch = payload
            self._last_skip_count = payload.skipped
            self._model.append(payload.rows)
            self._current_results = self._model.rows
            self._results_stack.setCurrentIndex(0 if self._current_results else 1)
            self._progress_label.setText(f'Scanned {payload.folders:,} folders, {payload.files:,} files, {payload.matches:,} matches; {payload.skipped:,} skipped')
        elif kind == 'phase':
            self._progress_label.setText(self._progress_label.text() + ' — checking open handles once…')
        elif kind == 'enriched':
            updated = []
            for row in payload:
                index = self._model.by_path.get(row.metadata.path)
                existing = self._model.rows[index] if index is not None else None
                if existing and row.metadata.path in self._detail_cache:
                    row = replace(row, own_signature=existing.own_signature,
                                  top_creator_signature=existing.top_creator_signature, reputation=existing.reputation)
                updated.append(row)
            self._model.update_rows(updated)
            selected = self._selected_analysis()
            if selected and selected.locking_processes:
                self._detail_cache.get(selected.metadata.path, {}).pop('metadata', None)
            self._on_row_selected()
        self._update_counts()

    def _search_done(self, worker, results):
        if worker is not self._search_worker:
            return
        if self._last_batch is None:
            self._populate_table(results)
        outcome = self._last_batch.outcome if self._last_batch else 'completed'
        self._finish_search(outcome)

    def _search_cancelled(self, worker):
        if worker is self._search_worker:
            self._finish_search('stopped')

    def _search_failed(self, worker, message):
        if worker is self._search_worker:
            pending = self._pending_banner_message
            self._finish_search('failed')
            self._on_search_error(f'{pending} Also: {message}' if pending else message)

    def _finish_search(self, outcome):
        self._search_worker = None
        if not _widget_valid(self._search_btn):
            return
        self._search_btn.setText('Search')
        self._search_btn.setEnabled(True)
        lead = 'Stopped' if outcome == 'stopped' else outcome.capitalize()
        progress = f'{lead} - {len(self._current_results):,} matches so far; {self._last_skip_count:,} skipped'
        if self._last_batch:
            progress += f'; scanned {self._last_batch.folders:,} folders, {self._last_batch.files:,} files'
        self._progress_label.setText(progress)
        messages = []
        if outcome == 'truncated':
            self._info_banner.setText(f'Showing the first {RESULT_LIMIT:,} matches -- narrow the folder or add a name filter.')
            self._info_banner.show()
        if self._last_skip_count:
            messages.append(f'{self._last_skip_count:,} folders/files could not be read or were links and were skipped.')
        if self._pending_banner_message:
            messages.append(self._pending_banner_message)
            self._pending_banner_message = None
        if messages:
            self._error_banner.set_error(' '.join(messages))
        self._on_row_selected()
        try:
            history_log.record({'path': self._search_folder, 'outcome': outcome,
                                'matches': len(self._current_results), 'skipped': self._last_skip_count})
            self._refresh_history()
        except OSError as error:
            self._error_banner.set_error(' '.join(messages) + f' History could not be saved: {error}')

    def _on_search_result(self, results):
        self._populate_table(results)
        self._search_btn.setEnabled(True)

    def _on_search_error(self, message):
        if not _widget_valid(getattr(self, '_search_btn', None)):
            return
        self._search_btn.setEnabled(True)
        self._search_btn.setText('Search')
        if self._pending_banner_message:
            message = f'{self._pending_banner_message} Also: {message}'
            self._pending_banner_message = None
        self._error_banner.set_error(message)

    def _populate_table(self, results):
        self._model.replace(results)
        self._current_results = self._model.rows
        self._results_stack.setCurrentIndex(0 if results else 1)
        self._update_counts()

    def _selected_analysis(self):
        index = self._proxy.mapToSource(self._table.currentIndex())
        return self._model.rows[index.row()] if index.isValid() else None

    def _filter_results(self, kind):
        self._proxy.set_kind(kind)
        self._update_counts()
        for key, (button, _) in self._chips.items():
            button.setChecked(key == kind)
        if kind == 'skipped':
            self._error_banner.set_error(f'{self._last_skip_count:,} skipped folders/files; unreadable items and directory links have no result rows.')

    def _update_counts(self):
        counts = {key: sum(matches_filter(r, key) for r in self._current_results) for key, _ in FILTERS}
        for key, (button, label) in self._chips.items():
            button.setText(f'{label} ({counts[key]:,})')
        for key, (button, label) in self._tiles.items():
            value = format_size(sum(r.metadata.size for r in self._current_results)) if key == 'size' else self._last_skip_count if key == 'skipped' else counts[key]
            pending = key == 'locked' and any(r.locking_summary == 'Not checked yet' for r in self._current_results)
            if pending:
                button.set(f'{value} observed', 'Inspection incomplete')
            else:
                button.set(str(value), 'Checked signatures only' if key == 'unsigned' else 'Click to filter')

    def _on_row_selected(self):
        if not _widget_valid(getattr(self, '_detail_label', None)):
            return
        row = self._selected_analysis()
        for button in (self._copy_btn, self._reveal_btn, self._signature_btn):
            button.setEnabled(row is not None)
        self._kill_btn.setEnabled(bool(row and row.locking_processes and self._search_worker is None))
        self._reputation_btn.setEnabled(bool(row and self._vt_api_key()))
        if not row:
            self._detail_label.setPlainText('Select a row to see details.')
            return
        self._path_label.setPath(row.metadata.path)
        meta = row.metadata
        lines = [f'Size: {format_size(meta.size)}', f'Created: {meta.created:%Y-%m-%d %H:%M:%S}', f'Modified: {meta.modified:%Y-%m-%d %H:%M:%S}',
                 f'Owner: {meta.owner or "Could not check"}', f'Read only: {"Yes" if meta.read_only else "No"}', row.locking_summary]
        lines.extend(f'Open: {p.process} (PID {p.pid})' for p in row.locking_processes)
        lines.append('Creator candidates — guess from start time, not proof:')
        lines.extend(f'{i+1}. {c.name} (PID {c.pid}), user: {c.user or c.user_error or "could not check"}; '
                     f'started {c.delta_seconds:+.1f}s from file creation; {c.path or c.path_error or "path unavailable"}'
                     for i, c in enumerate(row.creator_candidates))
        lines.append('Creator signature: ' + signature_text(row.top_creator_signature))
        if row.own_signature:
            lines.append(f'This file signature: {signature_text(row.own_signature)}; {row.own_signature.signer or ""}; {row.own_signature.reason or ""}')
        cached = self._detail_cache.get(meta.path, {})
        lines.extend(note for notes in cached.values() for note in notes)
        if 'signatures' not in cached:
            lines.append('File and creator signatures: not checked yet. Use Check signatures.')
        if 'reputation' not in cached:
            lines.append('SHA-256 / VirusTotal: not checked yet.' if self._vt_api_key() else 'Set virustotal.api_key in Settings to enable Check reputation.')
        self._detail_label.setPlainText('\n\n'.join(lines))
        if 'metadata' not in cached:
            self._start_details('metadata')

    def _check_signatures(self):
        self._start_details('signatures')

    def _check_reputation(self):
        self._start_details('reputation')

    def _start_details(self, mode):
        row = self._selected_analysis()
        if not row or (self._search_generation, row.metadata.path, mode) in self._detail_busy:
            return
        key = (self._search_generation, row.metadata.path, mode)
        self._detail_busy.add(key)
        api_key = self._vt_api_key()
        worker = Worker(lambda w: inspect_selected(row, mode, api_key))
        generation = self._search_generation
        worker.signals.result.connect(lambda result: self._details_ready(result) if generation == self._search_generation else None)
        worker.signals.error.connect(lambda error: self._details_failed(row.metadata.path, mode, error, generation))
        worker.signals.finished.connect(lambda: self._detail_finished(key))
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def _detail_finished(self, key):
        self._detail_busy.discard(key)
        generation, path, mode = key
        if mode == 'metadata' and generation == self._search_generation and _widget_valid(self._widget):
            self._on_row_selected()

    def _details_failed(self, path, mode, error, generation):
        if generation == self._search_generation and _widget_valid(self._widget):
            self._detail_cache.setdefault(path, {})[mode] = (f'{mode.capitalize()} could not be checked: {error}',)
            self._error_banner.set_error(f'Selected file: {error}')
            self._on_row_selected()

    def _details_ready(self, payload):
        if not _widget_valid(self._widget):
            return
        row, mode, notes = payload
        current = next((r for r in self._model.rows if r.metadata.path == row.metadata.path), None)
        if current is None:
            return
        if mode == 'metadata' and row.locking_processes != current.locking_processes:
            return  # The shared snapshot arrived during this metadata read; inspect the new lock list.
        updated = replace(current, metadata=row.metadata)
        if mode == 'signatures':
            updated = replace(updated, own_signature=row.own_signature, top_creator_signature=row.top_creator_signature)
        else:
            updated = replace(updated, reputation=row.reputation)
        self._detail_cache.setdefault(row.metadata.path, {})[mode] = notes
        self._model.update_row(updated)
        self._update_counts()
        self._on_row_selected()

    def _on_kill_locking_clicked(self) -> None:
        analysis = self._selected_analysis()
        if not analysis or not analysis.locking_processes:
            return
        proc = analysis.locking_processes[0]
        import psutil
        try:
            # A PID alone is not an identity: Windows can hand it to another
            # program while this dialog is open. Checked again before ending.
            created = psutil.Process(proc.pid).create_time()
        except psutil.Error:
            created = None
        reply = QMessageBox.question(
            self._widget, "Kill Locking Process",
            f"Kill '{proc.process}' (PID {proc.pid})?\n\n"
            "This ends the process immediately. Unsaved work may be lost. It cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # An identity that could not be read is not a match: nothing is
            # ended unless this is provably the process named in the dialog.
            try:
                same = (created is not None
                        and abs(psutil.Process(proc.pid).create_time() - created) < 0.5)
            except psutil.Error:
                same = False
            if not same:
                self._pending_banner_message = (f"'{proc.process}' (PID {proc.pid}) has exited or "
                                                "could not be identified -- nothing was ended.")
                self._on_search_clicked()
                return
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

    def _on_export_clicked(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self._widget, "Export Results", "file_forensics_export.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Path", "Size", "Created", "Owner", "Locked By",
                              "Likely Creator", "File Signature", "Modified", "Creator signed", "Lock inspection"])
            for analysis in self._current_results:
                meta = analysis.metadata
                locked = ", ".join(p.process for p in analysis.locking_processes)
                creator = (analysis.creator_candidates[0].name
                          if analysis.creator_candidates else "")
                sig = analysis.own_signature
                sig_text = _SIGNATURE_LABELS.get(sig.status, "") if sig else ""
                writer.writerow([meta.path, meta.size, meta.created, meta.owner,
                                 locked, creator, sig_text, meta.modified, signature_text(analysis.top_creator_signature),
                                 analysis.locking_summary])

    def _refresh_history(self) -> None:
        entries = history_log.recent(limit=50)
        self._fill_history_table(entries)
        self._report_history_load_failure()

    def _on_history_search(self) -> None:
        query = self._history_search_edit.text().strip()
        entries = history_log.search(query) if query else history_log.recent(limit=50)
        self._fill_history_table(entries)
        self._report_history_load_failure()

    def _report_history_load_failure(self) -> None:
        """`history_log._load()` swallows a corrupted-or-unreadable history
        file into a plain `[]` -- identical, from `recent()`/`search()`'s
        return value alone, to a genuinely empty history. `last_load_ok()`
        is what tells the two apart, so a real read/parse failure surfaces
        here rather than looking like "no history yet". Only ever SETS the
        banner, never clears it -- the same banner is shared with the Search
        tab (it lives in the outer layout above both tabs), and a clean
        history load has no business dismissing an unrelated Search-tab
        error still on screen."""
        if not history_log.last_load_ok() and _widget_valid(self._error_banner):
            self._error_banner.set_error(
                "History could not be loaded -- the log file may be corrupted or unreadable."
            )

    def _fill_history_table(self, entries: List[dict]) -> None:
        self._history_table.setSortingEnabled(False)
        self._history_table.setRowCount(0)
        for entry in entries:
            row = self._history_table.rowCount()
            self._history_table.insertRow(row)
            self._history_table.setItem(row, 0, QTableWidgetItem(entry.get("path", "")))
            self._history_table.setItem(row, 1, QTableWidgetItem(entry.get("creator", "")))
            self._history_table.setItem(row, 2, QTableWidgetItem(entry.get("at", "")))
            self._history_table.setItem(row, 3, QTableWidgetItem(entry.get("outcome", "Live detection")))
        self._history_table.setSortingEnabled(True)

    def _ignore_patterns(self) -> List[str]:
        raw = self._ignore_edit.text().strip()
        return [p.strip() for p in raw.split(";") if p.strip()]

    def _is_ignored(self, path: str) -> bool:
        name = os.path.basename(path)
        return any(fnmatch.fnmatch(name, pat) for pat in self._watch_patterns)

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
                retire([self._watch_worker])
                self._workers.remove(self._watch_worker)
        self._watcher = None
        self._watch_worker = None

    def _analyze_watch(self, path):
        from .engine.search import fast_analysis
        result = fast_analysis(path, os.stat(path, follow_symlinks=False))
        return replace(result, creator_candidates=find_creator_candidates(result.metadata.created))

    def _on_watched_file_created(self, path: str) -> None:
        """Metadata and creator heuristics run on the watch thread; signals deliver rows to Qt."""
        if self._is_ignored(path):
            return
        try:
            result = self._analyze_watch(path)
        except (FileNotFoundError, PermissionError, OSError) as error:
            import logging
            logging.getLogger(__name__).warning("Live file could not be inspected: %s", error)
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
        self._watch_feed.insertItem(0, f"{result.metadata.path} - {creator or 'no creator candidate'}")
        if self._watch_feed.count() > RESULT_LIMIT:
            self._watch_feed.takeItem(self._watch_feed.count() - 1)
        if self._search_worker is None and len(self._current_results) < RESULT_LIMIT:
            self._model.append([result])
            self._current_results = self._model.rows
            self._results_stack.setCurrentIndex(0)
            self._update_counts()
        # history_log.record() already ran on the watch worker's background
        # thread inside _on_watched_file_created (safe -- no Qt objects
        # involved), but _history_table IS a Qt widget, so the refresh that
        # reads it back must happen here instead, now that execution has
        # crossed back onto the UI thread via _watch_bridge's signal -- see
        # this module's own "Cross-thread widget access" rule.
        if _widget_valid(self._history_table):
            self._refresh_history()
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
