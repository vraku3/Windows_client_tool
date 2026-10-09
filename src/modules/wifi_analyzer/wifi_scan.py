"""Qt-free reading of ``netsh wlan``: networks, interfaces, and why a scan
came back empty.

Measured on this machine (MediaTek RZ717 Wi-Fi 7, Windows 11 26300,
2026-10-09), and the reason each piece below exists:

* **netsh exits 0 when it refuses.** ``netsh wlan show networks
  interface="nope"`` printed "There is no such wireless interface on the
  system." and returned 0. The pane used to parse whatever came back and
  report "0 network(s) found" -- so a missing adapter, a stopped WLAN
  AutoConfig service, a radio switched off, or Windows 11's location-
  permission gate (24H2+: "Network shell commands need location permission
  to access WLAN information") all looked like an empty neighbourhood.
  :func:`classify_scan_output` reads the TEXT and answers with the reason.
  An empty scan is only reported as empty when netsh itself said so.
* **netsh reports the band itself** (``Band : 2.4 GHz`` / ``5 GHz`` / ``6
  GHz``), and the driver here lists a 6 GHz range (5955-7115 MHz). Deriving
  the band from the channel number cannot work once 6 GHz exists: 6 GHz
  channels are numbered 1-233, so channel 5 in 6 GHz was being filed as
  2.4 GHz. The channel number is only the fallback when there is no Band
  line (older builds).
* **Radio type and BSS Load are in the output and were dropped.** Here:
  802.11be, 802.11ax and three 802.11n access points; one BSSID advertised
  ``Channel Utilization: 8 (3 %)`` -- a figure the AP measures itself, and
  the only airtime number Windows exposes without a capture.
* **A hidden network has an empty SSID** (``SSID 5 :``) and was shown as a
  blank cell.
* **``Radio status`` spans two lines** ("Hardware On" / "Software On"); the
  old ``key : value`` parser dropped the continuation line, so a radio
  switched off in software was invisible on the Current Connection tab.
* **2.4 GHz channels overlap.** Channel centres are 5 MHz apart and a
  transmission's spectral mask is 22 MHz wide, so channels fewer than five
  apart interfere (that is why 1/6/11 is the rule). Here the networks sit
  on 1, 3, 9, 10 and 10, and the old Channel Map showed channel 10 with "2
  networks" when 9 overlaps it too. :func:`overlap_report_24ghz` counts
  overlap by frequency and :func:`recommend_24ghz_channel` scores 1/6/11.
  netsh does not report channel WIDTH, so the 22 MHz mask is assumed;
  a 40 MHz neighbour overlaps more than this counts.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import logging

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

BANDS = ("2.4 GHz", "5 GHz", "6 GHz")

#: The non-overlapping 2.4 GHz channels in most regulatory domains.
CLEAN_24GHZ_CHANNELS = (1, 6, 11)


# ── running netsh ───────────────────────────────────────────────────────────

def run_netsh(*args: str, timeout: int = 30) -> Tuple[int, str]:
    """``(returncode, stdout+stderr)`` of ``netsh wlan <args>``.

    The return code is NOT a verdict -- netsh exits 0 on its refusals.
    """
    result = subprocess.run(
        ["netsh", "wlan", *args],
        capture_output=True, text=True, errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=timeout,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


# ── refusal / state classification ──────────────────────────────────────────

@dataclass(frozen=True)
class ScanProblem:
    """Why netsh returned no networks. ``kind`` is stable, ``message`` is
    what the user reads, ``detail`` is netsh's own words."""
    kind: str
    message: str
    detail: str = ""


