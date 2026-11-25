"""Tests for Quick Fix Overhaul: Pre-Flight Health Scanner, Triage Playbooks,
and Enhanced UI Components (Category Chips, Console Drawer, Playbook Cards).
"""

from unittest.mock import MagicMock, patch
import pytest

from modules.quick_fix.console_drawer import QuickFixConsoleDrawer
from modules.quick_fix.fix_actions import ALL_ACTIONS, FixAction
from modules.quick_fix.playbook_card import PlaybookCard
from modules.quick_fix.quick_fix_module import QuickFixModule, _FixCard
from modules.quick_fix.quick_fix_playbooks import (
    PLAYBOOKS,
    Playbook,
    PlaybookResult,
    get_playbook,
    run_playbook,
)
from modules.quick_fix.quick_fix_scanner import (
    HealthFinding,
    QuickFixHealthReport,
    check_pending_reboot_status,
    check_print_spooler_queue,
    check_time_synchronization,
    check_vss_writers,
    check_winre_status,
    check_wmi_repository,
    check_wu_and_bits_state,
    scan_system_health,
)


# ============================================================================
# 1. Scanner Unit Tests
# ============================================================================


def test_wmi_repo_check_clean():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_cmd.return_value = MagicMock(returncode=0, stdout="WMI repository is consistent\n", stderr="")
        res = check_wmi_repository()
        assert res is None


def test_wmi_repo_check_corrupt():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_cmd.return_value = MagicMock(returncode=1, stdout="WMI repository is inconsistent\n", stderr="")
        res = check_wmi_repository()
        assert res is not None
        assert res.check_id == "wmi_repository"
        assert res.severity == "danger"
        assert "wmi_salvage" in res.recommended_fix_keys


def test_print_spooler_check_empty(tmp_path):
    with patch("modules.quick_fix.quick_fix_scanner.system_root", return_value=str(tmp_path)):
        spool_dir = tmp_path / "System32" / "spool" / "PRINTERS"
        spool_dir.mkdir(parents=True, exist_ok=True)
        res = check_print_spooler_queue()
        assert res is None


def test_print_spooler_check_stuck_jobs(tmp_path):
    with patch("modules.quick_fix.quick_fix_scanner.system_root", return_value=str(tmp_path)):
        spool_dir = tmp_path / "System32" / "spool" / "PRINTERS"
        spool_dir.mkdir(parents=True, exist_ok=True)
        (spool_dir / "FP0001.SPL").write_text("dummy")
        (spool_dir / "FP0001.SHD").write_text("dummy")
        res = check_print_spooler_queue()
        assert res is not None
        assert res.check_id == "print_queue"
        assert "print_queue" in res.recommended_fix_keys


def test_vss_writers_check_clean():
    vss_out = """Writer name: 'System Writer'
   Writer Id: {e81062d3-1801-436f-a083-58665d73c22f}
   State: [1] Stable
   Last error: [0] No error
"""
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_cmd.return_value = MagicMock(returncode=0, stdout=vss_out, stderr="")
        res = check_vss_writers()
        assert res is None


def test_vss_writers_check_failed():
    vss_out = """Writer name: 'System Writer'
   Writer Id: {e81062d3-1801-436f-a083-58665d73c22f}
   State: [5] Waiting for completion
   Last error: [8] Timed out
"""
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_cmd.return_value = MagicMock(returncode=0, stdout=vss_out, stderr="")
        res = check_vss_writers()
        assert res is not None
        assert res.check_id == "vss_writers"
        assert "reset_vss_writers" in res.recommended_fix_keys


def test_winre_check_disabled():
    out = "Windows RE status:         Disabled\nWindows RE location:       \n"
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_cmd.return_value = MagicMock(returncode=0, stdout=out, stderr="")
        res = check_winre_status()
        assert res is not None
        assert res.check_id == "winre_disabled"
        assert "enable_winre" in res.recommended_fix_keys


def test_time_sync_check_desync():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_cmd.return_value = MagicMock(returncode=1, stdout="The computer did not resync because no time data was available.", stderr="")
        res = check_time_synchronization()
        assert res is not None
        assert res.check_id == "time_desync"
        assert "reset_w32tm" in res.recommended_fix_keys


