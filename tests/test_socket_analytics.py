"""Unit tests for Active TCP Sockets & Ephemeral Port Exhaustion Analytics."""
import unittest

from modules.network_diagnostics.socket_analytics import (
    EphemeralPortRange,
    ProcessSocketSummary,
    SocketAnalyticsSnapshot,
    SocketConnection,
    get_ephemeral_port_range,
    parse_netsh_dynamic_ports,
    parse_netstat_connections,
    read_socket_analytics,
)


class TestSocketAnalytics(unittest.TestCase):
    def test_parse_netsh_dynamic_ports(self):
        sample = """
Protocol tcp Dynamic Port Range
---------------------------------
Start Port      : 49152
Number of Ports : 16384
"""
        start, count = parse_netsh_dynamic_ports(sample)
        self.assertEqual(start, 49152)
        self.assertEqual(count, 16384)

        custom = """
Start Port : 1024
Number of Ports : 64511
"""
        start, count = parse_netsh_dynamic_ports(custom)
        self.assertEqual(start, 1024)
        self.assertEqual(count, 64511)

    def test_parse_netstat_connections(self):
        sample = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    192.168.1.50:49200     142.250.190.46:443     ESTABLISHED     1234
  TCP    127.0.0.1:50000        127.0.0.1:50001        CLOSE_WAIT      5678
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       900
  UDP    0.0.0.0:5353           *:*                                    2048
"""
        proc_cache = {
            1234: ("chrome.exe", "", ""),
            5678: ("app_with_leak.exe", "", ""),
            900: ("svchost.exe", "", ""),
        }
        conns = parse_netstat_connections(sample, proc_cache, start_port=49152, end_port=65535)
        # Should parse the 3 TCP connections (UDP ignored)
        self.assertEqual(len(conns), 3)

        est = next(c for c in conns if c.state == "ESTABLISHED")
        self.assertEqual(est.process_name, "chrome.exe")
        self.assertEqual(est.local_port, 49200)
        self.assertTrue(est.is_ephemeral)
        self.assertFalse(est.is_leak_suspect)
        self.assertEqual(est.service_name, "HTTPS")

        leak = next(c for c in conns if c.state == "CLOSE_WAIT")
        self.assertEqual(leak.process_name, "app_with_leak.exe")
        self.assertTrue(leak.is_leak_suspect)
        self.assertTrue(leak.is_ephemeral)

    def test_ephemeral_port_range_calculation(self):
        ephem = EphemeralPortRange(
            start_port=49152,
            num_ports=16384,
            in_use=8192,
            percent_used=50.0,
            status="Normal",
        )
        self.assertEqual(ephem.end_port, 65535)
        self.assertEqual(ephem.percent_used, 50.0)

    def test_read_socket_analytics_live(self):
        snap = read_socket_analytics()
        self.assertIsInstance(snap, SocketAnalyticsSnapshot)
        self.assertGreater(snap.ephemeral.num_ports, 0)
        self.assertGreaterEqual(snap.ephemeral.in_use, 0)
        self.assertIsInstance(snap.state_counts, dict)
        self.assertIsInstance(snap.connections, list)
        self.assertIsInstance(snap.process_summaries, list)