#: (kind, phrases in netsh's text, what to tell the user). Order matters:
#: the location message also mentions error 5 / elevation, and must win.
_PROBLEMS = (
    ("location",
     ("location permission", "ms-settings:privacy-location"),
     "Windows refused the scan: netsh needs Location permission "
     "(Settings > Privacy & security > Location, 'Let desktop apps access "
     "your location'). This is not an empty neighbourhood."),
    ("no_adapter",
     ("no wireless interface", "no such wireless interface"),
     "No Wi-Fi adapter is present (or it is disabled in Device Manager / "
     "Network Connections)."),
    ("service",
     ("wlansvc) is not running", "autoconfig service", "wlansvc is not running"),
     "The WLAN AutoConfig service (WlanSvc) is not running, so Windows "
     "cannot scan. Start it from Services."),
    ("radio_off",
     ("powered down", "radio is off", "radio is turned off"),
     "The Wi-Fi radio is switched off (airplane mode, the Wi-Fi toggle, or "
     "a hardware switch)."),
    ("denied",
     ("access is denied", "returns error 5", "requires elevation"),
     "Windows refused the scan (access denied)."),
)


def classify_scan_output(text: str, network_count: int) -> Optional[ScanProblem]:
    """``None`` when the scan is trustworthy, else the reason it is not.

    A scan that parsed at least one network is trustworthy whatever else it
    says. Zero networks is only an answer when netsh itself said "There are
    0 networks currently visible"; any other empty result is a problem, and
    an unrecognised one carries netsh's first line rather than a guess.
    """
    if network_count:
        return None
    lowered = text.lower()
    first = _first_line(text)
    for kind, phrases, message in _PROBLEMS:
        if any(p in lowered for p in phrases):
            return ScanProblem(kind, message, first)
    if re.search(r"\b0 networks? currently visible", lowered):
        return None
    if not text.strip():
        return ScanProblem("no_output", "netsh returned no output at all.")
    return ScanProblem(
        "unrecognised",
        "netsh returned no networks and no reason this tool recognises.",
        first)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


# ── networks ────────────────────────────────────────────────────────────────

HIDDEN_SSID = "(hidden network)"


def channel_to_band(channel: str) -> str:
    """Fallback only: 1-14 is 2.4 GHz, 32-177 is 5 GHz. Anything else is
    unknown -- a 6 GHz channel cannot be told apart by number."""
    try:
        n = int(channel)
    except (TypeError, ValueError):
        return ""
    if 1 <= n <= 14:
        return "2.4 GHz"
    if 32 <= n <= 177:
        return "5 GHz"
    return ""


def _normalise_band(text: str) -> str:
    m = re.match(r"\s*([\d.]+)\s*GHz", text, re.I)
    if not m:
        return ""
    value = m.group(1)
    return {"2.4": "2.4 GHz", "5": "5 GHz", "6": "6 GHz"}.get(value, f"{value} GHz")


_NET_RE = {
    "ssid": re.compile(r"^SSID\s+\d+\s*:\s?(.*)$"),
    "bssid": re.compile(r"^BSSID\s+\d+\s*:\s*(.*)$"),
    "auth": re.compile(r"^Authentication\s*:\s*(.*)$"),
    "enc": re.compile(r"^Encryption\s*:\s*(.*)$"),
    "signal": re.compile(r"^Signal\s*:\s*(\d+)\s*%"),
    "channel": re.compile(r"^Channel\s*:\s*(\d+)"),
    "band": re.compile(r"^Band\s*:\s*(.*)$"),
    "radio": re.compile(r"^Radio type\s*:\s*(.*)$"),
    "util": re.compile(r"^Channel Utilization\s*:\s*\d+\s*\((\d+)\s*%\)"),
    "stations": re.compile(r"^Connected Stations\s*:\s*(\d+)"),
}


