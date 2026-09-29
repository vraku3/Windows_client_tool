"""Qt-free SMB exposure audit: shares + permissions, sessions, open files,
server/client SMB settings, guest account, and computed findings.

One PowerShell call gathers everything as JSON. Each section reports its own
failure in `errors`; a section that failed is UNKNOWN in the findings, never
"nothing found". (Measured 2026-09-26: Get-SmbShare, Get-SmbShareAccess,
Get-SmbSession, Get-SmbOpenFile and Get-SmbServerConfiguration all answer
unelevated on this machine, but that is not promised elsewhere.)

Per-share and server-wide SMB *encryption* is a distinct setting from
signing (already covered above) and from SMB1 -- and it was being collected
into `ShareData` (both `EncryptData` per share and `RejectUnencryptedAccess`
server-wide) without ever being surfaced. Confirmed live 2026-09-29 on this
machine: `Get-SmbShare` really does return an `EncryptData` property per
share (`False` for the only real share here, `IPC$`), and
`Get-SmbServerConfiguration` really does return `EncryptData=False,
RejectUnencryptedAccess=True` -- the Windows default (encryption optional,
but a client that DOES negotiate it is never silently downgraded). A server
that turns EncryptData on but leaves RejectUnencryptedAccess off is a real,
actionable inconsistency: it announces "this share needs encryption" while
still accepting a client that cannot do it.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SEV_HIGH, SEV_MEDIUM, SEV_LOW, SEV_INFO = "high", "medium", "low", "info"
_SEV_ORDER = {SEV_HIGH: 0, SEV_MEDIUM: 1, SEV_LOW: 2, SEV_INFO: 3}

POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$o = @{ errors = @{} }
function Sect($n, [scriptblock]$b) { try { $o[$n] = @(& $b) } catch { $o.errors[$n] = $_.Exception.Message } }
Sect 'shares' { Get-SmbShare | ForEach-Object { [pscustomobject]@{ Name=$_.Name; Path=$_.Path; Description=$_.Description;
    ShareType=[string]$_.ShareType; Special=[bool]$_.Special; CurrentUsers=[int]$_.CurrentUsers;
    Enum=[string]$_.FolderEnumerationMode; Encrypt=[bool]$_.EncryptData } } }
Sect 'access' { Get-SmbShare | ForEach-Object { $n=$_.Name; Get-SmbShareAccess -Name $n | ForEach-Object {
    [pscustomobject]@{ Share=$n; Account=$_.AccountName; Type=[string]$_.AccessControlType; Right=[string]$_.AccessRight } } } }
Sect 'sessions' { Get-SmbSession | ForEach-Object { [pscustomobject]@{ Client=$_.ClientComputerName; User=$_.ClientUserName;
    Opens=[int]$_.NumOpens; Seconds=[int64]$_.SecondsExists; Idle=[int64]$_.SecondsIdle; Dialect=[string]$_.Dialect } } }
Sect 'open_files' { Get-SmbOpenFile | ForEach-Object { [pscustomobject]@{ Client=$_.ClientComputerName; User=$_.ClientUserName;
    Path=$_.Path; Share=$_.ShareRelativePath } } }
Sect 'server' { Get-SmbServerConfiguration | ForEach-Object { [pscustomobject]@{ Smb1=[bool]$_.EnableSMB1Protocol;
    Smb2=[bool]$_.EnableSMB2Protocol; RequireSigning=[bool]$_.RequireSecuritySignature; EnableSigning=[bool]$_.EnableSecuritySignature;
    Encrypt=[bool]$_.EncryptData; RejectUnencrypted=[bool]$_.RejectUnencryptedAccess;
    AutoShareServer=[bool]$_.AutoShareServer; AutoShareWks=[bool]$_.AutoShareWorkstation } } }
Sect 'client' { Get-SmbClientConfiguration | ForEach-Object { [pscustomobject]@{ InsecureGuest=[bool]$_.EnableInsecureGuestLogons;
    RequireSigning=[bool]$_.RequireSecuritySignature } } }
Sect 'guest' { Get-LocalUser -Name Guest | ForEach-Object { [pscustomobject]@{ Enabled=[bool]$_.Enabled } } }
$o | ConvertTo-Json -Depth 5 -Compress
"""

