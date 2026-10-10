"""Protection and exploit-mitigation reads for the Security view.

Pins the measured traps: DEP needs PROCESS_QUERY_INFORMATION (the limited
right is refused for every process) yet a 64-bit process still has an
answer; the process CFG policy is not the image's own CFG; and an unknown
is never written down as "off".
"""
import os
import struct
import sys

from core.procengine.mitigations import (
    ASLR, CFG, CHILD_PROCESS, MitigationReport, PE_DYNAMIC_BASE,
    PE_GUARD_CF, SHADOW_STACK, describe_aslr, describe_cfg, describe_dep,
    describe_protection, parse_dll_characteristics, read_mitigations,
    summarize,
)
from core.procengine.details import resolve

MY_PID = os.getpid()


# ---- synthetic --------------------------------------------------------

def test_protection_byte_decodes_like_process_explorer():
    # Measured here: DefenderSessionHelper.exe answers 0x31.
    assert describe_protection(0x31) == "PsProtectedSignerAntimalware-Light"
    assert describe_protection(0x72) == "PsProtectedSignerWinSystem"
    assert describe_protection(0) == "None"


def test_dep_wording():
    assert describe_dep(0x3, True) == ("Enabled, ATL thunk emulation off "
                                       "(permanent)")
    # Battle.net.exe (x86) here: DEP off, permanently.
    assert describe_dep(0x0, True) == "Disabled (permanent)"


def test_cfg_policy_without_image_flag_is_not_called_plain_enabled():
    report = MitigationReport(pid=1, flags={CFG: 0x1}, image_cfg=False)
    assert describe_cfg(report).startswith("Enabled for system DLLs only")
    report.image_cfg = True
    assert describe_cfg(report) == "Enabled"
    assert describe_cfg(MitigationReport(pid=1)) == "—"


def test_aslr_on_a_fixed_base_image():
    report = MitigationReport(pid=1, flags={ASLR: 0x1}, image_aslr=False)
    assert "fixed base" in describe_aslr(report)
    report.flags[ASLR] = 0x3        # force relocate overrides the image
    assert "force relocate" in describe_aslr(report)


def test_active_lists_only_enforcing_bits():
    # 0x100 is CetDynamicApisOutOfProcOnly -- 128 processes here report
    # only that, and it is NOT shadow-stack protection.
    report = MitigationReport(pid=1, flags={SHADOW_STACK: 0x100,
                                            CHILD_PROCESS: 0x1})
    assert report.active() == ["Child process creation blocked"]
    report.flags[SHADOW_STACK] = 0x105
    assert "Hardware shadow stack (CET)" in report.active()


def test_unknown_policy_is_none_not_false():
    report = MitigationReport(pid=1)
    assert report.is_on(CFG) is None


def test_pe_header_parsing():
    head = bytearray(1024)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", head, 0x80 + 24 + 70,
                     PE_GUARD_CF | PE_DYNAMIC_BASE)
    value, why = parse_dll_characteristics(bytes(head))
    assert why is None and value == PE_GUARD_CF | PE_DYNAMIC_BASE
    assert parse_dll_characteristics(b"not a pe")[0] is None


def test_refused_read_summarises_as_refusal():
    rows = summarize(MitigationReport(pid=4, error="Access is denied"))
    assert rows == [("Mitigations", "could not be read — Access is denied")]


# ---- real machine -----------------------------------------------------

def test_our_own_process_reads():
    details = resolve(MY_PID)
    report = read_mitigations(MY_PID, details.path, details.architecture)
    assert report.readable, report.error
    assert report.protection == "None"
    # DEP is always stated for us: either read, or by architecture.
    assert report.dep and report.dep.startswith("Enabled")
    assert report.is_on(ASLR) is not None
    # The interpreter's own header was read, whatever it says.
    assert report.image_cfg is not None, report.image_reason
    labels = dict(summarize(report))
    assert set(labels) == {"Protection", "DEP", "ASLR", "CFG"}


def test_python_exe_header_matches_the_file():
    with open(sys.executable, "rb") as handle:
        value, why = parse_dll_characteristics(handle.read(4096))
    assert why is None and value is not None


def test_system_process_is_a_refusal_unelevated():
    report = read_mitigations(4)
    if not report.readable:
        assert report.error
        assert report.flags == {} and report.protection is None
