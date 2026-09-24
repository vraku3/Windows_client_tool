r"""The Adapter Info card's DNS column was always blank.

Found 2026-09-24 sweeping every table against the real machine: get_adapter_info
initialised `dns = ""` and never assigned it, so all nine adapters showed an
empty DNS cell. It now comes from Get-DnsClientServerAddress.
"""
import json

import pytest

from modules.network_diagnostics import network_tools as nt


def test_several_adapters_arrive_as_a_list():
    raw = json.dumps([
        {"InterfaceAlias": "Ethernet 3", "ServerAddresses": ["192.168.3.1", "1.1.1.1"]},
        {"InterfaceAlias": "Wi-Fi", "ServerAddresses": ["8.8.8.8"]}])
    assert nt.parse_dns_servers(raw) == {
        "Ethernet 3": "192.168.3.1, 1.1.1.1", "Wi-Fi": "8.8.8.8"}


def test_a_single_adapter_arrives_as_a_bare_object_not_a_list():
    raw = json.dumps({"InterfaceAlias": "Ethernet", "ServerAddresses": ["9.9.9.9"]})
    assert nt.parse_dns_servers(raw) == {"Ethernet": "9.9.9.9"}


def test_a_single_server_may_be_a_bare_string():
    raw = json.dumps({"InterfaceAlias": "Ethernet", "ServerAddresses": "9.9.9.9"})
    assert nt.parse_dns_servers(raw) == {"Ethernet": "9.9.9.9"}


def test_an_adapter_with_no_servers_is_left_out_not_invented():
    raw = json.dumps([{"InterfaceAlias": "Loopback", "ServerAddresses": []},
                      {"InterfaceAlias": "Other", "ServerAddresses": None}])
    assert nt.parse_dns_servers(raw) == {}


def test_empty_output_is_no_answer():
    assert nt.parse_dns_servers("") == {}
    assert nt.parse_dns_servers("   ") == {}


def test_a_failed_read_returns_nothing_and_says_so(monkeypatch, caplog):
    def boom(*a, **k):
        raise OSError("powershell missing")
    monkeypatch.setattr(nt.subprocess, "run", boom)
    with caplog.at_level("WARNING"):
        assert nt.get_dns_servers() == {}
    assert "DNS" in caplog.text


def test_the_adapter_rows_carry_the_dns_servers(monkeypatch):
    monkeypatch.setattr(nt, "get_dns_servers", lambda: {"Ethernet 3": "192.168.3.1"})
    rows = {a["Name"]: a for a in nt.get_adapter_info()}
    if "Ethernet 3" in rows:                       # only on a machine that has it
        assert rows["Ethernet 3"]["DNS"] == "192.168.3.1"
    assert all("DNS" in a for a in rows.values())


@pytest.mark.real_machine
def test_real_machine_an_up_adapter_has_dns():
    up = [a for a in nt.get_adapter_info() if a["Up"] == "Yes" and a["Gateway"]]
    assert up and any(a["DNS"] for a in up), up
