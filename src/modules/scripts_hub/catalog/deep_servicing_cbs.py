"""Windows Servicing, CBS/WinSxS & Patch Forensics Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

UPD = maker("Updates & servicing")
TRO = maker("Troubleshooting")

SCRIPTS = [
    UPD(
        "cbs_winsxs_deep_analyzer",
        "WinSxS Component Store Health, Packages & Reclamation Potential",
        "Interrogates the Component-Based Servicing package registry, categorizing installed, superseded, "
        "and staged packages, and calculates component cleanup reclamation potential.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: WinSxS Component Store Health & Reclamation Potential"
Write-Host "PURPOSE   : Evaluates package bloat, superseded update backups, and servicing health"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK WINSXS PRIMER:"
Write-Host "  • WinSxS (Side-by-Side Store): Windows core repository for all OS assemblies and binaries."
Write-Host "  • Superseded Packages: Older update copies preserved so previous KBs can be uninstalled."
Write-Host "  • RECLAMATION: When superseded packages pile up, running DISM Component Cleanup"
Write-Host "    can reclaim 4GB to 15GB+ of disk space on drive C:!"
Write-Host ""

Write-Host "=== Phase 1: Component-Based Servicing (CBS) Package Inventory ==="
$cbsPkgKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\Packages'

if (Test-Path $cbsPkgKey) {
    $pkgs = Get-ChildItem $cbsPkgKey -ErrorAction SilentlyContinue
    $total = $pkgs.Count
    $installed = 0
    $superseded = 0
    $staged = 0
    $pending = 0

    foreach ($p in $pkgs) {
        $state = (Get-ItemProperty $p.PSPath -Name CurrentState -ErrorAction SilentlyContinue).CurrentState
        switch ($state) {
            112 { $installed++ }       # Installed
            128 { $superseded++ }      # Superseded
            32  { $staged++ }          # Staged
            64  { $staged++ }          # Resolved
            default { if ($state -in 80, 96, 144) { $pending++ } }
        }
    }

    Write-Host ("[INFO] Total Packages in Servicing Database: {0}" -f $total)
    Write-Host ("       Installed   : {0}" -f $installed)
    Write-Host ("       Superseded  : {0}" -f $superseded)
    Write-Host ("       Staged      : {0}" -f $staged)
    Write-Host ("       Pending     : {0}" -f $pending)

    if ($superseded -ge 15) {
        Write-Host ("[WARN] {0} superseded packages detected. DISM Component Cleanup recommended to reclaim disk space." -f $superseded)
    } else {
        Write-Host "[OK]   Component Store superseded packages are at normal baseline."
    }
} else {
    Write-Host "[WARN] CBS Packages registry key inaccessible."
}
Write-Host ""

Write-Host "=== Phase 2: Active Servicing Transaction Flags ==="
$rebootPending = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
$rebootProgress = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootInProgress'

if ($rebootPending) {
    Write-Host "[WARN] CBS RebootPending flag is SET! Servicing operations require a system restart."
}
if ($rebootProgress) {
    Write-Host "[WARN] CBS RebootInProgress flag is SET! Servicing transaction active across boot."
}
if (-not $rebootPending -and -not $rebootProgress) {
    Write-Host "[OK]   No active or pending CBS servicing transactions."
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($superseded -ge 15) {
    Write-Host "[INFO] To reclaim disk space from old updates:"
    Write-Host "       1. Open Quick Cleanup or System Health in WinClientTool."
    Write-Host "       2. Run DISM Component Cleanup: Dism.exe /Online /Cleanup-Image /StartComponentCleanup."
} else {
    Write-Host "[OK]   STATUS: WINSXS HEALTHY. Servicing store is clean and within normal limits."
}
""",
    ),
    UPD(
        "windows_update_pipeline_triage",
        "Windows Update Pipeline, Agent State & BITS Queue Diagnostic",
        "Audits Windows Update services (wuauserv, bits, cryptsvc, TrustedInstaller), "
        "inspects active BITS jobs, and enumerates recent update failure error codes.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Windows Update Pipeline, Services & BITS Queue"
Write-Host "PURPOSE   : Resolves 'Windows Update stuck at 0%, hanging, or throwing error codes'"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK UPDATE PIPELINE PRIMER:"
Write-Host "  • Windows Update depends on 4 core services: wuauserv, BITS, CryptSvc, TrustedInstaller."
Write-Host "  • BITS (Background Intelligent Transfer): Downloads patch payloads in background."
Write-Host "  • Stalled BITS jobs or corrupted SoftwareDistribution database cause updates to freeze."
Write-Host ""

Write-Host "=== Phase 1: Core Servicing & Update Services Status ==="
$svcNames = @('wuauserv', 'BITS', 'CryptSvc', 'TrustedInstaller')
$svcProblem = $false
foreach ($sn in $svcNames) {
    $s = Get-Service -Name $sn -ErrorAction SilentlyContinue
    if ($s) {
        $tag = if ($s.StartType -eq 'Disabled') { $svcProblem = $true; '[BAD] ' } else { '[OK]  ' }
        Write-Host ("  {0} Service: {1,-18} Status: {2,-8} Startup: {3} ({4})" -f $tag, $s.Name, $s.Status, $s.StartType, $s.DisplayName)
    } else {
        Write-Host ("  [BAD]  Service: {0,-18} MISSING from service database!" -f $sn)
    }
}
Write-Host ""

Write-Host "=== Phase 2: BITS Download Queue Audit ==="
try {
    $bitsJobs = Get-BitsTransfer -AllUsers -ErrorAction Stop
    if ($bitsJobs) {
        Write-Host ("[INFO] Active BITS Transfer Jobs ({0}):" -f $bitsJobs.Count)
        foreach ($bj in $bitsJobs) {
            $tag = if ($bj.JobState -in ('Error', 'TransientError')) { '[BAD] ' } else { '[INFO]' }
            Write-Host ("  {0} Job: {1,-28} State: {2,-12} Bytes: {3}/{4}" -f $tag, $bj.DisplayName, $bj.JobState, $bj.BytesTransferred, $bj.BytesTotal)
        }
    } else {
        Write-Host "[OK]   Zero queued or stalled BITS transfer jobs."
    }
} catch {
    Write-Host "[INFO] BITS query (Get-BitsTransfer requires elevation for AllUsers): $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 3: Recent Windows Update Installation Failures ==="
$hasFailures = $false
try {
    $wuFailEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Microsoft-Windows-WindowsUpdateClient';
        Id = 20;
        StartTime = (Get-Date).AddDays(-30)
    } -MaxEvents 10 -ErrorAction Stop

    if ($wuFailEvents) {
        $hasFailures = $true
        Write-Host ("[BAD]  Found {0} failed update installation event(s) in last 30 days:" -f $wuFailEvents.Count)
        $wuFailEvents | ForEach-Object {
            Write-Host ("  [{0:yyyy-MM-dd HH:mm}] {1}" -f $_.TimeCreated, ($_.Message -split "`r?`n")[0])
        }
    } else {
        Write-Host "[OK]   Zero Windows Update installation failure events in the last 30 days."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero Windows Update installation failure events in the last 30 days."
    } else {
        Write-Host "[INFO] WU event check note: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($hasFailures -or $svcProblem) {
    Write-Host "[WARN] WINDOWS UPDATE REQUIRES REPAIR!"
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Check Phase 1: Ensure wuauserv and BITS are not set to Disabled."
    Write-Host "       2. Reset Windows Update components via Quick Fix in WinClientTool."
    Write-Host "       3. If specific error code 0x800f* appears, run 'CBS & DISM Servicing Log Corruption Correlator'."
} else {
    Write-Host "[OK]   STATUS: WINDOWS UPDATE PIPELINE HEALTHY. Services and queues operating normally."
}
""",
    ),
    TRO(
        "reboot_pending_graph",
        "Comprehensive Pending Reboot Reason Graph",
        "Deep inspection of all reboot indicators: CBS RebootPending, Windows Update AU, "
        "Session Manager PendingFileRenameOperations, ComputerName mismatch, and SCCM client flags.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Comprehensive Pending Reboot Reason Graph"
Write-Host "PURPOSE   : Detects all system, servicing, and file-lock reboot triggers"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK REBOOT PENDING PRIMER:"
Write-Host "  • When a reboot is pending, Windows Installer (MSI) refuses to run (Error 1618/3010)."
Write-Host "  • Intune / SCCM patch deployments fail with error 0x87D00692."
Write-Host "  • Updated system DLLs and drivers stay locked until Session Manager reboots the kernel."
Write-Host ""

$rebootReasons = @()

Write-Host "=== Phase 1: Component-Based Servicing (CBS) Flags ==="
$cbsReboot = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
$cbsProgress = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootInProgress'
$cbsPackages = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\PackagesPending'

if ($cbsReboot) {
    Write-Host "[WARN] CBS RebootPending flag is ACTIVE (Servicing stack update requires restart)."
    $rebootReasons += "Component-Based Servicing (CBS) Update"
}
if ($cbsProgress) {
    Write-Host "[WARN] CBS RebootInProgress flag is ACTIVE (Mid-servicing transaction)."
    $rebootReasons += "CBS Servicing In-Progress"
}
if ($cbsPackages) {
    Write-Host "[WARN] CBS PackagesPending flag is ACTIVE (Staged packages queued for install)."
    $rebootReasons += "CBS Staged Packages Pending"
}
if (-not $cbsReboot -and -not $cbsProgress -and -not $cbsPackages) {
    Write-Host "[OK]   Zero CBS servicing reboot flags active."
}
Write-Host ""

Write-Host "=== Phase 2: Windows Update (AU) Flags ==="
$wuReq = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
$wuPost = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\PostRebootReporting'

if ($wuReq) {
    Write-Host "[WARN] Windows Update (AU) RebootRequired flag is ACTIVE."
    $rebootReasons += "Windows Update Patch Installation"
}
if ($wuPost) {
    Write-Host "[INFO] Windows Update PostRebootReporting key present."
}
if (-not $wuReq) {
    Write-Host "[OK]   Windows Update does not currently require a restart."
}
Write-Host ""

Write-Host "=== Phase 3: Session Manager PendingFileRenameOperations ==="
$smKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager'
$ops = (Get-ItemProperty $smKey -Name PendingFileRenameOperations -ErrorAction SilentlyContinue).PendingFileRenameOperations
if ($ops) {
    $filteredOps = $ops | Where-Object { $_ -and $_.Trim() }
    $opCount = @($filteredOps).Count
    Write-Host ("[WARN] Found {0} file rename/deletion operation(s) queued for next boot!" -f $opCount)
    $rebootReasons += ("Pending File Rename/Delete Operations ({0} files)" -f $opCount)
    Write-Host "       Sample of queued operations (First 8):"
    $filteredOps | Select-Object -First 8 | ForEach-Object {
        $cleanPath = $_.Trim().Replace('\??\', '')
        Write-Host ("         • {0}" -f $cleanPath)
    }
} else {
    Write-Host "[OK]   Zero pending file rename or deletion operations."
}
Write-Host ""

Write-Host "=== Phase 4: Computer Name & Domain Identity Sync ==="
$actName = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName' -Name ComputerName -ErrorAction SilentlyContinue).ComputerName
$pendName = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName' -Name ComputerName -ErrorAction SilentlyContinue).ComputerName

if ($actName -and $pendName -and $actName -ne $pendName) {
    Write-Host ("[WARN] Computer Rename is pending reboot: Active='{0}' vs Configured='{1}'" -f $actName, $pendName)
    $rebootReasons += "Computer Rename Pending ($actName -> $pendName)"
} else {
    Write-Host ("[OK]   Computer name is fully synchronized: '{0}'" -f $actName)
}
Write-Host ""

Write-Host "=== Phase 5: SCCM / ConfigMgr Client Reboot Flags ==="
$sccmReboot = Test-Path 'HKLM:\SOFTWARE\Microsoft\SMS\Mobile Client\Reboot Selected'
if ($sccmReboot) {
    Write-Host "[WARN] Microsoft Endpoint Configuration Manager (SCCM) has flagged a reboot."
    $rebootReasons += "SCCM / ConfigMgr Client Reboot Flag"
} else {
    Write-Host "[INFO] No SCCM client reboot flags detected."
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($rebootReasons.Count -gt 0) {
    Write-Host ("[WARN] REBOOT IS PENDING ({0} trigger(s) detected):" -f $rebootReasons.Count)
    foreach ($r in $rebootReasons) {
        Write-Host ("       • {0}" -f $r)
    }
    Write-Host ""
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Perform a clean system restart (via Start Menu or shutdown.exe /r /t 0)."
    Write-Host "       2. If software or MSI installers were failing with error 1618 or 3010, re-test after restart."
    Write-Host "       3. If PendingFileRenameOperations persists across multiple reboots, check Phase 3 to find the locked file/driver."
} else {
    Write-Host "[OK]   STATUS: NO REBOOT PENDING. System is in a clean, fully committed servicing state."
}
""",
    ),
    UPD(
        "cbs_dism_corruption_correlator",
        "CBS & DISM Servicing Log Corruption Correlator",
        "Extracts and correlates recent error and failure entries from CBS.log and DISM.log "
        "to pinpoint missing manifests, corrupt payload assemblies, or 0x800f* errors.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: CBS & DISM Servicing Log Corruption Correlator"
Write-Host "PURPOSE   : Pinpoints root causes for failed Windows updates, SFC errors, and 0x800f* codes"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK CBS / DISM ERROR CODE GUIDE:"
Write-Host "  • 0x800F081F : CBS_E_SOURCE_MISSING -- Component store payload files could not be found."
Write-Host "  • 0x800F0906 : CBS_E_DOWNLOAD_FAILURE -- Windows Update source download timed out or failed."
Write-Host "  • 0x80070002 : ERROR_FILE_NOT_FOUND -- Required assembly manifest or file is missing."
Write-Host "  • 0x80070490 : ERROR_NOT_FOUND -- Corrupted servicing metadata in registry."
Write-Host ""

Write-Host "=== Phase 1: CBS.log Recent Servicing Errors ==="
$cbsLog = "$env:SystemRoot\Logs\CBS\CBS.log"
$hasCbsErrors = $false
if (Test-Path $cbsLog) {
    try {
        $cbsErrors = Get-Content $cbsLog -Tail 2000 -ErrorAction SilentlyContinue |
            Where-Object { $_ -match ' ERROR | Failed | 0x800f| 0x8007' } | Select-Object -Last 10

        if ($cbsErrors) {
            $hasCbsErrors = $true
            Write-Host ("[BAD]  Found recent error signatures in CBS.log:")
            $cbsErrors | ForEach-Object { Write-Host ("  [BAD]  {0}" -f $_.Trim()) }
        } else {
            Write-Host "[OK]   Zero error signatures found in recent CBS.log tail."
        }
    } catch {
        Write-Host "[INFO] CBS log read note: $($_.Exception.Message)"
    }
} else {
    Write-Host "[INFO] CBS.log does not exist."
}
Write-Host ""

Write-Host "=== Phase 2: DISM.log Servicing Failures ==="
$dismLog = "$env:SystemRoot\Logs\DISM\dism.log"
$hasDismErrors = $false
if (Test-Path $dismLog) {
    try {
        $dismErrors = Get-Content $dismLog -Tail 1000 -ErrorAction SilentlyContinue |
            Where-Object { $_ -match '\[.*\]\s+Error\s+' } | Select-Object -Last 5

        if ($dismErrors) {
            $hasDismErrors = $true
            Write-Host ("[BAD]  Found recent error signatures in DISM.log:")
            $dismErrors | ForEach-Object { Write-Host ("  [BAD]  {0}" -f $_.Trim()) }
        } else {
            Write-Host "[OK]   Zero errors found in recent DISM.log tail."
        }
    } catch {
        Write-Host "[INFO] DISM log read note: $($_.Exception.Message)"
    }
} else {
    Write-Host "[INFO] DISM.log does not exist."
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($hasCbsErrors -or $hasDismErrors) {
    Write-Host "[BAD]  SERVICING STORE CORRUPTION DETECTED!"
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Run System File Checker: sfc /scannow"
    Write-Host "       2. Repair Component Store: Dism.exe /Online /Cleanup-Image /RestoreHealth"
    Write-Host "       3. If 0x800f081f persists: Provide clean install.wim as repair source: /Source:WIM:D:\sources\install.wim:1 /LimitAccess"
} else {
    Write-Host "[OK]   STATUS: SERVICING LOGS CLEAN. No recent CBS or DISM operation failures found."
}
""",
    ),
    UPD(
        "dism_feature_state_matrix",
        "Windows Optional Features Hardening & Deprecated Components",
        "Inventories Windows Optional Features, identifying deprecated or risky legacy components "
        "(SMB1, Telnet, TFTP, VBScript, Internet Explorer engine) that increase attack surface.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Windows Optional Features Attack Surface Matrix"
Write-Host "PURPOSE   : Audits legacy insecure components (SMB1, Telnet, TFTP) vs modern features"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK FEATURE HARDENING PRIMER:"
Write-Host "  • SMB1: Archaic 1980s protocol vulnerable to EternalBlue / WannaCry exploits. MUST BE DISABLED!"
Write-Host "  • Telnet / TFTP: Transmit data and credentials in plain unencrypted text over LAN."
Write-Host "  • Internet Explorer / VBScript: Obsolete legacy engines frequently targeted by web exploit kits."
Write-Host ""

Write-Host "=== Phase 1: High-Risk Legacy Optional Features ==="
$riskyFeatures = @(
    'SMB1Protocol',
    'TelnetClient',
    'TFTP',
    'Internet-Explorer-Optional-amd64',
    'DirectPlay',
    'VBScript'
)

$riskyEnabledCount = 0
try {
    $feats = Get-WindowsOptionalFeature -Online -ErrorAction Stop | Where-Object { $_.FeatureName -in $riskyFeatures }
    foreach ($f in $feats) {
        $st = $f.State
        $tag = if ($st -eq 'Enabled') { $riskyEnabledCount++; "[BAD] " } else { "[OK]  " }
        Write-Host ("  {0} Feature: {1,-35} State: {2}" -f $tag, $f.FeatureName, $st)
    }
} catch {
    Write-Host "[INFO] Get-WindowsOptionalFeature query (requires elevation for full list): $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($riskyEnabledCount -gt 0) {
    Write-Host ("[BAD]  ATTENTION: {0} legacy high-risk optional feature(s) are ENABLED!" -f $riskyEnabledCount)
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Disable unused legacy protocols via 'Turn Windows features on or off' (optionalfeatures.exe)."
    Write-Host "       2. Specifically ensure SMB1 is disabled across all enterprise client workstations."
} else {
    Write-Host "[OK]   STATUS: SECURE. All high-risk legacy Windows features are disabled."
}
""",
    ),
    TRO(
        "system_restore_shadow_integrity",
        "System Restore Points & Shadow Storage Health Diagnostic",
        "Inventories all existing System Restore points, sequence numbers, event types, "
        "and checks whether System Restore protection is enabled on system drives.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: System Restore Points & Shadow Storage Integrity"
Write-Host "PURPOSE   : Verifies backup safety net before performing system modifications"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK SYSTEM RESTORE PRIMER:"
Write-Host "  • System Restore is Windows' primary rollback mechanism for drivers, registry, and system files."
Write-Host "  • It relies on Volume Shadow Copy (VSS). If VSS storage is 100% full or VSS service is disabled,"
Write-Host "    Windows silently deletes older restore points or fails to create new ones!"
Write-Host ""

Write-Host "=== Phase 1: System Protection Configuration (Registry) ==="
$srKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore'
try {
    $sr = Get-ItemProperty -Path $srKey -ErrorAction SilentlyContinue
    $disabled = if ($sr.DisableSR -eq 1) { 'DISABLED (System Restore is turned off)' } else { 'ENABLED (System Protection is active)' }
    Write-Host ("System Protection Status : {0}" -f $disabled)
    if ($sr.RPSessionInterval) {
        Write-Host ("Restore Point Interval   : {0} minutes" -f $sr.RPSessionInterval)
    }
    if ($sr.DisableSR -eq 1) {
        Write-Host "[WARN] System Protection is globally disabled in registry!"
    } else {
        Write-Host "[OK]   System Protection is not globally disabled."
    }
} catch {
    Write-Host "[INFO] SystemRestore registry note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: System Restore Points Inventory ==="
$rpCount = 0
try {
    $rps = Get-ComputerRestorePoint -ErrorAction Stop
    if ($rps) {
        $rpCount = $rps.Count
        Write-Host ("[INFO] Discovered {0} System Restore Point(s) on machine:" -f $rps.Count)
        foreach ($rp in $rps) {
            $time = [datetime]::ParseExact($rp.CreationTime.Substring(0, 14), 'yyyyMMddHHmmss', $null)
            $typeDesc = switch ($rp.RestorePointType) {
                0 { 'APPLICATION_INSTALL' }
                1 { 'APPLICATION_UNINSTALL' }
                10 { 'DEVICE_DRIVER_INSTALL' }
                12 { 'MODIFY_SETTINGS' }
                13 { 'CANCELLED_OPERATION' }
                default { "TYPE_$($rp.RestorePointType)" }
            }
            Write-Host ("  [{0:yyyy-MM-dd HH:mm}] Seq: {1,-4} Type: {2,-22} Desc: {3}" -f $time, $rp.SequenceNumber, $typeDesc, $rp.Description)
        }
    } else {
        Write-Host "[WARN] Zero System Restore points exist on this computer!"
    }
} catch {
    if ($_.Exception.Message -match 'Access is denied') {
        Write-Host "[INFO] Get-ComputerRestorePoint requires elevation. Run elevated for complete list."
    } else {
        Write-Host "[INFO] Restore point query note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 3: Volume Shadow Copy (VSS) Storage Allocation ==="
try {
    $vssStorages = Get-CimInstance -Namespace root\cimv2 -ClassName Win32_ShadowStorage -ErrorAction Stop
    if ($vssStorages) {
        Write-Host ("[INFO] Found {0} Shadow Storage association(s):" -f $vssStorages.Count)
        foreach ($st in $vssStorages) {
            $usedMB = [math]::Round($st.UsedSpace / 1MB, 1)
            $allocMB = [math]::Round($st.AllocatedSpace / 1MB, 1)
            $maxMB = if ($st.MaxSpace -gt 0) { [math]::Round($st.MaxSpace / 1MB, 1) } else { 'UNLIMITED' }
            Write-Host ("  Volume: {0,-12} Used: {1,8} MB | Allocated: {2,8} MB | Max Limit: {3,8} MB" -f $st.Volume, $usedMB, $allocMB, $maxMB)
        }
    } else {
        Write-Host "[INFO] No shadow storage associations returned by Win32_ShadowStorage."
    }
} catch {
    Write-Host "[INFO] Shadow storage WMI query note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 4: Volume Shadow Copy (VSS) Core Services Health ==="
try {
    $vssSvc = Get-Service -Name 'VSS' -ErrorAction SilentlyContinue
    $swprvSvc = Get-Service -Name 'swprv' -ErrorAction SilentlyContinue
    if ($vssSvc) {
        $vssStatus = if ($vssSvc.StartType -eq 'Disabled') { '[BAD]  DISABLED' } else { "[OK]   $($vssSvc.StartType)" }
        Write-Host ("VSS Service (Volume Shadow Copy)     : Status={0}, Startup={1}" -f $vssSvc.Status, $vssStatus)
    }
    if ($swprvSvc) {
        $swpStatus = if ($swprvSvc.StartType -eq 'Disabled') { '[BAD]  DISABLED' } else { "[OK]   $($swprvSvc.StartType)" }
        Write-Host ("SWPRV Service (Software Shadow Copy) : Status={0}, Startup={1}" -f $swprvSvc.Status, $swpStatus)
    }
} catch {
    Write-Host "[INFO] VSS service check note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($rpCount -eq 0) {
    Write-Host "[WARN] NO RESTORE POINTS FOUND: The system currently lacks a rollback safety net."
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Open System Properties -> System Protection tab (SystemPropertiesProtection.exe)."
    Write-Host "       2. Ensure Protection is turned ON for drive C:."
    Write-Host "       3. Click 'Create...' to generate an immediate baseline restore point before applying fixes."
} else {
    Write-Host ("[OK]   STATUS: RESTORE PROTECTION ACTIVE. {0} restore point(s) available for recovery." -f $rpCount)
}
""",
    ),
]
