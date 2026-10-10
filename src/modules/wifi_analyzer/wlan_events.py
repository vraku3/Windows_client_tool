"""Qt-free Wi-Fi connection history from the WLAN AutoConfig event log.

"Wi-Fi keeps dropping" is answered from
``Microsoft-Windows-WLAN-AutoConfig/Operational``, not from a scan: it is
the first thing an admin opens, it is enabled by default, and it is
readable UNELEVATED (its channel ACL grants read to Interactive Users --
measured 2026-10-09, ``wevtutil qe`` as a standard user, rc 0).

Measured on this machine (the whole log: 963 events, 2026-09-06 .. 10-09):

* 8001 connected 94, 8002 connection FAILED 89, 8003 disconnected 82,
  plus 8000 "connecting" and 8011 / 11000-11010 association and security
  chatter. The pane showed none of it.
* Every event carries the SSID and a human-readable reason Windows itself
  wrote (``Reason`` on 8003, ``FailureReason`` on 8002), plus a numeric
  ``ReasonCode``. Grouped by (event, SSID, reason) the 171 failures and
  disconnects collapse to 14 rows -- 71 of them "The network is
  disconnected due to a policy disabling auto connect on this interface."
  (ReasonCode 5), in the samples checked 0.6 s after an 8001 connect. Not a
  fault: this desktop's Ethernet is up and Windows' connection manager
  drops Wi-Fi when a preferred wired link is present
  (``fMinimizeConnections`` is not configured here, so the default
  applies). Saying so is the difference between a finding and an alarm.
* The biggest group is 8002 "An internal failure prevented the operation
  from completing." (ReasonCode 229392, 80 of 89 failures). Pairing each
  failure with the 8000 that began it (same SSID + ``ConnectionId``) shows
  63 of those 80 ended within a second -- typically 13-40 ms -- with
  ``RSSI`` 255, i.e. no signal was ever measured. The attempt never reached
  the access point; Windows abandoned it locally. A few ran for hours (an
  attempt pending across sleep), so the MEDIAN duration is shown, never
  the maximum.
* ``wevtutil`` refuses with a non-zero exit (``Access is denied.``, rc 5,
  checked against the Security log) and a missing channel is rc 15007/159
  ("The specified channel could not be found."). Both are reported as "could
  not read", never as "no Wi-Fi problems".
* Piped ``wevtutil`` output is not reliably UTF-8 (CLAUDE.md, Diagnose
  notes), and an SSID can be any bytes -- decode UTF-8 first, then the ANSI
  code page, never raise.
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import logging

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
CHANNEL = "Microsoft-Windows-WLAN-AutoConfig/Operational"
DEFAULT_MAX_EVENTS = 2000

#: An attempt that failed this fast never got as far as the radio.
QUICK_ABORT_MS = 1000
#: RSSI value the 8002 event carries when no signal was measured.
RSSI_NOT_MEASURED = "255"

EVENT_CONNECTING = 8000
EVENT_CONNECTED = 8001
EVENT_FAILED = 8002
EVENT_DISCONNECTED = 8003

KIND_BY_ID = {
    EVENT_CONNECTING: "Connecting",
    EVENT_CONNECTED: "Connected",
    EVENT_FAILED: "Connection failed",
    EVENT_DISCONNECTED: "Disconnected",
}

#: Reason text fragment (lower case) -> what it means for the person reading.
#: Only reasons whose cause is known are explained; the rest show Windows'
#: own words alone rather than a guess.
_EXPLANATIONS = (
    ("policy disabling auto connect",
     "By design: Windows drops Wi-Fi when a preferred connection (usually "
     "wired Ethernet) is up -- the 'minimize simultaneous connections' "
     "default. Not a radio or access-point fault."),
    ("disconnected by the user",
     "Someone (or an app/VPN client) chose Disconnect."),
    ("radio is turned off", "The Wi-Fi radio was switched off."),
    ("the radio is off", "The Wi-Fi radio was switched off."),
    ("wrong key", "The saved password/key does not match the network's."),
    ("security key is not correct", "The saved password/key does not match the network's."),
    ("network is not available", "The access point was out of range or turned off."),
)


def explain(reason: str) -> str:
    """A one-line meaning for a reason Windows wrote, or ``""`` if unknown."""
    lowered = (reason or "").lower()
    for fragment, meaning in _EXPLANATIONS:
        if fragment in lowered:
            return meaning
    return ""


@dataclass(frozen=True)
class WlanEvent:
    event_id: int
    when: datetime            # UTC, tz-aware
    ssid: str
    reason: str               # Windows' own words; "" on a connect
    reason_code: str
    profile: str = ""
    phy: str = ""             # 8001 only: 802.11ax etc.
    auth: str = ""            # 8001 only
    connection_id: str = ""   # pairs an 8002/8003 with the 8000 that began it
    rssi: str = ""            # 8002 only; "255" = not measured

    @property
    def kind(self) -> str:
        return KIND_BY_ID.get(self.event_id, str(self.event_id))


@dataclass
class EventGroup:
    kind: str
    ssid: str
    reason: str
    count: int
    first: datetime
    last: datetime
    reason_codes: List[str] = field(default_factory=list)
    #: failures that ended within QUICK_ABORT_MS of starting AND carried no
    #: signal reading -- abandoned before the access point was reached.
    quick_aborts: int = 0
    #: ms from each failure's 8000 to the failure, where the start is logged.
    #: Some run for hours (an attempt left pending across sleep), so the
    #: median is the figure worth showing, never the maximum.
    durations_ms: List[int] = field(default_factory=list)

    @property
    def median_ms(self) -> Optional[int]:
        if not self.durations_ms:
            return None
        ordered = sorted(self.durations_ms)
        return ordered[len(ordered) // 2]

    @property
    def meaning(self) -> str:
        known = explain(self.reason)
        if known:
            return known
        if self.quick_aborts * 2 >= self.count and self.quick_aborts:
            return (f"{self.quick_aborts} of {self.count} gave up within "
                    f"{QUICK_ABORT_MS // 1000} s (median {self.median_ms} ms) with no "
                    f"signal reading (RSSI {RSSI_NOT_MEASURED}): Windows abandoned "
                    "them locally before reaching the access point. Look at what "
                    "else holds the connection (Ethernet, VPN), not at the Wi-Fi.")
        if self.quick_aborts:
            return (f"{self.quick_aborts} of {self.count} gave up within "
                    f"{QUICK_ABORT_MS // 1000} s with no signal reading.")
        return ""


@dataclass
class HistoryResult:
    events: List[WlanEvent]
    error: Optional[str] = None     # set when the log could not be READ

    @property
    def readable(self) -> bool:
        return self.error is None


# ── reading ─────────────────────────────────────────────────────────────────

def decode_output(data: bytes) -> str:
    """UTF-8 if it is, else the ANSI code page; never raises."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("mbcs", errors="replace")
        except LookupError:            # not on Windows
            return data.decode("latin-1", errors="replace")


