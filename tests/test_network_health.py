"""Network health engine, DNS client, ping stats and fixes. No display, no admin."""
import struct
import subprocess
from datetime import datetime

import pytest

from modules.network_diagnostics import dns_client, network_fixes, network_health as h
from modules.network_diagnostics.ping_stats import PingStats


def adapter(idx, name, status="Up", media="802.3", speed="1 Gbps", **kw):
    return dict(Name=name, InterfaceIndex=idx, Status=status, MediaType=media, LinkSpeed=speed, **kw)


def snap(**over):
    base = {
        "errors": [], "taken": "t",
        "adapters": [adapter(1, "Ethernet")],
        "addresses": [{"InterfaceIndex": 1, "IPAddress": "192.168.1.5", "AddressFamily": 2}],
        "routes": [{"InterfaceIndex": 1, "DestinationPrefix": "0.0.0.0/0", "NextHop": "192.168.1.1",
                    "RouteMetric": 0, "InterfaceMetric": 25}],
        "dns": [{"InterfaceIndex": 1, "AddressFamily": 2, "ServerAddresses": ["192.168.1.1"]}],
        "ifaces": [], "dhcp": [], "winhttp_proxy": "", "user_proxy": {"enabled": False, "server": "", "pac": "", "auto_detect": True},
        "hosts": [],
    }
    base.update(over)
    return base


def ok_probes(**over):
    p = {"gw:192.168.1.1": {"ok": True, "rtt_ms": 1.0, "error": None, "frag_needed": False},
         "dns:192.168.1.1": dns_client.DnsReply("192.168.1.1", "x", "A", 20.0, "NOERROR", ["1.2.3.4"]),
         "ntp": {"ok": True, "offset_s": 0.1, "error": None}}
    p.update(over)
    return p


def by_id(findings):
    return {f.id: f for f in findings}


def test_healthy_machine_has_no_problems():
    f = h.evaluate(snap(), ok_probes())
    assert not [x for x in f if x.severity in (h.ERROR, h.WARNING, h.UNKNOWN)]
    assert h.summarize(f) == "No problems found"


def test_no_default_route_is_error():
    f = h.evaluate(snap(routes=[]), {})
    assert by_id(f)["gateway"].severity == h.ERROR


def test_apipa_only_counts_on_connected_adapters():
    disconnected = snap(adapters=[adapter(1, "Ethernet"), adapter(2, "Wi-Fi", status="Disconnected")],
                        addresses=[{"InterfaceIndex": 2, "IPAddress": "169.254.1.1", "AddressFamily": 2}])
    assert "apipa" not in by_id(h.evaluate(disconnected, ok_probes()))
    connected = snap(addresses=[{"InterfaceIndex": 1, "IPAddress": "169.254.1.1", "AddressFamily": 2}])
    f = by_id(h.evaluate(connected, ok_probes()))["apipa"]
    assert f.severity == h.ERROR and f.fix == "renew_dhcp"


def test_multiple_default_routes_names_the_winner():
    s = snap(adapters=[adapter(1, "Ethernet"), adapter(2, "Wi-Fi", media="Native 802.11")],
             routes=[{"InterfaceIndex": 1, "DestinationPrefix": "0.0.0.0/0", "NextHop": "10.0.0.1", "RouteMetric": 0, "InterfaceMetric": 50},
                     {"InterfaceIndex": 2, "DestinationPrefix": "0.0.0.0/0", "NextHop": "10.1.0.1", "RouteMetric": 0, "InterfaceMetric": 20}])
    f = by_id(h.evaluate(s, {}))["multi-route"]
    assert "Wi-Fi wins" in f.title and len(f.items) == 2


def test_all_dns_failing_is_error_and_partial_is_warning():
    dead = dns_client.DnsReply("192.168.1.1", "x", "A", error="no answer within 2s")
    assert by_id(h.evaluate(snap(), ok_probes(**{"dns:192.168.1.1": dead})))["dns"].severity == h.ERROR
    s = snap(dns=[{"InterfaceIndex": 1, "AddressFamily": 2, "ServerAddresses": ["192.168.1.1", "9.9.9.9"]}])
    p = ok_probes(**{"dns:192.168.1.1": dead, "dns:9.9.9.9": dns_client.DnsReply("9.9.9.9", "x", "A", 30.0, "NOERROR", ["1.1.1.1"])})
    assert by_id(h.evaluate(s, p))["dns-partial"].severity == h.WARNING


