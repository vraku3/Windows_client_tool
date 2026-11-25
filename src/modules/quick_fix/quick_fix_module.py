from collections import OrderedDict
import logging
from typing import Dict, List, Optional, Set

from PyQt6.QtCore import QThreadPool, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.base_module import BaseModule
from core.long_op_pool import get_long_op_pool
from core.module_groups import ModuleGroup
from core.table_ui import set_role
from core.windows_utils import is_reboot_pending
from core.worker import Worker
from modules.quick_fix.browser_links_card import BrowserLinksCard
from modules.quick_fix.console_drawer import QuickFixConsoleDrawer
from modules.quick_fix.fix_actions import ALL_ACTIONS, FixAction
from modules.quick_fix.playbook_card import PlaybookCard
from modules.quick_fix.quick_fix_playbooks import PLAYBOOKS
from modules.quick_fix.quick_fix_scanner import (
    HealthFinding,
    QuickFixHealthReport,
    scan_system_health,
)
from ui.empty_state import EmptyState
from ui.glance import GlanceStrip, GlanceTile

logger = logging.getLogger(__name__)


class _FixCard(QFrame):
    _line = pyqtSignal(str)   # marshals output to main thread
    line_logged = pyqtSignal(str, str)  # (action_title, line)
    action_started = pyqtSignal(str)    # action_title
    action_completed = pyqtSignal(str, bool, str)  # (action_title, success, message)
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
        self._is_recommended = False
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

        self._recommended_badge = QLabel("⭐ Recommended")
        set_role(self._recommended_badge, "statusSuccess")
        self._recommended_badge.hide()
        title_row.addWidget(self._recommended_badge)

        title_row.addStretch()

        if self._action.reboot_required:
            badge = QLabel("⚠ Reboot required")
            set_role(badge, "statusWarning")
            title_row.addWidget(badge)
        layout.addLayout(title_row)

        # Description
        desc = QLabel(self._action.description)
        desc.setWordWrap(True)
        set_role(desc, "muted")
        layout.addWidget(desc)

        # Run button
        self._run_btn = QPushButton("Run")
        from .help_text import ACTIONS
        prompt = (" Asks for confirmation before running." if self._action.confirm_text
                  else " Starts immediately without another confirmation.")
        self._run_btn.setToolTip(ACTIONS.get(self._action.key, self._action.description + " There is no app undo.") + prompt + " Requires administrator rights.")
        self._run_btn.setFixedWidth(80)
        layout.addWidget(self._run_btn)

        # Status label
        self._status = QLabel("")
        self._status.setToolTip("Recorded outcome or current precondition for this repair. A completed command is not an independent check that the original problem is resolved.")
        set_role(self._status, "muted")
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

    def set_recommended(self, recommended: bool) -> None:
        self._is_recommended = recommended
        self._recommended_badge.setVisible(recommended)

    def is_recommended(self) -> bool:
        return self._is_recommended

    def _refresh_status(self) -> None:
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
        self.action_started.emit(self._action.title)

        def append(line: str):
            self._line.emit(line)
            self.line_logged.emit(self._action.title, line)

        def do_work(_w):
            from modules.quick_fix import fix_actions
            fix_actions.begin_tracking()
            try:
                action.fn(append)
            finally:
                failed = fix_actions.end_tracking()
            if failed:
                first = failed[0]
                more = f" (+{len(failed) - 1} more)" if len(failed) > 1 else ""
                raise RuntimeError(f"Step failed: {first}{more}")

        w = Worker(do_work)
        w.signals.result.connect(lambda: self._on_done(w))
        w.signals.error.connect(lambda err: self._on_error(err, w))
        self._worker = w

        pool = get_long_op_pool() if action.long_running else self._thread_pool
        pool.start(w)

    def _on_done(self, worker) -> None:
        if self._worker is not worker:
            return
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        from modules.quick_fix import quick_fix_history
        self._last_entry = quick_fix_history.record(self._action.title, "ok")
        self.history_changed.emit()
        self.action_completed.emit(self._action.title, True, "")
        self._recurring_count = quick_fix_history.recurring_actions().get(
            self._action.title, 0)
        self._refresh_status()

    def _on_error(self, error_str: str, worker) -> None:
        if self._worker is not worker:
            return
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._output.appendPlainText(f"ERROR: {error_str}")
        from modules.quick_fix import quick_fix_history
        self._last_entry = quick_fix_history.record(self._action.title, "error")
        self.history_changed.emit()
        self.action_completed.emit(self._action.title, False, error_str)
        self._recurring_count = quick_fix_history.recurring_actions().get(
            self._action.title, 0)
        self._refresh_status()

    def cancel(self) -> None:
        if self._worker is not None and self._running:
            from core.worker import retire
            retire([self._worker])
            self._running = False
            self._worker = None
            self._run_btn.setEnabled(True)
            self._output.appendPlainText("Cancelled.")
            from modules.quick_fix import quick_fix_history
            self._last_entry = quick_fix_history.record(self._action.title, "cancelled")
            self.action_completed.emit(self._action.title, False, "Cancelled")
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
        self._playbook_cards: list = []
        self._workers: list = []
        self._category_headers: Dict[str, QLabel] = {}
        self._selected_category = "All"
        self._filter_chips: Dict[str, QPushButton] = {}
        self._latest_report: Optional[QuickFixHealthReport] = None

    def create_widget(self) -> QWidget:
        outer = QWidget()
        self._widget = outer
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(6)

        # 1. Top bar: History button + Glance strip
        self._build_top_bar(outer_layout)

        # 2. Pre-flight health banner
        self._build_preflight_banner(outer_layout)

        # 3. Triage playbooks section
        self._build_playbooks_section(outer_layout)

        # 4. Filter chips bar (Category tabs)
        self._build_filter_chips_bar(outer_layout)

        # 5. Search box & empty state
        self._build_search_row(outer_layout)

        # 6. Scroll area with cards grid
        self._build_cards_scroll(outer_layout)

        # 7. Collapsible console log drawer
        self._console_drawer = QuickFixConsoleDrawer(outer)
        outer_layout.addWidget(self._console_drawer)

        self._wire_console_and_cards()
        self._check_pending_reboot_banner()

        from modules.cleanup.help_ui import explain, intro
        from .help_text import CONTROLS, INTRO
        explain(outer, CONTROLS, {}, {'_search': 'Filter repair cards by title or description; no repair runs and no Windows setting changes.'})
        intro(outer, INTRO)
        return outer

    def _build_top_bar(self, layout: QVBoxLayout) -> None:
        history_row = QHBoxLayout()
        history_row.addStretch()
        history_btn = QPushButton("View History")
        history_btn.setToolTip("Inspect previous Quick Fix runs and outcomes")
        history_btn.clicked.connect(self._show_history)
        history_row.addWidget(history_btn)
        layout.addLayout(history_row)

        self._reboot_banner = QLabel("⚠ A system reboot is pending.")
        set_role(self._reboot_banner, "statusWarning")
        self._reboot_banner.hide()
        layout.addWidget(self._reboot_banner)

        # Build glance tiles
        self._glance = {}
        values = (("Repair actions", 0, "Available tools"),
                  ("Recent failed runs", None, "Select to open History"),
                  ("Last run", None, "Select to open History"))
        for caption, count, detail in values:
            tile = GlanceTile(caption, tooltip=f"{caption} in the repair catalog. Opening this pane does not run any repair.")
            tile.set(count, detail)
            self._glance[caption] = tile
            if caption != "Repair actions":
                tile.clicked.connect(self._show_history)
        layout.addWidget(GlanceStrip(self._glance.values()))

    def _build_preflight_banner(self, layout: QVBoxLayout) -> None:
        banner = QFrame()
        banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(8, 6, 8, 6)

        icon_lbl = QLabel("🩺")
        icon_lbl.setFont(QFont("", 12))
        banner_layout.addWidget(icon_lbl)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        title_lbl = QLabel("Pre-Flight System Health Scanner")
        t_font = title_lbl.font()
        t_font.setBold(True)
        title_lbl.setFont(t_font)
        info_layout.addWidget(title_lbl)

        self._scan_status_lbl = QLabel("Checks WMI, spooler, VSS writers, WinRE, and service state for issues.")
        set_role(self._scan_status_lbl, "muted")
        info_layout.addWidget(self._scan_status_lbl)
        banner_layout.addLayout(info_layout)

        banner_layout.addStretch()

        self._scan_btn = QPushButton("Run Health Scan")
        self._scan_btn.setToolTip("Scan Windows for broken services, VSS failures, stuck queues, and corrupt components")
        self._scan_btn.clicked.connect(self.run_preflight_scan)
        banner_layout.addWidget(self._scan_btn)

        layout.addWidget(banner)

    def _build_playbooks_section(self, layout: QVBoxLayout) -> None:
        self._playbooks_frame = QFrame()
        self._playbooks_frame.setFrameShape(QFrame.Shape.StyledPanel)
        pb_layout = QVBoxLayout(self._playbooks_frame)
        pb_layout.setContentsMargins(8, 6, 8, 6)
        pb_layout.setSpacing(6)

        header_row = QHBoxLayout()
        title_lbl = QLabel("⚡ Triage Playbooks (Multi-Step Recipes)")
        t_font = title_lbl.font()
        t_font.setBold(True)
        title_lbl.setFont(t_font)
        header_row.addWidget(title_lbl)

        desc_lbl = QLabel("Orchestrates sequential end-to-end repairs for common break-fix scenarios.")
        set_role(desc_lbl, "muted")
        header_row.addWidget(desc_lbl)
        header_row.addStretch()
        pb_layout.addLayout(header_row)

        grid = QGridLayout()
        grid.setSpacing(8)
        self._playbook_cards.clear()
        for idx, pb in enumerate(PLAYBOOKS):
            card = PlaybookCard(pb)
            self._playbook_cards.append(card)
            grid.addWidget(card, idx // 2, idx % 2)
        pb_layout.addLayout(grid)

        layout.addWidget(self._playbooks_frame)

    def _build_filter_chips_bar(self, layout: QVBoxLayout) -> None:
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)

        chip_categories = [
            ("All", "All"),
            ("System Repairs", "System Repairs"),
            ("Network", "Network"),
            ("Windows Update", "Windows Update"),
            ("Cache & UI", "Cache & UI"),
            ("Print", "Print"),
            ("Cleanup", "Cleanup"),
            ("Needs Attention", "⚠️ Needs Attention"),
        ]

        self._filter_chips.clear()
        for cat_key, label in chip_categories:
            btn = QPushButton(label)
            btn.setCheckable(True)
            if cat_key == "All":
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, k=cat_key: self._on_chip_clicked(k))
            self._filter_chips[cat_key] = btn
            chips_row.addWidget(btn)

        chips_row.addStretch()
        layout.addLayout(chips_row)

    def _build_search_row(self, layout: QVBoxLayout) -> None:
        self._search = QLineEdit()
        self._search.setToolTip('Filter repair cards by title or description; no repair runs and no Windows setting changes.')
        self._search.setPlaceholderText("Search repair actions…")
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)

        self._filter_empty = EmptyState('search', "No matching repair actions", "Clear the search to see the available repair tools.", "Clear search")
        self._filter_empty.action_triggered.connect(self._search.clear)
        self._filter_empty.hide()
        layout.addWidget(self._filter_empty)

    def _build_cards_scroll(self, layout: QVBoxLayout) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)
        content_layout.setContentsMargins(8, 8, 8, 8)

        categories: OrderedDict = OrderedDict([(BrowserLinksCard.ACTION.category, [BrowserLinksCard.ACTION])])
        for action in ALL_ACTIONS:
            categories.setdefault(action.category, []).append(action)

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

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        if hasattr(self, "_glance") and "Repair actions" in self._glance:
            self._glance["Repair actions"].set(len(self._cards), "Available tools")

    def _wire_console_and_cards(self) -> None:
        for card in self._cards:
            if isinstance(card, _FixCard):
                card.line_logged.connect(self._console_drawer.append_line)
                card.action_started.connect(self._console_drawer.notify_start)
                card.action_completed.connect(self._console_drawer.notify_complete)
                card.history_changed.connect(self._refresh_history_glance)

        for pb_card in self._playbook_cards:
            pb_card.line_logged.connect(self._console_drawer.append_line)
            pb_card.playbook_started.connect(self._console_drawer.notify_start)
            pb_card.playbook_finished.connect(self._console_drawer.notify_complete)

    def _check_pending_reboot_banner(self) -> None:
        try:
            if is_reboot_pending():
                self._reboot_banner.show()
        except Exception as exc:
            logger.debug("Failed checking pending reboot status: %s", exc)

    def _on_chip_clicked(self, category_key: str) -> None:
        self._selected_category = category_key
        for k, btn in self._filter_chips.items():
            btn.setChecked(k == category_key)
        self._apply_filter(self._search.text())

    def run_preflight_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._scan_status_lbl.setText("Scanning system components and service states...")
        set_role(self._scan_status_lbl, "statusWarning")

        def worker_fn(worker_ctx):
            return scan_system_health(worker=worker_ctx)

        w = Worker(worker_fn)
        w.signals.result.connect(self._on_scan_completed)
        w.signals.error.connect(self._on_scan_error)
        self._workers.append(w)
        self.app.thread_pool.start(w) if hasattr(self, "app") and hasattr(self.app, "thread_pool") else QThreadPool.globalInstance().start(w)

    def _on_scan_completed(self, report: QuickFixHealthReport) -> None:
        self._scan_btn.setEnabled(True)
        self._latest_report = report

        # Update recommended badges on all cards
        for card in self._cards:
            if isinstance(card, _FixCard):
                is_rec = card._action.key in report.recommended_keys
                card.set_recommended(is_rec)

        issue_cnt = report.issue_count
        if issue_cnt > 0:
            self._scan_status_lbl.setText(f"Found {issue_cnt} issue(s) needing attention! Recommended fixes badged.")
            set_role(self._scan_status_lbl, "statusError")
            if "Needs Attention" in self._filter_chips:
                self._filter_chips["Needs Attention"].setText(f"⚠️ Needs Attention ({issue_cnt})")
        else:
            self._scan_status_lbl.setText("Clean: No common service, VSS, or component anomalies detected.")
            set_role(self._scan_status_lbl, "statusSuccess")
            if "Needs Attention" in self._filter_chips:
                self._filter_chips["Needs Attention"].setText("⚠️ Needs Attention (0)")

        self._apply_filter(self._search.text())

    def _on_scan_error(self, err: str) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_status_lbl.setText(f"Health scan encountered error: {err}")
        set_role(self._scan_status_lbl, "statusWarning")

    def _refresh_history_glance(self):
        from modules.quick_fix import quick_fix_history
        runs = quick_fix_history.recent(limit=20)
        failures = sum(run.get("outcome") == "error" for run in runs)
        if "Recent failed runs" in self._glance:
            self._glance["Recent failed runs"].set(failures, f"In last {len(runs)} recorded runs; History",
                                                   severity="danger" if failures else "info")
        last = runs[0] if runs else None
        if "Last run" in self._glance:
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

    def _apply_filter(self, text: str = "") -> None:
        query = text.strip().lower()
        selected_cat = self._selected_category
        visible_by_category: Dict[str, bool] = {name: False for name in self._category_headers}

        for card in self._cards:
            action = card._action
            # Text matching
            text_match = (not query
                          or query in action.title.lower()
                          or query in action.description.lower())

            # Category / Needs Attention matching
            cat_match = True
            if selected_cat == "Needs Attention":
                cat_match = getattr(card, "is_recommended", lambda: False)()
            elif selected_cat != "All":
                cat_match = (card._category == selected_cat)

            visible = text_match and cat_match
            card.setVisible(visible)
            if visible:
                visible_by_category[card._category] = True

        self._filter_empty.setVisible(not any(visible_by_category.values()))
        for cat_name, hdr in self._category_headers.items():
            hdr.setVisible(visible_by_category[cat_name])

    def on_deactivate(self) -> None:
        pass

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        for card in self._cards:
            card.cancel()
        for pb in self._playbook_cards:
            pb.cancel()
        self.cancel_all_workers()
