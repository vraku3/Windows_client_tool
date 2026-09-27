"""USB and PCI device inventory. No Qt.

`Win32_PnPEntity` is the one query that lists BOTH kinds without walking the
device tree by hand. USB composite children (`...&MI_xx` suffixes) are folded
under their parent so a webcam does not appear as four rows; the vendor name
reuses Driver Manager's own PCI/USB-IF tables rather than a second one.
"""
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from modules.driver_manager.vendor_updates.vendor_id import vendor_for_hardware_id

logger = logging.getLogger(__name__)

_COMPOSITE_CHILD = re.compile(r"&MI_\d+")
_PCI_IDS = re.compile(r"VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", re.I)
_USB_IDS = re.compile(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.I)

#: A PnP class that IS the bus itself, not a device hanging off it -- USB host
#: controllers and hubs enumerate under USB\ with class "USB", and listing
#: them as "devices" is noise (31 of them here).
_USB_BUS_CLASSES = {"USB"}


@dataclass(frozen=True)
class Device:
    name: str
    vendor: str
    pnp_class: str
    device_id: str
    status: str
    vendor_id: Optional[str] = None
    product_id: Optional[str] = None

    @property
    def is_problem(self) -> bool:
        return self.status not in ("OK", "")


def _vendor(device_id: str, name: str, manufacturer: str) -> str:
    known = vendor_for_hardware_id(device_id, name)
    if known:
        return known
    text = (manufacturer or "").strip()
    return "" if text.lower() in ("", "(standard system devices)", "(standard usb host controller)",
                                  "(standard disk drives)") else text


def read_usb_devices() -> Optional[List[Device]]:
    """Real USB peripherals, one row per physical device. None if WMI refused."""
    try:
        import wmi
        entities = wmi.WMI().Win32_PnPEntity()
    except Exception as e:
        logger.warning("USB device read failed: %s", e)
        return None
    found = []
    for d in entities:
        pid = str(d.PNPDeviceID or "")
        if not pid.startswith("USB") or _COMPOSITE_CHILD.search(pid):
            continue
        pnp_class = d.PNPClass or ("USB" if pid.startswith("USB4") else "")
        if pnp_class in _USB_BUS_CLASSES:
            continue
        ids = _USB_IDS.search(pid)
        found.append(Device(
            name=(d.Name or "").strip(), vendor=_vendor(pid, d.Name or "", d.Manufacturer or ""),
            pnp_class=pnp_class or "Unknown", device_id=pid, status=d.Status or "",
            vendor_id=ids.group(1).upper() if ids else None,
            product_id=ids.group(2).upper() if ids else None))
    return sorted(found, key=lambda x: x.name.lower())


def read_pci_devices() -> Optional[List[Device]]:
    """Every PCI/PCIe function: chipset, GPU, storage and network controllers."""
    try:
        import wmi
        entities = wmi.WMI().Win32_PnPEntity()
    except Exception as e:
        logger.warning("PCI device read failed: %s", e)
        return None
    found = []
    for d in entities:
        pid = str(d.PNPDeviceID or "")
        if not pid.startswith("PCI"):
            continue
        ids = _PCI_IDS.search(pid)
        found.append(Device(
            name=(d.Name or "").strip(), vendor=_vendor(pid, d.Name or "", d.Manufacturer or ""),
            pnp_class=d.PNPClass or "Unknown", device_id=pid, status=d.Status or "",
            vendor_id=ids.group(1).upper() if ids else None,
            product_id=ids.group(2).upper() if ids else None))
    return sorted(found, key=lambda x: (x.pnp_class, x.name.lower()))


def problem_devices(usb: Optional[List[Device]], pci: Optional[List[Device]]) -> List[Device]:
    """Anything Windows itself flags as not OK, from either list."""
    return [d for d in (usb or []) + (pci or []) if d.is_problem]
