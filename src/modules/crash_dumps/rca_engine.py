"""Root Cause Analysis (RCA) engine for Windows crashes, hangs, and hardware errors.

Synthesizes multiple diagnostic sources into actionable findings:
1. Kernel-Power Event 41: Dirty shutdowns, differentiating sudden power cuts (Bugcheck 0)
   from un-dumped BSODs (Bugcheck != 0).
2. WHEA-Logger Events (17, 18, 19, 20, 47): Machine Check Exceptions (MCE), corrected/uncorrected
   errors, mapping APIC IDs to processor cores.
3. Eventlog Event 6008: Unexpected shutdowns.
4. WER Event 1001 / Crash Dumps: Bugcheck codes, parameters, and dump files.

Qt-free, testable on any platform without a display or elevation.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from core.diag_knowledge import bugcheck_info, bugcheck_label
from core.types import LogEntry
from modules.crash_dumps.driver_fingerprint import extract_driver_culprit, get_driver_info, is_third_party_driver

logger = logging.getLogger(__name__)


@dataclass
class KernelPowerRecord:
    timestamp: datetime
    bugcheck_code: int
    bugcheck_params: Tuple[int, int, int, int] = (0, 0, 0, 0)
    power_button_timestamp: int = 0
    sleep_in_progress: int = 0
    is_power_cut: bool = False
    diagnosis: str = ""
    cause: str = ""


@dataclass
class WheaRecord:
    timestamp: datetime
    event_id: int
    error_source: str
    apic_id: Optional[int] = None
    bank: Optional[int] = None
    status_hex: str = ""
    is_fatal: bool = False
    description: str = ""


@dataclass
class CrashIncident:
    """A correlated crash, hang or unexpected shutdown incident."""
    timestamp: datetime
    category: str  # "bsod", "power_cut", "hardware_whea", "live_hang", "unexpected_shutdown"
    title: str
    summary: str
    bugcheck_code: Optional[int] = None
    bugcheck_label: str = ""
    params: Tuple[int, ...] = ()
    faulting_module: str = ""
    apic_id: Optional[int] = None
    evidence_sources: List[str] = field(default_factory=list)
    actionable_advice: str = ""


@dataclass
class RcaOverview:
    total_incidents: int = 0
    bsod_count: int = 0
    power_cut_count: int = 0
    whea_count: int = 0
    live_hang_count: int = 0
    incidents: List[CrashIncident] = field(default_factory=list)
    suspect_summary: str = ""
    core_whea_counts: Dict[int, int] = field(default_factory=dict)


_EXC_CODES: Dict[int, str] = {
    0xC0000005: "STATUS_ACCESS_VIOLATION (Memory access violation)",
    0xC000001D: "STATUS_ILLEGAL_INSTRUCTION",
    0xC0000094: "STATUS_INTEGER_DIVIDE_BY_ZERO",
    0x80000003: "STATUS_BREAKPOINT",
    0xC0000006: "STATUS_IN_PAGE_ERROR (Disk or paging read failure)",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN (Fast fail security violation)",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000022: "STATUS_ACCESS_DENIED",
}

_IRQL_NAMES: Dict[int, str] = {
    0: "PASSIVE_LEVEL",
    1: "APC_LEVEL",
    2: "DISPATCH_LEVEL",
    12: "SYNCH_LEVEL",
    15: "HIGH_LEVEL",
}

_SEC_CHECK_TYPES: Dict[int, str] = {
    0: "Stack-based buffer overrun detected (GS check failure)",
    1: "VTGuard check failure",
    2: "Stack cookie (canary) corrupted",
    3: "LIST_ENTRY doubly-linked list corruption (corrupted pointers during remove)",
    4: "Reserved",
    5: "Invalid parameter passed to security check",
    6: "Stack cookie initialization failure",
    7: "Fatal exception handler list corrupted",
    8: "Single-bit error detected in pool allocation (indicates bad RAM)",
    9: "Fast fail range check failure",
    10: "Kernel Control Flow Guard (CFG/XFG) check failure",
    12: "Long jump buffer corruption",
    13: "Protected memory corruption",
    14: "Critical object type corruption",
    20: "Corrupted linked list entry",
    21: "Corrupted pool list entry",
    25: "Bad list entry insertion",
}

_FAULT_50_TYPES: Dict[int, str] = {
    0: "Read or write on nonpaged memory / freed pool",
    1: "Page not present",
    2: "Transition fault",
    3: "Prototype PTE fault",
    16: "Execute fault on non-executable memory",
}


def get_bugcheck_sysadmin_explanation(code: int, params: Tuple[int, ...] = ()) -> str:
    """Return an authoritative, plain-English explanation and diagnostic direction for top bugchecks."""
    c = code & 0xFFFFFFFF
    if c == 0x0A:
        return (
            "A kernel routine or driver tried to access pageable (or invalid) memory while running at an elevated "
            "IRQL (typically DISPATCH_LEVEL). Page faults cannot be serviced at elevated IRQL. Usually caused by a "
            "buggy third-party driver dereferencing an invalid pointer, pool corruption, or faulty RAM."
        )
    elif c == 0x1E:
        return (
            "A kernel-mode program or driver generated an exception that was not caught by an error handler (most often "
            "Access Violation 0xC0000005). The driver attempted an illegal operation such as dereferencing a NULL or freed "
            "pointer. Usually caused by incompatible or outdated hardware drivers, system filter drivers, or memory corruption."
        )
    elif c == 0x3B:
        return (
            "An unhandled exception occurred during execution of a system service routine transitioning from user to kernel mode. "
            "Most commonly an Access Violation (0xC0000005) inside graphics display drivers (GPU), DirectX, filesystem filter "
            "drivers, or security/antivirus hooks."
        )
    elif c == 0x50:
        return (
            "Invalid system memory was referenced that cannot be found or paged in. Commonly caused by a buggy driver "
            "referencing freed pool memory or an invalid pointer, defective or mismatched RAM modules, faulty CPU cache, "
            "or filesystem/antivirus corruption."
        )
    elif c == 0x7E:
        return (
            "A background system thread generated an unhandled exception. This frequently occurs after hardware or driver "
            "updates. The faulting instruction pointer or stack trace almost always points directly to the problematic third-party driver."
        )
    elif c == 0x9F:
        return (
            "A device driver failed to handle or complete a sleep, hibernate, or wake power state transition IRP within the "
            "timeout period. Very common with USB hubs/controllers, Wi-Fi/Bluetooth adapters, NVMe storage controllers, and GPU "
            "drivers failing to enter or wake from low-power states."
        )
    elif c == 0xD1:
        return (
            "A driver accessed pageable memory (or an invalid memory address) at an elevated IRQL (typically DISPATCH_LEVEL). "
            "Unlike 0x0A, 0xD1 almost always specifically points to a third-party driver bug (especially network, Wi-Fi, VPN virtual "
            "NICs, audio, or storage drivers)."
        )
    elif c == 0x124:
        return (
            "A fatal hardware error detected by the CPU or platform chipset via WHEA. Typically caused by unstable CPU/RAM "
            "overclocking/undervolting (insufficient Vcore/curve optimizer), inadequate power delivery, overheating, or a failing "
            "PCIe peripheral/NVMe drive."
        )
    elif c == 0x133:
        return (
            "A device driver held execution at DISPATCH_LEVEL (DPC) longer than the kernel watchdog threshold (typically 10-30 seconds). "
            "Most frequently caused by SSD/NVMe controller firmware hangs, outdated storage (storport/iaStor) or Wi-Fi/NIC drivers, "
            "or hardware bus stalls."
        )
    elif c == 0x139:
        return (
            "A critical kernel security integrity check failed because internal data structures (such as a stack canary cookie or "
            "kernel doubly-linked list) were corrupted. Typically caused by a buggy driver overrunning a memory buffer, bad RAM modifying "
            "kernel pointers, or conflicting low-level security/antivirus drivers."
        )
    known = bugcheck_info(code)
    return known.cause if known else f"Kernel stopped with bugcheck code 0x{code:X}."


def decode_bugcheck_params(code: int, params: Tuple[int, ...]) -> List[str]:
    """Provide specific plain-English meaning for bugcheck parameters for all top bugchecks."""
    if not params:
        return []
    p1 = params[0] if len(params) > 0 else 0
    p2 = params[1] if len(params) > 1 else 0
    p3 = params[2] if len(params) > 2 else 0
    p4 = params[3] if len(params) > 3 else 0
    c = code & 0xFFFFFFFF

    res = _decode_params_irql_kmode(c, p1, p2, p3, p4)
    if res is not None:
        return res
    res = _decode_params_power_whea_dpc(c, p1, p2, p3, p4)
    if res is not None:
        return res
    return [f"Param {i+1}: 0x{p:X}" for i, p in enumerate(params)]


def _decode_params_irql_kmode(c: int, p1: int, p2: int, p3: int, p4: int) -> Optional[List[str]]:
    if c == 0x0A:
        irql_str = _IRQL_NAMES.get(p2, f"IRQL {p2}")
        access_str = "Execute" if p3 == 8 else ("Write" if p3 == 1 else "Read")
        return [
            f"Param 1: 0x{p1:X} — Memory address referenced",
            f"Param 2: 0x{p2:X} — IRQL at the time: {irql_str}",
            f"Param 3: 0x{p3:X} — Operation: {access_str}",
            f"Param 4: 0x{p4:X} — Instruction address attempting the reference",
        ]
    if c == 0x1E:
        exc_name = _EXC_CODES.get(p1, f"Exception 0x{p1:X}")
        sub_info = "Access type (0=Read, 1=Write, 8=Execute)" if p1 == 0xC0000005 else "Exception sub-parameter 0"
        target_info = "Target address referenced" if p1 == 0xC0000005 else "Exception sub-parameter 1"
        return [
            f"Param 1: 0x{p1:X} — Unhandled exception code: {exc_name}",
            f"Param 2: 0x{p2:X} — Faulting instruction address (RIP/EIP)",
            f"Param 3: 0x{p3:X} — {sub_info}",
            f"Param 4: 0x{p4:X} — {target_info}",
        ]
    if c == 0x3B:
        exc_name = _EXC_CODES.get(p1, f"Exception 0x{p1:X}")
        return [
            f"Param 1: 0x{p1:X} — Exception code: {exc_name}",
            f"Param 2: 0x{p2:X} — Faulting instruction address (RIP/EIP)",
            f"Param 3: 0x{p3:X} — Exception context record pointer (WinDbg: .cxr 0x{p3:X})",
            f"Param 4: 0x{p4:X} — Reserved (0)",
        ]
    if c == 0x50:
        access_str = "Execute" if p2 in (2, 8) else ("Write" if p2 == 1 else "Read")
        fault_desc = _FAULT_50_TYPES.get(p4, f"Fault code {p4}")
        return [
            f"Param 1: 0x{p1:X} — Invalid memory address referenced",
            f"Param 2: 0x{p2:X} — Operation: {access_str}",
            f"Param 3: 0x{p3:X} — Faulting instruction address (RIP/EIP)",
            f"Param 4: 0x{p4:X} — Fault type: {fault_desc}",
        ]
    if c == 0x7E:
        exc_name = _EXC_CODES.get(p1, f"Exception 0x{p1:X}")
        return [
            f"Param 1: 0x{p1:X} — Exception code: {exc_name}",
            f"Param 2: 0x{p2:X} — Faulting instruction address (RIP/EIP)",
            f"Param 3: 0x{p3:X} — Exception record pointer (WinDbg: .exr 0x{p3:X})",
            f"Param 4: 0x{p4:X} — Context record pointer (WinDbg: .cxr 0x{p4:X})",
        ]
    return None


def _decode_params_power_whea_dpc(c: int, p1: int, p2: int, p3: int, p4: int) -> Optional[List[str]]:
    if c == 0x9F:
        subtypes = {
            1: "A device object being freed still has pending power IRPs",
            2: "Driver failed to complete a power IRP in the allotted time",
            3: "A device object has been blocking a power IRP for too long (>90% of cases)",
            4: "The power manager timed out waiting for a power request",
            5: "Failure to complete a directed power transition",
        }
        sub_desc = subtypes.get(p1, f"Subtype {p1}")
        if p1 in (3, 4):
            return [
                f"Param 1: 0x{p1:X} — Subtype: {sub_desc}",
                f"Param 2: 0x{p2:X} — Target device object pointer (WinDbg: !devobj 0x{p2:X})",
                f"Param 3: 0x{p3:X} — Blocking IRP pointer (WinDbg: !irp 0x{p3:X})",
                f"Param 4: 0x{p4:X} — Blocking driver object pointer (WinDbg: !drvobj 0x{p4:X})",
            ]
        return [
            f"Param 1: 0x{p1:X} — Subtype: {sub_desc}",
            f"Param 2: 0x{p2:X} — Device object / Context pointer",
            f"Param 3: 0x{p3:X} — IRP / Request pointer",
            f"Param 4: 0x{p4:X} — Driver object / Status",
        ]
    if c == 0xD1:
        irql_str = _IRQL_NAMES.get(p2, f"IRQL {p2}")
        access_str = "Execute" if p3 == 8 else ("Write" if p3 == 1 else "Read")
        return [
            f"Param 1: 0x{p1:X} — Memory address referenced",
            f"Param 2: 0x{p2:X} — IRQL at the time: {irql_str}",
            f"Param 3: 0x{p3:X} — Operation: {access_str}",
            f"Param 4: 0x{p4:X} — Code address which attempted the reference",
        ]
    if c == 0x124:
        whea_types = {
            0: "Machine Check Exception (MCE)", 1: "Corrected Machine Check (CMC)",
            2: "Corrected Platform Error (CPE)", 3: "Non-Maskable Interrupt (NMI)",
            4: "PCI Express Error (AER)", 5: "Generic Hardware Error",
        }
        name = whea_types.get(p1, f"Hardware Error Type {p1}")
        return [
            f"Param 1: 0x{p1:X} — Error Source: {name}",
            f"Param 2: 0x{p2:X} — Pointer to WHEA_ERROR_RECORD structure (WinDbg: !errrec 0x{p2:X})",
            f"Param 3: 0x{p3:X} — MCi_STATUS high bits / error payload",
            f"Param 4: 0x{p4:X} — MCi_STATUS low bits / error payload",
        ]
    if c == 0x133:
        if p1 == 0:
            return [
                "Param 1: 0x0 — Single DPC watchdog timeout (one DPC routine ran too long)",
                f"Param 2: 0x{p2:X} — DPC time limit (in ticks)",
                f"Param 3: 0x{p3:X} — Watchdog sequence count",
                f"Param 4: 0x{p4:X} — Reserved",
            ]
        if p1 == 1:
            return [
                "Param 1: 0x1 — Cumulative DPC watchdog timeout (total time at DISPATCH_LEVEL exceeded limit)",
                f"Param 2: 0x{p2:X} — Watchdog time limit",
                f"Param 3: 0x{p3:X} — Watchdog sequence count",
                f"Param 4: 0x{p4:X} — Reserved",
            ]
        if p1 == 2:
            return [
                "Param 1: 0x2 — DPC watchdog detected a hang during an interrupt handler",
                f"Param 2: 0x{p2:X} — Watchdog ticks",
                f"Param 3: 0x{p3:X} — Reserved",
                f"Param 4: 0x{p4:X} — Reserved",
            ]
    if c == 0x139:
        check_name = _SEC_CHECK_TYPES.get(p1, f"Security check failure {p1}")
        return [
            f"Param 1: 0x{p1:X} — Corruption type: {check_name}",
            f"Param 2: 0x{p2:X} — Exception / instruction address (RIP)",
            f"Param 3: 0x{p3:X} — Context record pointer (WinDbg: .cxr 0x{p3:X})",
            f"Param 4: 0x{p4:X} — Sub-type details / Reserved",
        ]
    return None


def parse_kernel_power_41(raw_data: dict, timestamp: datetime) -> KernelPowerRecord:
    """Parse structured event data from a Kernel-Power Event 41."""
    def _int(val: any) -> int:
        if val is None:
            return 0
        if isinstance(val, int):
            return val
        s = str(val).strip()
        try:
            return int(s, 16) if s.lower().startswith("0x") else int(s)
        except (ValueError, TypeError):
            return 0

    code = _int(raw_data.get("BugcheckCode"))
    p1 = _int(raw_data.get("BugcheckParameter1"))
    p2 = _int(raw_data.get("BugcheckParameter2"))
    p3 = _int(raw_data.get("BugcheckParameter3"))
    p4 = _int(raw_data.get("BugcheckParameter4"))
    btn = _int(raw_data.get("PowerButtonTimestamp"))
    sleep = _int(raw_data.get("SleepInProgress"))

    if code == 0:
        is_power_cut = True
        diag = "Abrupt Power Loss / Hard Freeze / Manual Reset (BugCheck 0x0)"
        cause = (
            "The system rebooted without writing a bugcheck code. This indicates an abrupt power "
            "interruption (PSU cut, power outage), thermal protection trip, system lockup followed by a "
            "manual reset button press, or holding the power button. The Windows kernel did not encounter "
            "a software crash exception before power dropped."
        )
    else:
        is_power_cut = False
        lbl = bugcheck_label(code)
        diag = f"Kernel BugCheck {lbl} (0x{code:X})"
        cause = get_bugcheck_sysadmin_explanation(code, (p1, p2, p3, p4))

    return KernelPowerRecord(
        timestamp=timestamp,
        bugcheck_code=code,
        bugcheck_params=(p1, p2, p3, p4),
        power_button_timestamp=btn,
        sleep_in_progress=sleep,
        is_power_cut=is_power_cut,
        diagnosis=diag,
        cause=cause,
    )


def parse_whea_record(event_id: int, raw_data: dict, message: str, timestamp: datetime) -> WheaRecord:
    """Parse WHEA hardware error fields from event data and XML text."""
    apic_id = None
    bank = None
    status_hex = ""
    error_source = "Unknown"

    if "MCi_STATUS" in raw_data:
        status_hex = str(raw_data["MCi_STATUS"])
    if "ErrorSource" in raw_data:
        error_source = str(raw_data["ErrorSource"])
    if "ProcessorAPICID" in raw_data or "ApicId" in raw_data:
        try:
            apic_id = int(raw_data.get("ProcessorAPICID", raw_data.get("ApicId")))
        except (ValueError, TypeError) as exc:
            logger.debug("Failed parsing apic_id: %s", exc)
    if "Bank" in raw_data:
        try:
            bank = int(raw_data["Bank"])
        except (ValueError, TypeError) as exc:
            logger.debug("Failed parsing bank: %s", exc)

    # Fallback to regex on message text if structured data is absent
    if apic_id is None:
        m = re.search(r"APIC\s*ID[:\s]+(\d+)", message, re.IGNORECASE)
        if m:
            apic_id = int(m.group(1))

    if bank is None:
        m = re.search(r"Bank[:\s]+(\d+)", message, re.IGNORECASE)
        if m:
            bank = int(m.group(1))

    is_fatal = (event_id == 18)  # Event 18 is fatal MCE; 19, 47 are corrected

    if event_id == 18:
        desc = "Fatal Machine Check Exception (MCE)"
    elif event_id == 19:
        desc = "Corrected Machine Check (Processor Core / Cache)"
    elif event_id in (17, 20):
        desc = "PCI Express (PCIe) Bus / AER Error"
    elif event_id == 47:
        desc = "Corrected Memory / Hardware Error"
    else:
        desc = f"WHEA Hardware Error (Event {event_id})"

    if apic_id is not None:
        desc += f" on APIC ID {apic_id}"

    return WheaRecord(
        timestamp=timestamp,
        event_id=event_id,
        error_source=error_source,
        apic_id=apic_id,
        bank=bank,
        status_hex=status_hex,
        is_fatal=is_fatal,
        description=desc,
    )


def correlate_incidents(
    entries: List[LogEntry],
    window_seconds: int = 120,
) -> RcaOverview:
    """Correlate crash dumps, WER 1001, Kernel-Power 41, and WHEA events into unified incidents."""
    incidents: List[CrashIncident] = []
    core_whea_counts: Dict[int, int] = {}
    bsod_count = 0
    power_cut_count = 0
    whea_count = 0
    live_hang_count = 0

    # Sort entries by timestamp ascending for grouping
    sorted_entries = sorted(entries, key=lambda e: e.timestamp)
    used_timestamps: List[Tuple[datetime, CrashIncident]] = []

    for entry in sorted_entries:
        raw = entry.raw or {}
        source = entry.source.lower()
        t = entry.timestamp

        # Check if already correlated into an existing incident within the time window
        matched_incident: Optional[CrashIncident] = None
        for inc_time, inc in used_timestamps:
            if abs((t - inc_time).total_seconds()) <= window_seconds:
                matched_incident = inc
                break

        # 1. Kernel-Power 41
        if "kernel-power" in source and raw.get("event_id") == 41:
            kp = parse_kernel_power_41(raw.get("data", {}), t)
            if kp.is_power_cut:
                power_cut_count += 1
                incident = CrashIncident(
                    timestamp=t,
                    category="power_cut",
                    title="Dirty Shutdown / Sudden Power Cut",
                    summary=kp.cause,
                    evidence_sources=["Kernel-Power 41"],
                    actionable_advice=(
                        "Suspect power delivery (PSU, surge/outage), overheating cut-off, or physical "
                        "reset. If games cut out suddenly under full GPU/CPU load, check PSU 12V rails and transient capacity."
                    ),
                )
                incidents.append(incident)
                used_timestamps.append((t, incident))
            else:
                bsod_count += 1
                incident = CrashIncident(
                    timestamp=t,
                    category="bsod",
                    title=kp.diagnosis,
                    summary=kp.cause,
                    bugcheck_code=kp.bugcheck_code,
                    bugcheck_label=bugcheck_label(kp.bugcheck_code),
                    params=kp.bugcheck_params,
                    evidence_sources=["Kernel-Power 41 (Bugcheck)"],
                    actionable_advice="A blue screen occurred before the machine reset.",
                )
                incidents.append(incident)
                used_timestamps.append((t, incident))
            continue

        # 2. WHEA-Logger Events
        if "whea" in source:
            eid = raw.get("event_id", 0)
            whea = parse_whea_record(eid, raw.get("data", {}), entry.message, t)
            whea_count += 1
            if whea.apic_id is not None:
                core_whea_counts[whea.apic_id] = core_whea_counts.get(whea.apic_id, 0) + 1

            if matched_incident and matched_incident.category in ("bsod", "power_cut"):
                matched_incident.evidence_sources.append(f"WHEA {eid}")
                matched_incident.summary += f" Preceded by WHEA {whea.description}."
                if whea.apic_id is not None and matched_incident.apic_id is None:
                    matched_incident.apic_id = whea.apic_id
                continue

            incident = CrashIncident(
                timestamp=t,
                category="hardware_whea",
                title=f"Hardware Error: {whea.description}",
                summary=entry.message or whea.description,
                apic_id=whea.apic_id,
                evidence_sources=[f"WHEA-Logger {eid}"],
                actionable_advice=(
                    "Check CPU curve/voltage margins, memory stability (XMP/EXPO), or reseat PCIe devices. "
                    f"Errors clustered on APIC ID {whea.apic_id} point to a specific core." if whea.apic_id is not None
                    else "Hardware corrected or fatal error reported by platform firmware."
                ),
            )
            incidents.append(incident)
            used_timestamps.append((t, incident))
            continue

        # 3. LiveKernelReports (Watchdog hangs)
        if "livekernelreports" in source or raw.get("live_kind"):
            live_hang_count += 1
            culprit = extract_driver_culprit(raw) or extract_driver_culprit(entry.message)
            title = f"Driver Timeout / Live Recovery ({raw.get('live_kind', 'Watchdog')})"
            advice = "Windows recovered without rebooting. Frequently caused by GPU TDR (driver hang)."
            fault_mod = raw.get("faulting_module", "")
            if culprit:
                fault_mod = culprit.filename
                if culprit.is_third_party:
                    title += f" [Culprit: {culprit.display_tag}]"
                if culprit.remedy:
                    advice = f"{culprit.description}: {culprit.remedy}"

            incident = CrashIncident(
                timestamp=t,
                category="live_hang",
                title=title,
                summary=entry.message,
                bugcheck_code=raw.get("bugcheck_code"),
                bugcheck_label=raw.get("bug_check", "Live Kernel Report"),
                faulting_module=fault_mod,
                evidence_sources=["LiveKernelReports"],
                actionable_advice=advice,
            )
            incidents.append(incident)
            used_timestamps.append((t, incident))
            continue

        # 4. Crash dumps / BugCheck event 1001
        if "minidump" in source or "memory.dmp" in source or "bugcheck" in source:
            code = raw.get("bugcheck_code")
            code_int = None
            if code is not None:
                try:
                    code_int = int(code, 16) if isinstance(code, str) and code.startswith("0x") else int(code)
                except (ValueError, TypeError) as exc:
                    logger.debug("Failed parsing bugcheck code %s: %s", code, exc)

            label = raw.get("bug_check") or (bugcheck_label(code_int) if code_int else "BugCheck")
            src_name = "Minidump" if "minidump" in source else ("MEMORY.DMP" if "memory" in source else "BugCheck 1001")
            culprit = extract_driver_culprit(raw) or extract_driver_culprit(entry.message)

            if matched_incident:
                matched_incident.evidence_sources.append(src_name)
                if not matched_incident.bugcheck_label:
                    matched_incident.bugcheck_label = label
                if code_int and not matched_incident.bugcheck_code:
                    matched_incident.bugcheck_code = code_int
                if culprit and not matched_incident.faulting_module:
                    matched_incident.faulting_module = culprit.filename
                    if culprit.is_third_party and "[Culprit:" not in matched_incident.title:
                        matched_incident.title += f" [Culprit: {culprit.display_tag}]"
                continue

            bsod_count += 1
            title = f"BSOD: {label}"
            advice = get_bugcheck_sysadmin_explanation(code_int, tuple(raw.get("params", ()))) if code_int else "Kernel crash dump captured."
            fault_mod = raw.get("faulting_module", "")
            if culprit:
                fault_mod = culprit.filename
                if culprit.is_third_party:
                    title += f" [Culprit: {culprit.display_tag}]"
                if culprit.remedy:
                    advice += f" Suspect culprit: {culprit.filename} ({culprit.vendor}) — {culprit.remedy}"

            incident = CrashIncident(
                timestamp=t,
                category="bsod",
                title=title,
                summary=raw.get("cause") or entry.message,
                bugcheck_code=code_int,
                bugcheck_label=label,
                faulting_module=fault_mod,
                evidence_sources=[src_name],
                actionable_advice=advice,
            )
            incidents.append(incident)
            used_timestamps.append((t, incident))

    # Formulate suspect summary
    suspect_parts = []
    if bsod_count > 0:
        suspect_parts.append(f"{bsod_count} Blue Screen(s)")
    if power_cut_count > 0:
        suspect_parts.append(f"{power_cut_count} Sudden Power Loss / Lockup(s)")
    if whea_count > 0:
        suspect_parts.append(f"{whea_count} WHEA Hardware Error(s)")
    if live_hang_count > 0:
        suspect_parts.append(f"{live_hang_count} Driver Watchdog Hang(s)")

    if core_whea_counts:
        top_core = max(core_whea_counts.items(), key=lambda item: item[1])
        suspect_parts.append(f"Suspect Hardware: APIC ID {top_core[0]} logged {top_core[1]} error(s)")

    suspect_summary = "; ".join(suspect_parts) if suspect_parts else "No crashes or dirty shutdowns recorded."

    # Return newest-first incidents
    incidents.sort(key=lambda inc: inc.timestamp, reverse=True)

    return RcaOverview(
        total_incidents=len(incidents),
        bsod_count=bsod_count,
        power_cut_count=power_cut_count,
        whea_count=whea_count,
        live_hang_count=live_hang_count,
        incidents=incidents,
        suspect_summary=suspect_summary,
        core_whea_counts=core_whea_counts,
    )

