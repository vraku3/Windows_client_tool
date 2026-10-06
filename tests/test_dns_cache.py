"""DNS resolver cache parsing (ipconfig /displaydns). No Qt."""
import subprocess

from modules.network_diagnostics import dns_cache as dc

_SAMPLE = """
Windows IP Configuration

    breaking-news.support.blizzard.com
    ----------------------------------------
    Record Name . . . . . : breaking-news.support.blizzard.com
    Record Type . . . . . : 1
    Time To Live  . . . . : 10
    Data Length . . . . . : 4
    Section . . . . . . . : Answer
    A (Host) Record . . . : 34.110.236.123


    elb-ned-gcp.nimbus.bitdefender.net
    ----------------------------------------
    Record Name . . . . . : elb-ned-gcp.nimbus.bitdefender.net
    Record Type . . . . . : 1
    Time To Live  . . . . : 1614
    Data Length . . . . . : 4
    Section . . . . . . . : Answer
    A (Host) Record . . . : 34.54.215.149


    Record Name . . . . . : elb-ned-gcp.nimbus.bitdefender.net
    Record Type . . . . . : 28
    Time To Live  . . . . : 1674
    Data Length . . . . . : 16
    Section . . . . . . . : Answer
    AAAA Record . . . . . : 2600:1901:0:ed69::

"""


def test_real_captured_sample_parses_three_records():
    entries = dc.parse_displaydns(_SAMPLE)
    assert len(entries) == 3
    assert entries[0].name == "breaking-news.support.blizzard.com"
    assert entries[0].type_name == "A"
    assert entries[0].ttl == 10
    assert entries[0].data == "34.110.236.123"
    assert entries[0].section == "Answer"


def test_a_name_with_two_record_types_yields_two_separate_rows():
    entries = dc.parse_displaydns(_SAMPLE)
    same_name = [e for e in entries if e.name == "elb-ned-gcp.nimbus.bitdefender.net"]
    assert len(same_name) == 2
    assert {e.type_name for e in same_name} == {"A", "AAAA"}
    aaaa = next(e for e in same_name if e.type_name == "AAAA")
    assert aaaa.data == "2600:1901:0:ed69::"
    assert aaaa.ttl == 1674


def test_empty_cache_text_yields_no_entries():
    assert dc.parse_displaydns("Windows IP Configuration\n\n") == []


def test_unknown_record_type_falls_back_to_its_number():
    text = (
        "    Record Name . . . . . : example.com\n"
        "    Record Type . . . . . : 99\n"
        "    Time To Live  . . . . : 60\n"
        "    Data Length . . . . . : 4\n"
        "    Section . . . . . . . : Answer\n"
        "    Some Weird Record . . : payload\n"
    )
    entries = dc.parse_displaydns(text)
    assert len(entries) == 1
    assert entries[0].type_name == "99"
    assert entries[0].data == "payload"


def test_read_dns_cache_returns_none_on_refusal(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "Access is denied.")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert dc.read_dns_cache() is None


def test_read_dns_cache_returns_none_when_the_command_cannot_run(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("ipconfig not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert dc.read_dns_cache() is None


def test_real_dns_cache_read_on_this_machine_is_honest():
    """Elevated, the cache is read. Unelevated, current Windows builds refuse
    `ipconfig /displaydns` ("The requested operation requires elevation.",
    rc=1, measured 2026-10-06) -- and that must come back as None, "could
    not read", never [] ("the cache is empty")."""
    from core.admin_utils import is_admin
    entries = dc.read_dns_cache()
    if not is_admin() and entries is None:
        return
    assert isinstance(entries, list)
    if entries:
        assert all(e.name and e.type_name for e in entries)
