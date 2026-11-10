import pytest
from modules.disk_health import disk_reader as dr


def test_volume_findings_low_system_drive_headroom(monkeypatch):
    monkeypatch.setenv("SystemDrive", "C:")
    
    # 15 GB free on a 100 GB system drive (15% free, but < 20 GB absolute)
    vol = dr.VolumeInfo(
        letter="C",
        label="Windows",
        filesystem="NTFS",
        health="Healthy",
        size_bytes=100 * 1024**3,
        free_bytes=15 * 1024**3,
    )
    findings = dr.volume_findings(vol)
    assert any("Low system drive headroom" in f.title for f in findings)
    headroom_finding = next(f for f in findings if "Low system drive headroom" in f.title)
    assert headroom_finding.severity == "warning"
    assert "20 GB" in headroom_finding.detail


def test_volume_findings_sufficient_system_drive_headroom(monkeypatch):
    monkeypatch.setenv("SystemDrive", "C:")
    
    # 50 GB free on a 200 GB system drive (25% free, > 20 GB absolute)
    vol = dr.VolumeInfo(
        letter="C",
        label="Windows",
        filesystem="NTFS",
        health="Healthy",
        size_bytes=200 * 1024**3,
        free_bytes=50 * 1024**3,
    )
    findings = dr.volume_findings(vol)
    assert not any("Low system drive headroom" in f.title for f in findings)


def test_volume_findings_non_system_drive_ignores_headroom_rule(monkeypatch):
    monkeypatch.setenv("SystemDrive", "C:")
    
    # 15 GB free on a 100 GB data drive D: (15% free, < 20 GB)
    vol = dr.VolumeInfo(
        letter="D",
        label="Data",
        filesystem="NTFS",
        health="Healthy",
        size_bytes=100 * 1024**3,
        free_bytes=15 * 1024**3,
    )
    findings = dr.volume_findings(vol)
    assert not any("Low system drive headroom" in f.title for f in findings)


def test_read_disk_report_falls_back_to_win32_volumes(monkeypatch):
    # Simulate PowerShell returning disks but volumes failed with CIM error
    fake_json = '{"disks": [{"DeviceId": "0", "Name": "Test Disk", "Size": 1000000000}], "volumes": [], "volumes_error": "Access to a CIM resource was not available."}'
    monkeypatch.setattr(dr, "_run", lambda args, timeout: (0, fake_json, ""))
    
    mock_volumes = [
        dr.VolumeInfo(
            letter="C",
            label="OS",
            filesystem="NTFS",
            health="Healthy",
            size_bytes=500 * 1024**3,
            free_bytes=200 * 1024**3,
            bitlocker="Off",
        )
    ]
    monkeypatch.setattr(dr, "_read_volumes_win32", lambda: mock_volumes)
    monkeypatch.setattr(dr, "_attach_storage_spaces_crossref", lambda rep: None)
    monkeypatch.setattr(dr.disk_events, "read_disk_events", lambda: [])

    rep = dr.read_disk_report()
    assert len(rep.volumes) == 1
    assert rep.volumes[0].letter == "C"
    # Ensure volumes_error was cleared because fallback succeeded
    assert "volumes" not in rep.errors


def test_read_volumes_win32_live_smoke():
    # If run on Windows, should return a list of VolumeInfo
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows only")
    vols = dr._read_volumes_win32()
    assert isinstance(vols, list)
    if vols:
        first = vols[0]
        assert isinstance(first, dr.VolumeInfo)
        assert first.letter != ""
        assert first.size_bytes > 0
        assert first.health == "Healthy"

