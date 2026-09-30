"""System Restore analysis: time zones, gaps, shadow storage, protection, findings."""
from datetime import datetime, timedelta, timezone

import pytest

from modules.restore_manager import restore_analysis as ra

NOW = datetime(2026, 9, 26, 12, 0, 0)


def _pt(seq, stamp, desc="pt", kind=7):
    return {"SequenceNumber": seq, "Description": desc, "RestorePointType": kind, "CreationTime": stamp}


def _local_from_utc(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def test_dmtf_minus_000_is_utc_and_shown_in_local_time():
    # Measured: the point stamped 11:34:45 '-000' has its shadow copy at 14:34:55
    # local on a UTC+3 machine.  Reading the digits as local was 3 hours off.
    got = ra.dmtf_to_local("20260923113445.459238-000")
    assert got == _local_from_utc(2026, 9, 23, 11, 34, 45)


def test_dmtf_offset_is_honoured():
    # 12:00 at UTC+120 minutes is 10:00 UTC.
    got = ra.dmtf_to_local("20260923120000.000000+120")
    assert got == _local_from_utc(2026, 9, 23, 10, 0, 0)


@pytest.mark.parametrize("bad", ["", None, "garbage", "2026", 12345, "20261399999999.000000-000"])
def test_bad_times_are_none(bad):
    assert ra.dmtf_to_local(bad) is None


def test_points_sorted_with_ages_and_gaps():
    a = _pt(1, "20260901000000.000000-000")
    b = _pt(2, "20260910000000.000000-000")
    c = _pt(3, "20260920000000.000000-000")
    infos = ra.analyze_points([c, a, b], now=_local_from_utc(2026, 9, 26, 0, 0))
    assert [p.sequence for p in infos] == [1, 2, 3]
    assert infos[0].gap_days is None
    assert infos[1].gap_days == pytest.approx(9.0)
    assert infos[2].gap_days == pytest.approx(10.0)
    assert infos[2].age_days == pytest.approx(6.0)


def test_point_types_are_named():
    assert ra.analyze_points([_pt(1, "20260901000000.000000-000", kind=0)])[0].kind == "Application install"
    assert ra.analyze_points([_pt(1, "20260901000000.000000-000", kind="MODIFY_SETTINGS")])[0].kind == "Modify settings"
    assert ra.analyze_points([_pt(1, "20260901000000.000000-000", kind=99)])[0].kind == "Type 99"


def test_a_point_without_a_sequence_stays_listed_but_unaddressable():
    infos = ra.analyze_points([_pt(None, "20260901000000.000000-000")])
    assert infos[0].sequence is None
    assert ra.sequences_older_than(infos, 0) == []


def test_older_than_always_spares_the_newest():
    infos = ra.analyze_points(
        [_pt(1, "20260101000000.000000-000"), _pt(2, "20260102000000.000000-000"),
         _pt(3, "20260103000000.000000-000")], now=datetime(2026, 9, 1))
    # every point is "old", yet the newest must survive
    assert sorted(ra.sequences_older_than(infos, 30)) == [1, 2]
    assert ra.sequences_older_than(infos[:1], 30) == []


def test_older_than_respects_the_cutoff():
    now = _local_from_utc(2026, 9, 26, 0, 0)
    infos = ra.analyze_points(
        [_pt(1, "20260801000000.000000-000"), _pt(2, "20260910000000.000000-000"),
         _pt(3, "20260925000000.000000-000")], now=now)
    assert ra.sequences_older_than(infos, 30) == [1]


def test_verify_deleted_reads_back():
    remaining = ra.analyze_points([_pt(3, "20260101000000.000000-000")])
    gone, still = ra.verify_deleted([1, 2, 3], remaining)
    assert gone == [1, 2] and still == [3]


VSS_OUT = """vssadmin 1.1 - Volume Shadow Copy Service administrative command-line tool

Shadow Copy Storage association
   For volume: (C:)\\\\?\\Volume{c2ab9217-0cda-4030-a4a0-c422b25c2fd4}\\
   Shadow Copy Storage volume: (C:)\\\\?\\Volume{c2ab9217-0cda-4030-a4a0-c422b25c2fd4}\\
   Used Shadow Copy Storage space: 35.9 GB (1%)
   Allocated Shadow Copy Storage space: 36.7 GB (1%)
   Maximum Shadow Copy Storage space: 37.2 GB (1%)

Shadow Copy Storage association
   For volume: (D:)\\\\?\\Volume{aaaa}\\
   Used Shadow Copy Storage space: 0 bytes (0%)
   Allocated Shadow Copy Storage space: 0 bytes (0%)
   Maximum Shadow Copy Storage space: UNBOUNDED (100%)
"""


def test_parse_shadowstorage():
    entries = ra.parse_shadowstorage(VSS_OUT)
    assert [e.volume for e in entries] == ["C:", "D:"]
    c, d = entries
    assert c.used == pytest.approx(35.9 * 1024 ** 3, rel=0.001)
    assert c.used_of_max_percent == pytest.approx(96.5, abs=0.5)
    assert d.unbounded and d.used_of_max_percent is None and d.used == 0


def test_parse_shadowstorage_refusal_is_none_not_empty():
    assert ra.parse_shadowstorage("Error: You don't have the correct permissions") is None
    assert ra.parse_shadowstorage("") is None
    assert ra.parse_shadowstorage("No shadow copy storage associations exist.") == []


def test_parse_size_units():
    assert ra.parse_size("2 KB") == 2048
    assert ra.parse_size("1,024 MB (5%)") == 1024 ** 3
    assert ra.parse_size("UNBOUNDED") is None


def test_parse_protection_decodes_mounts():
    prot = ra.parse_protection(["\\\\?\\Volume{c2ab9217-0cda-4030-a4a0-c422b25c2fd4}\\:(C%3A)",
                                "\\\\?\\Volume{0900599b-9ede-46f3-bdcb-9c0e4f393e08}\\:()", "junk"])
    assert prot.mounts == ["C:"] and len(prot.volume_ids) == 2


def _findings(points=(), protection=None, storage=(), **kw):
    prot = ra.Protection(mounts=["C:"]) if protection is None else protection
    return ra.restore_findings(ra.analyze_points(points, now=NOW), prot, list(storage) or [], "",
                               kw.get("policy", False), kw.get("freq", 0), system_mount="C:",
                               vss_disabled=kw.get("vss_disabled"), sr_task=kw.get("sr_task"))


def test_protection_off_on_system_drive_is_an_error():
    f = ra.restore_findings([], ra.Protection(mounts=["E:"]), [], "", False, 0, system_mount="C:")
    assert f[0].severity == "error" and "OFF for C:" in f[0].title


def test_unreadable_protection_is_not_reported_as_off():
    f = ra.restore_findings([], None, [], "", False, 0, system_mount="C:")
    assert not any("OFF" in x.title for x in f)
    assert any("could not be read" in x.title for x in f)


def test_no_points_and_stale_points():
    assert any("No restore points" in f.title for f in _findings())
    old = [_pt(1, "20260101000000.000000-000")]
    assert any("days old" in f.title for f in _findings(old))


def test_full_shadow_storage_warns():
    full = ra.ShadowStorage("C:", used=95, allocated=95, maximum=100)
    f = _findings([_pt(1, "20260926000000.000000-000")], storage=[full])
    assert any("95% full" in x.title for x in f)


def test_policy_disabled_is_an_error():
    assert any(x.severity == "error" and "policy" in x.title for x in _findings(policy=True))


def test_default_frequency_explains_the_24h_limit():
    assert any("24 hours" in x.title for x in _findings([_pt(1, "20260926000000.000000-000")],
                                                        freq=ra.DEFAULT_FREQUENCY_MINUTES))


def test_protection_rows_unknown_vs_off():
    rows = ra.protection_rows(["C:", "E:"], ra.Protection(mounts=["C:"]), [ra.ShadowStorage("C:", used=1)])
    assert [(r.mount, r.protected) for r in rows] == [("C:", True), ("E:", False)]
    assert rows[0].storage.used == 1 and rows[1].storage is None


def test_markdown_has_points_storage_and_findings():
    pts = ra.analyze_points([_pt(1, "20260920000000.000000-000", "Before driver")], now=NOW)
    md = ra.points_to_markdown(pts, [ra.ShadowStorage("C:", used=GB2, allocated=GB2, maximum=GB2 * 2)],
                               [ra.Finding("warning", "Something", "detail")], "HOST")
    assert "HOST" in md and "Before driver" in md and "| C: |" in md and "[warning] Something" in md


GB2 = 2 * 1024 ** 3


def test_read_failure_raises_instead_of_returning_empty(monkeypatch):
    monkeypatch.setattr(ra, "_run", lambda *a, **k: (1, "", "Access is denied"))
    with pytest.raises(ra.RestoreReadError):
        ra.read_restore_points()
    monkeypatch.setattr(ra, "_run", lambda *a, **k: (0, "", ""))
    assert ra.read_restore_points() == []           # a real "none"


def test_real_machine_is_plausible():
    try:
        points = ra.read_restore_points()
    except ra.RestoreReadError as e:
        pytest.skip(str(e))
    infos = ra.analyze_points(points)
    now = datetime.now()
    for p in infos:
        assert p.created is None or p.created <= now + timedelta(minutes=5), "a point from the future"
    prot, err = ra.read_protection()
    assert prot is not None, err


def test_vss_service_disabled_is_an_error():
    f = _findings([_pt(1, "20260926000000.000000-000")], vss_disabled={"VSS": True})
    assert any(x.severity == "error" and "Volume Shadow Copy" in x.title for x in f)


def test_vss_service_not_disabled_reports_nothing():
    f = _findings([_pt(1, "20260926000000.000000-000")], vss_disabled={"VSS": False, "swprv": False})
    assert not any("Volume Shadow Copy" in x.title or "Shadow Copy Provider" in x.title for x in f)


def test_vss_service_unreadable_reports_nothing_not_a_false_positive():
    # None means "could not tell" (access denied / absent) -- collapsing that
    # into "disabled" would be a false alarm, the same trap this module's
    # protection and policy reads already guard against.
    f = _findings([_pt(1, "20260926000000.000000-000")], vss_disabled={"VSS": None})
    assert not any("Volume Shadow Copy" in x.title for x in f)


SCHTASKS_ENABLED = """Folder: \\Microsoft\\Windows\\SystemRestore
HostName:                             VRAKU
TaskName:                             \\Microsoft\\Windows\\SystemRestore\\SR
Next Run Time:                        N/A
Status:                               Ready
Last Run Time:                        9/29/2026 4:18:14 PM
Last Result:                          0
Scheduled Task State:                 Enabled
"""

SCHTASKS_DISABLED = SCHTASKS_ENABLED.replace("Scheduled Task State:                 Enabled",
                                             "Scheduled Task State:                 Disabled")

SCHTASKS_FAILED_RUN = SCHTASKS_ENABLED.replace("Last Result:                          0",
                                               "Last Result:                          2147943645")


def test_sr_task_status_parses_enabled_and_result(monkeypatch):
    monkeypatch.setattr(ra, "_run", lambda *a, **k: (0, SCHTASKS_ENABLED, ""))
    s = ra.read_sr_task_status()
    assert s.enabled is True and s.last_result == 0
    assert s.last_run == "9/29/2026 4:18:14 PM" and s.next_run == "N/A"
    assert s.error == ""


def test_sr_task_disabled_is_parsed(monkeypatch):
    monkeypatch.setattr(ra, "_run", lambda *a, **k: (0, SCHTASKS_DISABLED, ""))
    s = ra.read_sr_task_status()
    assert s.enabled is False


def test_sr_task_query_refusal_is_none_with_a_reason(monkeypatch):
    monkeypatch.setattr(ra, "_run", lambda *a, **k: (1, "", "ERROR: Access is denied."))
    s = ra.read_sr_task_status()
    assert s.enabled is None and "access denied" in s.error.lower()


def test_sr_task_not_found_is_none_with_a_reason(monkeypatch):
    monkeypatch.setattr(ra, "_run", lambda *a, **k: (
        1, "", "ERROR: The system cannot find the file specified."))
    s = ra.read_sr_task_status()
    assert s.enabled is None and s.error


def test_sr_task_disabled_is_an_error_finding():
    disabled = ra.SrTaskStatus(enabled=False)
    f = _findings([_pt(1, "20260926000000.000000-000")], sr_task=disabled)
    assert any(x.severity == "error" and "Automatic restore point task is disabled" in x.title for x in f)


def test_sr_task_failed_run_is_a_warning_finding():
    failed = ra.SrTaskStatus(enabled=True, last_result=2147943645, last_run="9/1/2026 1:00:00 AM")
    f = _findings([_pt(1, "20260926000000.000000-000")], sr_task=failed)
    hit = next(x for x in f if x.title == "Last automatic restore point run failed")
    assert hit.severity == "warning"
    assert "0x800704dd" in hit.detail.lower() and "9/1/2026" in hit.detail


def test_sr_task_healthy_reports_nothing():
    healthy = ra.SrTaskStatus(enabled=True, last_result=0, last_run="today")
    f = _findings([_pt(1, "20260926000000.000000-000")], sr_task=healthy)
    assert not any("Automatic restore point task" in x.title or "automatic restore point run" in x.title
                  for x in f)


def test_sr_task_unreadable_is_info_not_error():
    unreadable = ra.SrTaskStatus(error="Task Scheduler refused to list it (access denied).")
    f = _findings([_pt(1, "20260926000000.000000-000")], sr_task=unreadable)
    assert any(x.severity == "info" and "could not be read" in x.title for x in f)
    assert not any(x.severity == "error" and "Automatic restore point" in x.title for x in f)


def test_real_machine_sr_task_status_is_readable_unelevated():
    # Measured on this machine: `schtasks /query /v` on
    # \\Microsoft\\Windows\\SystemRestore\\SR succeeds with no elevation
    # prompt and reports Enabled, Last Result 0. A None here would mean the
    # unelevated read stopped working on this machine.
    s = ra.read_sr_task_status()
    assert s.error == "", s.error
    assert s.enabled is not None, "SR task Enabled/Disabled state could not be read unelevated"


def test_real_machine_vss_services_are_queryable_unelevated():
    # Measured on this machine: `sc qc VSS` / `sc qc swprv` both succeed with
    # SC_MANAGER_CONNECT + SERVICE_QUERY_CONFIG and no elevation prompt, and
    # both start types come back Demand-Start (neither Disabled). A None
    # here would mean the unelevated read stopped working on this machine.
    result = ra.read_vss_services_disabled()
    assert set(result) == set(ra.VSS_SERVICES)
    for name, disabled in result.items():
        assert disabled is not None, f"{name} start type could not be read unelevated"
        assert disabled is False, f"{name} is unexpectedly Disabled on this machine"
