"""Saved Wi-Fi profile security audit. Qt-free -- no PyQt6 import here, same
``scan/``+``store/`` split TreeSize and Monitor Control keep, so the parser is
testable with no display attached.

The Wi-Fi Analyzer tab (``wifi_module.py``) only shows what is CURRENTLY
broadcasting in the air right now. It says nothing about what Windows already
REMEMBERS and will silently rejoin -- and the existing "WiFi Profiles" card in
Network Diagnostics's own tools tab (``network_module.py``) only lists names
and dumps one profile's raw ``netsh wlan show profile ... key=clear`` text on
click; it never states the security TYPE across all saved profiles, and asking
for ``key=clear`` on every click needlessly puts a cleartext PSK in a
``QPlainTextEdit`` history each time.

Real finding on this machine (2026-09-30, ``netsh wlan show profile``, run
UNELEVATED -- no key requested, none needed for this): of 7 saved profiles,
two report ``Authentication: Open`` / ``Cipher: None`` / ``Security key:
Absent`` -- a smart-camera network and a phone hotspot. Windows will connect
to either the moment something nearby broadcasts a matching SSID, with no key
and no prompt: the exact setup a rogue access point using a familiar SSID
needs. Neither of the two existing Wi-Fi UIs surfaces this as a finding.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000

# Ordered weakest-to-strongest, for sorting a findings list weakest-first.
RATING_ORDER = {"open": 0, "wep": 1, "unknown": 2, "wpa": 3, "wpa2": 4, "wpa3": 5}


@dataclass
class ProfileSecurity:
    """One saved profile's security shape. ``refused`` carries a read that
    failed -- that is never collapsed into ``rating == "unknown"``, which
    means "we read it and didn't recognise the auth type", a different
    thing from "we could not read it at all"."""

    name: str
    auth_types: List[str] = field(default_factory=list)
    ciphers: List[str] = field(default_factory=list)
    has_key: Optional[bool] = None          # None: "Security key" line absent from output
    auto_connect: Optional[bool] = None     # None: "Connection mode" line absent
    hidden: Optional[bool] = None           # True: profile connects even when not broadcasting
    rating: str = "unknown"                 # open / wep / wpa / wpa2 / wpa3 / unknown
    refused: bool = False
    reason: str = ""


def _run_netsh(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["netsh", "wlan"] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=timeout,
    )


# "All User Profile" is the form confirmed live on this machine (7 profiles).
# "Group Policy Profile" is netsh's own documented label for the other
# section header ("Group policy profiles (read only)") but this machine has
# none to confirm the per-row wording against -- accepted defensively so a
# domain-joined machine's GPO-pushed profiles are not silently dropped from
# the list, never assumed to be the only other form that exists.
_PROFILE_LINE = re.compile(r"\s*(?:All User Profile|Group Policy Profile)\s*:\s*(.+)")


def list_profile_names() -> Optional[List[str]]:
    """Saved Wi-Fi profile names (user + group-policy). ``None`` means the
    list itself could not be read (netsh refused or errored), never "no
    profiles" -- that case is an empty list."""
    try:
        r = _run_netsh("show", "profiles")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("netsh wlan show profiles failed: %s", e)
        return None
    if r.returncode != 0:
        logger.warning("netsh wlan show profiles refused: %s", (r.stderr or r.stdout or "").strip()[:200])
        return None
    names: List[str] = []
    for line in r.stdout.splitlines():
        m = _PROFILE_LINE.match(line)
        if m:
            name = m.group(1).strip()
            if name and name != "<None>":
                names.append(name)
    return names


