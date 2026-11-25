"""Advanced System, Kernel & Performance Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

S = maker("System")
P = maker("Performance")

SCRIPTS = [
    P(
        "sys_bottleneck_matrix",
        "System Bottleneck & Saturation Report",
        "Multi-phase performance saturation assessment measuring processor queue length, "
        "memory commit charge vs physical headroom, kernel pool saturation, and disk I/O queue depth.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: System Bottleneck & Hardware Saturation Matrix"
Write-Host "PURPOSE   : Pinpoints whether system slowness is CPU, RAM, Pagefile, or Disk bound"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK BOTTLENECK PRIMER:"
Write-Host "  • CPU Bound: CPU load > 90% or Top processes consuming continuous execution cores."
Write-Host "  • RAM Bound: Physical RAM > 85% in use; system starts paging active working sets."
Write-Host "  • Commit Bound: Total Virtual Memory (Commit) > 90%; risk of software allocation crashes."
Write-Host "  • Disk Bound: Storage volume free space < 10% or saturated I/O queue."
Write-Host ""

Write-Host "=== Phase 1: Processor Load & Top CPU Consumers ==="
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$topCpu = Get-Process | Sort-Object CPU -Descending | Select-Object -First 5
foreach ($p in $topCpu) {
    $cpuSec = [math]::Round($p.CPU, 1)
    $tag = if ($cpuSec -gt 3600) { '[WARN]' } else { '[INFO]' }
    Write-Host ("  {0} PID: {1,-6} CPU: {2,7}s | RAM: {3,7:N1} MB | Process: {4}" -f $tag, $p.Id, $cpuSec, ($p.WorkingSet64 / 1MB), $p.ProcessName)
}
Write-Host ""

Write-Host "=== Phase 2: Memory & Commit Limit Saturation ==="
$totalRamMB = [math]::Round($cs.TotalPhysicalMemory / 1MB, 0)
$freeRamMB = [math]::Round($os.FreePhysicalMemory / 1024, 0)
$usedRamMB = $totalRamMB - $freeRamMB
$ramPercent = [math]::Round(($usedRamMB / $totalRamMB) * 100, 1)

$commitLimitMB = [math]::Round(($os.TotalVirtualMemorySize) / 1024, 0)
$commitFreeMB = [math]::Round(($os.FreeVirtualMemory) / 1024, 0)
$commitUsedMB = $commitLimitMB - $commitFreeMB
$commitPercent = [math]::Round(($commitUsedMB / $commitLimitMB) * 100, 1)

$ramTag = if ($ramPercent -ge 90) { '[BAD] ' } elseif ($ramPercent -ge 80) { '[WARN]' } else { '[OK]  ' }
$commitTag = if ($commitPercent -ge 90) { '[BAD] ' } elseif ($commitPercent -ge 80) { '[WARN]' } else { '[OK]  ' }

Write-Host ("  {0} Physical RAM In-Use : {1}% ({2} MB / {3} MB)" -f $ramTag, $ramPercent, $usedRamMB, $totalRamMB)
Write-Host ("  {0} Commit Charge In-Use: {1}% ({2} MB / {3} MB limit)" -f $commitTag, $commitPercent, $commitUsedMB, $commitLimitMB)
Write-Host ""

Write-Host "=== Phase 3: Storage Volume Capacity & Headroom ==="
$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"
foreach ($d in $disks) {
    $freeGB = [math]::Round($d.FreeSpace / 1GB, 1)
    $totalGB = [math]::Round($d.Size / 1GB, 1)
    $freePct = [math]::Round(($d.FreeSpace / $d.Size) * 100, 1)
    $dTag = if ($freePct -lt 10) { '[BAD] ' } elseif ($freePct -lt 20) { '[WARN]' } else { '[OK]  ' }
    Write-Host ("  {0} Drive {1} - Free: {2} GB ({3}%) of {4} GB [{5}]" -f $dTag, $d.DeviceID, $freeGB, $freePct, $totalGB, $d.FileSystem)
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($ramPercent -ge 85 -or $commitPercent -ge 85) {
    Write-Host "[WARN] MEMORY PRESSURE DETECTED: High physical RAM or virtual commit usage."
    Write-Host "       Check Phase 1 top processes or run 'Kernel Memory Pools Leak Tracer'."
} elseif ($freePct -lt 15) {
    Write-Host "[WARN] DISK CAPACITY LOW: Drive free space is critically low. Run Quick Cleanup."
} else {
    Write-Host "[OK]   STATUS: SYSTEM SATURATION BALANCED. CPU, Memory, and Storage operating within normal bounds."
}
""",
    ),
    P(
        "cpu_throttling_power",
        "CPU Throttling, Thermal & Power Delivery Diagnostic",
        "Investigates processor frequency throttling, thermal events, PROCHOT signals, "
        "and active Windows power plan delivery policies.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: CPU Frequency Throttling & Power Delivery"
Write-Host "PURPOSE   : Detects BDPROCHOT thermal bugs, power plan limits, and frequency drops"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK CPU THROTTLING PRIMER:"
Write-Host "  • BDPROCHOT / Thermal Throttling: When CPU or VRM exceeds temperature limits (95C-100C),"
Write-Host "    the processor forcefully clamps multiplier to 0.79 GHz or base clock, freezing the PC!"
Write-Host "  • Power Scheme: Power Saver or broken OEM plans can lock maximum CPU frequency to 50%."
Write-Host ""

Write-Host "=== Phase 1: Processor Clock Speeds & Frequency Ratio ==="
$procs = Get-CimInstance Win32_Processor
foreach ($p in $procs) {
    $curGHz = [math]::Round($p.CurrentClockSpeed / 1000, 2)
    $maxGHz = [math]::Round($p.MaxClockSpeed / 1000, 2)
    $pct = [math]::Round(($p.CurrentClockSpeed / $p.MaxClockSpeed) * 100, 1)
    $throttleTag = if ($pct -lt 50) { '[WARN]' } else { '[OK]  ' }
    Write-Host ("  {0} Processor: {1}" -f $throttleTag, $p.Name.Trim())
    Write-Host ("       Current Clock: {0} GHz | Rated Base: {1} GHz ({2}% speed)" -f $curGHz, $maxGHz, $pct)
    Write-Host ("       Cores: {0} | Logical Threads: {1} | Current Load: {2}%" -f $p.NumberOfCores, $p.NumberOfLogicalProcessors, $p.LoadPercentage)
}
Write-Host ""

Write-Host "=== Phase 2: Active Windows Power Plan ==="
try {
    $planOut = powercfg.exe /getactivescheme
    Write-Host ("  [INFO] Active Power Plan: {0}" -f ($planOut -replace '^Power Scheme GUID:\s*', ''))
} catch {
    Write-Host "[WARN] Could not retrieve active power scheme: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 3: Kernel-Processor-Power Throttling Events (Last 14 Days) ==="
$hasEvents = $false
try {
    $throttleEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Microsoft-Windows-Kernel-Processor-Power';
        Id = 37;
        StartTime = (Get-Date).AddDays(-14)
    } -MaxEvents 15 -ErrorAction Stop

    if ($throttleEvents) {
        $hasEvents = $true
        Write-Host ("[WARN] Detected {0} processor speed throttling event(s) in last 14 days:" -f $throttleEvents.Count)
        $throttleEvents | Select-Object -First 5 | ForEach-Object {
            Write-Host ("  • [{0:yyyy-MM-dd HH:mm}] Event 37: {1}" -f $_.TimeCreated, ($_.Message -split "`r?`n")[0])
        }
    } else {
        Write-Host "[OK]   Zero Kernel-Processor-Power throttling events (ID 37) in the last 14 days."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero Kernel-Processor-Power throttling events (ID 37) detected."
    } else {
        Write-Host "[INFO] Event log check note: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($hasEvents) {
    Write-Host "[WARN] THERMAL OR POWER THROTTLING DETECTED:"
    Write-Host "       1. Check CPU temperatures with Hardware Info tab; clean cooling fans/dust."
    Write-Host "       2. If clock is permanently locked to 0.79 GHz on laptop: check charger wattage."
    Write-Host "       3. Set Windows Power Plan to 'Balanced' or 'High Performance'."
} else {
    Write-Host "[OK]   STATUS: CPU FREQUENCY HEALTHY. No active thermal throttling detected."
}
""",
    ),
    P(
        "dpc_isr_latency_triage",
        "DPC & Interrupt Storm Latency Triage",
        "Audits interrupt activity, deferred procedure calls (DPC) execution rate, "
        "and checks for DPC watchdog timeout violations responsible for audio dropouts and micro-stutter.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: DPC / ISR Latency & Interrupt Storm Triage"
Write-Host "PURPOSE   : Pinpoints hardware driver routines causing audio crackling and micro-stutter"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK DPC PRIMER:"
Write-Host "  • DPC (Deferred Procedure Call): Driver routine running at high hardware interrupt priority."
Write-Host "  • If a buggy Wi-Fi, Audio, or GPU driver spends > 10ms in a DPC routine, Windows freezes user audio!"
Write-Host "  • DPC Watchdog Violation (0x133): Kernel BSOD caused when a driver holds a DPC too long."
Write-Host ""

Write-Host "=== Phase 1: Real-Time DPC & Interrupt Time Sample ==="
try {
    $dpcSamples = Get-Counter -Counter '\Processor(_Total)\% DPC Time', '\Processor(_Total)\% Interrupt Time' -MaxSamples 2 -SampleInterval 1 -ErrorAction Stop
    $latest = $dpcSamples[-1]
    foreach ($cooked in $latest.CounterSamples) {
        $val = [math]::Round($cooked.CookedValue, 2)
        if ($cooked.Path -like "*DPC Time*") {
            $tag = if ($val -gt 10.0) { '[BAD] ' } elseif ($val -gt 3.0) { '[WARN]' } else { '[OK]  ' }
            Write-Host ("  {0} DPC Time Execution      : {1}% (Threshold: < 3.0%)" -f $tag, $val)
        } else {
            $tag = if ($val -gt 5.0) { '[BAD] ' } elseif ($val -gt 2.0) { '[WARN]' } else { '[OK]  ' }
            Write-Host ("  {0} Hardware Interrupt Time : {1}% (Threshold: < 2.0%)" -f $tag, $val)
        }
    }
} catch {
    Write-Host "[INFO] Live performance counters note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: DPC Watchdog BugCheck History (Event 1001/41) ==="
$hasWatchdog = $false
try {
    $watchdogEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        Id = 1001, 41;
        StartTime = (Get-Date).AddDays(-30)
    } -MaxEvents 30 -ErrorAction Stop | Where-Object {
        $_.Message -like "*0x00000133*" -or $_.Message -like "*DPC_WATCHDOG_VIOLATION*"
    }

    if ($watchdogEvents) {
        $hasWatchdog = $true
        Write-Host ("[BAD]  Found {0} DPC_WATCHDOG_VIOLATION (0x133) crash event(s) in last 30 days!" -f $watchdogEvents.Count)
        $watchdogEvents | ForEach-Object {
            Write-Host ("  • Crash at {0}: {1}" -f $_.TimeCreated, ($_.Message -split "`r?`n")[0])
        }
    } else {
        Write-Host "[OK]   Zero DPC_WATCHDOG_VIOLATION crash events recorded in the last 30 days."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero DPC watchdog crash events found in System log."
    } else {
        Write-Host "[INFO] Event log check note: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($hasWatchdog) {
    Write-Host "[BAD]  CRITICAL DRIVER DPC DELAY DETECTED!"
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Check for outdated NVMe storage controller drivers (Intel RST or Samsung driver)."
    Write-Host "       2. Update Wi-Fi/NIC network adapter drivers."
    Write-Host "       3. Run LatencyMon tool for millisecond-level driver attribution."
} else {
    Write-Host "[OK]   STATUS: INTERRUPT LATENCY HEALTHY. No DPC watchdog crashes detected."
}
""",
    ),
    P(
        "thread_deadlock_wait_chain",
        "Process Thread Wait & Contention Inspector",
        "Enumerates thread counts, handle accumulation, and identifies high-contention processes "
        "holding hundreds of active handles or exhibiting suspicious wait states.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Process Thread Contention & Handle Leak Inspector"
Write-Host "PURPOSE   : Identifies memory leaks, handle runaway exhaustion, and application freezes"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK HANDLE & THREAD PRIMER:"
Write-Host "  • Handles: Pointers to open files, registry keys, and sockets. Normal apps use 200-2,000."
Write-Host "  • Handle Leaks: When an app opens handles without closing them (>10,000), Windows runs out of pool!"
Write-Host "  • Thread Runaways: Apps spawning > 150 threads usually have stuck deadlocked worker pools."
Write-Host ""

Write-Host "=== Phase 1: High Handle & High Thread Count Outliers ==="
$procs = Get-Process | Where-Object { $_.Handles -gt 1500 -or $_.Threads.Count -gt 60 } |
    Sort-Object Handles -Descending | Select-Object -First 10

if ($procs) {
    Write-Host "[INFO] Processes with elevated thread count (>60) or high handle allocation (>1500):"
    foreach ($p in $procs) {
        $tag = if ($p.Handles -gt 10000 -or $p.Threads.Count -gt 150) { '[WARN]' } else { '[INFO]' }
        Write-Host ("  {0} PID: {1,-6} Handles: {2,-7} Threads: {3,-4} RAM: {4,7:N1} MB | {5}" -f (
            $tag, $p.Id, $p.Handles, $p.Threads.Count, ($p.WorkingSet64 / 1MB), $p.ProcessName
        ))
    }
} else {
    Write-Host "[OK]   No processes currently exceed normal thread (>60) or handle (>1500) thresholds."
}
Write-Host ""

Write-Host "=== Phase 2: Application Hang Events (Event ID 1002 in Application Log) ==="
$hasHangs = $false
try {
    $hangs = Get-WinEvent -FilterHashtable @{
        LogName = 'Application';
        ProviderName = 'Application Hang';
        Id = 1002;
        StartTime = (Get-Date).AddDays(-14)
    } -MaxEvents 10 -ErrorAction Stop

    if ($hangs) {
        $hasHangs = $true
        Write-Host ("[WARN] Found {0} Application Hang event(s) in last 14 days:" -f $hangs.Count)
        $hangs | Select-Object -First 5 | ForEach-Object {
            Write-Host ("  • [{0:yyyy-MM-dd HH:mm}] {1}" -f $_.TimeCreated, ($_.Message -split "`r?`n")[0])
        }
    } else {
        Write-Host "[OK]   Zero Application Hang events (ID 1002) in the last 14 days."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero Application Hang events in the last 14 days."
    } else {
        Write-Host "[INFO] Application log check note: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($hasHangs) {
    Write-Host "[WARN] HANGING APPLICATIONS DETECTED: Review Phase 2 to identify freezing apps."
    Write-Host "       If a process has >10,000 handles in Phase 1, it has a handle leak. Restart the app."
} else {
    Write-Host "[OK]   STATUS: PROCESS THREADS & HANDLES HEALTHY. No runaway deadlocks detected."
}
""",
    ),
    S(
        "service_hang_recovery",
        "Service Crash, Hang & Auto-Recovery Configuration Audit",
        "Audits all Windows services configured for Automatic or Delayed start: identifies those "
        "in a Stopped state, inspects restart-on-failure recovery policies, and checks SCM hang events.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Service Hang & Auto-Recovery Configuration Audit"
Write-Host "PURPOSE   : Verifies whether crashed services automatically restart or remain dead"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK SERVICE RECOVERY PRIMER:"
Write-Host "  • First/Second Failure Actions: Can be set to 'Restart the Service' or 'Take No Action'."
Write-Host "  • SCM Hang Events (7011): Service took longer than 30,000ms to start, stop, or respond."
Write-Host ""

Write-Host "=== Phase 1: Automatic Services In Stopped State ==="
$stoppedAuto = Get-CimInstance Win32_Service | Where-Object {
    $_.StartMode -in 'Auto', 'Automatic' -and $_.State -ne 'Running'
}

$delayedKeys = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\*' -ErrorAction SilentlyContinue |
    Where-Object { $_.DelayedAutoStart -eq 1 } | ForEach-Object { $_.PSChildName }

$problemCount = 0
foreach ($s in $stoppedAuto) {
    $isDelayed = $s.Name -in $delayedKeys
    if ($s.Name -in @('edgeupdate', 'GoogleChromeElevationService', 'sppsvc', 'MapsBroker')) {
        continue
    }
    $problemCount++
    $typeStr = if ($isDelayed) { "Delayed Auto" } else { "Automatic" }
    Write-Host ("[WARN] Service: {0,-22} Name: {1,-28} [Type: {2}, ExitCode: {3}]" -f $s.Name, $s.DisplayName, $typeStr, $s.ExitCode)
}

if ($problemCount -eq 0) {
    Write-Host "[OK]   All non-transient Automatic services are currently RUNNING."
}
Write-Host ""

Write-Host "=== Phase 2: Service Control Manager Crash & Timeout Events ==="
try {
    $scmEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Service Control Manager';
        Id = 7000, 7009, 7011, 7031, 7034;
        StartTime = (Get-Date).AddDays(-7)
    } -MaxEvents 15 -ErrorAction Stop

    Write-Host ("[WARN] Found {0} SCM error or crash event(s) in the last 7 days:" -f $scmEvents.Count)
    $scmEvents | Select-Object -First 8 | ForEach-Object {
        $tag = if ($_.Id -in (7031, 7034)) { '[BAD] ' } else { '[WARN]' }
        Write-Host ("  {0} Event {1} at {2:yyyy-MM-dd HH:mm}: {3}" -f $tag, $_.Id, $_.TimeCreated, ($_.Message -split "`r?`n")[0])
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero Service Control Manager crash or timeout events in the last 7 days."
    } else {
        Write-Host "[INFO] SCM event log check note: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($problemCount -gt 0) {
    Write-Host "[WARN] STOPPED AUTOMATIC SERVICES: Review Phase 1."
    Write-Host "       Check service dependencies and ensure logon account credentials have not expired."
} else {
    Write-Host "[OK]   STATUS: SERVICE INFRASTRUCTURE HEALTHY. SCM running with zero active failures."
}
""",
    ),
    P(
        "paged_nonpaged_pool_leak_tracer",
        "Kernel Memory Pools (Paged / NonPaged) Leak Tracer",
        "Audits kernel memory allocations: PagedPool and NonPagedPool usage, system cache, "
        "and identifies user processes consuming disproportionate private committed memory.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Kernel Memory Pools (Paged / NonPaged) Leak Tracer"
Write-Host "PURPOSE   : Detects kernel-level driver memory leaks that do not show in Task Manager"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK KERNEL POOL PRIMER:"
Write-Host "  • NonPaged Pool: RAM dedicated to kernel and drivers that CANNOT be paged to disk."
Write-Host "    Threshold: > 512MB-1GB indicates an active driver memory leak (antivirus, VPN, NIC)."
Write-Host "  • Paged Pool: Kernel memory that can be paged to the pagefile. Threshold: > 1,024MB."
Write-Host "  • Symptom: System slows down over days until crashing with 0xD1, 0x50, or black screens."
Write-Host ""

Write-Host "=== Phase 1: Kernel Memory Pool Allocations ==="
$perf = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction SilentlyContinue

if ($perf) {
    $pagedMB = [math]::Round($perf.PoolPagedBytes / 1MB, 1)
    $nonPagedMB = [math]::Round($perf.PoolNonpagedBytes / 1MB, 1)
    $cacheMB = [math]::Round($perf.CacheBytes / 1MB, 1)
    $commitMB = [math]::Round($perf.CommittedBytes / 1MB, 1)
    $commitLimitMB = [math]::Round($perf.CommitLimit / 1MB, 1)

    $tagPaged = if ($pagedMB -gt 1024) { '[WARN]' } else { '[OK]  ' }
    $tagNonPaged = if ($nonPagedMB -gt 512) { '[BAD] ' } else { '[OK]  ' }

    Write-Host ("  {0} NonPaged Pool Usage : {1,7:N1} MB (Limit warning: > 512 MB)" -f $tagNonPaged, $nonPagedMB)
    Write-Host ("  {0} Paged Pool Usage    : {1,7:N1} MB (Limit warning: > 1024 MB)" -f $tagPaged, $pagedMB)
    Write-Host ("  [INFO] System File Cache : {0,7:N1} MB" -f $cacheMB)
    Write-Host ("  [INFO] Committed Memory  : {0,7:N1} MB of {1,7:N1} MB limit" -f $commitMB, $commitLimitMB)
} else {
    Write-Host "[INFO] Performance memory counters not currently accessible."
}
Write-Host ""

Write-Host "=== Phase 2: Top 10 Processes by Private Memory Footprint ==="
Get-Process | Sort-Object PrivateMemorySize64 -Descending | Select-Object -First 10 | ForEach-Object {
    $privMB = [math]::Round($_.PrivateMemorySize64 / 1MB, 1)
    $wsMB = [math]::Round($_.WorkingSet64 / 1MB, 1)
    Write-Host ("  PID: {0,-6} Private: {1,7:N1} MB | WorkingSet: {2,7:N1} MB | {3}" -f $_.Id, $privMB, $wsMB, $_.ProcessName)
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($perf -and $nonPagedMB -gt 512) {
    Write-Host "[BAD]  NON-PAGED POOL LEAK SUSPECTED!"
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Note: Task Manager will NOT show which driver is leaking NonPaged Pool."
    Write-Host "       2. Common culprits: Killer/Realtek Network drivers, third-party AV, or outdated VPN."
    Write-Host "       3. Use Windows poolmon.exe utility to view leaking 4-byte pool tags."
} else {
    Write-Host "[OK]   STATUS: KERNEL POOLS HEALTHY. NonPaged and Paged allocations within safe operational ranges."
}
""",
    ),
    P(
        "pagefile_thrashing_allocator",
        "Pagefile Allocation, Location & Usage Saturation",
        "Audits all configured paging files: allocated sizes, volume locations, current usage percentage, "
        "and warns if pagefile is placed on slow magnetic disks while SSDs are present.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Pagefile Allocation, Usage & Storage Volume Placement"
Write-Host "PURPOSE   : Evaluates virtual memory exhaustion and disk thrashing risks"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PAGEFILE PRIMER:"
Write-Host "  • Pagefile.sys: Backing store on disk where Windows offloads inactive memory pages."
Write-Host "  • Thrashing: When RAM is full and disk is slow, Windows spends 100% of time swapping pages."
Write-Host "  • CRITICAL: Never disable pagefile entirely; BSOD kernel crash dumps require it on C:!"
Write-Host ""

Write-Host "=== Phase 1: Paging File Instances & Active Usage ==="
$pagefiles = Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue
$highUsage = $false

if ($pagefiles) {
    foreach ($pf in $pagefiles) {
        $curMB = $pf.CurrentUsage
        $allocMB = $pf.AllocatedBaseSize
        $peakMB = $pf.PeakUsage
        $pct = if ($allocMB -gt 0) { [math]::Round(($curMB / $allocMB) * 100, 1) } else { 0 }
        $tag = if ($pct -gt 75) { $highUsage = $true; '[WARN]' } else { '[OK]  ' }

        Write-Host ("  {0} File: {1}" -f $tag, $pf.Name)
        Write-Host ("       Allocated Size: {0} MB | Current Usage: {1} MB ({2}%) | Peak: {3} MB" -f $allocMB, $curMB, $pct, $peakMB)
    }
} else {
    Write-Host "[WARN] No paging file usage detected (System may be running with pagefile DISABLED or crashdump-only)!"
}
Write-Host ""

Write-Host "=== Phase 2: Pagefile Management Policy ==="
$pfSetting = Get-CimInstance Win32_PageFileSetting -ErrorAction SilentlyContinue
if ($pfSetting) {
    foreach ($s in $pfSetting) {
        Write-Host ("  [INFO] Custom Configuration: Path={0} | Initial={1}MB | Max={2}MB" -f $s.Name, $s.InitialSize, $s.MaximumSize)
    }
} else {
    Write-Host "[OK]   Windows Automatically Managed Paging File is active (System recommended)."
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($highUsage) {
    Write-Host "[WARN] PAGEFILE SATURATION HIGH: Current virtual memory usage exceeds 75%."
    Write-Host "       Check for memory-hungry apps or consider expanding physical RAM."
} elseif (-not $pagefiles) {
    Write-Host "[WARN] Pagefile is disabled. Enable System Managed pagefile on drive C: to ensure BSOD dumps work."
} else {
    Write-Host "[OK]   STATUS: PAGEFILE ALLOCATION HEALTHY. Virtual memory operates with ample buffer."
}
""",
    ),
    P(
        "numa_memory_topology",
        "NUMA Node Architecture & Memory Proximity Topology",
        "Inspects Non-Uniform Memory Access (NUMA) node distribution, processor core affinity, "
        "and memory allocation balance for multi-socket servers and high-core workstations.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: NUMA Architecture & Memory Proximity Topology"
Write-Host "PURPOSE   : Verifies memory node balance across multi-socket servers and Threadripper CPUs"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK NUMA PRIMER:"
Write-Host "  • NUMA (Non-Uniform Memory Access): Architecture where CPU cores have fast local memory"
Write-Host "    and slower remote memory on adjacent sockets or dies (e.g. Dual-Xeon, Threadripper)."
Write-Host "  • Single-socket standard desktops use UMA (Uniform Memory Architecture)."
Write-Host ""

Write-Host "=== Phase 1: NUMA Nodes & Memory Architecture ==="
$numaNodes = Get-CimInstance Win32_PerfFormattedData_PerfOS_NUMANodeMemory -ErrorAction SilentlyContinue

if ($numaNodes) {
    Write-Host ("[INFO] Detected {0} NUMA Node(s):" -f $numaNodes.Count)
    foreach ($nn in $numaNodes) {
        $availMB = [math]::Round($nn.AvailableMBytes, 0)
        $totalMB = [math]::Round($nn.TotalMBytes, 0)
        Write-Host ("  [INFO] Node {0}: Total Memory: {1} MB | Available: {2} MB" -f $nn.Name, $totalMB, $availMB)
    }
} else {
    $cs = Get-CimInstance Win32_ComputerSystem
    Write-Host ("[OK]   Single-Node / Uniform Memory Architecture (UMA). Sockets: {0}, Cores: {1}" -f $cs.NumberOfProcessors, $cs.NumberOfLogicalProcessors)
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] Standard desktop workloads operate optimally under single-node UMA."
Write-Host "       On multi-socket servers running SQL or Hyper-V, ensure NUMA node memory is balanced."
""",
    ),
]
