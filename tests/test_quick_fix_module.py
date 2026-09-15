"""_FixCard's confirm/precondition/long-running mechanism, added for the
module-consolidation merge (docs/superpowers/specs/
2026-09-15-module-consolidation-design.md) -- migrating Quick Cleanup's
confirmed actions into Quick Fix's card UI must not silently drop their
confirmation dialogs."""
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QMessageBox

from modules.quick_fix.fix_actions import FixAction
from modules.quick_fix.quick_fix_module import _FixCard


@pytest.fixture
def card(qapp):
    calls = []
    action = FixAction("t", "Test Action", "desc", "Test", fn=lambda cb: calls.append(1))
    c = _FixCard(action)
    return c, calls


def test_an_action_with_no_confirm_or_precondition_runs_immediately(card):
    c, calls = card
    c._run()
    c._worker.fn(c._worker)  # run synchronously in-test
    assert calls == [1]


def test_a_confirm_text_action_does_not_run_when_cancelled(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        confirm_text="Are you sure?")
    c = _FixCard(action)
    with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel):
        c._run()
    assert calls == []
    assert not c._running


def test_a_confirm_text_action_runs_when_accepted(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        confirm_text="Are you sure?")
    c = _FixCard(action)
    with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok):
        c._run()
    c._worker.fn(c._worker)
    assert calls == [1]


def test_a_precondition_returning_a_message_blocks_the_run_and_shows_it(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        precondition=lambda: "nothing to do here")
    c = _FixCard(action)
    c._run()
    assert calls == []
    assert c._status.text() == "nothing to do here"


def test_a_precondition_returning_none_lets_the_action_run(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        precondition=lambda: None)
    c = _FixCard(action)
    c._run()
    c._worker.fn(c._worker)
    assert calls == [1]


def test_a_long_running_action_uses_the_long_op_pool(qapp):
    from core.long_op_pool import get_long_op_pool
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: None, long_running=True)
    c = _FixCard(action)
    with patch.object(get_long_op_pool(), "start") as mock_start:
        c._run()
        assert mock_start.called
