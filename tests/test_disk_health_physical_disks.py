"""physical_disks.py -- Storage Spaces pool members Win32_DiskDrive omits,
and disk_reader's cross-reference that surfaces them as a finding."""
import subprocess
import sys

import pytest

from modules.disk_health import disk_reader as dr
from modules.disk_health import physical_disks as pd


def _pdisk(**over):
    base = dict(device_id="0", friendly_name="Samsung SSD 990 PRO 4TB", serial="S1X2N.",
                media_type="SSD", bus_type="NVMe", health_status="Healthy",
                operational_status="OK", size_gb=3815.0, pool_name=None)
    base.update(over)
    return pd.PhysicalDiskInfo(**base)


# ── parsing ──────────────────────────────────────────────────────────────

def test_parses_a_pooled_disk_and_drops_primordial():
    out = pd._build_physical_disk({
        "DeviceId": "0", "FriendlyName": "Samsung SSD 990 PRO 4TB", "SerialNumber": "S1X2N.",
        "MediaType": "SSD", "BusType": "NVMe", "HealthStatus": "Healthy",
        "OperationalStatus": "OK", "SizeBytes": str(4_000_000_000_000),
        "Pools": "Primordial|Data Pool",
    })
    assert out.pool_name == "Data Pool"


def test_a_disk_only_in_the_primordial_pool_is_not_pooled():
    """Every disk on Windows belongs to the implicit Primordial pool -- that
    alone must never read as real Storage Spaces membership."""
    out = pd._build_physical_disk({
        "DeviceId": "1", "FriendlyName": "Boot NVMe", "SerialNumber": "X.",
        "Pools": "Primordial",
    })
    assert out.pool_name is None


def test_a_disk_with_no_pools_line_is_not_pooled():
    out = pd._build_physical_disk({"DeviceId": "2", "FriendlyName": "USB stick", "Pools": ""})
    assert out.pool_name is None


def test_parse_physical_disks_splits_multiple_records():
    output = (
        "PDISK_START\nDeviceId=0\nFriendlyName=A\nPools=\nPDISK_END\n"
        "PDISK_START\nDeviceId=1\nFriendlyName=B\nPools=Primordial|Pool1\nPDISK_END\n"
    )
    disks = pd._parse_physical_disks(output)
    assert [d.device_id for d in disks] == ["0", "1"]
    assert disks[1].pool_name == "Pool1"


# ── list_physical_disks refusal discipline ──────────────────────────────

def test_a_nonzero_exit_is_reported_as_unavailable_not_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0] if a else [], 1, "", "Access denied"))
    scan = pd.list_physical_disks()
    assert scan.available is False
    assert "Access denied" in scan.reason
    assert scan.disks == []


def test_a_failed_subprocess_launch_is_reported_as_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise OSError("no powershell")
    monkeypatch.setattr(subprocess, "run", _boom)
    scan = pd.list_physical_disks()
    assert scan.available is False
    assert "no powershell" in scan.reason


# ── hidden_from_smart matching ───────────────────────────────────────────

def test_hidden_from_smart_matches_by_serial_not_name_or_index():
    physical = [_pdisk(serial="AAA."), _pdisk(device_id="1", serial="BBB.")]
    hidden = pd.hidden_from_smart(physical, smart_serials=["AAA."])
    assert [d.serial for d in hidden] == ["BBB."]


def test_hidden_from_smart_ignores_blank_and_placeholder_serials():
    physical = [_pdisk(serial="AAA.")]
    hidden = pd.hidden_from_smart(physical, smart_serials=["", "—", None])
    assert len(hidden) == 1


def test_hidden_from_smart_with_no_hidden_disks_is_empty():
    physical = [_pdisk(serial="AAA.")]
    assert pd.hidden_from_smart(physical, smart_serials=["AAA."]) == []


# ── disk_reader integration ──────────────────────────────────────────────

