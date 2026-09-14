"""Each one-click action must guard against being clicked again while it's
already running, and must report ITS OWN result -- not clobber whatever
another action's status currently says. Before this fix, all 12 actions
shared one QLabel and none of them disabled their own button.
"""
import time

from PyQt6.QtCore import QThreadPool

from core.long_op_pool import get_long_op_pool


def _settle(qapp, timeout_ms: int = 5_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    # _compact_winsxs (and the busy-guard test below that drives it) runs
    # its worker on get_long_op_pool(), a SEPARATE bounded pool from the
    # global one -- waiting on only the global pool let that test pass on
    # its trailing processEvents loop rather than on a real wait for the
    # worker to actually finish. get_long_op_pool() returns a real
    # QThreadPool, so it has the same waitForDone(timeout_ms) API.
    get_long_op_pool().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_a_running_actions_own_button_is_disabled_until_it_finishes(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    release = []

    def fake_run(cmd, **kwargs):
        while not release:
            qapp.processEvents()
            time.sleep(0.01)
        class _R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    btn = tab._action_buttons["flush_dns"]
    assert btn.isEnabled()

    tab._flush_dns()
    qapp.processEvents()
    assert not btn.isEnabled(), "button stayed enabled while its own action was running"

    release.append(1)
    _settle(qapp)
    assert btn.isEnabled(), "button never re-enabled after finishing"


def test_two_actions_running_at_once_do_not_clobber_each_others_status(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    def fake_run(cmd, **kwargs):
        class _R:
            returncode = 0
            stdout = cmd if isinstance(cmd, str) else " ".join(cmd)
            stderr = ""
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    tab._flush_dns()
    _settle(qapp)
    tab._clear_clipboard()
    _settle(qapp)

    dns_status = tab._action_status["flush_dns"].text()
    clip_status = tab._action_status["clear_clipboard"].text()
    assert "DNS" in dns_status or "flush" in dns_status.lower()
    assert dns_status != clip_status, "both actions ended up showing the same text"


def test_a_cancelled_one_click_action_re_enables_its_button(qapp, monkeypatch):
    """QuickCleanupTab.cancel() -> _reset_after_cancel() used to cancel
    EVERY worker in self._workers, including one-click-action workers (they
    are appended to that same list) -- and a cancelled Worker emits
    `cancelled`, never `result`/`error` (core/worker.py), so the action's
    own `_done`/`_err` closure -- the only code that re-enabled its
    button -- never ran. Real impact: switch away from Cleanup while
    "Compact WinSxS" or any other one-click action is running, and that
    button is dead for the rest of the process."""
    import threading

    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    started = threading.Event()
    release = threading.Event()

    def fake_run(cmd, **kwargs):
        started.set()
        release.wait(10)
        class _R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    btn = tab._action_buttons["flush_dns"]
    tab._flush_dns()

    deadline = time.time() + 5
    while not started.is_set() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert started.is_set(), "the action's worker never started"
    assert not btn.isEnabled(), "button should be disabled while the action runs"

    tab.cancel()  # e.g. CleanupModule.on_deactivate() while the action is running
    assert btn.isEnabled(), "cancel() left a running one-click action's button disabled"
    status_text = tab._action_status["flush_dns"].text()
    assert "cancel" in status_text.lower(), (
        f"status did not say the action was cancelled: {status_text!r}")

    release.set()
    _settle(qapp)
    # The underlying subprocess call was never actually interrupted
    # (Worker.cancel() only sets a flag the worker function never checks),
    # but its result was dropped -- the button must stay enabled and not
    # be flipped back and forth by a stale, cancelled worker's callback.
    assert btn.isEnabled()


def test_compact_winsxs_also_guards_against_a_second_click(qapp, monkeypatch):
    """_compact_winsxs bypasses _run_action_command entirely -- it needs
    its own busy-guard, separately."""
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from PyQt6.QtWidgets import QMessageBox

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)

    release = []
    started = []

    class _FakeProc:
        returncode = 0
        def communicate(self, timeout=None):
            started.append(1)
            while not release:
                time.sleep(0.01)
            return "ok", ""

    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: _FakeProc())

    btn = tab._action_buttons["compact_winsxs"]
    tab._compact_winsxs()
    deadline = time.time() + 5
    while not started and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert not btn.isEnabled(), "button stayed enabled while WinSxS compaction was running"

    tab._compact_winsxs()  # second click while running -- must be a no-op
    qapp.processEvents()

    release.append(1)
    _settle(qapp)
    assert btn.isEnabled()
