"""Safe network repair actions with read-back. Qt-free.

Every fix returns a ``FixResult``.  ``verified`` is True only when the state was
READ BACK and shows the change; None means it could not be confirmed (say so);
False means Windows accepted the command and the state did not move.  A zero exit
code is never treated as proof: ``ipconfig`` and ``netsh`` both print a refusal
and still exit 0 on some builds.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from modules.network_diagnostics import network_health

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
_REFUSED = re.compile(r"requires elevation|access is denied|administrator|run as", re.I)


@dataclass
class FixResult:
    ok: bool
    message: str
    verified: Optional[bool] = None
    reboot_needed: bool = False


@dataclass(frozen=True)
class FixSpec:
    key: str
    label: str
    confirm_title: str
    confirm_text: str
    needs_admin: bool
    reboot: bool = False


FIXES: Dict[str, FixSpec] = {
    "flush_dns": FixSpec("flush_dns", "Flush DNS cache", "Flush DNS cache?",
                         "Clears this PC's cached name lookups. Harmless; the next lookups are just slower once.", False),
    "renew_dhcp": FixSpec("renew_dhcp", "Renew DHCP lease", "Renew DHCP leases?",
                          "Releases and re-requests the address on every DHCP adapter. Connections drop for a few seconds and the "
                          "IP address may change. Remote sessions over this adapter can be cut off.", True),
    "reset_winsock": FixSpec("reset_winsock", "Reset Winsock", "Reset Winsock catalog?",
                             "Rebuilds the Winsock catalog to defaults. This removes third-party network filters (VPN/AV layered service "
                             "providers) and REQUIRES A REBOOT to take effect. Use only after simpler fixes failed.", True, True),
}


def _run(cmd: List[str], timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          creationflags=CREATE_NO_WINDOW, timeout=timeout)


def refused(text: str) -> bool:
    return bool(_REFUSED.search(text or ""))


def dns_cache_count() -> Optional[int]:
    """Number of cached record names, or None if the cache could not be read."""
    try:
        r = _run(["ipconfig", "/displaydns"], 30)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("displaydns failed: %s", e)
        return None
    if r.returncode != 0:
        return None
    return len(re.findall(r"Record Name", r.stdout))


def flush_dns(run: Callable = _run, count: Callable = dns_cache_count) -> FixResult:
    before = count()
    try:
        r = run(["ipconfig", "/flushdns"])
    except (OSError, subprocess.SubprocessError) as e:
        return FixResult(False, f"Could not run ipconfig: {e}")
    out = (r.stdout or "") + (r.stderr or "")
    if refused(out):
        return FixResult(False, "Refused: " + out.strip().splitlines()[0])
    after = count()
    if before is None or after is None:
        return FixResult(r.returncode == 0, "Flush command ran; the cache could not be read back to confirm.", None)
    if after < before or after == 0:
        return FixResult(True, f"Cache emptied ({before} -> {after} records).", True)
    return FixResult(False, f"Windows accepted the command but the cache still holds {after} records (was {before}).", False)


def _lease_signature(snap: dict) -> Dict[str, str]:
    return {str(r.get("InterfaceIndex")): str(r.get("LeaseObtained")) for r in snap.get("dhcp", [])}


def renew_dhcp(run: Callable = _run, snapshot: Callable = network_health.collect_snapshot) -> FixResult:
    before = snapshot()
    if before.get("errors"):
        return FixResult(False, "Cannot verify a renew: " + before["errors"][0])
    try:
        r = run(["ipconfig", "/renew"], 60)
    except (OSError, subprocess.SubprocessError) as e:
        return FixResult(False, f"Could not run ipconfig: {e}")
    out = (r.stdout or "") + (r.stderr or "")
    if refused(out):
        return FixResult(False, "Refused (needs an elevated app): " + out.strip().splitlines()[0])
    after = snapshot()
    changed = [i for i, v in _lease_signature(after).items() if _lease_signature(before).get(i) != v]
    still_apipa = network_health._f_apipa(after)
    if still_apipa:
        return FixResult(False, "Renew ran but a connected adapter still has a 169.254.x.x address: " + "; ".join(still_apipa[0].items), False)
    if changed:
        return FixResult(True, f"{len(changed)} adapter(s) obtained a fresh lease.", True)
    return FixResult(False, "Command finished but no adapter's lease time changed (no DHCP adapter, or the server did not answer).", False)


def reset_winsock(run: Callable = _run) -> FixResult:
    try:
        r = run(["netsh", "winsock", "reset"])
    except (OSError, subprocess.SubprocessError) as e:
        return FixResult(False, f"Could not run netsh: {e}")
    out = (r.stdout or "") + (r.stderr or "")
    if refused(out) or r.returncode != 0:
        return FixResult(False, "Refused (needs an elevated app): " + (out.strip().splitlines() or ["no output"])[0])
    if "restart" in out.lower():
        return FixResult(True, "Winsock catalog reset. Restart the computer to finish; this cannot be verified until then.", None, True)
    return FixResult(True, "Command completed; a restart is still required and the result cannot be read back.", None, True)


RUNNERS: Dict[str, Callable[[], FixResult]] = {
    "flush_dns": flush_dns, "renew_dhcp": renew_dhcp, "reset_winsock": reset_winsock,
}
