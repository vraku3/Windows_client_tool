"""Drive-letter path -> NT device path, e.g. 'C:\\x' -> '\\Device\\
HarddiskVolume3\\x'.

`core.procengine.findref.find()` matches against handle names in the
kernel's OWN representation, never drive letters -- so searching for a
specific file means translating the search target once, rather than
reverse-translating every one of findref's results.
"""
import ctypes
from typing import Optional

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _query_dos_device(drive: str) -> Optional[str]:
    """'C:' -> '\\Device\\HarddiskVolume3', or None if it cannot be read."""
    buf = ctypes.create_unicode_buffer(260)
    length = _kernel32.QueryDosDeviceW(drive, buf, 260)
    if length == 0:
        return None
    return buf.value


def to_device_path(win_path: str) -> Optional[str]:
    if len(win_path) < 3 or win_path[1] != ":":
        return None  # UNC paths, or anything with no drive letter
    device = _query_dos_device(win_path[:2])
    if device is None:
        return None
    return device + win_path[2:]
