"""Windows Certificate Store Validator & TLS Inspection Sentinel.

Inspects Cert:\\LocalMachine\\Root and Cert:\\LocalMachine\\CA to detect:
1. Expired Root or Intermediate Certificates causing silent network/update failures.
2. 3rd-party Inspection CAs (Antivirus SSL scanning, Corporate Proxies, Fiddler/mitmproxy).

Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

INSPECTION_KEYWORDS = [
    "personal ca",
    "net-defender",
    "bitdefender",
    "kaspersky",
    "avast",
    "eset",
    "fiddler",
    "charles",
    "mitm",
    "proxy",
    "inspection",
    "zscaler",
    "fortinet",
    "palo alto",
]


@dataclass
class CertificateEntry:
    subject: str
    issuer: str
    thumbprint: str
    not_after_str: str
    is_expired: bool = False
    is_inspection_ca: bool = False
    details: str = ""


@dataclass
class CertAuditReport:
    total_certs: int = 0
    expired_count: int = 0
    inspection_count: int = 0
    flagged_certs: List[CertificateEntry] = field(default_factory=list)
    verdict: str = "Healthy"


def audit_root_certificates() -> CertAuditReport:
    """Scan local machine root and intermediate certificate stores."""
    ps_cmd = r"""
Get-ChildItem Cert:\LocalMachine\Root, Cert:\LocalMachine\CA -ErrorAction SilentlyContinue |
    Select-Object Subject, Issuer, Thumbprint, NotAfter |
    ConvertTo-Json -Depth 2 -Compress
"""
    report = CertAuditReport()

    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            report.verdict = "Certificate store unreadable"
            return report

        certs_raw = json.loads(proc.stdout)
        if isinstance(certs_raw, dict):
            certs_raw = [certs_raw]

        report.total_certs = len(certs_raw)
        now_dt = datetime.now()

        for c in certs_raw:
            subj = c.get("Subject") or ""
            issuer = c.get("Issuer") or ""
            thumb = c.get("Thumbprint") or ""
            not_after_raw = c.get("NotAfter") or ""

            # PowerShell JSON Date format: /Date(1732118400000)/ or ISO string
            not_after_dt = None
            date_m = re.search(r"/Date\((\d+)\)/", not_after_raw)
            if date_m:
                ts = int(date_m.group(1)) / 1000.0
                try:
                    not_after_dt = datetime.fromtimestamp(ts)
                except Exception as e:
                    logger.debug("Failed parsing timestamp for cert %s: %s", thumb, e)
            elif not_after_raw:
                try:
                    not_after_dt = datetime.fromisoformat(not_after_raw.replace("Z", ""))
                except Exception as e:
                    logger.debug("Failed parsing ISO date for cert %s: %s", thumb, e)

            is_exp = bool(not_after_dt and not_after_dt < now_dt)

            subj_lower = subj.lower()
            is_insp = any(kw in subj_lower for kw in INSPECTION_KEYWORDS)

            exp_str = not_after_dt.strftime("%Y-%m-%d") if not_after_dt else not_after_raw

            if is_exp or is_insp:
                if is_exp:
                    report.expired_count += 1
                if is_insp:
                    report.inspection_count += 1

                reasons = []
                if is_exp:
                    reasons.append("Expired")
                if is_insp:
                    reasons.append("TLS Inspection / Custom CA")

                entry = CertificateEntry(
                    subject=subj,
                    issuer=issuer,
                    thumbprint=thumb,
                    not_after_str=exp_str,
                    is_expired=is_exp,
                    is_inspection_ca=is_insp,
                    details=f"{' & '.join(reasons)}: {subj[:80]} (Expires {exp_str})",
                )
                report.flagged_certs.append(entry)

        if report.expired_count > 0 and report.inspection_count > 0:
            report.verdict = f"Attention: {report.expired_count} expired certificates and {report.inspection_count} TLS inspection CAs found."
        elif report.expired_count > 0:
            report.verdict = f"Notice: {report.expired_count} expired certificates present in LocalMachine store."
        elif report.inspection_count > 0:
            report.verdict = f"Info: {report.inspection_count} custom/inspection CAs active (e.g. Antivirus HTTPS scanning)."
        else:
            report.verdict = f"All {report.total_certs} root certificates are valid."

    except Exception as exc:
        report.verdict = f"Error scanning certificates: {exc}"

    return report
