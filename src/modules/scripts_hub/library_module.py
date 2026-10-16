"""Script Library -- the Qt side of `library` and `support_bundle`.

Left: the scripts by category (built-in, then the user's own). Right: what
the selected script does, its badges, Run/Stop, and its output streaming
live. "Collect support bundle" runs every export into one zip on the
Desktop. Every run is on a Worker; output lines cross to the UI thread
through the Worker's own `log_line` signal.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import set_role
from core.widget_life import widget_is_valid
from core.worker import Worker
from ui.error_banner import ErrorBanner

from . import library, support_bundle

logger = logging.getLogger(__name__)

_SCRIPT_ROLE = Qt.ItemDataRole.UserRole
_MAX_OUTPUT_LINES = 20_000


def badges(script: library.Script, elevated: bool) -> str:
    """The one-line facts shown under a script's title."""
    parts = ["Read-only" if script.read_only else "Your own script -- can change anything"]
    if script.admin:
        parts.append("needs administrator for the full answer" + ("" if elevated
                                                                    else " (not elevated now)"))
    parts.append("PowerShell" if script.kind == library.POWERSHELL else "Command Prompt")
    return "  ·  ".join(parts)


class ScriptLibraryModule(BaseModule):
    name = "Script Library"
    icon = "📜"
    description = "Read-only admin checks with live output, your own scripts, and a support bundle"
    group = ModuleGroup.TOOLS
    requires_admin = False

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._loaded = False
        self._scripts: List[library.Script] = []
        self._current: Optional[library.Script] = None
        self._run_worker: Optional[Worker] = None
        self._bundle_worker: Optional[Worker] = None
        self._elevated = support_bundle.is_admin()

    def on_start(self, app) -> None:
        self.app = app

    def get_refresh_interval(self):
        return None

    # ---- layout -------------------------------------------------------------------------------
    def create_widget(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addLayout(self._build_toolbar(w))
        self._banner = ErrorBanner("", w)
        self._banner.hide()
        lay.addWidget(self._banner)
        self._bundle_progress = QProgressBar(w)
        self._bundle_progress.hide()
        lay.addWidget(self._bundle_progress)
        split = QSplitter(Qt.Orientation.Horizontal, w)
        self._tree = QTreeWidget(w)
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(280)
        self._tree.currentItemChanged.connect(lambda *_: self._select())
        self._tree.itemDoubleClicked.connect(lambda *_: self._run())
        split.addWidget(self._tree)
        split.addWidget(self._build_detail(w))
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)
        self._widget = w
        return w

    def _build_toolbar(self, parent: QWidget) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._search = QLineEdit(parent)
        self._search.setPlaceholderText("Search scripts…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self._fill_tree())
        bar.addWidget(self._search, 1)
        for label, fn in (("Collect support bundle…", self._collect_bundle),
                          ("Open my scripts folder", self._open_user_folder),
                          ("Reload", self._reload)):
            b = QPushButton(label, parent)
            b.clicked.connect(fn)
            bar.addWidget(b)
        return bar

    def _build_detail(self, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 0, 0, 0)
        self._title = QLabel("Select a script", box)
        set_role(self._title, "sectionTitle")
        self._badges = QLabel("", box)
        set_role(self._badges, "statusInfo")
        self._desc = QLabel("", box)
        self._desc.setWordWrap(True)
        self._desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row = QHBoxLayout()
        self._btn: Dict[str, QPushButton] = {}
        for key, label, fn in (("run", "Run", self._run), ("stop", "Stop", self._stop),
                               ("view", "View script", self._view_script),
                               ("copy", "Copy output", self._copy_output),
                               ("save", "Save output…", self._save_output)):
            b = QPushButton(label, box)
            b.clicked.connect(fn)
            b.setEnabled(False)
            self._btn[key] = b
            row.addWidget(b)
        row.addStretch(1)
        self._status = QLabel("", box)
        set_role(self._status, "statusInfo")
        self._output = QPlainTextEdit(box)
        self._output.setReadOnly(True)
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._output.setMaximumBlockCount(_MAX_OUTPUT_LINES)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._output.setFont(mono)
        self._output.setPlaceholderText("Output appears here as the script runs.")
        for widget in (self._title, self._badges, self._desc):
            lay.addWidget(widget)
        lay.addLayout(row)
        lay.addWidget(self._status)
        lay.addWidget(self._output, 1)
        return box

    # ---- lifecycle ----------------------------------------------------------------------------
    def on_activate(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._reload()

    def on_deactivate(self) -> None:
        pass        # a running script keeps running while another tab is looked at

    def on_stop(self) -> None:
        for w in (self._run_worker, self._bundle_worker):
            if w is not None:
                w.cancel()
        self.cancel_all_workers()

    # ---- list ---------------------------------------------------------------------------------
    def _reload(self) -> None:
        if self._widget is None:
            return
        self._scripts = library.all_scripts()
        self._fill_tree()

    def _fill_tree(self) -> None:
        if not widget_is_valid(self._tree):
            return
        needle = self._search.text().strip().lower()
        self._tree.clear()
        groups: Dict[str, QTreeWidgetItem] = {}
        for cat in library.CATEGORIES:
            groups[cat] = QTreeWidgetItem([cat])
        for s in self._scripts:
            if needle and needle not in f"{s.title}\n{s.description}".lower():
                continue
            item = QTreeWidgetItem([s.title])
            item.setData(0, _SCRIPT_ROLE, s)
            item.setToolTip(0, s.description)
            groups.setdefault(s.category, QTreeWidgetItem([s.category])).addChild(item)
        if not any(s.category == "My scripts" for s in self._scripts) and not needle:
            hint = QTreeWidgetItem(["(drop .ps1/.cmd files in your scripts folder)"])
            hint.setDisabled(True)
            groups["My scripts"].addChild(hint)
        for cat, group in groups.items():
            if group.childCount():
                group.setText(0, f"{cat} ({sum(1 for i in range(group.childCount()) if group.child(i).data(0, _SCRIPT_ROLE))})")
                self._tree.addTopLevelItem(group)
                group.setExpanded(True)

    def _select(self) -> None:
        item = self._tree.currentItem()
        script = item.data(0, _SCRIPT_ROLE) if item is not None else None
        if script is None:
            return
        self._current = script
        self._title.setText(script.title)
        self._badges.setText(badges(script, self._elevated))
        self._desc.setText(script.description + (f"\n\nFile: {script.path}" if script.path else ""))
        self._update_buttons()

    def _update_buttons(self) -> None:
        running = self._run_worker is not None
        self._btn["run"].setEnabled(self._current is not None and not running)
        self._btn["stop"].setEnabled(running)
        self._btn["view"].setEnabled(self._current is not None)
        has_output = bool(self._output.toPlainText())
        self._btn["copy"].setEnabled(has_output)
        self._btn["save"].setEnabled(has_output)

    # ---- running ------------------------------------------------------------------------------
    def _confirm_user_script(self, script: library.Script) -> bool:
        box = QMessageBox(self._widget)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Run your own script")
        box.setText(f"Run {script.title}?")
        box.setInformativeText("This is your own script, not one of the app's read-only checks. "
                               "It runs with this app's rights"
                               + (" -- as ADMINISTRATOR" if self._elevated else "")
                               + " and can change anything. Its full text is under Show Details.")
        box.setDetailedText(script.body or "(the file could not be read)")
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Ok

    def _run(self) -> None:
        script = self._current
        if script is None or self._run_worker is not None:
            return
        if not script.read_only and not self._confirm_user_script(script):
            return
        self._output.clear()
        self._status.setText(f"Running {script.title}…")
        w = Worker(lambda worker: library.run(script, worker.signals.log_line.emit,
                                              lambda: worker.is_cancelled))
        w.signals.log_line.connect(self._append)
        w.signals.result.connect(self._finished)
        w.signals.error.connect(self._failed)
        w.signals.cancelled.connect(lambda: self._finished(None))
        self._run_worker = w
        self._workers.append(w)
        self._update_buttons()
        QThreadPool.globalInstance().start(w)

    def _append(self, line: str) -> None:
        if widget_is_valid(self._output):
            self._output.appendPlainText(line)

    def _finished(self, result: Optional[library.RunResult]) -> None:
        self._run_worker = None
        if not widget_is_valid(self._status):
            return
        if result is None:
            self._status.setText("Stopped.")
        elif result.cancelled:
            self._status.setText(f"Stopped after {result.seconds:.1f} s.")
        elif result.timed_out:
            self._status.setText(f"Stopped: no end after {self._current.timeout:.0f} s.")
        else:
            verdict = "Finished" if result.returncode == 0 else f"Finished with exit code {result.returncode}"
            self._status.setText(f"{verdict} in {result.seconds:.1f} s · {len(result.lines)} lines.")
        self._update_buttons()

    def _failed(self, err) -> None:
        self._run_worker = None
        message = str(err[1]) if isinstance(err, tuple) and len(err) > 1 else str(err)
        logger.error("script failed to start: %s", message)
        if widget_is_valid(self._status):
            self._status.setText(f"Could not run the script: {message}")
            self._update_buttons()

    def _stop(self) -> None:
        if self._run_worker is not None:
            self._run_worker.cancel()
            self._status.setText("Stopping…")

    def _view_script(self) -> None:
        if self._current is None:
            return
        self._output.setPlainText(self._current.body)
        self._status.setText("Showing the script itself (not output). Run it to see its output.")
        self._update_buttons()

    def _copy_output(self) -> None:
        QApplication.clipboard().setText(self._output.toPlainText())

    def _save_output(self) -> None:
        name = (self._current.id if self._current else "output").replace(":", "_")
        path, _ = QFileDialog.getSaveFileName(self._widget, "Save output", f"{name}.txt",
                                              "Text files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._output.toPlainText())
        except OSError as e:
            self._banner.set_error(f"Could not save the output: {e}")
            self._banner.show()

    def _open_user_folder(self) -> None:
        folder = library.user_scripts_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except OSError as e:
            self._banner.set_error(f"Could not open {folder}: {e}")
            self._banner.show()

    # ---- support bundle -----------------------------------------------------------------------
    def _collect_bundle(self) -> None:
        if self._bundle_worker is not None:
            return
        dest = support_bundle.default_folder()
        answer = QMessageBox.question(
            self._widget, "Collect support bundle",
            f"Collect a support bundle into a zip in\n{dest}?\n\n"
            "It contains system information, network configuration, drivers, services, "
            "installed programs, 7 days of System and Application event logs"
            + (" and the Security log" if self._elevated else "")
            + ", Group Policy results and the Script Library's checks. It includes machine "
              "and user names, IP addresses and recently visited domain names -- share it "
              "only with someone you trust. No passwords, Wi-Fi keys or documents."
            + ("" if self._elevated else "\n\nNot running as administrator: the Security log "
                                         "and power requests will be skipped (the manifest says so)."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._bundle_progress.setRange(0, len(support_bundle.steps()) + 1)
        self._bundle_progress.setValue(0)
        self._bundle_progress.show()

        def work(worker):
            def progress(i, n, name):
                worker.signals.progress.emit(i)
                worker.signals.log_line.emit(name)
            return support_bundle.collect(dest, progress, lambda: worker.is_cancelled)

        w = Worker(work)
        w.signals.progress.connect(lambda i: widget_is_valid(self._bundle_progress)
                                   and self._bundle_progress.setValue(i))
        w.signals.log_line.connect(lambda name: widget_is_valid(self._status)
                                   and self._status.setText(f"Support bundle: {name}…"))
        w.signals.result.connect(self._bundle_done)
        w.signals.error.connect(self._bundle_failed)
        w.signals.cancelled.connect(lambda: self._bundle_failed("cancelled"))
        self._bundle_worker = w
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _bundle_done(self, bundle: support_bundle.Bundle) -> None:
        self._bundle_worker = None
        if not widget_is_valid(self._bundle_progress):
            return
        self._bundle_progress.hide()
        counts = ", ".join(f"{n} {status.lower()}" for status, n in bundle.counts.items())
        self._status.setText(f"Support bundle saved: {bundle.path} ({counts}).")
        self._output.setPlainText(bundle.manifest)
        self._update_buttons()
        try:
            subprocess.Popen(["explorer.exe", "/select,", bundle.path])
        except OSError as e:
            logger.warning("could not show the bundle in Explorer: %s", e)

    def _bundle_failed(self, err) -> None:
        self._bundle_worker = None
        if not widget_is_valid(self._bundle_progress):
            return
        self._bundle_progress.hide()
        message = str(err[1]) if isinstance(err, tuple) and len(err) > 1 else str(err)
        self._banner.set_error(f"The support bundle could not be completed: {message}")
        self._banner.show()
