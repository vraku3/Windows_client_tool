"""Wi-Fi Analyzer: netsh parsing, refusal classification, 2.4 GHz overlap,
and the WLAN AutoConfig connection history.

The NETSH_NETWORKS / NETSH_INTERFACES fixtures are this machine's real
output (MediaTek RZ717, 2026-10-09), trimmed; the event XML is the real
shape of 8000/8002/8003 from Microsoft-Windows-WLAN-AutoConfig/Operational.
"""
import shutil
import sys
from datetime import datetime, timezone

import pytest

from modules.wifi_analyzer import wifi_scan, wlan_events

NETSH_NETWORKS = """
Interface name : Wi-Fi
There are 3 networks currently visible.

SSID 1 : vraku
    Network type            : Infrastructure
    Authentication          : WPA3-Personal
    Encryption              : CCMP
    BSSID 1                 : 30:68:93:21:23:64
         Signal             : 63%
         Radio type         : 802.11ax
         Band               : 2.4 GHz
         Channel            : 10
         Details            :  (H2E supported)
         Basic rates (Mbps) : 1 2 5.5 11
    BSSID 2                 : 30:68:93:21:23:65
         Signal             : 85%
         Radio type         : 802.11be
         Band               : 5 GHz
         Channel            : 44
         Details            :  (H2E supported)  (MLD)

SSID 2 : The Smurfs
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : 64:09:ac:07:a6:e8
         Signal             : 83%
         Radio type         : 802.11n
         Band               : 2.4 GHz
         Channel            : 9

SSID 3 :
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : ae:15:a2:1f:30:8d
         Signal             : 83%
         Radio type         : 802.11n
         Band               : 2.4 GHz
         Channel            : 3
         Bss Load:
             Connected Stations:         0
             Channel Utilization:        8 (3 %)
             Medium Available Capacity:  31250 (1000000 us/s)
"""

NETSH_INTERFACES = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : RZ717 WiFi 7 160MHz
    GUID                   : 6f1f6159-a465-437b-9c00-fce2513d1d37
    Physical address       : c8:a3:e8:4e:14:25
    Interface type         : Primary
    State                  : disconnected
    Radio status           : Hardware On
                             Software Off

