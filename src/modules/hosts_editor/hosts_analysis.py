"""Qt-free hosts-file model: lossless parse, validation, findings, backups.

The old editor rewrote the whole file from its table on Save, which dropped
every comment and header, and read any `# two words` comment as a disabled
entry (`# Copyright (c) 1993` became host "(c)" at IP "Copyright"). Here a
commented line is a disabled entry only when what follows the `#` starts with
a real IP address; every other line is kept verbatim and written back
untouched.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

SEV_HIGH, SEV_MEDIUM, SEV_LOW, SEV_INFO = "high", "medium", "low", "info"
_SEV_ORDER = {SEV_HIGH: 0, SEV_MEDIUM: 1, SEV_LOW: 2, SEV_INFO: 3}
_LABEL = re.compile(r"^(?!-)[A-Za-z0-9_-]{1,63}(?<!-)$")
_SINKS = {"0.0.0.0", "127.0.0.1", "::", "::1"}

# Domains a machine cannot do without. Sinking one is sometimes deliberate
# (telemetry lists) but is the classic cause of "X stopped working".
WELL_KNOWN = {
    "microsoft.com": "Microsoft services",
    "windowsupdate.com": "Windows Update",
    "update.microsoft.com": "Windows Update",
    "download.windowsupdate.com": "Windows Update",
    "login.live.com": "Microsoft account sign-in",
    "login.microsoftonline.com": "Entra ID / Microsoft 365 sign-in",
    "microsoftonline.com": "Microsoft 365",
    "office.com": "Microsoft 365",
    "windows.net": "Azure",
    "msftconnecttest.com": "Windows network status (shows 'no internet')",
    "www.msftconnecttest.com": "Windows network status (shows 'no internet')",
    "ctldl.windowsupdate.com": "Certificate trust list updates",
    "crl.microsoft.com": "Certificate revocation",
    "ocsp.digicert.com": "Certificate revocation",
    "google.com": "Google",
    "github.com": "GitHub",
    "cloudflare.com": "Cloudflare",
    "apple.com": "Apple",
    "amazonaws.com": "AWS",
    "pool.ntp.org": "Time sync",
    "time.windows.com": "Windows time sync",
}


@dataclass
class HostLine:
    """One physical line. `entry` lines carry ip/host/comment; others are raw."""
    raw: str
    is_entry: bool = False
    enabled: bool = True
    ip: str = ""
    hosts: List[str] = field(default_factory=list)
    comment: str = ""
    index: int = 0


@dataclass
class Finding:
    severity: str
    key: str
    title: str
    detail: str
    lines: List[int] = field(default_factory=list)   # HostLine.index values


def valid_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text.strip().strip("[]").split("%")[0])
        return True
    except ValueError:
        return False


def valid_hostname(name: str) -> bool:
    n = name.rstrip(".")
    return bool(n) and len(n) <= 253 and all(_LABEL.match(p) for p in n.split("."))


def parse_line(raw: str, index: int = 0) -> HostLine:
    body = raw.strip()
    enabled = True
    if body.startswith("#"):
        body = body[1:].strip()
        enabled = False
    main, _, comment = body.partition("#")
    parts = main.split()
    if len(parts) >= 2 and valid_ip(parts[0]) and (enabled or not _is_prose(parts)):
        return HostLine(raw, True, enabled, parts[0], parts[1:], comment.strip(), index)
    return HostLine(raw, False, index=index)


def _is_prose(parts: Sequence[str]) -> bool:
    """`# 127.0.0.1 localhost` is a disabled entry; `# 1.2.3.4 is our proxy`
    is more likely prose. A hostname list never contains a bare English word
    that is not a valid hostname, so demand every host token be one, few in
    number and free of common English words."""
    hosts = parts[1:]
    if len(hosts) > 3 or any(h.lower() in _STOPWORDS for h in hosts):
        return True
    return not all(valid_hostname(p) for p in hosts)


_STOPWORDS = {"is", "the", "our", "a", "an", "for", "to", "of", "and", "or", "this", "that",
              "was", "are", "as", "on", "in", "it", "not", "we", "use", "used", "old", "new"}


def parse_hosts(text: str) -> List[HostLine]:
    return [parse_line(ln.rstrip("\r\n"), i) for i, ln in enumerate(text.splitlines())]


def entries(lines: Sequence[HostLine]) -> List[HostLine]:
    return [ln for ln in lines if ln.is_entry]


def render_entry(enabled: bool, ip: str, hosts: Sequence[str], comment: str) -> str:
    cmt = "  # %s" % comment if comment else ""
    return "%s%s\t%s%s" % ("" if enabled else "# ", ip, " ".join(hosts), cmt)


def serialize(original: Sequence[HostLine], rows: Sequence[dict]) -> str:
    """Rebuild the file: raw lines verbatim, entries from `rows`.

    `rows` are dicts {origin, enabled, ip, hosts, comment}; `origin` is the
    HostLine.index the row came from, or -1 for a new one. An entry whose
    origin no row claims was deleted. An untouched row keeps its ORIGINAL text
    so spacing and trailing comments do not churn.
    """
    by_origin = {r["origin"]: r for r in rows if r.get("origin", -1) >= 0}
    out: List[str] = []
    for ln in original:
        if not ln.is_entry:
            out.append(ln.raw)
            continue
        r = by_origin.get(ln.index)
        if r is None:
            continue
        same = (r["enabled"] == ln.enabled and r["ip"] == ln.ip
                and list(r["hosts"]) == ln.hosts and r["comment"] == ln.comment)
        out.append(ln.raw if same else render_entry(r["enabled"], r["ip"], r["hosts"], r["comment"]))
    for r in rows:
        if r.get("origin", -1) < 0:
            out.append(render_entry(r["enabled"], r["ip"], r["hosts"], r["comment"]))
    return "\n".join(out) + "\n"


def _domain_matches(host: str, known: str) -> bool:
    h = host.lower().rstrip(".")
    return h == known or h.endswith("." + known)


def analyse(lines: Sequence[HostLine]) -> List[Finding]:
    """Findings over ENABLED entries (a commented one changes nothing)."""
    out: List[Finding] = []
    live = [ln for ln in entries(lines) if ln.enabled]
    for ln in live:
        if not valid_ip(ln.ip):
            out.append(Finding(SEV_HIGH, "bad_ip", "Invalid IP address %r" % ln.ip,
                               "Windows ignores the line.", [ln.index]))
        for h in ln.hosts:
            if not valid_hostname(h):
                out.append(Finding(SEV_MEDIUM, "bad_host", "Invalid hostname %r" % h,
                                   "Labels are letters, digits and hyphens, max 63 chars.", [ln.index]))

    by_host: Dict[str, List[HostLine]] = {}
    for ln in live:
        for h in ln.hosts:
            by_host.setdefault(h.lower(), []).append(ln)
    for host, lns in sorted(by_host.items()):
        ips = {l.ip.lower() for l in lns}
        if len(lns) > 1 and len(ips) > 1:
            out.append(Finding(SEV_HIGH, "conflict", "%s maps to %d different addresses" % (host, len(ips)),
                               "Only the first matching line applies: %s" % ", ".join(sorted(ips)),
                               [l.index for l in lns]))
        elif len(lns) > 1:
            out.append(Finding(SEV_LOW, "duplicate", "%s is listed %d times" % (host, len(lns)),
                               "Identical mapping repeated.", [l.index for l in lns]))

    sunk: Dict[str, List[int]] = {}
    hijack: List[Finding] = []
    for ln in live:
        for h in ln.hosts:
            for known, why in WELL_KNOWN.items():
                if not _domain_matches(h, known):
                    continue
                if ln.ip in _SINKS:
                    sunk.setdefault("%s (%s)" % (h, why), []).append(ln.index)
                elif valid_ip(ln.ip):
                    hijack.append(Finding(
                        SEV_MEDIUM, "redirect", "%s redirected to %s" % (h, ln.ip),
                        "%s is pointed at a fixed address. Fine for an intranet override; "
                        "it is also how malware intercepts sign-ins and updates." % why, [ln.index]))
                break
    for label, idxs in sorted(sunk.items()):
        out.append(Finding(SEV_MEDIUM, "blocked_known", "Blocks %s" % label,
                           "Sinked to a null address; this service will stop working.", idxs))
    out.extend(hijack)

    if not any(ln.hosts and "localhost" in [h.lower() for h in ln.hosts] for ln in live):
        out.append(Finding(SEV_INFO, "no_localhost", "No active localhost entry",
                           "Windows resolves localhost internally, so this is normal on Windows 10/11."))
    out.sort(key=lambda f: _SEV_ORDER[f.severity])
    return out


# ------------------------------------------------------------------- backups

def backup_hosts(hosts_path: str, backup_dir: str) -> str:
    """Copy the hosts file to a timestamped file in backup_dir; return its path.
    Raises OSError; the caller must not save if this failed."""
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, "hosts_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(backup_dir, "hosts_%s_%d.txt" % (time.strftime("%Y%m%d_%H%M%S"), n))
        n += 1
    shutil.copy2(hosts_path, dest)
    if os.path.getsize(dest) != os.path.getsize(hosts_path):
        raise OSError("backup size differs from the original")
    return dest


def list_backups(backup_dir: str) -> List[str]:
    """Newest first. A missing folder is 'no backups', not an error."""
    if not os.path.isdir(backup_dir):
        return []
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
             if f.startswith("hosts_") and f.endswith(".txt")]
    return sorted(files, key=os.path.getmtime, reverse=True)


def write_and_verify(hosts_path: str, text: str) -> Optional[str]:
    """Write then read back. Returns None on success or the reason."""
    try:
        with open(hosts_path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(text)
        with open(hosts_path, "r", encoding="utf-8", errors="replace") as fh:
            back = fh.read()
    except OSError as exc:
        return str(exc)
    if back.replace("\r\n", "\n") != text.replace("\r\n", "\n"):
        return "the file read back differs from what was written"
    return None
