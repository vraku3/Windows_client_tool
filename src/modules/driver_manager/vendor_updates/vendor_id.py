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
"""
import re
from typing import Optional

# PCI-SIG vendor IDs, hex, as they appear in a Win32_PnPSignedDriver
# HardWareID string (e.g. "PCI\VEN_10DE&DEV_2684&SUBSYS_88761458").
_PCI_VENDOR_IDS = {
    "10DE": "NVIDIA",
    "1002": "AMD",
    "8086": "Intel",
}

_VEN_RE = re.compile(r"PCI\\VEN_([0-9A-Fa-f]{4})", re.IGNORECASE)


def vendor_for_hardware_id(hardware_id: str) -> Optional[str]:
    """The company name for hardware_id's PCI vendor prefix, or None if
    hardware_id is empty, isn't a recognizable PCI hardware ID shape, or
    names a vendor not in the table yet."""
    if not hardware_id:
        return None
    match = _VEN_RE.match(hardware_id)
    if not match:
        return None
    return _PCI_VENDOR_IDS.get(match.group(1).upper())