def classify(auth_types: List[str], ciphers: Optional[List[str]] = None) -> str:
    """Weakest-first: a profile carrying both a modern and a legacy auth
    entry (a WPA3-transition network) is judged by whichever is actually
    exploitable, not by whichever line happened to match first.

    WEP is checked against BOTH fields, not just Authentication -- netsh
    documents a WEP network as ``Authentication: Open`` (WEP's "open
    system" auth) with ``Cipher: WEP`` carrying the actual weak-crypto
    fact. Unlike the Open/Cipher=None finding this module was built from
    (confirmed live on this machine), no WEP network was available here to
    confirm that exact pairing against real netsh output -- documented
    behaviour, not independently re-verified.
    """
    joined = " ".join(a.upper() for a in auth_types)
    joined += " " + " ".join(c.upper() for c in (ciphers or []))
    if "WEP" in joined:
        return "wep"
    if "OPEN" in joined:
        return "open"
    if "WPA3" in joined:
        return "wpa3"
    if "WPA2" in joined:
        return "wpa2"
    if "WPA" in joined:
        return "wpa"
    return "unknown"


def parse_profile_detail(name: str, text: str) -> ProfileSecurity:
    """Parse ``netsh wlan show profile name=<name>`` output (no ``key=clear``
    needed -- Authentication/Cipher/"Security key" presence are all printed
    without it)."""
    auth_types: List[str] = []
    ciphers: List[str] = []
    has_key: Optional[bool] = None
    auto_connect: Optional[bool] = None
    hidden: Optional[bool] = None

    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"Authentication\s*:\s*(.+)", s)
        if m:
            auth_types.append(m.group(1).strip())
            continue
        m = re.match(r"Cipher\s*:\s*(.+)", s)
        if m:
            ciphers.append(m.group(1).strip())
            continue
        m = re.match(r"Security key\s*:\s*(.+)", s)
        if m:
            has_key = m.group(1).strip().lower() == "present"
            continue
        m = re.match(r"Connection mode\s*:\s*(.+)", s)
        if m:
            auto_connect = "automatically" in m.group(1).lower()
            continue
        m = re.match(r"Network broadcast\s*:\s*(.+)", s)
        if m:
            # Confirmed wording on this machine: "Connect only if this
            # network is broadcasting" (not hidden) is the only value seen;
            # the opposite phrase netsh documents for a non-broadcast
            # profile contains "not broadcasting".
            hidden = "not broadcasting" in m.group(1).lower()
            continue

    return ProfileSecurity(
        name=name, auth_types=auth_types, ciphers=ciphers, has_key=has_key,
        auto_connect=auto_connect, hidden=hidden, rating=classify(auth_types, ciphers),
    )


def get_profile_security(name: str) -> ProfileSecurity:
    """Read one saved profile's security shape from the real machine."""
    try:
        r = _run_netsh("show", "profile", f"name={name}")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("netsh wlan show profile %r failed: %s", name, e)
        return ProfileSecurity(name=name, refused=True, reason=str(e))
    if r.returncode != 0:
        reason = (r.stderr or r.stdout or "").strip()[:200] or "netsh refused"
        logger.warning("netsh wlan show profile %r refused: %s", name, reason)
        return ProfileSecurity(name=name, refused=True, reason=reason)
    return parse_profile_detail(name, r.stdout)


def audit_profiles() -> Optional[List[ProfileSecurity]]:
    """Every saved profile's security shape. ``None`` only when the profile
    LIST itself could not be read; one profile's own refusal rides on its
    own ``ProfileSecurity.refused`` rather than aborting the whole audit."""
    names = list_profile_names()
    if names is None:
        return None
    return [get_profile_security(n) for n in names]


def risky_profiles(profiles: List[ProfileSecurity]) -> List[ProfileSecurity]:
    """Open or WEP profiles -- Windows will join either with no key and no
    warning the moment something nearby broadcasts a matching SSID."""
    return [p for p in profiles if not p.refused and p.rating in ("open", "wep")]


def hidden_profiles(profiles: List[ProfileSecurity]) -> List[ProfileSecurity]:
    """Profiles Windows will probe for by name even out of range -- the SSID
    leaks in cleartext 802.11 probe requests this device itself broadcasts."""
    return [p for p in profiles if not p.refused and p.hidden]
