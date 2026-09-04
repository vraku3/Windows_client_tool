r"""Who each monitor is, through `DisplayConfigGetDeviceInfo`.

`display_config.py` says what is on the desktop: target ids, resolutions,
positions. It cannot say which row is the Dell, and on a machine with three
2560x1440 panels that is the only thing the user is looking for. This module
turns a CCD target into a name, a manufacturer and a device path, and turns a
CCD *source* into the `\\.\DISPLAYn` name every GDI call needs.

Three things about the real data, each of which produces a wrong answer
rather than an error:

* **`edidManufactureId` is byte-swapped.** The Dell here returns `0xAC10`.
  Decoded five-bits-per-letter that is `K@P` -- three printable characters,
  indistinguishable downstream from a real PNP id. Swapped to `0x10AC` it is
  `DEL`. Every id must go through `manufacturer_from_edid`, never through
  `decode_manufacturer_id` alone.
* **The ids are only meaningful when `flags` says so.** Bit 2
  (`edidIdsValid`) gates them; without it the decode still produces a
  three-letter string, so this module answers `None` instead. Observed
  `flags` on all three panels here is `0x5` -- friendly name from EDID, ids
  valid.
* **A source id is an index, not a display number.** Source 1 is
  `\\.\DISPLAY2` on this machine and source 0 is `\\.\DISPLAY1`, but that
  correspondence is a coincidence of enumeration order and is not relied on
  anywhere: the GDI name is always asked for.

Read-only throughout. Nothing here calls `SetDisplayConfig` or changes any
part of the display configuration, and none of it needs elevation.
"""
from __future__ import annotations

import ctypes
import logging
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional, Tuple

from .display_config import _LUID, win32_error_name

logger = logging.getLogger(__name__)

#: DISPLAYCONFIG_DEVICE_INFO_TYPE. Only the read-only members are here; the
#: SET_* values are deliberately absent -- this module never writes.
DEVICE_INFO_GET_SOURCE_NAME = 1
DEVICE_INFO_GET_TARGET_NAME = 2
DEVICE_INFO_GET_ADAPTER_NAME = 4

#: DISPLAYCONFIG_TARGET_DEVICE_NAME_FLAGS. Observed value here: 0x5.
TARGET_NAME_FLAG_FRIENDLY_NAME_FROM_EDID = 0x1
TARGET_NAME_FLAG_FRIENDLY_NAME_FORCED = 0x2
TARGET_NAME_FLAG_EDID_IDS_VALID = 0x4

#: Every EDID base block starts with these eight bytes.
EDID_HEADER = bytes.fromhex("00FFFFFFFFFFFF00")

#: The base block is 128 bytes and must sum to 0 mod 256. A blob can be
#: longer -- the Gigabyte here is 384, carrying CTA-861 extension blocks --
#: and the extra blocks have their own checksums, which are not our problem.
EDID_BASE_BLOCK_SIZE = 128

#: The first detailed timing descriptor, which by spec is the PREFERRED
#: timing: the resolution the panel is actually made of.
EDID_PREFERRED_TIMING_OFFSET = 54


# -- the pure part -----------------------------------------------------


def byte_swap16(value: int) -> int:
    """Swap the two bytes of a USHORT.

    `edidManufactureId` comes out of the API in the opposite byte order to
    the one the EDID spec packs the letters in. Every id on this machine is
    wrong without this and *looks* right after decoding.
    """
    value &= 0xFFFF
    return ((value & 0xFF) << 8) | (value >> 8)


def decode_manufacturer_id(packed: int) -> str:
    """The three-letter PNP id packed five bits per letter, A=1.

    Takes an ALREADY-SWAPPED value -- see `manufacturer_from_edid`, which is
    what callers want. Deliberately does not validate: a caller passing the
    raw value should see the garbage (`K@P`) that pins the swap in the tests,
    not an exception that hides which of the two steps was skipped.
    """
    packed &= 0xFFFF
    return "".join(chr(64 + ((packed >> shift) & 0x1F)) for shift in (10, 5, 0))


def manufacturer_from_edid(raw: int) -> str:
    """`edidManufactureId` exactly as Windows returned it -> `"DEL"`."""
    return decode_manufacturer_id(byte_swap16(raw))


@dataclass(frozen=True)
class TargetName:
    """Everything GET_TARGET_NAME knows about one monitor.

    `manufacturer_id` and `product_code` are `None` -- not a plausible
    three-letter string and not 0 -- when `edid_valid` is False. A monitor
    whose EDID ids the driver did not vouch for is a monitor we could not
    identify, and this project does not dress that up as an answer.
    """

    friendly_name: str
    device_path: str
    manufacturer_id: Optional[str]
    product_code: Optional[int]
    output_technology: int
    connector_instance: int
    edid_valid: bool


def _fallback_name(device_path: str, target_id: int) -> str:
    r"""A usable name for a monitor with no EDID friendly name.

    Never an empty string: an empty cell in the UI reads as "no monitor".
    The device path carries the PNP hardware id as its second `#` segment
    (`\\?\DISPLAY#DEL41A8#5&...`), which is at least identifiable; failing
    that, the target id, which is at least unique.
    """
    parts = [p for p in device_path.split("#") if p]
    if len(parts) >= 2:
        return parts[1]
    return f"Display {target_id}"


