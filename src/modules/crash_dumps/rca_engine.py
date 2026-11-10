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


def decode_bugcheck_params(code: int, params: Tuple[int, ...]) -> List[str]:
    """Provide specific plain-English meaning for bugcheck parameters."""
    if not params:
        return []
    p1 = params[0] if len(params) > 0 else 0
    p2 = params[1] if len(params) > 1 else 0
    p3 = params[2] if len(params) > 2 else 0
    p4 = params[3] if len(params) > 3 else 0

    if code == 0x133:  # DPC_WATCHDOG_VIOLATION
        if p1 == 0:
            return [
                "Param 1: 0x0 — Single DPC watchdog timeout (one DPC routine ran too long)",
                f"Param 2: 0x{p2:X} — DPC time limit (in ticks)",
                f"Param 3: 0x{p3:X} — Watchdog sequence count",
                f"Param 4: 0x{p4:X} — Reserved",
            ]
        elif p1 == 1:
            return [
                "Param 1: 0x1 — Cumulative DPC watchdog timeout (total time at DISPATCH_LEVEL exceeded limit)",
                f"Param 2: 0x{p2:X} — Watchdog time limit",
                f"Param 3: 0x{p3:X} — Watchdog sequence count",
                f"Param 4: 0x{p4:X} — Reserved",
            ]
    elif code == 0x9F:  # DRIVER_POWER_STATE_FAILURE
        if p1 == 3:
            return [
                "Param 1: 0x3 — A device object has been blocking a power IRP for too long",
                f"Param 2: 0x{p2:X} — Target device object pointer",
                f"Param 3: 0x{p3:X} — Blocking IRP pointer",
                f"Param 4: 0x{p4:X} — Blocking driver object pointer",
            ]
        elif p1 == 4:
            return [
                "Param 1: 0x4 — The power manager timed out waiting for a power request",
                f"Param 2: 0x{p2:X} — Target device object pointer",
                f"Param 3: 0x{p3:X} — Blocking IRP pointer",
                f"Param 4: 0x{p4:X} — Blocking driver object pointer",
            ]
    elif code == 0x124:  # WHEA_UNCORRECTABLE_ERROR
        whea_types = {
            0: "Machine Check Exception (MCE)",
            1: "Corrected Machine Check (CMC)",
            2: "Corrected Platform Error (CPE)",
            3: "Non-Maskable Interrupt (NMI)",
            4: "PCI Express Error (AER)",
            5: "Generic Hardware Error",
        }
        name = whea_types.get(p1, f"Hardware Error Type {p1}")
        return [
            f"Param 1: 0x{p1:X} — Error Source: {name}",
            f"Param 2: 0x{p2:X} — Pointer to WHEA_ERROR_RECORD structure",
            f"Param 3: 0x{p3:X} — MCi_STATUS high bits / error payload",
            f"Param 4: 0x{p4:X} — MCi_STATUS low bits / error payload",
        ]
    elif code == 0xD1:  # DRIVER_IRQL_NOT_LESS_OR_EQUAL
        irql_names = {0: "PASSIVE_LEVEL", 1: "APC_LEVEL", 2: "DISPATCH_LEVEL", 12: "SYNCH_LEVEL", 15: "HIGH_LEVEL"}
        irql_str = irql_names.get(p2, f"IRQL {p2}")
        access_str = "Execute" if p3 == 8 else ("Write" if p3 == 1 else "Read")
        return [
            f"Param 1: 0x{p1:X} — Memory address referenced",
            f"Param 2: 0x{p2:X} — {irql_str} at time of access",
            f"Param 3: 0x{p3:X} — Operation: {access_str}",
            f"Param 4: 0x{p4:X} — Code address which attempted the reference",
        ]

    return [f"Param {i+1}: 0x{p:X}" for i, p in enumerate(params)]


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
        known = bugcheck_info(code)
        diag = f"Kernel BugCheck {lbl} (0x{code:X})"
        cause = known.cause if known else f"Kernel stopped with bugcheck code 0x{code:X}."

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
            incident = CrashIncident(
                timestamp=t,
                category="live_hang",
                title=f"Driver Timeout / Live Recovery ({raw.get('live_kind', 'Watchdog')})",
                summary=entry.message,
                bugcheck_code=raw.get("bugcheck_code"),
                bugcheck_label=raw.get("bug_check", "Live Kernel Report"),
                evidence_sources=["LiveKernelReports"],
                actionable_advice="Windows recovered without rebooting. Frequently caused by GPU TDR (driver hang).",
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

            if matched_incident:
                matched_incident.evidence_sources.append(src_name)
                if not matched_incident.bugcheck_label:
                    matched_incident.bugcheck_label = label
                if code_int and not matched_incident.bugcheck_code:
                    matched_incident.bugcheck_code = code_int
                continue

            bsod_count += 1
            incident = CrashIncident(
                timestamp=t,
                category="bsod",
                title=f"BSOD: {label}",
                summary=raw.get("cause") or entry.message,
                bugcheck_code=code_int,
                bugcheck_label=label,
                faulting_module=raw.get("faulting_module", ""),
                evidence_sources=[src_name],
                actionable_advice="Kernel crash dump captured.",
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

