"""Exercise the real PowerShell release logic without keys or artifact writes."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "sign_build.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell required")
THUMBPRINT = "A" * 40


def run_ps(body):
    script_path = str(SCRIPT).replace("'", "''")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
         f"$ErrorActionPreference = 'Stop'; . '{script_path}'; {body}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def fake_certificate():
    return f"""
    $cert = [pscustomobject]@{{
        Thumbprint = '{THUMBPRINT}'; Subject = 'CN=Test Publisher, C=RO';
        Issuer = 'CN=Provider CA'; HasPrivateKey = $true;
        NotBefore = (Get-Date).AddDays(-1); NotAfter = (Get-Date).AddDays(30);
        EnhancedKeyUsageList = @([pscustomobject]@{{ObjectId = '1.3.6.1.5.5.7.3.3'}})
    }}
    function Test-TrustedSigningChain {{ param($Certificate) return $true }}
    """


def test_native_sign_arguments_keep_spaces_and_metacharacters_in_one_path():
    output = run_ps(f"""
        @(Get-SignArguments -Thumbprint '{THUMBPRINT}' -TimestampUrl 'https://timestamp.example.test' -Target 'C:\\Build Space\\a & b.exe') | ConvertTo-Json -Compress
    """)
    args = json.loads(output)
    assert args == ["sign", "/sha1", THUMBPRINT, "/s", "My", "/fd", "SHA256",
                    "/td", "SHA256", "/tr", "https://timestamp.example.test",
                    "C:\\Build Space\\a & b.exe"]


@pytest.mark.parametrize("mutation, message", [
    ("$cert.Issuer = $cert.Subject", "self-signed"),
    ("$cert.HasPrivateKey = $false", "private key"),
    ("$cert.NotAfter = (Get-Date).AddDays(-1)", "validity"),
    ("$cert.EnhancedKeyUsageList = @()", "Code Signing"),
    ("$cert.Subject = 'CN=Other'", "publisher"),
    ("function Test-TrustedSigningChain { return $false }", "chain"),
])
def test_refuse_invalid_release_certificate(mutation, message):
    output = run_ps(fake_certificate() + f"""
        {mutation}
        try {{
            Assert-ReleaseCertificate -Certificate $cert -ExpectedSubject 'CN=Test Publisher, C=RO'
            throw 'UNEXPECTED ACCEPT'
        }} catch {{ $_.Exception.Message }}
    """)
    assert message.lower() in output.lower()
    assert "UNEXPECTED ACCEPT" not in output


def test_accept_expected_valid_provider_certificate():
    assert run_ps(fake_certificate() + """
        Assert-ReleaseCertificate -Certificate $cert -ExpectedSubject 'CN=Test Publisher, C=RO'
        'accepted'
    """) == "accepted"


def test_native_invocation_preserves_argument_boundaries():
    executable = sys.executable.replace("'", "''")
    output = run_ps(f"""
        Invoke-CheckedSignTool -Tool '{executable}' -Arguments @(
            '-c', 'import json,sys; print(json.dumps(sys.argv[1:]))',
            '/sha1', '{THUMBPRINT}', "C:\\Build Space\\a & b's.exe")
    """)
    assert json.loads(output) == ["/sha1", THUMBPRINT, "C:\\Build Space\\a & b's.exe"]


def test_native_tool_failure_is_not_success():
    executable = sys.executable.replace("'", "''")
    output = run_ps(f"""
        try {{
            Invoke-CheckedSignTool -Tool '{executable}' -Arguments @('-c', 'raise SystemExit(7)')
            'UNEXPECTED ACCEPT'
        }} catch {{ $_.Exception.Message }}
    """)
    assert 'exit code 7' in output
    assert 'UNEXPECTED ACCEPT' not in output


@pytest.mark.parametrize("status, timestamp, thumbprint, accepted", [
    ("Valid", "$null", THUMBPRINT, False),
    ("NotTrusted", "[pscustomobject]@{Subject='Timestamp CA'}", THUMBPRINT, False),
    ("Valid", "[pscustomobject]@{Subject='Timestamp CA'}", "B" * 40, False),
    ("Valid", "[pscustomobject]@{Subject='Timestamp CA'}", THUMBPRINT, True),
])
def test_verify_signature_requires_timestamp_and_selected_publisher(status, timestamp, thumbprint, accepted):
    output = run_ps(f"""
        function Get-AuthenticodeSignature {{
            [pscustomobject]@{{Status='{status}';
                SignerCertificate=[pscustomobject]@{{Thumbprint='{thumbprint}'}};
                TimeStamperCertificate={timestamp}}}
        }}
        try {{
            Assert-ReleaseSignature -Target 'C:\\fake.exe' -Thumbprint '{THUMBPRINT}' | Out-Null
            'accepted'
        }} catch {{ 'refused' }}
    """)
    assert output == ("accepted" if accepted else "refused")
