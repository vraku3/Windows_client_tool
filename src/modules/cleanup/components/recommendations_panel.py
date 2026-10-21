"""Recommendation cards for the Cleanup overview (row C of the redesign).

The panel shows what ``cleanup_scanner.recommend`` concluded, most severe first.
A card SAYS something and may NAVIGATE (``navigate(tab_key, ids, age_days)``:
switch tab, pre-tick only the named rows, apply the age filter); it never
cleans. ``ids`` may be empty, which means "open the tab, tick nothing".

Colours come from QSS roles only (``recoCard``/``recoCardHigh`` for the card,
``statusError``/``statusWarning``/``statusInfo`` for the icon, ``muted`` for
evidence, ``emptyState`` for the empty page), so a theme switch needs no code.
Severity is never colour-only: each card has an icon SHAPE and a word.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional, Sequence

from PyQt6.QtCore import Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from core.table_ui import set_role
from core.widget_life import widget_is_valid
from core.worker import Worker, retire
from modules.cleanup.cleanup_scanner import recommend as rc
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)

MAX_VISIBLE = 5

#: severity -> (shape, QSS role of the icon)
_ICON = {
    rc.HIGH: ("▲", "statusError"),      # ▲
    rc.WARNING: ("◆", "statusWarning"),  # ◆
    rc.INFO: ("ⓘ", "statusInfo"),        # ⓘ
}

EMPTY_BEFORE_SCAN = "Recommendations appear here after a scan."
EMPTY_NONE = "Nothing needs attention: no recommendation applies to this scan."
EMPTY_ALL_DISMISSED = "Every recommendation for this scan is dismissed for now."


def _alive(widget) -> bool:
    return widget_is_valid(widget)


class RecommendationCard(QFrame):
    """One recommendation. Emits; does nothing by itself."""

    navigate = pyqtSignal(str, list, int)
    dismiss = pyqtSignal(str)

    def __init__(self, rec: rc.Recommendation, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.rec = rec
        self.setObjectName("recoCardHigh" if rec.severity == rc.HIGH else "recoCard")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        word = rc.SEVERITY_WORD.get(rec.severity, rec.severity)
        tip = tooltip or rec.title
        self.setToolTip(tip)
        self.setAccessibleName(f"{word}: {rec.title}")
        self.setAccessibleDescription(rec.evidence)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)

        shape, role = _ICON.get(rec.severity, _ICON[rc.INFO])
        self.icon = QLabel(shape)
        set_role(self.icon, role)
        self.icon.setToolTip(f"{word}")
        self.icon.setAccessibleName(word)
        self.icon.setFixedWidth(18)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(rec.title)
        self.title.setWordWrap(True)
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        self.title.setToolTip(tip)
        text.addWidget(self.title)

        self.severity = QLabel(word)
        set_role(self.severity, "muted")
        self.severity.setToolTip("How much this matters: Important, Suggestion or Good to know.")
        text.insertWidget(0, self.severity)

        self.evidence = QLabel(rec.evidence)
        self.evidence.setWordWrap(True)
        set_role(self.evidence, "muted")
        self.evidence.setToolTip(tip)
        text.addWidget(self.evidence)

        self.note: Optional[QLabel] = None
        if rec.note:
            self.note = QLabel(rec.note)
            self.note.setWordWrap(True)
            set_role(self.note, "statusWarning")
            self.note.setToolTip(rec.note)
            text.addWidget(self.note)
        row.addLayout(text, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(4)
        self.action: Optional[QPushButton] = None
        if rec.navigates:
            self.action = QPushButton(rec.action_label)
            self.action.setToolTip(
                "Opens the tab and ticks only these rows. Nothing is cleaned until you "
                "confirm it there.")
            self.action.setAccessibleName(f"{rec.action_label}: {rec.title}")
            self.action.clicked.connect(self._on_action)
            buttons.addWidget(self.action)
        self.dismiss_btn = QToolButton()
        self.dismiss_btn.setText(f"Dismiss for {rc.DISMISS_DAYS} days")
        self.dismiss_btn.setToolTip(
            f"Hide this recommendation for {rc.DISMISS_DAYS} days. It comes back after that "
            "if it still applies.")
        self.dismiss_btn.setAccessibleName(f"Dismiss: {rec.title}")
        self.dismiss_btn.clicked.connect(lambda: self.dismiss.emit(self.rec.id))
        buttons.addWidget(self.dismiss_btn, 0, Qt.AlignmentFlag.AlignRight)
        buttons.addStretch()
        row.addLayout(buttons)

    def _on_action(self) -> None:
        r = self.rec
        if r.target_tab:
            self.navigate.emit(str(r.target_tab), list(r.target_ids), int(r.age_days))


class RecommendationsPanel(QWidget):
    """Up to MAX_VISIBLE cards ("Show N more" reveals the rest), an empty page,
    and an error banner. Feed it with :meth:`set_recommendations` (already
    evaluated) or :meth:`refresh` (gathers the probes on a Worker)."""

    navigate = pyqtSignal(str, list, int)
    dismissed = pyqtSignal(str)
    updated = pyqtSignal(int)          # number of visible recommendations

    def __init__(self, parent=None, *, config=None, thread_pool: Optional[QThreadPool] = None,
                 gather: Optional[Callable] = None):
        super().__init__(parent)
        self._config = config
        self._pool = thread_pool
        self._gather = gather or rc.gather_inputs
        self._workers: List[Worker] = []
        self._generation = 0
        self._all: List[rc.Recommendation] = []
        self._snapshot = None
        self._expanded = False
        self._busy = False
        self._session_dismissed: dict = {}      # used when no config is given
        self.setAccessibleName("Recommendations")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        head = QHBoxLayout()
        self.heading = QLabel("Recommendations")
        set_role(self.heading, "mutedTitle")
        self.heading.setToolTip(
            "Computed from this scan, the clean history, free space and whether a restart "
            "is pending. Each card states the measured figure it is based on.")
        head.addWidget(self.heading)
        self.status = QLabel("")
        set_role(self.status, "muted")
        head.addWidget(self.status, 1)
        outer.addLayout(head)

        self.banner = ErrorBanner()
        outer.addWidget(self.banner)

        self.stack = QStackedWidget()
        self._cards_page = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_page)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(6)
        self.stack.addWidget(self._cards_page)
        self.empty = QLabel(EMPTY_BEFORE_SCAN)
        self.empty.setObjectName("emptyState")
        self.empty.setWordWrap(True)
        self.empty.setToolTip("Recommendations are rules over measured facts; none fired.")
        self.stack.addWidget(self.empty)
        self.stack.setCurrentIndex(1)
        outer.addWidget(self.stack)

        self.more = QToolButton()
        self.more.setToolTip("Show or hide the less important recommendations.")
        self.more.clicked.connect(self._toggle_more)
        self.more.hide()
        outer.addWidget(self.more)
        self.cards: List[RecommendationCard] = []

    # ── Dismissals ──
    def _store(self) -> dict:
        if self._config is None:
            return self._session_dismissed
        try:
            raw = self._config.get(rc.DISMISS_CONFIG_KEY, {})
        except (AttributeError, TypeError) as exc:
            logger.warning("cannot read dismissed recommendations: %s", exc)
            return self._session_dismissed
        return raw if isinstance(raw, dict) else {}

    def _dismiss(self, rec_id: str) -> None:
        new = rc.dismiss(self._store(), rec_id)
        if self._config is None:
            self._session_dismissed = new
        else:
            try:
                self._config.set(rc.DISMISS_CONFIG_KEY, new)
            except (AttributeError, TypeError, OSError) as exc:
                logger.warning("cannot store a dismissed recommendation: %s", exc)
                self._session_dismissed = new
        self.dismissed.emit(rec_id)
        self._render()

    # ── Public API ──
    def recommendations(self) -> List[rc.Recommendation]:
        """The ones currently shown (dismissed ones excluded), most severe first."""
        return rc.visible(self._all, self._store())

    def set_recommendations(self, recs: Sequence[rc.Recommendation], snapshot=None) -> None:
        self._busy = False
        self._all = sorted(recs, key=rc.sort_key)
        if snapshot is not None:
            self._snapshot = snapshot
        self.banner.clear()
        self._render(scanned=True)

    def refresh(self, snapshot, **gather_kwargs) -> None:
        """Gather the probes and evaluate on a Worker; an older run is cancelled."""
        self.cancel()
        self._snapshot = snapshot
        self._generation += 1
        gen = self._generation
        self._busy = True
        self.status.setText("Checking…")
        gather = self._gather

        def _run(worker):
            inputs = gather(snapshot, is_cancelled=lambda: worker.is_cancelled, **gather_kwargs)
            return rc.evaluate(inputs)

        w = Worker(_run)

        def _done(recs, w=w):
            if not _alive(self) or gen != self._generation:
                return
            self.status.setText("")
            self.set_recommendations(recs or [])

        def _err(message: str, w=w):
            if not _alive(self) or gen != self._generation:
                return
            self._busy = False
            self.status.setText("")
            self.banner.set_error(f"Recommendations could not be computed: {message}")
            self._render(scanned=True)

        def _cancelled(w=w):
            if not _alive(self) or gen != self._generation:
                return
            self._busy = False
            self.status.setText("")

        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        w.signals.cancelled.connect(_cancelled)
        w.signals.finished.connect(lambda w=w: self._forget(w))
        self._workers.append(w)
        (self._pool or QThreadPool.globalInstance()).start(w)

    def cancel(self) -> None:
        retire(self._workers)       # held until delivered: see core.worker.retire
        self._busy = False
        if _alive(self.status):
            self.status.setText("")

    _cancel_all = cancel

    def _forget(self, w) -> None:
        if w in self._workers:
            self._workers.remove(w)

    # ── Rendering ──
    def _toggle_more(self) -> None:
        self._expanded = not self._expanded
        self._render()

    def _render(self, scanned: Optional[bool] = None) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        shown = self.recommendations()
        if not shown:
            if self._all:
                self.empty.setText(EMPTY_ALL_DISMISSED)
            elif scanned or self._snapshot is not None:
                self.empty.setText(EMPTY_NONE)
            else:
                self.empty.setText(EMPTY_BEFORE_SCAN)
            self.stack.setCurrentIndex(1)
            self.more.hide()
            self.updated.emit(0)
            return
        limit = len(shown) if self._expanded else MAX_VISIBLE
        for rec in shown[:limit]:
            try:
                tip = rc.tooltip_html(rec, self._snapshot)
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning("recommendation tooltip failed for %s: %s", rec.id, exc)
                tip = rec.title
            card = RecommendationCard(rec, tip, self._cards_page)
            card.navigate.connect(self.navigate)
            card.dismiss.connect(self._dismiss)
            self._cards_layout.addWidget(card)
            self.cards.append(card)
        extra = len(shown) - MAX_VISIBLE
        if extra > 0:
            self.more.setText("Show fewer" if self._expanded else f"Show {extra} more")
            self.more.show()
        else:
            self.more.hide()
        self.stack.setCurrentIndex(0)
        n_high = sum(1 for r in shown if r.severity == rc.HIGH)
        self.heading.setText(f"Recommendations ({len(shown)})")
        self.setAccessibleDescription(
            f"{len(shown)} recommendations, {n_high} important: "
            + "; ".join(r.title for r in shown))
        self.updated.emit(len(shown))
