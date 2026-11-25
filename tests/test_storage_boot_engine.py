"""Unit tests for Storage Integrity and Boot Health engine and UI."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.system_health.storage_boot_engine import (
    BootHealthSnapshot,
    VolumeIntegrity,
    get_boot_storage_health,
    get_fast_startup_info,
    get_queued_chkdsk_drives,
    get_secure_boot_info,
    get_trim_status,
    get_winre_info,
    query_volumes_integrity,
)


class TestStorageBootEngine(unittest.TestCase):
    """Test pure Python queries for storage integrity and boot posture."""

    @patch("winreg.OpenKey")
    @patch("winreg.QueryValueEx")
    @patch("psutil.boot_time")
    @patch("time.time")
    def test_get_fast_startup_info(self, mock_time, mock_btime, mock_qval, mock_open):
        mock_qval.return_value = (1, 4)
        mock_btime.return_value = 1000.0
        mock_time.return_value = 1000.0 + (3600.0 * 24.0 * 8.0)  # 8 days uptime

        res = get_fast_startup_info()
        self.assertTrue(res["enabled"])
        self.assertAlmostEqual(res["uptime_days"], 8.0, places=1)
        self.assertTrue(res["needs_cold_reboot"])

    def test_get_winre_info_parses_xml(self):
        xml_content = """<?xml version='1.0' encoding='utf-8'?>
        <WindowsRE version="2.0">
            <WinreBCD id="{12345678-ABCD-EF01-2345-6789ABCDEF01}"/>
            <WinreLocation path="\\Recovery\\WindowsRE"/>
            <InstallState state="1"/>
            <IsAutoRepairOn state="1"/>
        </WindowsRE>
        """
        with patch("os.path.isfile", return_value=True), \
             patch("xml.etree.ElementTree.parse") as mock_parse:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_content)
            tree_mock = MagicMock()
            tree_mock.getroot.return_value = root
            mock_parse.return_value = tree_mock

            info = get_winre_info("dummy.xml")
            self.assertTrue(info["enabled"])
            self.assertEqual(info["location"], "\\Recovery\\WindowsRE")
            self.assertEqual(info["bcd_id"], "{12345678-ABCD-EF01-2345-6789ABCDEF01}")

    @patch("subprocess.run")
    def test_get_trim_status(self, mock_subproc):
        sample = """
        NTFS DisableDeleteNotify = 0  (Allows TRIM)
        ReFS DisableDeleteNotify = 1  (Disables TRIM)
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=sample)
        res = get_trim_status()
        self.assertTrue(res["ntfs_trim"])
        self.assertFalse(res["refs_trim"])
        self.assertFalse(res["trim_healthy"])

    @patch("winreg.OpenKey")
    @patch("winreg.QueryValueEx")
    def test_get_queued_chkdsk_drives(self, mock_qval, mock_open):
        mock_qval.return_value = (["autochk /p \\??\\C:", "autochk /k:E *"], 7)
        queued = get_queued_chkdsk_drives()
        self.assertIn("C:", queued)

    @patch("psutil.disk_partitions")
    @patch("psutil.disk_usage")
    @patch("subprocess.run")
    def test_query_volumes_integrity_clean(self, mock_subproc, mock_usage, mock_parts):
        part = MagicMock()
        part.mountpoint = "C:\\"
        part.fstype = "NTFS"
        mock_parts.return_value = [part]

        usage = MagicMock()
        usage.total = 100 * 1024**3
        usage.free = 50 * 1024**3
        mock_usage.return_value = usage

        mock_subproc.return_value = MagicMock(returncode=0, stdout="Volume - C: is NOT Dirty\n")

        vols = query_volumes_integrity()
        self.assertEqual(len(vols), 1)
        self.assertEqual(vols[0].drive_letter, "C:")
        self.assertFalse(vols[0].is_dirty)
        self.assertEqual(vols[0].status_text, "Clean")

    def test_storage_boot_tab_ui(self):
        from PyQt6.QtWidgets import QApplication
        from modules.system_health.storage_boot_tab import StorageBootTab

        app = QApplication.instance() or QApplication([])
        tab = StorageBootTab()
        self.assertIsNotNone(tab._table)

        snap = BootHealthSnapshot(
            fast_startup_enabled=True,
            kernel_uptime_hours=24.0,
            kernel_uptime_days=1.0,
            winre_enabled=True,
            winre_location="\\Recovery\\WindowsRE",
            secure_boot_enabled=True,
            trim_ntfs_enabled=True,
            trim_refs_enabled=True,
            volumes=[
                VolumeIntegrity(
                    drive_letter="C:",
                    file_system="NTFS",
                    total_bytes=100 * 1024**3,
                    free_bytes=50 * 1024**3,
                    is_dirty=False,
                    chkdsk_queued=False,
                    status_text="Clean",
                )
            ],
            overall_health="Healthy",
            recommendations=[],
        )
        tab._on_loaded(snap)
        self.assertEqual(tab._table.rowCount(), 1)
        self.assertIn("Healthy", tab._health_badge.text())
        self.assertIn("Clean", tab._table.item(0, 3).text())

        tab.cancel()
        tab._cancel_all()


if __name__ == "__main__":
    unittest.main()
