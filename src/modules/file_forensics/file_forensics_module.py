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
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.widget_life import widget_is_valid as _widget_valid
from core.worker import Worker
from ui.empty_state import EmptyState
from ui.error_banner import ErrorBanner

from .engine.analysis import FileAnalysis, analyze

_COLUMNS = ["Name", "Path", "Size", "Created", "Owner", "Locked By", "Likely Creator"]


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
        self._results_stack.addWidget(self._table)
        self._empty = EmptyState(
            "🔎", "No results yet",
            "Enter a folder and click Search, or enable Live Watch.",
        )
        self._results_stack.addWidget(self._empty)
        self._results_stack.setCurrentIndex(1)
        layout.addWidget(self._results_stack, 1)

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
            self._table.setItem(row, 5, QTableWidgetItem(locked_text))
            creator_text = (analysis.creator_candidates[0].name
                            if analysis.creator_candidates else "")
            self._table.setItem(row, 6, QTableWidgetItem(creator_text))
        if not _widget_valid(self._results_stack):
            return
        self._results_stack.setCurrentIndex(0 if results else 1)
