r"""A display change you cannot see is a change you cannot undo.

Every mode or topology change goes through one guard: snapshot, apply, then a
countdown that reverts unless someone confirms. Windows does the same thing
for the same reason — a resolution or refresh rate a monitor cannot show
leaves you looking at "no signal", and the button that would put it back is
on a screen you can no longer read.

Two rules matter more than the countdown itself:

* **The confirm button must land on a screen that still exists after the
  change.** Parenting it to the display the change just switched off is the
  exact failure the guard exists to prevent.
* **A failed apply must not start a countdown.** Nothing changed, so there is
  nothing to revert, and a countdown implies something happened.
"""
import pytest

from modules.monitor_control import _apply_guard as guard


class FakeScreen:
    """Stands in for a QScreen without needing one."""

    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name

    def __repr__(self):
        return f"<FakeScreen {self._name}>"


@pytest.fixture
def screens():
    return {"left": FakeScreen("left"), "right": FakeScreen("right")}


# ── which screen the confirm lands on ──────────────────────────────────

def test_the_confirm_goes_to_the_affected_screen_when_it_survives(screens):
    chosen = guard.choose_confirm_screen(
        affected=screens["right"],
        present=[screens["left"], screens["right"]])
    assert chosen is screens["right"]


def test_the_confirm_moves_when_the_affected_screen_is_gone(screens):
    """The change turned that display off. Do not put the button on it."""
    chosen = guard.choose_confirm_screen(
        affected=screens["right"],
        present=[screens["left"]])
    assert chosen is screens["left"]


def test_the_confirm_still_lands_somewhere_when_nothing_is_affected(screens):
    chosen = guard.choose_confirm_screen(
        affected=None, present=[screens["left"], screens["right"]])
    assert chosen is screens["left"]


def test_no_screens_at_all_is_none_not_a_crash():
    assert guard.choose_confirm_screen(affected=None, present=[]) is None


# ── the screen to AVOID, which is the input-source case ────────────────
#
# An input switch leaves the screen present as far as Qt is concerned — the
# GPU is still driving it — while the panel shows another machine entirely.
# Preferring the affected screen, which is right for a mode change, puts the
# dialog on the one display that cannot show it.

def test_the_confirm_avoids_the_screen_that_is_being_switched_away(screens):
    chosen = guard.choose_confirm_screen_avoiding(
        avoid=screens["right"], present=[screens["left"], screens["right"]])
    assert chosen is screens["left"]


def test_avoiding_falls_back_to_the_only_screen_there_is(screens):
    """A countdown nobody can see is still safe: doing nothing reverts."""
    chosen = guard.choose_confirm_screen_avoiding(
        avoid=screens["right"], present=[screens["right"]])
    assert chosen is screens["right"]


def test_avoiding_an_unknown_screen_still_lands_somewhere(screens):
    chosen = guard.choose_confirm_screen_avoiding(
        avoid=None, present=[screens["left"], screens["right"]])
    assert chosen is screens["left"]


def test_avoiding_with_no_screens_at_all_is_none(screens):
    assert guard.choose_confirm_screen_avoiding(
        avoid=screens["right"], present=[]) is None


# ── the revert decision ────────────────────────────────────────────────

def test_a_timeout_reverts_to_the_snapshot():
    restored = []
    outcome = guard.resolve(confirmed=False, snapshot={"topology": "before"},
                            restore=restored.append)
    assert outcome is guard.Outcome.REVERTED
    assert restored == [{"topology": "before"}]


def test_a_confirmation_keeps_the_change():
    restored = []
    outcome = guard.resolve(confirmed=True, snapshot={"topology": "before"},
                            restore=restored.append)
    assert outcome is guard.Outcome.KEPT
    assert restored == []


def test_a_revert_that_itself_fails_is_reported_not_swallowed():
    def _explode(_snapshot):
        raise OSError("SetDisplayConfig failed: ERROR_NOT_SUPPORTED")

    outcome = guard.resolve(confirmed=False, snapshot={"x": 1},
                            restore=_explode)
    assert outcome is guard.Outcome.REVERT_FAILED


# ── applying ───────────────────────────────────────────────────────────

def test_a_failed_apply_does_not_start_a_countdown():
    """Nothing changed, so there is nothing to revert."""
    started = []

    def _apply():
        raise OSError("SetDisplayConfig failed: ERROR_INVALID_PARAMETER")

    result = guard.run_apply(snapshot=lambda: {"before": True}, apply=_apply,
                             start_countdown=lambda *a, **k: started.append(a))
    assert result.applied is False
    assert "ERROR_INVALID_PARAMETER" in result.error
    assert started == [], "a countdown ran for a change that never happened"


def test_a_successful_apply_starts_the_countdown_with_the_snapshot():
    started = []
    result = guard.run_apply(
        snapshot=lambda: {"before": True},
        apply=lambda: None,
        start_countdown=lambda snapshot: started.append(snapshot))
    assert result.applied is True
    assert started == [{"before": True}]


def test_the_snapshot_is_taken_before_the_change_not_after():
    order = []
    guard.run_apply(
        snapshot=lambda: order.append("snapshot") or {"s": 1},
        apply=lambda: order.append("apply"),
        start_countdown=lambda snapshot: order.append("countdown"))
    assert order == ["snapshot", "apply", "countdown"]


def test_a_snapshot_that_cannot_be_taken_blocks_the_change():
    """Without a way back, the change must not happen at all."""
    applied = []

    def _snapshot():
        raise OSError("QueryDisplayConfig failed: ERROR_GEN_FAILURE")

    result = guard.run_apply(snapshot=_snapshot,
                             apply=lambda: applied.append(True),
                             start_countdown=lambda *a: None)
    assert result.applied is False
    assert applied == [], "changed the display with no way to undo it"
    assert "ERROR_GEN_FAILURE" in result.error
