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
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.semantic_colors import CHROME_PALETTES, SEMANTIC_PALETTES
from core.table_ui import set_role
from core.widget_life import widget_is_valid
from core.worker import Worker
from ui.error_banner import ErrorBanner

from . import library, modules_catalog, output_style, support_bundle

logger = logging.getLogger(__name__)

_SCRIPT_ROLE = Qt.ItemDataRole.UserRole
_FOLDER_KEY = "modules.scripts.folder"
_MAX_OUTPUT_LINES = 20_000


def badges(script: library.Script, elevated: bool) -> str:
    """The one-line facts shown under a script's title."""
    parts = ["Read-only" if script.read_only else "Your own script -- can change anything"]
    if script.admin:
        parts.append("needs administrator for the full answer" + ("" if elevated
                                                                    else " (not elevated now)"))
    parts.append("PowerShell" if script.kind == library.POWERSHELL else "Command Prompt")
    if script.requires:
        parts.append(f"needs {script.requires} -- says NOT AVAILABLE where it is missing")
    return "  ·  ".join(parts)


#: The output pane looks like PowerShell ISE's console in every app theme: white,
#: black Lucida Console (the `iseConsole` QSS role). So the span colours come
#: from the LIGHT palettes whatever the app theme is -- the dark theme's amber
#: is unreadable on white. Like ISE, ordinary text, table headers and list keys
#: stay plain black; only verdicts, errors and warnings are coloured.
_ISE_STYLE = {output_style.OK: ("success", False), output_style.WARN: ("warning", False),
              output_style.BAD: ("error", False), output_style.INFO: ("info", False),
              output_style.NA: ("text_muted", True)}


def _format(kind: str) -> QTextCharFormat:
    fmt = QTextCharFormat()
    style = _ISE_STYLE.get(kind)
    if style is None:
        return fmt
    role, italic = style
    palette = CHROME_PALETTES["light"] if role.startswith("text") else SEMANTIC_PALETTES["light"]
    fmt.setForeground(QColor(palette[role]))
    fmt.setFontItalic(italic)
    return fmt