"""


# ── networks ────────────────────────────────────────────────────────────────

def test_real_output_parses_every_bssid_with_band_radio_and_load():
    nets = wifi_scan.parse_networks_text(NETSH_NETWORKS)
    assert len(nets) == 4
    by_bssid = {n["BSSID"]: n for n in nets}
    assert by_bssid["30:68:93:21:23:65"]["Band"] == "5 GHz"
    assert by_bssid["30:68:93:21:23:65"]["Radio"] == "802.11be"
    assert by_bssid["30:68:93:21:23:64"]["Security"] == "CCMP"
    hidden = by_bssid["ae:15:a2:1f:30:8d"]
    assert hidden["SSID"] == wifi_scan.HIDDEN_SSID
    assert hidden["Utilization %"] == 3
    assert hidden["Stations"] == 0
    assert "Utilization %" not in by_bssid["64:09:ac:07:a6:e8"]
    assert [n["Signal %"] for n in nets] == sorted((n["Signal %"] for n in nets), reverse=True)


def test_a_6ghz_channel_keeps_netshs_band_not_the_channel_guess():
    text = ("SSID 1 : six\n    Authentication : WPA3-Personal\n"
            "    BSSID 1 : aa:bb:cc:dd:ee:ff\n         Signal : 50%\n"
            "         Band : 6 GHz\n         Channel : 5\n")
    (net,) = wifi_scan.parse_networks_text(text)
    assert net["Band"] == "6 GHz"          # channel 5 alone would say 2.4 GHz


def test_band_falls_back_to_channel_only_when_netsh_gives_none():
    text = "SSID 1 : old\n    BSSID 1 : aa\n         Signal : 50%\n         Channel : 36\n"
    (net,) = wifi_scan.parse_networks_text(text)
    assert net["Band"] == "5 GHz"
    assert wifi_scan.channel_to_band("1") == "2.4 GHz"
    assert wifi_scan.channel_to_band("233") == ""     # a 6 GHz number: unknown
    assert wifi_scan.channel_to_band("x") == ""


# ── refusals are not empty neighbourhoods ───────────────────────────────────

@pytest.mark.parametrize("text, kind", [
    ("Network shell commands need location permission to access WLAN information. "
     "Turn on Location services on the Location page in Privacy & security settings.\n\n"
     "start ms-settings:privacy-location\n\n"
     "Function WlanGetNetworkBssList returns error 5:\n"
     "The requested operation requires elevation (Run as administrator).", "location"),
    ("There is no wireless interface on the system.", "no_adapter"),
    ("There is no such wireless interface on the system.", "no_adapter"),
    ("The Wireless AutoConfig Service (wlansvc) is not running.", "service"),
    ("The wireless local area network interface is powered down and doesn't "
     "support the requested operation.", "radio_off"),
    ("Access is denied.", "denied"),
    ("Something else entirely.", "unrecognised"),
    ("", "no_output"),
])
def test_an_empty_scan_with_a_reason_is_a_problem(text, kind):
    problem = wifi_scan.classify_scan_output(text, 0)
    assert problem is not None and problem.kind == kind
    assert problem.message


def test_netsh_saying_zero_visible_is_a_real_empty_answer():
    text = "Interface name : Wi-Fi\nThere are 0 networks currently visible.\n"
    assert wifi_scan.classify_scan_output(text, 0) is None


def test_parsed_networks_are_trusted_whatever_else_netsh_printed():
    assert wifi_scan.classify_scan_output("Access is denied.", 3) is None


# ── interfaces ──────────────────────────────────────────────────────────────

def test_interface_continuation_line_is_kept_and_header_skipped():
    (iface,) = wifi_scan.parse_interfaces_text(NETSH_INTERFACES)
    assert "There is 1 interface on the system" not in iface
    assert iface["Physical address"] == "c8:a3:e8:4e:14:25"
    assert iface["Radio status"] == "Hardware On, Software Off"
    assert wifi_scan.radio_problem(iface) == "Software Off"


def test_two_interfaces_split_on_name():
    text = ("    Name : A\n    State : connected\n    SSID : home\n"
            "    Name : B\n    State : disconnected\n")
    ifaces = wifi_scan.parse_interfaces_text(text)
    assert [i["Name"] for i in ifaces] == ["A", "B"]
    assert wifi_scan.connected_ssids(ifaces) == ("home",)
    assert wifi_scan.radio_problem(ifaces[0]) is None


# ── 2.4 GHz overlap ─────────────────────────────────────────────────────────

def test_overlap_follows_the_1_6_11_rule():
    assert wifi_scan.channels_overlap_24(1, 5)
    assert not wifi_scan.channels_overlap_24(1, 6)
    assert wifi_scan.channels_overlap_24(10, 10)
    assert wifi_scan.channels_overlap_24(14, 12)
    assert not wifi_scan.channels_overlap_24(14, 11)
    assert not wifi_scan.channels_overlap_24(36, 40)   # not 2.4 GHz at all


def test_overlap_report_counts_neighbouring_channels():
    nets = wifi_scan.parse_networks_text(NETSH_NETWORKS)
    report = wifi_scan.overlap_report_24ghz(nets)
    assert set(report) == {3, 9, 10}
    assert [n["SSID"] for n in report[10].same] == ["vraku"]
    assert [n["SSID"] for n in report[10].adjacent] == ["The Smurfs"]
    assert report[3].adjacent == []


def test_recommendation_weights_by_signal_and_ignores_your_own_network():
    nets = [
        {"SSID": "loud", "Band": "2.4 GHz", "Channel": "1", "Signal %": 95},
        {"SSID": "quiet", "Band": "2.4 GHz", "Channel": "11", "Signal %": 10},
        {"SSID": "mine", "Band": "2.4 GHz", "Channel": "6", "Signal %": 99},
        {"SSID": "five", "Band": "5 GHz", "Channel": "36", "Signal %": 99},
    ]
    advice = wifi_scan.recommend_24ghz_channel(nets, exclude_ssids=("mine",))
    assert advice.channel == 6 and advice.overlapping == 0
    assert advice.ranking[-1][0] == 1
    assert wifi_scan.recommend_24ghz_channel(nets[3:]) is None


def test_channel_map_has_all_three_bands():
    cmap = wifi_scan.build_channel_map(wifi_scan.parse_networks_text(NETSH_NETWORKS))
    assert cmap["2.4 GHz"] == {10: 1, 9: 1, 3: 1}
    assert cmap["5 GHz"] == {44: 1}
    assert cmap["6 GHz"] == {}


# ── connection history ──────────────────────────────────────────────────────

def _event(eid, when, **data):
    fields = "".join(f"<Data Name='{k}'>{v}</Data>" for k, v in data.items())
    return (f"<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
            f"<System><EventID>{eid}</EventID><TimeCreated SystemTime='{when}'/></System>"
            f"<EventData>{fields}</EventData></Event>")


POLICY = "The network is disconnected due to a policy disabling auto connect on this interface."
INTERNAL = "An internal failure prevented the operation from completing."

EVENTS_XML = "".join([
    _event(8003, "2026-10-09T05:48:22.5793460Z", SSID="Smurfs", Reason=POLICY,
           ConnectionId="0x1", ReasonCode="5"),
    _event(8001, "2026-10-09T05:48:21.9741620Z", SSID="Smurfs", ConnectionId="0x1",
           PHYType="802.11n"),
    _event(8002, "2026-10-06T09:41:12.8186714Z", SSID="Smurfs", FailureReason=INTERNAL,
           ReasonCode="229392", ConnectionId="0x1", RSSI="255"),
    _event(8000, "2026-10-06T09:41:12.7931748Z", SSID="Smurfs", ConnectionId="0x1"),
    _event(8002, "2026-10-05T16:07:41.0199590Z", SSID="Smurfs", FailureReason=INTERNAL,
           ReasonCode="229392", ConnectionId="0x1", RSSI="255"),
    _event(8000, "2026-10-05T16:07:41.0117334Z", SSID="Smurfs", ConnectionId="0x1"),
    _event(8003, "2026-10-03T21:18:28.7334190Z", SSID="R&amp;D", Reason="The network is "
           "disconnected by the user.", ConnectionId="0x2", ReasonCode="2"),
    _event(11004, "2026-10-03T21:18:28.0000000Z", SSID="noise"),
])


def test_events_parse_with_seven_digit_fractions_and_entities():
    events = wlan_events.parse_events_xml(EVENTS_XML)
    assert len(events) == 7                    # 11004 is chatter, dropped
    assert events[0].when == datetime(2026, 10, 9, 5, 48, 22, 579346, tzinfo=timezone.utc)
    assert events[-1].ssid == "R&D"


def test_grouping_collapses_repeats_and_spots_local_aborts():
    groups = wlan_events.group_problems(wlan_events.parse_events_xml(EVENTS_XML))
    failed = next(g for g in groups if g.kind == "Connection failed")
    assert failed.count == 2 and failed.quick_aborts == 2
    assert failed.median_ms == 25
    assert "before reaching the access point" in failed.meaning
    assert failed.reason_codes == ["229392"]
    policy = next(g for g in groups if g.reason == POLICY)
    assert policy.meaning.startswith("By design")
    assert groups[0] is failed                 # most frequent first


def test_a_slow_failure_with_a_signal_is_not_called_a_local_abort():
    xml = (_event(8000, "2026-10-05T16:00:00.0000000Z", SSID="x", ConnectionId="0x9")
           + _event(8002, "2026-10-05T16:00:09.0000000Z", SSID="x", ConnectionId="0x9",
                    FailureReason="The driver disconnected while associating.", RSSI="-70"))
    (group,) = wlan_events.group_problems(wlan_events.parse_events_xml(xml))
    assert group.quick_aborts == 0 and group.median_ms == 9000
    assert group.meaning == ""                 # unknown cause: Windows' words only


def test_summary_and_headline():
    events = wlan_events.parse_events_xml(EVENTS_XML)
    (smurfs, rnd) = wlan_events.summarise_by_ssid(events)
    assert (smurfs.connected, smurfs.failed, smurfs.disconnected) == (1, 2, 1)
    assert smurfs.unexplained_disconnects == 0
    assert rnd.unexplained_disconnects == 1
    assert wlan_events.failure_rate(smurfs) == pytest.approx(2 / 3)
    assert wlan_events.failure_rate(rnd) is None
    line = wlan_events.headline(events)
    assert "1 connected, 2 failed, 2 disconnected" in line
    assert "1 of the disconnects by design" in line
    assert "No Wi-Fi" in wlan_events.headline([])


@pytest.mark.parametrize("rc, out, err, expect", [
    (0, "<Event/>", "", None),
    (5, "", "Access is denied.\r\n\r\nFailed to open event query.", "access denied"),
    (15007, "", "The specified channel could not be found.", "does not exist"),
    (1, "", "", "exit 1"),
])
def test_a_wevtutil_refusal_is_never_an_empty_history(rc, out, err, expect):
    verdict = wlan_events.classify_wevtutil(rc, out, err)
    if expect is None:
        assert verdict is None
    else:
        assert expect in verdict


def test_output_that_is_not_utf8_still_decodes():
    assert "caf" in wlan_events.decode_output("café".encode("cp1252"))
    assert wlan_events.decode_output("ok".encode()) == "ok"


# ── the real machine ────────────────────────────────────────────────────────

windows_only = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("netsh"),
                                  reason="needs Windows netsh")


@windows_only
def test_real_scan_is_either_networks_or_a_stated_reason():
    result = wifi_scan.scan()
    if not result.networks:
        # Empty is only acceptable as netsh's own "0 visible", else a reason.
        assert result.problem is None or result.problem.message
    for net in result.networks:
        assert net["Band"] in wifi_scan.BANDS + ("",)
        assert 0 <= net["Signal %"] <= 100


@windows_only
def test_real_wlan_history_reads_unelevated_or_says_why():
    history = wlan_events.read_history(max_events=200)
    if not history.readable:
        assert history.error
        return
    for ev in history.events:
        assert ev.event_id in wlan_events.KIND_BY_ID
        assert ev.when.tzinfo is not None
    groups = wlan_events.group_problems(history.events)
    assert sum(g.count for g in groups) == sum(
        1 for ev in history.events
        if ev.event_id in (wlan_events.EVENT_FAILED, wlan_events.EVENT_DISCONNECTED))


# ── the pane renders what the engine says ───────────────────────────────────

def _module():
    from modules.wifi_analyzer.wifi_module import WifiAnalyzerModule
    module = WifiAnalyzerModule()
    module.create_widget()
    return module


def test_a_refused_scan_shows_the_reason_not_zero_networks():
    module = _module()
    problem = wifi_scan.classify_scan_output("There is no wireless interface on the system.", 0)
    scan = wifi_scan.ScanResult([], [], problem, problem)
    module._on_result({"scan": scan, "history": wlan_events.HistoryResult([], "denied")})
    assert module._banner.isVisibleTo(module._widget)
    assert "No Wi-Fi adapter" in module._banner.text()
    assert module._status_lbl.objectName() == "statusError"
    assert "denied" in module._hist_headline.text()
    assert module._hist_headline.objectName() == "statusError"


def test_a_good_scan_fills_tables_and_history():
    module = _module()
    nets = wifi_scan.parse_networks_text(NETSH_NETWORKS)
    ifaces = wifi_scan.parse_interfaces_text(NETSH_INTERFACES)
    scan = wifi_scan.ScanResult(nets, ifaces, None, None)
    history = wlan_events.HistoryResult(wlan_events.parse_events_xml(EVENTS_XML))
    module._on_result({"scan": scan, "history": history})
    assert module._net_table.rowCount() == 4
    assert not module._banner.isVisibleTo(module._widget)
    assert "Software Off" in module._iface_note.text()
    assert module._hist_table.rowCount() == 3
    assert "before reaching the access point" in module._hist_detail.text()
    module._show_history_detail(2)
    assert "chose Disconnect" in module._hist_detail.text()
    assert "channel" in module._advice_lbl.text()
