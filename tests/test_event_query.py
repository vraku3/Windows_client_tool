"""Reading events through wevtutil: parsing, query building, refusals."""
import subprocess

import pytest

from modules.event_viewer import event_query as eq

_EVENT = (
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
    "<Provider Name='Microsoft-Windows-DistributedCOM' EventSourceName='DCOM'/>"
    "<EventID Qualifiers='0'>10016</EventID><Level>3</Level>"
    "<TimeCreated SystemTime='2026-09-26T06:00:48.6272778Z'/><EventRecordID>14295</EventRecordID>"
    "<Channel>System</Channel><Computer>Vraku</Computer><Execution ProcessID='2156' ThreadID='1'/>"
    "<Security UserID='S-1-5-21-1'/></System>"
    "<EventData><Data Name='param1'>Local</Data><Data>bare</Data></EventData>"
    "<RenderingInfo Culture='en-US'><Message>Permission &lt;denied&gt; &amp; more&#13;&#10;line two</Message>"
    "</RenderingInfo></Event>"
)
_NO_MESSAGE = (
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
    "<Provider Name='Some-Provider'/><EventID>42</EventID><Level>2</Level>"
    "<TimeCreated SystemTime='2026-09-26T06:00:00.0Z'/><EventRecordID>1</EventRecordID>"
    "<Channel>System</Channel><Computer>x</Computer></System>"
    "<EventData><Data Name='a'>1</Data><Data Name='b'>two</Data></EventData></Event>"
)


def test_parse_renders_message_and_fields():
    (entry,) = eq.parse_event_xml(_EVENT, "System")
    assert entry.source == "DCOM"
    assert entry.level == "Warning"
    assert entry.message == "Permission <denied> & more\nline two"
    assert entry.raw["event_id"] == 10016
    assert entry.raw["provider"] == "Microsoft-Windows-DistributedCOM"
    assert entry.raw["data"] == {"param1": "Local", "Data2": "bare"}
    assert entry.raw["message_rendered"] is True
    assert entry.timestamp.year == 2026


def test_missing_message_is_stated_not_blank():
    (entry,) = eq.parse_event_xml(_NO_MESSAGE, "System")
    assert entry.level == "Error"
    assert entry.message.startswith("(message text unavailable")
    assert "b=two" in entry.message
    assert entry.raw["message_rendered"] is False


def test_control_characters_do_not_lose_the_batch():
    bad = _EVENT.replace("line two", "line\x01 two")
    assert len(eq.parse_event_xml(bad + _NO_MESSAGE, "System")) == 2


def test_one_broken_block_does_not_lose_the_others():
    broken = "<Event><System><Provider Name='x'></System></Event>"
    assert len(eq.parse_event_xml(_EVENT + broken + _NO_MESSAGE, "System")) == 2


def test_build_query():
    assert eq.build_query(None) == "*"
    q = eq.build_query(24, eq.PROBLEM_LEVELS)
    assert "Level=1" in q and "86400000" in q


class _Proc:
    def __init__(self, rc, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_access_denied_is_reported_not_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(5, b"", b"Access is denied."))
    result = eq.query_log("Security", 24)
    assert result.access_denied and result.error and result.entries == []


def test_missing_wevtutil_is_an_error_not_an_empty_log(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("wevtutil")
    monkeypatch.setattr(subprocess, "run", boom)
    result = eq.query_log("System", 24)
    assert result.error is not None and result.entries == []


def test_truncation_flag(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, (_EVENT * 3).encode()))
    assert eq.query_log("System", 24, max_events=3).truncated is True
    assert eq.query_log("System", 24, max_events=10).truncated is False


def test_real_system_log_has_rendered_messages():
    result = eq.query_log("System", 24, 300)
    if result.error:
        pytest.skip(f"wevtutil unavailable here: {result.error}")
    assert result.entries, "a 24 h System log is never empty on a live machine"
    rendered = sum(1 for e in result.entries if e.raw["message_rendered"])
    assert rendered / len(result.entries) > 0.7
    assert all(e.message.strip() for e in result.entries)


def test_ansi_output_is_decoded_not_replaced(monkeypatch):
    # wevtutil piped output is the ANSI code page: an em dash is the single byte 0x97.
    xml = _EVENT.replace("Permission", "Good news—you").encode("cp1252")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, xml))
    (entry,) = eq.query_log("System", 24).entries
    assert "—" in entry.message and "�" not in entry.message