def build_target_name(*, target_id: int, flags: int, output_technology: int,
                      edid_manufacture_id: int, edid_product_code_id: int,
                      connector_instance: int, friendly_name: str,
                      device_path: str) -> TargetName:
    """Assemble a `TargetName` from the raw struct fields.

    Split out from the ctypes call so the swap, the flag gate and the
    name fallback are all testable with no display attached.
    """
    edid_valid = bool(flags & TARGET_NAME_FLAG_EDID_IDS_VALID)
    name = (friendly_name or "").strip()
    return TargetName(
        friendly_name=name or _fallback_name(device_path, target_id),
        device_path=device_path,
        manufacturer_id=manufacturer_from_edid(edid_manufacture_id) if edid_valid else None,
        product_code=edid_product_code_id if edid_valid else None,
        output_technology=output_technology,
        connector_instance=connector_instance,
        edid_valid=edid_valid,
    )


# -- the panel's own resolution, out of the EDID -----------------------
#
# Neither CCD nor GDI will tell you this. `EnumDisplaySettingsExW` reports
# what the DRIVER will accept, which on this machine includes 3840x2160 on a
# 1440p Dell -- AMD's Virtual Super Resolution, rendered high and downscaled
# to the panel. It is larger, softer, and caps at 120 Hz where the panel
# itself does 280. Anything calling the biggest enumerated mode "best"
# recommends the worst of both, so the panel's real resolution has to come
# from the panel: the EDID's preferred timing.


def edid_base_block_ok(blob: bytes) -> bool:
    """Is this really an EDID, and did it survive the trip?

    Both checks matter and neither is theatre. A partially-written or
    absent blob still yields two 12-bit numbers when read at the timing
    offset, and a resolution nobody can trace back is worse than no answer.
    """
    if blob is None or len(blob) < EDID_BASE_BLOCK_SIZE:
        return False
    if bytes(blob[:8]) != EDID_HEADER:
        return False
    return sum(blob[:EDID_BASE_BLOCK_SIZE]) % 256 == 0


def parse_edid_preferred_timing(blob: bytes) -> Optional[Tuple[int, int]]:
    """The panel's native resolution from an EDID blob, or `None`.

    The first detailed timing descriptor lives at byte 54 and is 18 bytes.
    Both active counts are 12-bit values split across two fields: the low
    byte, and the high nibble of a shared byte four positions on.

    `None` for anything that does not validate, and for a descriptor that
    decodes to a zero dimension -- an EDID whose first descriptor is a
    monitor name or a blank filler, which is legal, and which would
    otherwise be read as a 0-pixel panel.
    """
    if not edid_base_block_ok(blob):
        logger.debug("EDID blob failed the header or checksum check")
        return None

    base = EDID_PREFERRED_TIMING_OFFSET
    horizontal = blob[base + 2] | ((blob[base + 4] & 0xF0) << 4)
    vertical = blob[base + 5] | ((blob[base + 7] & 0xF0) << 4)
    if not horizontal or not vertical:
        logger.debug("EDID preferred timing decodes to %sx%s -- not a panel",
                     horizontal, vertical)
        return None
    return (horizontal, vertical)


def device_instance_from_path(device_path: str) -> Optional[str]:
    r"""`\\?\DISPLAY#DELD0E6#7&327e9bec&0&UID256#{guid}` ->
    `DISPLAY\DELD0E6\7&327e9bec&0&UID256`.

    The device path GET_TARGET_NAME returns is the same identity the PnP
    enumerator uses, with `#` where the registry uses `\` and an interface
    GUID appended. `None` for anything that is not a display interface
    path -- guessing a registry key from an unrecognised string is how a
    read ends up pointed at the wrong device.
    """
    if not device_path:
        return None
    path = device_path.strip()
    for prefix in ("\\\\?\\", "\\\\.\\"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    else:
        return None

    parts = path.split("#")
    if len(parts) < 3 or parts[0].upper() != "DISPLAY":
        return None
    if not parts[1] or not parts[2]:
        return None
    return "\\".join(parts[:3])


def read_edid(device_path: str) -> Optional[bytes]:
    """The raw EDID Windows cached for this monitor, or `None`.

    A read of `HKLM\\SYSTEM\\CurrentControlSet\\Enum\\...\\Device
    Parameters\\EDID`, which is readable by an ordinary user -- measured
    unelevated on all three monitors here. `None` distinguishes nothing
    from nothing: a path we could not map, a key that is not there, and a
    read that was refused are all "we could not look", and each is logged
    with its reason rather than collapsed into an empty blob.
    """
    instance = device_instance_from_path(device_path)
    if instance is None:
        logger.warning("cannot derive a registry instance from device path %r",
                       device_path)
        return None

    subkey = f"SYSTEM\\CurrentControlSet\\Enum\\{instance}\\Device Parameters"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
            blob, kind = winreg.QueryValueEx(key, "EDID")
    except FileNotFoundError:
        logger.info("no cached EDID under %s", subkey)
        return None
    except OSError as exc:
        logger.warning("could not read the EDID under %s: %s", subkey, exc)
        return None

    if kind != winreg.REG_BINARY or not blob:
        logger.warning("EDID under %s is not a non-empty binary value", subkey)
        return None
    return bytes(blob)


def native_resolution(device_path: str) -> Optional[Tuple[int, int]]:
    """The panel's own resolution, or `None` when it cannot be established.

    `None` means exactly that. It is never the largest mode the driver
    offers, and never the resolution the display happens to be running.
    """
    blob = read_edid(device_path)
    if blob is None:
        return None
    resolution = parse_edid_preferred_timing(blob)
    if resolution is None:
        logger.warning("the EDID for %r did not yield a preferred timing",
                       device_path)
    return resolution


# -- the ctypes edge ---------------------------------------------------


class _DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", _LUID), ("id", wintypes.UINT)]


class _TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER),
                ("flags", wintypes.UINT),
                ("outputTechnology", wintypes.UINT),
                ("edidManufactureId", wintypes.USHORT),
                ("edidProductCodeId", wintypes.USHORT),
                ("connectorInstance", wintypes.UINT),
                ("monitorFriendlyDeviceName", ctypes.c_wchar * 64),
                ("monitorDevicePath", ctypes.c_wchar * 128)]


