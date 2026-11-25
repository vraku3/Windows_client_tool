"""Unit tests for Quick Fix pre-flight health scanner."""

from unittest.mock import MagicMock, patch
import pytest

from modules.quick_fix.quick_fix_scanner import (
    HealthFinding,
    QuickFixHealthReport,
    check_wmi_repository,
    check_print_spooler_queue,
    check_vss_writers,
    check_winre_status,
    check_unquoted_service_paths,
    check_wu_and_bits_state,
    check_time_synchronization,
    check_pending_reboot_status,
    scan_system_health,
)


def test_quick_fix_health_report_issue_count():
    report = QuickFixHealthReport(
        findings=[
            HealthFinding("wmi", "WMI", "WMI err", "danger", ["wmi_salvage"]),
            HealthFinding("print", "Print", "Print err", "warning", ["print_queue"]),
            HealthFinding("reboot", "Reboot", "Reboot pending", "info", []),
        ],
        recommended_keys={"wmi_salvage", "print_queue"},
    )
    assert report.issue_count == 2
    assert "wmi_salvage" in report.recommended_keys


def test_check_wmi_repository_detects_inconsistent():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "WMI repository is INCONSISTENT"
        mock_proc.stderr = ""
        mock_cmd.return_value = mock_proc

        finding = check_wmi_repository()
        assert finding is not None
        assert finding.check_id == "wmi_repository"
        assert finding.severity == "danger"
        assert "wmi_salvage" in finding.recommended_fix_keys


def test_check_wmi_repository_clean():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "WMI repository is consistent"
        mock_proc.stderr = ""
        mock_cmd.return_value = mock_proc

        assert check_wmi_repository() is None


def test_check_print_spooler_queue_detects_jobs(tmp_path):
    with patch("modules.quick_fix.quick_fix_scanner.system_root", return_value=str(tmp_path)):
        spool_dir = tmp_path / "System32" / "spool" / "PRINTERS"
        spool_dir.mkdir(parents=True)
        (spool_dir / "FP00001.SPL").write_text("dummy")

        finding = check_print_spooler_queue()
        assert finding is not None
        assert finding.check_id == "print_queue"
        assert "print_queue" in finding.recommended_fix_keys


def test_check_vss_writers_detects_failure():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = """
Writer name: 'System Writer'
   Writer Id: {e81069a3-c242-4bf0-8596-4f664a60ccff}
   Writer Instance Id: {1234}
   State: [5] Waiting for completion
   Last error: Timed out
"""
        mock_cmd.return_value = mock_proc

        finding = check_vss_writers()
        assert finding is not None
        assert finding.check_id == "vss_writers"
        assert "reset_vss_writers" in finding.recommended_fix_keys


def test_check_winre_status_detects_disabled():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Windows RE status:         Disabled\nWindows RE location:"
        mock_cmd.return_value = mock_proc

        finding = check_winre_status()
        assert finding is not None
        assert finding.check_id == "winre_disabled"
        assert "enable_winre" in finding.recommended_fix_keys


def test_check_wu_and_bits_state_disabled():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_q = MagicMock(returncode=0, stdout="STATE : 1 STOPPED", stderr="")
        mock_qc = MagicMock(returncode=0, stdout="START_TYPE : 4 DISABLED", stderr="")
        mock_cmd.side_effect = [mock_q, mock_qc]

        finding = check_wu_and_bits_state()
        assert finding is not None
        assert finding.check_id == "wu_service_disabled"
        assert "wu_reset" in finding.recommended_fix_keys


def test_check_time_synchronization_detects_error():
    with patch("modules.quick_fix.quick_fix_scanner._run_quick_cmd") as mock_cmd:
        mock_proc = MagicMock(returncode=1, stdout="The service has not been started.", stderr="")
        mock_cmd.return_value = mock_proc

        finding = check_time_synchronization()
        assert finding is not None
        assert finding.check_id == "time_desync"
        assert "reset_w32tm" in finding.recommended_fix_keys


def test_scan_system_health_runs_and_cancels():
    mock_worker = MagicMock()
    mock_worker.is_cancelled = True

    # When cancelled, loop exits immediately
    report = scan_system_health(worker=mock_worker)
    assert isinstance(report, QuickFixHealthReport)
    assert len(report.findings) == 0
