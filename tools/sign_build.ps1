<#
.SYNOPSIS
    Sign both release executables using a provider certificate in CurrentUser\My.
.DESCRIPTION
    Requires a provider-issued Code Signing certificate and its cloud/token
    middleware. No private keys, passwords or trust-store changes are managed here.
    Signing does not override SmartScreen reputation or enterprise app policy.
    See docs/release-signing.md. Dot-sourcing only loads the testable functions.
.EXAMPLE
    powershell -NoProfile -File tools\sign_build.ps1 -Profile tools\signing.local.json
#>
[CmdletBinding()]
param(
    [string]$Profile = '',
    [string]$Signtool = '',
    [string]$Root = '',
    [switch]$CheckOnly
)

function Find-Signtool {
    param([string]$ExplicitPath)
    if ($ExplicitPath) {
        return (Get-Item -LiteralPath $ExplicitPath -ErrorAction Stop).FullName
    }
    $candidate = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Source }
    $sdk = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe' -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($sdk) { return $sdk.FullName }
    throw 'signtool.exe not found. Install Windows SDK signing tools or pass -Signtool.'
}

function Test-TrustedSigningChain {
    param($Certificate)
    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    try {
        $chain.ChainPolicy.RevocationMode = 'Online'
        $chain.ChainPolicy.RevocationFlag = 'ExcludeRoot'
        $chain.ChainPolicy.UrlRetrievalTimeout = [TimeSpan]::FromSeconds(20)
        return $chain.Build($Certificate)
    } finally { $chain.Dispose() }
}

function Assert-ReleaseCertificate {
    param($Certificate, [string]$ExpectedSubject)
    if (-not $Certificate) { throw 'Release certificate was not found in CurrentUser\My.' }
    if (-not $ExpectedSubject -or $Certificate.Subject -cne $ExpectedSubject) {
        throw 'Certificate publisher does not match expectedSubject in the release profile.'
    }
    if ($Certificate.Subject -eq $Certificate.Issuer) {
        throw 'A self-signed certificate cannot be used for a public release.'
    }
    if (-not $Certificate.HasPrivateKey) {
        throw 'Certificate private key is unavailable. Connect/login to the provider middleware.'
    }
    $now = Get-Date
    if ($Certificate.NotBefore -gt $now -or $Certificate.NotAfter -le $now) {
        throw 'Certificate is outside its validity period.'
    }
    $codeSigning = @($Certificate.EnhancedKeyUsageList | Where-Object {
        $_.ObjectId -eq '1.3.6.1.5.5.7.3.3'
    })
    if (-not $codeSigning.Count) { throw 'Certificate must explicitly allow Code Signing.' }
    if (-not (Test-TrustedSigningChain -Certificate $Certificate)) {
        throw 'Certificate chain validation failed (trust, revocation or network availability).'
    }
}

function Get-SignArguments {
    param([string]$Thumbprint, [string]$TimestampUrl, [string]$Target)
    # Each array entry is one native argument; never concatenate or add shell quotes.
    @('sign', '/sha1', $Thumbprint, '/s', 'My', '/fd', 'SHA256',
      '/td', 'SHA256', '/tr', $TimestampUrl, $Target)
}

function Invoke-CheckedSignTool {
    param([string]$Tool, [string[]]$Arguments)
    & $Tool @Arguments 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "SignTool failed with exit code $LASTEXITCODE." }
}

function Assert-ReleaseSignature {
    param([string]$Target, [string]$Thumbprint)
    $signature = Get-AuthenticodeSignature -LiteralPath $Target -ErrorAction Stop
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
        $signature.SignerCertificate.Thumbprint -ne $Thumbprint -or
        -not $signature.TimeStamperCertificate) {
        throw "Release signature or timestamp verification failed: $Target"
    }
    return $signature
}

function Invoke-ReleaseSigning {
    param([string]$ProfilePath, [string]$ToolPath, [string]$ProjectRoot, [switch]$PreflightOnly)
    $ErrorActionPreference = 'Stop'
    if (-not (Test-Path -LiteralPath $ProfilePath -PathType Leaf)) {
        throw 'Signing profile missing. Copy tools/signing.example.json to tools/signing.local.json after provider enrollment. No files were signed.'
    }
    $settings = Get-Content -LiteralPath $ProfilePath -Raw | ConvertFrom-Json
    $thumbprint = ([string]$settings.thumbprint -replace '\s', '').ToUpperInvariant()
    if ($thumbprint -notmatch '^[A-F0-9]{40}$' -or -not $settings.expectedSubject) {
        throw 'Configure the issued certificate SHA-1 thumbprint and exact expectedSubject in the local profile.'
    }
    $timestamp = $null
    if (-not [Uri]::TryCreate([string]$settings.timestampUrl, [UriKind]::Absolute, [ref]$timestamp) -or
        $timestamp.Scheme -notin @('http', 'https')) {
        throw 'Configure an HTTP(S) RFC3161 timestampUrl.'
    }
    $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$thumbprint" -ErrorAction Stop
    Assert-ReleaseCertificate -Certificate $certificate -ExpectedSubject $settings.expectedSubject
    $tool = Find-Signtool -ExplicitPath $ToolPath
    $targets = @(
        (Join-Path $ProjectRoot 'dist\WinClientTool\WinClientTool.exe'),
        (Join-Path $ProjectRoot 'dist\WinClientTool-Portable.exe')
    )
    foreach ($target in $targets) {
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "Build both release formats before signing. Missing: $target"
        }
    }
    if ($PreflightOnly) {
        Write-Host 'Preflight passed. No signing or deployment performed; provider key access is tested only during signing.'
        return
    }
    $manifestPath = Join-Path $ProjectRoot 'dist\signing-manifest.json'
    if (Test-Path -LiteralPath $manifestPath) { Remove-Item -LiteralPath $manifestPath }
    $entries = @()
    foreach ($target in $targets) {
        Write-Host "Signing $target"
        $signArguments = @(Get-SignArguments -Thumbprint $thumbprint -TimestampUrl $timestamp.AbsoluteUri -Target $target)
        Invoke-CheckedSignTool -Tool $tool -Arguments $signArguments
        # /tw makes a missing timestamp a warning; all nonzero exit codes are refused.
        Invoke-CheckedSignTool -Tool $tool -Arguments @('verify', '/pa', '/all', '/tw', '/v', $target)
        $signature = Assert-ReleaseSignature -Target $target -Thumbprint $thumbprint
        $entries += [ordered]@{
            path = $target
            sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            publisher = $certificate.Subject
            thumbprint = $thumbprint
            timestampPublisher = $signature.TimeStamperCertificate.Subject
        }
    }
    [ordered]@{
        schemaVersion = 1
        verifiedUtc = [DateTime]::UtcNow.ToString('o')
        artifacts = $entries
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host "Both release signatures verified. Manifest: $manifestPath"
    Write-Host 'No deployment performed. Enterprise policy acceptance still needs a real target-PC test.'
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $Profile) { $Profile = Join-Path $PSScriptRoot 'signing.local.json' }
    if (-not $Root) { $Root = Join-Path $PSScriptRoot '..' }
    Invoke-ReleaseSigning -ProfilePath $Profile -ToolPath $Signtool -ProjectRoot $Root -PreflightOnly:$CheckOnly
}
