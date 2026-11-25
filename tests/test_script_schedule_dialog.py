"""Unit tests for the ScheduleScriptDialog in Scripts Hub."""
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QTime
from modules.scripts_hub.library import Script
from modules.scripts_hub.schedule_dialog import ScheduleScriptDialog


def test_schedule_dialog_initialization(qapp, tmp_path):
    dummy_ps1 = tmp_path / "audit_test.ps1"
    dummy_ps1.write_text("Write-Output 'hello'", encoding="utf-8")

    s = Script("test_audit", "Test Audit", "Testing", "A test check", "", path=str(dummy_ps1))
    dlg = ScheduleScriptDialog(s)

    assert "WinClientTool_test_audit" in dlg._task_name.text()
    assert dlg._frequency.currentText() == "Daily"
    assert dlg._elevated_chk.isChecked() is True

    # Test custom frequency and time change
    dlg._frequency.setCurrentText("Weekly")
    dlg._time_edit.setTime(QTime(4, 30))
    dlg._args_edit.setText("-AuditOnly")

    # Mock subprocess.run
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = "SUCCESS"
    mock_res.stderr = ""

    with patch("subprocess.run", return_value=mock_res) as mock_run:
        with patch("PyQt6.QtWidgets.QMessageBox.information") as mock_box:
            dlg._create_task()
            assert mock_run.called
            cmd = mock_run.call_args[0][0]
            assert "schtasks.exe" in cmd
            assert "/Create" in cmd
            assert "WEEKLY" in cmd
            assert "04:30" in cmd
            assert "/RL" in cmd and "HIGHEST" in cmd
