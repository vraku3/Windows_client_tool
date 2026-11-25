"""Cross-Log Incident Correlation & Event Spike Analyzer (Qt-Free).

Correlates entries across heterogeneous Windows diagnostic sources (Event Viewer,
CBS, DISM, Windows Update, Reliability, Crash Dumps, Timeline, Log Viewer) around
an anchor incident timestamp.

Capabilities:
1. Incident Window Slicing: isolates events in a configurable window (e.g. ±5 min).
2. Velocity & Spike Analysis: detects event storm bursts (>3x average rate or >15/min).
3. Precursor Chain Isolation: identifies failure precursors in the 60-120 seconds
   immediately preceding the primary critical fault.
4. Causal Cascade Detection: matches known multi-system failure chains (GPU TDR -> BSOD,
   Disk Retries -> NTFS Corruption -> App Crash, Missing Component -> WU Failure).
5. Candidate Root Cause Synthesis: ranks likely causes with remediation guidance.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Dict, List, Optional, Sequence, Tuple

from core.types import LogEntry


@dataclass(frozen=True)
class EventSpike:
    """A distinct one-minute period where event density spiked abnormally."""
    minute: datetime
    count: int
    error_count: int
    top_sources: Tuple[Tuple[str, int], ...]


@dataclass(frozen=True)
class CascadePattern:
    """A detected multi-step causal failure pattern across subsystems."""
    name: str
    confidence: str  # "High", "Medium", "Low"
    description: str
    events: Tuple[LogEntry, ...]


@dataclass(frozen=True)
class RootCauseCandidate:
    """A hypothesized root cause for an incident window."""
    title: str
    severity: str     # "Critical", "Error", "Warning"
    confidence: str   # "High", "Medium", "Low"
    explanation: str
    suggested_action: str
    anchor_event: Optional[LogEntry] = None
    supporting_events: Tuple[LogEntry, ...] = ()


@dataclass
class IncidentCorrelation:
    """Complete correlation report for a specific time window."""
    anchor_time: datetime
    window_minutes: int
    total_events: int
    error_count: int
    warning_count: int
    events: List[LogEntry] = field(default_factory=list)
    spikes: List[EventSpike] = field(default_factory=list)
    precursors: List[LogEntry] = field(default_factory=list)
    cascades: List[CascadePattern] = field(default_factory=list)
    root_causes: List[RootCauseCandidate] = field(default_factory=list)
    summary_text: str = ""


# Regular expressions for error code and component signatures
_RE_HRESULT = re.compile(r"\b0[xX](8[0-9a-fA-F]{7})\b")
_RE_NTSTATUS = re.compile(r"\b0[xX]([cC][0-9a-fA-F]{7})\b")
_RE_BUGCHECK = re.compile(r"\b0[xX](000000[0-9a-fA-F]{2}|[0-9a-fA-F]{1,3})\b")


def _is_error(level: str) -> bool:
    lvl = (level or "").lower()
    return "err" in lvl or "crit" in lvl or "fatal" in lvl


def _is_warning(level: str) -> bool:
    lvl = (level or "").lower()
    return "warn" in lvl


def _get_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def correlate_incident_window(
    anchor_time: datetime,
    entries_by_source: Dict[str, Sequence[LogEntry]],
    window_minutes: int = 5,
    precursor_seconds: int = 120,
) -> IncidentCorrelation:
    """Correlate entries across sources around `anchor_time` within `±window_minutes`."""
    if anchor_time is None:
        raise ValueError("anchor_time cannot be None")

    window_delta = timedelta(minutes=max(1, window_minutes))
    t_start = anchor_time - window_delta
    t_end = anchor_time + window_delta

    # Flatten and slice entries within the time window
    in_window: List[LogEntry] = []
    for source_name, entry_list in entries_by_source.items():
        for entry in entry_list:
            if not entry.timestamp:
                continue
            if t_start <= entry.timestamp <= t_end:
                in_window.append(entry)

    # Sort strictly chronologically
    in_window.sort(key=lambda e: e.timestamp)

    total_events = len(in_window)
    error_count = sum(1 for e in in_window if _is_error(e.level))
    warning_count = sum(1 for e in in_window if _is_warning(e.level))

    # ── 1. Velocity & Spike Analysis ──────────────────────────────────
    minute_buckets: Dict[datetime, List[LogEntry]] = defaultdict(list)
    for e in in_window:
        minute_buckets[_get_minute(e.timestamp)].append(e)

    spikes: List[EventSpike] = []
    if minute_buckets:
        avg_rate = total_events / max(1, len(minute_buckets))
        for minute_dt, m_entries in sorted(minute_buckets.items()):
            m_count = len(m_entries)
            m_errors = sum(1 for e in m_entries if _is_error(e.level))
            # Spike condition: > 15 events/min or (count >= 5 and count >= 2.5 * avg_rate) or m_errors >= 5
            if m_count >= 15 or (m_count >= 5 and m_count >= 2.5 * avg_rate) or m_errors >= 5:
                src_counts = Counter(e.source for e in m_entries).most_common(3)
                spikes.append(EventSpike(
                    minute=minute_dt,
                    count=m_count,
                    error_count=m_errors,
                    top_sources=tuple(src_counts),
                ))

    # ── 2. Precursor Chain Detection ───────────────────────────────────
    # Identify the earliest critical/error event in the window, or anchor_time
    earliest_crit = anchor_time
    for e in in_window:
        if _is_error(e.level):
            earliest_crit = e.timestamp
            break

    p_start = earliest_crit - timedelta(seconds=max(30, precursor_seconds))
    precursors = [
        e for e in in_window
        if p_start <= e.timestamp < earliest_crit
        and (_is_error(e.level) or _is_warning(e.level) or _is_significant_precursor(e))
    ]

    # ── 3. Causal Cascades ─────────────────────────────────────────────
    cascades = _detect_cascades(in_window)

    # ── 4. Candidate Root Cause Synthesis ──────────────────────────────
    root_causes = _synthesize_root_causes(anchor_time, in_window, precursors, cascades)

    # Build summary text
    summary_parts = [
        f"Correlated {total_events} events ({error_count} errors, {warning_count} warnings) "
        f"within ±{window_minutes}m of {anchor_time.strftime('%Y-%m-%d %H:%M:%S')}."
    ]
    if spikes:
        summary_parts.append(f"Detected {len(spikes)} event rate spike(s).")
    if cascades:
        cascade_names = ", ".join(c.name for c in cascades)
        summary_parts.append(f"Identified causal cascade(s): {cascade_names}.")
    if root_causes:
        top_rc = root_causes[0]
        summary_parts.append(f"Primary Root Cause Candidate: {top_rc.title} ({top_rc.confidence} confidence).")

    summary_text = " ".join(summary_parts)

    return IncidentCorrelation(
        anchor_time=anchor_time,
        window_minutes=window_minutes,
        total_events=total_events,
        error_count=error_count,
        warning_count=warning_count,
        events=in_window,
        spikes=spikes,
        precursors=precursors,
        cascades=cascades,
        root_causes=root_causes,
        summary_text=summary_text,
    )


def _is_significant_precursor(entry: LogEntry) -> bool:
    """Check if an event carries failure precursor signatures regardless of log level."""
    src = (entry.source or "").lower()
    msg = (entry.message or "").lower()
    keywords = (
        "reset", "timeout", "timed out", "retry", "exhausted", "corrupt",
        "terminated", "unresponsive", "tdr", "hang", "page fault", "delayed"
    )
    if any(k in msg for k in keywords):
        return True
    if any(s in src for s in ("disk", "whea", "display", "kernel-pnp", "resource-exhaustion")):
        return True
    return False


def _detect_cascades(entries: Sequence[LogEntry]) -> List[CascadePattern]:
    """Detect known multi-system failure chains in a chronological event sequence."""
    cascades: List[CascadePattern] = []

    # Cascade 1: GPU Timeout -> Blue Screen (TDR -> BugCheck 0x116/0x141)
    gpu_events = [
        e for e in entries
        if "display" in e.source.lower() or "4101" in str(e.raw.get("event_id", ""))
        or "nvlddmkm" in e.message.lower() or "amdkmdag" in e.message.lower() or "tdr" in e.message.lower()
    ]
    bsod_events = [
        e for e in entries
        if "bugcheck" in e.source.lower() or "crash" in e.source.lower()
        or e.raw.get("event_id") in (1001, 41)
        or "0x00000116" in e.message or "0x116" in e.message or "0x141" in e.message
    ]
    if gpu_events and bsod_events:
        cascades.append(CascadePattern(
            name="GPU TDR -> Kernel Crash",
            confidence="High",
            description="Graphics driver timeout detection and recovery (TDR) failed, resulting in a system BugCheck 0x116 / 0x141.",
            events=tuple(gpu_events + bsod_events),
        ))

    # Cascade 2: Storage Retry / Paging -> NTFS Corruption -> App Crash
    disk_events = [
        e for e in entries
        if "disk" in e.source.lower() and (
            e.raw.get("event_id") in (7, 11, 51, 153) or "retry" in e.message.lower() or "bad block" in e.message.lower()
        )
    ]
    ntfs_events = [
        e for e in entries
        if "ntfs" in e.source.lower() and (e.raw.get("event_id") in (55, 137) or "corrupt" in e.message.lower())
    ]
    app_crashes = [
        e for e in entries
        if "application error" in e.source.lower() and e.raw.get("event_id") == 1000
    ]
    if disk_events and (ntfs_events or app_crashes):
        cascades.append(CascadePattern(
            name="Storage Degradation -> System Corruption",
            confidence="High",
            description="Disk I/O timeouts, retries, or paging errors directly preceded filesystem corruption or executable crash.",
            events=tuple(disk_events + ntfs_events + app_crashes),
        ))

    # Cascade 3: CBS Component Missing -> Windows Update Failure
    cbs_errors = [
        e for e in entries
        if "cbs" in e.source.lower() and (
            "0x800f081f" in e.message or "0x800f0906" in e.message or "0x80073712" in e.message or "corruption" in e.message.lower()
        )
    ]
    wu_errors = [
        e for e in entries
        if "windowsupdate" in e.source.lower() or "windows update" in e.source.lower() and (
            _is_error(e.level) or e.raw.get("event_id") == 20
        )
    ]
    if cbs_errors and wu_errors:
        cascades.append(CascadePattern(
            name="Component Store Corruption -> Windows Update Failure",
            confidence="High",
            description="Component-Based Servicing (CBS) payload missing or manifest corrupt blocked Windows Update installation.",
            events=tuple(cbs_errors + wu_errors),
        ))

    # Cascade 4: Service Crash -> Dependency Failures (7031/7034 -> 7001)
    service_crashes = [
        e for e in entries
        if "service control manager" in e.source.lower() and e.raw.get("event_id") in (7031, 7034)
    ]
    service_deps = [
        e for e in entries
        if "service control manager" in e.source.lower() and e.raw.get("event_id") == 7001
    ]
    if service_crashes and service_deps:
        cascades.append(CascadePattern(
            name="Service Crash Cascade",
            confidence="High",
            description="A core service terminated unexpectedly, causing dependent services to fail startup (Event 7001).",
            events=tuple(service_crashes + service_deps),
        ))

    return cascades


def _synthesize_root_causes(
    anchor_time: datetime,
    entries: Sequence[LogEntry],
    precursors: Sequence[LogEntry],
    cascades: Sequence[CascadePattern],
) -> List[RootCauseCandidate]:
    """Synthesize and prioritize root causes based on detected patterns and precursors."""
    candidates: List[RootCauseCandidate] = []

    # 1. From detected cascades
    for c in cascades:
        if c.name == "GPU TDR -> Kernel Crash":
            candidates.append(RootCauseCandidate(
                title="GPU Driver Hang / TDR Timeout",
                severity="Critical",
                confidence="High",
                explanation="Display driver stopped responding to the operating system within the 2-second TDR threshold, triggering system recovery failure.",
                suggested_action="Clean install GPU drivers via Driver Manager or DDU; check GPU clock, temperature, and power cables.",
                supporting_events=c.events,
            ))
        elif c.name == "Storage Degradation -> System Corruption":
            candidates.append(RootCauseCandidate(
                title="Disk Storage Subsystem Fault",
                severity="Critical",
                confidence="High",
                explanation="Physical disk bad blocks or controller timeouts generated paging read failures.",
                suggested_action="Inspect SMART attributes in Disk Health; reseat/replace storage cable or NVMe slot; run chkdsk /f.",
                supporting_events=c.events,
            ))
        elif c.name == "Component Store Corruption -> Windows Update Failure":
            candidates.append(RootCauseCandidate(
                title="CBS Component Store Corruption",
                severity="Error",
                confidence="High",
                explanation="Missing assembly payload or damaged catalog manifests prevented Windows Update from completing.",
                suggested_action="Execute DISM /Online /Cleanup-Image /RestoreHealth in System Health, followed by sfc /scannow.",
                supporting_events=c.events,
            ))
        elif c.name == "Service Crash Cascade":
            candidates.append(RootCauseCandidate(
                title="Core Service Process Crash",
                severity="Error",
                confidence="High",
                explanation="A master Windows service terminated abnormally, causing cascading dependency failures.",
                suggested_action="Check Application Event 1000 for the faulting DLL; restart service in System Management.",
                supporting_events=c.events,
            ))

    # 2. Check for Hardware WHEA Errors
    whea_events = [e for e in entries if "whea" in e.source.lower()]
    if whea_events and not any("WHEA" in c.title for c in candidates):
        candidates.append(RootCauseCandidate(
            title="Hardware Machine Check / WHEA Instability",
            severity="Critical",
            confidence="High",
            explanation="CPU, memory cache, or PCIe fabric reported hardware-level execution errors.",
            suggested_action="Reset BIOS overclocks/undervolts; verify memory timings (XMP/EXPO); check thermal throttling.",
            supporting_events=tuple(whea_events),
        ))

    # 3. Check for Out-of-Memory / Resource Exhaustion
    mem_events = [
        e for e in entries
        if "resource-exhaustion" in e.source.lower() or "virtual memory" in e.message.lower() or "0xc0000017" in e.message
    ]
    if mem_events and not any("Memory" in c.title for c in candidates):
        candidates.append(RootCauseCandidate(
            title="Virtual Memory Exhaustion",
            severity="Error",
            confidence="High",
            explanation="The operating system ran out of committed memory and page file backing.",
            suggested_action="Identify high-commit processes; verify page file is system-managed; run RAM diagnostics.",
            supporting_events=tuple(mem_events),
        ))

    # 4. Check for Unclean Reboot (Kernel-Power 41 with BugcheckCode 0)
    kp_events = [
        e for e in entries
        if e.raw.get("event_id") == 41 and "kernel-power" in e.source.lower()
    ]
    for kp in kp_events:
        msg = kp.message.lower()
        if "bugcheckcode 0" in msg or "bugcheckcode: 0" in msg:
            candidates.append(RootCauseCandidate(
                title="Sudden Power Loss / Hard Reset",
                severity="Critical",
                confidence="Medium",
                explanation="Kernel-Power 41 with BugcheckCode 0 indicates power cut, PSU brownout, or manual hard reset without Windows generating a bugcheck.",
                suggested_action="Check power supply, UPS, wall outlet, and CPU/motherboard VRM temperatures.",
                anchor_event=kp,
            ))

    # 5. Fallback: inspect precursors if no cascade matched
    if not candidates and precursors:
        top_precursor = precursors[-1]  # most recent before incident
        candidates.append(RootCauseCandidate(
            title=f"Incident Precursor: {top_precursor.source}",
            severity=top_precursor.level or "Error",
            confidence="Medium",
            explanation=f"Precursor fault recorded {top_precursor.timestamp.strftime('%H:%M:%S')}: {top_precursor.message[:160]}",
            suggested_action="Investigate the precursor source and resolve reported warnings prior to incident timestamp.",
            anchor_event=top_precursor,
            supporting_events=tuple(precursors),
        ))

    return candidates