def parse_networks_text(text: str) -> List[Dict]:
    """``netsh wlan show networks mode=bssid`` -> one dict per BSSID, sorted
    strongest first. Keys: SSID, Authentication, Security, BSSID, Signal %,
    Channel, Band, Radio, Utilization % (only when the AP advertises BSS
    Load), Stations."""
    networks: List[Dict] = []
    net: Dict = {}
    bss: Dict = {}

    def flush():
        if bss and net:
            entry = dict(net)
            entry.update(bss)
            if not entry.get("Band"):
                entry["Band"] = channel_to_band(entry.get("Channel", ""))
            networks.append(entry)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _NET_RE["ssid"].match(line)
        if m:
            flush()
            bss = {}
            net = {"SSID": m.group(1).strip() or HIDDEN_SSID}
            continue
        m = _NET_RE["bssid"].match(line)
        if m:
            flush()
            bss = {"BSSID": m.group(1).strip()}
            continue
        if not bss:
            for key, col in (("auth", "Authentication"), ("enc", "Security")):
                m = _NET_RE[key].match(line)
                if m and net:
                    net[col] = m.group(1).strip()
            continue
        _parse_bss_line(line, bss)
    flush()
    return sorted(networks, key=lambda n: n.get("Signal %", 0), reverse=True)


def _parse_bss_line(line: str, bss: Dict) -> None:
    m = _NET_RE["signal"].match(line)
    if m:
        bss["Signal %"] = int(m.group(1))
        return
    m = _NET_RE["channel"].match(line)
    if m:
        bss["Channel"] = m.group(1)
        return
    m = _NET_RE["band"].match(line)
    if m:
        bss["Band"] = _normalise_band(m.group(1))
        return
    m = _NET_RE["radio"].match(line)
    if m:
        bss["Radio"] = m.group(1).strip()
        return
    m = _NET_RE["util"].match(line)
    if m:
        bss["Utilization %"] = int(m.group(1))
        return
    m = _NET_RE["stations"].match(line)
    if m:
        bss["Stations"] = int(m.group(1))


# ── interfaces ──────────────────────────────────────────────────────────────

def parse_interfaces_text(text: str) -> List[Dict[str, str]]:
    """``netsh wlan show interfaces`` -> one ordered dict per interface.

    A line with no ``:`` continues the previous key's value ("Radio status :
    Hardware On" / "Software On" -> "Hardware On, Software On"). Lines before
    the first ``Name`` ("There is 1 interface on the system:") are skipped.
    """
    interfaces: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    last_key = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key, sep, val = line.partition(":")
        if sep and key.strip():
            key = key.strip()
            if key == "Name" and current:
                interfaces.append(current)
                current = {}
            if key != "Name" and not current:
                continue          # header text before the first interface
            current[key] = val.strip()
            last_key = key
        elif current and last_key:
            current[last_key] = f"{current[last_key]}, {line}" if current[last_key] else line
    if current:
        interfaces.append(current)
    return interfaces


def radio_problem(interface: Dict[str, str]) -> Optional[str]:
    """``"Hardware Off"`` / ``"Software Off"`` when the radio is switched off."""
    status = interface.get("Radio status", "")
    off = [part.strip() for part in status.split(",") if part.strip().lower().endswith("off")]
    return ", ".join(off) or None


# ── 2.4 GHz overlap ─────────────────────────────────────────────────────────

def freq_24ghz(channel: int) -> Optional[int]:
    """Centre frequency in MHz of a 2.4 GHz channel, ``None`` if not one."""
    if channel == 14:
        return 2484
    if 1 <= channel <= 13:
        return 2407 + 5 * channel
    return None


def channels_overlap_24(a: int, b: int, width_mhz: int = 22) -> bool:
    """Two 2.4 GHz channels of ``width_mhz`` overlap when their centres are
    closer than one channel width -- with the 22 MHz mask, fewer than five
    channels apart (1 and 6 do not; 1 and 5 do). Same channel overlaps."""
    fa, fb = freq_24ghz(a), freq_24ghz(b)
    if fa is None or fb is None:
        return False
    return abs(fa - fb) < width_mhz


@dataclass
class ChannelLoad:
    channel: int
    same: List[Dict] = field(default_factory=list)
    adjacent: List[Dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.same) + len(self.adjacent)


def _int_channel(net: Dict) -> Optional[int]:
    try:
        return int(net.get("Channel", ""))
    except (TypeError, ValueError):
        return None


