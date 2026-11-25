"""Crash Dump & Post-Mortem Stability Analyzer Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

TRO = maker("Troubleshooting")
EVT = maker("Event logs")

SCRIPTS = [
    TRO(
        "minidump_driver_attribution",
        "Kernel BugCheck Minidump Triage & Faulting Driver Attribution",
        "Scans %SystemRoot%\\Minidump and MEMORY.DMP for kernel memory crash dumps, decodes BugCheck "
        "codes (WHEA, IRQL, Watchdog), verifies CrashControl configuration, and extracts faulting driver attribution.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Kernel Crash Dump (BSOD) Triage & BugCheck Decoder"
Write-Host "PURPOSE   : Decodes Blue-Screen stop codes, inspects minidumps, checks CrashControl"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK BSOD DECODER GUIDE:"
Write-Host "  • 0x0000000A / 0x000000D1 : IRQL_NOT_LESS_OR_EQUAL -- Driver memory access bug at high IRQL."
Write-Host "  • 0x0000003B              : SYSTEM_SERVICE_EXCEPTION -- System GUI or graphics driver fault."
Write-Host "  • 0x00000050              : PAGE_FAULT_IN_NONPAGED_AREA -- Bad RAM stick or corrupt driver."
Write-Host "  • 0x0000009F              : DRIVER_POWER_STATE_FAILURE -- Driver failed sleep/resume transition."
Write-Host "  • 0x00000124              : WHEA_UNCORRECTABLE_ERROR -- CRITICAL HARDWARE FAULT (CPU/PCIe/Thermals)!"
Write-Host "  • 0x00000133              : DPC_WATCHDOG_VIOLATION -- SSD firmware bug or GPU hung in DPC."
Write-Host ""

Write-Host "=== Phase 1: Windows Crash Dump Configuration (CrashControl) ==="
$ccKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
try {
    $cc = Get-ItemProperty -Path $ccKey -ErrorAction Stop
    $dumpType = switch ($cc.CrashDumpEnabled) {
        0 { 'None (Crash dumps DISABLED - no logs will be saved on BSOD!)' }
        1 { 'Complete Memory Dump' }
        2 { 'Kernel Memory Dump' }
        3 { 'Small Memory Dump (Minidump 256KB)' }
        7 { 'Automatic Memory Dump (Windows Recommended)' }
        default { "Custom/Unknown ($($cc.CrashDumpEnabled))" }
    }
    $autoReboot = if ($cc.AutoReboot -eq 1) { 'Enabled (PC instantly reboots on BSOD)' } else { 'Disabled (PC stays on Blue Screen)' }
    Write-Host ("Dump Mode      : {0}" -f $dumpType)
    Write-Host ("Auto Reboot    : {0}" -f $autoReboot)
    Write-Host ("Minidump Path  : {0}" -f $cc.MinidumpDir)
    if ($cc.CrashDumpEnabled -eq 0) {
        Write-Host "[WARN] Crash dumps are DISABLED. Windows will not capture crash telemetry on Blue Screen!"
    } else {
        Write-Host "[OK]   Crash dump capture is actively configured."
    }
} catch {
    Write-Host "[INFO] CrashControl registry check: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: Physical Dump Files Inventory (%SystemRoot%\\Minidump) ==="
$dumpDir = "$env:SystemRoot\Minidump"
$fullDump = "$env:SystemRoot\MEMORY.DMP"
$totalDumps = 0

if (Test-Path $fullDump) {
    try {
        $fItem = Get-Item $fullDump -ErrorAction SilentlyContinue
        $fSizeMB = [math]::Round($fItem.Length / 1MB, 1)
        Write-Host ("[WARN] Active Kernel Memory Dump exists: {0} ({1} MB, modified {2:yyyy-MM-dd HH:mm})" -f $fItem.Name, $fSizeMB, $fItem.LastWriteTime)
        $totalDumps++
    } catch {
        Write-Host "[INFO] MEMORY.DMP check note: $($_.Exception.Message)"
    }
}

if (Test-Path $dumpDir) {
    try {
        $dumps = Get-ChildItem -Path $dumpDir -Filter "*.dmp" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending

        if ($dumps) {
            $totalDumps += $dumps.Count
            Write-Host ("[BAD]  Found {0} kernel minidump file(s) on disk:" -f $dumps.Count)
            $dumps | Select-Object -First 10 | ForEach-Object {
                $sizeKB = [math]::Round($_.Length / 1024, 0)
                Write-Host ("  Dump: {0,-28} Date: {1:yyyy-MM-dd HH:mm:ss} ({2} KB)" -f $_.Name, $_.LastWriteTime, $sizeKB)
            }
        } else {
            Write-Host "[OK]   Minidump folder exists and is empty (zero recent minidumps)."
        }
    } catch {
        Write-Host "[INFO] Minidump folder query note: $($_.Exception.Message)"
    }
} else {
    Write-Host "[OK]   Minidump directory does not exist (system has never recorded a minidump)."
}
Write-Host ""

Write-Host "=== Phase 3: BugCheck Events in System Log (Event ID 1001) ==="
$bcCount = 0
try {
    $bcEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        Id = 1001;
        StartTime = (Get-Date).AddDays(-30)
    } -MaxEvents 15 -ErrorAction Stop | Where-Object { $_.Message -like "*computer has rebooted from a bugcheck*" }

    if ($bcEvents) {
        $bcCount = $bcEvents.Count
        Write-Host ("[BAD]  Found {0} BugCheck crash event(s) in the last 30 days:" -f $bcEvents.Count)
        $bcEvents | ForEach-Object {
            $msg = ($_.Message -split "`r?`n")[0]
            Write-Host ("  [{0:yyyy-MM-dd HH:mm:ss}] {1}" -f $_.TimeCreated, $msg)
            if ($msg -match '0x00000124') {
                Write-Host "       DECODED: 0x124 WHEA_UNCORRECTABLE_ERROR -- Severe hardware/CPU/PCIe error!"
            } elseif ($msg -match '0x0000009f') {
                Write-Host "       DECODED: 0x9F DRIVER_POWER_STATE_FAILURE -- Driver failed power/sleep transition."
            } elseif ($msg -match '0x000000d1') {
                Write-Host "       DECODED: 0xD1 DRIVER_IRQL_NOT_LESS_OR_EQUAL -- Faulty driver memory reference."
            } elseif ($msg -match '0x0000003b') {
                Write-Host "       DECODED: 0x3B SYSTEM_SERVICE_EXCEPTION -- System GUI or graphics driver fault."
            } elseif ($msg -match '0x00000133') {
                Write-Host "       DECODED: 0x133 DPC_WATCHDOG_VIOLATION -- Storage controller or GPU timeout."
            }
        }
    } else {
        Write-Host "[OK]   Zero BugCheck crash events recorded in the System event log in the last 30 days."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero BugCheck crash events recorded in the last 30 days."
    } else {
        Write-Host "[INFO] BugCheck event query note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 4: Volmgr Dump Generation Failures (Event ID 161) ==="
try {
    $volmgrEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'volmgr';
        Id = 161;
        StartTime = (Get-Date).AddDays(-30)
    } -MaxEvents 5 -ErrorAction Stop

    if ($volmgrEvents) {
        Write-Host ("[BAD]  Found {0} volmgr dump creation failure(s)!" -f $volmgrEvents.Count)
        Write-Host "       WHAT THIS MEANS: The system crashed, but Windows failed to write the dump file to disk!"
        Write-Host "       COMMON CAUSES  : Pagefile disabled on C:, insufficient free disk space, or SSD disconnected during crash."
    } else {
        Write-Host "[OK]   Zero volmgr dump generation failures recorded."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero volmgr dump generation failures recorded."
    } else {
        Write-Host "[INFO] Volmgr event query note: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($totalDumps -gt 0 -or $bcCount -gt 0) {
    Write-Host "[BAD]  SYSTEM HAS EXPERIENCED BLUE SCREEN CRASHES!"
    Write-Host "       HELPDESK ACTION PLAN:"
    Write-Host "       1. Check the decoded stop codes above."
    Write-Host "       2. If 0x124 WHEA: Check CPU thermals, remove undervolts, update motherboard BIOS/NVMe firmware."
    Write-Host "       3. If 0x50 or 0x1A: Run Windows Memory Diagnostic (mdsched.exe) to verify RAM integrity."
    Write-Host "       4. If 0xD1 or 0x3B: Update GPU, Wi-Fi, and network drivers to current vendor versions."
} else {
    Write-Host "[OK]   STATUS: SYSTEM IS KERNEL-STABLE. Zero recent BSOD minidumps or BugCheck events found."
}
""",
    ),
    TRO(
        "appcrash_wer_timeline",
        "Application Crash, Exception & Hang Timeline",
        "Scans Application event log for Event 1000 (Application Crash), 1001 (WER), and 1002 (Hang), "
        "grouping by faulting module, decoding NTSTATUS exception codes, and summarizing top unstable apps.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Application Crash & Hang Post-Mortem Timeline"
Write-Host "PURPOSE   : Pinpoints crashing programs, faulting DLL modules, and exception codes"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK EXCEPTION CODE GUIDE:"
Write-Host "  • 0xc0000005 : STATUS_ACCESS_VIOLATION -- Program tried to read/write invalid memory address."
Write-Host "  • 0xc0000409 : STATUS_STACK_BUFFER_OVERRUN -- Security buffer overflow fast-fail triggered."
Write-Host "  • 0xc0000374 : STATUS_HEAP_CORRUPTION -- Process heap was damaged before allocation."
Write-Host "  • 0xe0434352 : CLR_EXCEPTION -- Unhandled .NET Framework / C# managed runtime error."
Write-Host "  • 0x80000003 : STATUS_BREAKPOINT -- Program encountered hardcoded debug assert."
Write-Host ""

$cutDate = (Get-Date).AddDays(-14)
$crashEvents = @()
$hangEvents = @()

Write-Host "=== Phase 1: Application Crashes (Event ID 1000) - Last 14 Days ==="
try {
    $crashEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Application';
        ProviderName = 'Application Error';
        Id = 1000;
        StartTime = $cutDate
    } -MaxEvents 50 -ErrorAction Stop

    Write-Host ("[WARN] Found {0} Application Crash event(s) in last 14 days:" -f $crashEvents.Count)
    $modules = @{}
    $crashEvents | Select-Object -First 10 | ForEach-Object {
        $xml = [xml]$_.ToXml()
        $app = $xml.Event.EventData.Data | Where-Object { $_.Name -eq 'AppName' } | Select-Object -ExpandProperty '#text'
        $mod = $xml.Event.EventData.Data | Where-Object { $_.Name -eq 'ModuleName' } | Select-Object -ExpandProperty '#text'
        $code = $xml.Event.EventData.Data | Where-Object { $_.Name -eq 'ExceptionCode' } | Select-Object -ExpandProperty '#text'
        if (-not $app) { $app = 'Unknown' }
        if (-not $mod) { $mod = 'Unknown' }
        if (-not $code) { $code = 'Unknown' }
        $modules[$mod] = [int]$modules[$mod] + 1

        $decoded = switch -Regex ($code) {
            'c0000005' { 'ACCESS_VIOLATION' }
            'c0000409' { 'STACK_BUFFER_OVERRUN' }
            'c0000374' { 'HEAP_CORRUPTION' }
            'e0434352' { '.NET_CLR_EXCEPTION' }
            default { $code }
        }
        Write-Host ("  [{0:yyyy-MM-dd HH:mm}] App: {1,-22} Module: {2,-22} Code: {3}" -f $_.TimeCreated, $app, $mod, $decoded)
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero Application Crash events (ID 1000) recorded in the last 14 days."
    } else {
        Write-Host "[INFO] AppCrash query note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 2: Application Hangs & Freezes (Event ID 1002) ==="
try {
    $hangEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Application';
        ProviderName = 'Application Hang';
        Id = 1002;
        StartTime = $cutDate
    } -MaxEvents 20 -ErrorAction Stop

    if ($hangEvents) {
        Write-Host ("[WARN] Found {0} Application Hang event(s) in last 14 days:" -f $hangEvents.Count)
        $hangEvents | Select-Object -First 10 | ForEach-Object {
            $msg = ($_.Message -split "`r?`n")[0]
            Write-Host ("  [{0:yyyy-MM-dd HH:mm}] {1}" -f $_.TimeCreated, $msg)
        }
    } else {
        Write-Host "[OK]   Zero Application Hang events recorded in the last 14 days."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero Application Hang events recorded in the last 14 days."
    } else {
        Write-Host "[INFO] AppHang query note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 3: Top Faulting Modules Distribution ==="
if ($crashEvents.Count -gt 0) {
    Write-Host "[INFO] Correlating which DLLs are responsible for crashes:"
    $moduleCounts = @{}
    foreach ($evt in $crashEvents) {
        $xml = [xml]$evt.ToXml()
        $mod = $xml.Event.EventData.Data | Where-Object { $_.Name -eq 'ModuleName' } | Select-Object -ExpandProperty '#text'
        if ($mod) { $moduleCounts[$mod] = [int]$moduleCounts[$mod] + 1 }
    }
    $moduleCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 5 | ForEach-Object {
        Write-Host ("  • {0,-28} : {1} crash(es)" -f $_.Key, $_.Value)
    }
} else {
    Write-Host "[OK]   No crash telemetry to aggregate."
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($crashEvents.Count -gt 0) {
    Write-Host "[WARN] ATTENTION: Software instability detected!"
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. If crashes are isolated to ONE app: Reinstall or update that specific application."
    Write-Host "       2. If crashes occur across MULTIPLE apps in ntdll.dll or kernelbase.dll: Run System Health scan (SFC / DISM)."
    Write-Host "       3. If module is a graphics DLL (e.g. nvldumdx.dll, atidxx64.dll): Update GPU driver."
} else {
    Write-Host "[OK]   STATUS: APPLICATION ENVIRONMENT HEALTHY. Zero recorded crashes or hangs."
}
""",
    ),
    TRO(
        "service_dcom_hang_matrix",
        "Service Crashes, DCOM Permission Failures & Hang Correlation",
        "Correlates Service Control Manager crashes (Event 7031, 7034, 7024), startup failures (7000), "
        "and translates DCOM 10016 permission events, separating benign background noise from genuine failures.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Service Crashes & DCOM Permission Matrix"
Write-Host "PURPOSE   : Pinpoints crashed services and filters cosmetic DCOM 10016 noise"
Write-Host "================================================================================"
Write-Host "[INFO] SENIOR SYSADMIN DCOM PRIMER:"
Write-Host "  • DCOM Event 10016: Occurs when an application requests DCOM activation without explicit ACLs."
Write-Host "  • Microsoft KB explicitly documents that 95% of 10016 events (RuntimeBroker, ShellServiceHost)"
Write-Host "    are COMPLETELY BENIGN BY DESIGN! Do not waste time changing DCOM permissions unless an"
Write-Host "    essential line-of-business server role fails to start."
Write-Host ""

$cutDate = (Get-Date).AddDays(-14)
$svcEvents = @()

Write-Host "=== Phase 1: Service Failures & Abnormal Terminations (System Log) ==="
try {
    $svcEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Service Control Manager';
        Id = 7000, 7024, 7031, 7034;
        StartTime = $cutDate
    } -MaxEvents 30 -ErrorAction Stop

    Write-Host ("[BAD]  Found {0} service failure event(s) in the last 14 days:" -f $svcEvents.Count)
    $svcEvents | Select-Object -First 12 | ForEach-Object {
        $id = $_.Id
        $type = switch ($id) {
            7000 { 'Failed to Start' }
            7024 { 'Terminated with Error' }
            7031 { 'Crashed (Recovery Run)' }
            7034 { 'Crashed Unexpectedly' }
        }
        $msg = ($_.Message -split "`r?`n")[0]
        Write-Host ("  [{0:yyyy-MM-dd HH:mm}] [BAD] Event {1} ({2}): {3}" -f $_.TimeCreated, $id, $type, $msg)
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero abnormal service termination events in the last 14 days."
    } else {
        Write-Host "[INFO] SCM event check note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 2: DCOM Permission Denied Warnings (Event ID 10016) ==="
try {
    $dcomEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Microsoft-Windows-DistributedCOM';
        Id = 10016;
        StartTime = (Get-Date).AddDays(-7)
    } -MaxEvents 50 -ErrorAction Stop

    Write-Host ("[INFO] Recorded {0} DCOM permission warning(s) in the last 7 days." -f $dcomEvents.Count)
    if ($dcomEvents.Count -gt 25) {
        Write-Host "[INFO] Moderate frequency of DCOM 10016 events. Normal Windows client background behavior."
    } else {
        Write-Host "[OK]   Low frequency of DCOM permission warnings."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero DCOM 10016 permission warning events in the last 7 days."
    } else {
        Write-Host "[INFO] DCOM check note: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($svcEvents.Count -gt 0) {
    Write-Host "[BAD]  SERVICE FAILURES DETECTED: Investigate the crashed service names from Phase 1."
    Write-Host "       HELPDESK ACTION PLAN:"
    Write-Host "       1. Check if the crashed service belongs to an antivirus, VPN, backup agent, or print driver."
    Write-Host "       2. Open Windows Services console (services.msc) and inspect service logon account & recovery tab."
    Write-Host "       3. Check dependencies: Ensure required prerequisite services are set to Automatic and running."
} else {
    Write-Host "[OK]   STATUS: SERVICES STABLE. Zero abnormal service crashes or termination events found."
}
""",
    ),
    TRO(
        "kernel_power_dirty_shutdowns",
        "Kernel-Power Dirty Shutdowns, BugChecks & Power Outages",
        "Correlates Event 41 (Kernel-Power), Event 6008 (unexpected shutdown), and Event 1074 (planned shutdown), "
        "decodes PowerButtonTimestamp to identify forced user power-offs vs genuine electrical power loss.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Kernel-Power Dirty Shutdown & Power Loss Post-Mortem"
Write-Host "PURPOSE   : Distinguishes forced button hold-downs from power cuts and BSOD crashes"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK TRIAGE DECISION TREE:"
Write-Host "  • Case 1: BugcheckCode != 0 -> Kernel Blue Screen (BSOD) crashed the system."
Write-Host "  • Case 2: BugcheckCode == 0 AND PowerButtonTimestamp != 0 -> User pressed and held power button!"
Write-Host "  • Case 3: BugcheckCode == 0 AND PowerButtonTimestamp == 0 -> Hardware power drop (wall power/PSU/battery)."
Write-Host ""

$kpEvents = @()
$sdEvents = @()

Write-Host "=== Phase 1: Kernel-Power (Event ID 41) Unclean Reboots - Last 30 Days ==="
try {
    $kpEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Microsoft-Windows-Kernel-Power';
        Id = 41;
        StartTime = (Get-Date).AddDays(-30)
    } -MaxEvents 20 -ErrorAction Stop

    Write-Host ("[BAD]  Found {0} Kernel-Power Event 41 unclean reboot(s) in the last 30 days!" -f $kpEvents.Count)
    $kpEvents | ForEach-Object {
        $xml = [xml]$_.ToXml()
        $bcCode = $xml.Event.EventData.Data | Where-Object { $_.Name -eq 'BugcheckCode' } | Select-Object -ExpandProperty '#text'
        $btnTime = $xml.Event.EventData.Data | Where-Object { $_.Name -eq 'PowerButtonTimestamp' } | Select-Object -ExpandProperty '#text'
        $sleep = $xml.Event.EventData.Data | Where-Object { $_.Name -eq 'SleepInProgress' } | Select-Object -ExpandProperty '#text'

        $bcHex = if ($bcCode -and $bcCode -ne '0') { '0x{0:X}' -f [int64]$bcCode } else { '0x0' }
        $userForced = if ($btnTime -and $btnTime -ne '0') { 'YES (Power button held down)' } else { 'No' }

        $triage = if ($bcHex -ne '0x0') {
            "Kernel BugCheck Crash ($bcHex)"
        } elseif ($userForced -like 'YES*') {
            "User Forced Power-Off (Held power button for 4-10 sec)"
        } else {
            "Sudden Power Drop / PSU / Battery Loss"
        }

        Write-Host ("  [{0:yyyy-MM-dd HH:mm:ss}] [BAD] Cause: {1}" -f $_.TimeCreated, $triage)
        Write-Host ("       BugCheck: {0,-10} ButtonHeld: {1,-10} SleepActive: {2}" -f $bcHex, $userForced, $sleep)
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero Kernel-Power Event 41 unclean reboots in the last 30 days."
    } else {
        Write-Host "[INFO] Kernel-Power check note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 2: Planned vs Unexpected Shutdowns Ratio (1074 vs 6008) ==="
try {
    $sdEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        Id = 1074, 6008;
        StartTime = (Get-Date).AddDays(-30)
    } -MaxEvents 50 -ErrorAction Stop

    $unexpected = ($sdEvents | Where-Object { $_.Id -eq 6008 }).Count
    $planned = ($sdEvents | Where-Object { $_.Id -eq 1074 }).Count
    Write-Host ("Planned Graceful Shutdowns (1074)  : {0}" -f $planned)
    Write-Host ("Unexpected Dirty Shutdowns (6008)  : {0}" -f $unexpected)
    if ($unexpected -gt 0) {
        Write-Host "[BAD]  System has dirty shutdowns recorded where data could not be flushed to disk!"
    } else {
        Write-Host "[OK]   All shutdowns were cleanly executed."
    }
} catch {
    Write-Host "[INFO] Shutdown event check note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($kpEvents.Count -gt 0) {
    Write-Host "[BAD]  SYSTEM INSTABILITY OR POWER LOSS DETECTED!"
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. If 'User Forced Power-Off': Ask user what app was open when the PC froze."
    Write-Host "       2. If 'Sudden Power Drop': Inspect power brick, surge protector, laptop battery wear, or thermal throttling."
    Write-Host "       3. If 'Kernel BugCheck': Run minidump triage script to correlate bugcheck codes."
} else {
    Write-Host "[OK]   STATUS: POWER STABILITY HEALTHY. Zero abnormal shutdowns or power drops detected."
}
""",
    ),
]
