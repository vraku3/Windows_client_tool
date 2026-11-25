"""Unit tests for Batch 4 sysadmin engines:
- process_net_matrix.py
- dhcp_gateway.py
- smb_inspector.py
- cert_inspector.py
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.network_diagnostics.dhcp_gateway import parse_ipconfig_all
from modules.network_diagnostics.process_net_matrix import get_active_process_network_matrix
from modules.network_diagnostics.smb_inspector import inspect_smb_posture
from modules.security_dashboard.cert_inspector import audit_root_certificates


class TestBatch4Engines(unittest.TestCase):
    """Test Batch 4 network, DHCP, and protocol security engines."""

    def test_process_net_matrix_real(self):
        matrix = get_active_process_network_matrix()
        self.assertIsInstance(matrix, list)
        if matrix:
            self.assertGreater(matrix[0].total_sockets, 0)

    def test_dhcp_gateway_parsing(self):
        sample = """
Ethernet adapter Ethernet 2:

   Description . . . . . . . . . . . : Realtek PCIe 5GbE Family Controller
   DHCP Enabled. . . . . . . . . . . : Yes
   IPv4 Address. . . . . . . . . . . : 192.168.3.84(Preferred)
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Lease Obtained. . . . . . . . . . : Friday, November 20, 2026 8:21:51 AM
   Lease Expires . . . . . . . . . . : Friday, November 20, 2026 7:29:22 PM
   Default Gateway . . . . . . . . . : 192.168.3.1
   DHCP Server . . . . . . . . . . . : 192.168.3.1
   DNS Servers . . . . . . . . . . . : 1.1.1.1
                                       1.0.0.1
"""
        adapters = parse_ipconfig_all(sample)
        self.assertEqual(len(adapters), 1)
        a = adapters[0]
        self.assertEqual(a.ip_address, "192.168.3.84")
        self.assertTrue(a.dhcp_enabled)
        self.assertEqual(a.default_gateway, "192.168.3.1")
        self.assertIn("1.1.1.1", a.dns_servers)

    @patch("subprocess.run")
    def test_smb_inspector_posture(self, mock_subproc):
        json_sample = """
        {
            "Server": {
                "EnableSMB1Protocol": false,
                "EnableSMB2Protocol": true,
                "RequireSecuritySignature": true,
                "EncryptData": true
            },
            "Connections": []
        }
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=json_sample)
        rep = inspect_smb_posture()
        self.assertTrue(rep.server_posture.is_secure)
        self.assertFalse(rep.server_posture.smbv1_enabled)
        self.assertTrue(rep.server_posture.require_signing)

    @patch("subprocess.run")
    def test_cert_inspector_detection(self, mock_subproc):
        json_sample = """
        [
            {
                "Subject": "CN=Bitdefender Personal CA.Net-Defender, O=Bitdefender",
                "Issuer": "CN=Bitdefender",
                "Thumbprint": "123456",
                "NotAfter": "/Date(2109914310000)/"
            }
        ]
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=json_sample)
        audit = audit_root_certificates()
        self.assertEqual(audit.total_certs, 1)
        self.assertEqual(audit.inspection_count, 1)
        self.assertTrue(audit.flagged_certs[0].is_inspection_ca)


if __name__ == "__main__":
    unittest.main()
