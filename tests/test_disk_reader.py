"""Disk health engine: parsing, findings, TRIM, alignment.  No display needed."""
import json

import pytest

from modules.disk_health import disk_reader as dr


def _disk(**over):
    base = dict(DeviceId="1", Name="Test SSD", Serial="ABC.", Media="SSD", Bus="NVMe",
                Health="Healthy", Op="OK", Size=1_000_204_886_016, Firmware="F1",
                Temp=40, TempMax=82, Wear=3, Poh=None, ReadUnc=None, WriteUnc=None, HasRel=True)
    base.update(over)
    return base


def _report(disks=(), volumes=(), partitions=(), **extra):
    return dr.parse_disk_json(json.dumps(
        {"disks": list(disks), "volumes": list(volumes), "partitions": list(partitions), **extra}))


def test_unreported_counters_stay_none_not_zero():
    d = _report([_disk()]).disks[0]
    assert d.power_on_hours is None
    assert d.read_errors_uncorrected is None
    assert dr.power_on_text(d.power_on_hours) == "not reported"
    assert dr.power_on_hours_short(None) == "n/a"


def test_serial_trailing_dot_and_nul_letters_are_cleaned():
    rep = _report([_disk()], [{"Letter": "\u0000", "Label": "", "Fs": "NTFS", "Health": "Healthy",
                                "Size": 10, "Free": 5, "BitLocker": None}])
    assert rep.disks[0].serial == "ABC"
    assert rep.volumes[0].letter == ""


def test_healthy_disk_has_no_findings():
    assert dr.disk_findings(_report([_disk()]).disks[0]) == []


@pytest.mark.parametrize("wear,severity", [(70, "warning"), (89, "warning"), (90, "error")])
def test_wear_thresholds(wear, severity):
    f = dr.disk_findings(_report([_disk(Wear=wear)]).disks[0])
    assert [x.severity for x in f] == [severity]


def test_temperature_uses_drive_maximum():
    hot = _report([_disk(Temp=81, TempMax=82)]).disks[0]
    assert dr.disk_findings(hot)[0].severity == "error"
    warm = _report([_disk(Temp=72, TempMax=82)]).disks[0]
    assert dr.disk_findings(warm)[0].severity == "warning"
    assert dr.disk_findings(_report([_disk(Temp=50)]).disks[0]) == []


def test_hdd_runs_out_of_headroom_sooner():
    hdd = _report([_disk(Media="HDD", Bus="SATA", Temp=56, TempMax=None)]).disks[0]
    assert dr.disk_findings(hdd)[0].severity == "warning"


def test_unhealthy_status_and_uncorrected_errors():
    d = _report([_disk(Health="Unhealthy", ReadUnc=3)]).disks[0]
    sev = [f.severity for f in dr.disk_findings(d)]
    assert "error" in sev and "warning" in sev
    assert dr.disk_verdict(d)[0] == "error"


def test_virtual_disks_are_never_judged():
    d = _report([_disk(Name="Msft Virtual Disk", Bus="File Backed Virtual", Temp=0, Wear=0)]).disks[0]
    assert dr.disk_findings(d) == []
    assert dr.disk_verdict(d)[0] == "info"


def test_failed_smart_attribute_is_an_error():
    from modules.disk_health.smart_reader import SmartAttribute
    d = _report([_disk()]).disks[0]
    d.smart_attrs = [SmartAttribute(5, "Reallocated Sectors", 10, 20, 36, "50", True)]
    d.predicted_failure = True
    titles = [f.title for f in dr.disk_findings(d)]
    assert any("predicts failure" in t for t in titles)
    assert any("Reallocated" in t for t in titles)


def test_parse_trim_zero_means_enabled():
    out = ("NTFS DisableDeleteNotify = 0  (Allows TRIM operations to be sent to the storage device)\n"
           "ReFS DisableDeleteNotify = 1  (Disallows TRIM operations)\n")
    assert dr.parse_trim(out) == {"NTFS": True, "ReFS": False}
    assert dr.parse_trim("Access is denied.") == {}


def test_trim_off_with_an_ssd_is_flagged_but_not_without_one():
    with_ssd = _report([_disk()])
    with_ssd.trim_enabled = {"NTFS": False}
    assert any("TRIM" in f.title for f in dr.system_findings(with_ssd))
    hdd = _report([_disk(Media="HDD")])
    hdd.trim_enabled = {"NTFS": False}
    assert not any("TRIM" in f.title for f in dr.system_findings(hdd))


def test_unknown_trim_is_not_reported_as_disabled():
    rep = _report([_disk()])
    rep.trim_enabled = {}
    assert not any("TRIM is disabled" == f.title for f in dr.system_findings(rep))