def overlap_report_24ghz(networks: List[Dict]) -> Dict[int, ChannelLoad]:
    """For every occupied 2.4 GHz channel: the BSSIDs on it, and the BSSIDs
    on OTHER channels close enough to overlap it."""
    on_24 = [(n, _int_channel(n)) for n in networks if n.get("Band") == "2.4 GHz"]
    on_24 = [(n, ch) for n, ch in on_24 if ch is not None]
    report: Dict[int, ChannelLoad] = {}
    for ch in sorted({ch for _, ch in on_24}):
        load = ChannelLoad(ch)
        for net, other in on_24:
            if other == ch:
                load.same.append(net)
            elif channels_overlap_24(ch, other):
                load.adjacent.append(net)
        report[ch] = load
    return report


@dataclass(frozen=True)
class ChannelAdvice:
    channel: int
    score: int            # sum of the signal % of every overlapping BSSID
    overlapping: int      # how many BSSIDs overlap it
    ranking: Tuple[Tuple[int, int, int], ...]   # (channel, score, count) best first


def recommend_24ghz_channel(networks: List[Dict],
                            exclude_ssids: Tuple[str, ...] = ()) -> Optional[ChannelAdvice]:
    """The least-contended of channels 1/6/11, weighting each overlapping
    BSSID by its signal (a neighbour at 89% costs more airtime than one at
    20%). Networks in ``exclude_ssids`` -- your own -- do not count against
    a channel. ``None`` when nothing is on 2.4 GHz to judge by."""
    others = [n for n in networks
              if n.get("Band") == "2.4 GHz" and n.get("SSID") not in exclude_ssids
              and _int_channel(n) is not None]
    if not others:
        return None
    ranking = []
    for candidate in CLEAN_24GHZ_CHANNELS:
        hits = [n for n in others if channels_overlap_24(candidate, _int_channel(n))]
        ranking.append((candidate, sum(int(n.get("Signal %", 0)) for n in hits), len(hits)))
    ranking.sort(key=lambda r: (r[1], r[2], r[0]))
    best = ranking[0]
    return ChannelAdvice(best[0], best[1], best[2], tuple(ranking))


def build_channel_map(networks: List[Dict]) -> Dict[str, Dict[int, int]]:
    """``{band: {channel: count}}`` for every band, exact-channel counts."""
    result: Dict[str, Dict[int, int]] = {band: {} for band in BANDS}
    for n in networks:
        band = n.get("Band", "")
        ch = _int_channel(n)
        if band in result and ch is not None:
            result[band][ch] = result[band].get(ch, 0) + 1
    return result


def connected_ssids(interfaces: List[Dict[str, str]]) -> Tuple[str, ...]:
    """SSIDs the machine is connected to right now (``State : connected``)."""
    return tuple(i["SSID"] for i in interfaces
                 if i.get("State", "").lower() == "connected" and i.get("SSID"))


# ── one scan ────────────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    networks: List[Dict]
    interfaces: List[Dict[str, str]]
    problem: Optional[ScanProblem]
    interface_problem: Optional[ScanProblem]


def scan() -> ScanResult:
    """Run both netsh reads and classify them. Blocking; call on a Worker."""
    _rc, net_text = run_netsh("show", "networks", "mode=bssid")
    networks = parse_networks_text(net_text)
    problem = classify_scan_output(net_text, len(networks))

    _rc, if_text = run_netsh("show", "interfaces")
    interfaces = parse_interfaces_text(if_text)
    if_problem = None if interfaces else classify_scan_output(if_text, 0)
    if if_problem is None and not interfaces:
        if_problem = ScanProblem("unrecognised", "netsh listed no interfaces.",
                                 _first_line(if_text))
    if problem is not None:
        logger.info("Wi-Fi scan not trustworthy: %s (%s)", problem.kind, problem.detail)
    return ScanResult(networks, interfaces, problem, if_problem)