class _SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER),
                ("viewGdiDeviceName", ctypes.c_wchar * 32)]


class _ADAPTER_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER),
                ("adapterDevicePath", ctypes.c_wchar * 128)]


def _device_info(packet) -> int:
    """The one call, in one place, so tests can stand in for it.

    Returns a Win32 error code -- 0 is success, and per this project's
    standing rule that is not by itself proof the buffer says anything.
    """
    return ctypes.windll.user32.DisplayConfigGetDeviceInfo(ctypes.byref(packet))


def _fill_header(packet, info_type: int, adapter: Tuple[int, int],
                 device_id: int = 0) -> None:
    packet.header.type = info_type
    packet.header.size = ctypes.sizeof(packet)
    packet.header.adapterId = _LUID(adapter[0], adapter[1])
    packet.header.id = device_id


def _ask(packet, what: str) -> bool:
    """Make the call, and report a refusal as a refusal.

    `False` means "we could not look" and is never turned into a blank
    name by the callers below.
    """
    rc = _device_info(packet)
    if rc != 0:
        logger.warning("DisplayConfigGetDeviceInfo(%s) failed: %s",
                       what, win32_error_name(rc))
        return False
    return True


def target_name(adapter: Tuple[int, int], target_id: int) -> Optional[TargetName]:
    """The monitor on `target_id`, or `None` if the API refused.

    `None` is "we could not find out", never "this monitor has no name" --
    a monitor that answers with an empty friendly name still gets a
    `TargetName`, with a fallback name derived from its device path.
    """
    packet = _TARGET_DEVICE_NAME()
    _fill_header(packet, DEVICE_INFO_GET_TARGET_NAME, adapter, target_id)
    if not _ask(packet, f"GET_TARGET_NAME target={target_id}"):
        return None
    return build_target_name(
        target_id=target_id,
        flags=packet.flags,
        output_technology=packet.outputTechnology,
        edid_manufacture_id=packet.edidManufactureId,
        edid_product_code_id=packet.edidProductCodeId,
        connector_instance=packet.connectorInstance,
        friendly_name=packet.monitorFriendlyDeviceName,
        device_path=packet.monitorDevicePath,
    )


def source_gdi_name(adapter: Tuple[int, int], source_id: int) -> Optional[str]:
    r"""The `\\.\DISPLAYn` name for a CCD source id, or `None`.

    This is the bridge from the CCD world to the GDI one: `display_modes`
    enumerates modes by this name, and nothing else in the CCD API produces
    it. The source id is an index into the adapter's sources -- it is NOT
    the display number, even though the two happen to line up here.
    """
    packet = _SOURCE_DEVICE_NAME()
    _fill_header(packet, DEVICE_INFO_GET_SOURCE_NAME, adapter, source_id)
    if not _ask(packet, f"GET_SOURCE_NAME source={source_id}"):
        return None
    name = packet.viewGdiDeviceName.strip()
    if not name:
        logger.warning("GET_SOURCE_NAME source=%s returned success with an "
                       "empty name", source_id)
        return None
    return name


def adapter_name(adapter: Tuple[int, int]) -> Optional[str]:
    """The adapter's device path -- which GPU a monitor hangs off -- or `None`."""
    packet = _ADAPTER_NAME()
    _fill_header(packet, DEVICE_INFO_GET_ADAPTER_NAME, adapter)
    if not _ask(packet, f"GET_ADAPTER_NAME adapter={adapter}"):
        return None
    name = packet.adapterDevicePath.strip()
    if not name:
        logger.warning("GET_ADAPTER_NAME adapter=%s returned success with an "
                       "empty path", adapter)
        return None
    return name