# Accounts that make a share reachable by essentially anyone.
_BROAD = ("everyone", "anonymous logon", "authenticated users", "guests", "builtin\\users", "users")
_WRITE = ("full", "change")


@dataclass
class ShareData:
    shares: Optional[List[dict]] = None
    access: Optional[List[dict]] = None
    sessions: Optional[List[dict]] = None
    open_files: Optional[List[dict]] = None
    server: Optional[dict] = None
    client: Optional[dict] = None
    guest: Optional[dict] = None
    errors: Dict[str, str] = field(default_factory=dict)
    fatal: str = ""


@dataclass
class Finding:
    severity: str
    key: str
    title: str
    detail: str
    share: str = ""


def parse_collected(text: str) -> ShareData:
    """JSON from POWERSHELL_SCRIPT -> ShareData. Bad JSON is `fatal`, not empty."""
    try:
        raw = json.loads(text)
    except (ValueError, TypeError) as exc:
        return ShareData(fatal="Unreadable answer from PowerShell: %s" % exc)
    errors = dict(raw.get("errors") or {})

    def sect(name: str) -> Optional[list]:
        if name in errors or name not in raw:
            return None
        v = raw[name]
        return v if isinstance(v, list) else [v]

    def one(name: str) -> Optional[dict]:
        s = sect(name)
        return s[0] if s else None

    return ShareData(sect("shares"), sect("access"), sect("sessions"), sect("open_files"),
                     one("server"), one("client"), one("guest"), errors)


def collect(timeout: int = 60) -> ShareData:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SCRIPT],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("SMB collection failed: %s", exc)
        return ShareData(fatal="Could not run PowerShell: %s" % exc)
    if not proc.stdout.strip():
        return ShareData(fatal=(proc.stderr or "PowerShell returned nothing").strip()[:300])
    return parse_collected(proc.stdout)


_cache_lock = threading.Lock()
_cache: dict = {"at": 0.0, "data": None}


def collect_cached(max_age: float = 10.0, now=time.monotonic, fn=None) -> ShareData:
    """One PowerShell run (~1s) serves every tab refreshing within max_age."""
    fn = fn or collect
    with _cache_lock:
        if _cache["data"] is not None and now() - _cache["at"] < max_age:
            return _cache["data"]
        data = fn()
        _cache["data"], _cache["at"] = data, now()
        return data


def is_admin_share(share: dict) -> bool:
    return bool(share.get("Special")) or share.get("Name", "").endswith("$")


def _acl(data: ShareData, share: str) -> List[dict]:
    return [a for a in (data.access or []) if a.get("Share") == share]


