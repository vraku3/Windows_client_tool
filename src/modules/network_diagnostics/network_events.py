"""Network-adapter hardware and driver System log events -- the same
"read the System log for a known problem signature" pattern as
``modules.disk_health.disk_events``, applied to NICs and Wi-Fi. Qt-free.

Confirmed live on this real machine (2026-09-27, ``Get-WinEvent -FilterHashtable
@{LogName='System'} | Group-Object ProviderName``): 13 ``Tcpip`` Error-level
events in the last 14 days reading "The IPv6 TCP/IP interface with index N
failed to bind to its provider", and 27 ``Microsoft-Windows-WLAN-AutoConfig``
Warning-level "WLAN Extensibility Module has stopped" events pointing at this
machine's real MediaTek driver
(``...\\DriverStore\\FileRepository\\mtkwecx.inf_amd64_...\\mtkihvx.dll``) --
neither visible anywhere else in this app before this reader existed, on a
machine whose adapters otherwise read as healthy. A handful of
"WLAN AutoConfig detected limited connectivity, attempting automatic
recovery" (event 4003) events were captured live too.

``Microsoft-Windows-Dhcp-Client``'s own DHCP-failure events (1002/1003/...)
were checked and ruled out: they are logged to that provider's own
``.../Dhcp-Client/Admin`` channel (``EventLog`` registry key, channel 17),
never to ``System`` -- confirmed on this machine (zero non-Information
Dhcp-Client events in ``System`` across 30 days) and from the provider's own
manifest (``wevtutil gp Microsoft-Windows-Dhcp-Client /ge /gm:true``, event
1002/1003 both declare ``channel: 17``, not the System channel's 8). No
NDIS-named provider (``Microsoft-Windows-NDIS`` etc.) had logged anything to
System at all here, and no vendor-specific NIC driver provider (Realtek,
Intel, MediaTek's own non-WLAN name) was present either -- so this reader
covers exactly the two providers with real, observed evidence on this
machine, plus the well-known TCP/IP ephemeral-port-exhaustion events
(4227/4231) that are not present here today but are genuine, widely
documented Windows events worth watching for.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Providers that log network-adapter hardware/driver problems to the
#: System log specifically (as opposed to a provider's own private channel).
_PROVIDERS = ("Tcpip", "Microsoft-Windows-WLAN-AutoConfig")

#: (provider, event id) -> what it actually means, and whether it reflects a
#: problem happening right now (error) versus a recovery/degradation signal
#: worth reading (warning). Every entry here is either captured live on this
#: real machine (4207, 10002, 4003 -- see module docstring) or a real,
#: independently well-documented Windows event (4227/4231, the classic
#: ephemeral-port-exhaustion pair: "TCP/IP failed to establish an outgoing
#: connection because the selected local endpoint was recently used..." and
#: "...ephemeral port number from the global TCP/UDP port space has failed
#: due to all such ports being in use", both usually meaning a port-exhaustion
#: condition or, per Microsoft's own guidance, a possible SYN-flood attack).
EVENT_MEANINGS: Dict[Tuple[str, int], str] = {
    ("Tcpip", 4207): "A TCP/IP interface failed to bind to its provider -- "
                      "usually a Teredo/6to4/VPN virtual adapter racing the "
                      "IPv6 stack at startup or link change.",
    ("Tcpip", 4227): "TCP/IP refused an outgoing connection reusing a "
                      "recently-used local endpoint -- a sign of port "
                      "exhaustion, or an unusually high connection rate.",
    ("Tcpip", 4231): "TCP/IP could not allocate an ephemeral port at all: "
                      "every port in the dynamic range was in use. Usually "
                      "port exhaustion from a runaway app opening many short "
                      "connections; Microsoft's own guidance also names a "
                      "SYN-flood attack as a cause.",
    ("Microsoft-Windows-WLAN-AutoConfig", 10002): "The Wi-Fi driver's own "
        "extensibility module crashed or was unloaded and WLAN AutoConfig "
        "had to restart it -- real driver instability, not a Windows "
        "service issue.",
    ("Microsoft-Windows-WLAN-AutoConfig", 4003): "WLAN AutoConfig detected "
        "limited connectivity on the wireless adapter and attempted an "
        "automatic recovery (reassociate/renew/restart, depending on the "
        "trigger).",
}

#: Severity classification -- 4207 and 4231 are logged by Windows itself as
#: Error/hard-failure; 4227, 10002 and 4003 are Warning-level recoveries or
#: near-misses.
_ERROR_IDS = {("Tcpip", 4207), ("Tcpip", 4231)}

_INDEX_RE = re.compile(r"index\s+(\d+)")


@dataclass
class NetworkEvent:
    provider: str
    event_id: int
    level: str
    time: str
    index: Optional[str]
    message: str
    meaning: str

    @property
    def is_error(self) -> bool:
        return (self.provider, self.event_id) in _ERROR_IDS


def _extract_index(message: str) -> Optional[str]:
    match = _INDEX_RE.search(message)
    return match.group(1) if match else None


def read_network_events(days: int = 14, max_events: int = 200, timeout: int = 30) -> Optional[List[NetworkEvent]]:
    """None means the read failed or was refused; an empty list means no
    matching events in the window -- a genuinely healthy sign, not the same
    as "could not tell"."""
    ids = ",".join(str(i) for _p, i in EVENT_MEANINGS)
    providers = ",".join(f"'{p}'" for p in _PROVIDERS)
    script = (
        "$ErrorActionPreference='Stop';"
        f"$f=@{{LogName='System';ProviderName=@({providers});Id=@({ids});"
        f"StartTime=(Get-Date).AddDays(-{int(days)})}};"
        f"try{{$e=Get-WinEvent -FilterHashtable $f -MaxEvents {int(max_events)}}}"
        "catch{if($_.FullyQualifiedErrorId -like '*NoMatchingEventsFound*'){'[]';exit 0}else{throw}};"
        "@($e|%{[pscustomobject]@{Id=$_.Id;Provider=$_.ProviderName;Level=$_.LevelDisplayName;"
        "Time=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss');Message=$_.Message}})"
        "|ConvertTo-Json -Compress")
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("network event read failed: %s", exc)
        return None
    if done.returncode != 0:
        logger.warning("network event read refused: %s", (done.stderr or "").strip()[:200])
        return None
    try:
        data = json.loads(done.stdout.strip() or "[]")
    except ValueError:
        logger.warning("network event history was not JSON")
        return None
    if isinstance(data, dict):
        data = [data]
    events: List[NetworkEvent] = []
    for rec in data:
        event_id = rec.get("Id")
        provider = rec.get("Provider") or ""
        message = (rec.get("Message") or "").strip()
        events.append(NetworkEvent(
            provider=provider, event_id=event_id, level=rec.get("Level") or "",
            time=rec.get("Time") or "", index=_extract_index(message), message=message,
            meaning=EVENT_MEANINGS.get((provider, event_id), "")))
    return events


@dataclass
class EventGroup:
    provider: str
    event_id: int
    index: Optional[str]
    count: int
    latest: str
    meaning: str
    is_error: bool


def group_events(events: List[NetworkEvent]) -> List[EventGroup]:
    """One row per (provider, event id, interface index) rather than one per
    raw event -- confirmed live, "WLAN Extensibility Module has stopped"
    fired 27 times in a 14-day window on this real machine, and a finding
    per occurrence would have buried everything else."""
    groups: Dict[tuple, EventGroup] = {}
    for e in events:
        key = (e.provider, e.event_id, e.index)
        existing = groups.get(key)
        if existing is None:
            groups[key] = EventGroup(e.provider, e.event_id, e.index, 1, e.time, e.meaning, e.is_error)
        else:
            existing.count += 1
            if e.time > existing.latest:
                existing.latest = e.time
    return sorted(groups.values(), key=lambda g: (not g.is_error, -g.count))
