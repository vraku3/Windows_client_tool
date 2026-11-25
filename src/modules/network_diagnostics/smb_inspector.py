"""SMB Client & Server Configuration & Dialect Inspector.

Inspects SMB protocol versions, SMB signing enforcement, encryption posture, and active sessions.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SmbServerPostures:
    smbv1_enabled: bool = False
    smbv2_enabled: bool = True
    require_signing: bool = True
    encrypt_data: bool = False
    is_secure: bool = True
    summary: str = "SMB 2/3 Active, Signing Enforced"


@dataclass
class SmbActiveConnection:
    server_name: str
    share_name: str
    dialect: str
    num_opens: int
    is_encrypted: bool
    is_signed: bool


@dataclass
class SmbInspectionReport:
    server_posture: SmbServerPostures
    active_connections: List[SmbActiveConnection] = field(default_factory=list)
    verdict: str = "Normal"


def inspect_smb_posture() -> SmbInspectionReport:
    """Query SMB server configuration and active connection dialects."""
    ps_cmd = r"""
$server = Get-SmbServerConfiguration -ErrorAction SilentlyContinue |
    Select-Object EnableSMB1Protocol, EnableSMB2Protocol, RequireSecuritySignature, EncryptData
$conns = Get-SmbConnection -ErrorAction SilentlyContinue |
    Select-Object ServerName, ShareName, Dialect, NumOpens, Encrypted, Signed
[PSCustomObject]@{
    Server = $server
    Connections = @($conns)
} | ConvertTo-Json -Depth 3 -Compress
"""
    report = SmbInspectionReport(server_posture=SmbServerPostures())
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            report.verdict = "SMB configuration query unreadable"
            return report

        data = json.loads(proc.stdout)
        srv = data.get("Server", {})
        conns_raw = data.get("Connections", [])
        if isinstance(conns_raw, dict):
            conns_raw = [conns_raw]

        v1 = bool(srv.get("EnableSMB1Protocol", False))
        v2 = bool(srv.get("EnableSMB2Protocol", True))
        signing = bool(srv.get("RequireSecuritySignature", True))
        enc = bool(srv.get("EncryptData", False))

        is_sec = not v1 and signing

        desc_parts = []
        if v1:
            desc_parts.append("WARNING: Insecure SMBv1 is enabled!")
        else:
            desc_parts.append("SMBv1 Disabled")

        if signing:
            desc_parts.append("SMB Signing Enforced")
        else:
            desc_parts.append("SMB Signing Optional/Disabled")

        if enc:
            desc_parts.append("SMB Encryption Active")

        report.server_posture = SmbServerPostures(
            smbv1_enabled=v1,
            smbv2_enabled=v2,
            require_signing=signing,
            encrypt_data=enc,
            is_secure=is_sec,
            summary=" • ".join(desc_parts),
        )

        for c in conns_raw:
            report.active_connections.append(SmbActiveConnection(
                server_name=c.get("ServerName", ""),
                share_name=c.get("ShareName", ""),
                dialect=c.get("Dialect", "3.1.1"),
                num_opens=int(c.get("NumOpens", 0)),
                is_encrypted=bool(c.get("Encrypted", False)),
                is_signed=bool(c.get("Signed", False)),
            ))

        report.verdict = "Secure Posture" if is_sec else "Security Attention Required"

    except Exception as exc:
        report.verdict = f"Error inspecting SMB: {exc}"

    return report
