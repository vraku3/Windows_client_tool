"""Unit tests for Batch 3 sysadmin engines:
- usn_journal.py
- shadow_storage.py
- storage_spaces.py
- ads_scanner.py
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.disk_health.shadow_storage import _parse_size_mb, get_shadow_storage_entries
from modules.disk_health.storage_spaces import inspect_storage_spaces
from modules.file_forensics.ads_scanner import get_file_streams
from modules.file_forensics.usn_journal import query_usn_journal


class TestBatch3Engines(unittest.TestCase):
    """Test Batch 3 storage, shadow copy, and forensics engines."""

    @patch("subprocess.run")
    def test_usn_journal_query(self, mock_subproc):
        sample = """
Usn Journal ID   : 0x01dd709524176729
First Usn        : 0x00000000dd800000
Next Usn         : 0x00000000dfb8eea0
Maximum Size     : 0x0000000002000000 (32.0 MB)
Allocation Delta : 0x0000000000800000 ( 8.0 MB)
Write range tracking: Disabled
"""
        mock_subproc.return_value = MagicMock(returncode=0, stdout=sample)
        info = query_usn_journal("C:")
        self.assertTrue(info.is_active)
        self.assertEqual(info.max_size_mb, 32.0)
        self.assertEqual(info.allocation_delta_mb, 8.0)

    def test_shadow_storage_size_parsing(self):
        self.assertAlmostEqual(_parse_size_mb("5 GB"), 5120.0, places=1)
        self.assertAlmostEqual(_parse_size_mb("500 MB"), 500.0, places=1)
        self.assertAlmostEqual(_parse_size_mb("1 TB"), 1048576.0, places=1)

    @patch("subprocess.run")
    def test_storage_spaces_degraded_detection(self, mock_subproc):
        json_sample = """
        {
            "Pools": [
                {
                    "FriendlyName": "Degraded Pool",
                    "OperationalStatus": [6],
                    "HealthStatus": 2,
                    "Size": 1000,
                    "AllocatedSize": 500
                }
            ],
            "Disks": []
        }
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=json_sample)
        res = inspect_storage_spaces()
        self.assertTrue(res.is_degraded)
        self.assertIn("DEGRADED", res.verdict)

    def test_ads_scanner_notepad(self):
        # Notepad in Windows usually only has default ::$DATA
        streams = get_file_streams(r"C:\Windows\System32\notepad.exe")
        # ADS list only contains streams other than ::$DATA
        for s in streams:
            self.assertNotEqual(s.stream_name.lower(), "::$data")


if __name__ == "__main__":
    unittest.main()
