from collections import OrderedDict
from typing import Dict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QLineEdit, QMessageBox,
)

from core.base_module import BaseModule
from ui.empty_state import EmptyState
from ui.glance import GlanceStrip, GlanceTile
from core.module_groups import ModuleGroup
from core.windows_utils import is_reboot_pending
from modules.quick_fix.fix_actions import ALL_ACTIONS
from modules.quick_fix.fix_card import FixCard, QuickFixHistoryDialog
from modules.quick_fix.browser_links_card import BrowserLinksCard
import logging
logger = logging.getLogger(__name__)

# Back-compat alias so existing tests that import the private name still work.
_FixCard = FixCard


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
        self._safe_queue: list = []
        self._category_headers: Dict[str, QLabel] = {}

    def create_widget(self) -> QWidget:
        outer = QWidget()
        self._widget = outer
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Action row
        action_row = QHBoxLayout()
        self._run_all_btn = QPushButton("Run All Safe")
        self._run_all_btn.setToolTip(
            "Run all low-risk repair actions that need no confirmation, sequentially")
        self._run_all_btn.clicked.connect(self._on_run_all_clicked)
        action_row.addWidget(self._run_all_btn)
        action_row.addStretch()
        history_btn = QPushButton("View History")
        history_btn.clicked.connect(self._show_history)
        action_row.addWidget(history_btn)
        outer_layout.addLayout(action_row)

        sc = QShortcut(QKeySequence("Ctrl+Shift+R"), outer)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._on_run_all_clicked)

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
        self._category_containers: Dict[str, QWidget] = {}
        for cat_name, actions in categories.items():
            hdr_row = QHBoxLayout()
            hdr_row.setContentsMargins(0, 0, 0, 0)
            hdr = QPushButton(f"▼ {cat_name}")
            hdr.setFlat(True)
            hdr.setCursor(hdr.cursor())
            hdr.setStyleSheet(
                "QPushButton { text-align: left; font-weight: bold; font-size: 13px; "
                "padding: 4px 0; border: none; } "
                "QPushButton:hover { color: #5C9AFF; }"
            )
            hdr.setToolTip(f"Click to collapse or expand the {cat_name} category")
            hdr_row.addWidget(hdr)
            hdr_row.addStretch()
            cat_run_btn = QPushButton("Run Safe ▶")
            cat_run_btn.setFlat(True)
            cat_run_btn.setStyleSheet(
                "QPushButton { font-size: 11px; color: #4CAF50; border: none; padding: 2px 6px; } "
                "QPushButton:hover { color: #66BB6A; }")
            cat_run_btn.setToolTip(f"Run the low-risk actions in {cat_name} sequentially")
            cat_run_btn.clicked.connect(lambda _c=False, cn=cat_name: self._run_category_safe(cn))
            hdr_row.addWidget(cat_run_btn)
            content_layout.addLayout(hdr_row)
            self._category_headers[cat_name] = hdr

            container = QWidget()
            container_layout = QGridLayout(container)
            container_layout.setSpacing(8)
            container_layout.setContentsMargins(0, 0, 0, 0)
            for i, action in enumerate(actions):
                if action.key == BrowserLinksCard.ACTION.key:
                    card = BrowserLinksCard(module_workers=self._workers)
                else:
                    card = FixCard(action, last_outcomes.get(action.title),
                                   recurring.get(action.title, 0))
                card._category = cat_name
                self._cards.append(card)
                container_layout.addWidget(card, i // 2, i % 2)
            content_layout.addWidget(container)
            self._category_containers[cat_name] = container

            def _toggle(checked, h=hdr, c=container, name=cat_name):
                vis = not c.isVisible()
                c.setVisible(vis)
                h.setText(f"{'▼' if vis else '▶'} {name}")
            hdr.clicked.connect(_toggle)

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
        cat_summary = ", ".join(f"{len(v)} {k}" for k, v in categories.items())
        values = (("Repair actions", len(self._cards), cat_summary),
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
            if isinstance(card, FixCard):
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

    def _on_run_all_clicked(self) -> None:
        if self._safe_queue:
            self._stop_batch()
        else:
            self._run_all_safe()

    def _stop_batch(self) -> None:
        remaining = list(self._safe_queue)
        self._safe_queue.clear()
        for c in remaining:
            if not c._running:
                c._run_btn.setEnabled(True)
        self._run_all_btn.setText("Run All Safe")

    def _run_all_safe(self) -> None:
        safe_cards = [
            c for c in self._cards
            if isinstance(c, FixCard)
            and getattr(c._action, 'risk', None) == 'low'
            and c._action.confirm_text is None
            and c._action.precondition is None
            and not c._action.long_running
            and c._card_state != 'running'
        ]
        if not safe_cards:
            QMessageBox.information(self._widget, "Run All Safe",
                                   "No safe actions are available to run right now.")
            return
        names = "\n".join(f"  • {c._action.title}" for c in safe_cards)
        answer = QMessageBox.question(
            self._widget, "Run All Safe",
            f"Run these {len(safe_cards)} low-risk actions sequentially?\n\n{names}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        self._safe_queue = list(safe_cards)
        self._safe_total = len(safe_cards)
        self._safe_done = 0
        self._safe_results: list = []
        for c in safe_cards:
            c._run_btn.setEnabled(False)
        self._run_all_btn.setText(f"Stop (1/{self._safe_total})")
        self._run_next_safe()

    def _run_next_safe(self) -> None:
        if not self._safe_queue:
            self._run_all_btn.setEnabled(True)
            self._run_all_btn.setText("Run All Safe")
            self._show_batch_summary()
            return
        card = self._safe_queue.pop(0)
        self._safe_done += 1
        self._run_all_btn.setText(
            f"Stop ({self._safe_done}/{self._safe_total})")
        self._scroll_to_card(card)
        def _on_done(c=card):
            try:
                c.history_changed.disconnect(_on_done)
            except TypeError:
                pass
            self._safe_results.append((c, c._action.title, c._card_state))
            self._run_next_safe()
        card.history_changed.connect(_on_done)
        card._run()

    def _show_batch_summary(self) -> None:
        results = getattr(self, '_safe_results', [])
        if not results:
            return
        ok = sum(1 for _, _, s in results if s == 'success')
        fail = sum(1 for _, _, s in results if s == 'error')
        total = len(results)
        failed_cards = [(c, n) for c, n, s in results if s == 'error']
        if fail == 0:
            msg = f"All {total} actions completed successfully."
            QMessageBox.information(self._widget, "Run All Safe — Done", msg)
        else:
            failed_names = [n for _, n in failed_cards]
            msg = (f"{ok} of {total} succeeded, {fail} failed:\n\n"
                   + "\n".join(f"  ✗ {n}" for n in failed_names)
                   + "\n\nRetry the failed actions?")
            answer = QMessageBox.question(
                self._widget, "Run All Safe — Done", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                retry = [c for c, _ in failed_cards
                         if c._card_state != 'running']
                if retry:
                    self._safe_queue = retry
                    self._safe_total = len(retry)
                    self._safe_done = 0
                    self._safe_results = []
                    for c in retry:
                        c._run_btn.setEnabled(False)
                    self._run_all_btn.setText(
                        f"Stop (1/{self._safe_total})")
                    self._run_next_safe()
                    return
        self._safe_results = []

    def _run_category_safe(self, category: str) -> None:
        if self._safe_queue:
            return
        safe_cards = [
            c for c in self._cards
            if isinstance(c, FixCard)
            and getattr(c, '_category', '') == category
            and getattr(c._action, 'risk', None) == 'low'
            and c._action.confirm_text is None
            and c._action.precondition is None
            and not c._action.long_running
            and c._card_state != 'running'
        ]
        if not safe_cards:
            QMessageBox.information(self._widget, f"Run Safe — {category}",
                                   f"No safe actions in {category} right now.")
            return
        names = "\n".join(f"  • {c._action.title}" for c in safe_cards)
        answer = QMessageBox.question(
            self._widget, f"Run Safe — {category}",
            f"Run these {len(safe_cards)} low-risk actions?\n\n{names}",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        self._safe_queue = list(safe_cards)
        self._safe_total = len(safe_cards)
        self._safe_done = 0
        self._safe_results = []
        for c in safe_cards:
            c._run_btn.setEnabled(False)
        self._run_all_btn.setText(f"Stop (1/{self._safe_total})")
        self._run_next_safe()

    def _scroll_to_card(self, card: FixCard) -> None:
        card.setVisible(True)
        scroll = self._widget.findChild(QScrollArea)
        if scroll:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda: scroll.ensureWidgetVisible(card))

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
            has_visible = visible_by_category[cat_name]
            hdr.setVisible(has_visible)
            if cat_name in self._category_containers:
                self._category_containers[cat_name].setVisible(has_visible)

    def on_deactivate(self) -> None:
        # Leaving the pane does NOT cancel a running fix. `action.fn` never
        # reads the cancel flag, so sfc/DISM/netsh keep running whatever the
        # card says -- marking it "Cancelled" re-enabled Run and let a second
        # DISM start on top of the first. The cards outlive the pane being
        # hidden, so the run finishes and records its real outcome.
        pass

    def get_search_provider(self):
        from modules.quick_fix.quick_fix_search_provider import QuickFixSearchProvider
        return QuickFixSearchProvider()

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        self._safe_queue = []
        if hasattr(self, '_run_all_btn'):
            self._run_all_btn.setText("Run All Safe")
            self._run_all_btn.setEnabled(True)
        for card in self._cards:
            card.cancel()
        self._workers.clear()
        self.cancel_all_workers()