def test_nxdomain_with_no_answers_is_not_resolving():
    nx = dns_client.DnsReply("192.168.1.1", "x", "A", 5.0, "NXDOMAIN", [])
    assert by_id(h.evaluate(snap(), ok_probes(**{"dns:192.168.1.1": nx})))["dns"].severity == h.ERROR


def test_slow_dns_warns():
    slow = dns_client.DnsReply("192.168.1.1", "x", "A", 900.0, "NOERROR", ["1.2.3.4"])
    assert by_id(h.evaluate(snap(), ok_probes(**{"dns:192.168.1.1": slow})))["dns-slow"].severity == h.WARNING


def test_unreadable_proxy_and_hosts_are_unknown_not_ok():
    f = by_id(h.evaluate(snap(winhttp_proxy=None, user_proxy=None, hosts=None), ok_probes()))
    assert f["proxy-winhttp"].severity == h.UNKNOWN and f["proxy-user"].severity == h.UNKNOWN
    assert f["hosts"].severity == h.UNKNOWN
    assert "proxy" not in f  # no "No proxy" pass when we could not read it


def test_proxy_configured_warns():
    f = by_id(h.evaluate(snap(winhttp_proxy="proxy:8080"), ok_probes()))
    assert f["proxy"].severity == h.WARNING


def test_hosts_override_of_well_known_name():
    hosts = h.parse_hosts("127.0.0.1 localhost\n0.0.0.0 telemetry.microsoft.com # block\n10.1.1.1 login.microsoftonline.com login.microsoft.com\n")
    f = by_id(h.evaluate(snap(hosts=hosts), ok_probes()))
    assert f["hosts-wellknown"].severity == h.WARNING
    assert any("login.microsoft.com" in i for i in f["hosts-wellknown"].items)
    assert not any("telemetry" in i for i in f["hosts-wellknown"].items)  # a block is not a redirect
    assert f["hosts-blocks"].severity == h.INFO


def test_dhcp_lease_expiry():
    s = snap(dhcp=[{"InterfaceIndex": 1, "LeaseExpires": "2026-01-01T10:30:00"}])
    assert by_id(h.evaluate(s, ok_probes(), datetime(2026, 1, 1, 10, 0)))["dhcp:Ethernet"].severity == h.WARNING
    assert by_id(h.evaluate(s, ok_probes(), datetime(2026, 1, 1, 12, 0)))["dhcp:Ethernet"].severity == h.ERROR
    assert "dhcp:Ethernet" not in by_id(h.evaluate(s, ok_probes(), datetime(2026, 1, 1, 6, 0)))


def test_link_speed_and_duplex():
    s = snap(adapters=[adapter(1, "Ethernet", speed="100 Mbps", FullDuplex=False)])
    ids = by_id(h.evaluate(s, ok_probes()))
    assert ids["speed:Ethernet"].severity == h.WARNING and ids["duplex:Ethernet"].severity == h.WARNING
    assert "speed:Ethernet" not in by_id(h.evaluate(snap(adapters=[adapter(1, "Ethernet", speed="2.5 Gbps")]), ok_probes()))


def test_mtu_explained_by_adapter_is_info_otherwise_warning():
    p = ok_probes(mtu={"ok": False, "frag_needed": True, "mtu": 1400, "error": None})
    s = snap(ifaces=[{"InterfaceIndex": 1, "AddressFamily": 2, "NlMtu": 1400}])
    assert by_id(h.evaluate(s, p))["mtu"].severity == h.INFO
    assert by_id(h.evaluate(snap(), p))["mtu"].severity == h.WARNING
    silent = ok_probes(mtu={"ok": False, "frag_needed": False, "mtu": None, "error": None})
    assert by_id(h.evaluate(snap(), silent))["mtu"].severity == h.UNKNOWN


