"""Unexpected shutdowns and crashes from the System log, grouped into incidents.

Three events tell this story, and counting them raw tells it wrong:

- Kernel-Power 41 -- "rebooted without cleanly shutting down first";
- EventLog 6008   -- "the previous system shutdown ... was unexpected";
- WER-SystemErrorReporting 1001 -- "rebooted from a bugcheck" (a blue screen).

All three are logged at the NEXT boot, seconds apart, so one bad shutdown is
two or three events. Measured 2026-10-04 here: a 41 at 10:49:24Z and its 6008
at 10:49:42Z -- one incident, which a raw count reports as two.

Event 41 is also the most misread event in the log ("your PSU is failing").
Its own data says what happened, and that is what `classify` reads:

- BugcheckCode != 0      -> it WAS a blue screen; the dump just was not written;
- PowerButtonTimestamp/LongPowerButtonPressDetected -> someone held the button;
- SleepInProgress != 0   -> it died entering or resuming from sleep;
- all of the above zero  -> power was cut or the machine hard-reset with nothing
  recorded first: a hang plus reset, a power cut, or power delivery.

BugcheckCode is DECIMAL in event 41 (159 is 0x9F); 1001 carries the code as hex
text. No 1001 exists on this machine to verify against, so its parsing is
covered by synthetic tests only.
"""
from __future__ import annotations

import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"

#: Events closer than this belong to the same incident. All three are written
#: during the boot that follows the failure: seconds apart for 41/6008, and a
#: 1001 waits for WER, which can take a minute or two.
INCIDENT_WINDOW = timedelta(minutes=10)

POWER_LOSS = "power_loss"
BUGCHECK = "bugcheck"
POWER_BUTTON = "power_button"
SLEEP = "sleep"
UNKNOWN = "unknown"


@dataclass
class RawEvent:
    event_id: int
    provider: str
    when: datetime                       # UTC, timezone-aware
    named: Dict[str, str] = field(default_factory=dict)
    positional: List[str] = field(default_factory=list)


@dataclass
class Incident:
    when: datetime                       # local time of the earliest event
    cause: str
    summary: str
    bugcheck: str = ""                   # "0x0000009F DRIVER_POWER_STATE_FAILURE"
    event_ids: List[int] = field(default_factory=list)


def parse_events(xml_text: str) -> List[RawEvent]:
    """`wevtutil qe /f:xml` output: bare <Event> elements with no root."""
    try:
        root = ET.fromstring(f"<Events>{xml_text}</Events>")
    except ET.ParseError as e:
        logger.warning("could not parse System log XML: %s", e)
        return []
    out: List[RawEvent] = []
    for ev in root.findall(f"{_NS}Event"):
        system = ev.find(f"{_NS}System")
        if system is None:
            continue
        try:
            event_id = int(system.findtext(f"{_NS}EventID", "0"))
            when = datetime.fromisoformat(system.find(f"{_NS}TimeCreated").get("SystemTime", ""))
        except (AttributeError, ValueError) as e:
            logger.warning("skipping a malformed System log event: %s", e)
            continue
        provider = system.find(f"{_NS}Provider")
        raw = RawEvent(event_id, provider.get("Name", "") if provider is not None else "", when)
        for data in ev.iter(f"{_NS}Data"):
            text = data.text or ""
            if data.get("Name"):
                raw.named[data.get("Name")] = text
            else:
                raw.positional.append(text)
        out.append(raw)
    return out


def _nonzero(value: Optional[str]) -> bool:
    if not value:
        return False
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value, 0) != 0
    except ValueError:
        return False


def _bugcheck_from_1001(event: RawEvent) -> str:
    from core.diag_knowledge import bugcheck_label
    text = event.named.get("param1") or (event.positional[0] if event.positional else "")
    match = re.search(r"0x[0-9a-fA-F]+", text)
    return bugcheck_label(int(match.group(0), 16)) if match else ""


def classify(group: Sequence[RawEvent]) -> Tuple[str, str, str]:
    """(cause, summary, bugcheck label) for one incident's events."""
    from core.diag_knowledge import bugcheck_label
    by_id = {e.event_id: e for e in group}
    if 1001 in by_id:
        label = _bugcheck_from_1001(by_id[1001])
        return BUGCHECK, f"Blue screen{': ' + label if label else ''}", label
    kp = by_id.get(41)
    if kp is not None:
        code = kp.named.get("BugcheckCode", "0")
        if _nonzero(code):
            label = bugcheck_label(int(code, 0))
            return (BUGCHECK, f"Blue screen ({label}); Windows recorded the stop code "
                              "but wrote no crash dump", label)
        if _nonzero(kp.named.get("PowerButtonTimestamp")) or \
                _nonzero(kp.named.get("LongPowerButtonPressDetected")):
            return POWER_BUTTON, "Forced off: the power button was held down", ""
        if _nonzero(kp.named.get("SleepInProgress")):
            return SLEEP, "Died while entering or resuming from sleep", ""
        return (POWER_LOSS, "Lost power or hard-reset with no crash recorded "
                            "(a hang then reset, a power cut, or power delivery)", "")
    return UNKNOWN, "Previous shutdown was unexpected; Windows recorded no detail", ""


def group_incidents(events: Sequence[RawEvent],
                    window: timedelta = INCIDENT_WINDOW) -> List[Incident]:
    """Newest first. Events within `window` of the previous one are one incident."""
    groups: List[List[RawEvent]] = []
    for ev in sorted(events, key=lambda e: e.when):
        if groups and ev.when - groups[-1][-1].when <= window:
            groups[-1].append(ev)
        else:
            groups.append([ev])
    incidents = []
    for group in groups:
        cause, summary, label = classify(group)
        incidents.append(Incident(group[0].when.astimezone(), cause, summary, label,
                                  [e.event_id for e in group]))
    return list(reversed(incidents))


def read_incidents(days: int = 30, runner: Callable = subprocess.run
                   ) -> Tuple[Optional[List[Incident]], str]:
    """(incidents, reason). None with a reason when the log could not be read --
    never [] for a refusal, which would read as "no crashes"."""
    ms = days * 86400 * 1000
    query = ("*[System[(EventID=41 or EventID=6008 or EventID=1001) and "
             f"TimeCreated[timediff(@SystemTime) <= {ms}]]]")
    try:
        done = runner(["wevtutil", "qe", "System", f"/q:{query}", "/c:500", "/f:xml"],
                      capture_output=True, text=True, errors="replace", timeout=30,
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("wevtutil failed: %s", e)
        return None, f"wevtutil could not run: {e}"
    if done.returncode != 0:
        reason = (done.stderr or done.stdout or "").strip()[:200]
        logger.warning("wevtutil refused the System log: rc=%s %s", done.returncode, reason)
        return None, f"the System log could not be read: {reason or 'rc ' + str(done.returncode)}"
    relevant = [e for e in parse_events(done.stdout)
                if (e.event_id == 41 and "kernel-power" in e.provider.lower())
                or (e.event_id == 6008 and e.provider.lower() == "eventlog")
                or (e.event_id == 1001 and "systemerrorreporting" in e.provider.lower())]
    return group_incidents(relevant), ""
