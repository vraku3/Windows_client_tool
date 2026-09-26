"""Read a crash dump's header for the facts it states -- and nothing else.

Read-only byte parsing. A dump is never loaded into a debugger, never
executed and never opened for anything but reading its first few KB (plus, for
a user-mode MDMP, the two small streams that name the crash). Qt-free.

Two families of file matter:

* **Kernel dumps** (`PAGEDU64` / `PAGEDUMP`), which is what `C:\\Windows\\Minidump`,
  `MEMORY.DMP` and `LiveKernelReports` hold. The `DUMP_HEADER64` layout was
  checked against 30 real files on this machine: bugcheck code at 0x38, four
  parameters at 0x40, `EXCEPTION_RECORD64` at 0xF00 and the dump type at 0xF98.
  The 32-bit `PAGEDUMP` layout is written from the documented structure and
  has only synthetic tests: no 32-bit dump was available to check it against.
* **User-mode minidumps** (`MDMP`), whose exception stream and module list can
  name the module that crashed.

A kernel dump does not say which DRIVER faulted in its header: that needs the
symbol/stack analysis a debugger does, so this reports the bugcheck and
parameters and leaves the driver blank rather than guess.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

HEADER_READ = 0x1000

_MACHINES = {0x8664: "x64", 0x14C: "x86", 0xAA64: "ARM64"}
_MD_MODULE_LIST = 4
_MD_EXCEPTION = 6


@dataclass
class DumpInfo:
    #: "kernel64", "kernel32", "user", or "unknown" (not a recognised dump).
    kind: str = "unknown"
    bugcheck_code: Optional[int] = None
    params: Tuple[int, ...] = ()
    exception_code: Optional[int] = None
    exception_address: Optional[int] = None
    machine: str = ""
    processors: Optional[int] = None
    build: Optional[int] = None
    dump_type: Optional[int] = None
    faulting_module: str = ""
    #: Why parts of this are missing ("" when nothing is).
    problems: List[str] = field(default_factory=list)


def parse_header(data: bytes) -> DumpInfo:
    """Parse the leading bytes of a dump file (at least 0x1000 for kernel dumps)."""
    if data[:8] == b"PAGEDU64":
        return _kernel64(data)
    if data[:8] == b"PAGEDUMP":
        return _kernel32(data)
    if data[:4] == b"MDMP":
        return DumpInfo(kind="user")  # filled in by parse_user_dump, which needs the whole stream area
    return DumpInfo(problems=["not a recognised dump header"])


def _kernel64(data: bytes) -> DumpInfo:
    info = DumpInfo(kind="kernel64")
    if len(data) < 0x60:
        info.problems.append("header truncated")
        return info
    major, minor = struct.unpack_from("<II", data, 8)
    machine, procs = struct.unpack_from("<II", data, 0x30)
    info.build = minor
    info.machine = _MACHINES.get(machine, f"0x{machine:X}")
    info.processors = procs
    info.bugcheck_code, = struct.unpack_from("<I", data, 0x38)
    info.params = struct.unpack_from("<4Q", data, 0x40)
    if len(data) >= 0xF9C:
        info.exception_code, = struct.unpack_from("<I", data, 0xF00)
        info.exception_address, = struct.unpack_from("<Q", data, 0xF10)
        info.dump_type, = struct.unpack_from("<I", data, 0xF98)
    else:
        info.problems.append("exception record not read (file shorter than 4 KB)")
    return info


def _kernel32(data: bytes) -> DumpInfo:
    info = DumpInfo(kind="kernel32")
    if len(data) < 0x40:
        info.problems.append("header truncated")
        return info
    info.build, = struct.unpack_from("<I", data, 0x0C)
    machine, procs = struct.unpack_from("<II", data, 0x20)
    info.machine = _MACHINES.get(machine, f"0x{machine:X}")
    info.processors = procs
    info.bugcheck_code, = struct.unpack_from("<I", data, 0x28)
    info.params = struct.unpack_from("<4I", data, 0x2C)
    return info


def parse_user_dump(path: str) -> DumpInfo:
    """A user-mode MDMP: exception code/address and the module containing it."""
    info = DumpInfo(kind="user")
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if len(head) < 32 or head[:4] != b"MDMP":
                info.problems.append("not an MDMP file")
                return info
            n_streams, dir_rva = struct.unpack_from("<II", head, 8)
            if n_streams > 4096:
                info.problems.append("implausible stream count")
                return info
            f.seek(dir_rva)
            directory = f.read(12 * n_streams)
            streams = {}
            for i in range(min(n_streams, len(directory) // 12)):
                stype, size, rva = struct.unpack_from("<III", directory, i * 12)
                streams.setdefault(stype, (size, rva))
            _read_exception(f, streams, info)
            if info.exception_address is not None:
                _read_module_for(f, streams, info)
    except OSError as exc:
        info.problems.append(f"could not be read: {exc}")
    except struct.error as exc:
        info.problems.append(f"malformed dump: {exc}")
    return info


def _read_exception(f, streams, info: DumpInfo) -> None:
    if _MD_EXCEPTION not in streams:
        info.problems.append("no exception stream (a hang or on-demand dump)")
        return
    _size, rva = streams[_MD_EXCEPTION]
    f.seek(rva)
    blob = f.read(8 + 8 + 8 + 8)
    if len(blob) < 32:
        info.problems.append("exception stream truncated")
        return
    info.exception_code, = struct.unpack_from("<I", blob, 8)
    info.exception_address, = struct.unpack_from("<Q", blob, 24)


def _read_module_for(f, streams, info: DumpInfo) -> None:
    if _MD_MODULE_LIST not in streams:
        return
    _size, rva = streams[_MD_MODULE_LIST]
    f.seek(rva)
    count_raw = f.read(4)
    if len(count_raw) < 4:
        return
    count, = struct.unpack("<I", count_raw)
    if count > 8192:
        info.problems.append("implausible module count")
        return
    table = f.read(108 * count)
    for i in range(min(count, len(table) // 108)):
        base, size = struct.unpack_from("<QI", table, i * 108)
        name_rva, = struct.unpack_from("<I", table, i * 108 + 20)
        if base <= info.exception_address < base + size:
            info.faulting_module = _read_name(f, name_rva)
            return


def _read_name(f, rva: int) -> str:
    f.seek(rva)
    raw = f.read(4)
    if len(raw) < 4:
        return ""
    length, = struct.unpack("<I", raw)
    if length > 2048:
        return ""
    return f.read(length).decode("utf-16-le", errors="replace")


def read_dump_info(path: str) -> DumpInfo:
    """Header facts for the dump at `path`. Never raises; problems are recorded."""
    try:
        with open(path, "rb") as f:
            head = f.read(HEADER_READ)
    except OSError as exc:
        return DumpInfo(problems=[f"could not be read: {exc}"])
    info = parse_header(head)
    if info.kind == "user":
        return parse_user_dump(path)
    return info
