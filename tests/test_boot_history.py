"""Boot history parsing (pure) and a plausibility pass against the real logs."""
from datetime import datetime, timedelta

from modules.boot_analyzer import boot_history as bh

_NS = 'xmlns="http://schemas.microsoft.com/win/2004/08/events/event"'


def _event(event_id, when, data):
    fields = "".join(f'<Data Name="{k}">{v}</Data>' for k, v in data.items())
    return (f'<Event {_NS}><System><EventID>{event_id}</EventID>'
            f'<TimeCreated SystemTime="{when}"/></System><EventData>{fields}</EventData></Event>')


BOOT = _event(100, "2026-09-25T05:36:15.601249700Z", {
    "BootTime": 53006, "MainPathBootTime": 40906, "BootPostBootTime": 12100,
    "BootNumStartupApps": 22, "BootIsDegradation": "true", "BootDegradationDelta": 9000,
    "BootRootCauseStepDegradationBits": 4})


def test_parse_boot_event():
    (event,) = bh.split_events(BOOT)
    boot = bh.parse_boot_event(event)
    assert boot.boot_ms == 53006 and boot.main_path_ms == 40906 and boot.post_boot_ms == 12100
    assert boot.startup_apps == 22 and boot.degraded and boot.degradation_delta_ms == 9000
    assert boot.root_cause_bits == 4


def test_parse_slow_events_and_summary():
    events = bh.split_events(
        _event(101, "2026-09-24T13:27:24Z", {"Name": "a.exe", "FriendlyName": "App A", "TotalTime": 5000,
                                             "DegradationTime": 500, "Path": r"C:\A\a.exe", "CompanyName": "Acme"})
        + _event(101, "2026-09-23T13:27:24Z", {"Name": "a.exe", "FriendlyName": "App A", "TotalTime": 9000,
                                               "DegradationTime": 900, "Path": r"C:\A\a.exe"})
        + _event(103, "2026-09-22T13:27:24Z", {"Name": "windefend", "TotalTime": 3000, "DegradationTime": 2900,
                                               "Path": '"c:\\x\\msmpeng.exe"'})
        + _event(999, "2026-09-22T13:27:24Z", {"Name": "ignored"}))
    slow = [s for s in (bh.parse_slow_event(e) for e in events) if s]
    assert [s.kind for s in slow] == ["App", "App", "Service"]
    top = bh.summarise_slow(slow)
    assert top[0].name == "App A" and top[0].worst_ms == 9000 and top[0].avg_ms == 7000 and top[0].count == 2
    assert top[1].kind == "Service" and top[1].path == r"c:\x\msmpeng.exe"


def test_event_without_needed_fields_is_skipped_not_invented():
    (event,) = bh.split_events(_event(100, "2026-09-25T05:36:15Z", {"Other": 1}))
    assert bh.parse_boot_event(event) is None


def test_empty_output_is_no_events():
    assert bh.split_events("  ") == []


def test_boot_type_matches_nearest_preceding_kernel_event():
    when = datetime(2026, 9, 25, 8, 36)
    boot = bh.BootRecord(when, 1, 1, 1, 1, False, 0, 0)
    kernel = [(when - timedelta(minutes=34), 1), (when - timedelta(hours=30), 0), (when + timedelta(hours=1), 2)]
    bh.match_boot_types([boot], kernel)
    assert boot.boot_type == "Fast Startup (hybrid)"
    lonely = bh.BootRecord(when, 1, 1, 1, 1, False, 0, 0)
    bh.match_boot_types([lonely], [(when - timedelta(hours=9), 0)])
    assert lonely.boot_type is None


BCD = """Firmware Boot Manager
---------------------
identifier              {fwbootmgr}
timeout                 1

Windows Boot Manager
--------------------
identifier              {bootmgr}
description             Windows Boot Manager
timeout                 30

Windows Boot Loader
-------------------
identifier              {aaa}
inherit                 {bootloadersettings}

Windows Boot Loader
-------------------
identifier              {bbb}
inherit                 {bootloadersettings}

Resume from Hibernate
---------------------
identifier              {ccc}
"""


def test_bcd_timeout_is_the_boot_menus_not_firmwares():
    parsed = bh.parse_bcd(BCD)
    assert parsed == {"timeout": 30, "entries": 2}


def test_refused_bcdedit_is_none_not_zero():
    parsed = bh.parse_bcd("The boot configuration data store could not be opened.\nAccess is denied.\n")
    assert parsed == {"timeout": None, "entries": None}


def test_uptime_note_explains_fast_startup_carry_over():
    boot = bh.BootRecord(datetime.now() - timedelta(hours=2), 40000, 1, 1, 10, False, 0, 0)
    facts = bh.BootFacts(boots=[boot], fast_startup=True, uptime_s=5 * 86400)
    assert "outlived the shutdown" in bh.uptime_note(facts)
    off = bh.BootFacts(boots=[boot], fast_startup=False, uptime_s=2 * 3600)
    assert "every shutdown is a full boot" in bh.uptime_note(off)


def test_trend_note_handles_no_boots():
    assert "No boot events" in bh.trend_note([])


def test_real_logs_are_plausible():
    facts = bh.read_boot_facts()
    assert not facts.problems, facts.problems
    assert 1 <= len(facts.boots) <= 20
    for boot in facts.boots:
        assert 3_000 < boot.boot_ms < 900_000
        assert boot.when <= datetime.now() + timedelta(minutes=5)
    assert facts.uptime_s and facts.uptime_s > 0
    assert facts.fast_startup in (True, False)