def test_scan_system_health_aggregate():
    with patch("modules.quick_fix.quick_fix_scanner.check_wmi_repository") as m_wmi, \
         patch("modules.quick_fix.quick_fix_scanner.check_print_spooler_queue") as m_prn, \
         patch("modules.quick_fix.quick_fix_scanner.check_vss_writers") as m_vss, \
         patch("modules.quick_fix.quick_fix_scanner.check_winre_status") as m_re, \
         patch("modules.quick_fix.quick_fix_scanner.check_unquoted_service_paths") as m_unq, \
         patch("modules.quick_fix.quick_fix_scanner.check_wu_and_bits_state") as m_wu, \
         patch("modules.quick_fix.quick_fix_scanner.check_time_synchronization") as m_tm, \
         patch("modules.quick_fix.quick_fix_scanner.check_pending_reboot_status") as m_rb:

        m_wmi.return_value = HealthFinding("wmi", "WMI Error", "desc", "danger", ["wmi_salvage"])
        m_prn.return_value = None
        m_vss.return_value = None
        m_re.return_value = None
        m_unq.return_value = None
        m_wu.return_value = None
        m_tm.return_value = HealthFinding("time", "Time Desync", "desc", "warning", ["sync_time", "reset_w32tm"])
        m_rb.return_value = None

        report = scan_system_health()
        assert len(report.findings) == 2
        assert report.issue_count == 2
        assert "wmi_salvage" in report.recommended_keys
        assert "reset_w32tm" in report.recommended_keys


# ============================================================================
# 2. Playbook Engine Tests
# ============================================================================


def test_playbooks_catalog():
    assert len(PLAYBOOKS) == 4
    keys = [p.key for p in PLAYBOOKS]
    assert "network_repair" in keys
    assert "wu_reset_full" in keys
    assert "ui_cache_refresh" in keys
    assert "system_vss_refresh" in keys

    pb = get_playbook("network_repair")
    assert pb is not None
    assert pb.title == "Complete Network Stack Repair"
    assert "flush_dns" in pb.action_keys


def test_run_playbook_success():
    log_lines = []
    def out_cb(line):
        log_lines.append(line)

    test_pb = Playbook(
        key="test_pb",
        title="Test Playbook",
        description="Testing execution",
        category="Test",
        icon="🧪",
        action_keys=["cleanmgr"],
        reboot_required=False,
    )

    with patch("modules.quick_fix.quick_fix_playbooks._get_action_map") as mock_map:
        dummy_action = FixAction(
            key="cleanmgr",
            title="Clean Test",
            description="desc",
            category="Test",
            fn=lambda cb: cb("Ran successfully"),
        )
        mock_map.return_value = {"cleanmgr": dummy_action}

        res = run_playbook(test_pb, out_cb)
        assert res.success is True
        assert res.executed_steps == 1
        assert res.total_steps == 1
        assert len(res.failures) == 0
        assert any("Ran successfully" in line for line in log_lines)


def test_run_playbook_cancelled():
    test_pb = Playbook(
        key="test_cancel",
        title="Cancel Test",
        description="Testing cancellation",
        category="Test",
        icon="🛑",
        action_keys=["cleanmgr", "perf_reset"],
    )

    is_cancelled = MagicMock(return_value=True)
    res = run_playbook(test_pb, lambda l: None, is_cancelled=is_cancelled)
    assert res.success is False
    assert res.executed_steps == 0


# ============================================================================
# 3. UI Component Tests
# ============================================================================


def test_console_drawer_operations(qapp):
    drawer = QuickFixConsoleDrawer()
    assert drawer._line_count == 0

    drawer.append_line("Test", "First line")
    assert drawer._line_count == 1
    assert "First line" in drawer._text_edit.toPlainText()

    drawer.notify_start("Network Repair")
    assert "Running" in drawer._timer_lbl.text()

    drawer.notify_complete("Network Repair", True)
    assert "Completed" in drawer._timer_lbl.text()

    drawer.clear_log()
    assert drawer._line_count == 0
    assert drawer._text_edit.toPlainText() == ""


def test_playbook_card_creation(qapp):
    pb = PLAYBOOKS[0]
    card = PlaybookCard(pb)
    assert card._playbook.key == pb.key
    assert card._run_btn is not None
    assert card._run_btn.isEnabled()


def test_quick_fix_module_filtering(qapp):
    module = QuickFixModule()
    widget = module.create_widget()
    assert widget is not None
    assert len(module._cards) > 0
    widget.show()

    # Test category chip switching
    module._on_chip_clicked("Network")
    visible_cards = [c for c in module._cards if c.isVisible()]
    assert len(visible_cards) > 0
    assert all(c._category == "Network" for c in visible_cards)

    # Test Needs Attention filtering when none recommended
    module._on_chip_clicked("Needs Attention")
    visible_cards = [c for c in module._cards if c.isVisible()]
    assert len(visible_cards) == 0

    # Simulate health report recommendation
    report = QuickFixHealthReport(
        findings=[HealthFinding("wmi", "WMI", "desc", "danger", ["wmi_salvage"])],
        recommended_keys={"wmi_salvage"},
    )
    module._on_scan_completed(report)
    module._on_chip_clicked("Needs Attention")
    visible_cards = [c for c in module._cards if c.isVisible()]
    assert len(visible_cards) == 1
    assert getattr(visible_cards[0], "_action").key == "wmi_salvage"

    # Reset to All
    module._on_chip_clicked("All")
    visible_cards = [c for c in module._cards if c.isVisible()]
    assert len(visible_cards) == len(module._cards)
