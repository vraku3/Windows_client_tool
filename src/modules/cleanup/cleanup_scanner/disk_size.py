"""Bytes a file really occupies on disk -- the space deleting it frees.

`st_size` is the LOGICAL size. For a sparse or NTFS-compressed file it can be
far larger than what is allocated: the test suite's fake 2 GB VM disks are
sparse (~0.1 MB on disk), and leftover copies in %TEMP% made Cleanup offer
"Temporary files: 235 GB" where ~7 GB was reclaimable (measured 2026-10-21).

Only a file whose attributes say SPARSE or COMPRESSED costs a syscall
(`GetCompressedFileSizeW`); every other file answers from the stat it already
has, so a 100k-entry walk stays as fast as before. Qt-free.

Lives in its own module because both `_common` (sizes) and `deleter` (freed
bytes) need it, and `_common` already imports `deleter`.
"""
import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger(__name__)

FILE_ATTRIBUTE_SPARSE_FILE = 0x200
FILE_ATTRIBUTE_COMPRESSED = 0x800
_ALLOC_DIFFERS = FILE_ATTRIBUTE_SPARSE_FILE | FILE_ATTRIBUTE_COMPRESSED
INVALID_FILE_SIZE = 0xFFFFFFFF

_get_compressed_size = None
if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _get_compressed_size = _kernel32.GetCompressedFileSizeW
    _get_compressed_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _get_compressed_size.restype = wintypes.DWORD


def _allocated(path: str):
    """Allocated bytes per GetCompressedFileSizeW, or None when the call failed."""
    high = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    low = _get_compressed_size(path, ctypes.byref(high))
    if low == INVALID_FILE_SIZE:
        err = ctypes.get_last_error()
        if err != 0:
            logger.debug("GetCompressedFileSizeW failed for %s: winerror %d", path, err)
            return None
    return (high.value << 32) | low


def on_disk_size(path: str, st: os.stat_result) -> int:
    """`st.st_size` for an ordinary file; for a sparse or compressed one, the
    allocated size (falls back to `st_size` if Windows will not say)."""
    attrs = getattr(st, "st_file_attributes", 0)
    if not attrs & _ALLOC_DIFFERS or _get_compressed_size is None:
        return st.st_size
    size = _allocated(os.fspath(path))
    return st.st_size if size is None else size


def on_disk_size_of(path: str) -> int:
    """Drop-in for `os.path.getsize`: same stat (follows links), same OSError."""
    return on_disk_size(path, os.stat(path))
