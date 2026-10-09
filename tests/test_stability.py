"""core.stability: unexpected shutdowns grouped into incidents and classified."""
from types import SimpleNamespace

from core import stability as st

_NS = "xmlns='http://schemas.microsoft.com/win/2004/08/events/event'"


def _kp41(when, **data):
    fields = {"BugcheckCode": "0", "SleepInProgress": "0", "PowerButtonTimestamp": "0",
              "LongPowerButtonPressDetected": "false", **data}
    body = "".join(f"<Data Name='{k}'>{v}</Data>" for k, v in fields.items())
    return (f"<Event {_NS}><System><Provider Name='Microsoft-Windows-Kernel-Power'/>"
            f"<EventID>41</EventID><TimeCreated SystemTime='{when}'/></System>"
            f"<EventData>{body}</EventData></Event>")


def _el6008(when):
    # Real shape from this machine: positional Data, the date text carrying
    # junk direction marks -- which is why only TimeCreated is used.
    return (f"<Event {_NS}><System><Provider Name='EventLog'/>"
            f"<EventID Qualifiers='32768'>6008</EventID><TimeCreated SystemTime='{when}'/></System>"
            f"<EventData><Data>12:57:16 PM</Data><Data>?10/?4/?2026</Data></EventData></Event>")


def _wer1001(when, param1):
    return (f"<Event {_NS}><System><Provider Name='Microsoft-Windows-WER-SystemErrorReporting'/>"
            f"<EventID>1001</EventID><TimeCreated SystemTime='{when}'/></System>"
            f"<EventData><Data Name='param1'>{param1}</Data></EventData></Event>")


def _incidents(*events):
    return st.group_incidents(st.parse_events("".join(events)))


def test_a_41_and_its_6008_seconds_apart_are_one_incident():
    """Measured here 2026-10-04: 41 at 10:49:24Z, 6008 at 10:49:42Z."""
    found = _incidents(_kp41("2026-10-04T10:49:24.8184300Z"), _el6008("2026-10-04T10:49:42.0218215Z"))
    assert len(found) == 1
    assert found[0].event_ids == [41, 6008]
    assert found[0].cause == st.POWER_LOSS


def test_separate_failures_are_separate_incidents_newest_first():
    found = _incidents(_kp41("2026-09-23T08:34:00Z"), _el6008("2026-09-23T08:34:20Z"),
                       _el6008("2026-10-04T09:57:16Z"))
    assert [i.event_ids for i in found] == [[6008], [41, 6008]]


def test_a_41_bugcheck_code_is_decimal_and_means_a_blue_screen_without_a_dump():
    (incident,) = _incidents(_kp41("2026-10-01T10:00:00Z", BugcheckCode="159"))
    assert incident.cause == st.BUGCHECK
    assert incident.bugcheck.startswith("0x0000009F")
    assert "no crash dump" in incident.summary


def test_a_held_power_button_is_named_as_such():
    (incident,) = _incidents(_kp41("2026-10-01T10:00:00Z", PowerButtonTimestamp="133734869562185590"))
    assert incident.cause == st.POWER_BUTTON
    (incident,) = _incidents(_kp41("2026-10-01T10:00:00Z", LongPowerButtonPressDetected="true"))
    assert incident.cause == st.POWER_BUTTON


def test_dying_in_sleep_is_named_as_such():
    (incident,) = _incidents(_kp41("2026-10-01T10:00:00Z", SleepInProgress="4"))
    assert incident.cause == st.SLEEP


def test_a_1001_names_the_stop_code_and_outranks_the_41():
    found = _incidents(_kp41("2026-10-01T10:00:00Z"),
                       _wer1001("2026-10-01T10:01:30Z", "0x00000133 (0x0000000000000001, 0x0)"))
    assert len(found) == 1
    assert found[0].cause == st.BUGCHECK
    assert "DPC_WATCHDOG_VIOLATION" in found[0].bugcheck


def test_a_6008_alone_says_there_is_no_detail_rather_than_guessing():
    (incident,) = _incidents(_el6008("2026-10-04T09:57:16Z"))
    assert incident.cause == st.UNKNOWN


def test_malformed_xml_is_nothing_not_a_crash():
    assert st.parse_events("<Event><broken") == []


def test_a_refused_log_is_none_with_a_reason_never_an_empty_list():
    refused = lambda *a, **k: SimpleNamespace(returncode=5, stdout="", stderr="Access is denied.")
    incidents, reason = st.read_incidents(runner=refused)
    assert incidents is None and "Access is denied" in reason


def test_reading_the_real_system_log_works_unelevated():
    incidents, reason = st.read_incidents(days=30)
    assert incidents is not None, reason
    for incident in incidents:
        assert incident.cause in (st.POWER_LOSS, st.BUGCHECK, st.POWER_BUTTON, st.SLEEP, st.UNKNOWN)
        assert incident.summary
