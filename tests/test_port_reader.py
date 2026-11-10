"""Unit tests for Listening Ports & Sockets reader."""
import unittest

from modules.network_diagnostics.port_reader import (
    ListeningPort,
    classify_scope,
    get_service_name,
    is_system_process,
    parse_netstat_output,
    read_listening_ports,
)


class TestPortReader(unittest.TestCase):
    def test_classify_scope(self):
        self.assertEqual(classify_scope("127.0.0.1"), "Loopback")
        self.assertEqual(classify_scope("::1"), "Loopback")
        self.assertEqual(classify_scope("[::1]"), "Loopback")
        self.assertEqual(classify_scope("127.0.0.53"), "Loopback")

        self.assertEqual(classify_scope("0.0.0.0"), "All Interfaces")
        self.assertEqual(classify_scope("::"), "All Interfaces")
        self.assertEqual(classify_scope("[::]"), "All Interfaces")

        self.assertEqual(classify_scope("192.168.1.100"), "Specific IP")
        self.assertEqual(classify_scope("10.0.0.1"), "Specific IP")

    def test_get_service_name(self):
        self.assertIn("DNS", get_service_name(53, "UDP"))
        self.assertIn("HTTP", get_service_name(80, "TCP"))
        self.assertIn("HTTPS", get_service_name(443, "TCP"))
        self.assertIn("SMB", get_service_name(445, "TCP"))
        self.assertIn("RDP", get_service_name(3389, "TCP"))
        self.assertIn("WinRM", get_service_name(5985, "TCP"))
        # High unassigned port returns string port
        self.assertEqual(get_service_name(54321), "54321")

    def test_is_system_process(self):
        self.assertTrue(is_system_process("svchost.exe"))
        self.assertTrue(is_system_process("System"))
        self.assertTrue(is_system_process("lsass.exe"))
        self.assertTrue(is_system_process("my_service.exe", r"C:\Windows\System32\my_service.exe"))
        self.assertFalse(is_system_process("node.exe", r"C:\Program Files\nodejs\node.exe"))
        self.assertFalse(is_system_process("python.exe", r"C:\Users\user\app.exe"))

    def test_parse_netstat_output(self):
        sample = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       900
  TCP    127.0.0.1:5040         0.0.0.0:0              LISTENING       1200
  TCP    192.168.1.50:49152     142.250.190.46:443     ESTABLISHED     3400
  TCP    [::]:445               [::]:0                 LISTENING       4
  UDP    0.0.0.0:5353           *:*                                    2048
  UDP    [::1]:54321            *:*                                    2048
"""
        proc_cache = {
            4: ("System", "", "NT AUTHORITY\\SYSTEM"),
            900: ("svchost.exe", r"C:\Windows\System32\svchost.exe", "NT AUTHORITY\\NETWORK SERVICE"),
            1200: ("custom_daemon.exe", r"C:\opt\daemon.exe", "user"),
            2048: ("mDNSResponder.exe", r"C:\Program Files\Bonjour\mDNSResponder.exe", "SYSTEM"),
        }
        ports = parse_netstat_output(sample, proc_cache)

        # 4 listening ports: TCP 135, TCP 5040, TCP 445, UDP 5353, UDP 54321 (5 total, ESTABLISHED excluded)
        self.assertEqual(len(ports), 5)

        # TCP 135 on 0.0.0.0 is public
        p135 = next(p for p in ports if p.port == 135)
        self.assertEqual(p135.proto, "TCP")
        self.assertEqual(p135.scope, "All Interfaces")
        self.assertTrue(p135.is_public)
        self.assertTrue(p135.is_system)

        # TCP 5040 on 127.0.0.1 is loopback
        p5040 = next(p for p in ports if p.port == 5040)
        self.assertEqual(p5040.scope, "Loopback")
        self.assertTrue(p5040.is_loopback)
        self.assertFalse(p5040.is_system)

        # TCP 445 on [::] is public
        p445 = next(p for p in ports if p.port == 445)
        self.assertEqual(p445.proto, "TCP")
        self.assertEqual(p445.scope, "All Interfaces")

        # UDP 5353
        p5353 = next(p for p in ports if p.port == 5353)
        self.assertEqual(p5353.proto, "UDP")
        self.assertEqual(p5353.scope, "All Interfaces")

    def test_read_listening_ports_smoke(self):
        # Must return list and problems without raising
        ports, problems = read_listening_ports()
        self.assertIsInstance(ports, list)
        self.assertIsInstance(problems, list)


if __name__ == "__main__":
    unittest.main()

