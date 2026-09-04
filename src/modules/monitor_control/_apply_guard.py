r"""Snapshot, apply, count down, revert.

Every display change goes through here. A resolution or refresh rate a
monitor cannot show leaves you looking at "no signal", and the control that
would put it back is on a screen you can no longer read — so the change
undoes itself unless someone confirms it. Windows does the same thing for the
same reason.

Two rules matter more than the countdown:

* **The confirm must land on a screen that still exists after the change.**
  Parenting it to the display the change just switched off is precisely the
  failure this guard exists to prevent.
* **A failed apply starts no countdown**, and a change with no way back never
  happens at all. If the snapshot cannot be taken, there is no undo, so the
  change is refused rather than attempted hopefully.

The decision logic is kept as plain functions so it can be tested without a
display; only `CountdownOverlay` needs Qt.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Long enough to read the dialog and reach the mouse, short enough that a
#: black screen is not frightening. Windows uses 15 too.
COUNTDOWN_SECONDS = 15


class Outcome(enum.Enum):
    KEPT = "kept"
    REVERTED = "reverted"
    REVERT_FAILED = "revert-failed"


@dataclass
class ApplyResult:
    applied: bool
    error: str = ""


def choose_confirm_screen(affected: Any,
                          present: Sequence[Any]) -> Optional[Any]:
    """Where to put the confirm, given what is still attached.

    The affected screen when it survived the change — that is where the user
    is looking. Otherwise any screen that is still there; the point is that
    the button exists somewhere reachable, not which monitor it is on.
    """
    if not present:
        return None
    if affected is not None and affected in present:
        return affected
    return present[0]


def choose_confirm_screen_avoiding(avoid: Any,
                                   present: Sequence[Any]) -> Optional[Any]:
    """Where to put the confirm when `avoid` is the screen going dark.

    `choose_confirm_screen` prefers the affected screen, because for a mode
    or arrangement change that is where the user is looking and the screen
    either survives or disappears from `present` entirely. **Switching a
    monitor's input source breaks that assumption**: the GPU is still
    driving the panel, so Qt still lists the screen, but the panel is
    showing another machine. It is the one screen guaranteed not to display
    the dialog, so it is the one to avoid.

    Falls back to `avoid` when there is nothing else — a countdown nobody
    can see is still safe, because doing nothing is what reverts.
    """
    if not present:
        return None
    others = [screen for screen in present if screen is not avoid]
    if others:
        return others[0]
    return avoid if avoid in present else present[0]


def resolve(confirmed: bool, snapshot: Any,
            restore: Callable[[Any], None]) -> Outcome:
    """Keep the change, or put the old configuration back.

    A revert that itself fails is reported, never swallowed: the user is
    sitting in front of a configuration nobody chose, and silence is the
    worst possible answer.
    """
    if confirmed:
        return Outcome.KEPT
    try:
        restore(snapshot)
    except Exception:                                    # noqa: BLE001
        logger.exception("Reverting the display change failed")
        return Outcome.REVERT_FAILED
    return Outcome.REVERTED


def run_apply(snapshot: Callable[[], Any], apply: Callable[[], Any],
              start_countdown: Callable[[Any], None]) -> ApplyResult:
    """Take the snapshot, make the change, then arm the countdown.

    Strictly in that order. The snapshot is the undo, so a change made before
    one exists is a change that cannot be taken back.
    """
    try:
        before = snapshot()
    except Exception as exc:                             # noqa: BLE001
        logger.warning("Refusing a display change with no way back: %s", exc)
        return ApplyResult(applied=False, error=str(exc))

    try:
        apply()
    except Exception as exc:                             # noqa: BLE001
        logger.warning("Display change failed: %s", exc)
        return ApplyResult(applied=False, error=str(exc))

    start_countdown(before)
    return ApplyResult(applied=True)


# ── the Qt half ────────────────────────────────────────────────────────

class RevertCountdown:
    """"Keep these changes?", counting down to no.

    Doing nothing REVERTS. That is the whole design: a mode the monitor
    cannot show leaves someone looking at a black screen, unable to click
    anything, and the safe outcome has to be the one that needs no input.

    Not a QDialog: `exec()` would block the caller inside a modal loop while
    the display is in an unknown state. It is a frameless always-on-top
    window driven by a QTimer, so the app keeps running and the revert fires
    from the event loop like anything else.
    """

    def __init__(self, seconds: int = COUNTDOWN_SECONDS, on_resolve=None,
                 summary: str = ""):
        self._total = seconds
        self.remaining_seconds = seconds
        self._on_resolve = on_resolve or (lambda _kept: None)
        self._summary = summary
        self._resolved = False
        self._window = None
        self._label = None
        self._timer = None

    # -- what it says --

    def message(self) -> str:
        plural = "" if self.remaining_seconds == 1 else "s"
        return ("Keep these display changes?\n"
                f"Reverting in {self.remaining_seconds} second{plural}")

    # -- lifecycle --

    def start(self, affected_screen=None) -> None:
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton,
                                     QVBoxLayout, QWidget)

        screen = choose_confirm_screen(affected_screen, present_screens())

        self._window = QWidget(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool)
        self._window.setObjectName("revertCountdown")
        layout = QVBoxLayout(self._window)
        layout.setContentsMargins(20, 16, 20, 16)

        if self._summary:
            summary = QLabel(self._summary)
            summary.setObjectName("muted")
            summary.setWordWrap(True)
            layout.addWidget(summary)

        self._label = QLabel(self.message())
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        buttons = QHBoxLayout()
        keep = QPushButton("Keep changes")
        revert = QPushButton("Revert now")
        keep.clicked.connect(self.keep)
        revert.clicked.connect(self.revert_now)
        buttons.addWidget(revert)
        buttons.addStretch()
        buttons.addWidget(keep)
        layout.addLayout(buttons)

        self._window.adjustSize()
        if screen is not None:
            area = screen.geometry()
            size = self._window.size()
            self._window.move(
                area.x() + (area.width() - size.width()) // 2,
                area.y() + (area.height() - size.height()) // 3)
        self._window.show()
        self._window.raise_()

        self._timer = QTimer()
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self.remaining_seconds -= 1
        if self.remaining_seconds <= 0:
            self._resolve(kept=False)
            return
        if self._label is not None:
            self._label.setText(self.message())

    # -- outcomes --

    def keep(self) -> None:
        self._resolve(kept=True)

    def revert_now(self) -> None:
        self._resolve(kept=False)

    def _resolve(self, kept: bool) -> None:
        """Exactly once. Clicking Keep as the timer fires must not do both."""
        if self._resolved:
            return
        self._resolved = True
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._window is not None:
            self._window.close()
            self._window.deleteLater()
            self._window = None
        self._on_resolve(kept)

    def geometry(self):
        from PyQt6.QtCore import QRect
        return self._window.geometry() if self._window else QRect()


def present_screens() -> List[Any]:
    """The screens Qt can still see. Asked fresh, never cached.

    A stored QScreen says nothing about whether its monitor is still there;
    after a topology change some of them are gone.
    """
    from PyQt6.QtWidgets import QApplication

    return list(QApplication.screens())
