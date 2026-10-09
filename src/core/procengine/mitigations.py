r"""Process protection and exploit mitigations, per process.

Process Explorer's DEP / ASLR / CFG / Protection columns and its
"Mitigation Policies" list: the first thing someone checks when asking
whether a process is a soft target, or whether that "antimalware" process
really is one Windows protects.

Measured on this machine, unelevated (2026-10-09, 334 processes, 191
openable with `PROCESS_QUERY_LIMITED_INFORMATION`):

- **`GetProcessMitigationPolicy` answers every policy with the LIMITED
  right except DEP.** `ProcessDEPPolicy` (0) failed ERROR_ACCESS_DENIED for
  191 of 191 limited handles and succeeded for all 182 opened with
  `PROCESS_QUERY_INFORMATION`. So DEP gets its own open, and where that is
  refused a 64-bit process still has an answer: DEP is permanently on for
  every 64-bit process, by architecture, not by policy.
- **DEP is genuinely OFF in eight 32-bit `Battle.net.exe` processes here**,
  though their PE header carries `NX_COMPAT`. Two independent reads agree:
  the DEP policy says Enable=0, and `ProcessExecuteFlags` (34) reads `0x3a`
  -- MEM_EXECUTE_OPTION_ENABLE | PERMANENT, i.e. executable data, locked
  in -- where every 64-bit process reads `0xd`. `Get-ProcessMitigation -Id`
  says the same. So the header is not the answer; the process is.
- **The process CFG policy said ON for 192 of 192 processes -- including
  39 whose own EXE was never compiled with /guard:cf** (no
  `IMAGE_DLLCHARACTERISTICS_GUARD_CF` in its PE header: GCC.exe,
  Battle.net.exe, steamwebhelper.exe, ...). The policy is process-wide and
  Windows' own DLLs carry CFG, so "CFG: enabled" from the policy alone
  claims protection the program's own code does not have. Both facts are
  reported, separately.
- **Protection (PPL) reads with the limited right** via
  `NtQueryInformationProcess(ProcessProtectionInformation)`. One process
  answered protected unelevated: `DefenderSessionHelper.exe`, `0x31` =
  PsProtectedSignerAntimalware-Light. MsMpEng itself refuses the open, so
  it reads as unknown -- not as unprotected.
- **Hardware shadow stacks (CET) are live here**: 64 processes report
  `EnableUserShadowStack`; 128 report only `CetDynamicApisOutOfProcOnly`
  (0x100), which is NOT shadow-stack protection.
- Each read costs ~0.01 ms once the handle is open.

Qt-free. Every unknown is `None` with a reason, never `False`: "no ASLR"
and "we could not ask" are different findings.
"""
from __future__ import annotations

import ctypes
import logging
import struct
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ProcessProtectionInformation = 61

# PROCESS_MITIGATION_POLICY values.
DEP, ASLR, DYNAMIC_CODE, STRICT_HANDLE, SYSCALL_DISABLE = 0, 1, 2, 3, 4
EXTENSION_POINT, CFG, SIGNATURE, FONT, IMAGE_LOAD = 6, 7, 8, 9, 10
CHILD_PROCESS, SHADOW_STACK = 13, 15

# IMAGE_OPTIONAL_HEADER.DllCharacteristics bits.
PE_HIGH_ENTROPY_VA = 0x0020
PE_DYNAMIC_BASE = 0x0040
PE_NX_COMPAT = 0x0100
PE_GUARD_CF = 0x4000

PROTECTION_TYPES = {0: "None", 1: "Light", 2: "Full"}
PROTECTION_SIGNERS = {
    0: "None", 1: "Authenticode", 2: "CodeGen", 3: "Antimalware",
    4: "Lsa", 5: "Windows", 6: "WinTcb", 7: "WinSystem", 8: "App",
}

