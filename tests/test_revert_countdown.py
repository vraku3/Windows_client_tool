r"""The dialog that gives a bad display change back.

Windows shows one for the same reason: a mode the monitor cannot display
leaves you looking at nothing, and doing nothing has to be the safe answer.
So the countdown reverts on timeout, and the only way to keep a change is to
actively say so.

The two properties that matter are about what happens when nobody is
looking: silence must revert, and the dialog must not be sitting on a screen
that the change just switched off.
"""
import time

import pytest
from PyQt6.QtCore import QTimer

from modules.monitor_control import _apply_guard as guard


def _pump(qapp, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_doing_nothing_reverts(qapp):
    """The whole point. An unattended countdown must undo itself."""
    reverted = []
    dialog = guard.RevertCountdown(seconds=1, on_resolve=reverted.append)
    dialog.start()
    _pump(qapp, 2.0)
    assert reverted == [False], "the countdown expired without reverting"


def test_keeping_it_stops_the_countdown(qapp):
    resolved = []
    dialog = guard.RevertCountdown(seconds=5, on_resolve=resolved.append)
    dialog.start()
    _pump(qapp, 0.2)
    dialog.keep()
    _pump(qapp, 0.3)
    assert resolved == [True]
    assert dialog.remaining_seconds <= 5


def test_reverting_now_does_not_wait_for_the_clock(qapp):
    resolved = []
    dialog = guard.RevertCountdown(seconds=30, on_resolve=resolved.append)
    dialog.start()
    _pump(qapp, 0.2)
    dialog.revert_now()
    _pump(qapp, 0.3)
    assert resolved == [False]


def test_it_resolves_exactly_once(qapp):
    """Clicking Keep as the timer fires must not resolve twice."""
    resolved = []
    dialog = guard.RevertCountdown(seconds=1, on_resolve=resolved.append)
    dialog.start()
    dialog.keep()
    dialog.keep()
    dialog.revert_now()
    _pump(qapp, 1.5)
    assert resolved == [True], f"resolved {len(resolved)} times"


def test_the_countdown_text_names_the_seconds_left(qapp):
    dialog = guard.RevertCountdown(seconds=9, on_resolve=lambda _k: None)
    dialog.start()
    assert "9" in dialog.message()
    _pump(qapp, 1.2)
    assert "8" in dialog.message() or "7" in dialog.message()


def test_it_starts_on_a_screen_that_still_exists(qapp):
    """Never on the display the change just turned off."""
    from PyQt6.QtWidgets import QApplication

    screens = QApplication.screens()
    if not screens:
        pytest.skip("no screens")
    dialog = guard.RevertCountdown(seconds=5, on_resolve=lambda _k: None)
    dialog.start(affected_screen=None)
    _pump(qapp, 0.1)
    centre = dialog.geometry().center()
    assert any(s.geometry().contains(centre) for s in screens), (
        "the countdown was placed off every attached screen")
    dialog.keep()


def test_a_gone_screen_falls_back_rather_than_vanishing(qapp):
    """A QScreen that is no longer attached must not be used."""
    class Gone:
        def name(self):
            return "gone"

    chosen = guard.choose_confirm_screen(affected=Gone(),
                                         present=guard.present_screens())
    assert chosen is not Gone
    if guard.present_screens():
        assert chosen in guard.present_screens()
