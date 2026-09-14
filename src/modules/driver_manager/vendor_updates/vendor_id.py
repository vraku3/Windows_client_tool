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

Realtek (0x10EC) and MediaTek (0x14C3) were added after a real-machine
sweep found three Realtek NICs (onboard 5GbE, two USB dongles) and a
MediaTek WiFi7+Bluetooth combo (RZ717) all reporting UNRECOGNIZED_VENDOR
-- recognized here even before either has a registered provider (see
provider.py), so the UI can say "Realtek detected, no update source yet"
rather than "unknown vendor" for hardware this table now knows about.

That first pass only added PCI-SIG ids, though, and both of this
machine's two USB Realtek NICs are USB devices
("USB\\VID_0BDA&PID_8153&REV_3100") -- confirmed by testing against the
REAL update-check harness after the PCI fix, not assumed. USB vendor ids
are a genuinely separate registry (USB-IF's, not PCI-SIG's) -- 0x0BDA
happens to also be Realtek's, by coincidence of two different standards
bodies both assigning them an id, not because the two registries share
numbering. Kept as its own table and regex, matched only against a
USB\\ prefix, specifically so it's never confused with or merged into
the PCI table (a prior edit to this file briefly widened the PCI regex
to also match USB\\, then reverted it for exactly this reason -- USB and
PCI vendor ids for the SAME company are not guaranteed or even likely to
share a numeric value in general, 0x0BDA/0x10EC being coincidental).

A full real-machine sweep (every distinct vendor `fetch_drivers()`
returns here, 2026-09-14) found five more real, non-generic vendors:
Razer, SteelSeries, Dell, LG, and a SunplusIT-made Lenovo accessory.
Razer and Lenovo are recognized here (see the tables below); Dell and
LG deliberately are NOT, and SteelSeries is recognized but will never
get a provider -- see provider.py's module docstring or the relevant
git commit for why each of those three is a genuine dead end rather
than something left unexplored.
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
    "10EC": "Realtek",   # real machine data: onboard 5GbE NIC
    "14C3": "MediaTek",  # real machine data: RZ717 WiFi 7 + Bluetooth combo
}

# USB-IF vendor ids -- a SEPARATE registry from PCI-SIG's (see this
# module's docstring). Only ever matched against a USB\ prefix.
_USB_VENDOR_IDS = {
    "0BDA": "Realtek",   # real machine data: both USB NIC dongles report this
                         # (also several unrelated "Generic USB Hub"
                         # entries -- Realtek makes hub controller chips
                         # too; that's a device-TYPE question for the
                         # provider to gate on, not a vendor-id question)
    "0E8D": "MediaTek",  # real machine data: RZ717 Bluetooth adapter
                         # (publisher field independently confirms
                         # "Mediatek Inc." for this exact device)
    "1532": "Razer",     # real machine data: Razer Naga Pro
    "17EF": "Lenovo",    # real machine data: "Lenovo 500 IR/RGB Camera" --
                         # this is LENOVO'S OWN USB-IF id, not the camera
                         # chip maker's (WMI's publisher field says
                         # "SunplusIT", the OEM silicon vendor, but 0x17EF
                         # belongs to Lenovo -- the real distribution
                         # channel for this accessory is Lenovo's own
                         # support site, never a chip maker with no
                         # direct-to-consumer presence at all).
}

_PCI_VEN_RE = re.compile(r"PCI\\VEN_([0-9A-Fa-f]{4})", re.IGNORECASE)
_USB_VID_RE = re.compile(r"USB\\VID_([0-9A-Fa-f]{4})", re.IGNORECASE)

# Last-resort fallback for devices with no PCI/USB vendor id to read at
# all (ACPI-enumerated ones, chiefly the CPU itself) -- ordered so a name
# containing more than one of these (unlikely) resolves to the first
# match, not last-write-wins from a dict.
_DEVICE_NAME_FALLBACKS = (
    ("amd", "AMD"),
    ("nvidia", "NVIDIA"),
    ("intel", "Intel"),
    ("realtek", "Realtek"),
    ("mediatek", "MediaTek"),
    # "SteelSeries GG Component Device" (hardware_id "SWC\VEN_SSGG&IID_0100")
    # is not a PCI or USB device at all -- "SWC" is a software-component
    # pseudo-bus their own GG app registers itself under, real machine
    # data confirmed. No new bus-prefix table for one non-hardware case;
    # the name fallback already exists for exactly this shape.
    ("steelseries", "SteelSeries"),
)


def vendor_for_hardware_id(hardware_id: str, device_name: str = "") -> Optional[str]:
    """The company name for hardware_id's PCI or USB vendor prefix, or --
    only when hardware_id carries neither -- a name-based guess from
    device_name (see this module's docstring for why that's sometimes
    the only signal that exists). None if hardware_id is empty, none of
    the above recognizes anything, or the vendor isn't in a table yet."""
    if hardware_id:
        pci_match = _PCI_VEN_RE.match(hardware_id)
        if pci_match:
            return _PCI_VENDOR_IDS.get(pci_match.group(1).upper())
        usb_match = _USB_VID_RE.match(hardware_id)
        if usb_match:
            return _USB_VENDOR_IDS.get(usb_match.group(1).upper())
    name_lower = device_name.lower()
    for needle, vendor in _DEVICE_NAME_FALLBACKS:
        if needle in name_lower:
            return vendor
    return None