#: (policy, bit, label) -- what each policy's flag word means when on.
#: Only the ENFORCING bits are listed; audit-only bits enforce nothing and
#: would read as protection.
_POLICY_BITS: List[Tuple[int, int, str]] = [
    (ASLR, 0x1, "ASLR: bottom-up randomization"),
    (ASLR, 0x2, "ASLR: force relocate images"),
    (ASLR, 0x4, "ASLR: high entropy"),
    (ASLR, 0x8, "ASLR: disallow stripped images"),
    (DYNAMIC_CODE, 0x1, "Dynamic code prohibited (ACG)"),
    (STRICT_HANDLE, 0x1, "Strict handle checks"),
    (SYSCALL_DISABLE, 0x1, "Win32k system calls disabled"),
    (EXTENSION_POINT, 0x1, "Legacy extension points disabled"),
    (CFG, 0x1, "Control Flow Guard"),
    (CFG, 0x4, "Control Flow Guard: strict"),
    (SIGNATURE, 0x1, "Signature: Microsoft-signed only"),
    (SIGNATURE, 0x2, "Signature: Store-signed only"),
    (FONT, 0x1, "Non-system fonts disabled"),
    (IMAGE_LOAD, 0x1, "Image load: no remote images"),
    (IMAGE_LOAD, 0x2, "Image load: no low-integrity images"),
    (IMAGE_LOAD, 0x4, "Image load: prefer System32"),
    (CHILD_PROCESS, 0x1, "Child process creation blocked"),
    (SHADOW_STACK, 0x1, "Hardware shadow stack (CET)"),
    (SHADOW_STACK, 0x10, "Hardware shadow stack: strict"),
]

_QUERIED = sorted({policy for policy, _bit, _label in _POLICY_BITS})


@dataclass
class MitigationReport:
    pid: int
    error: Optional[str] = None
    protection: Optional[str] = None
    protection_raw: Optional[int] = None
    dep: Optional[str] = None
    dep_reason: Optional[str] = None
    #: policy -> its flag word, for every policy that answered.
    flags: Dict[int, int] = field(default_factory=dict)
    #: policy -> why it did not answer.
    refused: Dict[int, str] = field(default_factory=dict)
    #: The main image's own PE header, separately from the process policy.
    image_cfg: Optional[bool] = None
    image_aslr: Optional[bool] = None
    image_high_entropy: Optional[bool] = None
    image_reason: Optional[str] = None

    @property
    def readable(self) -> bool:
        return self.error is None

    def active(self) -> List[str]:
        """Every enforcing mitigation that is on, in a stable order."""
        found = []
        for policy, bit, label in _POLICY_BITS:
            word = self.flags.get(policy)
            if word is not None and word & bit:
                found.append(label)
        return found

    def is_on(self, policy: int, bit: int = 0x1) -> Optional[bool]:
        word = self.flags.get(policy)
        return None if word is None else bool(word & bit)


def describe_protection(value: int) -> str:
    """A PS_PROTECTION byte as Process Explorer words it.

    Bits 0-2 are the type, bits 4-7 the signer: 0x31 is Light + Antimalware.
    """
    kind = value & 0x7
    if kind == 0:
        return "None"
    signer = PROTECTION_SIGNERS.get(value >> 4, f"signer {value >> 4}")
    suffix = "-Light" if kind == 1 else ""
    return f"PsProtectedSigner{signer}{suffix}"


def describe_dep(flags: int, permanent: bool) -> str:
    if not flags & 0x1:
        return "Disabled" + (" (permanent)" if permanent else "")
    text = "Enabled"
    if flags & 0x2:
        text += ", ATL thunk emulation off"
    return text + (" (permanent)" if permanent else "")


def describe_cfg(report: MitigationReport) -> str:
    """The honest CFG line: the process policy AND the image's own flag."""
    process = report.is_on(CFG)
    if process is None:
        return "—"
    if not process:
        return "Disabled"
    if report.image_cfg is True:
        return "Enabled"
    if report.image_cfg is False:
        return ("Enabled for system DLLs only — this program's own image "
                "was not compiled with CFG")
    return "Enabled (the image's own flag could not be read)"


def describe_aslr(report: MitigationReport) -> str:
    word = report.flags.get(ASLR)
    if word is None:
        return "—"
    if report.image_aslr is False:
        # A non-/DYNAMICBASE image loads at its preferred base unless
        # "force relocate" is on.
        if not word & 0x2:
            return "Not supported by the image (loads at a fixed base)"
    parts = []
    if word & 0x1 or report.image_aslr:
        parts.append("Enabled")
    if word & 0x4 and report.image_high_entropy is not False:
        parts.append("high entropy")
    if word & 0x2:
        parts.append("force relocate")
    return ", ".join(parts) if parts else "Image-only"


# ---- reads ------------------------------------------------------------

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetProcessMitigationPolicy.restype = wintypes.BOOL
_kernel32.GetProcessMitigationPolicy.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
_ntdll.NtQueryInformationProcess.restype = ctypes.c_long
_ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG)]


