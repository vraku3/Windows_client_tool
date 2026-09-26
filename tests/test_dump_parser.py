"""Crash-dump header parsing, on synthetic bytes and on this machine's real dumps."""
import glob
import os
import struct

import pytest

from modules.crash_dumps import crash_dump_reader as cdr
from modules.crash_dumps import dump_parser as dp


def _kernel64(code=0x133, params=(1, 0x1F4, 0x1F4, 0), exc=(0x80000003, 0xFFFFF80012345678), dtype=2):
    data = bytearray(0x2000)
    data[0:8] = b"PAGEDU64"
    struct.pack_into("<II", data, 8, 15, 26200)
    struct.pack_into("<II", data, 0x30, 0x8664, 16)
    struct.pack_into("<I", data, 0x38, code)
    struct.pack_into("<4Q", data, 0x40, *params)
    struct.pack_into("<I", data, 0xF00, exc[0])
    struct.pack_into("<Q", data, 0xF10, exc[1])
    struct.pack_into("<I", data, 0xF98, dtype)
    return bytes(data)


def test_kernel64_header():
    info = dp.parse_header(_kernel64())
    assert info.kind == "kernel64"
    assert info.bugcheck_code == 0x133
    assert info.params == (1, 0x1F4, 0x1F4, 0)
    assert info.machine == "x64" and info.processors == 16 and info.build == 26200
    assert info.exception_address == 0xFFFFF80012345678 and info.dump_type == 2


def test_truncated_kernel_header_says_so():
    info = dp.parse_header(_kernel64()[:0x100])
    assert info.bugcheck_code == 0x133
    assert any("exception record not read" in p for p in info.problems)
    assert dp.parse_header(b"PAGEDU64" + b"\0" * 8).problems == ["header truncated"]


def test_kernel32_header():
    data = bytearray(0x100)
    data[0:8] = b"PAGEDUMP"
    struct.pack_into("<I", data, 0x0C, 7601)
    struct.pack_into("<II", data, 0x20, 0x14C, 2)
    struct.pack_into("<I", data, 0x28, 0x50)
    struct.pack_into("<4I", data, 0x2C, 0xAAAA, 0, 1, 0xBBBB)
    info = dp.parse_header(bytes(data))
    assert (info.kind, info.machine, info.bugcheck_code) == ("kernel32", "x86", 0x50)
    assert info.params == (0xAAAA, 0, 1, 0xBBBB)


def test_garbage_is_unknown_not_a_crash():
    info = dp.parse_header(b"hello world, not a dump")
    assert info.kind == "unknown" and info.bugcheck_code is None and info.problems


def _mdmp(tmp_path, with_module=True):
    """A user-mode dump: exception stream + a module list containing the fault address."""
    name = "faulty.dll".encode("utf-16-le")
    body = bytearray()
    exc_rva = 32 + 2 * 12
    mod_rva = exc_rva + 168
    name_rva = mod_rva + 4 + 108
    head = bytearray(32)
    head[0:4] = b"MDMP"
    struct.pack_into("<II", head, 8, 2, 32)
    directory = struct.pack("<III", 6, 168, exc_rva) + struct.pack("<III", 4, 112, mod_rva)
    exc = bytearray(168)
    struct.pack_into("<I", exc, 8, 0xC0000005)
    struct.pack_into("<Q", exc, 24, 0x7FF800001234)
    modlist = struct.pack("<I", 1)
    module = bytearray(108)
    struct.pack_into("<QI", module, 0, 0x7FF800000000 if with_module else 0x10000, 0x100000)
    struct.pack_into("<I", module, 20, name_rva)
    body = head + directory + exc + modlist + module + struct.pack("<I", len(name)) + name
    path = tmp_path / "user.dmp"
    path.write_bytes(bytes(body))
    return str(path)


def test_user_dump_names_the_faulting_module(tmp_path):
    info = dp.read_dump_info(_mdmp(tmp_path))
    assert info.kind == "user"
    assert info.exception_code == 0xC0000005
    assert info.faulting_module == "faulty.dll"


def test_user_dump_with_fault_outside_every_module(tmp_path):
    info = dp.read_dump_info(_mdmp(tmp_path, with_module=False))
    assert info.exception_code == 0xC0000005 and info.faulting_module == ""


def test_unreadable_file_reports_instead_of_raising(tmp_path):
    info = dp.read_dump_info(str(tmp_path / "missing.dmp"))
    assert info.kind == "unknown" and info.problems


def test_describe_a_bugcheck_and_a_live_dump():
    info = dp.parse_header(_kernel64(code=0x133))
    level, message, raw = cdr.describe(info, "091526-1.dmp", 300 * 1024)
    assert level == "Error" and "DPC_WATCHDOG_VIOLATION" in message and raw["cause"]
    live = dp.parse_header(_kernel64(code=0x141))
    level, message, raw = cdr.describe(live, "WATCHDOG-1.dmp", 600 * 1024, live_kind="WATCHDOG")
    assert level == "Warning" and "live dump" in message and "not a blue screen" in raw["cause"]


def test_detail_html_explains_parameters():
    info = dp.parse_header(_kernel64(code=0xD1, params=(0x10, 2, 0, 0xFFFFF800AABB)))
    _l, _m, raw = cdr.describe(info, "x.dmp", 1024)
    from core.types import LogEntry
    from datetime import datetime
    html = cdr.detail_html(LogEntry(datetime.now(), "Minidump", "Error", "x", raw))
    assert "DRIVER_IRQL_NOT_LESS_OR_EQUAL" in html and "IRQL at the time" in html
    assert "WinDbg" in html  # says why the faulting driver is not named


def test_bugcheck_event_message_is_parsed():
    text = ("The computer has rebooted from a bugcheck.  The bugcheck was: 0x00000133 "
            "(0x0000000000000001, 0x0000000000001e00, 0x0000000000000000, 0x0000000000000000). "
            "A dump was saved in: C:\\WINDOWS\\MEMORY.DMP. Report Id: abc.")
    parsed = cdr.parse_bugcheck_event(text)
    assert parsed["code"] == 0x133 and len(parsed["params"]) == 4
    assert parsed["dump_path"] == "C:\\WINDOWS\\MEMORY.DMP"
    assert cdr.parse_bugcheck_event("nothing here") is None


def test_missing_and_unreadable_directories_are_different(tmp_path, monkeypatch):
    assert cdr.read_crash_dumps(dump_dir=str(tmp_path / "nope")) == []
    monkeypatch.setattr(os, "listdir", lambda p: (_ for _ in ()).throw(PermissionError("denied")))
    entries, note = cdr._scan_dir(str(tmp_path), "Minidump")
    assert entries == [] and "could not be read" in note


def test_summary_states_when_nothing_could_be_read():
    assert "no blue screen" in cdr.summary_text([], [])
    assert "no blue screen" not in cdr.summary_text([], ["C:\\x could not be read"])


REAL = glob.glob(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "LiveKernelReports", "*", "*.dmp"))


@pytest.mark.skipif(not REAL, reason="no live kernel reports on this machine")
def test_real_live_kernel_reports_parse_plausibly():
    parsed = [dp.read_dump_info(p) for p in REAL[:20]]
    good = [i for i in parsed if i.kind == "kernel64"]
    assert good, "at least one real dump should parse as PAGEDU64"
    for info in good:
        assert info.machine in ("x64", "ARM64")
        assert 1 <= info.processors <= 1024
        assert 10000 < info.build < 40000
        assert info.bugcheck_code is not None
