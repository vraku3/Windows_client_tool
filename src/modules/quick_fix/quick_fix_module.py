from collections import OrderedDict
from typing import Dict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QFrame, QScrollArea, QGridLayout, QMessageBox, QLineEdit, QDialog,
    QListWidget,
)
from PyQt6.QtCore import QThreadPool, pyqtSignal
from PyQt6.QtGui import QFont

from core.base_module import BaseModule
from ui.empty_state import EmptyState
from ui.glance import GlanceStrip, GlanceTile
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.windows_utils import is_reboot_pending
from core.long_op_pool import get_long_op_pool
from modules.quick_fix.fix_actions import ALL_ACTIONS, FixAction
from modules.quick_fix.browser_links_card import BrowserLinksCard
import logging
logger = logging.getLogger(__name__)


class _FixCard(QFrame):
    _line = pyqtSignal(str)   # marshals output to main thread
    history_changed = pyqtSignal()

    def __init__(self, action: FixAction, last_entry: dict = None,
                 recurring_count: int = 0, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._action = action
        self._running = False
        self._worker = None   # track for cancellation
        self._last_entry = last_entry   # most recent history row for this action, if any
        self._recurring_count = recurring_count   # >= quick_fix_history.recurring_actions' min_count, or 0
        self._thread_pool = QThreadPool.globalInstance()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Title row
        title_row = QHBoxLayout()
        title_lbl = QLabel(self._action.title)
        font = title_lbl.font()
        font.setBold(True)
        title_lbl.setFont(font)
        title_row.addWidget(title_lbl)
        if self._action.reboot_required:
            badge = QLabel("⚠ Reboot required")
            badge.setStyleSheet("color: orange;")
            title_row.addStretch()
            title_row.addWidget(badge)
        layout.addLayout(title_row)

        # Description
        desc = QLabel(self._action.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        # Run button
        self._run_btn = QPushButton("Run")
        from .help_text import ACTIONS
        prompt = (" Asks for confirmation before running." if self._action.confirm_text
                  else " Starts immediately without another confirmation.")
        self._run_btn.setToolTip(ACTIONS.get(self._action.key, self._action.description + " There is no app undo.") + prompt + " Requires administrator rights.")
        self._run_btn.setFixedWidth(80)
        layout.addWidget(self._run_btn)

        # Status label -- shows a blocked precondition or the last run's
        # outcome at rest; overwritten with "Running..." while a worker is
        # in flight (see _run/_on_done/_on_error/cancel).
        self._status = QLabel("")
        self._status.setToolTip("Recorded outcome or current precondition for this repair. A completed command is not an independent check that the original problem is resolved.")
        self._status.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._status)
        self._refresh_status()

        # Output
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setMaximumHeight(150)
        mono_font = QFont("Consolas", 8)
        self._output.setFont(mono_font)
        self._output.hide()
        layout.addWidget(self._output)

        self._run_btn.clicked.connect(self._run)
        self._line.connect(self._output.appendPlainText)

    def _refresh_status(self) -> None:
        """What the status label shows while nothing is running: a
        recurring-run warning first if this action has hit
        quick_fix_history.recurring_actions' threshold (shown regardless of
        what follows -- a problem that keeps coming back is worth knowing
        about even on a machine where the precondition now blocks a further
        run, or where there is no last-run line at all), then a blocked
        precondition's own reason (checked eagerly, at build time, not only
        after a click that then goes nowhere -- e.g. "Right-size Hibernation
        File" on a machine with hibernation off, which this label now says
        up front instead of making the admin click Run to find out), else
        the most recent history entry for this exact action, else nothing."""
        parts = []
        if self._recurring_count:
            parts.append(
                f"⚠ Run {self._recurring_count} times in the last 14 days — "
                f"if this keeps happening, the underlying cause needs a look.")
        if self._action.precondition is not None:
            msg = self._action.precondition()
            if msg is not None:
                parts.append(msg)
                self._status.setText(" ".join(parts))
                return
        if self._last_entry is not None:
            parts.append(
                f"Last run: {self._last_entry['at']} — {self._last_entry['outcome']}")
        self._status.setText(" ".join(parts))

    def _run(self):
        if self._running:
            return
        action = self._action

        if action.precondition is not None:
            msg = action.precondition()
            if msg is not None:
                self._status.setText(msg)
                return

        if action.confirm_text is not None:
            mb = QMessageBox(self)
            mb.setWindowTitle(action.title)
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setText(action.confirm_text)
            mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if mb.exec() != QMessageBox.StandardButton.Ok:
                return

        self._running = True
        self._run_btn.setEnabled(False)
        self._output.clear()
        self._output.show()
        self._status.setText("Running...")

        def append(line: str):
            self._line.emit(line)

        def do_work(_w):
            from modules.quick_fix import fix_actions
            self._worker = _w
            fix_actions.begin_tracking()
            try:
                action.fn(append)
            finally:
                failed = fix_actions.end_tracking()
            if failed:
                # Raising sends the run to _on_error: "error" in the history,
                # never "ok" for a fix whose commands were refused.
                raise RuntimeError(f"{len(failed)} step(s) failed: " + "; ".join(failed[:3]))

        self._worker = Worker(do_work)
        w = self._worker
        w.signals.result.connect(lambda _r, w=w: self._on_done(w))
        w.signals.error.connect(lambda err, w=w: self._on_error(err, w))
        pool = get_long_op_pool() if action.long_running else self._thread_pool
        pool.start(w)

    def _on_done(self, worker) -> None:
        if self._worker is not worker:
            # Either cancel() already put this card back to its resting
            # state (self._worker is None) and recorded "cancelled", or a
            # NEW run has since started (self._worker is a different,
            # newer Worker) -- either way, this result belongs to a run
            # that is no longer "the current one", and must not overwrite
            # its outcome (or the current run's own in-flight state) with
            # a second, contradictory history entry.
            return
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        from modules.quick_fix import quick_fix_history
        self._last_entry = quick_fix_history.record(self._action.title, "ok")
        self.history_changed.emit()
        self._recurring_count = quick_fix_history.recurring_actions().get(
            self._action.title, 0)
        self._refresh_status()

    def _on_error(self, error_str: str, worker) -> None:
        if self._worker is not worker:
            # Same race as _on_done: either cancelled already, or a newer
            # run has since replaced this one.
            return
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._output.appendPlainText(f"ERROR: {error_str}")
        from modules.quick_fix import quick_fix_history
        self._last_entry = quick_fix_history.record(self._action.title, "error")
        self.history_changed.emit()
        self._recurring_count = quick_fix_history.recurring_actions().get(
            self._action.title, 0)
        self._refresh_status()

    def cancel(self) -> None:
        """Cancel the running worker if any."""
        if self._worker is not None and self._running:
            # retire(): dropping a running worker freed its signals while queued
            # ones were pending -- the native abort in whole-suite runs.
            from core.worker import retire
            retire([self._worker])
            self._running = False
            self._worker = None
            self._run_btn.setEnabled(True)
            self._output.appendPlainText("Cancelled.")
            from modules.quick_fix import quick_fix_history
            self._last_entry = quick_fix_history.record(self._action.title, "cancelled")
            self._recurring_count = quick_fix_history.recurring_actions().get(
                self._action.title, 0)
            self._refresh_status()


class QuickFixHistoryDialog(QDialog):
    """Read-only browser over the local Quick Fix run history."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Fix History")
        self.resize(520, 420)
        root = QVBoxLayout(self)
        from ui.module_intro import module_intro
        root.addWidget(module_intro("Read the recent repair outcomes recorded by this app. Nothing here changes Windows or undoes completed repairs; administrator rights are not needed to read local history."))

        self._list = QListWidget()
        from modules.quick_fix import quick_fix_history
        entries = quick_fix_history.recent(limit=20)
        if entries:
            for entry in entries:
                self._list.addItem(
                    f"{entry['at']} — {entry['action']}: {entry['outcome']}")
        else:
            self._list.addItem("No actions run yet.")
        root.addWidget(self._list)

        close_btn = QPushButton("Close")
        close_btn.setToolTip("Close this history window; no repair runs and the recorded history is kept.")
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)


class QuickFixModule(BaseModule):
    name = "Quick Fix"
    icon = "🔧"
    description = "One-click system repair and maintenance tools"
    requires_admin = True
    group = ModuleGroup.TOOLS

    def __init__(self):
        super().__init__()
        self._cards: list = []
        self._workers: list = []
        self._category_headers: Dict[str, QLabel] = {}

    def create_widget(self) -> QWidget:
        outer = QWidget()
        self._widget = outer
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # View History row
        history_row = QHBoxLayout()
        history_row.addStretch()
        history_btn = QPushButton("View History")
        history_btn.clicked.connect(self._show_history)
        history_row.addWidget(history_btn)
        outer_layout.addLayout(history_row)

        # Reboot banner (hidden by default)
        self._reboot_banner = QLabel("⚠ A system reboot is pending.")
        self._reboot_banner.setStyleSheet(
            "background: #FF8800; color: white; padding: 4px; font-weight: bold;"
        )
        self._reboot_banner.hide()
        outer_layout.addWidget(self._reboot_banner)

        # Search box
        self._search = QLineEdit()
        self._search.setToolTip('Filter repair cards by title or description; no repair runs and no Windows setting changes.')
        self._search.setPlaceholderText("Search repair actions…")
        self._search.textChanged.connect(self._apply_filter)
        outer_layout.addWidget(self._search)

        self._filter_empty = EmptyState('search', "No matching repair actions", "Clear the search to see the available repair tools.", "Clear search")
        self._filter_empty.action_triggered.connect(self._search.clear)
        self._filter_empty.hide()
        outer_layout.addWidget(self._filter_empty)
        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)
        content_layout.setContentsMargins(8, 8, 8, 8)

        # Group actions by category preserving order
        categories: OrderedDict = OrderedDict([(BrowserLinksCard.ACTION.category, [BrowserLinksCard.ACTION])])
        for action in ALL_ACTIONS:
            categories.setdefault(action.category, []).append(action)

        # One read of the history file for every card, rather than one per
        # card -- see quick_fix_history.last_by_action's docstring.
        from modules.quick_fix import quick_fix_history
        last_outcomes = quick_fix_history.last_by_action()
        recurring = quick_fix_history.recurring_actions()

        self._cards.clear()
        self._category_headers.clear()
        for cat_name, actions in categories.items():
            hdr = QLabel(cat_name)
            hdr_font = hdr.font()
            hdr_font.setBold(True)
            _pt = hdr_font.pointSize()
            if _pt > 0:
                hdr_font.setPointSize(_pt + 1)
            hdr.setFont(hdr_font)
            content_layout.addWidget(hdr)
            self._category_headers[cat_name] = hdr

            grid = QGridLayout()
            grid.setSpacing(8)
            for i, action in enumerate(actions):
                if action.key == BrowserLinksCard.ACTION.key:
                    card = BrowserLinksCard(module_workers=self._workers)
                else:
                    card = _FixCard(action, last_outcomes.get(action.title),
                                    recurring.get(action.title, 0))
                card._category = cat_name
                self._cards.append(card)
                grid.addWidget(card, i // 2, i % 2)
            content_layout.addLayout(grid)

        self._build_glance(outer_layout, categories)
        content_layout.addStretch()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        # Check reboot status
        try:
            if is_reboot_pending():
                self._reboot_banner.show()
        except Exception:
            logger.warning("Ignored Exception", exc_info=True)

        from modules.cleanup.help_ui import explain, intro
        from .help_text import CONTROLS, INTRO
        explain(outer, CONTROLS, {}, {'_search': 'Filter repair cards by title or description; no repair runs and no Windows setting changes.'})
        intro(outer, INTRO)
        return outer

    def _build_glance(self, layout, categories):
        self._glance = {}
        values = (("Repair actions", len(self._cards), "Available tools"),
                  ("Recent failed runs", None, "Select to open History"),
                  ("Last run", None, "Select to open History"))
        for caption, count, detail in values:
            tile = GlanceTile(caption, tooltip=f"{caption} in the repair catalog. Opening this pane does not run any repair.")
            tile.set(count, detail)
            self._glance[caption] = tile
            if caption != "Repair actions":
                tile.clicked.connect(self._show_history)
        layout.insertWidget(0, GlanceStrip(self._glance.values()))
        for card in self._cards:
            if isinstance(card, _FixCard):
                card.history_changed.connect(self._refresh_history_glance)
        self._refresh_history_glance()

    def _refresh_history_glance(self):
        from modules.quick_fix import quick_fix_history
        runs = quick_fix_history.recent(limit=20)
        failures = sum(run.get("outcome") == "error" for run in runs)
        self._glance["Recent failed runs"].set(failures, f"In last {len(runs)} recorded runs; History",
                                               severity="danger" if failures else "info")
        last = runs[0] if runs else None
        self._glance["Last run"].set(last.get("outcome", "Unknown") if last else "No runs",
                                    last.get("action", "") if last else "History is empty",
                                    tooltip=f"{last.get('at', '') if last else 'No recorded runs'}. Select to open History.",
                                    severity="danger" if last and last.get("outcome") == "error" else "info")

    def on_activate(self) -> None:
        if hasattr(self, "_glance"):
            self._refresh_history_glance()
        for card in self._cards:
            if isinstance(card, BrowserLinksCard):
                card.activate()

    def _show_history(self) -> None:
        QuickFixHistoryDialog(self._widget).exec()

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        visible_by_category: Dict[str, bool] = {name: False for name in self._category_headers}
        for card in self._cards:
            action = card._action
            matches = (not query
                      or query in action.title.lower()
                      or query in action.description.lower())
            card.setVisible(matches)
            if matches:
                visible_by_category[card._category] = True
        self._filter_empty.setVisible(not any(visible_by_category.values()))
        for cat_name, hdr in self._category_headers.items():
            hdr.setVisible(visible_by_category[cat_name])

    def on_deactivate(self) -> None:
        # Leaving the pane does NOT cancel a running fix. `action.fn` never
        # reads the cancel flag, so sfc/DISM/netsh keep running whatever the
        # card says -- marking it "Cancelled" re-enabled Run and let a second
        # DISM start on top of the first. The cards outlive the pane being
        # hidden, so the run finishes and records its real outcome.
        pass

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        for card in self._cards:
            card.cancel()
        self._workers.clear()
        self.cancel_all_workers()
