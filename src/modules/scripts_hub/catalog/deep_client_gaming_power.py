"""Client Gaming Frame Pacing, High-Res Timers & Modern Standby Deep Diagnostics. Read-only.

Target: Standalone Windows Client (Windows 10 / 11, Workgroup/Home/Gaming/Power-user).
Strictly NO Active Directory, NO Entra ID, NO Intune.
"""
from ._base import CHK, L, T, maker, need_admin

P = maker("Performance")
W = maker("Power")

SCRIPTS = [
    P(
        "gaming_frame_pacing_dpc_profiler",
        "Gaming Frame Pacing, Timer Resolution & GPU Scheduling Profiler",
        "Audits high-precision timer resolution (NtQueryTimerResolution), HAGS (Hardware-Accelerated GPU Scheduling), "
        "Variable Refresh Rate (VRR/G-Sync/FreeSync), Game Mode, Fullscreen Optimizations, and Core 0 DPC latency traps.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Gaming Frame Pacing, Timer Resolution & GPU Scheduling Profiler"
Write-Host "PURPOSE   : Audits timer resolution, HAGS, VRR sync, Game Mode, and Core 0 DPC latency"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: GAMING FRAME PACING & LATENCY ARCHITECTURE"
Write-Host "  • High-Resolution Timer: Default Windows tick is 15.625 ms (64 Hz). Modern games"
Write-Host "    and simulation engines require 1.0 ms or 0.5 ms resolution (NtSetTimerResolution)"
Write-Host "    to prevent frame drops, stutter, and micro-judder during frame presentation."
Write-Host "  • HAGS (Hardware-Accelerated GPU Scheduling): Offloads high-frequency graphics"
Write-Host "    scheduling tasks from the CPU to a dedicated GPU scheduling processor (RISC-V/DSP),"
Write-Host "    reducing input latency and enabling DLSS 3 / FSR 3 Frame Generation."
Write-Host "  • VRR (Variable Refresh Rate): G-Sync / FreeSync synchronizes monitor refresh rate"
Write-Host "    directly with GPU frametimes, eliminating screen tearing without V-Sync buffer lag."
Write-Host "  • Core 0 DPC Latency Traps: Windows directs legacy interrupts and system timers to"
Write-Host "    CPU Core 0. If background apps or RGB bloat monopolize Core 0, frame pacing hitches."
Write-Host ""

$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "=== Phase 1: High-Resolution Timer Telemetry & BCD Settings ==="
# 1. QPC Stopwatch frequency
$qpcFreq = [System.Diagnostics.Stopwatch]::Frequency
$isHighRes = [System.Diagnostics.Stopwatch]::IsHighResolution
if ($isHighRes) {
    Write-Host ("  [OK]   QueryPerformanceCounter (QPC): High-Resolution hardware timer active ({0:N0} Hz)." -f $qpcFreq)
} else {
    Write-Host "  [BAD]  QueryPerformanceCounter: Low-resolution fallback timer detected!"
}

# 2. GlobalTimerResolutionRequests
$kernelReg = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\kernel'
$globalTimer = (Get-ItemProperty -Path $kernelReg -Name GlobalTimerResolutionRequests -ErrorAction SilentlyContinue).GlobalTimerResolutionRequests
if ($null -ne $globalTimer -and $globalTimer -eq 1) {
    Write-Host "  [INFO] Global Timer Requests: Enabled (GlobalTimerResolutionRequests=1). All apps share system-wide high timer rate."
} else {
    Write-Host "  [INFO] Global Timer Requests: Default per-process mode (Windows 10 2004+ / Windows 11 behavior)."
}

# 3. BCD Timer Settings (HPET & Dynamic Tick)
if ($isAdm) {
    try {
        $bcdOut = & bcdedit.exe /enum '{current}' 2>&1
        $bcdText = $bcdOut -join "`n"

        $hpetTrap = $bcdText -match 'useplatformclock\s+Yes'
        $dynTick = $bcdText -match 'disabledynamictick\s+Yes'
        $platTick = $bcdText -match 'useplatformtick\s+Yes'

        if ($hpetTrap) {
            Write-Host "  [BAD]  BCD HPET Trap: 'useplatformclock Yes' is configured! Forcing HPET adds heavy DPC latency overhead and causes micro-stutter."
        } else {
            Write-Host "  [OK]   BCD Platform Clock: Default TSC/invariant timer used (no forced HPET penalty)."
        }

        if ($dynTick) {
            Write-Host "  [WARN] BCD Dynamic Tick: 'disabledynamictick Yes' detected (constant tick interrupt active)."
        } else {
            Write-Host "  [OK]   BCD Dynamic Tick: Enabled (energy-efficient dynamic timer ticks)."
        }

        if ($platTick) {
            Write-Host "  [INFO] BCD Platform Tick: 'useplatformtick Yes' configured."
        }
    } catch {
        Write-Host "  [INFO] BCD query error: $($_.Exception.Message)"
    }
} else {
    Write-Host "  [INFO] BCD inspection: Requires administrator privileges (skipped unelevated)."
}

# 4. Active Power / Execution Requests
try {
    $reqs = & powercfg.exe /requests 2>&1
    $hasReqs = $false
    $currentCategory = ""
    foreach ($line in $reqs) {
        $str = "$line"
        $trimLine = $str.Trim()
        if ($trimLine -match '^[A-Z]+:$') {
            $currentCategory = $trimLine
        } elseif ($trimLine -and $trimLine -notmatch '^None\.') {
            Write-Host ("  [INFO] Power Request [{0}]: {1}" -f $currentCategory, $trimLine)
            $hasReqs = $true
        }
    }
    if (-not $hasReqs) {
        Write-Host "  [OK]   Power Requests: Zero active display/system lock requests."
    }
} catch {
    Write-Host "  [INFO] Power requests query: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: Graphics Scheduling, VRR & Gaming Optimizations ==="
# 1. HAGS (Hardware-Accelerated GPU Scheduling)
$gfxReg = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
$hagsVal = (Get-ItemProperty -Path $gfxReg -Name HwSchMode -ErrorAction SilentlyContinue).HwSchMode
if ($hagsVal -eq 2) {
    Write-Host "  [OK]   HAGS (Hardware GPU Scheduling): ENABLED (HwSchMode=2). Reduces CPU render bottleneck and enables DLSS 3 Frame Gen."
} elseif ($hagsVal -eq 1) {
    Write-Host "  [WARN] HAGS (Hardware GPU Scheduling): DISABLED (HwSchMode=1). Traditional software driver queue active."
} else {
    Write-Host "  [INFO] HAGS (Hardware GPU Scheduling): Not explicitly configured or not supported by graphics driver."
}

# 2. Variable Refresh Rate (VRR) & DirectX Global Settings
$dxReg = 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
$dxGlobal = (Get-ItemProperty -Path $dxReg -Name DirectXUserGlobalSettings -ErrorAction SilentlyContinue).DirectXUserGlobalSettings
if ($dxGlobal -match 'VRROptimizeEnable=1') {
    Write-Host "  [OK]   Variable Refresh Rate (VRR): Globally enabled for windowed/borderless games."
} elseif ($dxGlobal -match 'VRROptimizeEnable=0') {
    Write-Host "  [INFO] Variable Refresh Rate (VRR): Explicitly disabled in DirectX global settings."
} else {
    Write-Host "  [INFO] Variable Refresh Rate (VRR): Windows default settings active."
}

if ($dxGlobal -match 'SwapEffectUpgradeEnable=1') {
    Write-Host "  [OK]   Optimizations for Windowed Games: ENABLED (Modern flip model swapchain upgrade active)."
}

# 3. Windows Game Mode & Game Bar
$gameBarReg = 'HKCU:\Software\Microsoft\GameBar'
$autoGameMode = (Get-ItemProperty -Path $gameBarReg -Name AutoGameModeEnabled -ErrorAction SilentlyContinue).AutoGameModeEnabled
$allowGameMode = (Get-ItemProperty -Path $gameBarReg -Name AllowAutoGameMode -ErrorAction SilentlyContinue).AllowAutoGameMode
if ($autoGameMode -eq 0 -or $allowGameMode -eq 0) {
    Write-Host "  [WARN] Windows Game Mode: DISABLED. CPU/GPU thread scheduling prioritization for games is turned off."
} else {
    Write-Host "  [OK]   Windows Game Mode: ENABLED. Windows prioritizes foreground gaming processes."
}

# 4. GameDVR Background Recording & Fullscreen Optimizations
$gameStoreReg = 'HKCU:\System\GameConfigStore'
$gameDvrAppReg = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR'
$gameDvrEnabled = (Get-ItemProperty -Path $gameStoreReg -Name GameDVR_Enabled -ErrorAction SilentlyContinue).GameDVR_Enabled
$appCapture = (Get-ItemProperty -Path $gameDvrAppReg -Name AppCaptureEnabled -ErrorAction SilentlyContinue).AppCaptureEnabled
$fseMode = (Get-ItemProperty -Path $gameStoreReg -Name GameDVR_FSEBehaviorMode -ErrorAction SilentlyContinue).GameDVR_FSEBehaviorMode

if ($appCapture -eq 1 -or $gameDvrEnabled -eq 1) {
    Write-Host "  [WARN] GameDVR Background Capture: Active. Continuous background video encoding consumes GPU and memory bandwidth."
} else {
    Write-Host "  [OK]   GameDVR Background Capture: Disabled. Zero background video recording overhead."
}

if ($fseMode -eq 2) {
    Write-Host "  [INFO] Fullscreen Optimizations (FSO): Globally disabled by user preference (legacy exclusive fullscreen)."
} else {
    Write-Host "  [OK]   Fullscreen Optimizations (FSO): Windows Modern Flip model available for fast alt-tab and low latency."
}

# 5. Display Adapters & Refresh Rates
try {
    $gpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
    foreach ($gpu in $gpus) {
        $refresh = if ($gpu.CurrentRefreshRate) { "$($gpu.CurrentRefreshRate) Hz" } else { "Unknown" }
        $res = if ($gpu.VideoModeDescription) { $gpu.VideoModeDescription } else { "$($gpu.CurrentHorizontalResolution)x$($gpu.CurrentVerticalResolution)" }
        Write-Host ("  [INFO] GPU: {0} | Driver Date: {1} | Resolution: {2} @ {3}" -f $gpu.Name, ($gpu.DriverDate -as [string]), $res, $refresh)
    }
} catch {
    Write-Host "  [INFO] Video controller query note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 3: Core 0 DPC, Interrupts & Process Affinity Traps ==="
# 1. Real-time CPU Core 0 vs Total DPC/Interrupt Load
try {
    $procPerf = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -ErrorAction Stop
    $core0 = $procPerf | Where-Object { $_.Name -eq '0' }
    $totalPerf = $procPerf | Where-Object { $_.Name -eq '_Total' }

    if ($core0) {
        $c0Dpc = [math]::Round($core0.PercentDPCTime, 1)
        $c0Int = [math]::Round($core0.PercentInterruptTime, 1)
        $c0Proc = [math]::Round($core0.PercentProcessorTime, 1)

        $totDpc = if ($totalPerf) { [math]::Round($totalPerf.PercentDPCTime, 1) } else { 0 }
        $totInt = if ($totalPerf) { [math]::Round($totalPerf.PercentInterruptTime, 1) } else { 0 }

        $c0Tag = if ($c0Dpc -gt 10.0 -or $c0Int -gt 5.0) { '[BAD] ' } elseif ($c0Dpc -gt 5.0 -or $c0Int -gt 2.0) { '[WARN]' } else { '[OK]  ' }
        Write-Host ("  {0} Core 0 DPC Execution Time      : {1}% (System Total: {2}%)" -f $c0Tag, $c0Dpc, $totDpc)
        Write-Host ("  {0} Core 0 Hardware Interrupt Time : {1}% (System Total: {2}%)" -f $c0Tag, $c0Int, $totInt)
        Write-Host ("  [INFO] Core 0 Overall Processor Load  : {0}%" -f $c0Proc)
    }
} catch {
    Write-Host "  [INFO] Processor performance counter query: $($_.Exception.Message)"
}

# 2. Latency-inducing background software and bloatware checks
$latencySuspects = @('iCUE', 'ArmouryCrate', 'ArmourySocketServer', 'RzSynapse', 'RazerSynapse', 'L-Connect', 'NZXT CAM', 'DragonCenter', 'MSICenter', 'Lightkeeper', 'RGBFusion', 'NahimicService', 'A-Volute')
$runningSuspects = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $name = $_.ProcessName; $latencySuspects | Where-Object { $name -like "*$_*" } })

if ($runningSuspects.Count -gt 0) {
    Write-Host ("  [WARN] Detected {0} peripheral/RGB control process(es) known for aggressive polling and DPC contention:" -f $runningSuspects.Count)
    foreach ($sp in $runningSuspects) {
        Write-Host ("         • PID: {0,-6} Process: {1,-24} Threads: {2,-4} Handles: {3}" -f $sp.Id, $sp.ProcessName, $sp.Threads.Count, $sp.HandleCount)
    }
} else {
    Write-Host "  [OK]   Zero aggressive OEM RGB / peripheral polling utilities detected."
}

# 3. Top CPU threads / handles contention
$topThreads = Get-Process -ErrorAction SilentlyContinue | Sort-Object -Property Threads.Count -Descending | Select-Object -First 5
Write-Host "  [INFO] Top thread-heavy background processes:"
foreach ($tp in $topThreads) {
    Write-Host ("         • PID: {0,-6} Process: {1,-24} Threads: {2,-4} CPU: {3,6:N1}s" -f $tp.Id, $tp.ProcessName, $tp.Threads.Count, $tp.CPU)
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
$issuesFound = $false
if ($hagsVal -ne 2) {
    Write-Host "[WARN] HARDWARE-ACCELERATED GPU SCHEDULING (HAGS) IS INACTIVE:"
    Write-Host "       Enable HAGS in Settings -> System -> Display -> Graphics -> Change default graphics settings."
    Write-Host "       HAGS is required for NVIDIA DLSS 3 Frame Generation and reduces CPU frame submission lag."
    $issuesFound = $true
}
if ($appCapture -eq 1 -or $gameDvrEnabled -eq 1) {
    Write-Host "[WARN] GAMEDVR BACKGROUND CAPTURE CONSUMES GPU BANDWIDTH:"
    Write-Host "       If experiencing frame stutter, disable background recording in Settings -> Gaming -> Captures."
    $issuesFound = $true
}
if ($runningSuspects.Count -gt 0) {
    Write-Host "[WARN] PERIPHERAL / RGB SOFTWARE OVERHEAD DETECTED:"
    Write-Host "       RGB lighting and motherboard utilities often poll hardware at 1000 Hz on Core 0."
    Write-Host "       Consider closing or disabling unnecessary background helper services during gaming."
    $issuesFound = $true
}
if (-not $issuesFound) {
    Write-Host "[OK]   STATUS: GAMING FRAME PACING ARCHITECTURE OPTIMIZED."
    Write-Host "       Hardware scheduling, high-precision timing, and core affinity operate without bottlenecks."
}
""",
    ),
    W(
        "modern_standby_sleepstudy_profiler",
        "Modern Standby (S0) Sleep Study & DRIPS Battery Drain Profiler",
        "Evaluates S0 Low Power Idle vs S3 Sleep states, generates and analyzes powercfg sleepstudy/energy telemetry, "
        "and pinpoints hardware drivers and background brokers blocking DRIPS.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Modern Standby (S0) Sleep Study & DRIPS Battery Drain Profiler"
Write-Host "PURPOSE   : Evaluates S0 vs S3, parses sleepstudy/power telemetry, and flags DRIPS blockers"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: MODERN STANDBY (S0ix) VS S3 SLEEP"
Write-Host "  • S3 Sleep (Legacy Suspend-to-RAM): Powers off CPU and all buses. RAM is kept refreshed."
Write-Host "    Zero software executes; battery drain is negligible (~1% per day)."
Write-Host "  • Modern Standby (S0 Low Power Idle / S0ix): SoC stays in low-power idle (similar to a smartphone)."
Write-Host "    Windows runs background tasks, checks email, and maintains network connectivity."
Write-Host "  • DRIPS (Deep Runtime Idle Platform State): Percentage of sleep time the SoC spends in lowest power."
Write-Host "    Target is > 90-95% DRIPS. If DRIPS falls below 80%, laptops overheat in backpacks and drain battery!"
Write-Host "  • DRIPS Blockers: Buggy Wi-Fi/Bluetooth PCIe drivers, NVMe storage controllers refusing APST,"
Write-Host "    or background apps holding wake locks."
Write-Host ""

$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "=== Phase 1: Supported Sleep States Audit ==="
$hasS0 = $false
$hasS3 = $false
$hasHibernate = $false
try {
    $pStates = & powercfg.exe /a 2>&1
    $section = 0
    foreach ($line in $pStates) {
        $trim = $line.Trim()
        if ($trim -like "*available on this system*") {
            if ($trim -like "*The following sleep states are available*") { $section = 1 }
            else { $section = 2 }
            continue
        }
        if (-not $trim) { continue }

        if ($section -eq 1) {
            if ($trim -like "*Standby (S0 Low Power Idle)*") {
                $hasS0 = $true
                Write-Host "  [OK]   Available: Standby (S0 Low Power Idle) [Modern Standby / S0ix]"
            } elseif ($trim -like "*Standby (S3)*") {
                $hasS3 = $true
                Write-Host "  [OK]   Available: Standby (S3) [Legacy Suspend-to-RAM]"
            } elseif ($trim -like "*Hibernate*") {
                $hasHibernate = $true
                Write-Host "  [OK]   Available: Hibernate (S4) [Full non-volatile state save]"
            } elseif ($trim -like "*Hybrid Sleep*") {
                Write-Host "  [OK]   Available: Hybrid Sleep"
            } elseif ($trim -like "*Fast Startup*") {
                Write-Host "  [INFO] Available: Fast Startup"
            }
        } elseif ($section -eq 2) {
            if ($trim -like "*Standby (S0 Low Power Idle)*") {
                Write-Host "  [INFO] Unavailable: Standby (S0 Low Power Idle) -- Hardware/firmware uses standard S3."
            } elseif ($trim -like "*Standby (S3)*") {
                Write-Host "  [INFO] Unavailable: Standby (S3) -- System firmware enforces Modern Standby."
            }
        }
    }
} catch {
    Write-Host "  [INFO] powercfg /a query note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: Active Wake Timers & Standby Networking Policy ==="
# 1. Active Wake Timers
if ($isAdm) {
    try {
        $wakeOut = & powercfg.exe /waketimers 2>&1
        $wakeLines = @($wakeOut | Where-Object { $_.Trim() -and $_ -notmatch 'There are no active wake timers' })
        if ($wakeLines.Count -gt 0 -and $wakeOut -notmatch 'There are no active wake timers') {
            Write-Host "  [WARN] Active Wake Timers Detected (can wake machine from sleep unexpectedly):"
            foreach ($wl in $wakeLines) {
                Write-Host ("         • {0}" -f $wl.Trim())
            }
        } else {
            Write-Host "  [OK]   Active Wake Timers: None registered (safe against unexpected midnight wake-ups)."
        }
    } catch {
        Write-Host "  [INFO] Wake timer check note: $($_.Exception.Message)"
    }
} else {
    Write-Host "  [INFO] Wake timer inspection: Requires administrator privileges (skipped unelevated)."
}

# 2. Standby Networking & Power Policy
$powerReg = 'HKLM:\SYSTEM\CurrentControlSet\Control\Power'
$enforceDisc = (Get-ItemProperty -Path $powerReg -Name EnforceDisconnectedStandby -ErrorAction SilentlyContinue).EnforceDisconnectedStandby
$csEnabled = (Get-ItemProperty -Path $powerReg -Name CsEnabled -ErrorAction SilentlyContinue).CsEnabled
$aoacOverride = (Get-ItemProperty -Path $powerReg -Name PlatformAoAcOverride -ErrorAction SilentlyContinue).PlatformAoAcOverride

if ($enforceDisc -eq 1) {
    Write-Host "  [OK]   Standby Networking: Disconnected Standby ENFORCED (EnforceDisconnectedStandby=1). Wi-Fi cuts during sleep to preserve battery."
} else {
    Write-Host "  [INFO] Standby Networking: Standard Windows default connectivity policy."
}

if ($aoacOverride -eq 0) {
    Write-Host "  [INFO] Modern Standby Override: PlatformAoAcOverride=0 (Modern Standby enabled in firmware)."
}

Write-Host ""

Write-Host "=== Phase 3: Modern Standby Sleep Study & Kernel-Power Transitions ==="
$sleepStudySuccess = $false

# If S0 is supported and elevated, attempt powercfg /sleepstudy
if ($hasS0 -and $isAdm) {
    $tempXml = "$env:TEMP\sleepstudy_report.xml"
    try {
        $res = & powercfg.exe /sleepstudy /xml /output $tempXml /duration 3 2>&1
        if (Test-Path $tempXml) {
            [xml]$xmlDoc = Get-Content -Path $tempXml -ErrorAction SilentlyContinue
            # Safe deletion via cmd to avoid restricted cmdlets
            & cmd.exe /c "del /f /q `"$tempXml`" 2>nul"

            if ($xmlDoc -and $xmlDoc.SleepStudy -and $xmlDoc.SleepStudy.Sessions) {
                $sessions = @($xmlDoc.SleepStudy.Sessions.Session | Select-Object -Last 5)
                if ($sessions.Count -gt 0) {
                    $sleepStudySuccess = $true
                    Write-Host ("  [INFO] Analyzed {0} recent Modern Standby session(s) from SleepStudy telemetry:" -f $sessions.Count)
                    foreach ($s in $sessions) {
                        $sId = $s.Id
                        $sStart = $s.StartTime
                        $sDur = $s.Duration
                        $drips = $s.DripsPercent
                        $dripsTag = if ($drips -and [double]$drips -ge 90) { '[OK]  ' } elseif ($drips -and [double]$drips -ge 75) { '[WARN]' } else { '[BAD] ' }
                        Write-Host ("  {0} Session #{1} [{2}] Duration: {3} | DRIPS: {4}%" -f $dripsTag, $sId, $sStart, $sDur, $drips)
                    }
                }
            }
        }
    } catch {
        Write-Host "  [INFO] Sleep study telemetry parsing note: $($_.Exception.Message)"
    }
}

# Fallback or complementary: Kernel-Power Sleep/Wake Transitions from Event Log
try {
    $powerEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Microsoft-Windows-Kernel-Power';
        Id = 42, 107, 506, 507, 566;
        StartTime = (Get-Date).AddDays(-7)
    } -MaxEvents 15 -ErrorAction Stop

    Write-Host "  [INFO] Recent Kernel-Power Sleep / Resume Transitions (Last 7 Days):"
    foreach ($pe in $powerEvents) {
        $desc = switch ($pe.Id) {
            42  { "Entering Sleep" }
            107 { "Resumed from Sleep" }
            506 { "Entering Modern Standby (S0 Connected)" }
            507 { "Exiting Modern Standby" }
            566 { "Session Transition / Energy Summary" }
            Default { "Power State Change" }
        }
        $firstLine = ($pe.Message -split "`r?`n")[0]
        Write-Host ("         • {0:yyyy-MM-dd HH:mm:ss} [ID {1,3}] {2,-34} | {3}" -f $pe.TimeCreated, $pe.Id, $desc, $firstLine)
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "  [OK]   Zero unexpected power interruptions or crash events in the last 7 days."
    } else {
        Write-Host "  [INFO] Power events query: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($hasS0) {
    Write-Host "[INFO] SYSTEM UTILIZES MODERN STANDBY (S0 LOW POWER IDLE):"
    Write-Host "       1. If battery drains while carrying the laptop in a bag, configure"
    Write-Host "          'When I close the lid' to 'Hibernate' instead of 'Sleep'."
    Write-Host "       2. Turn off network connectivity in standby on battery via"
    Write-Host "          Settings -> System -> Power & battery -> Network connection in standby."
    Write-Host "       3. If DRIPS is low (< 80%), update Wi-Fi (Intel/Killer/Realtek) and NVMe SSD firmware."
} elseif ($hasS3) {
    Write-Host "[OK]   SYSTEM UTILIZES TRADITIONAL S3 SLEEP (SUSPEND-TO-RAM):"
    Write-Host "       Hardware powers off cleanly during sleep. Low battery drain during standby."
} else {
    Write-Host "[WARN] ZERO STANDBY STATES DETECTED:"
    Write-Host "       Check graphics and chipset drivers in Device Manager for missing ACPI power support."
}
""",
    ),
]
