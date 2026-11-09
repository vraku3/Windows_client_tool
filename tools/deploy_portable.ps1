# Deploy WinClientTool-Portable.exe and rotate the two-version share
$ErrorActionPreference = 'Stop'

$appDir = 'C:\Users\iorda\OneDrive\1 Personal\Aplicații'
$dest = Join-Path $appDir 'WinClientTool-Portable.exe'
$share = Join-Path $appDir 'Windows Client Tool Share'
$shareOld = Join-Path $share 'old'

if (-not (Test-Path $shareOld)) {
    New-Item -ItemType Directory -Path $shareOld -Force | Out-Null
}

# 1. Rotate the share copy to old/ if it exists
$currentShare = Join-Path $share 'WinClientTool-Portable.exe'
if (Test-Path $currentShare) {
    Copy-Item -Path $currentShare -Destination (Join-Path $shareOld 'WinClientTool-Portable.exe') -Force
}

# 2. Deploy dist build to Aplicații root
Copy-Item -Path 'dist\WinClientTool-Portable.exe' -Destination $dest -Force

# 3. Deploy dist build to share root
Copy-Item -Path 'dist\WinClientTool-Portable.exe' -Destination $currentShare -Force

# 4. Clean up any stale files in old
Get-ChildItem -Path $shareOld -Filter '*.exe_old' -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem -Path $shareOld -Filter '*-new.exe' -ErrorAction SilentlyContinue | Remove-Item -Force

Write-Host "Deployed to $dest"
Write-Host "Rotated share: $currentShare and old copy in $shareOld"

