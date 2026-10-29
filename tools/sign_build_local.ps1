<#
.SYNOPSIS
    Sign the built executables with a fresh local code-signing certificate.

.DESCRIPTION
    Creates a new self-signed code-signing certificate, trusts it for the
    current user (Root + TrustedPublisher), signs dist\WinClientTool-Portable.exe
    and dist\WinClientTool\WinClientTool.exe when present, verifies the result,
    then removes the previous WinClientTool code-signing certificate from the
    CurrentUser stores. One certificate per build; the old one is deleted.

    This exists because Smart App Control blocks each new unsigned onefile
    build by hash. A locally trusted signer can satisfy the policy without
    turning Smart App Control off.

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
    $stores = @("Cert:\CurrentUser\My", "Cert:\CurrentUser\Root",
                "Cert:\CurrentUser\TrustedPublisher")
    foreach ($store in $stores) {
        Get-ChildItem $store -ErrorAction SilentlyContinue |
            Where-Object { $_.Subject -eq $Subject -and $_.Thumbprint -ne $KeepThumbprint } |
            ForEach-Object {
                Remove-Item -LiteralPath (Join-Path $store $_.Thumbprint) -Force -ErrorAction SilentlyContinue
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

$cer = Join-Path $env:TEMP ("wct-codesign-" + $cert.Thumbprint + ".cer")
Export-Certificate -Cert $cert -FilePath $cer -Force | Out-Null
Import-Certificate -FilePath $cer -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
Import-Certificate -FilePath $cer -CertStoreLocation Cert:\CurrentUser\TrustedPublisher | Out-Null
Remove-Item -LiteralPath $cer -Force -ErrorAction SilentlyContinue

Write-Host "Signing $($targets.Count) target(s) with $($cert.Thumbprint)..."
foreach ($target in $targets) {
    $signed = $false
    & $signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /sha1 $cert.Thumbprint "`"$target`"" 2>&1 | Out-Host
    if ($LASTEXITCODE -eq 0) {
        $signed = $true
    } else {
        Write-Host "Timestamp server failed; signing without a timestamp." -ForegroundColor Yellow
        & $signtool sign /fd SHA256 /sha1 $cert.Thumbprint "`"$target`"" 2>&1 | Out-Host
        if ($LASTEXITCODE -eq 0) { $signed = $true }
    }
    if (-not $signed) { throw "signtool failed for $target" }
    & $signtool verify /pa /v "`"$target`"" 2>&1 |
        Select-String -Pattern "Verifying|Signing Certificate|OK|error" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "signature verification failed for $target" }
}

Remove-PreviousWctCerts -KeepThumbprint $cert.Thumbprint
Write-Host "Signed and verified. Certificate $($cert.Thumbprint) is trusted for this user." -ForegroundColor Green
