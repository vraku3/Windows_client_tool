"""Winsock LSP catalog parsing (netsh winsock show catalog). No Qt."""
import subprocess

from modules.network_diagnostics import winsock_catalog as wc

_SAMPLE = """
Winsock Catalog Provider Entry
------------------------------------------------------
Entry Type:                         Base Service Provider
Description:                        Hyper-V RAW
Provider ID:                        {1234191B-4BF7-4CA7-86E0-DFD7C32B5445}
Provider Path:                      %SystemRoot%\\system32\\mswsock.dll
Catalog Entry ID:                   1001
Version:                            2

Winsock Catalog Provider Entry
------------------------------------------------------
Entry Type:                         Base Service Provider
Description:                        MSAFD Tcpip [TCP/IPv6]
Provider ID:                        {F9EAB0C0-26D4-11D0-BBBF-00AA006C34E4}
Provider Path:                      %SystemRoot%\\system32\\mswsock.dll
Catalog Entry ID:                   1007
Version:                            2
"""


def test_real_captured_sample_parses_two_providers():
    providers = wc.parse_catalog(_SAMPLE)
    assert len(providers) == 2
    assert providers[0].description == "Hyper-V RAW"
    assert providers[0].provider_path == r"%SystemRoot%\system32\mswsock.dll"
    assert providers[0].catalog_id == "1001"
    assert providers[0].entry_type == "Base Service Provider"
    assert providers[1].description == "MSAFD Tcpip [TCP/IPv6]"


def test_empty_catalog_yields_no_providers():
    assert wc.parse_catalog("") == []


def test_broken_providers_flags_a_missing_dll(monkeypatch, tmp_path):
    real = tmp_path / "real.dll"
    real.write_text("x")
    missing = str(tmp_path / "gone.dll")
    providers = [
        wc.WinsockProvider("Real", str(real), "1", "Base Service Provider"),
        wc.WinsockProvider("Gone", missing, "2", "Base Service Provider"),
    ]
    broken = wc.broken_providers(providers)
    assert len(broken) == 1 and broken[0].description == "Gone"


def test_read_catalog_returns_none_on_refusal(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert wc.read_catalog() is None


def test_read_catalog_returns_none_when_the_command_cannot_run(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("netsh not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert wc.read_catalog() is None


def test_real_winsock_catalog_is_readable_on_this_machine():
    providers = wc.read_catalog()
    assert providers is not None
    assert len(providers) > 5
    # Confirmed clean on this real machine (28 providers, all mswsock.dll).
    assert wc.broken_providers(providers) == []
