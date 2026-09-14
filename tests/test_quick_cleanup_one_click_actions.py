"""Each one-click action must guard against being clicked again while it's
already running, and must report ITS OWN result -- not clobber whatever
another action's status currently says. Before this fix, all 12 actions
shared one QLabel and none of them disabled their own button.
"""
import time

from PyQt6.QtCore import QThreadPool


def _settle(qapp, timeout_ms: int = 5_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
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
