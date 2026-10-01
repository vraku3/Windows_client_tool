"""DISM /Online /Cleanup-Image /CheckHealth -- a read-only, fast health
read of the component-store corruption flag. No Qt.

This is NOT /ScanHealth (a full scan of every component against its
manifest, several minutes) and NOT /RestoreHealth (the repair action) --
both of those live in System Health's Servicing tab and this module makes
no attempt to duplicate them. CheckHealth only reads a flag a PREVIOUS
scan already set; it does no scanning of its own, which is what makes it
fast enough to offer from a log-reading tab with no warning about runtime.

Measured on a real machine (2026-10-01, this repo's own dev box):
  - Unelevated: refuses in ~30ms, exit code 740 (ERROR_ELEVATION_REQUIRED),
    "Elevated permissions are required to run DISM." on stdout. DISM
    refuses CheckHealth exactly like every other elevated-image operation
    this codebase has already documented refusing while otherwise looking
    like it ran -- except here it is an honest non-zero exit, not a silent
    0.
  - Elevated: ~120ms, exit code 0, and on THIS machine the real verdict is
    "The component store is repairable." -- i.e. a previous scan found
    damage and recorded it, with no visible corruption marker anywhere in
    the live CBS.log tail or this app's own `health_checks.check_cbs_log`
    regex (`cannot repair`, `store corruption`, `do not match`,
    `STATUS_SXS_\\w+`) -- "is repairable" matches none of them. That gap is
    exactly why this exists: System Health's own CBS-log text scan found
    nothing on this machine despite DISM's own flag saying the store is
    damaged.

The "healthy" ("No component store corruption detected.") and
"unrepairable" ("The component store cannot be repaired.") wordings below
are DISM's own documented, longstanding output strings; this machine's
store was in the "repairable" state during development, so only that
branch and the unelevated refusal were exercised against a real process.
A response matching none of the three known strings is reported as
"unknown" rather than guessed into either bucket.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

CREATE_NO_WINDOW = 0x08000000

ERROR_ELEVATION_REQUIRED = 740

_HEALTHY = re.compile(r"No component store corruption detected", re.IGNORECASE)
_REPAIRABLE = re.compile(r"component store is repairable", re.IGNORECASE)
_UNREPAIRABLE = re.compile(r"component store cannot be repaired", re.IGNORECASE)
_NEEDS_ELEVATION = re.compile(r"Elevated permissions are required", re.IGNORECASE)


@dataclass
class ComponentStoreHealth:
    #: "healthy" | "repairable" | "unrepairable" | "refused" | "unknown" | "timeout"
    verdict: str
    detail: str
    raw: str
    returncode: int


def classify(returncode: int, raw: str) -> ComponentStoreHealth:
    """Pure classification of a CheckHealth run, split out from the
    subprocess call so it can be tested without ever invoking DISM."""
    if returncode == ERROR_ELEVATION_REQUIRED or _NEEDS_ELEVATION.search(raw or ""):
        return ComponentStoreHealth(
            "refused",
            "DISM refused -- this needs an elevated process to check.",
            raw, returncode)
    if _UNREPAIRABLE.search(raw or ""):
        return ComponentStoreHealth(
            "unrepairable",
            "The component store cannot be repaired by DISM itself. "
            "Try sfc /scannow, or an in-place upgrade/repair install.",
            raw, returncode)
    if _REPAIRABLE.search(raw or ""):
        return ComponentStoreHealth(
            "repairable",
            "A previous scan found component-store damage that DISM "
            "believes it can fix. Run RestoreHealth (System Health's "
            "Servicing tab) to repair it.",
            raw, returncode)
    if _HEALTHY.search(raw or ""):
        return ComponentStoreHealth(
            "healthy", "No component-store corruption is on record.",
            raw, returncode)
    return ComponentStoreHealth(
        "unknown",
        "DISM answered, but not with any of the three wordings this "
        "reads for. See the raw output below.",
        raw, returncode)


def run_check_health(timeout: int = 60) -> ComponentStoreHealth:
    """Runs `dism /Online /Cleanup-Image /CheckHealth` and classifies the
    result. Never raises for a refusal or an unrecognised answer -- those
    are verdicts of their own, not silently folded into "healthy"."""
    cmd = ["dism", "/Online", "/Cleanup-Image", "/CheckHealth"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return ComponentStoreHealth(
            "timeout", f"DISM did not answer within {timeout}s.", "", -1)
    raw = (proc.stdout or "") + (proc.stderr or "")
    return classify(proc.returncode, raw)
