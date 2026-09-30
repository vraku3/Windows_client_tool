"""Boot history parsing (pure) and a plausibility pass against the real logs."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

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


def _key_cm():
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock())
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_read_firmware_type_uefi_secure_boot_on():
    with patch("winreg.OpenKey", return_value=_key_cm()), \
         patch("winreg.QueryValueEx", return_value=(1, 4)):
        firmware, secure_boot = bh.read_firmware_type()
    assert firmware == "UEFI"
    assert secure_boot is True


def test_read_firmware_type_uefi_secure_boot_off():
    with patch("winreg.OpenKey", return_value=_key_cm()), \
         patch("winreg.QueryValueEx", return_value=(0, 4)):
        firmware, secure_boot = bh.read_firmware_type()
    assert firmware == "UEFI"
    assert secure_boot is False


def test_read_firmware_type_uefi_but_secure_boot_value_unreadable():
    with patch("winreg.OpenKey", return_value=_key_cm()), \
         patch("winreg.QueryValueEx", side_effect=OSError("refused")):
        firmware, secure_boot = bh.read_firmware_type()
    assert firmware == "UEFI"
    assert secure_boot is None


def test_read_firmware_type_legacy_bios_key_absent():
    """Key does not exist at all -- a real, unelevated Legacy BIOS signal."""
    with patch("winreg.OpenKey", side_effect=FileNotFoundError()):
        firmware, secure_boot = bh.read_firmware_type()
    assert firmware == "Legacy BIOS"
    assert secure_boot is None


def test_read_firmware_type_refused_is_unknown_never_a_guess():
    with patch("winreg.OpenKey", side_effect=PermissionError()):
        firmware, secure_boot = bh.read_firmware_type()
    assert firmware == "Unknown"
    assert secure_boot is None


def test_read_firmware_type_real_machine():
    """Real, unelevated read on this machine.

    Confirmed live 2026-09-28 (unelevated): `Test-Path
    HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State` is True and
    `UEFISecureBootEnabled` reads back 0 -- a real UEFI machine with Secure
    Boot off. `bcdedit /enum firmware` on the very same machine, same
    privilege level, exits 1 "Access is denied" with empty stdout, which is
    the reading this function replaces.
    """
    firmware, secure_boot = bh.read_firmware_type()
    assert firmware == "UEFI"
    assert secure_boot is False


def test_real_logs_are_plausible():
    facts = bh.read_boot_facts()
    # Unelevated, Diagnostics-Performance/Operational refuses -- a real,
    # honestly-reported refusal, not a bug. Elevated, there must be none.
    from core.admin_utils import is_admin
    if is_admin():
        assert not facts.problems, facts.problems
        assert 1 <= len(facts.boots) <= 20
    else:
        assert all("Access is denied" in p or "denied" in p.lower() for p in facts.problems), facts.problems
        assert 0 <= len(facts.boots) <= 20
        # The primary log is refused unelevated on this real machine, which
        # is exactly the case read_fallback_boot_history exists for: the
        # System log is a different, always-unelevated-readable source, and
        # this machine reboots often enough that it must have something.
        if not facts.boots:
            assert facts.fallback_boots, (
                "primary boot log refused and the System-log fallback found "
                "nothing either -- both boot-history sources are dark")
            for record in facts.fallback_boots:
                assert record.when <= datetime.now() + timedelta(minutes=5)
                if record.duration_seconds is not None:
                    assert 1.0 < record.duration_seconds < 3600.0
    for boot in facts.boots:
        assert 3_000 < boot.boot_ms < 900_000
        assert boot.when <= datetime.now() + timedelta(minutes=5)
    assert facts.uptime_s and facts.uptime_s > 0
    assert facts.fast_startup in (True, False)


def _fallback_query(boot_xmls="", shutdown_xmls=""):
    """A `_query` stand-in: routes by which xpath asked, like the real one
    routes by which log/filter -- lets the pairing logic be tested without
    touching wevtutil."""
    def fake(log, xpath, count):
        if "EventID=12" in xpath:
            return bh.split_events(boot_xmls), None
        return bh.split_events(shutdown_xmls), None
    return fake


def test_read_fallback_boot_history_pairs_ready_and_clean_shutdown():
    boot = _event(12, "2026-09-30T05:46:36Z", {})
    ready = _event(6005, "2026-09-30T05:47:09Z", {})
    prior_clean = _event(6006, "2026-09-29T20:42:17Z", {})
    with patch("modules.boot_analyzer.boot_history._query",
               side_effect=_fallback_query(boot, prior_clean + ready)):
        records, reason = bh.read_fallback_boot_history()
    assert reason is None
    assert len(records) == 1
    rec = records[0]
    assert rec.duration_seconds is not None
    assert 30.0 < rec.duration_seconds < 35.0
    assert rec.prior_shutdown_clean is True


def test_read_fallback_boot_history_flags_an_unexpected_prior_shutdown():
    boot = _event(12, "2026-09-30T05:46:36Z", {})
    ready = _event(6005, "2026-09-30T05:47:09Z", {})
    prior_dirty = _event(6008, "2026-09-29T20:42:17Z", {})
    with patch("modules.boot_analyzer.boot_history._query",
               side_effect=_fallback_query(boot, prior_dirty + ready)):
        records, _reason = bh.read_fallback_boot_history()
    assert records[0].prior_shutdown_clean is False


def test_read_fallback_boot_history_missing_ready_is_none_not_zero():
    """No 6005 after the boot (log rotated, or it just hasn't happened)
    must report `duration_seconds=None`, never 0 or a guessed value."""
    boot = _event(12, "2026-09-30T05:46:36Z", {})
    with patch("modules.boot_analyzer.boot_history._query",
               side_effect=_fallback_query(boot, "")):
        records, _reason = bh.read_fallback_boot_history()
    assert records[0].duration_seconds is None
    assert records[0].prior_shutdown_clean is None


def test_read_fallback_boot_history_propagates_a_refusal():
    with patch("modules.boot_analyzer.boot_history._query",
               return_value=(None, "System: Access is denied.")):
        records, reason = bh.read_fallback_boot_history()
    assert records == []
    assert reason == "System: Access is denied."


def test_fallback_trend_note_needs_at_least_one_measured_boot():
    assert "No boot events" in bh.fallback_trend_note([])


def test_fallback_trend_note_reports_median_and_worst():
    now = datetime(2026, 9, 30, 5, 46, 36)
    records = [
        bh.FallbackBootRecord(now, 30.0, True),
        bh.FallbackBootRecord(now, 40.0, True),
        bh.FallbackBootRecord(now, 20.0, None),
    ]
    note = bh.fallback_trend_note(records)
    assert "3 boots" in note and "System log approximation" in note


def test_read_boot_facts_falls_back_when_the_perf_log_is_refused(monkeypatch):
    """The integration point: `read_boot_facts` must reach for the System
    log only once the Performance log has genuinely given nothing, and
    must report the fallback separately rather than inventing BootRecords
    it has no MainPath/PostBoot breakdown for."""
    boot = _event(12, "2026-09-30T05:46:36Z", {})
    ready = _event(6005, "2026-09-30T05:47:09Z", {})

    def fake_query(log, xpath, count):
        if log == bh.PERF_LOG:
            return None, f"{log}: Access is denied."
        if "Kernel-Boot" in xpath:
            return [], None
        if "EventID=12" in xpath:
            return bh.split_events(boot), None
        return bh.split_events(ready), None

    monkeypatch.setattr(bh, "_query", fake_query)
    monkeypatch.setattr(bh, "read_fast_startup", lambda: (True, None))
    facts = bh.read_boot_facts()
    assert facts.boots == []
    assert len(facts.fallback_boots) == 1
    assert facts.fallback_boots[0].duration_seconds is not None
    assert any("Access is denied" in p for p in facts.problems)