def test_alignment_ignores_msr_and_virtual_disks():
    parts = [
        {"Disk": 1, "Part": 1, "Offset": 1048576, "Size": 1, "Type": "System"},
        {"Disk": 1, "Part": 2, "Offset": 17408, "Size": 1, "Type": "Reserved"},   # normal MSR
        {"Disk": 1, "Part": 3, "Offset": 1048576 + 512, "Size": 1, "Type": "Basic"},  # misaligned
        {"Disk": 9, "Part": 1, "Offset": 513, "Size": 1, "Type": "Basic"},
    ]
    rep = _report([_disk(), _disk(DeviceId="9", Name="Msft Virtual Disk", Bus="File Backed Virtual")],
                  partitions=parts)
    found = dr.alignment_findings(rep)
    assert len(found) == 1 and "Partition 3" in found[0].title


def test_low_free_space_thresholds():
    vols = [{"Letter": "C", "Label": "", "Fs": "NTFS", "Health": "Healthy", "Size": 1000, "Free": 40},
            {"Letter": "D", "Label": "", "Fs": "NTFS", "Health": "Healthy", "Size": 1000, "Free": 80},
            {"Letter": "E", "Label": "", "Fs": "NTFS", "Health": "Healthy", "Size": 1000, "Free": 500}]
    rep = _report(volumes=vols)
    sev = {f.subject: f.severity for v in rep.volumes for f in dr.volume_findings(v)}
    assert sev == {"C:": "error", "D:": "warning"}


def test_bitlocker_off_on_system_drive_only(monkeypatch):
    monkeypatch.setenv("SystemDrive", "C:")
    vols = [{"Letter": "C", "Fs": "NTFS", "Health": "Healthy", "Size": 10, "Free": 5, "BitLocker": 2},
            {"Letter": "E", "Fs": "NTFS", "Health": "Healthy", "Size": 10, "Free": 5, "BitLocker": 2}]
    subjects = [f.subject for f in dr.system_findings(_report(volumes=vols))]
    assert subjects == ["C:"]


def test_a_failed_section_is_reported_not_hidden():
    rep = _report([_disk()], volumes_error="Access is denied")
    assert rep.errors["volumes"] == "Access is denied"
    assert any("Could not read volumes" == f.title for f in dr.all_findings(rep))


def test_findings_sorted_errors_first():
    rep = _report([_disk(Wear=75, Health="Unhealthy")])
    sev = [f.severity for f in dr.all_findings(rep)]
    assert sev == sorted(sev, key=lambda s: {"error": 0, "warning": 1, "info": 2}[s])


def test_markdown_lists_drives_and_findings():
    rep = _report([_disk(Wear=95)], [{"Letter": "C", "Fs": "NTFS", "Health": "Healthy",
                                       "Size": 1000, "Free": 500}])
    md = dr.report_to_markdown(rep, "HOST")
    assert "HOST" in md and "Test SSD" in md and "95% of rated life used" in md and "C:" in md


def test_event_findings_group_and_use_the_right_severity():
    from modules.disk_health import disk_events as de
    rep = _report([_disk()])
    rep.events = [
        de.DiskEvent(154, "Error", "2026-09-20 02:23:12", "4", "m", "hardware error"),
        de.DiskEvent(158, "Warning", "2026-09-27 20:28:43", "5", "m", "duplicate ids"),
        de.DiskEvent(158, "Warning", "2026-09-26 08:00:00", "5", "m", "duplicate ids"),
    ]
    findings = dr.event_findings(rep)

    assert len(findings) == 2
    hw = next(f for f in findings if f.subject == "Disk 4")
    assert hw.severity == "error" and "hardware error" in hw.detail
    dup = next(f for f in findings if f.subject == "Disk 5")
    assert dup.severity == "warning" and "x2" in dup.title


def test_a_refused_events_read_is_reported_via_the_generic_errors_path(monkeypatch):
    monkeypatch.setattr(dr, "_run", lambda *a, **k: (0, json.dumps(
        {"disks": [_disk()], "volumes": [], "partitions": []}), ""))
    monkeypatch.setattr("modules.disk_health.disk_events.read_disk_events", lambda: None)
    monkeypatch.setattr(dr, "_attach_smart", lambda rep: None)

    rep = dr.read_disk_report()

    assert rep.errors.get("events") and rep.events == []
    assert any(f.title == "Could not read events" for f in dr.all_findings(rep))


def test_read_disk_report_failure_is_an_exception_not_empty(monkeypatch):
    monkeypatch.setattr(dr, "_run", lambda *a, **k: (1, "", "boom"))
    with pytest.raises(dr.DiskReadError):
        dr.read_disk_report()


def test_real_machine_report_is_plausible():
    try:
        rep = dr.read_disk_report()
    except dr.DiskReadError as e:
        pytest.skip(f"cannot read disks here: {e}")
    real = [d for d in rep.disks if not d.is_virtual]
    assert real, "a real machine has at least one physical disk"
    for d in real:
        assert d.size_bytes > 10 * 1000 ** 3 or d.bus == "USB"
        if d.temperature:
            assert 0 < d.temperature < 120
        if d.wear_percent is not None:
            assert 0 <= d.wear_percent <= 100
    assert any(v.letter for v in rep.volumes)