def test_clock_skew_and_unmeasurable():
    assert by_id(h.evaluate(snap(), ok_probes(ntp={"ok": True, "offset_s": 400, "error": None})))["time"].severity == h.ERROR
    assert by_id(h.evaluate(snap(), ok_probes(ntp={"ok": True, "offset_s": -30, "error": None})))["time"].severity == h.WARNING
    assert by_id(h.evaluate(snap(), ok_probes(ntp={"ok": False, "offset_s": None, "error": "timeout"})))["time"].severity == h.UNKNOWN


def test_unreadable_config_is_reported():
    f = h.evaluate(snap(errors=["boom"], adapters=[], routes=[], dns=[]), {})
    assert any(x.severity == h.UNKNOWN and "boom" in x.detail for x in f)


def test_snapshot_json_collapses_single_items():
    s = h.parse_snapshot_json('{"adapters":{"Name":"a"},"routes":null,"dns":[{"InterfaceIndex":1,"ServerAddresses":"1.1.1.1"}]}')
    assert s["adapters"] == [{"Name": "a"}] and s["routes"] == [] and s["dns"][0]["ServerAddresses"] == ["1.1.1.1"]


def test_report_contains_findings_and_dns():
    p = ok_probes()
    text = h.build_report(snap(), p, h.evaluate(snap(), p), host="PC")
    assert "Network diagnostics report - PC" in text and "192.168.1.1" in text and "20 ms" in text


# ---- DNS client --------------------------------------------------------
def _reply_packet(txid, rcode, records, tc=False):
    flags = 0x8180 | rcode | (0x0200 if tc else 0)
    pkt = struct.pack(">HHHHHH", txid, flags, 1, len(records), 0, 0)
    pkt += b"\x03foo\x03com\x00" + struct.pack(">HH", 1, 1)
    for rtype, rdata in records:
        pkt += b"\xc0\x0c" + struct.pack(">HHIH", rtype, 1, 60, len(rdata)) + rdata
    return pkt


def test_parse_a_and_cname_and_mx_with_compression():
    pkt = _reply_packet(7, 0, [(1, bytes([1, 2, 3, 4])), (5, b"\x03bar\xc0\x10")])
    txid, rcode, recs, tc = dns_client.parse_reply(pkt)
    assert txid == 7 and rcode == "NOERROR" and not tc
    assert recs[0] == (1, "1.2.3.4") and recs[1][0] == 5 and recs[1][1].startswith("bar")


def test_parse_txt_and_nxdomain_and_truncation():
    _t, rc, recs, _tc = dns_client.parse_reply(_reply_packet(1, 0, [(16, b"\x03abc\x02de")]))
    assert recs == [(16, "abcde")]
    assert dns_client.parse_reply(_reply_packet(1, 3, []))[1] == "NXDOMAIN"
    assert dns_client.parse_reply(_reply_packet(1, 0, [], tc=True))[3] is True


def test_malformed_packets_raise_valueerror():
    with pytest.raises(ValueError):
        dns_client.parse_reply(b"\x00\x01")
    loop = struct.pack(">HHHHHH", 1, 0x8180, 0, 1, 0, 0) + b"\xc0\x0c" + b"\x00" * 10
    with pytest.raises(ValueError):
        dns_client.parse_reply(loop)


