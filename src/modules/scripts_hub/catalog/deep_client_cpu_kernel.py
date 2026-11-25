"""Client CPU Heterogeneous Scheduling, Memory Compression, NDIS Filters & Deadlock Diagnostics. Read-only.

Target: Standalone Windows Client (Windows 10 / 11, Workgroup/Home/Gaming/Power-user).
Strictly NO Active Directory, NO Entra ID, NO Intune.
"""
from ._base import CHK, L, T, maker, need_admin

P = maker("Performance")
NET = maker("Network")
SP = maker("Services & processes")

SCRIPTS = [
    P(
        "cpu_heterogeneous_core_director",
        "Heterogeneous CPU Scheduler, Thread Director & 3D V-Cache Core Parking",
        "Audits Intel P/E-core Thread Director scheduling, AMD Ryzen 3D V-Cache dual-CCD core parking, "
        "CPPC2 preferred core rankings, and game thread affinity routing.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Heterogeneous CPU Scheduler, Thread Director & 3D V-Cache Core Parking"
Write-Host "PURPOSE   : Audits Intel P/E hybrid scheduling, AMD 3D V-Cache parking & CPPC2"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: ASYMMETRIC CPU SCHEDULING & CORE PARKING"
Write-Host "  • Intel Thread Director (ITD): Intel 12th/13th/14th Gen and Core Ultra CPUs combine"
Write-Host "    high-performance P-cores (with SMT) and high-efficiency E-cores (no SMT)."
Write-Host "    Windows 11 coordinates with on-die hardware feedback (EHFI) to dispatch"
Write-Host "    real-time game rendering to P-cores and background tasks to E-cores."
Write-Host "  • AMD Ryzen 3D V-Cache Dual-CCD Architecture: 7900X3D/7950X3D/9900X3D/9950X3D chips"
Write-Host "    feature CCD0 with stacked 64MB 3D V-Cache (low latency, massive cache) and"
Write-Host "    CCD1 with standard silicon (higher clocks, 32MB cache). The AMD 3D V-Cache"
Write-Host "    Performance Optimizer driver must park all threads of CCD1 during gaming"
Write-Host "    so game threads do not cross the Infinity Fabric and incur cache penalties."
Write-Host "  • CPPC2 (Collaborative Processor Performance Control): Communicates individual"
Write-Host "    core silicon quality rankings directly from UEFI firmware to Windows scheduler."
Write-Host "  • Core Parking: PPM (Processor Power Management) dynamically places logical"
Write-Host "    cores into deep low-power sleep states, concentrating workloads on optimal cores."
Write-Host ""

Write-Host "=== Phase 1: CPU Topology & Heterogeneous Core Identification ==="
try {
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $name = if ($cpu.Name) { $cpu.Name.Trim() } else { "Unknown Processor" }
    $cores = [int]$cpu.NumberOfCores
    $logProc = [int]$cpu.NumberOfLogicalProcessors
    $l3Kb = [int]$cpu.L3CacheSize
    $l2Kb = [int]$cpu.L2CacheSize
    $maxMhz = [int]$cpu.MaxClockSpeed
    $socket = $cpu.SocketDesignation

    Write-Host ("  Processor Name    : {0}" -f $name)
    Write-Host ("  Physical Cores    : {0}" -f $cores)
    Write-Host ("  Logical Processors: {0}" -f $logProc)
    Write-Host ("  L3 Cache Capacity : {0:N0} KB ({1:N1} MB)" -f $l3Kb, ($l3Kb / 1024))
    Write-Host ("  L2 Cache Capacity : {0:N0} KB ({1:N1} MB)" -f $l2Kb, ($l2Kb / 1024))
    Write-Host ("  Socket / Max Clock: {0} @ {1} MHz" -f $socket, $maxMhz)
    Write-Host ""

    # Detect Intel Hybrid Architecture
    if ($name -match 'Intel') {
        if ($logProc -gt $cores -and $logProc -lt ($cores * 2)) {
            $pCores = $logProc - $cores
            $eCores = $cores - $pCores
            Write-Host ("  [OK]   Intel Hybrid Architecture Detected: Asymmetric P/E core topology active.")
            Write-Host ("         Estimated Breakdown: {0} P-cores ({1} threads with Hyper-Threading) + {2} E-cores (single-threaded)." -f $pCores, ($pCores * 2), $eCores)
            Write-Host ("         Intel Thread Director (ITD) / EHFI scheduling active in Windows 11.")
        } elseif ($logProc -eq ($cores * 2)) {
            Write-Host ("  [INFO] Intel Uniform SMT Architecture: All {0} cores have 2 threads ({1} logical processors)." -f $cores, $logProc)
        } else {
            Write-Host ("  [INFO] Intel Standard Architecture: {0} cores, {1} logical processors." -f $cores, $logProc)
        }
    }

    # Detect AMD Architecture & 3D V-Cache
    if ($name -match 'AMD') {
        if ($name -match 'X3D|3D') {
            Write-Host ("  [INFO] AMD 3D V-Cache Processor Detected: {0}" -f $name)
            if ($cores -gt 8) {
                Write-Host ("  [WARN] Dual-CCD Asymmetric Architecture Detected ({0} cores):" -f $cores)
                Write-Host ("         CCD0 contains 3D V-Cache; CCD1 contains standard frequency cores.")
                Write-Host ("         Requires active core parking during games to prevent Infinity Fabric latency hops!")
            } else {
                Write-Host ("  [OK]   Single-CCD 3D V-Cache Architecture: All {0} cores directly share the 3D V-Cache pool." -f $cores)
                Write-Host ("         Uniform cache latency across all cores. Asymmetric CCD parking is not required.")
            }
        } else {
            Write-Host ("  [INFO] AMD Standard Ryzen Architecture: {0} cores, {1} threads with uniform CCD layout." -f $cores, $logProc)
        }

        # Check AMD 3D V-Cache driver & optimization services
        $vcacheDriver = Join-Path $env:SystemRoot "System32\drivers\amd3dcache.sys"
        if (Test-Path $vcacheDriver) {
            Write-Host ("  [OK]   AMD 3D V-Cache Driver: Present ({0})" -f $vcacheDriver)
        } else {
            if ($name -match 'X3D|3D' -and $cores -gt 8) {
                Write-Host ("  [WARN] AMD 3D V-Cache Driver (amd3dcache.sys): Not found in System32\drivers!")
            }
        }

        $amdServices = Get-CimInstance Win32_Service -Filter "Name LIKE '%3D%Cache%' OR Name LIKE 'amd3d%' OR Name LIKE 'AmdPPM%'" -ErrorAction SilentlyContinue
        if ($amdServices) {
            foreach ($s in $amdServices) {
                $statusTag = if ($s.State -eq 'Running') { "[OK]  " } else { "[WARN]" }
                Write-Host ("  {0} Service: {1} (State: {2}, StartMode: {3})" -f $statusTag, $s.Name, $s.State, $s.StartMode)
            }
        } elseif ($name -match 'X3D|3D' -and $cores -gt 8) {
            Write-Host ("  [WARN] AMD 3D V-Cache Optimizer Service: No specialized AMD 3D V-Cache services detected.")
        }
    }
} catch {
    Write-Host ("  [INFO] CPU topology query note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "=== Phase 2: Core Parking & Processor Power Management (PPM) State ==="
try {
    $activeSchemeStr = & powercfg.exe /getactivescheme 2>&1
    Write-Host ("  Active Power Scheme: {0}" -f ($activeSchemeStr -join " ").Trim())

    # Query PPM Core Parking & Heterogeneous scheduling settings
    $procSubgroup = "54533251-82be-4824-96c1-47b60b740d00"
    $powerCfgOut = & powercfg.exe /qh SCHEME_CURRENT $procSubgroup 2>&1
    $powerText = $powerCfgOut -join "`n"

    # 1. Min Core Parking setting (0cc5b647-c1df-44e7-8840-70775440d044)
    if ($powerText -match '0cc5b647-c1df-44e7-8840-70775440d044[\s\S]*?Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)') {
        $minParkHex = $Matches[1]
        $minParkPct = [Convert]::ToInt32($minParkHex, 16)
        if ($minParkPct -ge 100) {
            Write-Host ("  [INFO] Core Parking Minimum Cores: {0}% (Core parking is disabled; all logical cores kept active)." -f $minParkPct)
            if ($name -match 'X3D|3D' -and $cores -gt 8) {
                Write-Host ("  [WARN] Dual-CCD X3D Alert: Disabling core parking prevents CCD1 from parking during games, causing micro-stutter!")
            }
        } else {
            Write-Host ("  [OK]   Core Parking Minimum Cores: {0}% (Core parking is enabled; unused cores can enter sleep states)." -f $minParkPct)
        }
    } else {
        Write-Host "  [INFO] Core Parking Min Cores setting: Hidden or system default."
    }

    # 2. Max Core Parking setting (ea062031-0e34-4ff1-9b6d-eb10593ac08f)
    if ($powerText -match 'ea062031-0e34-4ff1-9b6d-eb10593ac08f[\s\S]*?Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)') {
        $maxParkHex = $Matches[1]
        $maxParkPct = [Convert]::ToInt32($maxParkHex, 16)
        Write-Host ("  [INFO] Core Parking Maximum Cores: {0}% unparked allowance." -f $maxParkPct)
    }

    # 3. Heterogeneous Thread Scheduling Policy (7f2492b6-60b1-45e5-ae55-773f8f2ca038)
    if ($powerText -match '7f2492b6-60b1-45e5-ae55-773f8f2ca038[\s\S]*?Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)') {
        $schedPolicyHex = $Matches[1]
        $schedPolicy = [Convert]::ToInt32($schedPolicyHex, 16)
        $policyDesc = switch ($schedPolicy) {
            0 { "All processors (No preference)" }
            1 { "Performant processors only" }
            2 { "Prefer performant processors (Standard hybrid default)" }
            3 { "Efficient processors" }
            4 { "Automatic / Hardware feedback" }
            default { "Custom policy ($schedPolicy)" }
        }
        Write-Host ("  [OK]   Heterogeneous Thread Scheduling Policy: {0} (Setting: 0x{1:X})" -f $policyDesc, $schedPolicy)
    } else {
        Write-Host "  [INFO] Heterogeneous Thread Scheduling Policy: OS default active."
    }

    # 4. Heterogeneous Short Running Thread Policy (93b8b6dc-0646-436a-b649-1748d86086b8)
    if ($powerText -match '93b8b6dc-0646-436a-b649-1748d86086b8[\s\S]*?Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)') {
        $shortPolicyHex = $Matches[1]
        $shortPolicy = [Convert]::ToInt32($shortPolicyHex, 16)
        Write-Host ("  [INFO] Heterogeneous Short Running Thread Policy: 0x{0:X}" -f $shortPolicy)
    }
} catch {
    Write-Host ("  [INFO] Power scheme inspection note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "=== Phase 3: Collaborative Processor Performance Control (CPPC2) Status ==="
try {
    # Check processor driver services
    $ppmServices = Get-CimInstance Win32_Service -Filter "Name = 'amdppm' OR Name = 'intelpep'" -ErrorAction SilentlyContinue
    if ($ppmServices) {
        foreach ($s in $ppmServices) {
            $tag = if ($s.State -eq 'Running') { "[OK]  " } else { "[WARN]" }
            Write-Host ("  {0} Processor Driver Service '{1}': State={2}, StartMode={3}" -f $tag, $s.Name, $s.State, $s.StartMode)
        }
    } else {
        Write-Host "  [INFO] Standard processor driver active."
    }

    # Check CPPC Autonomous Mode in power settings
    $cppcGuid = "5d624f24-d2c0-4106-a0a8-d5402e5f8b0a"
    if ($powerText -match "$cppcGuid[\s\S]*?Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)") {
        $cppcVal = [Convert]::ToInt32($Matches[1], 16)
        if ($cppcVal -eq 1) {
            Write-Host "  [OK]   CPPC Autonomous Mode (HWP/CPPC2): Enabled (Firmware autonomous frequency selection active)."
        } else {
            Write-Host ("  [INFO] CPPC Autonomous Mode: Setting index 0x{0:X}" -f $cppcVal)
        }
    } else {
        Write-Host "  [INFO] CPPC Autonomous Mode: Controlled directly by UEFI ACPI tables and processor driver."
    }

    # Check Registry for CPPC overrides
    $powerReg = "HKLM:\SYSTEM\CurrentControlSet\Control\Power"
    $cppcOverride = (Get-ItemProperty -Path $powerReg -Name "CppcEnabled" -ErrorAction SilentlyContinue).CppcEnabled
    if ($null -ne $cppcOverride) {
        Write-Host ("  [INFO] CPPC Registry Override (CppcEnabled): {0}" -f $cppcOverride)
    } else {
        Write-Host "  [OK]   CPPC Registry: Default ACPI autonomous control (no restrictive overrides)."
    }
} catch {
    Write-Host ("  [INFO] CPPC inspection note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== Phase 4: HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "1. Intel 12th/13th/14th Gen / Core Ultra Hybrid Users:"
Write-Host "   • Windows 11 22H2/23H2/24H2 is strongly recommended over Windows 10 for hybrid CPUs."
Write-Host "     Windows 11 features complete Enhanced Hardware Feedback Interface (EHFI) integration,"
Write-Host "     ensuring gaming render threads remain pinned to high-IPC P-cores while background"
Write-Host "     tasks (Discord, OBS encoding, web browsers) run quietly on low-power E-cores."
Write-Host "2. AMD Ryzen Dual-CCD 3D V-Cache (7900X3D, 7950X3D, 9900X3D, 9950X3D):"
Write-Host "   • Ensure the latest official AMD Chipset Drivers are installed, including the"
Write-Host "     'AMD 3D V-Cache Performance Optimizer Driver' and 'AMD PPM Provisioning Service'."
Write-Host "   • Verify Windows Game Mode is enabled so Windows Game Bar automatically triggers"
Write-Host "     parking of CCD1 whenever a game process is running in the foreground."
Write-Host "   • In UEFI/BIOS: Ensure 'CPPC' is set to Auto/Enabled and 'CPPC Preferred Cores' is Auto/Driver."
Write-Host "   • DO NOT use registry core unparking utilities on dual-CCD X3D chips; unparking CCD1"
Write-Host "     forces game threads across the Infinity Fabric, causing 15-25% lower 1% low FPS."
Write-Host "3. AMD Single-CCD 3D V-Cache (7800X3D, 9800X3D, 5800X3D):"
Write-Host "   • All 8 cores sit on the single 3D V-Cache CCD. Core parking between CCDs is not needed."
Write-Host "   • Enable CPPC in BIOS to ensure Windows boosts the highest-binned silicon cores first."
"""),

    P(
        "memory_compression_store_profiler",
        "Memory Compression Store & Working Set Trimming Latency Analyzer",
        "Analyzes Windows Memory Manager compressed store size, compression ratio, "
        "pagefault thrashing, and MMAgent working set trimming policies.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Memory Compression Store & Working Set Trimming Latency Analyzer"
Write-Host "PURPOSE   : Analyzes compressed memory store, pagefault thrashing, & MMAgent"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: WINDOWS MEMORY COMPRESSION ARCHITECTURE"
Write-Host "  • Windows Store Manager (Memory Compression): Instead of writing inactive"
Write-Host "    pages to the disk pagefile (pagefile.sys), Windows compresses them in RAM"
Write-Host "    using LZ4/Xpress within the kernel System process (PID 4)."
Write-Host "  • Compression Ratio: Typically achieves 2:1 to 4:1 compression, effectively"
Write-Host "    increasing available physical RAM without storage I/O penalties."
Write-Host "  • Working Set Trimming: Windows regularly trims inactive pages from background"
Write-Host "    applications. Refocusing an application causes decompression (soft page faults)."
Write-Host "  • Latency & Hitching Trap: If memory compression thrashes during CPU-bound games"
Write-Host "    or real-time audio (DAW), CPU core decompression interrupts can cause micro-stutter."
Write-Host ""

Write-Host "=== Phase 1: Physical Memory Telemetry & Compressed Store Size ==="
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $totalRamGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
    $freeRamGb = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
    $usedRamGb = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB, 2)
    $usedPct = [math]::Round(($usedRamGb / $totalRamGb) * 100, 1)

    Write-Host ("  Total Physical RAM : {0} GB" -f $totalRamGb)
    Write-Host ("  Available RAM      : {0} GB" -f $freeRamGb)
    Write-Host ("  Used RAM           : {0} GB ({1}% utilized)" -f $usedRamGb, $usedPct)

    if ($usedPct -ge 90) {
        Write-Host "  [BAD]  Physical RAM is severely saturated (>90% in use)! High compression and paging pressure."
    } elseif ($usedPct -ge 80) {
        Write-Host "  [WARN] Physical RAM utilization is elevated (>80%). Background pages are being compressed."
    } else {
        Write-Host "  [OK]   Physical RAM headroom is healthy."
    }
    Write-Host ""

    # Check System process (PID 4) working set
    $sysProc = Get-Process -Id 4 -ErrorAction SilentlyContinue
    if ($sysProc) {
        $sysWsMb = [math]::Round($sysProc.WorkingSet64 / 1MB, 1)
        $sysPrivMb = [math]::Round($sysProc.PrivateMemorySize64 / 1MB, 1)
        Write-Host ("  System Kernel (PID 4) Working Set: {0} MB (Private: {1} MB)" -f $sysWsMb, $sysPrivMb)
    }

    # Query Memory Performance counters via CIM
    $perfMem = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction SilentlyContinue
    if ($perfMem) {
        $availMb = $perfMem.AvailableMBytes
        $cacheMb = [math]::Round($perfMem.CacheBytes / 1MB, 1)
        $faultsPerSec = $perfMem.PageFaultsPersec
        $pagesInPerSec = $perfMem.PagesInputPersec
        $pagesOutPerSec = $perfMem.PagesOutputPersec
        $nonPagedMb = [math]::Round($perfMem.PoolNonpagedBytes / 1MB, 1)
        $pagedMb = [math]::Round($perfMem.PoolPagedBytes / 1MB, 1)

        Write-Host ("  System Cache Size : {0} MB" -f $cacheMb)
        Write-Host ("  Paged Pool        : {0} MB" -f $pagedMb)
        Write-Host ("  Non-Paged Pool    : {0} MB" -f $nonPagedMb)
        Write-Host ("  Total Page Faults : {0:N0} / sec (includes soft faults & in-memory decompression)" -f $faultsPerSec)
        Write-Host ("  Disk Pages In     : {0:N0} / sec (hard faults requiring physical disk read)" -f $pagesInPerSec)
        Write-Host ("  Disk Pages Out    : {0:N0} / sec (pages written to pagefile)" -f $pagesOutPerSec)

        if ($pagesInPerSec -gt 150) {
            Write-Host "  [WARN] Elevated Hard Page Fault rate detected (>150/sec)! Applications are actively waiting on disk I/O."
        } else {
            Write-Host "  [OK]   Hard page fault rate is within low-latency operating parameters."
        }
    }
} catch {
    Write-Host ("  [INFO] Memory telemetry note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "=== Phase 2: Pagefile Allocation & Virtual Memory Saturation ==="
try {
    $pagefiles = Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue
    if ($pagefiles) {
        foreach ($pf in $pagefiles) {
            $baseMb = $pf.AllocatedBaseSize
            $curMb = $pf.CurrentUsage
            $peakMb = $pf.PeakUsage
            $pct = if ($baseMb -gt 0) { [math]::Round(($curMb / $baseMb) * 100, 1) } else { 0 }

            Write-Host ("  Pagefile Location : {0}" -f $pf.Name)
            Write-Host ("  Allocated Size    : {0:N0} MB" -f $baseMb)
            Write-Host ("  Current Usage     : {0:N0} MB ({1}%)" -f $curMb, $pct)
            Write-Host ("  Peak Usage        : {0:N0} MB" -f $peakMb)

            if ($pct -gt 75) {
                Write-Host "  [WARN] Pagefile utilization is above 75%! Virtual memory commit exhaustion risk."
            } else {
                Write-Host "  [OK]   Pagefile allocation and headroom are stable."
            }
        }
    } else {
        Write-Host "  [BAD]  No Active Pagefile Detected! Disabling pagefile causes abrupt application crashes on commit exhaustion and prevents BSOD kernel dumps."
    }

    # Virtual Memory Commit calculation
    $totalCommitMb = [math]::Round($os.TotalVirtualMemorySize / 1024, 0)
    $freeCommitMb = [math]::Round($os.FreeVirtualMemory / 1024, 0)
    $usedCommitMb = $totalCommitMb - $freeCommitMb
    $commitPct = [math]::Round(($usedCommitMb / $totalCommitMb) * 100, 1)

    Write-Host ""
    Write-Host ("  Virtual Memory Commit Limit: {0:N0} MB" -f $totalCommitMb)
    Write-Host ("  Committed Memory in Use    : {0:N0} MB ({1}%)" -f $usedCommitMb, $commitPct)

    if ($commitPct -ge 90) {
        Write-Host "  [BAD]  Commit Limit is critically saturated (>90%)! Applications may crash with STATUS_COMMITMENT_LIMIT."
    } elseif ($commitPct -ge 80) {
        Write-Host "  [WARN] Commit Limit is elevated (>80%). Monitor virtual memory allocations."
    } else {
        Write-Host "  [OK]   Virtual memory commit limit has sufficient headroom."
    }
} catch {
    Write-Host ("  [INFO] Pagefile inspection note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "=== Phase 3: Memory Manager Agent (MMAgent) Configuration ==="
try {
    $mm = Get-MMAgent -ErrorAction SilentlyContinue
    if ($mm) {
        # Memory Compression
        if ($mm.MemoryCompression) {
            Write-Host "  [OK]   Memory Compression      : ENABLED (In-RAM store compresses inactive pages to reduce SSD paging)."
        } else {
            Write-Host "  [INFO] Memory Compression      : DISABLED (Pages write directly to pagefile. No CPU compression overhead)."
        }

        # Page Combining
        if ($mm.PageCombining) {
            Write-Host "  [INFO] Page Combining          : ENABLED (Kernel hashes and deduplicates identical memory pages across processes)."
        } else {
            Write-Host "  [OK]   Page Combining          : DISABLED (No background page-hashing CPU overhead)."
        }

        # Application PreLaunch
        if ($mm.ApplicationPreLaunch) {
            Write-Host "  [INFO] Application PreLaunch   : ENABLED (Pre-launches common UWP/Store apps in background)."
        } else {
            Write-Host "  [OK]   Application PreLaunch   : DISABLED (Conserves RAM and background cycles)."
        }

        # Application Launch Profiling
        if ($mm.ApplicationLaunchProfiling) {
            Write-Host "  [INFO] App Launch Profiling    : ENABLED (Prefetch profiling active)."
        } else {
            Write-Host "  [INFO] App Launch Profiling    : DISABLED."
        }

        Write-Host ("  MaxOperationAPIFiles   : {0}" -f $mm.MaxOperationAPIFiles)
    } else {
        Write-Host "  [INFO] MMAgent settings could not be retrieved via PowerShell."
    }
} catch {
    Write-Host ("  [INFO] MMAgent query note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== Phase 4: HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "1. High-Spec Gaming & Audio Rig Guidance (32 GB - 64 GB+ RAM):"
Write-Host "   • If you observe micro-stuttering during high-framerate competitive gaming or buffer"
Write-Host "     underruns in low-latency DAW audio production, memory compression decompression"
Write-Host "     cycles may be contributing to DPC latency."
Write-Host "   • You can test toggling memory compression off using PowerShell:"
Write-Host "     run command: disable-mmagent -mc"
Write-Host "   • To save additional CPU hashing overhead, page combining can also be toggled off:"
Write-Host "     run command: disable-mmagent -pagecombining"
Write-Host "2. General & Moderate-Spec PC Guidance (8 GB - 16 GB RAM):"
Write-Host "   • Keep memory compression enabled! Compressing RAM in-memory is up to 100x faster than"
Write-Host "     paging to an NVMe SSD and 1,000x faster than a SATA SSD/HDD."
Write-Host "   • If disabled on 16GB systems, running a modern game alongside a browser will quickly"
Write-Host "     exhaust physical RAM and trigger harsh disk I/O thrashing."
Write-Host "3. Pagefile Best Practices:"
Write-Host "   • NEVER set the pagefile to 0 MB. Windows requires an active pagefile to back virtual"
Write-Host "     memory allocations and save kernel memory dumps in the event of a system crash."
Write-Host "   • Ensure the pagefile is located on your fastest NVMe solid-state drive."
"""),

    NET(
        "ndis_filter_driver_latency_hunter",
        "NDIS Lightweight Filter (LWF) Drivers & Network Stack Jitter Hunter",
        "Audits the NDIS filter driver stack across physical network adapters, "
        "detects third-party packet inspection filters (VPNs, QoS, capture drivers), and pinpoints latency jitter.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: NDIS Lightweight Filter (LWF) Drivers & Network Stack Jitter Hunter"
Write-Host "PURPOSE   : Audits NDIS filter bindings, third-party packet drivers, & TCP stack"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: NDIS FILTER DRIVER ARCHITECTURE & LATENCY"
Write-Host "  • NDIS Lightweight Filter (LWF): NDIS 6.x filter drivers insert themselves"
Write-Host "    directly between physical network adapter miniport drivers and the TCP/IP stack."
Write-Host "  • Modifying vs Monitoring Filters: Modifying filters (antivirus web protection,"
Write-Host "    VPN virtual taps, packet capture engines like Npcap, QoS shapers, and gaming"
Write-Host "    optimizers like cFosSpeed/ROG GameFirst) intercept, buffer, and inspect every packet."
Write-Host "  • Network Jitter & Ping Spikes: Each intermediate filter driver adds DPC serialization"
Write-Host "    delays. In competitive gaming and real-time streaming, multiple stacked filters"
Write-Host "    frequently introduce 10-50ms ping jitter spikes and sporadic packet drops."
Write-Host "  • Green Ethernet / EEE: Physical PHY power saving states that sleep during idle gaps"
Write-Host "    introduce 5-15ms wake latency to sporadic network traffic."
Write-Host ""

Write-Host "=== Phase 1: Physical Network Adapters & Third-Party NDIS Bindings ==="
try {
    $physAdapters = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -ne 'Disabled' }
    if ($physAdapters) {
        foreach ($ad in $physAdapters) {
            Write-Host ("Adapter: {0} ({1})" -f $ad.Name, $ad.InterfaceDescription)
            Write-Host ("  Status / Link Speed: {0} / {1}" -f $ad.Status, $ad.LinkSpeed)
            Write-Host ("  Driver Version     : {0} ({1})" -f $ad.DriverVersion, $ad.DriverDate)

            # Query bindings for this adapter
            $bindings = Get-NetAdapterBinding -Name $ad.Name -ErrorAction SilentlyContinue
            $thirdPartyActive = 0
            $thirdPartyInactive = 0

            foreach ($b in $bindings) {
                $cId = $b.ComponentID
                # Filter out standard Microsoft in-box protocol and filter IDs
                $isMs = ($cId -match '^ms_' -or $cId -eq 'ms_tcpip' -or $cId -eq 'ms_tcpip6' -or $cId -eq 'ms_server' -or $cId -eq 'ms_msclient' -or $cId -eq 'ms_pacer' -or $cId -eq 'ms_lltdio' -or $cId -eq 'ms_rspndr' -or $cId -eq 'ms_netbt' -or $cId -eq 'ms_implat' -or $cId -eq 'ms_pppoe')

                if (-not $isMs) {
                    if ($b.Enabled) {
                        $thirdPartyActive++
                        Write-Host ("  [WARN] Third-Party Filter ACTIVE  : {0} [{1}]" -f $b.DisplayName, $cId)
                    } else {
                        $thirdPartyInactive++
                        Write-Host ("  [INFO] Third-Party Filter Disabled: {0} [{1}]" -f $b.DisplayName, $cId)
                    }
                }
            }

            if ($thirdPartyActive -gt 0) {
                Write-Host ("  [WARN] {0} active third-party NDIS filter(s) detected on this adapter. Potential source of ping jitter." -f $thirdPartyActive)
            } else {
                Write-Host "  [OK]   Zero active third-party filter drivers on this adapter (clean Microsoft stack)."
            }
            Write-Host ""
        }
    } else {
        Write-Host "  [INFO] No active physical network adapters discovered."
    }
} catch {
    Write-Host ("  [INFO] Adapter binding query note: {0}" -f $_.Exception.Message)
}

Write-Host "=== Phase 2: NDIS Class Driver Inspection & Advanced NIC Properties ==="
try {
    # Inspect Registry for installed NDIS network filters
    $filterClassKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Network\{4d36e974-e325-11ce-bfc1-08002be10318}"
    if (Test-Path $filterClassKey) {
        $filterSubKeys = Get-ChildItem -Path $filterClassKey -ErrorAction SilentlyContinue
        $installedFilters = @()
        foreach ($k in $filterSubKeys) {
            $props = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
            if ($props.Description -and $props.Description -notmatch 'Virtual|Miniport') {
                $installedFilters += [PSCustomObject]@{
                    Name = $props.Description
                    Inf  = $props.InfPath
                    Id   = $props.MatchingDeviceId
                }
            }
        }
        if ($installedFilters.Count -gt 0) {
            Write-Host "  Installed System-Wide NDIS Network Filters:"
            foreach ($f in $installedFilters) {
                Write-Host ("    • {0} (INF: {1}, DeviceID: {2})" -f $f.Name, $f.Inf, $f.Id)
            }
        } else {
            Write-Host "  [OK]   Zero auxiliary system-wide NDIS filter packages registered."
        }
    }
    Write-Host ""

    # Inspect Advanced Properties on first physical adapter
    if ($physAdapters) {
        $primary = $physAdapters[0]
        Write-Host ("  Advanced NIC Properties for Primary Adapter '{0}':" -f $primary.Name)
        $advProps = Get-NetAdapterAdvancedProperty -Name $primary.Name -ErrorAction SilentlyContinue

        # 1. Energy Efficient Ethernet (EEE) / Green Ethernet
        $eee = $advProps | Where-Object { $_.DisplayName -match 'Energy Efficient|Green Ethernet|Gigabit Lite|Energy-Efficient' }
        if ($eee) {
            foreach ($item in $eee) {
                if ($item.DisplayValue -match 'Enabled|On|Auto') {
                    Write-Host ("  [WARN] {0}: {1} (May introduce 5-15ms wake latency spikes on idle network gaps)." -f $item.DisplayName, $item.DisplayValue)
                } else {
                    Write-Host ("  [OK]   {0}: {1} (Power-saving PHY sleep disabled for minimum latency)." -f $item.DisplayName, $item.DisplayValue)
                }
            }
        } else {
            Write-Host "  [INFO] Energy Efficient Ethernet: Not present or not exposed by driver."
        }

        # 2. Interrupt Moderation
        $intMod = $advProps | Where-Object { $_.DisplayName -match 'Interrupt Moderation' -and $_.DisplayName -notmatch 'Rate' }
        if ($intMod) {
            Write-Host ("  [INFO] Interrupt Moderation: {0}" -f $intMod.DisplayValue)
        }

        # 3. Receive Side Scaling (RSS)
        $rss = $advProps | Where-Object { $_.DisplayName -match 'Receive Side Scaling' }
        if ($rss) {
            if ($rss.DisplayValue -match 'Enabled') {
                Write-Host "  [OK]   Receive Side Scaling (RSS): ENABLED (Distributes network packet processing across multiple CPU cores)."
            } else {
                Write-Host "  [WARN] Receive Side Scaling (RSS): DISABLED (All network traffic processed on a single core)."
            }
        }

        # 4. Large Send Offload (LSO)
        $lso = $advProps | Where-Object { $_.DisplayName -match 'Large Send Offload' }
        if ($lso) {
            foreach ($l in $lso) {
                Write-Host ("  [INFO] {0}: {1}" -f $l.DisplayName, $l.DisplayValue)
            }
        }
    }
} catch {
    Write-Host ("  [INFO] Advanced property inspection note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "=== Phase 3: TCP Auto-Tuning, Congestion Control & Stack State ==="
try {
    $tcpSettings = Get-NetTCPSetting -SettingName Internet -ErrorAction SilentlyContinue
    if (-not $tcpSettings) {
        $tcpSettings = Get-NetTCPSetting -ErrorAction SilentlyContinue | Select-Object -First 1
    }

    if ($tcpSettings) {
        # Auto-Tuning Level
        $autoTuning = $tcpSettings.AutoTuningLevelLocal
        if ($autoTuning -eq 'Disabled') {
            Write-Host "  [BAD]  TCP Window Auto-Tuning is DISABLED! Receive window is capped at 64 KB, severely throttling gigabit broadband throughput."
        } elseif ($autoTuning -eq 'Normal') {
            Write-Host "  [OK]   TCP Window Auto-Tuning: Normal (RFC 1323 dynamic window scaling active)."
        } else {
            Write-Host ("  [INFO] TCP Window Auto-Tuning: {0}" -f $autoTuning)
        }

        # Congestion Provider
        Write-Host ("  Congestion Provider   : {0}" -f $tcpSettings.CongestionProvider)
        Write-Host ("  ECN Capability        : {0}" -f $tcpSettings.EcnCapability)
        Write-Host ("  Timestamps            : {0}" -f $tcpSettings.Timestamps)
        Write-Host ("  Initial RTO / Min RTO : {0} ms / {1} ms" -f $tcpSettings.InitialRto, $tcpSettings.MinRto)
    } else {
        Write-Host "  [INFO] NetTCPSetting profile could not be queried."
    }
} catch {
    Write-Host ("  [INFO] TCP stack inspection note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== Phase 4: HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "1. Eliminating Network Latency Jitter from Third-Party Filter Drivers:"
Write-Host "   • If ping spikes or packet drops occur during competitive gaming or voice calls,"
Write-Host "     open Network Connections (ncpa.cpl)."
Write-Host "   • Right-click your active physical network adapter, select Properties, and inspect"
Write-Host "     the protocol list."
Write-Host "   • Uncheck unnecessary third-party filter drivers such as cFosSpeed, old packet"
Write-Host "     capture filters (Npcap/WinPcap), VPN tap bindings, or third-party QoS drivers."
Write-Host "2. Disabling Network Adapter Energy-Saving Latency Traps:"
Write-Host "   • In Device Manager, expand Network Adapters, right-click your Ethernet/Wi-Fi NIC,"
Write-Host "     select Properties, and navigate to the Advanced tab."
Write-Host "   • Set 'Energy Efficient Ethernet', 'Green Ethernet', and 'Gigabit Lite' to Disabled."
Write-Host "     This prevents the physical transceiver from sleeping during brief idle gaps."
Write-Host "3. Restoring Clean TCP Window Auto-Tuning Defaults:"
Write-Host "   • If TCP Auto-Tuning was disabled by legacy speed-up guides, restore it to normal."
Write-Host "   • Run elevated console command: netsh int tcp set global autotuninglevel=normal"
"""),

    SP(
        "process_thread_waitchain_sniffer",
        "Process Thread Wait-Chain & Ghosted Window Deadlock Sniffer",
        "Sniffs user-mode process deadlocks, thread wait-chain states, "
        "and isolates resource locks causing '(Not Responding)' window hangs.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Process Thread Wait-Chain & Ghosted Window Deadlock Sniffer"
Write-Host "PURPOSE   : Sniffs thread wait-chains, ALPC deadlocks, & hung GUI applications"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: WAIT CHAIN TRAVERSAL & GHOSTED WINDOW ARCHITECTURE"
Write-Host "  • Ghosted Windows (Not Responding): When a Win32 GUI thread fails to pump its"
Write-Host "    message queue (GetMessage/DispatchMessage) for >= 5 seconds, the Desktop Window"
Write-Host "    Manager (DWM) replaces the window with a ghost window and appends '(Not Responding)'."
Write-Host "  • Wait Chain Traversal (WCT): A thread wait chain is an alternating sequence of"
Write-Host "    threads and synchronization objects where each thread waits for an object owned by another."
Write-Host "  • Common Deadlock & Hang Causes:"
Write-Host "    1. Mutex / Critical Section Lock: Thread A holds Lock 1 and waits on Lock 2; Thread B"
Write-Host "       holds Lock 2 and waits on Lock 1 (Deadlock)."
Write-Host "    2. ALPC / RPC Port Block: Thread invokes a synchronous local RPC call to a system"
Write-Host "       service (AudioSrv, CryptSvc, Spooler) that is stuck or unresponsive (LpcReceive wait)."
Write-Host "    3. Synchronous Storage I/O: Thread waits synchronously for disk reads on a frozen volume."
Write-Host "    4. Kernel Handle Exhaustion: App leaks handles approaching system limits."
Write-Host ""

Write-Host "=== Phase 1: Active GUI Windows & Ghosted / Hung Window Detection ==="
try {
    $guiProcs = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
    $hungList = @()
    $activeGuiCount = 0

    foreach ($p in $guiProcs) {
        $activeGuiCount++
        $title = if ($p.MainWindowTitle) { $p.MainWindowTitle } else { "(Untitled Window)" }
        $resp = $p.Responding

        if (-not $resp) {
            $hungList += $p
            Write-Host ("  [BAD]  GHOSTED / NOT RESPONDING: '{0}' (PID: {1})" -f $p.ProcessName, $p.Id)
            Write-Host ("         Window Title : '{0}'" -f $title)
            Write-Host ("         CPU Seconds  : {0:N1} s | Working Set: {1:N1} MB | Threads: {2}" -f $p.CPU, ($p.WorkingSet64 / 1MB), $p.Threads.Count)
        }
    }

    Write-Host ""
    if ($hungList.Count -gt 0) {
        Write-Host ("  [BAD]  {0} active GUI application(s) are currently in a hung / Not Responding state!" -f $hungList.Count)
    } else {
        Write-Host ("  [OK]   All {0} active top-level GUI applications are responding normally (zero ghosted windows)." -f $activeGuiCount)
    }
} catch {
    Write-Host ("  [INFO] GUI window responsiveness check note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "=== Phase 2: Thread Wait Reason & Kernel State Profiler ==="
try {
    $waitMap = @{
        0  = "Executive (Kernel Event/Mutex)"
        1  = "FreePage"
        2  = "PageIn (Disk Page Fault)"
        3  = "SystemAllocation"
        4  = "ExecutionDelay (Sleep/Yield)"
        5  = "Suspended (Debugged/Frozen)"
        6  = "UserRequest (Waiting for Input)"
        7  = "EventPairHigh"
        8  = "EventPairLow"
        9  = "LpcReceive (Waiting for ALPC RPC Reply)"
        10 = "LpcReply"
        11 = "VirtualMemory"
        12 = "PageOut"
        13 = "Unknown"
    }

    $stateMap = @{
        0 = "Initialized"
        1 = "Ready"
        2 = "Running"
        3 = "Standby"
        4 = "Terminated"
        5 = "Waiting"
        6 = "Transition"
        7 = "DeferredReady"
    }

    # If hung processes exist, inspect their specific threads
    if ($hungList.Count -gt 0) {
        Write-Host "  Detailed Thread Analysis for Hung Application(s):"
        foreach ($hp in $hungList) {
            Write-Host ("    Process: {0} (PID: {1})" -f $hp.ProcessName, $hp.Id)
            $threads = Get-CimInstance Win32_Thread -Filter "ProcessHandle = '$($hp.Id)'" -ErrorAction SilentlyContinue
            if ($threads) {
                foreach ($t in $threads) {
                    $stName = if ($stateMap.ContainsKey([int]$t.ThreadState)) { $stateMap[[int]$t.ThreadState] } else { $t.ThreadState }
                    $wrName = if ($waitMap.ContainsKey([int]$t.ThreadWaitReason)) { $waitMap[[int]$t.ThreadWaitReason] } else { $t.ThreadWaitReason }

                    $tTag = "[INFO]"
                    if ($wrName -match 'LpcReceive') {
                        $tTag = "[WARN] Blocked on ALPC Reply: "
                    } elseif ($stName -eq 'Waiting' -and $wrName -match 'Executive') {
                        $tTag = "[WARN] Blocked on Mutex/Sync: "
                    }
                    Write-Host ("      Thread {0,6}: State={1,-10} WaitReason={2} {3}" -f $t.Handle, $stName, $wrName, $tTag)
                }
            }
        }
        Write-Host ""
    }

    # System-wide thread census for anomalous blocked states
    Write-Host "  System-Wide Thread State Census:"
    $allThreads = Get-CimInstance Win32_Thread -ErrorAction SilentlyContinue
    if ($allThreads) {
        $totalThreads = $allThreads.Count
        $lpcWaitCount = ($allThreads | Where-Object { $_.ThreadWaitReason -eq 9 }).Count
        $suspendedCount = ($allThreads | Where-Object { $_.ThreadWaitReason -eq 5 }).Count
        $pageInCount = ($allThreads | Where-Object { $_.ThreadWaitReason -eq 2 }).Count

        Write-Host ("    Total System Threads       : {0:N0}" -f $totalThreads)
        Write-Host ("    Threads in LpcReceive (RPC): {0:N0}" -f $lpcWaitCount)
        Write-Host ("    Threads Suspended          : {0:N0}" -f $suspendedCount)
        Write-Host ("    Threads in PageIn (Disk)   : {0:N0}" -f $pageInCount)

        if ($pageInCount -gt 25) {
            Write-Host "  [WARN] Elevated thread count waiting on disk page-ins. Storage subsystem may be bottlenecked."
        } else {
            Write-Host "  [OK]   Storage page-in wait counts are within normal thresholds."
        }
    }
} catch {
    Write-Host ("  [INFO] Thread profiling note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "=== Phase 3: Top Resource Consumers & Kernel Handle Safety ==="
try {
    # 1. Top Handle Consumers (check for handle leaks)
    $allProcs = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Id -gt 4 }
    $topHandles = $allProcs | Sort-Object HandleCount -Descending | Select-Object -First 5
    Write-Host "  Top 5 Processes by Kernel Handle Count:"
    $handleLeakFound = $false

    foreach ($p in $topHandles) {
        $hCount = [int]$p.HandleCount
        $hTag = "[OK]  "
        if ($hCount -ge 20000) {
            $hTag = "[BAD] "
            $handleLeakFound = $true
        } elseif ($hCount -ge 10000) {
            $hTag = "[WARN]"
            $handleLeakFound = $true
        }
        Write-Host ("    {0} {1,-8} {2,-10:N0} handles  {3}" -f $hTag, $p.Id, $hCount, $p.ProcessName)
    }

    if ($handleLeakFound) {
        Write-Host "  [WARN] One or more processes exceed 10,000 handles. Monitor for persistent handle leak growth."
    } else {
        Write-Host "  [OK]   Kernel handle allocations are well below warning thresholds."
    }
    Write-Host ""

    # 2. Top CPU Consumers
    $topCpu = $allProcs | Where-Object { $_.CPU -gt 0 } | Sort-Object CPU -Descending | Select-Object -First 5
    Write-Host "  Top 5 Processes by Total CPU Execution Time:"
    foreach ($p in $topCpu) {
        Write-Host ("    • PID {0,-8} {1,8:N1} s CPU  {2}" -f $p.Id, $p.CPU, $p.ProcessName)
    }
} catch {
    Write-Host ("  [INFO] Resource contention check note: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== Phase 4: HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "1. How to Isolate a Hung / '(Not Responding)' Application:"
Write-Host "   • Open Windows Task Manager (Ctrl + Shift + Esc) and navigate to the Details tab."
Write-Host "   • Right-click the unresponsive process and select 'Analyze wait chain'."
Write-Host "   • The dialog displays the exact root thread or process tree that is holding the"
Write-Host "     synchronization lock or socket needed by the hung application."
Write-Host "2. Troubleshooting ALPC / LpcReceive Hangs:"
Write-Host "   • If an application is stuck with threads in LpcReceive, it is waiting for an ALPC"
Write-Host "     message reply from an underlying Windows service or local RPC endpoint."
Write-Host "   • Common culprits include an unresponsive Windows Audio service (AudioSrv),"
Write-Host "     Print Spooler (Spooler), or Cryptographic Services (CryptSvc)."
Write-Host "   • Open services.msc to check if dependent services are responsive."
Write-Host "3. Safe Termination of Deadlocked Processes:"
Write-Host "   • If an application is deadlocked on a closed mutex or infinite loop,"
Write-Host "     terminate only the specific worker process via Task Manager rather than force-killing"
Write-Host "     the entire user session, preserving other active application data."
"""),
]
