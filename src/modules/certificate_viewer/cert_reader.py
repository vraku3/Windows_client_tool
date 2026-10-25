from core.system_paths import powershell_exe
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
    key_exportable: Optional[bool] = None   # None = no private key, or mechanism refused


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


def _build_ps_script(store_path: str) -> str:
    """The PowerShell enumeration script for one certificate store path."""
    return f"""
$ErrorActionPreference = 'SilentlyContinue'
$certs = Get-ChildItem -Path '{store_path}' -ErrorAction Stop
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
    $keyExportable = $null
    if ($cert.HasPrivateKey) {{
        try {{
            $cngKey = $null
            $rsaKey = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
            if ($rsaKey -is [System.Security.Cryptography.RSACng]) {{ $cngKey = $rsaKey.Key }}
            if ($cngKey -eq $null) {{
                $ecKey = [System.Security.Cryptography.X509Certificates.ECDsaCertificateExtensions]::GetECDsaPrivateKey($cert)
                if ($ecKey -is [System.Security.Cryptography.ECDsaCng]) {{ $cngKey = $ecKey.Key }}
            }}
            if ($cngKey -ne $null) {{
                $keyExportable = [bool]($cngKey.ExportPolicy -ne [System.Security.Cryptography.CngExportPolicies]::None)
            }} else {{
                # Pre-CNG legacy CAPI key container (e.g. an older PFX import).
                try {{
                    $csp = $cert.PrivateKey.CspKeyContainerInfo
                    if ($csp) {{ $keyExportable = [bool]$csp.Exportable }}
                }} catch {{ }}
            }}
        }} catch {{ }}
    }}
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
        KeyExportable = $keyExportable
        DerBase64     = $derB64
    }}
}}
if ($result.Count -eq 0) {{ Write-Output '[]'; exit 0 }}
$result | ConvertTo-Json -Depth 3 -Compress
"""


def _parse_cert_item(item: dict, store_name: str, store_location: str,
                      today: datetime.datetime) -> CertInfo:
    """Build one CertInfo from a single decoded JSON record."""
    expiry = datetime.datetime.strptime(item["Expiry"], "%Y-%m-%d %H:%M:%S")
    days = (expiry - today).days
    flag = ""
    if days < 0:
        flag = "🔴 Expired"
    elif days <= 30:
        flag = "🟠 Expiring Soon"
    raw_der = base64.b64decode(item.get("DerBase64") or "")
    return CertInfo(
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
        key_exportable=item.get("KeyExportable"),
    )


def fetch_certs(store_name: str, store_location: str = "user") -> List[CertInfo]:
    """Load certificates from a Windows certificate store using PowerShell."""
    store_path = _STORE_PATHS.get(
        (store_name, store_location),
        rf"Cert:\CurrentUser\{store_name}",
    )
    ps = _build_ps_script(store_path)

    # A store we could not read is never an empty one: an admin checking
    # Root or Disallowed must not read "no certificates" off a refusal or a
    # timeout. Failures RAISE; the pane runs this on a Worker and shows them.
    try:
        proc = subprocess.run(
            [powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Certificate enumeration failed for %s (%s): %s",
                       store_name, store_location, e)
        raise RuntimeError(f"could not read the {store_name} store: {e}") from e
    if proc.returncode != 0:
        why = ((proc.stderr or "") + (proc.stdout or "")).strip()[-300:]
        logger.warning("Certificate enumeration failed for %s (%s), exit %s: %s",
                       store_name, store_location, proc.returncode, why)
        raise RuntimeError(f"could not read the {store_name} store "
                           f"(PowerShell exit {proc.returncode}): {why}")
    raw = (proc.stdout or "").strip()
    if not raw or raw == "[]":
        return []
    try:
        data = json.loads(raw)
    except ValueError as e:
        logger.warning("Certificate enumeration for %s returned unreadable output: %s",
                       store_name, e)
        raise RuntimeError(f"could not read the {store_name} store: unreadable output") from e
    if isinstance(data, dict):
        data = [data]

    today = datetime.datetime.utcnow()
    certs: List[CertInfo] = []
    for item in data:
        try:
            certs.append(_parse_cert_item(item, store_name, store_location, today))
        except Exception:
            logger.warning("Ignored Exception reading certificate", exc_info=True)
            continue
    return certs
