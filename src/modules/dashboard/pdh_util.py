"""A small PDH (performance counter) wrapper. No Qt.

Windows exposes several things -- energy meters, thermal zones -- only as
performance counters. This lists a counter object's instances and reads named
counters, always English-named (`PdhAddEnglishCounter`) so a localised Windows
does not break the path. Anything the counter system refuses is an `OSError`
naming the counter, never a silent zero.
"""
import ctypes
import logging
from ctypes import wintypes
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BS = chr(92)
PDH_FMT_DOUBLE = 0x200
PERF_DETAIL_WIZARD = 400
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_NO_OBJECT = 0xC0000BB8


class _Value(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", ctypes.c_double)]


def _pdh():
    return ctypes.WinDLL("pdh")


def _multi_sz(buffer, length: int) -> List[str]:
    text = ctypes.wstring_at(ctypes.addressof(buffer), length)
    return [part for part in text.split("\x00") if part]


def list_instances(obj: str) -> List[str]:
    """Instance names of a counter object ("Energy Meter"); [] when it has none
    or the object does not exist on this machine."""
    pdh = _pdh()
    counters_len, instances_len = wintypes.DWORD(0), wintypes.DWORD(0)
    status = pdh.PdhEnumObjectItemsW(None, None, obj, None, ctypes.byref(counters_len),
                                     None, ctypes.byref(instances_len), PERF_DETAIL_WIZARD, 0)
    if (status & 0xFFFFFFFF) not in (PDH_MORE_DATA, 0):
        logger.debug("PDH object %r not available (0x%08x)", obj, status & 0xFFFFFFFF)
        return []
    if not instances_len.value:
        return []
    counters = ctypes.create_unicode_buffer(counters_len.value + 2)
    instances = ctypes.create_unicode_buffer(instances_len.value + 2)
    status = pdh.PdhEnumObjectItemsW(None, None, obj, counters, ctypes.byref(counters_len),
                                     instances, ctypes.byref(instances_len), PERF_DETAIL_WIZARD, 0)
    if status != 0:
        logger.warning("PdhEnumObjectItems(%s) failed: 0x%08x", obj, status & 0xFFFFFFFF)
        return []
    return _multi_sz(instances, instances_len.value)


class Query:
    """Named counters read together. Own one for the tab's lifetime; `close()` it."""

    def __init__(self, paths: Dict[str, str]) -> None:
        """`paths` maps a label to a full counter path (with backslashes)."""
        self._pdh = _pdh()
        self._query = wintypes.HANDLE()
        self._handles: Dict[str, wintypes.HANDLE] = {}
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
            raise OSError("PdhOpenQuery failed")
        for label, path in paths.items():
            handle = wintypes.HANDLE()
            status = self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle))
            if status != 0:
                self.close()
                raise OSError(f"counter {path!r} unavailable (0x{status & 0xFFFFFFFF:08x})")
            self._handles[label] = handle
        self._primed = False

    def read(self) -> Optional[Dict[str, float]]:
        """Label -> value. The first call primes rate counters and answers None."""
        if self._pdh.PdhCollectQueryData(self._query) != 0:
            return None
        if not self._primed:
            self._primed = True
            return None
        out: Dict[str, float] = {}
        for label, handle in self._handles.items():
            value = _Value()
            if self._pdh.PdhGetFormattedCounterValue(handle, PDH_FMT_DOUBLE, None, ctypes.byref(value)) == 0 \
                    and value.CStatus in (0, 1):
                out[label] = value.value
        return out

    def close(self) -> None:
        if getattr(self, "_query", None) and self._query.value:
            self._pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()
        self._handles = {}


def counter_path(obj: str, instance: str, counter: str) -> str:
    return f"{BS}{obj}({instance}){BS}{counter}"
