"""App Buster -- every app on the PC in one list, O&O AppBuster style.

Windows apps (Store/UWP/MSIX), desktop apps (Win32/MSI), hidden and
installable ones, system and framework packages (shown, never removable),
orphaned leftovers and defect entries. Smart View chips, Cards or Details,
curated keep/optional/remove recommendations, removal by scope with every
result read back, install for your account, winget updates, change tracking
and a restore point before the first change.

The engine (`engine/`) is Qt-free and tested headless; this file is the pane.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Dict, List, Optional, Sequence

from PyQt6.QtCore import QModelIndex, Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QListView, QMenu, QMessageBox, QProgressDialog,
                             QPushButton, QStackedWidget, QTabBar, QTableView, QToolButton,
                             QVBoxLayout, QWidget)

from core.admin_utils import is_admin
from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import set_role
from core.widget_life import widget_is_valid
from core.worker import Worker
from ui.chips import set_chip_counts
from ui.error_banner import ErrorBanner

from . import dialogs
from .app_list import RECORD_ROLE, AppModel, CardDelegate
from .engine import actions as act
from .engine import extras, model as m, scan, views

logger = logging.getLogger(__name__)

CFG = "modules.app_buster."


def _seen_path() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), "WindowsTweaker", "app_buster", "seen.json")


class AppBusterWidget(QWidget):
    def __init__(self, module: "AppBusterModule") -> None:
        super().__init__()
        self._module = module
        self._workers: List[Worker] = []
        self._rows: List[m.AppRecord] = []
        self._new_keys: set = set()
        self._baseline = None
        self._baseline_loaded = False
        self._view = "all"
        self._type_tab = ""
        self._busy = False
        self._fingerprint = None
        self._restore_asked = False
        self._opts = views.ViewOptions(bool(self._cfg("show_ms", True)),
                                       bool(self._cfg("show_installable", False)))
        self._sort = str(self._cfg("sort", "recent"))
        self._descending = bool(self._cfg("descending", True))
        self._model = AppModel(self)
        self._model.fields = list(self._cfg("fields", self._model.fields))
        self._model.on_check = lambda _k, _on: self._update_action_bar()
        self._build()

    # ---- config ----------------------------------------------------------------------------
    def _cfg(self, key: str, default):
        config = getattr(getattr(self._module, "app", None), "config", None)
        if config is None:
            return default
        value = config.get(CFG + key, default)
        return default if value is None else value

    def _save_cfg(self, key: str, value) -> None:
        config = getattr(getattr(self._module, "app", None), "config", None)
        if config is not None:
            config.set(CFG + key, value)

    # ---- layout ----------------------------------------------------------------------------
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(self._build_title_bar())
        self._counter = QLabel("")
        set_role(self._counter, "statusInfo")
        lay.addWidget(self._counter)
        self._chips_row = QHBoxLayout()
        self._chips: Dict[str, tuple] = {}
        self._build_chips()
        lay.addLayout(self._chips_row)
        self._notice = QWidget()
        n_lay = QHBoxLayout(self._notice)
        n_lay.setContentsMargins(6, 2, 6, 2)
        self._notice_label = QLabel("")
        set_role(self._notice_label, "noticeBanner")
        show_all = QPushButton("Show all")
        show_all.clicked.connect(self._show_all)
        n_lay.addWidget(self._notice_label, 1)
        n_lay.addWidget(show_all)
        self._notice.hide()
        lay.addWidget(self._notice)
        self._banner = ErrorBanner("", self)
        self._banner.hide()
        lay.addWidget(self._banner)
        lay.addWidget(self._build_action_bar())
        self._tabs = QTabBar(self)
        self._tabs.setVisible(bool(self._cfg("group", False)))
        self._tabs.currentChanged.connect(self._type_tab_changed)
        lay.addWidget(self._tabs)
        self._stack = QStackedWidget(self)
        self._cards = QListView(self)
        self._cards.setModel(self._model)
        self._cards.setItemDelegate(CardDelegate(self._model, self._cards))
        self._cards.setUniformItemSizes(True)
        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionsClickable(True)
        self._table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        for view in (self._cards, self._table):
            view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda pos, v=view: self._context_menu(v, pos))
            view.doubleClicked.connect(self._properties_of)
        self._empty = QLabel("No apps match.", alignment=Qt.AlignmentFlag.AlignCenter)
        for w in (self._cards, self._table, self._empty):
            self._stack.addWidget(w)
        lay.addWidget(self._stack, 1)
        self._status = QLabel("")
        lay.addWidget(self._status)
        self._set_layout(str(self._cfg("layout", "cards")))
        for seq, fn in (("F5", self.rescan), ("Ctrl+F", self._search.setFocus),
                        ("Delete", self._uninstall_selected), ("Ctrl+C", self._copy_selected),
                        ("Return", self._properties_current)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    def _build_title_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        for title, builder in (("File", self._file_menu), ("View", self._view_menu),
                               ("Select", self._select_menu), ("Actions", self._actions_menu)):
            btn = QToolButton(self)
            btn.setText(title)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(btn)
            menu.aboutToShow.connect(lambda mn=menu, b=builder: (mn.clear(), b(mn)))
            btn.setMenu(menu)
            bar.addWidget(btn)
        bar.addStretch(1)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search apps…")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(240)
        self._search.textChanged.connect(lambda _t: self._apply(search_moved=True))
        bar.addWidget(self._search)
        self._sort_box = QComboBox(self)
        for key, label in views.SORTS:
            self._sort_box.addItem(label, key)
        self._sort_box.setCurrentIndex(max(0, [k for k, _l in views.SORTS].index(self._sort)
                                           if self._sort in dict(views.SORTS) else 0))
        self._sort_box.currentIndexChanged.connect(self._sort_changed)
        bar.addWidget(QLabel("Sort:"))
        bar.addWidget(self._sort_box)
        self._dir_btn = QToolButton(self)
        self._dir_btn.clicked.connect(self._flip_direction)
        bar.addWidget(self._dir_btn)
        self._layout_btns = {}
        for key, label in (("cards", "Cards"), ("details", "Details")):
            b = QToolButton(self)
            b.setText(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._set_layout(k))
            self._layout_btns[key] = b
            bar.addWidget(b)
        self._update_dir_button()
        return bar

    def _build_chips(self) -> None:
        from PyQt6.QtWidgets import QButtonGroup
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label, _fn in views.SMART_VIEWS:
            chip = QPushButton(label, self)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.clicked.connect(lambda _=False, k=key: self._pick_view(k))
            group.addButton(chip)
            self._chips[key] = (chip, label)
            self._chips_row.addWidget(chip)
        self._chips_row.addStretch(1)

    def _build_action_bar(self) -> QWidget:
        bar = QWidget(self)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 2, 6, 2)
        self._sel_label = QLabel("")
        set_role(self._sel_label, "metric")
        lay.addWidget(self._sel_label)
        self._bar_buttons = {}
        for key, label, fn in (("uninstall", "Uninstall", self._uninstall_checked),
                               ("install", "Install", self._install_checked),
                               ("update", "Update", self._update_checked),
                               ("all", "Select all", lambda: self._select("all")),
                               ("reset", "Reset selection", self._reset_selection)):
            b = QPushButton(label, bar)
            b.clicked.connect(fn)
            self._bar_buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        bar.hide()
        self._action_bar = bar
        return bar

    # ---- menus -----------------------------------------------------------------------------
    def _file_menu(self, menu: QMenu) -> None:
        self._add(menu, "Refresh\tF5", self.rescan)
        self._add(menu, "Create restore point…", self._restore_point_now)

    def _view_menu(self, menu: QMenu) -> None:
        info = menu.addMenu("App information")
        for key, label, _on in views.FIELDS:
            a = QAction(label, info, checkable=True, checked=key in self._model.fields)
            a.toggled.connect(lambda on, k=key: self._toggle_field(k, on))
            info.addAction(a)
        for label, checked, fn in (
                ("Group by type", self._tabs.isVisible(), self._toggle_group),
                ("Show Microsoft apps", self._opts.show_microsoft, self._toggle_ms),
                ("Show installable Windows apps", self._opts.show_installable, self._toggle_installable)):
            a = QAction(label, menu, checkable=True, checked=checked)
            a.toggled.connect(fn)
            menu.addAction(a)
        menu.addSeparator()
        self._add(menu, "Cards", lambda: self._set_layout("cards"))
        self._add(menu, "Details", lambda: self._set_layout("details"))

    def _select_menu(self, menu: QMenu) -> None:
        for key, label, _fn in views.SELECTIONS:
            self._add(menu, label, lambda k=key: self._select(k))
        menu.addSeparator()
        self._add(menu, "Reset selection", self._reset_selection)

    def _actions_menu(self, menu: QMenu, recs: Optional[List[m.AppRecord]] = None) -> None:
        recs = recs if recs is not None else self._selected_records()
        one = recs[0] if len(recs) == 1 else None
        self._add(menu, "Uninstall", lambda: self._uninstall(recs),
                  any(r.can_uninstall for r in recs))
        self._add(menu, "Install", lambda: self._install(recs), any(r.can_install for r in recs))
        self._add(menu, "Update", lambda: self._update(recs), any(r.update for r in recs))
        self._add(menu, "Modify", lambda: self._modify(one), bool(one and one.can_modify))
        menu.addSeparator()
        self._add(menu, "Properties", lambda: self._properties(one), one is not None)
        self._add(menu, "Copy to clipboard", lambda: self._copy(recs), bool(recs))
        self._add(menu, "Browse folder…", lambda: self._browse(one),
                  bool(one and one.install_location and os.path.isdir(one.install_location)))

    @staticmethod
    def _add(menu: QMenu, label: str, fn, enabled: bool = True) -> QAction:
        a = menu.addAction(label)
        a.setEnabled(enabled)
        a.triggered.connect(lambda _=False: fn())
        return a

    def _context_menu(self, view, pos) -> None:
        index = view.indexAt(pos)
        if not index.isValid():
            return
        if not view.selectionModel().isRowSelected(index.row(), QModelIndex()):
            view.selectRow(index.row()) if isinstance(view, QTableView) else view.setCurrentIndex(index)
        menu = QMenu(self)
        self._actions_menu(menu, self._selected_records())
        menu.exec(view.viewport().mapToGlobal(pos))

    # ---- scanning --------------------------------------------------------------------------
    def rescan(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._counter.setText("Scanning… 0 apps found")
        self._counter.show()
        load_baseline = not self._baseline_loaded

        def work(worker):
            def progress(n, _what):
                worker.signals.log_line.emit(str(n))
            result = scan.scan(progress)
            store = extras.SeenStore(_seen_path())
            baseline = store.load() if load_baseline else None
            store.save(r.key for r in result.rows)
            return result, baseline, scan.fingerprint()

        w = Worker(work)
        w.signals.log_line.connect(lambda n: widget_is_valid(self._counter) and
                                   self._counter.setText(f"Scanning… {n} apps found"))
        w.signals.result.connect(self._scanned)
        w.signals.error.connect(self._scan_failed)
        w.signals.cancelled.connect(self._scan_cancelled)
        self._start(w)

    def _scan_cancelled(self) -> None:
        self._busy = False

    def _scan_failed(self, message: str) -> None:
        self._busy = False
        if not widget_is_valid(self._banner):
            return
        self._counter.hide()
        self._banner.set_error(f"Scan failed: {message}")

    def _scanned(self, payload) -> None:
        self._busy = False
        if not widget_is_valid(self._stack):
            return
        result, baseline, fp = payload
        if not self._baseline_loaded:
            self._baseline, self._baseline_loaded = baseline, True
        self._fingerprint = fp
        old = {r.key: r for r in self._rows}
        for r in result.rows:                       # keep measured sizes/updates across a rescan
            prev = old.get(r.key)
            if prev is not None and prev.version == r.version:
                r.files_bytes = r.files_bytes if r.files_bytes is not None else prev.files_bytes
                r.data_bytes = prev.data_bytes if r.data_bytes is None else r.data_bytes
                r.update, r.winget_id = prev.update, prev.winget_id
        self._rows = result.rows
        self._new_keys = extras.newly_discovered(self._rows, self._baseline)
        live = {r.key for r in self._rows}
        self._model.checked &= live
        self._model.icons.clear()
        self._counter.hide()
        if result.problems:
            self._banner.set_error(" ".join(result.problems))
        else:
            self._banner.clear()
        self._apply()
        self._fill_extras()

    def _fill_extras(self) -> None:
        rows = [r for r in self._rows if r.storage is None]

        def work(worker):
            for r in rows:
                if worker.is_cancelled:
                    return None
                extras.measure(r)
            worker.signals.progress.emit(50)
            ups, err = extras.query_upgrades()
            return ups, err

        w = Worker(work)
        w.signals.progress.connect(lambda _p: widget_is_valid(self._stack) and self._extras_partial())
        w.signals.result.connect(self._extras_done)
        self._status.setText("Measuring storage and checking winget for updates…")
        self._start(w)

    def _extras_partial(self) -> None:
        self._model.refresh_values()
        self._apply(keep_rows=True)

    def _extras_done(self, payload) -> None:
        if not widget_is_valid(self._stack) or payload is None:
            return
        ups, err = payload
        matched = extras.attach_updates(self._rows, ups or [])
        self._model.refresh_values()
        self._apply(keep_rows=True)
        self._status.setText(f"winget: {matched} update(s) available." if ups is not None
                             else f"winget update check did not complete: {err}")

    def refresh_if_changed(self) -> None:
        """The auto-refresh tick: rescan only if something was installed or removed."""
        if self._busy or self._fingerprint is None:
            return
        if scan.fingerprint() != self._fingerprint:
            self.rescan()

    # ---- filtering / view ------------------------------------------------------------------
    def _apply(self, search_moved: bool = False, keep_rows: bool = False) -> None:
        text = self._search.text()
        counts = views.view_counts(self._rows, self._new_keys, self._opts, text)
        if search_moved and text:
            self._view = views.pick_view(self._view, counts)
        shown = set(views.visible_views(counts))
        for key, (chip, _label) in self._chips.items():
            chip.setVisible(key in shown or key == self._view)
            chip.setChecked(key == self._view)
        set_chip_counts(self._chips, counts)
        self._chips["all"][0].setText(f"All ({counts.get('all', 0)})")
        rows = views.arrange(self._rows, self._view, text, self._opts, self._new_keys,
                             self._sort, self._descending, self._type_tab)
        if self._tabs.isVisible():
            self._refresh_type_tabs(views.arrange(self._rows, self._view, text, self._opts,
                                                  self._new_keys, self._sort, self._descending))
        if keep_rows and [r.key for r in rows] == [r.key for r in self._model.rows]:
            self._model.refresh_values()
        else:
            self._model.set_rows(rows)
            self._size_columns()
        hidden = views.hidden_by_options(self._rows, self._opts)
        self._notice_label.setText(f"{hidden} apps are hidden by the View options.")
        self._notice.setVisible(hidden > 0)
        self._stack.setCurrentIndex(2 if not rows and self._rows else
                                    (0 if self._layout == "cards" else 1))
        if not rows and text and not any(counts.values()):
            self._empty.setText(f"No app matches “{text}”.")
        elif not rows:
            self._empty.setText("No apps in this view.")
        self._update_action_bar()

    def _size_columns(self) -> None:
        """Details: Name gets the room, the rest fit their content once."""
        header = self._table.horizontalHeader()
        if self._model.rowCount() == 0:
            return
        self._table.resizeColumnsToContents()
        header.resizeSection(0, max(header.sectionSize(0), 320) if header.sectionSize(0) < 420 else 420)
        for col in range(1, self._model.columnCount()):
            header.resizeSection(col, min(max(header.sectionSize(col), 90), 260))

    def _refresh_type_tabs(self, rows) -> None:
        tabs = views.type_tabs(rows)
        labels = [("", f"All types ({len(rows)})")] + [(t, f"{t} ({n})") for t, n in tabs]
        self._tabs.blockSignals(True)
        while self._tabs.count():
            self._tabs.removeTab(0)
        for key, label in labels:
            i = self._tabs.addTab(label)
            self._tabs.setTabData(i, key)
        keys = [k for k, _l in labels]
        if self._type_tab not in keys:
            self._type_tab = ""
        self._tabs.setCurrentIndex(keys.index(self._type_tab))
        self._tabs.blockSignals(False)

    def _type_tab_changed(self, index: int) -> None:
        self._type_tab = self._tabs.tabData(index) or ""
        self._apply()

    def _pick_view(self, key: str) -> None:
        self._view = key
        self._apply()

    def _show_all(self) -> None:
        self._opts.show_microsoft = self._opts.show_installable = True
        self._save_cfg("show_ms", True)
        self._save_cfg("show_installable", True)
        self._apply()

    def _toggle_ms(self, on: bool) -> None:
        self._opts.show_microsoft = on
        self._save_cfg("show_ms", on)
        self._apply()

    def _toggle_installable(self, on: bool) -> None:
        self._opts.show_installable = on
        self._save_cfg("show_installable", on)
        self._apply()

    def _toggle_group(self, on: bool) -> None:
        self._tabs.setVisible(on)
        self._type_tab = ""
        self._save_cfg("group", on)
        self._apply()

    def _toggle_field(self, key: str, on: bool) -> None:
        chosen = set(self._model.fields) | {key} if on else set(self._model.fields) - {key}
        fields = [k for k, _l, _o in views.FIELDS if k in chosen]
        self._model.set_fields(fields)
        self._save_cfg("fields", fields)
        self._apply()

    def _set_layout(self, key: str) -> None:
        self._layout = "details" if key == "details" else "cards"
        for k, b in self._layout_btns.items():
            b.setChecked(k == self._layout)
        self._save_cfg("layout", self._layout)
        if self._stack.currentIndex() != 2:
            self._stack.setCurrentIndex(0 if self._layout == "cards" else 1)

    def _sort_changed(self, index: int) -> None:
        self._sort = self._sort_box.itemData(index)
        self._descending = self._sort in views.DESCENDING_BY_DEFAULT
        self._save_cfg("sort", self._sort)
        self._save_cfg("descending", self._descending)
        self._update_dir_button()
        self._apply()

    def _flip_direction(self) -> None:
        self._descending = not self._descending
        self._save_cfg("descending", self._descending)
        self._update_dir_button()
        self._apply()

    def _update_dir_button(self) -> None:
        self._dir_btn.setText("↓" if self._descending else "↑")
        self._dir_btn.setToolTip("Descending" if self._descending else "Ascending")

    def _header_clicked(self, section: int) -> None:
        sort = views.FIELD_SORT.get(self._model.column_field(section), "name")
        keys = [k for k, _l in views.SORTS]
        if sort == self._sort:
            self._flip_direction()
        else:
            self._sort_box.setCurrentIndex(keys.index(sort))

    # ---- selection -------------------------------------------------------------------------
    def _checked_records(self) -> List[m.AppRecord]:
        return [r for r in self._rows if r.key in self._model.checked]

    def _selected_records(self) -> List[m.AppRecord]:
        view = self._cards if self._layout == "cards" else self._table
        rows = sorted({i.row() for i in view.selectionModel().selectedRows()}) if view.selectionModel() else []
        return [r for r in (self._model.record(i) for i in rows) if r is not None]

    def _select(self, which: str) -> None:
        self._model.set_checked_many(views.select(self._model.rows, which), True)

    def _reset_selection(self) -> None:
        self._model.set_checked_many(list(self._model.checked), False)

    def _update_action_bar(self) -> None:
        recs = self._checked_records()
        self._action_bar.setVisible(bool(recs))
        self._sel_label.setText(f"{len(recs)} selected")
        self._bar_buttons["uninstall"].setEnabled(any(r.can_uninstall for r in recs))
        self._bar_buttons["install"].setVisible(any(r.can_install for r in recs))
        self._bar_buttons["update"].setVisible(any(r.update for r in recs))

    # ---- simple actions ---------------------------------------------------------------------
    def _properties_of(self, index) -> None:
        self._properties(index.data(RECORD_ROLE))

    def _properties_current(self) -> None:
        recs = self._selected_records()
        if len(recs) == 1:
            self._properties(recs[0])

    def _properties(self, rec: Optional[m.AppRecord]) -> None:
        if rec is not None:
            dialogs.PropertiesDialog(rec, self).exec()

    def _copy_selected(self) -> None:
        self._copy(self._selected_records())

    def _copy(self, recs: Sequence[m.AppRecord]) -> None:
        if recs:
            QApplication.clipboard().setText("\n\n".join(views.properties_text(r) for r in recs))
            self._status.setText(f"Copied {len(recs)} app(s) to the clipboard.")

    def _browse(self, rec: Optional[m.AppRecord]) -> None:
        if rec and rec.install_location and os.path.isdir(rec.install_location):
            subprocess.Popen(["explorer", rec.install_location])

    # ---- changes ------------------------------------------------------------------------------
    def _uninstall_checked(self) -> None:
        self._uninstall(self._checked_records())

    def _uninstall_selected(self) -> None:
        self._uninstall(self._selected_records())

    def _install_checked(self) -> None:
        self._install(self._checked_records())

    def _update_checked(self) -> None:
        self._update(self._checked_records())

    def _restore_first(self) -> Optional[bool]:
        """Ask once per session; None cancels the action."""
        if self._restore_asked:
            return False
        answer = dialogs.ask_restore_point(self, is_admin())
        if answer is not None:
            self._restore_asked = True
        return answer

    def _restore_point_now(self) -> None:
        if not is_admin():
            QMessageBox.information(self, "Restore point",
                                    "Creating a restore point needs administrator rights.")
            return
        self._run_batch("Creating restore point", [], act.SCOPE_USER, restore=True, removal=False,
                        kind="restore")

    def _uninstall(self, recs: Sequence[m.AppRecord]) -> None:
        recs = [r for r in recs if r.can_uninstall]
        if not recs or self._busy:
            return
        dlg = dialogs.RemovalDialog(recs, is_admin(), self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        restore = self._restore_first()
        if restore is None:
            return
        self._run_batch("Removing", recs, dlg.scope, restore=restore, removal=True)

    def _install(self, recs: Sequence[m.AppRecord]) -> None:
        recs = [r for r in recs if r.can_install]
        if recs and not self._busy:
            self._run_batch("Installing", recs, act.SCOPE_USER, restore=False, removal=False, kind="install")

    def _update(self, recs: Sequence[m.AppRecord]) -> None:
        recs = [r for r in recs if r.update]
        if recs and not self._busy:
            self._run_batch("Updating", recs, act.SCOPE_USER, restore=False, removal=False, kind="update")

    def _modify(self, rec: Optional[m.AppRecord]) -> None:
        if rec is not None and rec.can_modify and not self._busy:
            self._run_batch("Modifying", [rec], act.SCOPE_USER, restore=False, removal=False, kind="modify")

    def _run_batch(self, verb: str, recs: Sequence[m.AppRecord], scope: str, restore: bool,
                   removal: bool, kind: str = "remove") -> None:
        """One worker for the whole batch; progress shows 'N of M' and the app."""
        self._busy = True
        runner = act.Runner()
        total = len(recs)
        progress = QProgressDialog(f"{verb}…", "Cancel", 0, max(total, 1), self)
        progress.setWindowTitle(f"{verb} ({act.SCOPE_LABELS[scope]})" if removal else verb)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        lines: List[str] = []
        outcomes: List[act.Outcome] = []      # outside the worker: a cancel keeps what was done

        def work(worker):
            log = worker.signals.log_line.emit
            if restore:
                from core.system_restore import create_restore_point
                ok, out = create_restore_point("App Buster: before removing apps")
                log(f"#restore point {'created' if ok else 'NOT created: ' + out.strip()[-200:]}")
            for i, rec in enumerate(recs):
                if worker.is_cancelled:
                    break
                log(f"#{i}|{rec.name}")
                if kind == "install":
                    o = act.install_windows_app(rec, runner, log)
                elif kind == "update":
                    o = act.update_app(rec, runner, log)
                elif kind == "modify":
                    o = act.modify_app(rec, runner, log, lambda: worker.is_cancelled)
                else:
                    o = act.remove(rec, scope, runner, log, lambda: worker.is_cancelled)
                outcomes.append(o)
            return outcomes

        def on_line(text: str) -> None:
            if not widget_is_valid(progress):
                return
            if text.startswith("#") and "|" in text:
                i, name = text[1:].split("|", 1)
                progress.setValue(int(i))
                progress.setLabelText(f"{verb}: {int(i) + 1} of {total}\n{name}")
            else:
                lines.append(text.lstrip("#"))

        w = Worker(work)
        w.signals.log_line.connect(on_line)
        progress.canceled.connect(w.cancel)
        w.signals.result.connect(lambda done: self._batch_done(done, scope, kind, lines, progress))
        w.signals.error.connect(lambda msg: self._batch_failed(msg, progress))
        # Cancel takes effect before the next app; apps already done stay done and are reported.
        w.signals.cancelled.connect(lambda: self._batch_done(list(outcomes), scope, kind, lines, progress))
        self._start(w)

    def _batch_failed(self, message: str, progress) -> None:
        self._busy = False
        if widget_is_valid(progress):
            progress.close()
        if widget_is_valid(self._status):
            self._status.setText(f"Stopped: {message}")
            self.rescan()

    def _batch_done(self, outcomes, scope: str, kind: str, lines: List[str], progress) -> None:
        self._busy = False
        if widget_is_valid(progress):
            progress.close()
        if not widget_is_valid(self._stack):
            return
        if kind == "restore":
            dialogs.LogDialog("Restore point", lines, self).exec()
            return
        if kind != "remove":
            ok = sum(1 for o in outcomes if o.ok)
            summary = [f"{o.rec.name}: {o.state}{' -- ' + o.reason if o.reason else ''}" for o in outcomes]
            dialogs.LogDialog(f"{ok} of {len(outcomes)} done", summary + [""] + lines, self).exec()
            self.rescan()
            return
        outcomes = self._resolve_locked(outcomes, scope)
        if not outcomes:
            self.rescan()
            return
        report = dialogs.ResultDialog(outcomes, scope, self)
        report.exec()
        self._model.set_checked_many([o.rec.key for o in outcomes if o.state == act.REMOVED], False)
        if report.retry:
            retry = [o.rec for o in outcomes if not o.ok]
            self._run_batch("Removing", retry, scope, restore=False, removal=True)
        else:
            self.rescan()

    def _resolve_locked(self, outcomes, scope: str):
        """Ask about each locked app: close and retry, defer, or skip."""
        out = []
        for o in outcomes:
            if o.state != act.LOCKED:
                out.append(o)
                continue
            dlg = dialogs.LockedDialog(o, self)
            dlg.exec()
            if dlg.choice == dlg.CLOSE:
                act.close_programs(o.blockers, lambda line: logger.info("app buster: %s", line))
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    o = act.remove(o.rec, scope, act.Runner(), lambda line: logger.info("%s", line),
                                   lambda: False)
                finally:
                    QApplication.restoreOverrideCursor()
            elif dlg.choice == dlg.RESTART:
                o = act.remove_at_restart(o.rec, scope)
            else:
                o = act.Outcome(o.rec, act.SKIPPED, o.reason or act.REASON_IN_USE, o.blockers)
            out.append(o)
        return out

    # ---- workers ---------------------------------------------------------------------------
    def _start(self, w: Worker) -> None:
        self._workers = [x for x in self._workers if not getattr(x, "_done", False)]
        w.signals.finished.connect(lambda: setattr(w, "_done", True))
        self._workers.append(w)
        pool = getattr(getattr(self._module, "app", None), "thread_pool", None)
        if pool is None:
            from PyQt6.QtCore import QThreadPool
            pool = QThreadPool.globalInstance()
        pool.start(w)

    def cancel_all(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._busy = False


class AppBusterModule(BaseModule):
    name = "App Buster"
    icon = "🧹"
    description = "Every app on the PC: remove, install, update; orphaned and defect entries"
    group = ModuleGroup.MANAGE
    requires_admin = False

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[AppBusterWidget] = None
        self._loaded = False

    def on_start(self, app) -> None:
        self.app = app

    def create_widget(self) -> QWidget:
        self._widget = AppBusterWidget(self)
        return self._widget

    def on_activate(self) -> None:
        if self._widget is not None and not self._loaded:
            self._loaded = True
            QTimer.singleShot(0, self._widget.rescan)

    def on_deactivate(self) -> None:
        """Nothing to stop: a scan in flight finishes and lands in the list."""

    def on_stop(self) -> None:
        if self._widget is not None and widget_is_valid(self._widget):
            self._widget.cancel_all()
        self.cancel_all_workers()

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        if self._widget is not None and widget_is_valid(self._widget) and self._loaded:
            self._widget.refresh_if_changed()
