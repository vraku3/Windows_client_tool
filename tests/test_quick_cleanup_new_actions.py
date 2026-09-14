"""New one-click actions added in the cleanup-new-targets sub-project:
hibernation right-sizing and clearing stuck print jobs. Same busy-guard/
status pattern as every other one-click action -- see
tests/test_quick_cleanup_one_click_actions.py for the pattern this
follows.
"""
import time

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QMessageBox


def _settle(qapp, timeout_ms: int = 5_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_resize_hibernation_is_wired_into_the_action_panel(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    assert "resize_hibernation" in tab._action_buttons
    assert "resize_hibernation" in tab._action_status


def test_resize_hibernation_reports_when_hibernation_is_off(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    class _Off:
        returncode = 0
        stdout = "Hibernation has not been enabled."
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Off())
    tab._resize_hibernation()

    assert "off" in tab._action_status["resize_hibernation"].text().lower()


def test_resize_hibernation_runs_powercfg_when_enabled_and_confirmed(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []

    class _On:
        returncode = 0
        stdout = "Hibernate is enabled."
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _On()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)

    tab._resize_hibernation()
    _settle(qapp)

    assert any("powercfg" in c and "/hibernate" in c and "/size 50" in c for c in calls if isinstance(c, str))


def test_resize_hibernation_does_nothing_when_confirm_declined(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []

    class _On:
        returncode = 0
        stdout = "Hibernate is enabled."
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _On()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    tab._resize_hibernation()
    _settle(qapp)

    # The FIRST call (the "powercfg /a" status check) is expected; a
    # SECOND call containing "/hibernate /size" must not happen.
    assert not any("/hibernate" in c and "/size" in c for c in calls if isinstance(c, str))
