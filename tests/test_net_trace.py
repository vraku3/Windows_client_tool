"""Per-process network counting. The trace itself needs elevation; the counting,
the refusal and the cleanup do not."""
import pytest

from core.admin_utils import is_admin
from modules.dashboard import net_trace as nt


def test_send_and_receive_events_are_counted_per_pid():
    totals = {}
    assert nt.apply_event(totals, (10, {"PID": "100", "size": "500"}))       # TCPv4 send
    assert nt.apply_event(totals, (11, {"PID": "100", "size": "1500"}))      # TCPv4 receive
    assert nt.apply_event(totals, (43, {"PID": "200", "size": "35"}))        # UDPv4 receive
    assert nt.apply_event(totals, (58, {"PID": "200", "size": "10"}))        # UDPv6 send
    assert totals == {100: [500, 1500], 200: [10, 35]}


def test_other_events_and_malformed_ones_are_ignored_not_crashed_on():
    totals = {}
    assert not nt.apply_event(totals, (18, {"PID": "1", "size": "1"}))       # not a data event
    assert not nt.apply_event(totals, (10, {"size": "5"}))                   # no PID
    assert not nt.apply_event(totals, (10, {"PID": "x", "size": "5"}))       # not a number
    assert not nt.apply_event(totals, None)
    assert totals == {}


def test_rates_are_deltas_per_second_and_the_first_sample_is_empty(monkeypatch):
    trace = nt.NetworkTrace()
    clock = iter([100.0, 101.0, 103.0, 104.0])
    monkeypatch.setattr(nt.time, "monotonic", lambda: next(clock))
    assert trace.sample_rates() == {}                       # no earlier sample in time
    nt.apply_event(trace._totals, (10, {"PID": "7", "size": "1000"}))
    nt.apply_event(trace._totals, (11, {"PID": "7", "size": "3000"}))
    up, down = trace.sample_rates()[7]                      # 1 s later
    assert up == pytest.approx(1000) and down == pytest.approx(3000)
    nt.apply_event(trace._totals, (11, {"PID": "7", "size": "2000"}))
    assert trace.sample_rates()[7][1] == pytest.approx(1000)  # 2000 B over 2 s
    assert trace.total_of(7) == (1000, 5000) and trace.total_of(999) == (0, 0)


def test_unelevated_start_is_refused_with_the_reason_and_starts_nothing(monkeypatch):
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: False)
    trace = nt.NetworkTrace()
    ok, message = trace.start()
    assert ok is False and "administrator" in message and not trace.running


@pytest.mark.skipif(not is_admin(), reason="a kernel trace session needs an elevated process")
def test_a_real_trace_sees_this_process_traffic_and_cleans_up():
    """Loopback is not reported by the kernel network provider, so this makes a
    real outbound connection."""
    import os
    import socket
    import time
    trace = nt.NetworkTrace()
    ok, message = trace.start()
    assert ok, message
    try:
        try:
            client = socket.create_connection(("example.com", 80), timeout=8)
        except OSError as e:
            pytest.skip(f"no outbound network for the live check: {e}")
        client.sendall(b"GET / HTTP/1.0" + bytes([13, 10]) + b"Host: example.com" + bytes([13, 10, 13, 10]))
        client.settimeout(8)
        got = b""
        try:
            while len(got) < 500:
                chunk = client.recv(4096)
                if not chunk:
                    break
                got += chunk
        finally:
            client.close()
        time.sleep(5.0)           # ETW delivers in buffers: measured ~2 s behind the traffic
        sent, recv = trace.total_of(os.getpid())
        assert trace.events_seen > 0, "the trace saw no network events at all"
        assert sent > 0 and recv > 0, (sent, recv)
        trace.sample_rates()
    finally:
        trace.stop()
    assert not trace.running