def audit(data: ShareData, path_exists=os.path.exists) -> List[Finding]:
    out: List[Finding] = []
    if data.fatal:
        return [Finding(SEV_INFO, "unreadable", "SMB state could not be read", data.fatal)]
    for sect, why in data.errors.items():
        out.append(Finding(SEV_INFO, "unreadable_" + sect,
                           "Could not read: %s" % sect.replace("_", " "),
                           "%s (this is unknown, not 'none found')" % why))

    srv = data.server
    if srv:
        if srv.get("Smb1"):
            out.append(Finding(SEV_HIGH, "smb1", "SMB1 server protocol is enabled",
                               "SMB1 has no pre-authentication integrity and is the WannaCry/EternalBlue path."))
        if not srv.get("RequireSigning"):
            out.append(Finding(SEV_MEDIUM, "signing", "SMB signing is not required by the server",
                               "Unsigned SMB sessions can be relayed or tampered with."))
        if not srv.get("Encrypt"):
            out.append(Finding(SEV_INFO, "encrypt", "SMB encryption is not enforced server-wide",
                               "Fine on a trusted LAN; consider it across untrusted links."))
        elif not srv.get("RejectUnencrypted"):
            out.append(Finding(SEV_LOW, "reject_unencrypted_off",
                               "Encryption is required server-wide, but unencrypted access is not rejected",
                               "EncryptData is on but RejectUnencryptedAccess is off: a client that cannot "
                               "negotiate SMB 3.x encryption still connects unencrypted instead of being refused, "
                               "which quietly defeats the point of requiring encryption."))
    cli = data.client
    if cli and cli.get("InsecureGuest"):
        out.append(Finding(SEV_MEDIUM, "insecure_guest", "Insecure guest logons are allowed (client)",
                           "This machine will connect to shares that accept unauthenticated guests."))
    if data.guest and data.guest.get("Enabled"):
        out.append(Finding(SEV_MEDIUM, "guest_on", "The Guest account is enabled",
                           "Shares granting Everyone can be reached without a password."))

    for s in data.shares or []:
        out.extend(_audit_share(data, s, path_exists))
    n_open = len(data.open_files or [])
    if data.sessions:
        out.append(Finding(SEV_INFO, "sessions", "%d SMB session(s) connected, %d open file(s)"
                           % (len(data.sessions), n_open), "See the Sessions tab."))
    out.sort(key=lambda f: _SEV_ORDER[f.severity])
    return out


def _audit_share(data: ShareData, s: dict, path_exists) -> List[Finding]:
    name, out = s.get("Name", ""), []
    if s.get("ShareType", "").lower().startswith("interprocess"):
        return out
    if is_admin_share(s):
        if data.server and not data.server.get("AutoShareWks") and not data.server.get("AutoShareServer"):
            out.append(Finding(SEV_LOW, "admin_share", "Administrative share %s exists" % name,
                               "Automatic admin shares are disabled, yet this one is present: someone created it.", name))
        return out
    if data.access is not None:
        for a in _acl(data, name):
            who, right = a.get("Account", "").lower(), a.get("Right", "").lower()
            if a.get("Type", "").lower() != "allow" or not any(who.endswith(b) for b in _BROAD):
                continue
            if right in _WRITE:
                out.append(Finding(SEV_HIGH, "share_write", "%s grants %s to %s" % (name, a["Right"], a["Account"]),
                                   "Anyone in that group can modify files through this share.", name))
            else:
                out.append(Finding(SEV_MEDIUM, "share_read", "%s grants %s to %s" % (name, a["Right"], a["Account"]),
                                   "Readable by that whole group.", name))
    p = s.get("Path") or ""
    if p and not path_exists(p):
        out.append(Finding(SEV_LOW, "share_path_missing", "%s points at a path that does not exist" % name,
                           p, name))
    if s.get("Enum", "").lower() == "unrestricted":
        out.append(Finding(SEV_INFO, "no_abe", "%s lists files a user cannot open" % name,
                           "Access-based enumeration is off.", name))
    if data.server and not data.server.get("Encrypt") and s.get("Encrypt"):
        out.append(Finding(SEV_INFO, "share_encrypt_override", "%s requires SMB encryption" % name,
                           "This share turns on encryption itself even though the server default is off.", name))
    return out


def rows_shares(data: ShareData) -> List[dict]:
    out = []
    for s in data.shares or []:
        acl = _acl(data, s.get("Name", ""))
        access = "; ".join("%s: %s %s" % (a["Account"], a["Type"], a["Right"]) for a in acl) \
            if data.access is not None else "(permissions unreadable)"
        out.append({"Name": s.get("Name", ""), "Kind": "Admin/special" if is_admin_share(s) else "User",
                    "Path": s.get("Path", ""), "Access": access,
                    "Encrypted": "Yes" if s.get("Encrypt") else "No",
                    "Users": s.get("CurrentUsers", 0), "Comment": s.get("Description", "")})
    return out
