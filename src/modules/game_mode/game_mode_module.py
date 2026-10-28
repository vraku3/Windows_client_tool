"""Game Mode -- the Qt side of `engine/`.

Two buttons: Game Mode ON applies what is ticked under "What it changes"
(journaled first, see `engine/journal.py`); Game Mode OFF puts back exactly
what the journal recorded and reads it back. "Recommendations" is advice
for this PC, computed from what was read. While Game Mode is on and this app
is open, the PC is kept awake and the background-download services Windows
restarts on its own are stopped again every minute. The module owns that
timer, so it runs whichever tab is on screen.
"""
from __future__ import annotations

from ui.help.debloat_widgets import explain
from . import help_text as help_info

import ctypes
import datetime
import logging
import os
import textwrap
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLabel,
                             QPlainTextEdit, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.semantic_colors import chrome, semantic
from core.table_ui import set_role
from core.widget_life import widget_is_valid
from core.worker import Worker
from ui.error_banner import ErrorBanner

from .engine import advice, catalog, session
from .engine.journal import Journal
from .engine.system import WinSystem

logger = logging.getLogger(__name__)

CONFIG_KEY = "modules.game_mode.choices"
LIVE_MS = 5_000
ENFORCE_MS = 60_000
_ES_CONTINUOUS, _ES_SYSTEM, _ES_DISPLAY = 0x80000000, 0x00000001, 0x00000002
_LEVEL_COLOR = {session.OK: "success", session.INFO: "info", session.WARN: "warning",
                session.BAD: "error", advice.GOOD: "success", advice.ACT: "error",
                advice.UNKNOWN: "warning"}
_LEVEL_WORD = {advice.ACT: "Do this", advice.WARN: "Check", advice.UNKNOWN: "Unknown",
               advice.INFO: "Note", advice.GOOD: "OK"}


def _session_id() -> int:
    sid = ctypes.c_uint(0)
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    return int(sid.value)


