"""Unit tests for DNS Benchmark engine and UI."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.network_diagnostics.dns_benchmark import (
    BenchmarkResult,
    get_system_dns_servers,
    run_dns_benchmark,
)
from modules.network_diagnostics.dns_client import DnsReply


class TestDnsBenchmark(unittest.TestCase):
    """Test DNS benchmarking engine."""

    @patch("subprocess.run")
    def test_get_system_dns_servers_parses_netsh(self, mock_subproc):
        netsh_output = """
Configuration for interface "Ethernet 2"
    DNS servers configured through DHCP:  1.1.1.1
                                          1.0.0.1
    Register with which suffix:           Primary only
"""
        mock_subproc.return_value = MagicMock(returncode=0, stdout=netsh_output)
        servers = get_system_dns_servers()
        self.assertEqual(servers, ["1.1.1.1", "1.0.0.1"])

    @patch("modules.network_diagnostics.dns_benchmark.get_system_dns_servers")
    @patch("modules.network_diagnostics.dns_client.query")
    def test_run_dns_benchmark_orders_fastest_first(self, mock_query, mock_sys_dns):
        mock_sys_dns.return_value = ["1.1.1.1"]

        def fake_query(server, domain, qtype="A", timeout=2.0):
            if server == "1.1.1.1":
                return DnsReply(server=server, name=domain, qtype=qtype, rtt_ms=5.0, rcode="NOERROR", answers=["93.184.216.34"])
            elif server == "8.8.8.8":
                return DnsReply(server=server, name=domain, qtype=qtype, rtt_ms=25.0, rcode="NOERROR", answers=["93.184.216.34"])
            return DnsReply(server=server, name=domain, qtype=qtype, error="timeout")

        mock_query.side_effect = fake_query

        custom = [("Google", "8.8.8.8"), ("Broken", "192.0.2.1")]
        results = run_dns_benchmark(custom_servers=custom, domains=["example.com"])

        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].is_fastest)
        self.assertEqual(results[0].server, "1.1.1.1")
        self.assertEqual(results[0].avg_rtt_ms, 5.0)

        self.assertEqual(results[1].server, "8.8.8.8")
        self.assertEqual(results[1].avg_rtt_ms, 25.0)

        self.assertIsNone(results[2].avg_rtt_ms)
        self.assertEqual(results[2].samples_ok, 0)

    def test_dns_benchmark_pane_ui(self):
        from PyQt6.QtWidgets import QApplication
        from modules.network_diagnostics.network_health_module import _DnsBenchmarkPane

        app = QApplication.instance() or QApplication([])
        pane = _DnsBenchmarkPane(MagicMock())
        self.assertIsNotNone(pane._table)

        results = [
            BenchmarkResult(
                name="Test Fast",
                server="1.1.1.1",
                samples_sent=2,
                samples_ok=2,
                rtts=[4.0, 6.0],
                avg_rtt_ms=5.0,
                min_rtt_ms=4.0,
                max_rtt_ms=6.0,
                is_fastest=True,
                sample_answer="test -> 1.2.3.4",
            )
        ]
        pane._on_done(results)
        self.assertEqual(pane._table.rowCount(), 1)
        self.assertIn("1.1.1.1", pane._table.item(0, 2).text())
        self.assertIn("5.0 ms", pane._table.item(0, 3).text())


if __name__ == "__main__":
    unittest.main()
