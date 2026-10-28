"""Run progress status and stall detection (#19)."""
from modules.updates.run_all_tab import STALL_AFTER_S, format_running_status


def test_status_shows_phase_and_elapsed():
    assert format_running_status("Installing", 75) == "Installing… 1m 15s"
    assert format_running_status("", 5) == "Working… 0m 05s"


def test_status_says_when_there_has_been_no_output():
    assert format_running_status("Installing", 200, idle_s=STALL_AFTER_S - 1) == "Installing… 3m 20s"
    assert "no output for 5m" in format_running_status("Installing", 400, idle_s=310)


def test_phase_markers_update_the_tab_not_the_log(qapp):
    from modules.updates.run_all_tab import _RunAllTab
    tab = _RunAllTab()
    tab._running = True
    tab._op_timer = type("T", (), {"elapsed": lambda self: 1000})()
    tab._on_log("Phase: installing")
    assert tab._phase == "Installing" and "Installing" in tab._status_lbl.text()
    tab._on_log("real output")
    assert "real output" in tab._log.toPlainText()
    assert "Phase: installing" not in tab._log.toPlainText()
    tab._running = False
