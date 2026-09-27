"""Network-adapter/driver System log events (Get-WinEvent). No Qt."""
import subprocess

from modules.network_diagnostics import network_events as ne

_SAMPLE = ('[{"Id":4207,"Provider":"Tcpip","Level":"Error","Time":"2026-09-25 08:56:49",'
          '"Message":"The IPv6 TCP/IP interface with index 10 failed to bind to its provider."},'
          '{"Id":10002,"Provider":"Microsoft-Windows-WLAN-AutoConfig","Level":"Warning",'
          '"Time":"2026-09-27 20:00:00","Message":"WLAN Extensibility Module has stopped.\\n\\n'
          'Module Path: C:\\\\WINDOWS\\\\System32\\\\DriverStore\\\\FileRepository\\\\'
          'mtkwecx.inf_amd64_872157d8484ca516\\\\mtkihvx.dll"}]')


def test_read_network_events_returns_none_on_refusal(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ne.read_network_events() is None


def test_read_network_events_returns_none_when_the_command_cannot_run(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("powershell not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ne.read_network_events() is None


def test_read_network_events_parses_the_real_captured_sample(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, _SAMPLE, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    events = ne.read_network_events()

    assert events is not None and len(events) == 2
    bind_fail = next(e for e in events if e.event_id == 4207)
    assert bind_fail.index == "10" and bind_fail.is_error
    assert "bind" in bind_fail.meaning

    wlan_crash = next(e for e in events if e.event_id == 10002)
    assert wlan_crash.index is None and not wlan_crash.is_error
    assert "driver" in wlan_crash.meaning.lower()


def test_empty_result_is_a_healthy_empty_list_not_none(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ne.read_network_events() == []


def test_group_events_collapses_repeats_and_keeps_the_latest_time():
    events = [
        ne.NetworkEvent("Tcpip", 4207, "Error", "2026-09-20 08:00:00", "10", "m", "bind failure"),
        ne.NetworkEvent("Tcpip", 4207, "Error", "2026-09-27 20:28:43", "10", "m", "bind failure"),
        ne.NetworkEvent("Microsoft-Windows-WLAN-AutoConfig", 10002, "Warning", "2026-09-25 02:23:12", None, "m", "module stopped"),
    ]
    groups = ne.group_events(events)

    assert len(groups) == 2
    # Errors sort first, then by descending count.
    assert groups[0].event_id == 4207 and groups[0].is_error
    tcpip_group = next(g for g in groups if g.event_id == 4207)
    assert tcpip_group.count == 2 and tcpip_group.latest == "2026-09-27 20:28:43"


def test_index_not_extracted_when_message_has_none():
    events = [ne.NetworkEvent("Microsoft-Windows-WLAN-AutoConfig", 4003, "Warning", "t", None, "m", "recovery")]
    groups = ne.group_events(events)
    assert groups[0].index is None


def test_real_network_events_are_readable_on_this_machine():
    events = ne.read_network_events()
    assert events is not None
    # Confirmed live 2026-09-27: this machine has real Tcpip 4207 (interface
    # bind failure) and WLAN-AutoConfig 10002 (extensibility module crash)
    # events in its System log within the last 14 days.
    ids = {(e.provider, e.event_id) for e in events}
    assert ids  # at least one known network event exists in the last 14 days here
