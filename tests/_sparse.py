"""Big test files that take no disk space.

`seek(size - 1); write(b"\0")` on NTFS is NOT sparse: Windows zero-fills the
whole length. Five 2 GB virtual-disk fixtures plus two breakdown payloads
made every full test run write ~13.8 GB to the SSD, and with a fixed
--basetemp those runs piled up in %TEMP% (53.8 GB found there, 2026-10-10).
FSCTL_SET_SPARSE first keeps the same logical size (what the scanners read,
st_size) with almost nothing allocated.
"""
import ctypes
import msvcrt
from ctypes import wintypes

FSCTL_SET_SPARSE = 0x000900C4


def make_sparse(path, size: int) -> None:
    with open(path, "wb") as handle:
        returned = wintypes.DWORD()
        ctypes.windll.kernel32.DeviceIoControl(
            wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno())), FSCTL_SET_SPARSE,
            None, 0, None, 0, ctypes.byref(returned), None)
        # If the volume refuses sparse files the file is still correct, just not small.
        handle.seek(size - 1)
        handle.write(b"\0")
