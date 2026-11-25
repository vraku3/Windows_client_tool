"""Deep Client Diagnostics: Desktop Heap / GDI Leaks, User Profile Forensics, and Boot Profiler. Read-only."""
from ._base import maker

SP = maker("Services & processes")
UG = maker("Users & groups")
ST = maker("Startup & tasks")

SCRIPTS = [
    SP(
        "desktop_heap_gdi_leak_hunter",
        "Desktop Heap & GDI / User Handle Leak Sentinel",
        "Monitors per-process GDI and User handle allocations, detects GUI resource leaks approaching the 10,000 handle ceiling, and isolates culprits causing black windows or rendering crashes.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Desktop Heap & GDI / USER Handle Leak Sentinel"
Write-Host "PURPOSE   : Detects GUI object leaks, handles approaching the 10,000 limit, & heap exhaustion"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: DESKTOP HEAP & GDI ALLOCATION ARCHITECTURE"
Write-Host "  • GDI & USER Object Ceiling: Windows win32k subsystem imposes a hard limit of 10,000"
Write-Host "    GDI objects (bitmaps, brushes, fonts, device contexts) and 10,000 USER objects"
Write-Host "    (windows, menus, hooks) per process (HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows)."
Write-Host "  • Failure Symptom: When an application leaks GUI handles towards the 10,000 ceiling,"
Write-Host "    GDI allocations silently fail: windows open completely pitch black or invisible,"
Write-Host "    UI controls stop rendering, text disappears, or apps crash with STATUS_NO_MEMORY (0xC0000017)."
Write-Host "  • Desktop Heap: Shared memory pool allocated to each desktop (interactive WinSta0\Default"
Write-Host "    vs non-interactive service sessions). Exhausting desktop heap causes system-wide GUI failure."
Write-Host "  • Common Culprits: Electron apps (Discord, Teams, Slack), hardware monitor overlays,"
Write-Host "    leaking browser helper processes, and unmanaged legacy WPF/WinForms rendering loops."
Write-Host ""

Write-Host "=== Phase 1: High Handle Count & Resource Allocation Process Census ==="
try {
    $allProcs = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Id -gt 4 }
    $topHandles = $allProcs | Sort-Object HandleCount -Descending | Select-Object -First 15
    $highCount = 0
    $badCount = 0

    Write-Host ("  {0,-7} {1,-8} {2,-12} {3,-12} {4,-10} {5}" -f "Status", "PID", "Handles", "WorkingSet", "Threads", "Process Name")
    Write-Host ("  {0,-7} {1,-8} {2,-12} {3,-12} {4,-10} {5}" -f "-------", "---", "-------", "----------", "-------", "------------")

    foreach ($p in $topHandles) {
        $hCount = [int]$p.HandleCount
        $wsMB = [math]::Round($p.WorkingSet64 / 1MB, 1)
        $threads = [int]$p.Threads.Count
        $pName = $p.ProcessName

        $tag = "[OK]  "
        if ($hCount -ge 5000) {
            $tag = "[BAD] "
            $badCount++
        } elseif ($hCount -ge 1500) {
            $tag = "[WARN]"
            $highCount++
        }

        Write-Host ("  {0,-7} {1,-8} {2,-12:N0} {3,-10:N1} MB {4,-10} {5}" -f $tag, $p.Id, $hCount, $wsMB, $threads, $pName)
    }

    Write-Host ""
    if ($badCount -gt 0) {
        Write-Host ("[BAD]  {0} process(es) are consuming over 5,000 handles (severe handle pressure)!" -f $badCount)
    } elseif ($highCount -gt 0) {
        Write-Host ("[WARN] {0} process(es) exceed 1,500 handles. Monitor for continuous handle leak growth." -f $highCount)
    } else {
        Write-Host "[OK]   All running processes are operating well below elevated handle thresholds."
    }
} catch {
    Write-Host "[INFO] Process census note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: Desktop Heap Registry Configuration (SharedSection) ==="
$subsystemsKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\SubSystems'
try {
    $props = Get-ItemProperty -Path $subsystemsKey -ErrorAction Stop
    $winVal = $props.Windows
    if ($winVal -match 'SharedSection=([0-9]+),([0-9]+),([0-9]+)') {
        $sysHeap = $Matches[1]
        $interactHeap = $Matches[2]
        $nonInteractHeap = $Matches[3]

        Write-Host ("Raw Subsystem Configuration: SharedSection={0},{1},{2}" -f $sysHeap, $interactHeap, $nonInteractHeap)
        Write-Host ("  • System-Wide Global Heap    : {0} KB" -f $sysHeap)
        Write-Host ("  • Interactive Desktop Heap   : {0} KB ({1:N1} MB)" -f $interactHeap, ([int]$interactHeap / 1024))
        Write-Host ("  • Non-Interactive Heap (Svc) : {0} KB ({1:N1} MB)" -f $nonInteractHeap, ([int]$nonInteractHeap / 1024))

        if ([int]$interactHeap -lt 12288) {
            Write-Host ("[WARN] Interactive Desktop Heap ({0} KB) is lower than the recommended 20480 KB default on x64." -f $interactHeap)
        } else {
            Write-Host ("[OK]   Interactive Desktop Heap ({0} KB) is adequately sized for modern multi-window workloads." -f $interactHeap)
        }
    } else {
        Write-Host "[INFO] SharedSection parameter could not be extracted from SubSystems\Windows value."
    }
} catch {
    Write-Host "[INFO] SubSystems registry check note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($badCount -gt 0) {
    Write-Host "[BAD]  RESOURCE EXHAUSTION RISK: One or more processes are hoarding handles near critical limits."
    Write-Host "       1. Check the top processes flagged with [BAD] above."
    Write-Host "       2. In Task Manager -> Details tab, right-click columns, click 'Select columns',"
    Write-Host "          and check 'GDI objects', 'USER objects', and 'Handles'."
    Write-Host "       3. If GDI or USER objects for a process climb continuously without plateauing, terminate the process."
    Write-Host "       4. Check for overlay software (Discord overlay, RivaTuner, GPU game bars) hook conflicts."
} elseif ($highCount -gt 0) {
    Write-Host "[WARN] ELEVATED HANDLES OBSERVED: Monitor processes flagged with [WARN] during heavy multitasking."
    Write-Host "       If black windows occur in browsers or Electron apps, inspect GPU hardware acceleration settings."
} else {
    Write-Host "[OK]   STATUS: DESKTOP HEAP & HANDLE HEALTHY. No critical GUI or handle leaks detected."
}
""",
    ),
    UG(
        "local_profile_registry_hive_forensics",
        "Local User Profile Hive & Temporary Profile (.bak) Forensics",
        "Scans HKLM ProfileList for corrupted .bak profile keys, identifies processes locking NTUSER.DAT or UsrClass.dat, and detects bloated local profile cache footprints.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Local User Profile Hive & Temporary Profile (.bak) Forensics"
Write-Host "PURPOSE   : Audits ProfileList for corrupted .bak keys, hive file health, & local bloat"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: WINDOWS USER PROFILE & .BAK RECOVERY MECHANICS"
Write-Host "  • Profile Mounting: When a user signs in, User Profile Service (ProfSvc) reads"
Write-Host "    HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\<User-SID> and loads"
Write-Host "    NTUSER.DAT into HKCU, and UsrClass.dat into HKCU\Software\Classes."
Write-Host "  • The .bak Renaming Bug: If an unclosed process (third-party antivirus, backup agent,"
Write-Host "    file indexer) holds an exclusive lock on NTUSER.DAT at sign-in, ProfSvc fails to mount the hive."
Write-Host "  • Temporary Profile: Windows appends '.bak' to the user SID key and creates a temporary key"
Write-Host "    pointing to C:\Users\TEMP. The user receives: 'You've been signed in with a temporary profile',"
Write-Host "    desktop icons appear missing, and Application Event 1511 / 1515 is logged."
Write-Host "  • Safe Recovery: Requires releasing locks, deleting the temporary SID key, and renaming the .bak key."
Write-Host ""

Write-Host "=== Phase 1: ProfileList Registry & Orphaned .bak Key Audit ==="
$profileListPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList'
$orphanedBakCount = 0
$corruptProfileCount = 0
$userProfiles = @()

try {
    $subkeys = Get-ChildItem -Path $profileListPath -ErrorAction Stop
    foreach ($k in $subkeys) {
        $sid = $k.PSChildName
        # Skip service accounts: S-1-5-18 (System), S-1-5-19 (Local Service), S-1-5-20 (Network Service)
        if ($sid -match '^S-1-5-(18|19|20)$') { continue }

        $props = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
        $imagePath = $props.ProfileImagePath
        $state = $props.State
        $flags = $props.Flags

        $isBak = $sid.EndsWith('.bak')
        $pathExists = if ($imagePath) { Test-Path -Path $imagePath } else { $false }

        $tag = "[OK]  "
        if ($isBak) {
            $tag = "[BAD] "
            $orphanedBakCount++
        } elseif (-not $pathExists) {
            $tag = "[BAD] "
            $corruptProfileCount++
        } elseif ($state -and ($state -band 0x0100)) {
            $tag = "[BAD] "
            $corruptProfileCount++
        } elseif ($state -and $state -ne 0) {
            $tag = "[WARN]"
        }

        Write-Host ("  {0} SID: {1}" -f $tag, $sid)
        Write-Host ("        Path   : {0} [Exists: {1}]" -f $imagePath, $pathExists)
        Write-Host ("        State  : {0} | Flags: {1}" -f $state, $flags)

        if ($isBak) {
            Write-Host ("        [CRITICAL] Orphaned .bak key detected! Profile is in temporary/recovery state.")
        }
        if (-not $pathExists -and $imagePath) {
            Write-Host ("        [CRITICAL] ProfileImagePath does not exist on disk!")
        }

        if ($pathExists -and -not $isBak) {
            $userProfiles += [PSCustomObject]@{
                SID = $sid
                Path = $imagePath
                State = $state
            }
        }
    }
} catch {
    Write-Host "[INFO] ProfileList query note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: User Registry Hive Files & Lock Status ==="
foreach ($u in $userProfiles) {
    $uPath = $u.Path
    Write-Host ("Checking profile hive files in: {0}" -f $uPath)

    # 1. NTUSER.DAT
    $ntuserPath = Join-Path -Path $uPath -ChildPath "NTUSER.DAT"
    if (Test-Path -Path $ntuserPath) {
        try {
            $f = Get-Item -Path $ntuserPath -Force -ErrorAction Stop
            $sizeMB = [math]::Round($f.Length / 1MB, 2)
            Write-Host ("  [OK]   NTUSER.DAT exists ({0} MB, modified {1:yyyy-MM-dd HH:mm})" -f $sizeMB, $f.LastWriteTime)
        } catch {
            Write-Host ("  [WARN] NTUSER.DAT exists but access was restricted: {0}" -f $_.Exception.Message)
        }
    } else {
        Write-Host ("  [BAD]  NTUSER.DAT is MISSING from user directory: {0}" -f $ntuserPath)
    }

    # 2. UsrClass.dat
    $usrClassPath = Join-Path -Path $uPath -ChildPath "AppData\Local\Microsoft\Windows\UsrClass.dat"
    if (Test-Path -Path $usrClassPath) {
        try {
            $uf = Get-Item -Path $usrClassPath -Force -ErrorAction Stop
            $uSizeMB = [math]::Round($uf.Length / 1MB, 2)
            Write-Host ("  [OK]   UsrClass.dat exists ({0} MB, modified {1:yyyy-MM-dd HH:mm})" -f $uSizeMB, $uf.LastWriteTime)
        } catch {
            Write-Host ("  [WARN] UsrClass.dat exists but access was restricted: {0}" -f $_.Exception.Message)
        }
    } else {
        Write-Host ("  [INFO] UsrClass.dat not present at standard subpath (may be created on first app launch).")
    }
}
Write-Host ""

Write-Host "=== Phase 3: Profile Service (ProfSvc) Event Log Triage ==="
try {
    $profEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Application'
        ProviderName = 'Microsoft-Windows-User Profiles Service'
        Id = 1500, 1502, 1508, 1511, 1515
        StartTime = (Get-Date).AddDays(-14)
    } -MaxEvents 20 -ErrorAction Stop

    Write-Host ("[WARN] Found {0} Profile Service warning/error event(s) in the last 14 days:" -f $profEvents.Count)
    foreach ($evt in $profEvents) {
        $desc = ($evt.Message -split "`r?`n")[0]
        Write-Host ("  [BAD] [{0:yyyy-MM-dd HH:mm}] Event {1}: {2}" -f $evt.TimeCreated, $evt.Id, $desc)
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero temporary profile or ProfSvc error events in the last 14 days."
    } else {
        Write-Host "[INFO] ProfSvc event query note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 4: Local Profile Bloat & Temp Staging Footprint ==="
foreach ($u in $userProfiles) {
    $tempPath = Join-Path -Path $u.Path -ChildPath "AppData\Local\Temp"
    if (Test-Path -Path $tempPath) {
        try {
            $items = Get-ChildItem -Path $tempPath -Force -Recurse -File -ErrorAction SilentlyContinue
            $count = $items.Count
            $totalBytes = ($items | Measure-Object -Property Length -Sum).Sum
            $sizeMB = [math]::Round($totalBytes / 1MB, 1)

            $tag = "[OK]  "
            if ($sizeMB -gt 5120) {
                $tag = "[WARN]"
            }

            Write-Host ("  {0} User: {1} | Temp Cache: {2:N0} MB across {3:N0} files" -f $tag, (Split-Path $u.Path -Leaf), $sizeMB, $count)
        } catch {
            Write-Host ("  [INFO] User Temp measurement note: {0}" -f $_.Exception.Message)
        }
    }
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($orphanedBakCount -gt 0) {
    Write-Host "[BAD]  TEMPORARY PROFILE (.BAK) DETECTED: A user account is failing to load its genuine profile!"
    Write-Host "       RECOVERY PROCEDURE FOR HELPDESK:"
    Write-Host "       1. Reboot the PC to release all file locks on NTUSER.DAT."
    Write-Host "       2. Sign in with a separate local administrator account (not the affected account)."
    Write-Host "       3. Open regedit and browse to: HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
    Write-Host "       4. Check the SID without .bak (pointing to C:\Users\TEMP) and delete that temporary registry key."
    Write-Host "       5. Rename the .bak key by stripping off the '.bak' suffix."
    Write-Host "       6. In the renamed key, update the 'State' DWORD value to 0."
    Write-Host "       7. Sign out and log back in as the user. The genuine profile will load cleanly."
} elseif ($corruptProfileCount -gt 0) {
    Write-Host "[BAD]  CORRUPT OR MISSING PROFILE DIRECTORY DETECTED: Check Phase 1 paths."
} else {
    Write-Host "[OK]   STATUS: USER PROFILE HIVES HEALTHY. No .bak keys, locked hives, or temporary profiles detected."
}
""",
    ),
    ST(
        "startup_boot_phase_delay_profiler",
        "Windows Boot Phase Delay & Startup Bottleneck Profiler",
        "Parses Microsoft-Windows-Diagnostics-Performance event logs (Event IDs 100-110) to attribute boot degradation: breaks down MainPathBootTime vs PostBootTime and isolates the slowest startup applications.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Windows Boot Phase Delay & Startup Bottleneck Profiler"
Write-Host "PURPOSE   : Attributes boot time breakdown (MainPath vs PostBoot) & identifies slowest apps"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: WINDOWS BOOT DIAGNOSTICS TELEMETRY ARCHITECTURE"
Write-Host "  • Diagnostics-Performance Telemetry: Windows records detailed boot performance in the"
Write-Host "    Microsoft-Windows-Diagnostics-Performance/Operational log via event ID 100."
Write-Host "  • MainPathBootTime: Duration from kernel initialization until desktop shell appears"
Write-Host "    (Firmware, kernel init, smss, winlogon, driver loading). Target on modern SSD: < 20s."
Write-Host "  • BootPostBootTime: Duration from desktop appearance until system reaches 80% idle"
Write-Host "    (loading autostart tray applications, background services). Target: < 30s."
Write-Host "  • Degradation Events: Specific culprits delaying startup are categorized by Event ID:"
Write-Host "    - Event ID 101: Application startup delay (a specific program hung or took excessive time)."
Write-Host "    - Event ID 102: Driver degradation during startup (slow device driver initialization)."
Write-Host "    - Event ID 103: Service initialization delay (a background service delayed boot)."
Write-Host "    - Event ID 108: Post-boot application degradation (slow shell response)."
Write-Host ""

$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdm) {
    Write-Host "[WARN] NOTE: Querying Microsoft-Windows-Diagnostics-Performance requires administrator elevation."
    Write-Host "       Some boot degradation metrics may be inaccessible in standard user mode."
    Write-Host ""
}

Write-Host "=== Phase 1: Recent Boot Performance Metrics (Event ID 100) ==="
$logName = 'Microsoft-Windows-Diagnostics-Performance/Operational'
$bootList = @()

try {
    $events = Get-WinEvent -FilterHashtable @{
        LogName = $logName
        Id = 100
        StartTime = (Get-Date).AddDays(-30)
    } -MaxEvents 5 -ErrorAction Stop

    Write-Host ("  {0,-20} {1,-14} {2,-16} {3,-16} {4}" -f "Boot Date/Time", "Total Boot", "MainPath (Kernel)", "PostBoot (Shell)", "Status")
    Write-Host ("  {0,-20} {1,-14} {2,-16} {3,-16} {4}" -f "-------------------", "----------", "-----------------", "----------------", "------")

    foreach ($evt in $events) {
        $xml = [xml]$evt.ToXml()
        $dataNodes = $xml.Event.EventData.Data
        $bootDur = ($dataNodes | Where-Object { $_.Name -eq 'BootDuration' } | Select-Object -ExpandProperty '#text')
        $mainPath = ($dataNodes | Where-Object { $_.Name -eq 'MainPathBootTime' } | Select-Object -ExpandProperty '#text')
        $postBoot = ($dataNodes | Where-Object { $_.Name -eq 'BootPostBootTime' } | Select-Object -ExpandProperty '#text')

        $durSec = if ($bootDur) { [math]::Round([int64]$bootDur / 1000, 1) } else { 0 }
        $mainSec = if ($mainPath) { [math]::Round([int64]$mainPath / 1000, 1) } else { 0 }
        $postSec = if ($postBoot) { [math]::Round([int64]$postBoot / 1000, 1) } else { 0 }

        $tag = "[OK]  "
        if ($durSec -ge 120) {
            $tag = "[BAD] "
        } elseif ($durSec -ge 60) {
            $tag = "[WARN]"
        }

        Write-Host ("  {0,-20:yyyy-MM-dd HH:mm} {1,-14} {2,-16} {3,-16} {4}" -f $evt.TimeCreated, ("{0:N1}s" -f $durSec), ("{0:N1}s" -f $mainSec), ("{0:N1}s" -f $postSec), $tag)
        $bootList += [PSCustomObject]@{
            Date = $evt.TimeCreated
            TotalSec = $durSec
            MainSec = $mainSec
            PostSec = $postSec
            Tag = $tag
        }
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   No boot performance degradation events recorded in the last 30 days."
    } elseif ($_.Exception.Message -match 'unauthorized|access is denied') {
        Write-Host "[WARN] Administrator elevation required to read Diagnostics-Performance log."
    } else {
        Write-Host "[INFO] Boot telemetry query note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 2: Top Startup Applications & Services Causing Degradation ==="
try {
    $degEvents = Get-WinEvent -FilterHashtable @{
        LogName = $logName
        Id = 101, 102, 103, 107, 108
        StartTime = (Get-Date).AddDays(-30)
    } -MaxEvents 30 -ErrorAction Stop

    Write-Host ("[INFO] Detected {0} boot degradation event(s) in the last 30 days:" -f $degEvents.Count)
    Write-Host ("  {0,-19} {1,-6} {2,-10} {3,-12} {4}" -f "Event Date", "ID", "Type", "Delay (ms)", "Name / Description")
    Write-Host ("  {0,-19} {1,-6} {2,-10} {3,-12} {4}" -f "------------------", "----", "----", "----------", "------------------")

    foreach ($devt in $degEvents) {
        $dxml = [xml]$devt.ToXml()
        $dnodes = $dxml.Event.EventData.Data

        $name = ($dnodes | Where-Object { $_.Name -in @('Name', 'AppName', 'DriverName', 'ServiceName') } | Select-Object -ExpandProperty '#text' -First 1)
        $totalTime = ($dnodes | Where-Object { $_.Name -in @('TotalTime', 'DegradationTime') } | Select-Object -ExpandProperty '#text' -First 1)

        $typeLabel = switch ($devt.Id) {
            101 { "App" }
            102 { "Driver" }
            103 { "Service" }
            107 { "Resume" }
            108 { "PostApp" }
            default { "Misc" }
        }

        $timeVal = if ($totalTime) { [int64]$totalTime } else { 0 }
        if ($timeVal -gt 1000 -or -not $totalTime) {
            $dTag = if ($timeVal -ge 5000) { "[BAD] " } else { "[WARN]" }
            Write-Host ("  {0} {1,-19:yyyy-MM-dd HH:mm} {2,-6} {3,-10} {4,-12:N0} {5}" -f $dTag, $devt.TimeCreated, $devt.Id, $typeLabel, $timeVal, $name)
        }
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero application or driver boot degradation events in the last 30 days."
    } elseif ($_.Exception.Message -match 'unauthorized|access is denied') {
        Write-Host "[INFO] Application degradation details require elevated administrator rights."
    } else {
        Write-Host "[INFO] Degradation query note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 3: Fast Startup (Hiberboot) Configuration Status ==="
$powerKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
try {
    $powerProps = Get-ItemProperty -Path $powerKey -ErrorAction Stop
    $hiberboot = $powerProps.HiberbootEnabled
    $hiberFileExists = Test-Path -Path "$env:SystemDrive\hiberfil.sys"

    if ($hiberboot -eq 1) {
        Write-Host "[OK]   Fast Startup (HiberbootEnabled = 1) is ACTIVE."
        Write-Host "       System hibernates kernel session to accelerate cold boot performance."
    } else {
        Write-Host "[INFO] Fast Startup (HiberbootEnabled = 0) is DISABLED."
        Write-Host "       System performs a cold kernel initialization on every boot."
    }

    if ($hiberFileExists) {
        $hf = Get-Item -Path "$env:SystemDrive\hiberfil.sys" -Force -ErrorAction SilentlyContinue
        Write-Host ("  • Hibernation File: {0} ({1:N1} GB)" -f $hf.FullName, ($hf.Length / 1GB))
    } else {
        Write-Host "  • Hibernation File: hiberfil.sys is absent from system drive."
    }
} catch {
    Write-Host "[INFO] Fast Startup check note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
$latestBoot = if ($bootList.Count -gt 0) { $bootList[0] } else { $null }

if ($latestBoot -and $latestBoot.TotalSec -gt 90) {
    Write-Host ("[BAD]  SIGNIFICANT BOOT DELAY: Latest boot required {0:N1}s (MainPath: {1:N1}s, PostBoot: {2:N1}s)." -f $latestBoot.TotalSec, $latestBoot.MainSec, $latestBoot.PostSec)
    if ($latestBoot.PostSec -gt $latestBoot.MainSec) {
        Write-Host "       Bottleneck is in POST-BOOT desktop phase:"
        Write-Host "       • Inspect Startup Apps in Task Manager or WinClientTool Startup tab."
        Write-Host "       • Delay autostarting heavy desktop applications (Discord, Steam, Spotify, Teams)."
    } else {
        Write-Host "       Bottleneck is in MAIN-PATH kernel phase:"
        Write-Host "       • Check storage health, disk queues, and review Phase 2 drivers with high initialization times."
    }
} elseif ($latestBoot -and $latestBoot.TotalSec -gt 45) {
    Write-Host ("[WARN] MODERATE BOOT TIME: Latest boot took {0:N1}s. Review Phase 2 degraders for optimization." -f $latestBoot.TotalSec)
} else {
    Write-Host "[OK]   STATUS: BOOT PERFORMANCE HEALTHY. System boot phases and startup apps are responsive."
}
""",
        admin=True,
    ),
]