def read_mitigations(pid: int, image_path: Optional[str] = None,
                     architecture: Optional[str] = None) -> MitigationReport:
    """Protection, DEP, every enforcing mitigation, and the image's flags.

    `architecture` ("x64"/"ARM64"/"x86") lets DEP be stated for a 64-bit
    process whose DEP read is refused. Never raises.
    """
    report = MitigationReport(pid=pid)
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                   False, pid)
    if not handle:
        report.error = _reason(ctypes.get_last_error())
        return report
    try:
        _read_protection(handle, report)
        for policy in _QUERIED:
            word, why = _policy_word(handle, policy)
            if word is None:
                report.refused[policy] = why
            else:
                report.flags[policy] = word
    finally:
        _kernel32.CloseHandle(handle)
    _read_dep(pid, architecture, report)
    _read_image(image_path, report)
    return report


def _read_protection(handle, report: MitigationReport) -> None:
    value = ctypes.c_ubyte(0)
    returned = wintypes.ULONG(0)
    status = _ntdll.NtQueryInformationProcess(
        handle, _ProcessProtectionInformation, ctypes.byref(value), 1,
        ctypes.byref(returned))
    if status != 0:
        logger.debug("ProcessProtectionInformation: 0x%08X", status & 0xFFFFFFFF)
        return
    report.protection_raw = value.value
    report.protection = describe_protection(value.value)


def _policy_word(handle, policy: int) -> Tuple[Optional[int], Optional[str]]:
    buffer = (ctypes.c_uint32 * 2)()
    if _kernel32.GetProcessMitigationPolicy(handle, policy, buffer, 4):
        return int(buffer[0]), None
    return None, _reason(ctypes.get_last_error())


def _read_dep(pid: int, architecture: Optional[str],
              report: MitigationReport) -> None:
    """DEP needs PROCESS_QUERY_INFORMATION -- measured, the limited right
    is refused for every process. A 64-bit process needs no read at all."""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if handle:
        try:
            buffer = (ctypes.c_uint32 * 2)()
            if _kernel32.GetProcessMitigationPolicy(handle, DEP, buffer, 8):
                report.dep = describe_dep(int(buffer[0]),
                                          bool(buffer[1] & 0xFF))
                return
            why = _reason(ctypes.get_last_error())
        finally:
            _kernel32.CloseHandle(handle)
    else:
        why = _reason(ctypes.get_last_error())
    if architecture in ("x64", "ARM64"):
        report.dep = "Enabled (permanent — always on for 64-bit processes)"
        return
    report.dep_reason = why


def _read_image(path: Optional[str], report: MitigationReport) -> None:
    if not path:
        report.image_reason = "no image path"
        return
    characteristics, why = pe_dll_characteristics(path)
    if characteristics is None:
        report.image_reason = why
        return
    report.image_cfg = bool(characteristics & PE_GUARD_CF)
    report.image_aslr = bool(characteristics & PE_DYNAMIC_BASE)
    report.image_high_entropy = bool(characteristics & PE_HIGH_ENTROPY_VA)


def pe_dll_characteristics(path: str) -> Tuple[Optional[int], Optional[str]]:
    """`DllCharacteristics` from a PE file's optional header.

    At offset 70 of the optional header in both PE32 and PE32+ -- the two
    layouts diverge only after it. Reads 4 KB; never the whole file.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError as error:
        return None, error.strerror or str(error)
    return parse_dll_characteristics(head)


def parse_dll_characteristics(head: bytes
                              ) -> Tuple[Optional[int], Optional[str]]:
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None, "not a PE image"
    offset = struct.unpack_from("<I", head, 0x3C)[0]
    field_at = offset + 24 + 70
    if field_at + 2 > len(head) or head[offset:offset + 4] != b"PE\0\0":
        return None, "PE header not where the DOS header says"
    return struct.unpack_from("<H", head, field_at)[0], None


def _reason(code: int) -> str:
    if not code:
        return "unknown error"
    try:
        return (ctypes.WinError(code).strerror or f"error {code}").rstrip(".")
    except Exception:  # noqa: BLE001 - a number is still a reason
        return f"error {code}"


def summarize(report: MitigationReport) -> List[Tuple[str, str]]:
    """The (label, value) rows the Security view shows above the list."""
    if not report.readable:
        return [("Mitigations", f"could not be read — {report.error}")]
    return [
        ("Protection", report.protection or "—"),
        ("DEP", report.dep or f"— ({report.dep_reason or 'unknown'})"),
        ("ASLR", describe_aslr(report)),
        ("CFG", describe_cfg(report)),
    ]
