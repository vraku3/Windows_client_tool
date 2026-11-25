"""Unit tests for DriverStore scanner, superseded detection, and removal dialog."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.driver_manager.driver_store_scanner import (
    DriverPackage,
    delete_driver_package,
    mark_superseded_packages,
    parse_pnputil_output,
    scan_driver_store,
)


class TestDriverStoreScanner(unittest.TestCase):
    """Test pure Python parsing and superseded package detection."""

    def test_parse_pnputil_output(self):
        sample_output = """
Microsoft PnP Utility

Published Name:     oem7.inf
Original Name:      amdafd.inf
Provider Name:      AMD
Class Name:         System
Class GUID:         {4d36e97d-e325-11ce-bfc1-08002be10318}
Driver Version:     06/07/2024 24.10.0.1
Signer Name:        Microsoft Windows Hardware Compatibility Publisher

Published Name:     oem68.inf
Original Name:      amdafd.inf
Provider Name:      AMD
Class Name:         System
Class GUID:         {4d36e97d-e325-11ce-bfc1-08002be10318}
Driver Version:     08/21/2025 25.30.0.1
Signer Name:        Microsoft Windows Hardware Compatibility Publisher
"""
        pkgs = parse_pnputil_output(sample_output)
        self.assertEqual(len(pkgs), 2)
        self.assertEqual(pkgs[0].published_name, "oem7.inf")
        self.assertEqual(pkgs[0].original_name, "amdafd.inf")
        self.assertEqual(pkgs[0].driver_version, "24.10.0.1")
        self.assertEqual(pkgs[0].driver_date, "06/07/2024")
        self.assertEqual(pkgs[1].published_name, "oem68.inf")
        self.assertEqual(pkgs[1].driver_version, "25.30.0.1")

    def test_mark_superseded_packages(self):
        p_old = DriverPackage(
            published_name="oem7.inf",
            original_name="amdafd.inf",
            provider_name="AMD",
            class_name="System",
            class_guid="",
            driver_version="24.10.0.1",
            driver_date="06/07/2024",
            signer_name="",
        )
        p_new = DriverPackage(
            published_name="oem68.inf",
            original_name="amdafd.inf",
            provider_name="AMD",
            class_name="System",
            class_guid="",
            driver_version="25.30.0.1",
            driver_date="08/21/2025",
            signer_name="",
        )
        p_diff = DriverPackage(
            published_name="oem1.inf",
            original_name="other.inf",
            provider_name="Intel",
            class_name="Net",
            class_guid="",
            driver_version="1.0.0.0",
            driver_date="01/01/2020",
            signer_name="",
        )

        pkgs = [p_old, p_new, p_diff]
        mark_superseded_packages(pkgs)

        self.assertTrue(p_old.is_superseded)
        self.assertIn("25.30.0.1", p_old.superseded_by)
        self.assertIn("oem68.inf", p_old.superseded_by)

        self.assertFalse(p_new.is_superseded)
        self.assertFalse(p_diff.is_superseded)

    @patch("subprocess.run")
    def test_delete_driver_package_success(self, mock_subproc):
        mock_subproc.return_value = MagicMock(
            returncode=0,
            stdout="Driver package deleted successfully.\n",
            stderr="",
        )
        ok, msg = delete_driver_package("oem7.inf")
        self.assertTrue(ok)
        self.assertIn("Successfully deleted", msg)

    @patch("subprocess.run")
    def test_delete_driver_package_in_use(self, mock_subproc):
        mock_subproc.return_value = MagicMock(
            returncode=1,
            stdout="Failed to delete driver package: One or more devices are presently installed using the specified INF.\n",
            stderr="",
        )
        ok, msg = delete_driver_package("oem68.inf")
        self.assertFalse(ok)
        self.assertIn("Failed to delete", msg)

    def test_delete_driver_invalid_name(self):
        ok, msg = delete_driver_package("usb.inf")
        self.assertFalse(ok)
        self.assertIn("Invalid package name", msg)

    def test_driver_store_dialog_ui(self):
        from PyQt6.QtWidgets import QApplication
        from modules.driver_manager.driver_store_dialog import DriverStoreDialog

        app = QApplication.instance() or QApplication([])
        dlg = DriverStoreDialog()
        self.assertIsNotNone(dlg._table)

        # Populate with test packages
        p1 = DriverPackage(
            published_name="oem7.inf",
            original_name="amdafd.inf",
            provider_name="AMD",
            class_name="System",
            class_guid="",
            driver_version="24.10.0.1",
            driver_date="06/07/2024",
            signer_name="",
            is_superseded=True,
            superseded_by="25.30.0.1 (oem68.inf)",
        )
        p2 = DriverPackage(
            published_name="oem68.inf",
            original_name="amdafd.inf",
            provider_name="AMD",
            class_name="System",
            class_guid="",
            driver_version="25.30.0.1",
            driver_date="08/21/2025",
            signer_name="",
            is_superseded=False,
        )
        dlg._on_packages_loaded([p1, p2])

        # By default, only superseded are shown
        self.assertEqual(dlg._table.rowCount(), 1)

        # Uncheck only superseded
        dlg._only_superseded_cb.setChecked(False)
        self.assertEqual(dlg._table.rowCount(), 2)

        dlg.close()


if __name__ == "__main__":
    unittest.main()