def _keep_awake(on: bool) -> None:
    """While Game Mode is on: no sleep, no screen-off (a controller game sends
    no keyboard input). Cleared on OFF, and by Windows when this app exits."""
    flags = _ES_CONTINUOUS | (_ES_SYSTEM | _ES_DISPLAY if on else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


class GameModeModule(BaseModule):
    name = "Game Mode"
    icon = "🎮"
    description = "Recommendations for gaming on this PC, and a reversible Game Mode ON / OFF"
    group = ModuleGroup.OPTIMIZE
    requires_admin = False

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._system = WinSystem()
        self._journal = Journal()
        self._choices = session.Choices()
        self._busy = False
        self._preflight_worker = None
        self._preflight_dialog = None
        self._loaded = False
        self._groups: List[session.AppGroup] = []
        self._procs: list = []
        self._enforce_timer: Optional[QTimer] = None
        self._enforce_worker: Optional[Worker] = None
        self._off_pending = False
        self._recovery_message = ""
        self._live_timer: Optional[QTimer] = None
        # The enforce timer starts in on_start when Game Mode was left ON, so
        # its log lines can arrive before the pane is ever built: the widgets
        # are None until then and the lines wait in _pending_log.
        self._log: Optional[QPlainTextEdit] = None
        self._state = self._numbers = self._banner = None
        self._advice = self._advice_status = self._estimate = None
        self._pending_log: List[str] = []
        self._optimise_tab = None
        self._history_tab = None

    # ---- lifecycle ----------------------------------------------------------------------------
    def on_start(self, app) -> None:
        self.app = app
        config = getattr(app, "config", None)
        self._choices = session.choices_from_config(config.get(CONFIG_KEY, None) if config else None)
        if self._journal.load():
            self._start_enforcing()

    def get_refresh_interval(self):
        return None

    def on_activate(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._refresh_all()
        if self._live_timer is None:
            self._live_timer = QTimer()
            self._live_timer.timeout.connect(self._live_tick)
        self._live_timer.start(LIVE_MS)

    def on_deactivate(self) -> None:
        self._cancel_preflight()
        if self._live_timer is not None:
            self._live_timer.stop()

    def _live_tick(self) -> None:
        """The handle count, every few seconds, while the pane is on screen
        (one NtQuerySystemInformation call -- milliseconds)."""
        if self._busy or not widget_is_valid(self._numbers):
            return
        try:
            procs = self._system.processes()
        except OSError as error:
            logger.warning("game mode: live count unreadable: %s", error)
            return
        self._show_state(session.total_handles(procs), len(procs))

    def on_stop(self) -> None:
        self._cancel_preflight()
        if self._optimise_tab is not None:
            self._optimise_tab.cancel()
        if self._live_timer is not None:
            self._live_timer.stop()
        if self._enforce_timer is not None:
            self._enforce_timer.stop()
        _keep_awake(False)
        self.cancel_all_workers()

    def _navigate_to(self, module_name: str) -> None:
        """Optimise OS's "Open in App Buster": App Buster does the removal."""
        bus = getattr(getattr(self, "app", None), "event_bus", None)
        if bus is None:
            return
        from core.events import NAV_REQUEST_MODULE, NavRequestData
        bus.publish(NAV_REQUEST_MODULE, NavRequestData(module_name=module_name))

    def _save_choices(self) -> None:
        config = getattr(getattr(self, "app", None), "config", None)
        if config is not None:
            config.set(CONFIG_KEY, self._choices.to_config())

    # ---- layout -------------------------------------------------------------------------------
    def create_widget(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addLayout(self._build_header(w))
        self._banner = ErrorBanner("", w)
        self._banner.hide()
        lay.addWidget(self._banner)
        self._tabs = QTabWidget(w)
        self._tabs.addTab(self._build_advice(w), "Recommendations")
        self._tabs.addTab(self._build_choices(w), "What Game Mode changes")
        self._log = QPlainTextEdit(w)
        self._log.setReadOnly(True)
        set_role(self._log, "iseConsole")
        for line in self._pending_log:
            self._log.appendPlainText(line)
        self._pending_log = []
        log_page = QWidget(w)
        log_layout = QVBoxLayout(log_page)
        log_layout.addWidget(self._log)
        self._tabs.addTab(log_page, "Log")
        from .optimise_tab import OptimiseTab
        self._optimise_tab = OptimiseTab(lambda: self.app.thread_pool, w, host_busy=lambda: self._busy,
                                         busy_changed=self._show_state, navigate=self._navigate_to)
        self._tabs.addTab(self._optimise_tab, "Optimise OS")
        from .history_tab import HistoryTab
        self._history_tab = HistoryTab(self._journal.folder, w)
        self._tabs.addTab(self._history_tab, "History")
        self._tabs.currentChanged.connect(self._tab_changed)
        lay.addWidget(self._tabs, 1)
        self._widget = w
        self._state.setToolTip("ON means this app has an active recorded Game Mode session; OFF means no active session. Unresolved recovery entries keep it ON so OFF can retry.")
        self._estimate.setToolTip("Estimated handle reduction from the selected running programs and their helpers. This is not a promised frame-rate or memory improvement.")
        self._show_state()
        if self._journal.active:
            age = session.session_age(self._journal)
            if age is None or age <= session.LONG_SESSION_S:
                self._show_error(f"Game Mode is still {session.age_text(self._journal)} -- this app "
                                 "was closed while it was on. Game Mode OFF restores the changes.")
        explain(w, help_info.CONTROLS, help_info.HEADERS, help_info.PANES, help_info.INTRO)
        return w

    def _tab_changed(self, index: int) -> None:
        if self._history_tab is not None and self._tabs.widget(index) is self._history_tab:
            self._history_tab.activate()

    def _build_header(self, parent: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        col = QVBoxLayout()
        self._state = QLabel(parent)
        set_role(self._state, "sectionTitle")
        self._numbers = QLabel(parent)
        set_role(self._numbers, "statusInfo")
        col.addWidget(self._state)
        col.addWidget(self._numbers)
        row.addLayout(col, 1)
        self._on_btn = QPushButton("Game Mode ON", parent)
        set_role(self._on_btn, "accentButton")
        self._on_btn.setMinimumSize(170, 40)
        self._on_btn.clicked.connect(self._turn_on)
        self._off_btn = QPushButton("Game Mode OFF", parent)
        self._off_btn.setMinimumSize(170, 40)
        self._off_btn.clicked.connect(self._turn_off)
        row.addWidget(self._on_btn)
        row.addWidget(self._off_btn)
        return row

    def _build_advice(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        lay = QVBoxLayout(page)
        top = QHBoxLayout()
        self._advice_status = QLabel("Reading this PC…", page)
        set_role(self._advice_status, "statusInfo")
        refresh = QPushButton("Refresh", page)
        refresh.clicked.connect(self._refresh_all)
        top.addWidget(self._advice_status, 1)
        top.addWidget(refresh)
        lay.addLayout(top)
        self._advice = QTreeWidget(page)
        self._advice.setHeaderLabels(["", "Finding", "What to do"])
        self._advice.setRootIsDecorated(False)
        self._advice.setWordWrap(True)
        self._advice.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._advice.setAlternatingRowColors(True)
        head = self._advice.header()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._advice, 1)
        return page

    def _build_choices(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        lay = QVBoxLayout(page)
        presets = QHBoxLayout()
        self._rec_btn = QPushButton("Recommended", page)
        self._rec_btn.setToolTip("Updaters, sync, indexing and telemetry stopped; your browsers, "
                                 "chat and editors stay open at a lower priority.")
        self._rec_btn.setToolTip(self._rec_btn.toolTip() + " This selects choices only; ON previews and asks first. OFF restores only recorded changes.")
        self._rec_btn.clicked.connect(lambda: self._apply_preset(catalog.REC))
        self._max_btn = QPushButton("Maximum", page)
        self._max_btn.setToolTip("Everything Recommended does, plus catalogued browsers, chat and "
                                 "work apps closed, more Windows background services stopped, "
                                 "transparency and animations off. Unknown programs stay unticked; "
                                 "Explorer restarts only if you tick it.")
        self._max_btn.setToolTip(self._max_btn.toolTip() + " This selects choices only; ON asks first and may end unsaved programs after eight seconds. OFF restores only recorded changes.")
        self._max_btn.clicked.connect(lambda: self._apply_preset(catalog.MAX))
        self._estimate = QLabel(page)
        set_role(self._estimate, "statusInfo")
        presets.addWidget(QLabel("Preset:", page))
        presets.addWidget(self._rec_btn)
        presets.addWidget(self._max_btn)
        presets.addWidget(self._estimate, 1)
        lay.addLayout(presets)
        self._plan_cb = QCheckBox(page)
        self._gamebar_cb = QCheckBox("Turn on Windows' own Game Mode setting (Settings > Gaming)", page)
        self._vcache_cb = QCheckBox("Start the AMD 3D V-Cache optimizer service if it is stopped", page)
        self._maint_cb = QCheckBox("Pause Windows Automatic Maintenance (defrag, scans, maintenance "
                                   "updates)", page)
        self._tasks_cb = QCheckBox("Pause scheduled updater and maintenance tasks (Google, Edge, "
                                   "Brave, OneDrive, Office updates; defrag; telemetry)", page)
        self._visuals_cb = QCheckBox("Transparency and window animations off", page)
        self._explorer_cb = QCheckBox("Restart Explorer (interrupts copies in progress in File Explorer "
                                      "and closes folder windows)", page)
        self._lower_cb = QCheckBox("Apps left open (browsers, chat, editors) run at Below Normal "
                                   "priority while Game Mode is on", page)
        self._flag_boxes = {"plan": self._plan_cb, "gamebar": self._gamebar_cb,
                            "vcache": self._vcache_cb, "maintenance": self._maint_cb,
                            "tasks": self._tasks_cb, "visuals": self._visuals_cb,
                            "explorer": self._explorer_cb, "lower": self._lower_cb}
        for attr, cb in self._flag_boxes.items():
            cb.setToolTip(help_info.FLAGS[attr])
            cb.setChecked(getattr(self._choices, attr))
            cb.toggled.connect(lambda on, a=attr: self._flag(a, on))
            lay.addWidget(cb)
        note = QLabel("Services are only STOPPED -- their startup type is never changed, so a "
                      "restart brings them back by itself. Apps are closed politely and ended "
                      "after 8 s if they do not close; Game Mode OFF reopens them as you, not as "
                      "administrator. Security software is never touched.", page)
        note.setWordWrap(True)
        set_role(note, "muted")
        lay.addWidget(note)
        self._svc_tree = self._checked_tree(page, ["Stop", "Service", "Now", "Why"])
        self._svc_tree.itemChanged.connect(self._svc_toggled)
        lay.addWidget(QLabel("Background services", page))
        lay.addWidget(self._svc_tree, 1)
        self._app_tree = self._checked_tree(page, ["Close", "Program", "Handles", "While playing"])
        self._app_tree.itemChanged.connect(self._app_toggled)
        lay.addWidget(QLabel("Programs running now", page))
        lay.addWidget(self._app_tree, 2)
        return page

    @staticmethod
    def _checked_tree(parent: QWidget, headers: List[str]) -> QTreeWidget:
        tree = QTreeWidget(parent)
        tree.setHeaderLabels(headers)
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        head = tree.header()
        for i in range(len(headers) - 1):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
        return tree

    # ---- reading ------------------------------------------------------------------------------
    def _refresh_all(self) -> None:
        if not widget_is_valid(self._advice_status):
            return
        self._advice_status.setText("Reading this PC…")
        own, sid, system = os.getpid(), _session_id(), self._system

        def work(_w):
            facts = advice.collect(system, own, sid)
            procs = system.processes()
            groups = session.running_apps(procs, sid, own, lambda pid: system.details(pid)[0],
                                          system.fullscreen_pids())
            plan = session.recommended_plan(facts.cpu, system.plans(), facts.vcache_installed)
            states = {spec.name: [(n, system.service(n)) for n in system.service_names(spec.name)]
                      for spec in catalog.SERVICES}
            return facts, groups, plan, states, procs
        w = Worker(work)
        w.signals.result.connect(self._refreshed)
        w.signals.error.connect(lambda err: self._show_error(f"Could not read this PC: {err}"))
        self._start(w)

    def _refreshed(self, payload) -> None:
        if not widget_is_valid(self._advice):
            return
        facts, groups, (plan_guid, plan_why), states, procs = payload
        self._groups, self._procs = groups, procs
        if self._choices.preset in (catalog.REC, catalog.MAX) and not self._journal.active:
            self._choices = session.preset(self._choices.preset, groups)   # follow the catalog
            self._sync_flag_boxes()
            self._save_choices()
        self._fill_advice(advice.findings(facts))
        self._advice_status.setText(f"{facts.cpu}  ·  read {datetime.datetime.now():%H:%M:%S}")
        self._plan_cb.setText("Power plan: " + (f"switch to the one this CPU wants -- {plan_why}"
                                                if plan_guid else f"left alone ({plan_why})"))
        self._vcache_cb.setVisible(facts.vcache_installed)
        self._fill_services(states)
        self._fill_apps(groups)
        self._show_state(facts.handles, facts.processes)
        self._update_estimate()

    def _fill_advice(self, items: List[advice.Finding]) -> None:
        self._advice.clear()
        for f in items:
            # Wrapped here: QTreeWidget sizes a row from its line breaks, not
            # from wrapping at the column width, and cut the detail with "...".
            row = QTreeWidgetItem([_LEVEL_WORD.get(f.level, f.level),
                                   f.title + ("\n" + textwrap.fill(f.detail, 100) if f.detail else ""),
                                   textwrap.fill(f.fix, 90)])
            row.setForeground(0, QColor(semantic(_LEVEL_COLOR.get(f.level, "info"))))
            row.setToolTip(1, f"{f.title}\n\n{f.detail}")
            row.setToolTip(2, f.fix)
            self._advice.addTopLevelItem(row)

    def _fill_services(self, states: Dict[str, list]) -> None:
        self._svc_tree.blockSignals(True)
        self._svc_tree.clear()
        for spec in catalog.SERVICES:
            found = [(n, st) for n, st in states.get(spec.name, []) if st is not None]
            if not found:
                continue                                   # not installed here
            running = [st for _n, st in found if st.running]
            st = running[0] if running else found[0][1]
            now = "unreadable" if st.running is None else ("Running" if st.running else "Stopped")
            row = QTreeWidgetItem(["", spec.label, f"{now} ({st.start_type})" if st.start_type else now,
                                   spec.why])
            row.setToolTip(1, ", ".join(n for n, _st in found))
            row.setData(0, Qt.ItemDataRole.UserRole, spec.name)
            row.setCheckState(0, Qt.CheckState.Checked if spec.name in self._choices.services
                              else Qt.CheckState.Unchecked)
            if not st.running:
                row.setForeground(2, QColor(chrome("text_muted")))
            self._svc_tree.addTopLevelItem(row)
        self._svc_tree.blockSignals(False)

    def _fill_apps(self, groups: List[session.AppGroup]) -> None:
        self._app_tree.blockSignals(True)
        self._app_tree.clear()
        for g in groups:
            ticked = g.exe in self._choices.close
            row = QTreeWidgetItem(["", g.label + (f"  ×{len(g.roots)}" if len(g.roots) > 1 else ""),
                                   f"{g.handles:,}", self._app_plan(g, ticked)])
            row.setData(0, Qt.ItemDataRole.UserRole, g.exe)
            row.setToolTip(1, g.exe + (f"\n{g.spec.why}" if g.spec else ""))
            row.setCheckState(0, Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
            self._app_tree.addTopLevelItem(row)
        self._app_tree.blockSignals(False)

    def _apply_preset(self, level: str) -> None:
        self._choices = session.preset(level, self._groups)
        self._sync_flag_boxes()
        self._save_choices()
        self._refill_ticks()

    def _sync_flag_boxes(self) -> None:
        for attr, cb in self._flag_boxes.items():
            cb.setToolTip(help_info.FLAGS[attr])
            cb.blockSignals(True)
            cb.setChecked(getattr(self._choices, attr))
            cb.blockSignals(False)

    def _refill_ticks(self) -> None:
        for tree, bucket in ((self._svc_tree, self._choices.services),
                             (self._app_tree, self._choices.close)):
            tree.blockSignals(True)
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                key = item.data(0, Qt.ItemDataRole.UserRole)
                item.setCheckState(0, Qt.CheckState.Checked if key in bucket else Qt.CheckState.Unchecked)
            tree.blockSignals(False)
        self._fill_apps(self._groups)
        self._update_estimate()

    def _update_estimate(self) -> None:
        if not widget_is_valid(self._estimate) or not getattr(self, "_procs", None):
            return
        total = session.total_handles(self._procs)
        freed = session.freed_estimate(self._procs, self._groups, self._choices.close)
        name = {catalog.REC: "Recommended", catalog.MAX: "Maximum"}.get(self._choices.preset, "Custom")
        self._estimate.setText(f"{name}: programs ticked to close hold ~{freed:,} handles (web-view "
                               f"helpers included) -> about {total - freed:,} of {total:,} left")

    def _app_plan(self, g: session.AppGroup, ticked: bool) -> str:
        if ticked:
            reopen = g.spec.reopen if g.spec else True
            if not reopen:
                return "closed (Windows starts it again when needed)"
            return "closed, reopened after" + (" with its tabs" if g.spec and g.spec.reopen_args else "")
        if g.spec and g.spec.lower and self._choices.lower:
            return "left open, Below Normal priority"
        if g.spec and not g.spec.close:
            return g.spec.why
        return "left open (tick it to close it while you play)"

    # ---- choices ------------------------------------------------------------------------------
    def _flag(self, attr: str, on: bool) -> None:
        setattr(self._choices, attr, on)
        self._choices.preset = "custom"
        self._save_choices()
        self._update_estimate()
        if attr == "lower":
            self._fill_apps(self._groups)

    def _svc_toggled(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self._toggle(self._choices.services, item.data(0, Qt.ItemDataRole.UserRole),
                         item.checkState(0) == Qt.CheckState.Checked)

    def _app_toggled(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        self._toggle(self._choices.close, item.data(0, Qt.ItemDataRole.UserRole),
                     item.checkState(0) == Qt.CheckState.Checked)
        group = next((g for g in self._groups if g.exe == item.data(0, Qt.ItemDataRole.UserRole)), None)
        if group is not None:
            self._app_tree.blockSignals(True)
            item.setText(3, self._app_plan(group, group.exe in self._choices.close))
            self._app_tree.blockSignals(False)
        self._update_estimate()

    def _toggle(self, bucket: set, key: str, on: bool) -> None:
        (bucket.add if on else bucket.discard)(key)
        self._choices.preset = "custom"
        self._save_choices()

    # ---- state --------------------------------------------------------------------------------
    def _show_state(self, handles: Optional[int] = None, processes: Optional[int] = None) -> None:
        if not widget_is_valid(self._state):
            return
        on = self._journal.active
        if on:
            self._state.setText(f"Game Mode is {session.age_text(self._journal)} "
                                f"({sum(1 for a in self._journal.actions if isinstance(a, dict) and a.get('done'))} changes)")
            age = session.session_age(self._journal)
            if self._recovery_message:
                self._show_error(self._recovery_message)
            elif age is not None and age > session.LONG_SESSION_S:
                self._show_error(session.long_session_warning(self._journal))
        else:
            self._state.setText("Game Mode is OFF")
        set_role(self._state, "statusSuccess" if on else "sectionTitle")
        self._numbers.setText(self._numbers_text(handles, processes))
        optimise_busy = self._optimise_tab is not None and self._optimise_tab.busy
        self._on_btn.setEnabled(not on and not self._busy and not optimise_busy)
        self._off_btn.setEnabled(on and not self._busy)
        for widget in (*self._flag_boxes.values(), self._svc_tree, self._app_tree,
                       self._rec_btn, self._max_btn):
            widget.setEnabled(not on and not self._busy)

    def _numbers_text(self, handles: Optional[int], processes: Optional[int]) -> str:
        d = self._journal.data
        if self._journal.active and d.get("handles_before") is not None and handles is not None:
            freed = d["handles_before"] - handles
            return (f"Handles now {handles:,} in {processes} processes  ·  before Game Mode "
                    f"{d['handles_before']:,} in {d.get('processes_before')} ({freed:,} fewer)")
        if self._journal.active and d.get("handles_after") is not None:
            freed = d.get("handles_before", 0) - d["handles_after"]
            return (f"Handles {d['handles_before']:,} -> {d['handles_after']:,} ({freed:,} freed)  ·  "
                    f"processes {d.get('processes_before')} -> {d.get('processes_after')}")
        if handles is not None:
            return f"Now: {handles:,} handles in {processes} processes"
        return ""

    def _show_error(self, text: str) -> None:
        if widget_is_valid(self._banner):
            self._banner.set_error(text)

    def _append(self, payload: str) -> None:
        level, _, text = payload.partition("\t")
        mark = {session.OK: "[OK]  ", session.INFO: "[INFO]", session.WARN: "[WARN]",
                session.BAD: "[BAD] "}.get(level, "      ")
        line = f"{datetime.datetime.now():%H:%M:%S} {mark} {text}"
        if not widget_is_valid(self._log):
            self._pending_log = (self._pending_log + [line])[-500:]
            return
        self._log.appendPlainText(line)

    # ---- ON / OFF -----------------------------------------------------------------------------
    def _confirm_on(self, items) -> bool:
        from PyQt6.QtWidgets import QDialog
        from .confirm_dialog import PreflightDialog
        self._preflight_dialog = PreflightDialog(self._widget, items)
        try:
            return self._preflight_dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._preflight_dialog = None

    def _cancel_preflight(self) -> None:
        if self._preflight_worker is not None:
            self._preflight_worker.cancel()
            self._preflight_worker = None
            self._busy = False
            self._show_state()
        if widget_is_valid(self._preflight_dialog):
            self._preflight_dialog.reject()

    def _turn_on(self) -> None:
        if (self._busy or self._journal.active
                or (self._optimise_tab is not None and self._optimise_tab.busy)):
            return
        self._busy = True
        self._show_state()
        choices = session.choices_from_config(self._choices.to_config())
        own, sid, system = os.getpid(), _session_id(), self._system
        worker = Worker(lambda w: session.preflight(system, choices, own_pid=own, session_id=sid))
        self._preflight_worker = worker
        worker.signals.result.connect(lambda items: self._preflight_ready(worker, items, choices, own, sid))
        worker.signals.error.connect(lambda error: self._preflight_failed(worker, error))
        self._start(worker)

    def _preflight_failed(self, worker, error) -> None:
        if worker is not self._preflight_worker:
            return
        self._preflight_worker = None
        self._busy = False
        if widget_is_valid(self._widget):
            self._show_state()
            self._show_error(f"Game Mode preflight could not complete: {error}")

    def _preflight_ready(self, worker, items, choices, own, sid) -> None:
        if worker is not self._preflight_worker:
            return
        self._preflight_worker = None
        if not widget_is_valid(self._widget):
            self._busy = False
            return
        if not self._confirm_on(items):
            self._busy = False
            self._show_state()
            return
        if not widget_is_valid(self._widget) or self._journal.active:
            self._busy = False
            self._show_state()
            return
        self._log.clear()
        self._tabs.setCurrentIndex(2)
        system, journal = self._system, self._journal

        def work(w):
            def log(level, text):
                w.signals.log_line.emit(f"{level}\t{text}")
            return session.turn_on(system, choices, journal, log, own, sid)
        self._run(work, self._on_done)

    def _turn_off(self) -> None:
        if self._busy or not self._journal.active:
            return
        self._busy = True
        self._show_state()
        self._tabs.setCurrentIndex(2)
        if self._enforce_timer is not None:
            self._enforce_timer.stop()
        if self._enforce_worker is not None:
            self._off_pending = True
            return
        self._start_off()

    def _start_off(self) -> None:
        system, journal = self._system, self._journal

        def work(w):
            log = lambda level, text: w.signals.log_line.emit(f"{level}\t{text}")  # noqa: E731
            return session.turn_off(system, journal, log)
        self._run(work, self._off_done)

    def _run(self, work, done) -> None:
        w = Worker(work)
        w.signals.log_line.connect(self._append)
        w.signals.result.connect(done)
        w.signals.error.connect(self._failed)
        self._start(w)

    def _on_done(self, data: dict) -> None:
        self._busy = False
        if self._history_tab is not None:
            self._history_tab.invalidate()
        self._start_enforcing()
        self._append(f"{session.OK}\tGame Mode is ON. Handles {data.get('handles_before', 0):,} -> "
                     f"{data.get('handles_after', 0):,}, processes {data.get('processes_before')} -> "
                     f"{data.get('processes_after')}. Leave this app open to keep it tight.")
        self._show_state()

    def _off_done(self, report: List[dict]) -> None:
        self._busy = False
        if self._history_tab is not None:
            self._history_tab.invalidate()
        if self._enforce_timer is not None:
            self._enforce_timer.stop()
        _keep_awake(False)
        bad = sum(1 for r in report if r["level"] == session.BAD)
        if self._journal.active:
            message = next((r["text"] for r in reversed(report)
                            if "Recovery incomplete:" in r["text"]),
                           "Recovery incomplete: journal kept. Game Mode stays ON; OFF can be tried again.")
            self._recovery_message = message
            self._append(f"{session.BAD}\t{message}")
            self._show_state()
            self._show_error(message)
        else:
            self._recovery_message = ""
            self._append(f"{session.BAD if bad else session.OK}\tGame Mode is OFF -- "
                         + ("rejected journal entries; see the Log tab." if bad
                            else "everything is back as it was."))
            self._show_state()
            if bad:
                self._show_error("Game Mode OFF: rejected journal entries -- see the Log tab.")
            elif widget_is_valid(self._banner):
                self._banner.hide()
        self._refresh_all()

    def _failed(self, err) -> None:
        self._busy = False
        if self._history_tab is not None:
            self._history_tab.invalidate()
        self._show_error(f"Game Mode stopped with an error: {err}. Whatever was changed is in the "
                         "journal -- Game Mode OFF puts it back.")
        self._journal.load()
        self._show_state()

    def _start_enforcing(self) -> None:
        _keep_awake(True)
        if self._enforce_timer is None:
            self._enforce_timer = QTimer()
            self._enforce_timer.timeout.connect(self._enforce)
        self._enforce_timer.start(ENFORCE_MS)

    def _enforce(self) -> None:
        if self._busy or self._enforce_worker is not None or not self._journal.active:
            return
        system, journal = self._system, self._journal

        def work(w):
            return session.enforce(system, journal,
                                   lambda level, text: w.signals.log_line.emit(f"{level}\t{text}"))
        w = Worker(work)
        w.signals.log_line.connect(self._append)
        w.signals.error.connect(lambda err: logger.warning("game mode: enforce failed: %s", err))
        self._enforce_worker = w
        w.signals.finished.connect(self._enforce_finished)
        self._start(w)

    def _enforce_finished(self) -> None:
        self._enforce_worker = None
        if self._off_pending:
            self._off_pending = False
            self._start_off()

    def _start(self, w: Worker) -> None:
        """Track `w` until it finishes -- the enforce tick runs every minute
        for as long as Game Mode is on, so finished workers must not pile up."""
        self._workers.append(w)
        w.signals.finished.connect(lambda: w in self._workers and self._workers.remove(w))
        self.app.thread_pool.start(w)
