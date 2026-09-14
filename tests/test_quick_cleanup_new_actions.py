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
    """M1: the gate is now hiberfil.sys's existence, not an English-only
    substring match on powercfg /a's output."""
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    monkeypatch.setattr("os.path.exists", lambda path: False)
    run_calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: run_calls.append(a) or None)

    tab._resize_hibernation()

    assert "off" in tab._action_status["resize_hibernation"].text().lower()
    assert run_calls == [], "no subprocess call is needed for a file-existence check"


def test_resize_hibernation_runs_powercfg_when_enabled_and_confirmed(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []

    class _On:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _On()

    monkeypatch.setattr("os.path.exists", lambda path: True)
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

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return _R()

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    tab._resize_hibernation()
    _settle(qapp)

    # The gate is a plain file-existence check now (no subprocess call at
    # all), so declining the confirm must leave subprocess.run uncalled.
    assert calls == []


def test_clear_print_queue_is_wired_into_the_action_panel(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    assert "clear_print_queue" in tab._action_buttons
    assert "clear_print_queue" in tab._action_status


def test_clear_print_queue_runs_the_stop_clear_restart_sequence(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []

    class _R:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)

    tab._clear_print_queue()
    _settle(qapp)

    assert len(calls) == 1
    cmd = calls[0]
    assert "net stop spooler" in cmd
    assert "spool\\PRINTERS" in cmd or "spool\\\\PRINTERS" in cmd
    assert "net start spooler" in cmd


def test_clear_print_queue_does_nothing_when_confirm_declined(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    tab._clear_print_queue()

    assert calls == []


def test_clear_print_queue_restarts_spooler_even_when_del_fails(qapp, monkeypatch):
    """I2: `del` failing (a locked spool file, nonzero exit) must not
    prevent `net start spooler` from running -- an all-&& chain would
    leave the spooler stopped and printing broken. The fix uses `&`
    (unconditional) between del and the restart, grouped in parens so the
    leading `&&` still gates the whole group on `net stop spooler`
    succeeding. This is verified by inspecting the constructed command
    string's shape, since a real shell isn't invoked in this test."""
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)

    tab._clear_print_queue()
    _settle(qapp)

    assert len(calls) == 1
    cmd = calls[0]
    # "net start spooler" must be in the SAME command string, joined to
    # the del step by a plain `&` (unconditional), not `&&` (gated on
    # success) -- so it always runs regardless of whether del succeeded.
    assert "net start spooler" in cmd
    del_and_restart = cmd.split("net stop spooler && ", 1)[1]
    assert "&&" not in del_and_restart, (
        f"del and the restart must be joined unconditionally (&), not "
        f"gated (&&): {del_and_restart!r}")
    assert " & " in del_and_restart or del_and_restart.count("&") >= 1


def test_resize_hibernations_own_button_is_disabled_until_it_finishes(qapp, monkeypatch):
    """M7: the new actions must follow the same busy-guard/status pattern
    as the established ones -- see
    test_quick_cleanup_one_click_actions.py::
    test_a_running_actions_own_button_is_disabled_until_it_finishes."""
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

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)

    btn = tab._action_buttons["resize_hibernation"]
    assert btn.isEnabled()

    tab._resize_hibernation()
    qapp.processEvents()
    assert not btn.isEnabled(), "button stayed enabled while its own action was running"

    release.append(1)
    _settle(qapp)
    assert btn.isEnabled(), "button never re-enabled after finishing"
