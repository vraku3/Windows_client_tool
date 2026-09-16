"""_FixCard's confirm/precondition/long-running mechanism, added for the
module-consolidation merge (docs/superpowers/specs/
2026-09-15-module-consolidation-design.md) -- migrating Quick Cleanup's
confirmed actions into Quick Fix's card UI must not silently drop their
confirmation dialogs."""
import time
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QMessageBox

from core.long_op_pool import get_long_op_pool
from modules.quick_fix.fix_actions import FixAction
from modules.quick_fix.quick_fix_module import _FixCard


@pytest.fixture(autouse=True)
def _drain_thread_pools(qapp):
    """Every test in this file that calls _FixCard._run() dispatches a real
    Worker onto QThreadPool.globalInstance() or the long-op pool -- neither is
    ever waited on before the test returns, which left workers running on a
    background thread while later, unrelated tests executed. That raced a
    real native crash (test_revert_countdown.py caught mid-run with a
    quick_fix worker still alive on another thread).

    Waiting for the pools alone is not enough: `_worker.signals.result` is a
    cross-thread (queued) connection to `_FixCard._on_done`/`_on_error`, so
    the worker finishing only means the signal has been POSTED to the main
    thread's event queue, not delivered. If nothing pumps that queue before
    this test's _FixCard goes out of scope, the queued call fires later --
    during whatever unrelated test happens to call `qapp.processEvents()`
    next (that is exactly how test_revert_countdown.py's `_pump` ended up
    running a quick_fix lambda against an already-destroyed widget). So
    after draining the pools, also pump events here, while the card is
    still alive, so the callback lands in THIS test.

    This fixture follows the established time-boxed event-pumping convention
    used by test_revert_countdown.py, test_cleanup_scan_watchdog.py and
    related sibling test files."""
    yield
    QThreadPool.globalInstance().waitForDone(5000)
    get_long_op_pool().waitForDone(5000)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


class _SyncPool:
    """Runs a Worker synchronously on the calling thread instead of a real
    background one, so a test can see the outcome as soon as `_run()`
    returns. Same technique as tests/test_driver_module.py's `_SyncPool`.

    Before this, tests that wanted a fast action's result had to reach in
    and call `c._worker.fn(c._worker)` by hand *in addition to* `_run()`
    dispatching the same Worker onto the real global QThreadPool -- running
    the identical closure twice, once on a real background thread and once
    synchronously in the test, a genuine race rather than a harmless
    redundancy (and the same shape of bug Finding 4 fixed in `_FixCard`
    itself: a late arrival from the "other" execution recording a second,
    contradictory outcome)."""

    def start(self, worker) -> None:
        worker.run()


@pytest.fixture
def card(qapp, monkeypatch):
    monkeypatch.setattr(QThreadPool, "globalInstance", staticmethod(lambda: _SyncPool()))
    calls = []
    action = FixAction("t", "Test Action", "desc", "Test", fn=lambda cb: calls.append(1))
    c = _FixCard(action)
    return c, calls


def test_an_action_with_no_confirm_or_precondition_runs_immediately(card):
    c, calls = card
    c._run()
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


def test_a_confirm_text_action_runs_when_accepted(qapp, monkeypatch):
    monkeypatch.setattr(QThreadPool, "globalInstance", staticmethod(lambda: _SyncPool()))
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        confirm_text="Are you sure?")
    c = _FixCard(action)
    with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok):
        c._run()
    assert calls == [1]


def test_a_precondition_returning_a_message_blocks_the_run_and_shows_it(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        precondition=lambda: "nothing to do here")
    c = _FixCard(action)
    c._run()
    assert calls == []
    assert c._status.text() == "nothing to do here"


def test_a_precondition_returning_none_lets_the_action_run(qapp, monkeypatch):
    monkeypatch.setattr(QThreadPool, "globalInstance", staticmethod(lambda: _SyncPool()))
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        precondition=lambda: None)
    c = _FixCard(action)
    c._run()
    assert calls == [1]


def test_a_long_running_action_uses_the_long_op_pool(qapp):
    from core.long_op_pool import get_long_op_pool
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: None, long_running=True)
    c = _FixCard(action)
    with patch.object(get_long_op_pool(), "start") as mock_start:
        c._run()
        assert mock_start.called


def test_running_a_card_twice_while_busy_does_not_start_a_second_worker(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1))
    c = _FixCard(action)
    c._run()
    first_worker = c._worker
    c._run()  # second call while "running" -- must be a no-op
    assert c._worker is first_worker


def test_search_hides_non_matching_cards_and_their_category_header(qapp):
    from modules.quick_fix.quick_fix_module import QuickFixModule

    class FakeApp:
        pass
    mod = QuickFixModule()
    mod.on_start(FakeApp())
    widget = mod.create_widget()  # Keep a reference to prevent garbage collection
    widget.show()  # Show the widget so the hierarchy is visible

    mod._apply_filter("winsock")
    matched = [c for c in mod._cards if c.isVisible()]
    assert matched, "expected at least one card to match 'winsock'"
    assert all("winsock" in c._action.title.lower()
              or "winsock" in c._action.description.lower() for c in matched)

    non_matching_categories = {c._category for c in mod._cards if not c.isVisible()}
    fully_hidden = non_matching_categories - {c._category for c in matched}
    for cat in fully_hidden:
        assert not mod._category_headers[cat].isVisible()


def test_on_done_records_ok_outcome(card):
    c, _calls = card
    # _on_done is now a no-op unless a worker is tracked as running (Finding
    # 4's guard against a cancelled-then-late result recording a second,
    # contradictory outcome) -- simulate "a run is in flight".
    c._worker = object()
    with patch("modules.quick_fix.quick_fix_history.record") as mock_record:
        c._on_done()
    mock_record.assert_called_once_with(c._action.title, "ok")


def test_on_error_records_error_outcome(card):
    c, _calls = card
    c._worker = object()
    with patch("modules.quick_fix.quick_fix_history.record") as mock_record:
        c._on_error("boom")
    mock_record.assert_called_once_with(c._action.title, "error")


def test_on_done_is_a_no_op_after_cancel(card):
    """The guard Finding 4 adds: once cancel() has already put the card
    back to its resting state (self._worker is None), a result arriving
    from the cancelled-but-still-running command must not record a second,
    contradictory 'ok' outcome."""
    c, _calls = card
    assert c._worker is None
    with patch("modules.quick_fix.quick_fix_history.record") as mock_record:
        c._on_done()
    mock_record.assert_not_called()


def test_on_error_is_a_no_op_after_cancel(card):
    c, _calls = card
    assert c._worker is None
    with patch("modules.quick_fix.quick_fix_history.record") as mock_record:
        c._on_error("boom")
    mock_record.assert_not_called()


def test_cancel_records_cancelled_outcome_only_while_running(qapp):
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: None)
    c = _FixCard(action)
    c._run()
    with patch("modules.quick_fix.quick_fix_history.record") as mock_record:
        c.cancel()
    mock_record.assert_called_once_with(c._action.title, "cancelled")


def test_clearing_the_search_shows_everything_again(qapp):
    from modules.quick_fix.quick_fix_module import QuickFixModule

    class FakeApp:
        pass
    mod = QuickFixModule()
    mod.on_start(FakeApp())
    widget = mod.create_widget()  # Keep a reference to prevent garbage collection
    widget.show()  # Show the widget so the hierarchy is visible

    mod._apply_filter("winsock")
    mod._apply_filter("")
    assert all(c.isVisible() for c in mod._cards)
    assert all(hdr.isVisible() for hdr in mod._category_headers.values())
