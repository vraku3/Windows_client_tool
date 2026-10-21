"""Manual duplicate discovery, virtual results and guarded, recoverable actions."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime

from PyQt6 import sip
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QImageReader, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSlider,
    QSplitter, QStackedWidget, QToolButton, QTreeView, QVBoxLayout, QWidget,
)

from core.long_op_pool import get_long_op_pool
from core.table_ui import set_role
from core.worker import Worker, _emit
from modules.cleanup.cleanup_scanner import format_size, known_folders
from modules.cleanup.cleanup_scanner.drives import fixed_drive_roots
from modules.cleanup.duplicates.actions import perform_actions
from modules.cleanup.duplicates.enumerate import ScanOptions, system_folders
from modules.cleanup.duplicates.finder import METHODS, ScanCancelled, find_duplicates, image_support, safe_snapshot
from modules.cleanup.duplicates.hash_cache import HashCache
from modules.cleanup.duplicates.models import select_files, under
from modules.cleanup.duplicates.reports import export_report
from ._duplicates_model import DuplicatesModel, DuplicatesProxy

logger = logging.getLogger(__name__)


def _alive(widget):
    return not sip.isdeleted(widget)


def _tokens(text):
    return tuple(part.strip() for part in text.replace(",", ";").split(";") if part.strip())


class _DuplicatesTab(QWidget):
    freed_bytes = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers = []
        self._active_worker = None
        self._action_running = False
        self._scan_options = None
        self._outcomes = []
        self._model = DuplicatesModel(self)
        self._proxy = DuplicatesProxy(self)
        self._proxy.setSourceModel(self._model)
        layout = QVBoxLayout(self)
        settings = self._build_settings()
        self._build_filters(layout, settings)
        self._build_scan_controls(layout)
        self._build_summaries(layout)
        self._build_results(layout)
        self._build_actions(layout)
        self._model.selection_changed.connect(self._summarise)
        self._model.selection_refused.connect(self._show_status)
        self._summarise()

    def auto_scan(self):
        """Intentionally manual, including the first activation."""

    def _show_status(self, text):
        if _alive(self):
            self._status.setText(text)

    def _method_changed(self):
        key = self._method.currentData()
        self._explanation.setText(METHODS[key][1])
        self._image_controls.setVisible(key == "images")

    def _update_threshold(self, value):
        self._threshold_label.setText(f"Hamming distance: {value} / 64")

    def _add_root(self, path):
        if path and not self._roots.findItems(path, Qt.MatchFlag.MatchFixedString):
            self._roots.addItem(path)

    def _add_folder(self):
        self._add_root(QFileDialog.getExistingDirectory(self, "Add scan folder"))

    def _add_drive(self):
        roots = fixed_drive_roots(refresh=True)
        if not roots:
            self._show_status("No fixed drives are available.")
            return
        path, ok = QInputDialog.getItem(self, "Add fixed drive", "Drive", roots, editable=False)
        if ok:
            self._add_root(path)

    def _remove_root(self):
        for item in self._roots.selectedItems():
            self._roots.takeItem(self._roots.row(item))

    def _options(self):
        return ScanOptions(roots=[self._roots.item(i).text() for i in range(self._roots.count())],
                           min_size=int(self._minimum.value() * 1024**2),
                           max_size=int(self._maximum.value() * 1024**2), method=self._method.currentData(),
                           include_extensions=_tokens(self._include.text()),
                           exclude_extensions=_tokens(self._exclude.text()),
                           exclude_folders=_tokens(self._excluded_folders.text()),
                           skip_system_folders=self._skip_system.isChecked(),
                           skip_hidden_system=self._skip_hidden.isChecked(), use_cache=self._cache.isChecked(),
                           image_threshold=self._threshold.value())

    def _busy(self, busy):
        for widget in (self._settings, self._scan_btn, self._actions_bar, self._select_btn, self._view):
            widget.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        self._progress.setVisible(busy)

    def _launch(self, task, done, *, action=False):
        if self._active_worker is not None:
            return
        holder = {}

        def run(worker):
            try:
                holder["value"] = task(worker)
                return holder["value"]
            except ScanCancelled:
                # Worker will emit only cancelled (plus finished) after flag is set.
                worker.cancel()
                return None
            except Exception:
                if worker.is_cancelled:
                    logger.info("Duplicate work ended after cancellation", exc_info=True)
                    return None
                raise

        worker = Worker(run)
        self._active_worker = worker
        self._action_running = action
        self._workers.append(worker)
        self._busy(True)

        def log_line(line):
            if not _alive(self) or self._active_worker is not worker:
                return
            self._on_progress(json.loads(line))

        def error(reason):
            if not _alive(self) or self._active_worker is not worker:
                return
            holder["error"] = reason
            self._status.setText(f"Failed: {reason}")
            self._action_log.appendPlainText(f"FAILED: {reason}")

        def cancelled():
            if not _alive(self) or self._active_worker is not worker:
                return
            self._status.setText("Stopped. Completed actions remain in History.")

        def finished():
            if not _alive(self):
                return
            if worker in self._workers:
                self._workers.remove(worker)
            if self._active_worker is not worker:
                return
            self._active_worker = None
            self._action_running = False
            self._busy(False)
            if "value" in holder and (action or not worker.is_cancelled):
                done(holder["value"])

        worker.signals.log_line.connect(log_line)
        worker.signals.error.connect(error)
        worker.signals.cancelled.connect(cancelled)
        worker.signals.finished.connect(finished)
        get_long_op_pool().start(worker)

    def _scan(self):
        if self._active_worker is not None:
            return
        options = self._options()
        if not options.roots:
            self._show_status("Add at least one folder or fixed drive.")
            return
        if options.max_size and options.max_size < options.min_size:
            self._show_status("Maximum size must be at least the minimum size.")
            return
        self._scan_options = options
        self._outcomes = []
        self._action_log.clear()
        self._status.setText("Listing files...")
        self._progress.setRange(0, 0)

        def task(worker):
            return find_duplicates(options, cancelled=lambda: worker.is_cancelled,
                                   progress=lambda p: _emit(worker.signals, "log_line", json.dumps(p)))

        self._launch(task, self._show_result)

    def _show_result(self, result):
        self._model.set_result(result)
        self._stack.setCurrentIndex(0 if result.groups else 1)
        self._empty.setText("No duplicate groups found with these roots and filters.")
        self._status.setText(f"{result.listed:,} files; {len(result.groups):,} groups; "
                             f"{result.hardlinks:,} hard-link aliases; {result.skipped:,} skipped; "
                             f"{len(result.errors):,} issues; {result.timings.get('total', 0):.2f}s.")
        for path, reason in result.errors:
            self._action_log.appendPlainText(f"SKIPPED: {path} - {reason}")

    def _on_progress(self, p):
        if p.get("outcome"):
            self._action_log.appendPlainText(p["outcome"])
            return
        total, done = p.get("total", 0), p.get("done", 0)
        if total:
            self._progress.setRange(0, 1000)
            self._progress.setValue(min(1000, int(done / total * 1000)))
        else:
            self._progress.setRange(0, 0)
        eta = p.get("eta")
        throughput = f"; {p['mb_s']:.1f} MB/s" if "mb_s" in p else ""
        eta_text = f"; ETA {eta:.0f}s" if eta is not None else ""
        sizes = f"; {format_size(done)} / {format_size(total)}" if total and p.get("stage") != "Image fingerprints" else ""
        self._status.setText(f"{p['stage']}: {p.get('files', 0):,} files; "
                             f"{p.get('groups', 0):,} candidate groups{sizes}{throughput}{eta_text}")

    def _summarise(self):
        groups = self._model.result.groups
        values = (f"{len(groups):,}", f"{sum(len(g.files) - 1 for g in groups):,}",
                  format_size(sum(g.reclaimable for g in groups)),
                  format_size(sum(f.on_disk for g in groups for f in g.files if f.path in self._model.selected)))
        for label, value in zip(self._summary_labels, values):
            label.setText(value)

    def _auto_select(self, strategy):
        if self._active_worker is not None:
            return
        folder = ""
        if strategy in ("preferred", "keep_folder"):
            folder = QFileDialog.getExistingDirectory(self, "Keep files in folder")
            if not folder:
                return
        self._model.set_selected(select_files(self._model.result.groups, strategy, folder))
        self._show_status("Selection updated. One keeper remains in every set; size and image groups are never auto-selected.")

    def _current_entry(self):
        source = self._proxy.mapToSource(self._view.currentIndex())
        return self._model.group_entry(source)[1]

    def _detail_changed(self, current, previous):
        entry = self._current_entry()
        self._preview.clear()
        self._preview.setText("No image preview")
        if entry is None:
            self._detail.clear()
            return
        self._detail.setPlainText(f"{entry.path}\n\nSize: {format_size(entry.size)}\n"
                                  f"Modified: {datetime.fromtimestamp(entry.mtime_ns / 1e9)}\n"
                                  f"Created: {datetime.fromtimestamp(entry.ctime_ns / 1e9)}\n"
                                  f"Volume: {entry.dev}\nFile index: {entry.ino}\nHard links: {entry.nlink}")
        if os.path.splitext(entry.path)[1].lower() not in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
            return
        try:
            safe_snapshot(entry)
            QImageReader.setAllocationLimit(32)
            reader = QImageReader(entry.path)
            reader.setAutoTransform(True)
            size = reader.size()
            if not size.isValid() or size.width() * size.height() > 40_000_000:
                self._preview.setText("Image too large to preview safely")
                return
            reader.setScaledSize(size.scaled(QSize(300, 220), Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                self._preview.setText(f"Preview unavailable: {reader.errorString()}")
            else:
                self._preview.setPixmap(QPixmap.fromImage(image))
        except OSError as exc:
            self._preview.setText(str(exc))

    def _act(self, mode):
        selected = set(self._model.selected)
        if self._active_worker is not None or not selected:
            self._show_status("Select files to act on; keep one file in every set.")
            return
        destination = ""
        if mode == "move":
            destination = QFileDialog.getExistingDirectory(self, "Move selected duplicates to folder")
            if not destination:
                return
        if mode == "delete":
            count, ok = QInputDialog.getText(self, "Permanently delete duplicates",
                                           f"This cannot be undone. Type {len(selected)} to delete "
                                           f"{len(selected)} selected files permanently:")
            if not ok or count != str(len(selected)):
                return
        elif QMessageBox.question(self, "Confirm duplicate action",
                                  f"{'Recycle' if mode == 'recycle' else 'Move'} {len(selected)} selected files?\n"
                                  "Every file will be checked again against its keeper.\n"
                                  "Recycle Bin and same-volume moves free zero disk space.",
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        roots = tuple(self._scan_options.roots) if self._scan_options else ()
        confirm_system = False
        if any(under(path, system) for path in selected for system in system_folders()):
            confirm_system = QMessageBox.warning(
                self, "System folder files", "Selected files are inside a system/application folder.\n"
                "Continue only for folders explicitly added as scan roots? Protected data remains refused.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
            if not confirm_system:
                return
        groups = self._model.result.groups
        if any(g.method == "images" and any(f.path in selected for f in g.files) for g in groups):
            if QMessageBox.warning(
                    self, "Similar images have different content",
                    "These images are visually similar, not necessarily identical.\n"
                    "Have you reviewed the previews and chosen the versions to keep?\n"
                    "The similarity threshold and all file metadata will be checked again.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        threshold = self._scan_options.image_threshold if self._scan_options else 5

        def task(worker):
            return perform_actions(groups, selected, mode=mode, destination=destination,
                                   cancelled=lambda: worker.is_cancelled, scanned_roots=roots,
                                   confirm_system=confirm_system,
                                   image_threshold=threshold,
                                   on_outcome=lambda o: _emit(worker.signals, "log_line", json.dumps({
                                       "stage": "Actions", "outcome": f"{o.status.upper()}: {o.path} - {o.reason}"})))

        self._launch(task, self._actions_done, action=True)

    def _actions_done(self, result):
        self._outcomes.extend(result.outcomes)
        self._model.set_selected(set())
        done = sum(o.status == "done" for o in result.outcomes)
        skipped = sum(o.status == "skipped" for o in result.outcomes)
        failed = sum(o.status == "failed" for o in result.outcomes)
        self._status.setText(f"{'Stopped' if result.cancelled else 'Finished'}: {done} done, {skipped} skipped, "
                             f"{failed} failed; measured freed {format_size(result.freed)}. Scan again to refresh results.")
        self.freed_bytes.emit(result.freed)

    def _open(self, folder):
        """Open the file or its folder UNELEVATED, through the desktop shell.

        This app runs elevated, and QDesktopServices.openUrl is a plain ShellExecute: a
        duplicate .exe or script from Downloads would run as administrator with no UAC
        prompt. The desktop shell's ShellExecute runs at the user's Medium integrity
        (core.shell_launch, measured). It can block while Explorer shows a
        message, so it runs on a worker."""
        entry = self._current_entry()
        if entry is None:
            return
        try:
            safe_snapshot(entry)
        except OSError as exc:
            self._show_status(str(exc))
            return
        target = os.path.dirname(entry.path) if folder else entry.path

        def task(_worker):
            from core.shell_launch import launch
            return launch(target)

        def done(error):
            if _alive(self) and error:
                self._show_status(f"Windows could not open this path: {error}")

        worker = Worker(task)
        worker.signals.result.connect(done)
        worker.signals.error.connect(lambda err: _alive(self) and self._show_status(f"Could not open: {err}"))
        # Kept referenced while running; a short history is enough (one per click).
        self._open_workers = (getattr(self, "_open_workers", []) + [worker])[-8:]
        get_long_op_pool().start(worker)

    def _copy_path(self):
        entry = self._current_entry()
        if entry:
            QApplication.clipboard().setText(entry.path)

    def _exclude_current(self):
        entry = self._current_entry()
        if entry:
            exclusions = list(_tokens(self._excluded_folders.text()))
            folder = os.path.dirname(entry.path)
            if folder not in exclusions:
                exclusions.append(folder)
            self._excluded_folders.setText("; ".join(exclusions))
            self._model.set_selected({path for path in self._model.selected if not under(path, folder)})
            self._show_status("Folder excluded from the next scan.")

    def _clear_cache(self):
        try:
            with HashCache() as cache:
                cache.clear()
            self._show_status("Hash cache cleared.")
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Cannot clear duplicate cache: %s", exc)
            self._show_status(f"Cannot clear cache: {exc}")

    def _export(self, html):
        path, _ = QFileDialog.getSaveFileName(self, "Export duplicates report", "duplicates.html" if html else "duplicates.csv",
                                             "HTML (*.html)" if html else "CSV (*.csv)")
        if path:
            try:
                export_report(path, self._model.result, self._outcomes, html=html)
                self._show_status(f"Report saved to {path}")
            except OSError as exc:
                logger.warning("Cannot export duplicates report: %s", exc)
                self._show_status(f"Export failed: {exc}")

    def _cancel_all(self):
        for worker in self._workers:
            worker.cancel()
        if self._active_worker is not None:
            self._show_status("Stopping after the current buffer or file action...")
            self._stop_btn.setEnabled(False)

    def _build_settings(self):
        self._settings = QFrame()
        self._settings.setObjectName("card")
        settings = QVBoxLayout(self._settings)
        title = QLabel("Find duplicate files")
        set_role(title, "heading")
        settings.addWidget(title)
        roots_row = QHBoxLayout()
        self._roots = QListWidget()
        self._roots.setMaximumHeight(90)
        self._roots.setToolTip("Scan multiple folders. Cloud-only files and directory links are always skipped.")
        for path in known_folders.user_data_dirs():
            self._roots.addItem(path)
        roots_row.addWidget(self._roots, 1)
        roots_buttons = QVBoxLayout()
        for label, callback in (("Add folder", self._add_folder), ("Add drive", self._add_drive),
                                ("Remove root", self._remove_root)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            roots_buttons.addWidget(button)
        roots_row.addLayout(roots_buttons)
        settings.addLayout(roots_row)
        self._method = QComboBox()
        for key, (name, _) in METHODS.items():
            self._method.addItem(name, key)
        available, reason = image_support()
        if not available:
            item = self._method.model().item(self._method.count() - 1)
            item.setEnabled(False)
            item.setToolTip(reason)
        settings.addWidget(self._method)
        self._explanation = QLabel()
        self._explanation.setWordWrap(True)
        set_role(self._explanation, "muted")
        settings.addWidget(self._explanation)
        self._image_controls = QWidget()
        image_row = QHBoxLayout(self._image_controls)
        image_row.setContentsMargins(0, 0, 0, 0)
        self._threshold = QSlider(Qt.Orientation.Horizontal)
        self._threshold.setRange(0, 16)
        self._threshold.setValue(5)
        self._threshold_label = QLabel("Hamming distance: 5 / 64")
        self._threshold.valueChanged.connect(self._update_threshold)
        image_row.addWidget(self._threshold_label)
        image_row.addWidget(self._threshold)
        settings.addWidget(self._image_controls)
        self._method.currentIndexChanged.connect(self._method_changed)
        self._method_changed()
        return settings

    def _build_filters(self, layout, settings):
        toggle = QToolButton()
        toggle.setText("Filters and cache")
        toggle.setCheckable(True)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.RightArrow)
        settings.addWidget(toggle)
        self._filters = QWidget()
        form = QFormLayout(self._filters)
        self._minimum, self._maximum = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self._minimum, self._maximum):
            spin.setRange(0, 10_000_000)
            spin.setDecimals(2)
            spin.setSuffix(" MB")
        self._minimum.setValue(1)
        self._maximum.setSpecialValueText("Unlimited")
        size_row = QHBoxLayout()
        size_row.addWidget(self._minimum)
        size_row.addWidget(QLabel("to"))
        size_row.addWidget(self._maximum)
        form.addRow("File size", size_row)
        self._include, self._exclude, self._excluded_folders = QLineEdit(), QLineEdit(), QLineEdit()
        self._include.setPlaceholderText("e.g. jpg; png; pdf (blank = all)")
        self._exclude.setPlaceholderText("e.g. tmp; bak")
        self._excluded_folders.setPlaceholderText("Full folder paths separated by semicolons")
        form.addRow("Include extensions", self._include)
        form.addRow("Exclude extensions", self._exclude)
        form.addRow("Exclude folders", self._excluded_folders)
        self._skip_system = QCheckBox("Skip Windows, Program Files, ProgramData and AppData")
        self._skip_system.setChecked(True)
        form.addRow(self._skip_system)
        self._skip_hidden = QCheckBox("Skip hidden and system files (zero-byte files always skipped)")
        self._skip_hidden.setChecked(True)
        form.addRow(self._skip_hidden)
        cache_row = QHBoxLayout()
        self._cache = QCheckBox("Use local hash cache")
        self._cache.setChecked(True)
        clear_cache = QPushButton("Clear cache")
        clear_cache.clicked.connect(self._clear_cache)
        cache_row.addWidget(self._cache)
        cache_row.addWidget(clear_cache)
        cache_row.addStretch()
        form.addRow(cache_row)
        settings.addWidget(self._filters)
        self._filters.hide()
        toggle.toggled.connect(self._filters.setVisible)
        toggle.toggled.connect(lambda checked: toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        layout.addWidget(self._settings)

    def _build_scan_controls(self, layout):
        scan_row = QHBoxLayout()
        self._scan_btn, self._stop_btn = QPushButton("Scan"), QPushButton("Stop")
        self._scan_btn.setObjectName("accentButton")
        self._scan_btn.setDefault(True)
        self._scan_btn.clicked.connect(self._scan)
        self._stop_btn.clicked.connect(self._cancel_all)
        self._stop_btn.setEnabled(False)
        scan_row.addWidget(self._scan_btn)
        scan_row.addWidget(self._stop_btn)
        self._status = QLabel("Choose roots and press Scan. Nothing runs automatically.")
        self._status.setWordWrap(True)
        scan_row.addWidget(self._status, 1)
        layout.addLayout(scan_row)
        self._progress = QProgressBar()
        self._progress.hide()
        layout.addWidget(self._progress)

    def _build_summaries(self, layout):
        summaries = QHBoxLayout()
        self._summary_labels = []
        for caption in ("Groups", "Duplicate files", "Potential reclaimable", "Selected space"):
            card = QFrame()
            card.setObjectName("card")
            box = QVBoxLayout(card)
            label = QLabel(caption)
            set_role(label, "muted")
            box.addWidget(label)
            value = QLabel("0")
            set_role(value, "heading")
            box.addWidget(value)
            self._summary_labels.append(value)
            summaries.addWidget(card)
        layout.addLayout(summaries)

    def _build_results(self, layout):
        results_tools = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search paths, folders, hashes or confidence...")
        self._search.textChanged.connect(self._proxy.setFilterFixedString)
        results_tools.addWidget(self._search, 1)
        self._select_btn = QPushButton("Auto-select")
        selection_menu = QMenu(self._select_btn)
        for text, strategy in (("Keep newest", "newest"), ("Keep oldest", "oldest"),
                               ("Keep shortest path", "shortest"), ("Keep longest path", "longest"),
                               ("Keep first in preferred folder...", "preferred"),
                               ("Keep all files in folder...", "keep_folder"), ("Clear selection", "clear")):
            selection_menu.addAction(text, lambda checked=False, s=strategy: self._auto_select(s))
        self._select_btn.setMenu(selection_menu)
        results_tools.addWidget(self._select_btn)
        layout.addLayout(results_tools)
        self._splitter = QSplitter()
        self._stack = QStackedWidget()
        self._view = QTreeView()
        self._view.setModel(self._proxy)
        self._view.setUniformRowHeights(True)
        self._view.setAlternatingRowColors(True)
        self._view.setSortingEnabled(True)
        self._view.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._view.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._view.setColumnWidth(0, 360)
        self._view.setColumnWidth(2, 240)
        self._view.setColumnWidth(8, 180)
        self._view.selectionModel().currentChanged.connect(self._detail_changed)
        self._stack.addWidget(self._view)
        self._empty = QLabel("Press Scan to find duplicates. No files are selected automatically.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._stack.addWidget(self._empty)
        self._stack.setCurrentIndex(1)
        self._splitter.addWidget(self._stack)
        detail = QWidget()
        detail_box = QVBoxLayout(detail)
        detail_box.addWidget(QLabel("File details"))
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        detail_box.addWidget(self._detail, 1)
        self._preview = QLabel("Select an image to preview")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMaximumSize(320, 240)
        detail_box.addWidget(self._preview)
        detail.setMaximumWidth(360)
        self._splitter.addWidget(detail)
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 1)
        layout.addWidget(self._splitter, 1)

    def _build_actions(self, layout):
        self._actions_bar = QWidget()
        action_row = QHBoxLayout(self._actions_bar)
        action_row.setContentsMargins(0, 0, 0, 0)
        recycle = QPushButton("Move to Recycle Bin")
        recycle.clicked.connect(lambda: self._act("recycle"))
        action_row.addWidget(recycle)
        more = QPushButton("More actions")
        more_menu = QMenu(more)
        for text, callback in (("Move to folder...", lambda: self._act("move")),
                               ("Delete permanently...", lambda: self._act("delete")),
                               ("Open file", lambda: self._open(False)),
                               ("Open containing folder", lambda: self._open(True)),
                               ("Copy path", self._copy_path), ("Exclude this folder", self._exclude_current)):
            more_menu.addAction(text, callback)
        more.setMenu(more_menu)
        action_row.addWidget(more)
        action_row.addStretch()
        for text, kind in (("Export CSV", False), ("Export HTML", True)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, h=kind: self._export(h))
            action_row.addWidget(button)
        layout.addWidget(self._actions_bar)
        self._action_log = QPlainTextEdit()
        self._action_log.setReadOnly(True)
        self._action_log.setMaximumBlockCount(2000)
        self._action_log.setMaximumHeight(100)
        self._action_log.setPlaceholderText("Per-file action and scan issues appear here; exports include every action outcome.")
        layout.addWidget(self._action_log)
