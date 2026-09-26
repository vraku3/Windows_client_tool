"""Pure parsing and formatting for the hardware asset views (no Qt, no I/O).

Everything here takes plain values so it can be tested with no display, no
WMI and no registry.  The readers that fetch the values live in
``asset_reader.py``.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# EDID
# --------------------------------------------------------------------------

_EDID_HEADER = bytes([0, 255, 255, 255, 255, 255, 255, 0])


@dataclass
class MonitorRecord:
    manufacturer_id: str = ""      # three-letter PnP id, e.g. "GBT"
    product_code: int = 0
    name: str = ""                 # descriptor 0xFC ("MO27Q28G")
    serial: str = ""               # descriptor 0xFF, else numeric serial
    week: int = 0
    year: int = 0
    width_cm: int = 0
    height_cm: int = 0
    instance_path: str = ""
    active: Optional[bool] = None  # None = could not tell

    @property
    def manufactured(self) -> str:
        if not self.year:
            return ""
        return f"{self.year}-W{self.week:02d}" if self.week else str(self.year)

    @property
    def diagonal_inches(self) -> Optional[float]:
        if not (self.width_cm and self.height_cm):
            return None
        return round(((self.width_cm ** 2 + self.height_cm ** 2) ** 0.5) / 2.54, 1)

    @property
    def make(self) -> str:
        return PNP_VENDORS.get(self.manufacturer_id, self.manufacturer_id)


# Only ids seen on monitors; an unknown id is shown as its three letters.
PNP_VENDORS = {
    "GBT": "Gigabyte", "DEL": "Dell", "GSM": "LG", "SAM": "Samsung",
    "ACR": "Acer", "AUS": "ASUS", "BNQ": "BenQ", "LEN": "Lenovo",
    "HWP": "HP", "MSI": "MSI", "AOC": "AOC", "PHL": "Philips",
    "VSC": "ViewSonic", "SNY": "Sony", "NEC": "NEC", "IVM": "Iiyama",
    "APP": "Apple", "MEI": "Panasonic", "CMN": "Chimei Innolux",
    "BOE": "BOE", "AUO": "AU Optronics", "LGD": "LG Display",
    "SDC": "Samsung Display", "HKC": "HKC", "KOG": "Koorui",
}


def _descriptor_text(block: bytes) -> str:
    """Text of an 18-byte EDID descriptor.

    Not always newline-terminated: some panels fill all 13 bytes and end with
    NUL, and ``str.strip()`` does not remove NUL.  Terminate on both.
    """
    raw = block[5:18]
    for stop in (b"\n", b"\x00"):
        cut = raw.find(stop)
        if cut != -1:
            raw = raw[:cut]
    text = raw.decode("cp437", errors="replace")
    return "".join(ch for ch in text if ch.isprintable()).strip()


def parse_edid(edid: bytes, instance_path: str = "") -> Optional[MonitorRecord]:
    """Decode a base EDID block.  None when it is not an EDID at all."""
    if not edid or len(edid) < 128 or bytes(edid[:8]) != _EDID_HEADER:
        return None
    # Manufacturer id is BIG-endian, three 5-bit letters ('A' == 1).
    word = (edid[8] << 8) | edid[9]
    letters = [(word >> shift) & 0x1F for shift in (10, 5, 0)]
    mfg = "".join(chr(ord("A") + v - 1) if 1 <= v <= 26 else "?" for v in letters)
    # Product code is LITTLE-endian.
    product = edid[10] | (edid[11] << 8)
    numeric_serial = int.from_bytes(edid[12:16], "little")
    week, year_off = edid[16], edid[17]
    rec = MonitorRecord(
        manufacturer_id=mfg,
        product_code=product,
        week=week if 1 <= week <= 54 else 0,
        year=1990 + year_off if year_off else 0,
        width_cm=edid[21],
        height_cm=edid[22],
        instance_path=instance_path,
    )
    text_serial = ""
    for off in (54, 72, 90, 108):
        block = bytes(edid[off:off + 18])
        if block[:3] != b"\x00\x00\x00":
            continue
        tag = block[3]
        if tag == 0xFC:
            rec.name = _descriptor_text(block)
        elif tag == 0xFF:
            text_serial = _descriptor_text(block)
    rec.serial = text_serial or (str(numeric_serial) if numeric_serial else "")
    return rec


# --------------------------------------------------------------------------
# Memory slot map
# --------------------------------------------------------------------------

_MEM_TYPES = {
    17: "SDRAM", 20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4",
    27: "LPDDR", 28: "LPDDR2", 29: "LPDDR3", 30: "LPDDR4", 34: "DDR5",
    35: "LPDDR5",
}
_FORM_FACTORS = {8: "DIMM", 12: "SODIMM", 13: "SRIMM", 9: "RIMM", 11: "Row of chips"}
_ECC_MODES = {
    3: "None", 4: "Parity", 5: "Single-bit ECC", 6: "Multi-bit ECC", 7: "CRC",
}


def memory_type_name(code) -> str:
    try:
        return _MEM_TYPES.get(int(code), f"type {int(code)}")
    except (TypeError, ValueError):
        return "unknown"


def form_factor_name(code) -> str:
    try:
        return _FORM_FACTORS.get(int(code), f"form {int(code)}")
    except (TypeError, ValueError):
        return "unknown"


def ecc_mode_name(code) -> str:
    try:
        return _ECC_MODES.get(int(code), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


@dataclass
class MemorySlot:
    locator: str
    bank: str = ""
    populated: bool = True
    capacity_bytes: int = 0
    mem_type: str = ""
    form_factor: str = ""
    rated_mhz: Optional[int] = None
    configured_mhz: Optional[int] = None
    manufacturer: str = ""
    part_number: str = ""
    serial: str = ""
    ecc_bits: bool = False   # TotalWidth > DataWidth: extra ECC bits on the module

    @property
    def speed_note(self) -> str:
        if not self.rated_mhz or not self.configured_mhz:
            return ""
        if self.configured_mhz > self.rated_mhz:
            return "running above the rated speed (XMP/EXPO profile)"
        if self.configured_mhz < self.rated_mhz:
            return "running below the rated speed"
        return "at rated speed"


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_slot_map(sticks: Sequence[dict], total_slots: Optional[int]) -> List[MemorySlot]:
    """Populated modules first, then one 'Empty' row per unused slot.

    ``total_slots`` None means Win32_PhysicalMemoryArray could not be read; in
    that case no empty rows are invented.
    """
    slots: List[MemorySlot] = []
    for st in sticks:
        cap = _to_int(st.get("Capacity")) or 0
        total_w, data_w = _to_int(st.get("TotalWidth")), _to_int(st.get("DataWidth"))
        slots.append(MemorySlot(
            locator=str(st.get("DeviceLocator") or st.get("BankLabel") or "?").strip(),
            bank=str(st.get("BankLabel") or "").strip(),
            capacity_bytes=cap,
            mem_type=memory_type_name(st.get("SMBIOSMemoryType")),
            form_factor=form_factor_name(st.get("FormFactor")),
            rated_mhz=_to_int(st.get("Speed")),
            configured_mhz=_to_int(st.get("ConfiguredClockSpeed")),
            manufacturer=str(st.get("Manufacturer") or "").strip(),
            part_number=str(st.get("PartNumber") or "").strip(),
            serial=str(st.get("SerialNumber") or "").strip(),
            ecc_bits=bool(total_w and data_w and total_w > data_w),
        ))
    if total_slots is not None:
        for n in range(max(0, total_slots - len(slots))):
            slots.append(MemorySlot(locator=f"Empty slot {n + 1}", populated=False))
    return slots


# --------------------------------------------------------------------------
# Battery
# --------------------------------------------------------------------------

@dataclass
class BatteryHealth:
    design_mwh: Optional[int]
    full_charge_mwh: Optional[int]
    cycle_count: Optional[int] = None

    @property
    def wear_percent(self) -> Optional[float]:
        if not self.design_mwh or self.full_charge_mwh is None:
            return None
        return round(max(0.0, 100.0 * (1 - self.full_charge_mwh / self.design_mwh)), 1)

    @property
    def health_percent(self) -> Optional[float]:
        wear = self.wear_percent
        return None if wear is None else round(100.0 - wear, 1)


# --------------------------------------------------------------------------
# Warranty helper (text only; no network calls)
# --------------------------------------------------------------------------

def warranty_lookup(manufacturer: str, serial: str, model: str = "") -> Tuple[str, str]:
    """(vendor, URL) for the vendor's warranty page, or ("", "").

    The tool never contacts the vendor: it hands the user the link, and the
    serial is only interpolated where the vendor's own URL scheme takes it.
    A placeholder serial ("To be filled by O.E.M.") gets no link.
    """
    m = (manufacturer or "").lower()
    s = (serial or "").strip()
    usable = bool(s) and not is_placeholder(s)
    q = quote(s, safe="") if usable else ""
    if "dell" in m and usable:
        return ("Dell", f"https://www.dell.com/support/home/en-us/product-support/servicetag/{q}/overview")
    if "lenovo" in m and usable:
        return ("Lenovo", f"https://pcsupport.lenovo.com/us/en/search?query={q}")
    if m.startswith("hp") or "hewlett" in m:
        return ("HP", "https://support.hp.com/us-en/checkwarranty")
    if "asus" in m:
        return ("ASUS", "https://www.asus.com/support/warranty-status-inquiry/")
    if "acer" in m:
        return ("Acer", "https://www.acer.com/us-en/support/warranty")
    if "microsoft" in m:
        return ("Microsoft", "https://support.microsoft.com/en-us/devices/register-a-device")
    if "msi" in m or "micro-star" in m:
        return ("MSI", "https://www.msi.com/support/warranty")
    if "gigabyte" in m:
        return ("Gigabyte", "https://www.gigabyte.com/Support/Warranty")
    if "asrock" in m:
        return ("ASRock", "https://www.asrock.com/support/index.asp")
    return ("", "")


_PLACEHOLDERS = (
    "to be filled", "default string", "not specified", "none", "n/a",
    "system serial number", "0123456789", "123456789", "o.e.m",
)


def is_placeholder(value: str) -> bool:
    """SMBIOS fields a board maker never filled in."""
    v = (value or "").strip().lower()
    if not v:
        return True
    if set(v) <= {"0", " ", "-", "_", "."}:
        return True
    return any(p in v for p in _PLACEHOLDERS)


# --------------------------------------------------------------------------
# Firmware / security summary and findings
# --------------------------------------------------------------------------

@dataclass
class FirmwareInfo:
    """Every field is None when the machine refused or lacks the answer."""
    firmware_mode: Optional[str] = None       # "UEFI" | "Legacy BIOS"
    secure_boot: Optional[bool] = None
    secure_boot_reason: str = ""              # why secure_boot is None / False
    virtualization: Optional[bool] = None
    tpm_present: Optional[bool] = None
    tpm_version: str = ""
    tpm_reason: str = ""
    bios_date: Optional[date] = None
    bios_version: str = ""


@dataclass
class Finding:
    severity: str    # "error" | "warning" | "info"
    title: str
    detail: str


def firmware_findings(fw: FirmwareInfo, today: Optional[date] = None) -> List[Finding]:
    today = today or date.today()
    out: List[Finding] = []
    if fw.firmware_mode == "Legacy BIOS":
        out.append(Finding("warning", "Legacy BIOS boot",
                           "Windows 11 requires UEFI; this machine boots in legacy mode."))
    if fw.secure_boot is False:
        out.append(Finding("warning", "Secure Boot is off",
                           fw.secure_boot_reason or "Enable Secure Boot in firmware setup "
                           "(required by Windows 11 and most anti-cheat / BitLocker policies)."))
    if fw.tpm_present is False:
        out.append(Finding("warning", "No TPM detected",
                           fw.tpm_reason or "Windows 11 and BitLocker device encryption need a TPM 2.0."))
    elif fw.tpm_present and fw.tpm_version and not fw.tpm_version.startswith("2"):
        out.append(Finding("warning", "TPM is not version 2.0", f"Reported spec version: {fw.tpm_version}."))
    if fw.virtualization is False:
        out.append(Finding("info", "Hardware virtualization is off in firmware",
                           "Hyper-V, WSL2 and Windows Sandbox need it enabled."))
    if fw.bios_date and (today - fw.bios_date).days > 5 * 365:
        years = (today - fw.bios_date).days // 365
        out.append(Finding("info", "Firmware is more than 5 years old",
                           f"BIOS dated {fw.bios_date.isoformat()} ({years} years)."))
    for label, val, reason in (("Secure Boot", fw.secure_boot, fw.secure_boot_reason),
                               ("TPM", fw.tpm_present, fw.tpm_reason)):
        if val is None:
            out.append(Finding("info", f"{label} state could not be read", reason or "Read was refused."))
    return out


def memory_findings(slots: Sequence[MemorySlot]) -> List[Finding]:
    out: List[Finding] = []
    filled = [s for s in slots if s.populated]
    if not filled:
        return out
    caps = {s.capacity_bytes for s in filled}
    if len(caps) > 1:
        out.append(Finding("info", "Mixed module sizes",
                           "Populated modules differ in capacity; dual-channel may be reduced."))
    speeds = {s.configured_mhz for s in filled if s.configured_mhz}
    if len(speeds) > 1:
        out.append(Finding("warning", "Modules run at different speeds",
                           "All modules are clocked to the slowest on most boards."))
    empty = sum(1 for s in slots if not s.populated)
    if empty:
        out.append(Finding("info", f"{empty} empty memory slot(s)", "Capacity can be expanded."))
    for s in filled:
        note = s.speed_note
        if note.startswith("running below"):
            out.append(Finding("warning", f"{s.locator} {note}",
                               f"Rated {s.rated_mhz} MHz, configured {s.configured_mhz} MHz."))
    return out


# --------------------------------------------------------------------------
# Asset record (one flat, exportable dict)
# --------------------------------------------------------------------------

def _fmt_gb(n: int) -> str:
    return f"{n / 1024 ** 3:.0f} GB" if n else ""


_VIRTUAL_DISK_MARKERS = ("virtual disk", "storage space", "virtual hd")


def physical_disks_only(disks: Sequence[dict]) -> List[dict]:
    """Drop Hyper-V/VHD/Storage Spaces stand-ins, empty card readers and
    duplicate rows (a USB card reader appears once per slot)."""
    out: List[dict] = []
    seen = set()
    for d in disks:
        model = str(d.get("Model", "")).lower()
        if any(mk in model for mk in _VIRTUAL_DISK_MARKERS):
            continue
        if str(d.get("Size", "N/A")) in ("N/A", ""):
            continue
        key = (model, str(d.get("Serial", "")))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _monitor_label(m: "MonitorRecord") -> str:
    name = m.name or "unnamed"
    label = name if name.lower().startswith(m.make.lower()) else f"{m.make} {name}"
    return f"{label} (SN {m.serial or '?'}, {m.manufactured or 'date ?'})"


def build_asset_record(*, hostname: str, manufacturer: str, model: str, serial: str,
                       cpu: str, ram_bytes: int, slots: Sequence[MemorySlot],
                       disks: Sequence[dict], monitors: Sequence[MonitorRecord],
                       os_name: str, bios: str, fw: FirmwareInfo,
                       generated: Optional[str] = None) -> Dict[str, str]:
    """Ordered flat mapping of asset field -> text; blank when unknown."""
    vendor, url = warranty_lookup(manufacturer, serial, model)
    rec: Dict[str, str] = {
        "Hostname": hostname,
        "Manufacturer": manufacturer,
        "Model": model,
        "Serial number": "" if is_placeholder(serial) else serial,
        "Operating system": os_name,
        "CPU": cpu,
        "RAM": _fmt_gb(ram_bytes),
        "RAM modules": "; ".join(
            f"{s.locator}: {_fmt_gb(s.capacity_bytes)} {s.mem_type} {s.configured_mhz or ''}MHz".strip()
            for s in slots if s.populated),
        "Storage": "; ".join(
            f"{d.get('Model', '?')} ({d.get('Size', '?')}, SN {d.get('Serial', '?')})"
            for d in physical_disks_only(disks)),
        "Monitors": "; ".join(_monitor_label(m) for m in monitors),
        "Firmware": bios,
        "Boot mode": fw.firmware_mode or "unknown",
        "Secure Boot": _tri(fw.secure_boot),
        "TPM": (fw.tpm_version or "present") if fw.tpm_present else _tri(fw.tpm_present, "absent"),
        "Warranty lookup": f"{vendor}: {url}" if url else "",
        "Generated": generated or date.today().isoformat(),
    }
    return rec


def _tri(value: Optional[bool], no: str = "off") -> str:
    if value is None:
        return "unknown"
    return "on" if value else no


def record_to_markdown(rec: Dict[str, str]) -> str:
    lines = [f"### Asset record: {rec.get('Hostname', '')}", "", "| Field | Value |", "|---|---|"]
    for k, v in rec.items():
        lines.append(f"| {k} | {(v or '-').replace('|', chr(92) + '|')} |")
    return "\n".join(lines) + "\n"


def record_to_csv(rec: Dict[str, str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(list(rec.keys()))
    w.writerow(list(rec.values()))
    return buf.getvalue()


def record_to_json(rec: Dict[str, str]) -> str:
    return json.dumps(rec, indent=2, ensure_ascii=False)
