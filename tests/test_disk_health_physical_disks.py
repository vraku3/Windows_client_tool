"""physical_disks.py cross-references Get-PhysicalDisk against the
Win32_DiskDrive-based SMART scan disk_health_module.py already runs.

Measured live on this machine (2026-09-30): Win32_DiskDrive lists 5 entries
(indexes 1, 3, 4, 5, 6) and never mentions the Samsung SSD 990 PRO 4TB or the
ADATA LEGEND 960 -- both real NVMe drives, both Storage Spaces pool members.
Get-PhysicalDisk lists all four real disks (device ids 0, 1, 2, 6), and
Get-StoragePool is what tells a real pool membership (a named pool) apart
from every disk's implicit "Primordial" membership.
"""
import sys

from PyQt6.QtWidgets import QApplication

QApplication.instance() or QApplication(sys.argv)

from modules.disk_health.disk_health_module import _query_disks  # noqa: E402
from modules.disk_health.physical_disks import (  # noqa: E402
    PhysicalDiskInfo, hidden_from_smart, list_physical_disks, _parse_physical_disks,
)

_SAMPLE_OUTPUT = """
PDISK_START
DeviceId=0
FriendlyName=Samsung SSD 990 PRO 4TB
SerialNumber=0025_384C_41C2_4DBF.
MediaType=SSD
BusType=NVMe
HealthStatus=Healthy
OperationalStatus=OK
SizeBytes=4000787030016
Pools=Storage pool|Primordial
PDISK_END
PDISK_START
DeviceId=1
FriendlyName=Samsung SSD 990 PRO 2TB
SerialNumber=0025_384C_4141_4C79.
MediaType=SSD
BusType=NVMe
HealthStatus=Healthy
OperationalStatus=OK
SizeBytes=2000398934016
Pools=Primordial
PDISK_END
"""


def test_parses_pool_membership_and_filters_out_primordial():
    """Every disk is nominally in "Primordial" -- that is Windows' reservoir
    of unallocated disks, not a real Storage Spaces pool, and must not be
    reported as if it were one."""
    disks = _parse_physical_disks(_SAMPLE_OUTPUT)
    assert len(disks) == 2

    pooled, unpooled = disks[0], disks[1]
    assert pooled.device_id == "0"
    assert pooled.pool_name == "Storage pool"

    assert unpooled.device_id == "1"
    assert unpooled.pool_name is None, "Primordial-only membership must read as unpooled"


def test_parses_size_and_identity_fields():
    disks = _parse_physical_disks(_SAMPLE_OUTPUT)
    d = disks[0]
    assert d.friendly_name == "Samsung SSD 990 PRO 4TB"
    assert d.serial == "0025_384C_41C2_4DBF."
    assert d.size_gb == round(4000787030016 / (1024 ** 3), 1)


def test_a_refusal_is_never_an_empty_list_of_disks():
    """`_parse_physical_disks` on genuinely empty output IS an empty list --
    that distinction belongs to `list_physical_disks`, which sets
    `available=False` on a real refusal rather than reporting zero disks."""
    assert _parse_physical_disks("") == []


def test_hidden_from_smart_finds_disks_the_smart_scan_never_mentioned():
    physical = [
        PhysicalDiskInfo("0", "Samsung SSD 990 PRO 4TB", "SERIAL-A", "SSD",
                          "NVMe", "Healthy", "OK", 4000.0, "Storage pool"),
        PhysicalDiskInfo("1", "Samsung SSD 990 PRO 2TB", "SERIAL-B", "SSD",
                          "NVMe", "Healthy", "OK", 2000.0, None),
    ]
    # The SMART scan only ever saw SERIAL-B.
    hidden = hidden_from_smart(physical, ["SERIAL-B"])
    assert [d.serial for d in hidden] == ["SERIAL-A"]


def test_hidden_from_smart_ignores_the_unknown_serial_placeholder():
    """`_query_disks()` reports a missing serial as the em-dash placeholder
    ("—"), never as a real value -- that must not accidentally "match" a
    physical disk that also failed to report a serial."""
    physical = [
        PhysicalDiskInfo("0", "No Serial Drive", "", "SSD", "NVMe",
                          "Healthy", "OK", 100.0, None),
    ]
    hidden = hidden_from_smart(physical, ["—"])
    assert len(hidden) == 1


# ---------------------------------------------------------------------------
# Real machine
# ---------------------------------------------------------------------------

def test_this_real_machine_has_disks_storage_spaces_hides_from_smart():
    """The whole reason this module exists: on the box this was built and
    tested on, Get-PhysicalDisk sees 4 real physical disks and the
    Win32_DiskDrive-based SMART scan sees only some of them, because two are
    Storage Spaces pool members. This is a live, unelevated read of this
    exact machine -- not a mock."""
    scan = list_physical_disks()
    assert scan.available, f"Get-PhysicalDisk was refused: {scan.reason}"
    assert len(scan.disks) >= 4, (
        f"expected at least 4 physical disks on this machine, got "
        f"{[d.friendly_name for d in scan.disks]}")

    smart_disks = _query_disks()
    smart_serials = [d.serial for d in smart_disks]
    hidden = hidden_from_smart(scan.disks, smart_serials)

    assert len(hidden) >= 2, (
        "expected at least 2 physical disks hidden from the SMART scan by "
        f"Storage Spaces pooling on this machine, found: "
        f"{[d.friendly_name for d in hidden]} (smart saw: "
        f"{[d.friendly_name for d in smart_disks]})")
    # At least one of the hidden disks must show real (non-Primordial) pool
    # membership -- that is what makes it hidden, not a coincidence.
    assert any(d.pool_name for d in hidden), (
        "at least one hidden disk should show a real Storage Spaces pool name")
