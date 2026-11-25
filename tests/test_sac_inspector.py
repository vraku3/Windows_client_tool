"""Unit tests for Smart App Control & WDAC Inspector engine and UI."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.sac_inspector import (
    CIBlockEvent,
    check_file_trust,
    get_recent_ci_blocks,
    get_sac_state,
)


class TestSacInspector(unittest.TestCase):
    """Test pure Python queries for SAC and Code Integrity."""

    @patch("winreg.OpenKey")
    @patch("winreg.QueryValueEx")
    @patch("subprocess.run")
    @patch("os.path.exists")
    @patch("os.listdir")
    def test_get_sac_state_enforced(self, mock_listdir, mock_exists, mock_subproc, mock_qval, mock_open):
        mock_qval.return_value = (1, 4)  # 1 = Enforced (On)
        mock_subproc.return_value = MagicMock(returncode=0, stdout="ConstrainedLanguage\n")
        mock_exists.return_value = True
        mock_listdir.return_value = ["{0283AC0F-FFF1-49AE-ADA1-8A933130CAD6}.cip", "other.cip"]

        state = get_sac_state()

        self.assertEqual(state["state_code"], 1)
        self.assertEqual(state["state_label"], "Enforced (On)")
        self.assertTrue(state["is_enforced"])
        self.assertFalse(state["is_eval"])
        self.assertEqual(state["ps_language_mode"], "ConstrainedLanguage")
        self.assertTrue(state["has_sac_policy"])
        self.assertIn("other.cip", state["active_policies"])

    @patch("subprocess.run")
    def test_get_recent_ci_blocks_parses_xml(self, mock_subproc):
        xml_sample = """
        <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
            <System>
                <EventID>3077</EventID>
                <TimeCreated SystemTime="2026-11-20T10:15:30.1234567Z"/>
            </System>
            <EventData>
                <Data Name="File Name">\\Device\\HarddiskVolume3\\Users\\test\\bad.exe</Data>
                <Data Name="Process Name">cmd.exe</Data>
                <Data Name="PolicyName">VerifiedAndReputablePolicy</Data>
                <Data Name="PolicyGUID">{0283ac0f-fff1-49ae-ada1-8a933130cad6}</Data>
                <Data Name="SHA256 Flat Hash">ABCDEF1234567890</Data>
                <Data Name="Status">0xc0000428</Data>
            </EventData>
        </Event>
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=xml_sample)

        blocks = get_recent_ci_blocks()
        self.assertEqual(len(blocks), 1)
        evt = blocks[0]
        self.assertEqual(evt.event_id, 3077)
        self.assertEqual(evt.process_name, "cmd.exe")
        self.assertTrue(evt.is_sac)
        self.assertEqual(evt.sha256, "ABCDEF1234567890")
        self.assertEqual(evt.status_code, "0xc0000428")

    @patch("os.path.isfile")
    @patch("subprocess.run")
    def test_check_file_trust_trusted(self, mock_subproc, mock_isfile):
        mock_isfile.return_value = True
        mock_subproc.return_value = MagicMock(returncode=0, stdout="File is Trusted\n", stderr="")

        trusted, msg = check_file_trust(r"C:\App\test.exe")
        self.assertTrue(trusted)
        self.assertIn("Trusted", msg)

    @patch("os.path.isfile")
    @patch("subprocess.run")
    def test_check_file_trust_untrusted(self, mock_subproc, mock_isfile):
        mock_isfile.return_value = True
        mock_subproc.return_value = MagicMock(returncode=1, stdout="Not a known good file\n", stderr="")

        trusted, msg = check_file_trust(r"C:\App\unsigned.exe")
        self.assertFalse(trusted)
        self.assertIn("Untrusted", msg)

    def test_app_control_tab_ui_creation(self):
        from PyQt6.QtWidgets import QApplication
        from modules.security_dashboard.app_control_tab import AppControlTab

        app = QApplication.instance() or QApplication([])
        tab = AppControlTab()
        self.assertIsNotNone(tab._sac_badge)
        self.assertIsNotNone(tab._table)

        # Test populating results
        fake_result = {
            "state": {
                "state_label": "Enforced (On)",
                "is_enforced": True,
                "is_eval": False,
                "ps_language_mode": "ConstrainedLanguage",
                "active_policies": ["test.cip"],
                "has_sac_policy": True,
            },
            "events": [
                CIBlockEvent(
                    event_id=3077,
                    timestamp="2026-11-20T12:00:00Z",
                    target_file=r"\bad.exe",
                    process_name="cmd.exe",
                    policy_name="Smart App Control",
                    policy_guid="{0283ac0f-fff1-49ae-ada1-8a933130cad6}",
                    sha256="123456",
                    is_sac=True,
                    status_code="0xc0000428",
                )
            ],
        }
        tab._on_loaded(fake_result)
        self.assertEqual(tab._table.rowCount(), 1)
        self.assertEqual(tab._sac_badge.text(), "Enforced (On)")
        self.assertEqual(tab._ps_badge.text(), "ConstrainedLanguage")

        tab.cancel()
        tab._cancel_all()


if __name__ == "__main__":
    unittest.main()