def test_storage_spaces_findings_reports_each_hidden_disk():
    report = dr.DiskReport(hidden_pool_disks=[_pdisk(pool_name="Data Pool")])
    findings = dr.storage_spaces_findings(report)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "Data Pool" in findings[0].title


def test_storage_spaces_findings_empty_when_nothing_hidden():
    assert dr.storage_spaces_findings(dr.DiskReport()) == []


def test_all_findings_includes_hidden_pool_disks():
    report = dr.DiskReport(hidden_pool_disks=[_pdisk(pool_name="Data Pool")])
    titles = [f.title for f in dr.all_findings(report)]
    assert any("Data Pool" in t for t in titles)


def test_attach_crossref_records_a_refusal_as_its_own_error(monkeypatch):
    monkeypatch.setattr(pd, "list_physical_disks",
                         lambda: pd.PhysicalDiskScan(available=False, reason="denied"))
    report = dr.DiskReport()
    dr._attach_storage_spaces_crossref(report)
    assert report.hidden_pool_disks == []
    assert report.errors["storage_spaces"] == "denied"


def test_attach_crossref_a_raised_exception_is_also_its_own_error(monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(pd, "list_physical_disks", _boom)
    report = dr.DiskReport()
    dr._attach_storage_spaces_crossref(report)
    assert "kaboom" in report.errors["storage_spaces"]


def test_attach_crossref_populates_hidden_disks_by_serial(monkeypatch):
    scan = pd.PhysicalDiskScan(available=True, reason="", disks=[
        _pdisk(serial="AAA.", pool_name="Data Pool"),
        _pdisk(device_id="1", serial="BBB.", pool_name=None),
    ])
    monkeypatch.setattr(pd, "list_physical_disks", lambda: scan)
    report = dr.DiskReport()
    report.disks = [dr.PhysicalDiskInfo(device_id="1", name="Boot", serial="BBB.")]
    dr._attach_storage_spaces_crossref(report)
    assert [d.serial for d in report.hidden_pool_disks] == ["AAA."]


# ── real machine ─────────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_real_machine_get_physicaldisk_runs_unelevated():
    """Confirmed live 2026-09-30: Get-PhysicalDisk answers unelevated on this
    machine and lists >= 1 real physical disk. Not asserting the pooled-disk
    count, which is real machine state that can change (drives added/removed,
    pool membership changed)."""
    scan = pd.list_physical_disks()
    if not scan.available:
        pytest.skip(f"Get-PhysicalDisk refused on this machine: {scan.reason}")
    assert len(scan.disks) >= 1
    for d in scan.disks:
        assert d.device_id != ""


def test_a_trailing_dot_or_case_difference_is_still_the_same_disk():
    """Re-measured 2026-10-09: the drives table holds '0025_384C_41C2_4DBF'
    while Get-PhysicalDisk says '0025_384C_41C2_4DBF.'. Compared raw, all
    four disks here were reported 'Not shown above' while shown above."""
    physical = [_pdisk(serial="0025_384C_41C2_4DBF."),
                _pdisk(device_id="2", serial="0000_0000_707c_1800_2522_1E66.")]
    hidden = pd.hidden_from_smart(physical, smart_serials=["0025_384C_41C2_4DBF",
                                                           "0000_0000_707C_1800_2522_1E66"])
    assert hidden == []


def test_a_disk_with_no_serial_is_never_called_hidden():
    """The Msft Virtual Disk has no serial on either side; it cannot be
    matched, so claiming it is missing from the table is a guess."""
    assert pd.hidden_from_smart([_pdisk(serial="")], smart_serials=["AAA"]) == []


def test_real_machine_no_disk_in_the_drives_table_is_called_hidden():
    from modules.disk_health import disk_reader
    rep = disk_reader.read_disk_report()
    in_table = {pd._serial_key(d.serial) for d in rep.disks}
    wrongly_hidden = [h.friendly_name for h in rep.hidden_pool_disks
                      if pd._serial_key(h.serial) in in_table]
    assert wrongly_hidden == []