def read_history(max_events: int = DEFAULT_MAX_EVENTS, timeout: int = 30) -> HistoryResult:
    """The newest ``max_events`` connection events. Blocking; use a Worker."""
    query = "*[System[(EventID>=8000 and EventID<=8003)]]"
    try:
        proc = subprocess.run(
            ["wevtutil", "qe", CHANNEL, f"/q:{query}", f"/c:{int(max_events)}",
             "/rd:true", "/f:xml"],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Reading %s failed: %s", CHANNEL, exc)
        return HistoryResult([], f"Could not run wevtutil: {exc}")
    out = decode_output(proc.stdout or b"")
    err = decode_output(proc.stderr or b"")
    refusal = classify_wevtutil(proc.returncode, out, err)
    if refusal:
        logger.warning("Reading %s refused: %s", CHANNEL, refusal)
        return HistoryResult([], refusal)
    return HistoryResult(parse_events_xml(out))


def classify_wevtutil(rc: int, out: str, err: str) -> Optional[str]:
    """``None`` when the read worked, else a sentence saying why not."""
    text = f"{err}\n{out}".strip()
    lowered = text.lower()
    if rc == 0 and "failed to open event query" not in lowered:
        return None
    if rc == 5 or "access is denied" in lowered:
        return "Windows refused to let this account read the WLAN AutoConfig log (access denied)."
    if "could not be found" in lowered:
        return "The WLAN AutoConfig log does not exist on this machine (no Wi-Fi stack installed?)."
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return f"wevtutil failed (exit {rc}): {first or 'no message'}"


_EVENT_RE = re.compile(r"<Event[ >].*?</Event>", re.S)
_ID_RE = re.compile(r"<EventID[^>]*>(\d+)</EventID>")
_TIME_RE = re.compile(r"<TimeCreated SystemTime=['\"]([^'\"]+)['\"]")
_DATA_RE = re.compile(r"<Data Name=['\"](\w+)['\"]>(.*?)</Data>", re.S)
_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&"))


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return text.strip()


def parse_time(stamp: str) -> Optional[datetime]:
    """``2026-10-09T05:48:22.5793460Z`` -> aware UTC datetime (7-digit
    fractions are trimmed to the 6 Python accepts)."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?", stamp or "")
    if not m:
        return None
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    try:
        return datetime.strptime(f"{m.group(1)}.{frac}", "%Y-%m-%dT%H:%M:%S.%f").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_events_xml(text: str) -> List[WlanEvent]:
    """Every 8001/8002/8003 ``<Event>`` in ``text``, newest first as given.
    An event without a parseable id or time is skipped (and logged)."""
    events: List[WlanEvent] = []
    for block in _EVENT_RE.findall(text or ""):
        id_m, time_m = _ID_RE.search(block), _TIME_RE.search(block)
        when = parse_time(time_m.group(1)) if time_m else None
        if not id_m or when is None:
            logger.warning("Skipped an unparseable WLAN event: %.120s", block)
            continue
        event_id = int(id_m.group(1))
        if event_id not in KIND_BY_ID:
            continue
        data: Dict[str, str] = {k: _unescape(v) for k, v in _DATA_RE.findall(block)}
        events.append(WlanEvent(
            event_id=event_id, when=when,
            ssid=data.get("SSID", "") or data.get("ProfileName", ""),
            reason=data.get("Reason") or data.get("FailureReason") or "",
            reason_code=data.get("ReasonCode", ""),
            profile=data.get("ProfileName", ""),
            phy=data.get("PHYType", ""), auth=data.get("AuthenticationAlgorithm", ""),
            connection_id=data.get("ConnectionId", ""), rssi=data.get("RSSI", ""),
        ))
    return events


# ── summarising ─────────────────────────────────────────────────────────────

def attempt_duration_ms(failure: WlanEvent, events: List[WlanEvent]) -> Optional[int]:
    """Milliseconds from the 8000 that began ``failure``'s attempt (same SSID
    and ConnectionId, at or before it) to the failure, or ``None`` when the
    start fell outside the log."""
    starts = [ev.when for ev in events
              if ev.event_id == EVENT_CONNECTING and ev.ssid == failure.ssid
              and ev.connection_id == failure.connection_id and ev.when <= failure.when]
    if not starts:
        return None
    return int((failure.when - max(starts)).total_seconds() * 1000)


def group_problems(events: List[WlanEvent]) -> List[EventGroup]:
    """Failures and disconnects grouped by (kind, SSID, reason), most
    frequent first -- "how many, how bad, most recent", not a wall of rows."""
    groups: Dict[tuple, EventGroup] = {}
    for ev in events:
        if ev.event_id not in (EVENT_FAILED, EVENT_DISCONNECTED):
            continue
        group = _count_into(groups, ev)
        if ev.event_id == EVENT_FAILED:
            _note_attempt(group, ev, events)
    return sorted(groups.values(), key=lambda g: (-g.count, -g.last.timestamp()))


def _count_into(groups: Dict[tuple, EventGroup], ev: WlanEvent) -> EventGroup:
    key = (ev.kind, ev.ssid, ev.reason)
    g = groups.get(key)
    if g is None:
        g = groups[key] = EventGroup(ev.kind, ev.ssid, ev.reason, 1, ev.when, ev.when,
                                     [ev.reason_code] if ev.reason_code else [])
        return g
    g.count += 1
    g.first = min(g.first, ev.when)
    g.last = max(g.last, ev.when)
    if ev.reason_code and ev.reason_code not in g.reason_codes:
        g.reason_codes.append(ev.reason_code)
    return g


def _note_attempt(group: EventGroup, failure: WlanEvent, events: List[WlanEvent]) -> None:
    ms = attempt_duration_ms(failure, events)
    if ms is None:
        return
    group.durations_ms.append(ms)
    if ms < QUICK_ABORT_MS and failure.rssi == RSSI_NOT_MEASURED:
        group.quick_aborts += 1


@dataclass
class SsidSummary:
    ssid: str
    connected: int = 0
    failed: int = 0
    disconnected: int = 0
    unexplained_disconnects: int = 0   # disconnects with no known benign cause
    last_connected: Optional[datetime] = None


def summarise_by_ssid(events: List[WlanEvent]) -> List[SsidSummary]:
    """Per network: connects, failures, disconnects -- busiest first."""
    by: Dict[str, SsidSummary] = {}
    for ev in events:
        if ev.event_id not in (EVENT_CONNECTED, EVENT_FAILED, EVENT_DISCONNECTED):
            continue
        s = by.setdefault(ev.ssid, SsidSummary(ev.ssid))
        if ev.event_id == EVENT_CONNECTED:
            s.connected += 1
            if s.last_connected is None or ev.when > s.last_connected:
                s.last_connected = ev.when
        elif ev.event_id == EVENT_FAILED:
            s.failed += 1
        else:
            s.disconnected += 1
            if not explain(ev.reason).startswith("By design"):
                s.unexplained_disconnects += 1
    return sorted(by.values(),
                  key=lambda s: (-(s.failed + s.disconnected + s.connected), s.ssid))


def failure_rate(summary: SsidSummary) -> Optional[float]:
    """Failed attempts / all attempts that reached an outcome, or None."""
    attempts = summary.connected + summary.failed
    return summary.failed / attempts if attempts else None


def headline(events: List[WlanEvent]) -> str:
    """The one sentence above the table."""
    events = [ev for ev in events if ev.event_id != EVENT_CONNECTING]
    if not events:
        return "No Wi-Fi connections, failures or disconnects are recorded in the log."
    counts = Counter(ev.event_id for ev in events)
    oldest = min(ev.when for ev in events).astimezone()
    benign = sum(1 for ev in events if ev.event_id == EVENT_DISCONNECTED
                 and explain(ev.reason).startswith("By design"))
    parts = [f"{counts[EVENT_CONNECTED]} connected",
             f"{counts[EVENT_FAILED]} failed",
             f"{counts[EVENT_DISCONNECTED]} disconnected"]
    text = (f"Since {oldest:%Y-%m-%d %H:%M}: " + ", ".join(parts))
    if benign:
        text += f" ({benign} of the disconnects by design: a preferred connection was up)"
    return text + "."
