<#
.SYNOPSIS
    Sign the built executables with a fresh local code-signing certificate.

.DESCRIPTION
    Creates a new self-signed code-signing certificate, trusts it for the
    current user (Root + TrustedPublisher), signs dist\WinClientTool-Portable.exe
    and dist\WinClientTool\WinClientTool.exe when present, verifies the result,
    then removes previous WinClientTool certificates from their owning user
    or machine stores. One certificate per build; the old ones are deleted.

    This is a local development signing workflow. A valid locally trusted
    self-signed signature does not meet Smart App Control's requirement for
    a certificate issued by a trusted provider. Check actual launch results;
    do not treat Authenticode Valid as proof that App Control permits the app.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\sign_build_local.ps1
#>
param(
    [string]$Root = (Join-Path $PSScriptRoot ".."),
    [string]$Subject = "CN=WinClientTool Local Code Signing",
    [int]$ValidDays = 365,
    [string]$Signtool = ""
)

$ErrorActionPreference = "Stop"

function Find-Signtool {
    if ($Signtool) { return $Signtool }
    $candidate = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Source }
    $sdk = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($sdk) { return $sdk.FullName }
    throw "signtool.exe not found. Install the Windows SDK, or pass -Signtool <path>."
}

function Get-Targets {
    @(
        (Join-Path $Root "dist\WinClientTool-Portable.exe"),
        (Join-Path $Root "dist\WinClientTool\WinClientTool.exe")
    ) | Where-Object { Test-Path $_ }
}

function Remove-PreviousWctCerts {
    param([string]$KeepThumbprint)
    # Stale certificates in LocalMachine require an elevated run; ordinary
    # fresh builds with only CurrentUser certificates need no machine writes.
    # CurrentUser Root/TrustedPublisher also enumerate inherited machine
    # certificates. Removing those through CurrentUser fails with access
    # denied even in an elevated process. Clean the owning machine store
    # first, and request write access only when stale certificates exist.
    foreach ($scope in @("LocalMachine", "CurrentUser")) {
        foreach ($storeName in @("My", "Root", "TrustedPublisher")) {
            $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
                $storeName, $scope)
            $store.Open("ReadOnly")
            try {
                $stale = @($store.Certificates | Where-Object {
                    $_.Subject -eq $Subject -and $_.Thumbprint -ne $KeepThumbprint
                })
            } finally { $store.Close() }
            if ($stale.Count -eq 0) { continue }
            $store.Open("ReadWrite")
            try {
                foreach ($old in $stale) { $store.Remove($old) }
            } finally { $store.Close() }
        }
    }
}

$targets = Get-Targets
if (-not $targets) {
    Write-Host "No built exe found under dist\. Build first, then sign." -ForegroundColor Yellow
    exit 1
}

$signtool = Find-Signtool

Write-Host "Creating a new local code-signing certificate ($Subject)..."
$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $Subject `
    -CertStoreLocation Cert:\CurrentUser\My `
    -KeyExportPolicy Exportable `
    -KeyUsage DigitalSignature `
    -NotAfter (Get-Date).AddDays($ValidDays)

# Add the public certificate to the user's trust stores directly through .NET.
# Import-Certificate can raise an interactive security prompt and hang a
# non-interactive build; X509Store.Add does not.
foreach ($storeName in @("Root", "TrustedPublisher")) {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
        $storeName, "CurrentUser")
    $store.Open("ReadWrite")
    try { $store.Add($cert) } finally { $store.Close() }
}

Write-Host "Signing $($targets.Count) target(s) with $($cert.Thumbprint)..."
foreach ($target in $targets) {
    & $signtool sign /fd SHA256 /sha1 $cert.Thumbprint "`"$target`"" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "signtool failed for $target" }
    & $signtool verify /pa /v "`"$target`"" 2>&1 |
        Select-String -Pattern "Verifying|Signing Certificate|OK|error" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "signature verification failed for $target" }
}

Remove-PreviousWctCerts -KeepThumbprint $cert.Thumbprint
Write-Host "Signed and verified. Certificate $($cert.Thumbprint) is trusted for this user." -ForegroundColor Green
Write-Warning "Local development signature only: Smart App Control may still require a certificate issued by a trusted provider. Verify application launch separately."
