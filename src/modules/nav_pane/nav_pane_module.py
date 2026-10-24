"""Explorer Navigation Pane: worker-driven editor hosted by Scripts."""
import logging
import os
from functools import partial

from PyQt6 import sip
from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                             QListWidgetItem, QMessageBox, QFileDialog, QSplitter,
                             QVBoxLayout, QWidget, QScrollArea)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import set_role
from core.worker import Worker, retire, track
from .engine import shell
from .engine.model import Health, Kind, values_for
from .engine.operations import Operations
from .form import build_form, button
from .widgets import EntryCard, extract_icon, icon_from_image, preview_rows

logger = logging.getLogger(__name__)


def read_context(engine):
    from modules.cleanup.cleanup_scanner.known_folders import known_folder
    entries = engine.list_entries()
    common = [(name, known_folder(name)) for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")]
    common += [("User profile", engine.profile)]
    common += [("OneDrive — " + path, path) for path in engine.roots()]
    return entries, engine.show_all_folders(), common, engine.account_check(), engine.journal.read()


class NavPane(QWidget):
    def __init__(self, engine=None, pool=None, parent=None):
        super().__init__(parent)
        self.engine = engine if engine is not None else Operations()
        self.pool = pool if pool is not None else QThreadPool.globalInstance()
        self._workers = []
        self._busy = False
        self._entries = []
        self._selected = None
        self._name_edited = False
        self._refusal = "Not checked yet; click Refresh."
        self._valid = False
        self._generation = 0
        self._loaded = False
        self._action_message = ""
        self._build()
        self._validation_timer = QTimer(self)
        self._validation_timer.setSingleShot(True)
        self._validation_timer.setInterval(250)
        self._validation_timer.timeout.connect(self.validate_path)
        self.path.textChanged.connect(self.path_changed)
        self.display_name.textEdited.connect(self.name_edited)
        self.display_name.textChanged.connect(self.form_changed)
        self.position.currentIndexChanged.connect(self.position_changed)
        self.custom.valueChanged.connect(self.form_changed)
        self.icons.currentIndexChanged.connect(self.form_changed)
        self.form_changed()
        self._enable()

    def _build(self):
        layout = QVBoxLayout(self)
        intro = QLabel("Add a folder as its own shortcut in File Explorer's navigation pane. "
                       "With Show all folders enabled, it appears under Desktop; otherwise it is a top-level entry. "
                       "Shortcuts made here can be removed without touching the real folder.")
        intro.setObjectName("moduleIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        bar = QHBoxLayout()
        button("Refresh", "Read the current navigation entries and settings again.", self.refresh, bar)
        button("New shortcut", "Clear the form to add another folder shortcut.", self.new_entry, bar)
        bar.addStretch()
        layout.addLayout(bar)
        self.status = QLabel("No data — click Refresh")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        split = QSplitter()
        split.setToolTip("Drag the divider to resize the entry list and shortcut editor.")
        self.entries = QListWidget()
        self.entries.setMinimumWidth(220)
        self.entries.setToolTip("Select a shortcut card to inspect or edit it. Windows and OneDrive entries are read-only.")
        self.entries.currentRowChanged.connect(self.select)
        split.addWidget(self.entries)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(build_form(self))
        split.addWidget(scroll)
        split.setSizes([320, 640])
        layout.addWidget(split, 1)
        self.show_all = QCheckBox("Show all folders in the navigation pane")
        self.show_all.setToolTip("Make Desktop the tree root, with these shortcuts beneath it. This changes the current user's Explorer setting.")
        self.show_all.clicked.connect(self.toggle_show_all)
        layout.addWidget(self.show_all)
        note = QLabel("Enabled: Desktop is the root. Disabled: custom shortcuts sit beside Home. Reopen Explorer after changes; sign-out may be needed.")
        note.setWordWrap(True)
        set_role(note, "muted")
        layout.addWidget(note)
        row = QHBoxLayout()
        button("Restore original folder view setting", "Restore the exact Show all folders value recorded before this app changed it.", self.revert_setting, row)
        self.settings_repair = button("Roll back interrupted view setting", "Undo an interrupted Show all folders change from the journal.", self.repair_setting, row)
        layout.addLayout(row)

    def run(self, function, callback=None, mutation=False):
        if mutation and self._busy:
            return
        if mutation:
            self._action_message = ""
            self._busy = True
            self._enable()
        worker = Worker(lambda w: function())
        track(worker)
        self._workers.append(worker)

        def result(value):
            if not sip.isdeleted(self):
                if mutation:
                    self.action_result("Operation completed successfully.")
                if callback:
                    callback(value)
                    if mutation:
                        self._action_message = self.status.text()

        def error(message):
            logger.warning("nav pane job failed: %s", message)
            if not sip.isdeleted(self):
                if mutation:
                    self._action_message = "Could not complete: " + str(message)
                self.message("Could not complete: " + str(message), "statusError")

        def finished():
            if worker in self._workers:
                self._workers.remove(worker)
            if not sip.isdeleted(self) and mutation:
                self._busy = False
                self._enable()
                self.refresh()

        worker.signals.result.connect(result)
        worker.signals.error.connect(error)
        worker.signals.finished.connect(finished)
        self.pool.start(worker)

    def message(self, text, role="statusInfo"):
        self.status.setText(text)
        set_role(self.status, role)

    def action_result(self, text, role="statusSuccess"):
        self._action_message = text
        self.message(text, role)

    def refresh(self):
        if self._busy:
            return
        self.run(partial(read_context, self.engine), self.set_context)

    def set_context(self, context):
        self._entries, checked, folders, self._refusal, data = context
        self._loaded = True
        selected_guid = self._selected.guid if self._selected else None
        self.entries.blockSignals(True)
        self.entries.clear()
        selected_row = -1
        for index, entry in enumerate(self._entries):
            item = QListWidgetItem()
            item.setToolTip(entry.detail or f"{entry.kind.value}: {entry.name}")
            card = EntryCard(entry)
            item.setSizeHint(card.sizeHint())
            self.entries.addItem(item)
            self.entries.setItemWidget(item, card)
            if entry.guid == selected_guid:
                selected_row = index
        self.entries.blockSignals(False)
        self.show_all.setChecked(checked)
        self.common.clear()
        self.common.addItem("Pick a common folder…", "")
        for label, path in folders:
            if path:
                self.common.addItem(label, path)
                self.common.setItemData(self.common.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
        self.settings_repair.setVisible("settings" in data["transactions"])
        self.message(self._refusal or self._action_message or f"{len(self._entries)} entries read. Changes apply to new Explorer windows.",
                     "statusError" if self._refusal or self._action_message.startswith("Could not complete:") else "statusInfo")
        if selected_row >= 0:
            self.entries.setCurrentRow(selected_row)
        elif selected_guid:
            self.new_entry()
        self.form_changed()
        self._enable()
        self.load_icons()

    def load_icons(self):
        for index in range(self.icons.count()):
            location = self.icons.itemData(index)
            self.run(partial(extract_icon, location), partial(self.set_icon, index))

    def set_icon(self, index, image):
        self.icons.setItemIcon(index, icon_from_image(image))

    def select(self, row):
        if not 0 <= row < len(self._entries):
            return
        self._selected = self._entries[row]
        entry = self._selected
        self.path.setText(entry.resolved_target or entry.target)
        self.display_name.setText(entry.name)
        self._name_edited = True
        self.custom.setValue(entry.sort_order)
        self.target_info.setText(f"Target: {entry.target}\nResolved folder: {entry.resolved_target or entry.target}")
        index = self.position.findData(entry.sort_order)
        self.position.setCurrentIndex(index if index >= 0 else 3)
        index = self.icons.findData(entry.icon)
        if index < 0:
            self.icons.addItem(entry.icon, entry.icon)
            index = self.icons.count() - 1
        self.icons.setCurrentIndex(index)
        self.save_button.setText("Save changes")
        self.form_changed()
        self._enable()

    def new_entry(self):
        self._selected = None
        self.target_info.clear()
        self.entries.setCurrentRow(-1)
        self._name_edited = False
        self.path.clear()
        self.display_name.clear()
        self.position.setCurrentIndex(1)
        self.save_button.setText("Add to navigation pane")
        self._enable()

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose navigation folder", self.path.text())
        if path:
            self.path.setText(path)

    def pick_common(self, index):
        if self.common.itemData(index):
            self.path.setText(self.common.itemData(index))

    def choose_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose icon source", "", "Icons (*.ico *.dll *.exe)")
        if not path:
            return
        index, ok = QInputDialog.getInt(self, "Icon index", "Index (negative means resource ID)", 0, -100000, 100000)
        if ok:
            location = f"{path},{index}"
            self.icons.addItem(os.path.basename(path) + f" ({index})", location)
            self.icons.setCurrentIndex(self.icons.count() - 1)
            self.run(partial(extract_icon, location), partial(self.set_icon, self.icons.count() - 1))

    def name_edited(self):
        self._name_edited = True

    def path_changed(self):
        self._generation += 1
        self._valid = False
        if not self._name_edited:
            self.display_name.setText(os.path.basename(self.path.text().rstrip("\\/")))
        self._validation_timer.start()
        self.form_changed()
        self._enable()

    def position_changed(self):
        self.custom.setEnabled(self.position.currentData() is None)
        self.form_changed()

    def order(self):
        value = self.position.currentData()
        return int(self.custom.value()) if value is None else value

    def form_changed(self):
        self._generation += 1
        self._valid = False
        self.preview.rows = preview_rows(self.display_name.text(), self.order(), self.show_all.isChecked(),
                                         self._entries, self._selected.guid if self._selected else None)
        self.preview.update()
        self.preview_note.setText("Desktop is the root because Show all folders is enabled." if self.show_all.isChecked()
                                  else "This entry sits beside Home because Show all folders is disabled.")
        guid = self._selected.guid if self._selected else "{NEW-GUID}"
        rows = values_for(guid, self.display_name.text(), self.path.text(), self.order(), self.icons.currentData())
        self.details.setText("\n".join(f"HKCU\\{key} [{name or '(Default)'}] = {value} (type {kind})" for key, name, value, kind in rows))
        if hasattr(self, "_validation_timer"):
            self._validation_timer.start()
        self._enable()

    def validate_path(self):
        path, name, order, icon = self.path.text(), self.display_name.text(), self.order(), self.icons.currentData()
        generation = self._generation

        def check():
            try:
                record = self.engine.journal.read()["entries"].get(selected_guid, {})
                planned = self.engine._record(selected_guid, path, name, order, icon, record, selected_entry)
                link = planned["target"] if planned["target"] != path else ""
                return True, "✓ Folder is available.", link
            except (OSError, ValueError) as error:
                return False, f"Could not use this folder: {error}", ""

        selected_guid = self._selected.guid if self._selected else None
        selected_entry = self._selected
        self.run(check, partial(self.validated, generation))

    def validated(self, generation, result):
        if generation != self._generation:
            return
        self._valid, text, link = result
        self.validation.setText(text)
        set_role(self.validation, "statusSuccess" if self._valid else "statusWarning")
        keep = bool(self._selected and link == self._selected.target)
        self.cloud.setText((f"Keep existing Target: {link}\nResolved folder: {self.path.text()}" if keep else
                            f"OneDrive folder detected: a hidden link will be created at {link}") if link else "")
        if link:
            rows = values_for(self._selected.guid if self._selected else "{NEW-GUID}", self.display_name.text(),
                              link, self.order(), self.icons.currentData())
            self.details.setText(f"Hidden junction: {link} → {self.path.text()}\n" + "\n".join(
                f"HKCU\\{key} [{name or '(Default)'}] = {value} (type {kind})" for key, name, value, kind in rows))
        self._enable()

    def _enable(self):
        entry = self._selected
        editable = entry is None or entry.kind != Kind.SYSTEM and entry.health not in (Health.UNREADABLE, Health.INCOMPLETE)
        writable = not self._busy and not self._refusal
        self.save_button.setEnabled(writable and editable and self._valid)
        self.remove_button.setEnabled(writable and editable and entry is not None)
        self.verify_button.setEnabled(not self._busy and entry is not None)
        self.open_button.setEnabled(not self._busy and entry is not None and bool(entry.target))
        incomplete = bool(entry and entry.health == Health.INCOMPLETE)
        self.finish_button.setVisible(incomplete)
        self.rollback_button.setVisible(incomplete)
        self.finish_button.setEnabled(writable)
        self.rollback_button.setEnabled(writable)
        self.show_all.setEnabled(writable)

    def save(self):
        if not self._valid or self._refusal or self._busy:
            return
        self._action_message = ""
        args = (self.path.text(), self.display_name.text(), self.order(), self.icons.currentData())
        duplicates = [e for e in self._entries if e.name.casefold() == args[1].casefold()
                      and (not self._selected or e.guid != self._selected.guid)]
        if duplicates and not self.confirm("Duplicate display name", "Another entry has this name. Create another with the same name?"):
            return
        function = partial(self.engine.update_entry, self._selected.guid, *args) if self._selected else partial(self.engine.add_entry, *args)
        self.run(function, lambda _: self.message("Registry values read back successfully. Reopen Explorer, then Verify."), True)

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def remove(self):
        if not self._selected:
            return
        entry = self._selected
        text = (f"Delete HKCU CLSID\\{entry.guid}, Desktop\\NameSpace\\{entry.guid} and its HideDesktopIcons value? "
                "Only an app-created, target-checked junction is removed. Junctions made outside this app are kept. "
                "The real folder and its contents are not touched.")
        if self.confirm("Remove navigation shortcut", text):
            self.run(partial(self.engine.remove_entry, entry.guid),
                     lambda _: self.action_result(f"Removed navigation shortcut: {entry.name}."), mutation=True)

    def open_folder(self):
        if self._selected:
            self._action_message = ""
            self.run(partial(shell_launch_folder, self._selected.resolved_target or self._selected.target), self.action_result)

    def verify(self):
        if self._selected:
            self._action_message = ""
            self.run(partial(self.engine.verify, self._selected.guid),
                     lambda result: self.action_result(result.status.value + ": " + result.detail,
                                                 "statusSuccess" if result.status.value == "VERIFIED" else "statusWarning"))

    def restart(self):
        if self.confirm("Restart Explorer", "All open Explorer windows will close. Finish file copies first. Restart Explorer now?"):
            self.run(shell.restart_explorer, self.message, True)

    def repair(self, finish):
        if not self._selected:
            return
        try:
            transaction = self.engine.journal.read()["transactions"].get(self._selected.guid, {})
        except (OSError, ValueError) as error:
            self.message(f"Could not read recovery details: {error}", "statusError")
            return
        entry = transaction.get("entry") or transaction.get("record") or transaction.get("before_record") or {}
        operation = transaction.get("operation") or ("removal" if transaction.get("record") is None else
                                                    "edit" if transaction.get("before_record") else "add")
        text = (f"{'Finish' if finish else 'Roll back'} the recorded {operation}?\n"
                f"Entry: {entry.get('name', self._selected.name)}\n"
                f"Target folder: {entry.get('resolved_target') or self._selected.resolved_target or self._selected.target}\n"
                f"Junction: {entry.get('junction') or self._selected.junction or 'None'}")
        if self.confirm("Repair interrupted change", text):
            self.run(partial(self.engine.repair, self._selected.guid, finish),
                     lambda _: self.action_result("Interrupted change finished." if finish else "Interrupted change rolled back."), mutation=True)

    def toggle_show_all(self, checked):
        self.form_changed()
        self.run(partial(self.engine.set_show_all_folders, checked),
                 lambda _: self.action_result(f"Show all folders {'enabled' if checked else 'disabled'}."), mutation=True)

    def revert_setting(self):
        self.run(self.engine.revert_show_all_folders,
                 lambda _: self.action_result("Original folder view setting restored."), mutation=True)

    def repair_setting(self):
        self.run(partial(self.engine.repair, "settings", False),
                 lambda _: self.action_result("Interrupted folder view setting rolled back."), mutation=True)

    def cancel(self):
        self._validation_timer.stop()
        retire(self._workers)


def shell_launch_folder(path):
    from core.shell_launch import launch
    reason = shell.write_refusal()
    if reason:
        raise PermissionError(reason)
    error = launch(path)
    if error:
        raise OSError(error)
    return "Folder handed to the signed-in user's Explorer."


class NavPaneModule(BaseModule):
    name = "Explorer Navigation Pane"
    icon = "📁"
    description = "Reversible folder shortcuts in Explorer's navigation pane"
    group = ModuleGroup.TOOLS
    requires_admin = False

    def __init__(self):
        super().__init__()
        self._widget = None

    def create_widget(self):
        self._widget = NavPane(pool=self.thread_pool)
        return self._widget

    def on_activate(self):
        if self._widget is not None and not sip.isdeleted(self._widget):
            self._widget.refresh()

    def on_stop(self):
        if self._widget is not None and not sip.isdeleted(self._widget):
            self._widget.cancel()

    def on_deactivate(self):
        if self._widget is not None and not sip.isdeleted(self._widget):
            self._widget._validation_timer.stop()
