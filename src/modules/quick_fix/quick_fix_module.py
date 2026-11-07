from collections import OrderedDict
from typing import Dict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGridLayout, QLineEdit,
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
        self._category_containers: Dict[str, QWidget] = {}
        for cat_name, actions in categories.items():
            hdr = QPushButton(f"▼ {cat_name}")
            hdr.setFlat(True)
            hdr.setCursor(hdr.cursor())
            hdr.setStyleSheet(
                "QPushButton { text-align: left; font-weight: bold; font-size: 13px; "
                "padding: 4px 0; border: none; } "
                "QPushButton:hover { color: #5C9AFF; }"
            )
            hdr.setToolTip(f"Click to collapse or expand the {cat_name} category")
            content_layout.addWidget(hdr)
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

    def on_start(self, app) -> None:
        self.app = app

    def on_stop(self) -> None:
        for card in self._cards:
            card.cancel()
        self._workers.clear()
        self.cancel_all_workers()
