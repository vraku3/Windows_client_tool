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

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.procengine.actions import end_process
from core.procengine.signatures import COULD_NOT_VERIFY, INVALID, NOT_SIGNED
from core.widget_life import widget_is_valid as _widget_valid
from core.worker import Worker
from ui.empty_state import EmptyState
from ui.error_banner import ErrorBanner

from .engine.analysis import FileAnalysis, analyze

_COLUMNS = ["Name", "Path", "Size", "Created", "Owner", "Locked By", "Likely Creator"]

_LOCKED_COLOR = QColor("#5c4a1a")        # amber -- in use
_CREATOR_HIGH_COLOR = QColor("#1a5c2a")  # green -- confident single match
_CREATOR_LOW_COLOR = QColor("#5c4a1a")   # amber -- ambiguous/multiple


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

    def on_start(self, app) -> None:
        self.app = app

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_stop(self) -> None:
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
        try:
            results = self._analyze_folder(folder, name_filter, recurse)
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as e:
            self._error_banner.set_error(str(e))
            return
        if self._last_skip_count:
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
            # Refresh FIRST, then apply the kill outcome to the banner --
            # _on_search_clicked() unconditionally clears the banner on a
            # clean (no-skip) search, which would silently wipe out a kill
            # failure message if it were set beforehand. Setting it after
            # is what actually gets it in front of the user.
            self._on_search_clicked()
            if not result.ok:
                self._error_banner.set_error(result.message)

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
