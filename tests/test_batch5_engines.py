"""Unit tests for Batch 5 sysadmin engines:
- dpc_monitor.py
- pcie_auditor.py
- pnp_error_resolver.py
- gpo_diagnostics.py
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.driver_manager.pnp_error_resolver import get_failing_pnp_devices
from modules.gpresult.gpo_diagnostics import inspect_gpo_processing
from modules.hardware_info.pcie_auditor import audit_pcie_graphics_devices
from modules.system_health.dpc_monitor import sample_dpc_isr_latency


class TestBatch5Engines(unittest.TestCase):
    """Test Batch 5 hardware, driver, and policy stability engines."""

    @patch("subprocess.run")
    def test_dpc_monitor_sample(self, mock_subproc):
        json_sample = """
        {
            "PercentProcessorTime": 10,
            "PercentDPCTime": 1,
            "PercentInterruptTime": 1,
            "DPCsQueuedPersec": 5000,
            "InterruptsPersec": 20000
        }
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=json_sample)
        sample = sample_dpc_isr_latency()
        self.assertFalse(sample.is_spiking)
        self.assertEqual(sample.dpcs_queued_per_sec, 5000)

    @patch("subprocess.run")
    def test_pcie_auditor_devices(self, mock_subproc):
        json_sample = """
        [
            {
                "Name": "AMD Radeon RX 7900 XTX",
                "DriverVersion": "32.0.0",
                "VideoProcessor": "Navi 31",
                "AdapterRAM": 4294967296
            }
        ]
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=json_sample)
        rep = audit_pcie_graphics_devices()
        self.assertEqual(rep.total_controllers, 1)
        self.assertEqual(rep.primary_gpu, "AMD Radeon RX 7900 XTX")

    @patch("subprocess.run")
    def test_pnp_error_resolver(self, mock_subproc):
        json_sample = r"""
        [
            {
                "Name": "Unknown USB Device",
                "DeviceID": "USB\\VID_0000&PID_0002\\5&123",
                "HardWareID": ["USB\\VID_046D&PID_C52B"],
                "ConfigManagerErrorCode": 28
            }
        ]
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=json_sample)
        failing = get_failing_pnp_devices()
        self.assertEqual(len(failing), 1)
        self.assertEqual(failing[0].error_code, 28)
        self.assertEqual(failing[0].vendor_id, "0x046D")
        self.assertEqual(failing[0].product_id, "0xC52B")
        self.assertIn("missing driver", failing[0].recommended_fix.lower())

    def test_gpo_diagnostics_real(self):
        rep = inspect_gpo_processing()
        self.assertIsNotNone(rep.domain_name)
        self.assertIsNotNone(rep.verdict)


if __name__ == "__main__":
    unittest.main()