#: Top level of the tree: (label, which scripts).
_BRANCHES = (("Windows client", lambda s: not s.server and s.category != library.USER_CATEGORY),
             ("Windows Server", lambda s: s.server),
             (library.USER_CATEGORY, lambda s: s.category == library.USER_CATEGORY))


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
        #: requirement key -> installed (absent = not known yet), and whether
        #: this is Windows Server -- filled by a background detection.
        self._mod_status: Dict[str, bool] = {}
        self._is_server = False

    def on_start(self, app) -> None:
        self.app = app
        library.set_user_scripts_dir(self._config_get(_FOLDER_KEY, ""))

    def _config_get(self, key: str, default):
        config = getattr(getattr(self, "app", None), "config", None)
        if config is None:
            return default
        value = config.get(key, default)
        return default if value is None else value

    def _config_set(self, key: str, value) -> None:
        config = getattr(getattr(self, "app", None), "config", None)
        if config is not None:
            config.set(key, value)

    def get_refresh_interval(self):
        return None

    # ---- layout -------------------------------------------------------------------------------
    def create_widget(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addLayout(self._build_toolbar(w))
        lay.addLayout(self._build_folder_row(w))
        self._banner = ErrorBanner("", w)
        self._banner.hide()
        lay.addWidget(self._banner)
        self._bundle_progress = QProgressBar(w)
        self._bundle_progress.hide()
        lay.addWidget(self._bundle_progress)
        split = QSplitter(Qt.Orientation.Horizontal, w)
        self._tree = QTreeWidget(w)
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(360)
        self._tree.currentItemChanged.connect(lambda *_: self._select())
        self._tree.itemDoubleClicked.connect(lambda *_: self._run())
        split.addWidget(self._tree)
        split.addWidget(self._build_detail(w))
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)
        self._widget = w
        return w

    def _build_folder_row(self, parent: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        self._folder_label = QLabel("", parent)
        self._folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_role(self._folder_label, "statusInfo")
        row.addWidget(self._folder_label, 1)
        for label, fn in (("Change…", self._change_folder), ("Reset to default", self._reset_folder)):
            b = QPushButton(label, parent)
            b.clicked.connect(fn)
            row.addWidget(b)
        self._show_folder()
        return row

    def _show_folder(self) -> None:
        folder = library.user_scripts_dir()
        custom = folder != library.default_user_scripts_dir()
        self._folder_label.setText(f"Scripts folder: {folder}" + ("" if custom else "  (default)")
                                   + "  --  your .ps1/.cmd/.bat files, copies of built-ins you "
                                     "edit, and servers.txt for the fleet checks")

    def _change_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self._widget, "Choose the scripts folder",
                                                  library.user_scripts_dir())
        if not folder:
            return
        library.set_user_scripts_dir(folder)
        self._config_set(_FOLDER_KEY, folder)
        self._show_folder()
        self._reload()

    def _reset_folder(self) -> None:
        library.set_user_scripts_dir(None)
        self._config_set(_FOLDER_KEY, "")
        self._show_folder()
        self._reload()

    def _build_toolbar(self, parent: QWidget) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._search = QLineEdit(parent)
        self._search.setPlaceholderText("Search scripts…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self._fill_tree())
        bar.addWidget(self._search, 1)
        for label, fn in (("Collect support bundle…", self._collect_bundle),
                          ("New script…", self._new_script),
                          ("Required modules…", self._show_modules),
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
        # A plain button with a menu, so it looks like its neighbours; the
        # first entry is the preferred editor (PowerShell ISE when present).
        self._edit = QPushButton("Edit", box)
        self._edit.setToolTip("Open the script in an editor. A built-in is copied to your scripts "
                              "folder first -- the copy is yours to change, the original stays.")
        menu = QMenu(self._edit)
        for label, _exe in library.editors():
            menu.addAction(f"Edit in {label}", lambda checked=False, lb=label: self._edit_current(lb))
        self._edit.setMenu(menu)
        self._edit.setEnabled(False)
        row.insertWidget(3, self._edit)
        row.addStretch(1)
        self._status = QLabel("", box)
        set_role(self._status, "statusInfo")
        self._output = QPlainTextEdit(box)
        self._output.setReadOnly(True)
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._output.setMaximumBlockCount(_MAX_OUTPUT_LINES)
        # Font and colours come from the `iseConsole` role: an application
        # stylesheet overrides setFont(), which is why columns did not line up.
        set_role(self._output, "iseConsole")
        self._output.setPlaceholderText("Output appears here as the script runs.")
        need_row = QHBoxLayout()
        self._need = QLabel("", box)
        self._need.setWordWrap(True)
        self._install_req = QPushButton("Install required module…", box)
        self._install_req.clicked.connect(self._install_current_requirement)
        self._install_req.hide()
        need_row.addWidget(self._need, 1)
        need_row.addWidget(self._install_req)
        for widget in (self._title, self._badges, self._desc):
            lay.addWidget(widget)
        lay.addLayout(need_row)
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
        self._detect_modules()

    def _detect_modules(self) -> None:
        """Ask once per reload which server modules this PC has (~1 s)."""
        w = Worker(lambda _w: modules_catalog.detect())
        w.signals.result.connect(self._modules_detected)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _modules_detected(self, payload) -> None:
        status, server = payload
        self._mod_status, self._is_server = status, server
        if widget_is_valid(self._need) and self._current is not None:
            self._show_requirement(self._current)

    def _fill_tree(self) -> None:
        if not widget_is_valid(self._tree):
            return
        needle = self._search.text().strip().lower()
        self._tree.clear()
        shown = [s for s in self._scripts
                 if not needle or needle in f"{s.title}\n{s.description}\n{s.category}".lower()]
        for label, belongs in _BRANCHES:
            mine = [s for s in shown if belongs(s)]
            branch = QTreeWidgetItem([f"{label} ({len(mine)})"])
            if label == library.USER_CATEGORY:
                for s in mine:
                    branch.addChild(self._leaf(s))
                if not mine and not needle:
                    hint = QTreeWidgetItem(["(put .ps1 / .cmd files in your scripts folder)"])
                    hint.setDisabled(True)
                    branch.addChild(hint)
            else:
                for cat in library.CATEGORIES:
                    in_cat = [s for s in mine if s.category == cat]
                    if not in_cat:
                        continue
                    group = QTreeWidgetItem([f"{cat} ({len(in_cat)})"])
                    for s in sorted(in_cat, key=lambda x: x.title.lower()):
                        group.addChild(self._leaf(s))
                    branch.addChild(group)
                    group.setExpanded(bool(needle))
            if branch.childCount():
                self._tree.addTopLevelItem(branch)
                branch.setExpanded(True)

    @staticmethod
    def _leaf(s: library.Script) -> QTreeWidgetItem:
        item = QTreeWidgetItem([s.title])
        item.setData(0, _SCRIPT_ROLE, s)
        item.setToolTip(0, s.description)
        return item

    def _select(self) -> None:
        item = self._tree.currentItem()
        script = item.data(0, _SCRIPT_ROLE) if item is not None else None
        if script is None:
            return
        self._current = script
        self._title.setText(script.title)
        self._badges.setText(badges(script, self._elevated))
        self._desc.setText(script.description + (f"\n\nFile: {script.path}" if script.path else ""))
        self._show_requirement(script)
        self._update_buttons()

    def _show_requirement(self, script: library.Script) -> None:
        """Which module this script needs and whether this PC has it."""
        req = modules_catalog.requirement_for(script)
        if req is None:
            self._need.clear()
            self._install_req.hide()
            return
        have = self._mod_status.get(req.key)
        if have:
            self._need.setText(f"Uses {req.label} -- installed on this PC.")
            set_role(self._need, "statusSuccess")
            self._install_req.hide()
            return
        state = "not installed on this PC" if have is False else "checking whether it is installed…"
        self._need.setText(f"Needs {req.label} -- {state}. Without it the script prints NOT AVAILABLE.")
        set_role(self._need, "statusWarning" if have is False else "statusInfo")
        installable = modules_catalog.install_command(req, self._is_server) is not None
        self._install_req.setVisible(have is False and installable)

    def _install_current_requirement(self) -> None:
        from .modules_dialog import start_install
        req = modules_catalog.requirement_for(self._current) if self._current else None
        if req is None:
            return
        message = start_install(self._widget, req, self._is_server)
        if message:
            self._status.setText(message + " Press Reload when it finishes.")

    def _show_modules(self) -> None:
        from .modules_dialog import ModulesDialog
        ModulesDialog(self._mod_status, self._is_server, self._scripts,
                      lambda msg: widget_is_valid(self._status) and self._status.setText(msg),
                      self._widget).exec()
        self._detect_modules()

    def _update_buttons(self) -> None:
        running = self._run_worker is not None
        self._btn["run"].setEnabled(self._current is not None and not running)
        self._btn["stop"].setEnabled(running)
        self._btn["view"].setEnabled(self._current is not None)
        self._edit.setEnabled(self._current is not None)
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
        self._append(library.prompt_line(script), plain=True)
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

    def _append(self, line: str, plain: bool = False) -> None:
        """Append one line, coloured. A `----` rule restyles the line above it as
        a table header -- the header is only known once its rule arrives."""
        if not widget_is_valid(self._output):
            return
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if output_style.is_rule(line) and not self._output.document().isEmpty():
            header = QTextCursor(self._output.document().lastBlock())
            header.select(QTextCursor.SelectionType.BlockUnderCursor)
            header.mergeCharFormat(_format(output_style.HEADER))
        if not self._output.document().isEmpty():
            cursor.insertBlock()
        for text, kind in ([(line, output_style.PLAIN)] if plain else output_style.spans(line)):
            cursor.insertText(text, _format(kind))
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    def _finished(self, result: Optional[library.RunResult]) -> None:
        self._run_worker = None
        if not widget_is_valid(self._status):
            return
        if self._current is not None:              # ISE ends a run with a fresh prompt
            self._append("", plain=True)
            self._append(f"PS {library.working_dir(self._current)}> ", plain=True)
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

    def _edit_current(self, editor: Optional[str]) -> None:
        """Open the selected script for editing. A user script opens as it is; a
        built-in lives inside the app, so a copy goes to the scripts folder first."""
        script = self._current
        if script is None:
            return
        if script.path:
            path, note = script.path, ""
        else:
            try:
                path, created = library.export_for_editing(script)
            except OSError as e:
                self._banner.set_error(f"Could not copy the script to edit it: {e}")
                self._banner.show()
                return
            note = ("Copied to your scripts folder -- edit the copy; the built-in stays as it is."
                    if created else "You already have a copy of this script -- opening it.")
        self._open_in_editor(path, editor, note)
        self._reload()
        self._select_path(path)

    def _open_in_editor(self, path: str, editor: Optional[str], note: str) -> None:
        argv = library.editor_command(path, editor)
        try:
            subprocess.Popen(argv, creationflags=library.CREATE_NO_WINDOW)
        except OSError as e:
            self._banner.set_error(f"Could not open an editor ({argv[0]}): {e}")
            self._banner.show()
            return
        used = editor or next((label for label, exe in library.editors() if exe in argv), argv[0])
        self._status.setText(f"{note} Opened {os.path.basename(path)} in {used}. "
                             "Changes apply the next time you run it.".strip())

    def _select_path(self, path: str) -> None:
        from PyQt6.QtWidgets import QTreeWidgetItemIterator
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            node = it.value()
            script = node.data(0, _SCRIPT_ROLE)
            if script is not None and script.path and os.path.normcase(script.path) == os.path.normcase(path):
                self._tree.setCurrentItem(node)
                return
            it += 1

    def _new_script(self) -> None:
        title, ok = QInputDialog.getText(self._widget, "New script", "Name of the new script:")
        if not ok or not title.strip():
            return
        try:
            path = library.new_script(title.strip())
        except OSError as e:
            self._banner.set_error(f"Could not create the script: {e}")
            self._banner.show()
            return
        self._open_in_editor(path, None, "Created from a template.")
        self._reload()
        self._select_path(path)

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
