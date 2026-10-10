"""'A restart is pending' after every reboot -- and what actually needs one.

The entries below are the real PendingFileRenameOperations on this machine
(2026-10-10), three hours after a reboot: OneDrive and Edge updaters queue
their leftovers for deletion after every boot.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core import pending_reboot as pr  # noqa: E402

REAL = [
    "*1\\??\\C:\\Windows\\System32\\gamingservicesproxy_13.dll.0", "",
    "*1\\??\\C:\\Program Files (x86)\\Microsoft\\Edge\\Temp\\66312_821001325\\old_msedge.exe", "",
    "*1\\??\\C:\\Program Files (x86)\\Microsoft\\Edge\\Temp", "",
    "*1\\??\\C:\\Program Files\\Microsoft OneDrive\\Update\\OneDriveSetup.exe", "",
    "*2\\??\\C:\\Program Files\\Microsoft OneDrive\\StandaloneUpdater\\OneDriveSetup.exe", "",
    "*1\\??\\C:\\Program Files\\Microsoft OneDrive\\26.178.0913.0006", "",
]


def test_the_star_prefix_and_device_prefix_are_stripped():
    ops = pr.parse_operations(REAL)
    assert ops[0].path == "C:\\Windows\\System32\\gamingservicesproxy_13.dll.0"
    assert all(op.kind == pr.DELETE for op in ops)


def test_updater_leftovers_are_housekeeping_not_a_pending_restart():
    state = pr.check(lambda p: False, lambda: pr.parse_operations(REAL))
    assert not state.needed and state.reasons == []
    assert {op.owner for op in state.housekeeping} == {"Gaming Services", "Microsoft Edge", "OneDrive"}
    assert "OneDrive" in pr.housekeeping_text(state.housekeeping)


def test_a_queued_replacement_needs_the_restart_and_names_who():
    ops = REAL + ["\\??\\C:\\Program Files\\NVIDIA Corporation\\x\\new.dll",
                  "!\\??\\C:\\Program Files\\NVIDIA Corporation\\x\\nvx.dll"]
    state = pr.check(lambda p: False, lambda: pr.parse_operations(ops))
    assert state.needed and "NVIDIA" in state.reasons[0] and "1 file replacement" in state.reasons[0]


def test_servicing_and_windows_update_markers_need_the_restart():
    state = pr.check(lambda p: "RebootPending" in p, lambda: [])
    assert state.reasons == ["servicing (CBS)"]


def test_an_unreadable_list_is_unreadable_not_clear():
    state = pr.check(lambda p: None, lambda: None)
    assert not state.needed and len(state.unreadable) == 3


def test_overview_offers_a_restart_button_only_when_a_restart_is_needed():
    from modules.dashboard import overview_health as oh
    finding = oh.judge_reboot(["Windows Update"])
    assert finding.action_module == oh.RESTART_ACTION and finding.action_label.startswith("Restart now")
    assert oh.judge_reboot([]) is None


def test_system_health_keeps_pairs_aligned_and_ignores_deletions():
    from modules.system_health.health_checks import pending_reboot_reasons
    reasons, bad = pending_reboot_reasons(lambda p: False, lambda: list(REAL))
    assert reasons == [] and bad == []


def test_the_restart_button_asks_first_and_restarts_only_on_yes(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from modules.dashboard import dashboard_module as dm
    from modules.dashboard.overview_health import RESTART_ACTION
    calls = []
    monkeypatch.setattr(pr, "restart_now", lambda delay=10: calls.append(delay) or (True, "ok"))
    target = dm.OverviewModule().create_widget()            # the Overview page itself
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    target._navigate(RESTART_ACTION)
    assert calls == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    target._navigate(RESTART_ACTION)
    assert calls == [10]


def test_live_this_machine_reads_cleanly():
    state = pr.check()
    assert state.unreadable == []
