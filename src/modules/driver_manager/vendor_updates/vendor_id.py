"""PCI vendor ID -> company name, for identifying which vendor made a
device from its hardware_id -- never from DriverInfo.publisher, which WMI
reports inconsistently (often just "Microsoft" for the driver class, not
the silicon vendor).

Static table: PCI-SIG vendor IDs are permanent and don't change, so this
is not a live lookup. Recognizing a vendor here does NOT mean a provider
exists for it (see provider.py) -- the two are deliberately separate, so
the UI can say "AMD detected, no update source configured yet" rather
than "unknown vendor" for a vendor this table knows about but Phase 1/2
hasn't built an adapter for.

Real machine data caught two gaps in the first pass of this table:
1. AMD ships PCI devices under BOTH 0x1002 (the ATI heritage ID, used for
   GPUs and GPU-attached audio) and 0x1022 (AMD's own ID, used for the
   platform security processor, SMBUS, and other chipset devices) --
   missing 0x1022 meant every non-GPU AMD chipset device on a real Ryzen
   system reported UNRECOGNIZED_VENDOR.
2. A CPU is not a PCI device. Windows exposes it as a generic ACPI
   processor object (real example: "AMD Processor" with hardware_id
   "ACPI\\VEN_ACPI&DEV_0007") -- the VEN token is the literal string
   "ACPI", not a real vendor ID, because the ACPI processor driver model
   carries no per-vendor identification at all. There is no PCI vendor ID
   to extract here, ever -- the device NAME is the only signal available,
   so device-name substring matching is the deliberate, documented
   fallback for exactly this shape, used only when no PCI vendor ID could
   be found at all (never as an override of a real one).
"""
import re
from typing import Optional

# PCI-SIG vendor IDs, hex, as they appear in a Win32_PnPSignedDriver
# HardWareID string (e.g. "PCI\VEN_10DE&DEV_2684&SUBSYS_88761458").
_PCI_VENDOR_IDS = {
    "10DE": "NVIDIA",
    "1002": "AMD",   # ATI heritage ID: GPUs, GPU-attached audio
    "1022": "AMD",   # AMD's own ID: platform security processor, SMBUS, chipset
    "8086": "Intel",
}

_VEN_RE = re.compile(r"PCI\\VEN_([0-9A-Fa-f]{4})", re.IGNORECASE)

# Last-resort fallback for devices with no PCI vendor ID to read at
# all (ACPI-enumerated ones, chiefly the CPU itself) -- ordered so a name
# containing more than one of these (unlikely) resolves to the first
# match, not last-write-wins from a dict.
_DEVICE_NAME_FALLBACKS = (
    ("amd", "AMD"),
    ("nvidia", "NVIDIA"),
    ("intel", "Intel"),
)


def vendor_for_hardware_id(hardware_id: str, device_name: str = "") -> Optional[str]:
    """The company name for hardware_id's PCI vendor prefix, or --
    only when hardware_id carries no such prefix at all -- a name-based
    guess from device_name (see this module's docstring for why that's
    sometimes the only signal that exists). None if hardware_id is empty,
    neither approach recognizes anything, or the vendor isn't in the
    table yet."""
    if hardware_id:
        match = _VEN_RE.match(hardware_id)
        if match:
            return _PCI_VENDOR_IDS.get(match.group(1).upper())
    name_lower = device_name.lower()
    for needle, vendor in _DEVICE_NAME_FALLBACKS:
        if needle in name_lower:
            return vendor
    return None