def test_query_reports_timeout_as_error_not_empty_answer(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("x")
    import socket
    monkeypatch.setattr(dns_client, "_udp_exchange", lambda *a, **k: (_ for _ in ()).throw(socket.timeout()))
    r = dns_client.query("192.0.2.1", "example.com")
    assert not r.ok and "no answer" in r.error and r.rtt_ms is None


def test_compare_verdicts():
    a = dns_client.DnsReply("a", "x", "A", 1, "NOERROR", ["1.1.1.1", "2.2.2.2"])
    b = dns_client.DnsReply("b", "x", "A", 1, "NOERROR", ["2.2.2.2", "1.1.1.1"])
    assert "agree" in dns_client.compare([a, b])
    c = dns_client.DnsReply("c", "x", "A", 1, "NXDOMAIN", [])
    assert "disagree" in dns_client.compare([a, c])
    d = dns_client.DnsReply("d", "x", "A", error="timeout")
    assert "no usable answer" in dns_client.compare([a, d])


def test_bad_label_rejected():
    r = dns_client.query("127.0.0.1", "a" * 70 + ".com")
    assert r.error and r.rtt_ms is None


def test_real_resolver_round_trip_if_online():
    """Real-machine sanity: a public resolver answers within a plausible time, or we skip."""
    r = dns_client.query("1.1.1.1", "microsoft.com", "A", 3.0)
    if not r.ok:
        pytest.skip(f"offline: {r.error}")
    assert r.resolved and 0 < r.rtt_ms < 3000


# ---- ping stats ---------------------------------------------------------
def test_ping_stats_loss_jitter_and_runs():
    s = PingStats()
    for v in (10.0, 20.0, None, None, 10.0):
        s.add(v)
    assert s.sent == 5 and s.received == 3 and s.loss_pct == pytest.approx(40.0)
    assert (s.min, s.max) == (10.0, 20.0) and s.avg == pytest.approx(40 / 3)
    assert s.jitter == pytest.approx(10.0)  # 10->20 counted; loss resets the chain
    assert s.longest_loss_run == 2 and list(s.history)[2] is None


def test_ping_stats_empty_and_all_lost():
    s = PingStats()
    assert s.summary() == "No probes yet" and s.avg is None and s.loss_pct is None
    s.add(None)
    assert s.loss_pct == 100.0 and s.avg is None and "-" in s.summary()


# ---- fixes ---------------------------------------------------------------
def cp(out="", rc=0):
    return subprocess.CompletedProcess([], rc, out, "")


def test_flush_dns_verified_by_readback():
    counts = iter([50, 0])
    r = network_fixes.flush_dns(run=lambda c, *a: cp("ok"), count=lambda: next(counts))
    assert r.ok and r.verified is True


def test_flush_dns_accepted_but_cache_unchanged_is_not_success():
    counts = iter([50, 50])
    r = network_fixes.flush_dns(run=lambda c, *a: cp("Successfully flushed"), count=lambda: next(counts))
    assert not r.ok and r.verified is False


def test_flush_dns_refusal_and_unreadable_cache():
    assert not network_fixes.flush_dns(run=lambda c, *a: cp("The requested operation requires elevation."), count=lambda: 1).ok
    r = network_fixes.flush_dns(run=lambda c, *a: cp("ok"), count=lambda: None)
    assert r.ok and r.verified is None


def test_renew_dhcp_detects_refusal_and_unchanged_lease():
    s = snap(dhcp=[{"InterfaceIndex": 1, "LeaseObtained": "A"}])
    assert not network_fixes.renew_dhcp(run=lambda c, *a: cp("requires elevation"), snapshot=lambda: s).ok
    same = network_fixes.renew_dhcp(run=lambda c, *a: cp("done"), snapshot=lambda: s)
    assert not same.ok and same.verified is False
    snaps = iter([s, snap(dhcp=[{"InterfaceIndex": 1, "LeaseObtained": "B"}])])
    assert network_fixes.renew_dhcp(run=lambda c, *a: cp("done"), snapshot=lambda: next(snaps)).verified is True


def test_winsock_reset_never_claims_verification():
    r = network_fixes.reset_winsock(run=lambda c, *a: cp("Successfully reset the Winsock Catalog. You must restart the computer"))
    assert r.ok and r.reboot_needed and r.verified is None
    assert not network_fixes.reset_winsock(run=lambda c, *a: cp("Access is denied", 1)).ok


def test_every_fix_has_a_runner_and_destructive_ones_say_so():
    assert set(network_fixes.FIXES) == set(network_fixes.RUNNERS)
    assert network_fixes.FIXES["reset_winsock"].reboot and "REBOOT" in network_fixes.FIXES["reset_winsock"].confirm_text


# ---- real machine (read-only) ----------------------------------------------
def test_real_snapshot_is_plausible():
    s = h.collect_snapshot()
    if s["errors"]:
        pytest.skip(s["errors"][0])
    assert s["adapters"] and s["addresses"]
    assert all("InterfaceIndex" in a for a in s["adapters"])
    f = h.evaluate(s, {})  # no probes: must not raise and must not invent verdicts
    assert all(x.severity in h.SEVERITY_ORDER for x in f)
