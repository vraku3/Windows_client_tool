"""Unit tests for Batch 1 sysadmin engines:
- reboot_pending.py
- vss_inspector.py
- kernel_memory.py
- winsxs_analyzer.py
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.system_health.kernel_memory import get_kernel_memory_snapshot
from modules.system_health.reboot_pending import get_pending_reboot_status
from modules.system_health.vss_inspector import parse_vssadmin_writers, restart_vss_services
from modules.system_health.winsxs_analyzer import analyze_cbs_packages


class TestBatch1Engines(unittest.TestCase):
    """Test Batch 1 system health engines."""

    @patch("winreg.OpenKey")
    @patch("winreg.QueryValueEx")
    def test_reboot_pending_operations(self, mock_qval, mock_open):
        mock_qval.return_value = ([r"\??\C:\test.dll", r"\??\C:\app.exe"], 7)
        status = get_pending_reboot_status()
        self.assertTrue(status.is_pending)
        self.assertTrue(status.rename_pending)
        self.assertIn("C:\\test.dll", status.queued_renames)

    def test_vssadmin_writer_parsing(self):
        sample = """
Writer name: 'System Writer'
   Writer Id: {e81069a3-c242-470f-81aa-114213e34b34}
   Writer Instance Id: {12345678-1234-1234-1234-123456789abc}
   State: [1] Stable
   Last error: No error

Writer name: 'SqlServerWriter'
   Writer Id: {a65faa63-5ea8-4ebc-9dbd-a0c4db26912a}
   Writer Instance Id: {98765432-4321-4321-4321-cba987654321}
   State: [8] Failed
   Last error: Retryable error
"""
        writers = parse_vssadmin_writers(sample)
        self.assertEqual(len(writers), 2)
        self.assertEqual(writers[0].name, "System Writer")
        self.assertTrue(writers[0].is_stable)
        self.assertFalse(writers[0].is_failed)

        self.assertEqual(writers[1].name, "SqlServerWriter")
        self.assertEqual(writers[1].state_code, 8)
        self.assertTrue(writers[1].is_failed)

    def test_kernel_memory_snapshot_real(self):
        snap = get_kernel_memory_snapshot()
        self.assertIsNotNone(snap)
        self.assertGreater(snap.physical_total_mb, 1000.0)
        self.assertGreater(snap.handles_count, 100)

    @patch("winreg.OpenKey")
    @patch("winreg.QueryInfoKey")
    @patch("winreg.EnumKey")
    @patch("winreg.QueryValueEx")
    def test_winsxs_analyzer(self, mock_qval, mock_enum, mock_qinfo, mock_open):
        mock_qinfo.return_value = (3, 0, 0)
        mock_enum.side_effect = ["pkg1", "pkg2", "pkg3"]
        mock_qval.side_effect = [(112, 4), (64, 4), (64, 4)]  # 1 installed, 2 superseded

        snap = analyze_cbs_packages()
        self.assertEqual(snap.total_packages, 3)
        self.assertEqual(snap.installed_count, 1)
        self.assertEqual(snap.superseded_count, 2)


if __name__ == "__main__":
    unittest.main()
