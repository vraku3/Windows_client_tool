"""Diagnostic bridge: maps known Windows errors and log sources to actionable fixes (Qt-free).

Enables one-click navigation from error records in Diagnose / Event Viewer
directly to the relevant repair action in Quick Fix, Driver Manager, or System Health.
"""
from __future__ import annotations

import re
from typing import NamedTuple, Optional, Sequence, Tuple


class SuggestedFix(NamedTuple):
    title: str
    target_module: str
    reason: str


_RULES: Sequence[Tuple[re.Pattern, SuggestedFix]] = [
    (
        re.compile(r"\b(AudioEndpointBuilder|AudioSrv|audiodg)\b", re.IGNORECASE),
        SuggestedFix("Restart Audio Engine", "Quick Fix", "Restarts Windows audio services without requiring a reboot."),
    ),
    (
        re.compile(r"\b(Print|Spooler|PrintQueue|localspl)\b", re.IGNORECASE),
        SuggestedFix("Clear Print Queue", "Quick Fix", "Purges stuck jobs in the print spooler and restarts the service."),
    ),
    (
        re.compile(r"\b(WindowsUpdateClient|BITS|wuauserv|0x80070422|0x8024[0-9a-f]{4})\b", re.IGNORECASE),
        SuggestedFix("Reset Cryptographic & BITS", "Quick Fix", "Resets update pipeline, BITS and cryptographic services."),
    ),
    (
        re.compile(r"\b(VSS|VolSnap|VssAdmin|0x800423[0-9a-f]{2})\b", re.IGNORECASE),
        SuggestedFix("Reset VSS Writers", "Quick Fix", "Restarts Volume Shadow Copy writers for reliable backup operations."),
    ),
    (
        re.compile(r"\b(nvlddmkm|amdkmdag|atikmdag|igdkmd64|igdgmr64|LiveKernelEvent|TDR)\b", re.IGNORECASE),
        SuggestedFix("Open Driver Manager", "Driver Manager", "Inspect and update graphics drivers to resolve GPU timeout crashes."),
    ),
    (
        re.compile(r"\b(rtwlane|rtwlanu|netwtw|e1d68|e2fnexpress|rt640x64|rtx64x64|mtkwl6ex|qca106x64)\b", re.IGNORECASE),
        SuggestedFix("Open Driver Manager", "Driver Manager", "Inspect and update network / Wi-Fi drivers to resolve connection or DPC latency crashes."),
    ),
    (
        re.compile(r"\b(iastor|amdsata|secnvme|ene\.sys|asusgio3|corsairllaservice|steelseries|rzendpt)\b", re.IGNORECASE),
        SuggestedFix("Open Driver Manager", "Driver Manager", "Inspect or rollback storage controller / peripheral utility drivers."),
    ),
    (
        re.compile(r"\b(Servicing|CBS|WinSxS|0x800f081f|0x80070002)\b", re.IGNORECASE),
        SuggestedFix("Run DISM Component Cleanup", "System Health", "Check and repair component store integrity."),
    ),
    (
        re.compile(r"\b(Disk|Ntfs|disk_wear|Event 51|Event 153)\b", re.IGNORECASE),
        SuggestedFix("Check Disk Health", "Disk Health", "Inspect SMART attributes, error telemetry and wear levels."),
    ),
    (
        re.compile(r"\b(DNS|NameResolution|Wlansvc|Netman|Winsock)\b", re.IGNORECASE),
        SuggestedFix("Flush & Register DNS", "Quick Fix", "Flushes DNS resolver cache and resets network stack."),
    ),
]


def match_suggested_fix(source: str, message: str) -> Optional[SuggestedFix]:
    """Return a SuggestedFix if the source or message matches a known repairable pattern."""
    combined = f"{source} {message}"
    for pattern, fix in _RULES:
        if pattern.search(combined):
            return fix
    return None
