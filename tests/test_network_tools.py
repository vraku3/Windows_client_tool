"""Unit tests for network_tools.py: parsing, streaming, and error detection."""
from modules.network_diagnostics import network_tools


def test_is_error_stat():
    assert network_tools.is_error_stat("TCP segments retransmitted") is True
    assert network_tools.is_error_stat("Inbound Packets Discarded") is True
    assert network_tools.is_error_stat("Outbound Packets No Route") is True
    assert network_tools.is_error_stat("Packets Received Successfully") is False
    assert network_tools.is_error_stat("Total Bytes Sent") is False


def test_parse_tracert_line_normal():
    line = "  1    <1 ms    <1 ms    <1 ms  192.168.1.1"
    parsed = network_tools.parse_tracert_line(line)
    assert parsed == (1, "192.168.1.1", "<1 ms")


def test_parse_tracert_line_with_ms():
    line = "  3    12 ms    11 ms    14 ms  8.8.8.8"
    parsed = network_tools.parse_tracert_line(line)
    assert parsed == (3, "8.8.8.8", "12 ms")


def test_parse_tracert_line_timed_out():
    line = "  2     *        *        *     Request timed out."
    parsed = network_tools.parse_tracert_line(line)
    assert parsed is not None
    assert parsed[0] == 2
    assert parsed[2] == "*"


def test_parse_tracert_line_header_returns_none():
    assert network_tools.parse_tracert_line("Tracing route to 8.8.8.8 over a maximum of 30 hops") is None
    assert network_tools.parse_tracert_line("") is None
    assert network_tools.parse_tracert_line("Trace complete.") is None


def test_traceroute_streaming_and_cancellation(monkeypatch):
    class FakeProc:
        def __init__(self):
            self.stdout = [
                "  1    <1 ms    <1 ms    <1 ms  192.168.1.1\n",
                "  2    10 ms    10 ms    10 ms  10.0.0.1\n",
                "  3    20 ms    20 ms    20 ms  8.8.8.8\n",
            ]
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    fake_proc = FakeProc()
    monkeypatch.setattr(network_tools.subprocess, "Popen", lambda *a, **k: fake_proc)

    hops = []
    def on_hop(h, ip, t):
        hops.append((h, ip, t))

    cancelled = [False]
    res = network_tools.traceroute("8.8.8.8", on_hop=on_hop, is_cancelled=lambda: cancelled[0])
    assert len(res) == 3
    assert len(hops) == 3
    assert res[0][1] == "192.168.1.1"
    assert res[2][1] == "8.8.8.8"
    assert fake_proc.killed is False

    # Test early cancellation
    cancelled[0] = True
    fake_proc_cancel = FakeProc()
    monkeypatch.setattr(network_tools.subprocess, "Popen", lambda *a, **k: fake_proc_cancel)
    res_cancel = network_tools.traceroute("8.8.8.8", is_cancelled=lambda: cancelled[0])
    assert len(res_cancel) == 0
    assert fake_proc_cancel.killed is True
