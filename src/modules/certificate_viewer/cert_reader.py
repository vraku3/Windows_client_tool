import base64
import datetime
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CertInfo:
    subject_cn: str
    subject_full: str
    issuer: str
    expiry: datetime.datetime
    thumbprint: str        # hex string
    key_usage: str
    has_private_key: bool
    raw_der: bytes         # raw certificate bytes for export
    days_until_expiry: int
    flag: str              # "" | "🔴 Expired" | "🟠 Expiring Soon"
    not_before: Optional[datetime.datetime] = None
    sig_algorithm: str = ""    # e.g. "sha256RSA"; "" = not read
    key_algorithm: str = ""    # e.g. "RSA", "ECC"
    key_size: int = 0          # 0 = not read
    is_ca: Optional[bool] = None
    self_signed: bool = False
    store_name: str = ""
    store_location: str = ""


_STORE_PATHS = {
    ("MY",               "user"):    r"Cert:\CurrentUser\My",
    ("ROOT",             "user"):    r"Cert:\CurrentUser\Root",
    ("CA",               "user"):   r"Cert:\CurrentUser\CA",
    ("TrustedPublisher", "user"):   r"Cert:\CurrentUser\TrustedPublisher",
    ("Disallowed",       "machine"): r"Cert:\LocalMachine\Disallowed",
    ("MY",               "machine"): r"Cert:\LocalMachine\My",
    ("ROOT",             "machine"): r"Cert:\LocalMachine\Root",
    ("CA",               "machine"): r"Cert:\LocalMachine\CA",
    ("TrustedPublisher", "machine"): r"Cert:\LocalMachine\TrustedPublisher",
}


def _parse_time(text) -> Optional[datetime.datetime]:
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.warning("Unparseable certificate date %r", text)
        return None


def fetch_certs(store_name: str, store_location: str = "user") -> List[CertInfo]:
    """Load certificates from a Windows certificate store using PowerShell."""
    store_path = _STORE_PATHS.get(
        (store_name, store_location),
        rf"Cert:\CurrentUser\{store_name}",
    )

    ps = f"""
$ErrorActionPreference = 'SilentlyContinue'
$certs = Get-ChildItem -Path '{store_path}'
if (-not $certs) {{ Write-Output '[]'; exit 0 }}
$result = @()
foreach ($cert in $certs) {{
    $cnMatch = [regex]::Match($cert.Subject, 'CN=([^,]+)')
    $cn = if ($cnMatch.Success) {{ $cnMatch.Groups[1].Value.Trim() }} else {{ $cert.Subject }}
    $issuerMatch = [regex]::Match($cert.Issuer, 'CN=([^,]+)')
    $issuer = if ($issuerMatch.Success) {{ $issuerMatch.Groups[1].Value.Trim() }} else {{ $cert.Issuer }}
    $keyUsage = 'N/A'
    $ext = $cert.Extensions | Where-Object {{ $_.Oid.FriendlyName -eq 'Key Usage' }}
    if ($ext) {{ $keyUsage = $ext.Format($false) }}
    $keySize = 0
    try {{ $keySize = [int]$cert.PublicKey.Key.KeySize }} catch {{ $keySize = 0 }}
    $isCa = $null
    $bc = $cert.Extensions | Where-Object {{ $_ -is [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension] }}
    if ($bc) {{ $isCa = [bool]$bc.CertificateAuthority }}
    try {{
        $derB64 = [Convert]::ToBase64String(
            $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
        )
    }} catch {{ $derB64 = '' }}
    $result += [PSCustomObject]@{{
        SubjectCN     = $cn
        SubjectFull   = $cert.Subject
        Issuer        = $issuer
        IssuerFull    = $cert.Issuer
        Expiry        = $cert.NotAfter.ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')
        Thumbprint    = $cert.Thumbprint
        KeyUsage      = $keyUsage
        HasPrivateKey = [bool]$cert.HasPrivateKey
        NotBefore     = $cert.NotBefore.ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')
        SigAlg        = $cert.SignatureAlgorithm.FriendlyName
        KeyAlg        = $cert.PublicKey.Oid.FriendlyName
        KeySize       = $keySize
        IsCA          = $isCa
        DerBase64     = $derB64
    }}
}}
if ($result.Count -eq 0) {{ Write-Output '[]'; exit 0 }}
$result | ConvertTo-Json -Depth 3 -Compress
"""

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=60,
        )
        raw = proc.stdout.strip()
        if not raw or raw == "[]":
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
    except Exception as e:
        # `store` never existed — the parameter is `store_name`. The handler
        # meant to report a failed enumeration raised NameError instead, and
        # since the pane runs this on a Worker, what reached the user was
        # "name 'store' is not defined" with the real reason discarded.
        logger.warning("Certificate enumeration failed for %s (%s): %s",
                       store_name, store_location, e)
        return []

    today = datetime.datetime.utcnow()
    certs: List[CertInfo] = []
    for item in data:
        try:
            expiry = datetime.datetime.strptime(item["Expiry"], "%Y-%m-%d %H:%M:%S")
            days = (expiry - today).days
            flag = ""
            if days < 0:
                flag = "🔴 Expired"
            elif days <= 30:
                flag = "🟠 Expiring Soon"
            raw_der = base64.b64decode(item.get("DerBase64") or "")
            certs.append(CertInfo(
                subject_cn=item.get("SubjectCN") or "",
                subject_full=item.get("SubjectFull") or "",
                issuer=item.get("Issuer") or "",
                expiry=expiry,
                thumbprint=item.get("Thumbprint") or "",
                key_usage=item.get("KeyUsage") or "N/A",
                has_private_key=bool(item.get("HasPrivateKey", False)),
                raw_der=raw_der,
                days_until_expiry=days,
                flag=flag,
                not_before=_parse_time(item.get("NotBefore")),
                sig_algorithm=item.get("SigAlg") or "",
                key_algorithm=item.get("KeyAlg") or "",
                key_size=int(item.get("KeySize") or 0),
                is_ca=item.get("IsCA"),
                self_signed=(item.get("SubjectFull") or "") == (item.get("IssuerFull") or "<none>"),
                store_name=store_name,
                store_location=store_location,
            ))
        except Exception:
            logger.warning("Ignored Exception reading certificate", exc_info=True)
            continue
    return certs
