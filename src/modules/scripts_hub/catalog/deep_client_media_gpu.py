"""Windows Client Audio Graph, Media & GPU Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

HW = maker("Hardware")
TRO = maker("Troubleshooting")

SCRIPTS = [
    HW(
        "audio_mediagraph_latency_profiler",
        "Windows Audio Graph, APO Stutter & audiodg Memory Leak Profiler",
        "Audits Windows audio engine (audiodg.exe) working set memory, Audio Processing Objects (APOs), "
        "sample rate discrepancies between endpoints, and MMCSS priority.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Windows Audio Graph, APO Stutter & audiodg Memory Leak Profiler"
Write-Host "PURPOSE   : Audits audiodg.exe footprint, endpoint sample rates, APOs & MMCSS priority"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: WINDOWS AUDIO ENGINE ARCHITECTURE"
Write-Host "  • audiodg.exe: Isolated user-mode audio engine process hosting DSP effects & APOs."
Write-Host "  • APO LEAKS: Third-party OEM Audio Processing Objects (Nahimic, Realtek, Dolby, Waves)"
Write-Host "    frequently leak non-paged pool or private working set memory, inducing audio dropouts."
Write-Host "  • SAMPLE RATE MISMATCH: When render & capture endpoints differ (e.g. 44.1 kHz vs 48.0 kHz),"
Write-Host "    the audio engine forces software resampling on CPU, creating latency and crackling."
Write-Host "  • MMCSS: Multimedia Class Scheduler Service prioritizes real-time audio threads over background tasks."
Write-Host ""

Write-Host "=== Phase 1: Windows Audio Device Graph (audiodg.exe) Footprint ==="
$audioProcs = Get-Process -Name audiodg -ErrorAction SilentlyContinue
if ($audioProcs) {
    foreach ($p in $audioProcs) {
        $wsMB = [math]::Round($p.WorkingSet64 / 1MB, 2)
        $pmMB = [math]::Round($p.PrivateMemorySize64 / 1MB, 2)
        $cpuSec = if ($p.CPU) { [math]::Round($p.CPU, 2) } else { 0 }
        $threads = $p.Threads.Count
        $handles = $p.Handles
        Write-Host ("Process ID    : {0}" -f $p.Id)
        Write-Host ("Working Set   : {0} MB" -f $wsMB)
        Write-Host ("Private Bytes : {0} MB" -f $pmMB)
        Write-Host ("Thread Count  : {0}" -f $threads)
        Write-Host ("Handle Count  : {0}" -f $handles)
        Write-Host ("Total CPU Time: {0} s" -f $cpuSec)

        if ($wsMB -gt 250 -or $pmMB -gt 200) {
            Write-Host "[BAD]  audiodg.exe is consuming excessive memory (>200 MB)! Severe APO memory leak detected."
        } elseif ($wsMB -gt 100 -or $pmMB -gt 80) {
            Write-Host "[WARN] audiodg.exe memory footprint is elevated (>80 MB). Potential OEM enhancement leak."
        } else {
            Write-Host "[OK]   audiodg.exe memory consumption is normal and within healthy baseline limits."
        }
        if ($handles -gt 1500) {
            Write-Host "[WARN] Handle count is unusually high (>1500), indicating handle leakage in audio driver."
        }
    }
} else {
    Write-Host "[INFO] audiodg.exe is not currently running (no active audio stream playing or Windows Audio idle)."
}

$srvAudio = Get-Service -Name Audiosrv -ErrorAction SilentlyContinue
$srvEp = Get-Service -Name AudioEndpointBuilder -ErrorAction SilentlyContinue
Write-Host ("Audio Service (Audiosrv)            : {0} (Status: {1})" -f $srvAudio.DisplayName, $srvAudio.Status)
Write-Host ("Endpoint Builder (AudioEndpointBuilder) : {0} (Status: {1})" -f $srvEp.DisplayName, $srvEp.Status)
if ($srvAudio.Status -ne 'Running' -or $srvEp.Status -ne 'Running') {
    Write-Host "[BAD]  Critical audio services are not in 'Running' state!"
} else {
    Write-Host "[OK]   Core Windows Audio background services are running."
}
Write-Host ""

Write-Host "=== Phase 2: Active Audio Endpoints & Sample Rate Alignment ==="
$renderBase = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render'
$captureBase = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture'

$activeEndpoints = @()
$sampleRates = @{}

$endpointTypes = @(
    @{ Label = 'Playback (Render)'; Key = $renderBase },
    @{ Label = 'Recording (Capture)'; Key = $captureBase }
)

foreach ($epType in $endpointTypes) {
    $tLabel = $epType.Label
    $tKey = $epType.Key
    if (Test-Path $tKey) {
        $devKeys = Get-ChildItem -Path $tKey -ErrorAction SilentlyContinue
        foreach ($dk in $devKeys) {
            $state = (Get-ItemProperty -Path $dk.PSPath -Name DeviceState -ErrorAction SilentlyContinue).DeviceState
            if ($state -eq 1) {
                $propKey = Join-Path $dk.PSPath 'Properties'
                $p = Get-ItemProperty -Path $propKey -ErrorAction SilentlyContinue
                $devName = $p.'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
                if (-not $devName) { $devName = $p.'{b3f8fa53-0004-438e-9003-51a46e139bfc},6' }
                if (-not $devName) { $devName = "Audio Endpoint $($dk.PSChildName)" }

                $fmt = $p.'{f19f064d-082c-4e27-bc73-6882a1bb8e4c},0'
                $sr = 0; $bits = 0; $ch = 0
                if ($fmt -and $fmt.Length -ge 24) {
                    $ch = [BitConverter]::ToUInt16($fmt, 10)
                    $sr = [BitConverter]::ToUInt32($fmt, 12)
                    $bits = [BitConverter]::ToUInt16($fmt, 22)
                }

                $fxKey = Join-Path $dk.PSPath 'FxProperties'
                $fx = Get-ItemProperty -Path $fxKey -ErrorAction SilentlyContinue
                $disFx = $fx.'{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},1'
                $enhDisabled = ($disFx -eq 1)

                $epObj = [PSCustomObject]@{
                    Type = $tLabel
                    Name = $devName
                    Id = $dk.PSChildName
                    Channels = $ch
                    SampleRate = $sr
                    Bits = $bits
                    EnhancementsDisabled = $enhDisabled
                }
                $activeEndpoints += $epObj
                if ($sr -gt 0) {
                    $sampleRates[$sr] = $sampleRates[$sr] + 1
                }
            }
        }
    }
}

if ($activeEndpoints.Count -gt 0) {
    Write-Host ("[INFO] Discovered {0} active audio endpoint(s):" -f $activeEndpoints.Count)
    foreach ($ep in $activeEndpoints) {
        $rateStr = if ($ep.SampleRate -gt 0) { "{0:N1} kHz ({1} Hz), {2}-bit, {3} ch" -f ($ep.SampleRate / 1000), $ep.SampleRate, $ep.Bits, $ep.Channels } else { "WaveFormat: Unspecified / Windows Default" }
        $enhStr = if ($ep.EnhancementsDisabled) { "Disabled [OPTIMAL]" } else { "Active / Enabled" }
        Write-Host ("  [{0}] {1}" -f $ep.Type, $ep.Name)
        Write-Host ("    Audio Format: {0}" -f $rateStr)
        Write-Host ("    Audio Enhancements : {0}" -f $enhStr)
    }

    $uniqueRates = @($sampleRates.Keys)
    if ($uniqueRates.Count -gt 1) {
        Write-Host ""
        Write-Host ("[WARN] SAMPLE RATE MISMATCH DETECTED across active endpoints! Detected rates: {0} Hz" -f ($uniqueRates -join ', '))
        Write-Host "       Simultaneous playback/recording (e.g. mic at 44.1 kHz and headphones at 48.0 kHz)"
        Write-Host "       forces audiodg.exe to perform software resampling, triggering DPC latency and audio crackles."
    } elseif ($uniqueRates.Count -eq 1) {
        Write-Host ""
        Write-Host ("[OK]   Sample rate alignment is consistent across all active endpoints ({0} Hz / {1:N1} kHz)." -f $uniqueRates[0], ($uniqueRates[0] / 1000))
    }
} else {
    Write-Host "[WARN] No active audio playback or recording endpoints found in MMDevices registry."
}
Write-Host ""

Write-Host "=== Phase 3: Audio Processing Objects (APO) & Enhancement Audit ==="
$mmcssKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile'
if (Test-Path $mmcssKey) {
    $mm = Get-ItemProperty $mmcssKey -ErrorAction SilentlyContinue
    $netThrottling = $mm.NetworkThrottlingIndex
    $sysResp = $mm.SystemResponsiveness
    $throttlingDesc = if ($netThrottling -eq 0xFFFFFFFF -or $netThrottling -eq -1) { "Disabled (Optimal for low network latency)" } elseif ($null -ne $netThrottling) { "$netThrottling (Default: 10 packets/ms)" } else { "Default" }
    $respDesc = if ($sysResp -eq 0 -or $sysResp -eq 10) { "$sysResp% (High real-time multimedia priority)" } elseif ($null -ne $sysResp) { "$sysResp% (Default: 20%)" } else { "Default" }
    Write-Host ("MMCSS NetworkThrottlingIndex : {0}" -f $throttlingDesc)
    Write-Host ("MMCSS SystemResponsiveness   : {0}" -f $respDesc)
}

$activeWithEnhancements = $activeEndpoints | Where-Object { -not $_.EnhancementsDisabled }
Write-Host ("Endpoints with audio enhancements active: {0} of {1}" -f $activeWithEnhancements.Count, $activeEndpoints.Count)

$audioServices = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match 'Nahimic|RtkAudio|Dolby|WavesSysSvc|DTS' -or
    $_.DisplayName -match 'Nahimic|Realtek Audio|Dolby|Waves Audio|DTS Audio'
}
if ($audioServices) {
    Write-Host "[INFO] Detected OEM Audio Processing background service(s):"
    foreach ($as in $audioServices) {
        Write-Host ("  • {0} ({1}) -- State: {2}, StartMode: {3}" -f $as.DisplayName, $as.Name, $as.State, $as.StartMode)
    }
} else {
    Write-Host "[OK]   No third-party OEM audio enhancement services (Nahimic, Waves, Sonic Studio) detected."
}

$soundDevs = Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue
if ($soundDevs) {
    Write-Host "[INFO] Installed Sound Hardware Controllers:"
    foreach ($sd in $soundDevs) {
        Write-Host ("  • {0} (Manufacturer: {1}, Status: {2})" -f $sd.Name, $sd.Manufacturer, $sd.Status)
    }
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
$leakProcs = if ($audioProcs) { @($audioProcs | Where-Object { $_.WorkingSet64 -gt 250MB -or $_.PrivateMemorySize64 -gt 200MB }) } else { @() }
if ($leakProcs.Count -gt 0) {
    Write-Host "[BAD]  MEMORY LEAK DETECTED: audiodg.exe is leaking memory. This is the primary root cause of audio crackling and micro-stutters in games."
    Write-Host "       RECOMMENDED REMEDIATION:"
    Write-Host "       1. Open Sound Control Panel (run mmsys.cpl)."
    Write-Host "       2. Right-click active playback device -> Properties -> Advanced or Enhancements tab."
    Write-Host "       3. Check 'Disable all sound effects' or 'Disable all enhancements'."
    Write-Host "       4. Check Spatial Sound tab and select 'Off'."
    Write-Host "       5. Open Windows Services console (services.msc) and restart audio service (Windows Audio)."
} elseif ($uniqueRates -and $uniqueRates.Count -gt 1) {
    Write-Host "[WARN] SAMPLE RATE MISMATCH: Active audio devices run on conflicting sample rates."
    Write-Host "       RECOMMENDED REMEDIATION:"
    Write-Host "       1. Open Sound Control Panel (run mmsys.cpl)."
    Write-Host "       2. Align all active Playback AND Recording devices to 24-bit 48000 Hz (Studio Quality) or 16-bit 48000 Hz (DVD Quality)."
    Write-Host "       3. Matching sample rates eliminates software resampler overhead in the Windows Audio Engine."
} else {
    Write-Host "[OK]   STATUS: Audio engine footprint is healthy. Working set memory and sample rate alignment are within normal parameters."
    Write-Host "       If experiencing game audio stutters, check DPC latency (storport.sys / nvlddmkm.sys) or disable OEM effects in mmsys.cpl."
}
""",
    ),
    TRO(
        "gpu_tdr_directx_crash_postmortem",
        "DirectX Shader Cache & GPU Driver TDR (Event 4101) Post-Mortem",
        "Diagnoses GPU Timeout Detection and Recovery (TDR) crashes (Event ID 4101), calculates "
        "DirectX Shader Cache (D3DSCache) disk bloat, and inspects TdrDelay registry timeouts.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: DirectX Shader Cache & GPU Driver TDR (Event 4101) Post-Mortem"
Write-Host "PURPOSE   : Analyzes WDDM TDR timeouts, Display Event 4101, and Shader Cache bloat"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: GPU TIMEOUT DETECTION & RECOVERY (TDR)"
Write-Host "  • WDDM TDR ARCHITECTURE: Windows WDDM scheduler monitors GPU engine execution."
Write-Host "    If the GPU does not complete a work packet within the timeout (default 2 seconds),"
Write-Host "    the OS assumes the hardware hung, resets the GPU engine, and logs Event ID 4101."
Write-Host "  • EVENT ID 4101: 'Display driver <nvlddmkm/amdkmdag/igfx> stopped responding and has successfully recovered.'"
Write-Host "  • ROOT CAUSES: Corrupt DirectX shader caches, unstable VRAM / core overclocks, PCIe power rail sag,"
Write-Host "    or long compute shader passes exceeding the 2-second watchdog threshold."
Write-Host "  • SHADER CACHE BLOAT: Stale or corrupted D3DSCache files cause severe frame stuttering and TDR timeouts."
Write-Host ""

Write-Host "=== Phase 1: WDDM TDR Registry Configuration (GraphicsDrivers) ==="
$gfxPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
if (Test-Path $gfxPath) {
    $g = Get-ItemProperty -Path $gfxPath -ErrorAction SilentlyContinue
    $tdrDelay = $g.TdrDelay
    $tdrDdiDelay = $g.TdrDdiDelay
    $tdrLevel = $g.TdrLevel
    $tdrLimitTime = $g.TdrLimitTime
    $tdrLimitCount = $g.TdrLimitCount

    $delayVal = if ($null -ne $tdrDelay) { "$tdrDelay seconds (Custom)" } else { "2 seconds (Windows Default)" }
    $ddiVal = if ($null -ne $tdrDdiDelay) { "$tdrDdiDelay seconds (Custom)" } else { "5 seconds (Windows Default)" }

    $levelVal = switch ($tdrLevel) {
        0 { "0 -- TDR Detection Disabled [DANGEROUS: System will hard lock on GPU hang!]" }
        1 { "1 -- Bugcheck on Timeout (Forces BSOD 0x116 / 0x117 on timeout)" }
        2 { "2 -- Recover Without Reset" }
        3 { "3 -- Recover on Timeout (Default WDDM Engine Reset)" }
        default { if ($null -eq $tdrLevel) { "3 -- Recover on Timeout (Windows Default)" } else { "$tdrLevel (Unknown)" } }
    }

    Write-Host ("TdrDelay (GPU Scheduler Timeout) : {0}" -f $delayVal)
    Write-Host ("TdrDdiDelay (Driver Init Timeout) : {0}" -f $ddiVal)
    Write-Host ("TdrLevel (Recovery Policy)        : {0}" -f $levelVal)
    if ($null -ne $tdrLimitTime) { Write-Host ("TdrLimitTime                      : {0} seconds" -f $tdrLimitTime) }
    if ($null -ne $tdrLimitCount) { Write-Host ("TdrLimitCount                     : {0} resets" -f $tdrLimitCount) }

    if ($tdrLevel -eq 0) {
        Write-Host "[BAD]  TdrLevel is set to 0 (TDR disabled)! A frozen GPU will completely freeze the entire system requiring hard reboot."
    } elseif ($tdrDelay -and $tdrDelay -gt 15) {
        Write-Host "[WARN] TdrDelay is set very high (>15s). While preventing false TDRs during heavy compute, it will mask real GPU deadlocks."
    } elseif ($tdrDelay -and $tdrDelay -ge 8) {
        Write-Host "[OK]   TdrDelay has been extended (>=8s), accommodating heavy Ray Tracing or compute shader workloads."
    } else {
        Write-Host "[OK]   WDDM TDR recovery settings are active and standard."
    }
} else {
    Write-Host "[WARN] GraphicsDrivers registry key not found."
}
Write-Host ""

Write-Host "=== Phase 2: Display Driver TDR Crashes in System Log (Event ID 4101) ==="
$days = 14
$tdrEvents = @()
try {
    $tdrEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Display';
        Id = 4101;
        StartTime = (Get-Date).AddDays(-$days)
    } -MaxEvents 50 -ErrorAction Stop
} catch {
    if ($_.Exception.Message -match 'No events were found|There is not an event provider') {
        $tdrEvents = @()
    } else {
        Write-Host "[INFO] Event log query note: $($_.Exception.Message)"
    }
}

if ($tdrEvents.Count -gt 0) {
    Write-Host ("[BAD]  DETECTED {0} Display Driver TDR Crash(es) in the past {1} days:" -f $tdrEvents.Count, $days)

    $tdrEvents | Group-Object Message | ForEach-Object {
        $first = $_.Group[0]
        $summaryMsg = ($first.Message -split "`r?`n")[0]
        Write-Host ("  • Count: {0,-4} | Last Seen: {1:yyyy-MM-dd HH:mm:ss} | {2}" -f $_.Count, $first.TimeCreated, $summaryMsg)
    }

    Write-Host ""
    Write-Host "  Recent TDR Incident Log (Newest 5 events):"
    $tdrEvents | Select-Object -First 5 | ForEach-Object {
        Write-Host ("    - [{0:yyyy-MM-dd HH:mm:ss}] Event 4101: {1}" -f $_.TimeCreated, ($_.Message -split "`r?`n")[0])
    }
} else {
    Write-Host ("[OK]   Zero Display Driver TDR crashes (Event ID 4101) recorded in the past {0} days." -f $days)
}

$watchdogDir = "$env:SystemRoot\LiveKernelReports\WATCHDOG"
try {
    if (Test-Path $watchdogDir -ErrorAction Stop) {
        $liveDumps = Get-ChildItem -Path $watchdogDir -Filter "*.dmp" -ErrorAction Stop
        if ($liveDumps) {
            $dCount = $liveDumps.Count
            $dNewest = ($liveDumps | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
            Write-Host ("[WARN] Found {0} LiveKernel Watchdog dump(s) in %SystemRoot%\LiveKernelReports\WATCHDOG." -f $dCount)
            Write-Host ("       Newest Live Watchdog Dump: {0} ({1:yyyy-MM-dd HH:mm:ss})" -f $dNewest.Name, $dNewest.LastWriteTime)
        } else {
            Write-Host "[OK]   LiveKernel Watchdog directory exists with zero crash dump files."
        }
    }
} catch {
    Write-Host "[INFO] LiveKernelReports\WATCHDOG query note (folder is restricted to Administrators)."
}
Write-Host ""

Write-Host "=== Phase 3: DirectX & GPU Vendor Shader Cache Footprint ==="
$cacheLocations = @(
    @{ Label = 'Windows DirectX D3DSCache'; Path = "$env:LOCALAPPDATA\D3DSCache" },
    @{ Label = 'NVIDIA DirectX Shader Cache'; Path = "$env:LOCALAPPDATA\NVIDIA\DXCache" },
    @{ Label = 'NVIDIA OpenGL Shader Cache'; Path = "$env:LOCALAPPDATA\NVIDIA\GLCache" },
    @{ Label = 'NVIDIA Compute Shader Cache'; Path = "$env:APPDATA\NVIDIA\ComputeCache" },
    @{ Label = 'AMD DirectX Shader Cache'; Path = "$env:LOCALAPPDATA\AMD\DxCache" },
    @{ Label = 'AMD OpenGL Shader Cache'; Path = "$env:LOCALAPPDATA\AMD\GLCache" },
    @{ Label = 'Intel Graphics Shader Cache'; Path = "$env:LOCALAPPDATA\Intel\ShaderCache" }
)

$totalCacheFiles = 0
$totalCacheBytes = 0

foreach ($loc in $cacheLocations) {
    $cPath = $loc.Path
    $cLabel = $loc.Label
    if (Test-Path $cPath) {
        try {
            $files = Get-ChildItem -Path $cPath -File -Recurse -ErrorAction SilentlyContinue
            if ($files) {
                $m = $files | Measure-Object -Property Length -Sum
                $fCount = $m.Count
                $fBytes = $m.Sum
                $fMB = [math]::Round($fBytes / 1MB, 1)
                $totalCacheFiles += $fCount
                $totalCacheBytes += $fBytes

                $status = if ($fMB -gt 1024) { "[WARN]" } else { "[OK]  " }
                Write-Host ("{0} {1,-32} : {2,6} files | {3,8} MB  ({4})" -f $status, $cLabel, $fCount, $fMB, $cPath)
            } else {
                Write-Host ("{0} {1,-32} : {2,6} files | {3,8} MB" -f "[OK]  ", $cLabel, 0, 0)
            }
        } catch {
            Write-Host ("[INFO] {0,-32} : Note: {1}" -f $cLabel, $_.Exception.Message)
        }
    } else {
        Write-Host ("[INFO] {0,-32} : Not present on disk" -f $cLabel)
    }
}

$totalCacheMB = [math]::Round($totalCacheBytes / 1MB, 1)
Write-Host ""
Write-Host ("Total Shader Cache Footprint: {0} files | {1} MB" -f $totalCacheFiles, $totalCacheMB)
if ($totalCacheMB -gt 2048 -or $totalCacheFiles -gt 8000) {
    Write-Host "[WARN] Shader cache footprint is abnormally large (>2 GB / >8000 files). Stale caches can trigger stuttering."
} else {
    Write-Host "[OK]   Shader cache footprint is within normal storage limits."
}
Write-Host ""

Write-Host "=== Phase 4: Installed GPU Display Adapters & WDDM Driver Versions ==="
$gpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
if ($gpus) {
    foreach ($gpu in $gpus) {
        $vramMB = if ($gpu.AdapterRAM) { [math]::Round($gpu.AdapterRAM / 1MB, 0) } else { "N/A" }
        $driverDate = if ($gpu.DriverDate) { [string]::Format("{0:yyyy-MM-dd}", $gpu.DriverDate) } else { "Unknown" }
        Write-Host ("Adapter Name     : {0}" -f $gpu.Name)
        Write-Host ("Driver Version   : {0} (Release Date: {1})" -f $gpu.DriverVersion, $driverDate)
        Write-Host ("Dedicated VRAM   : {0} MB" -f $vramMB)
        Write-Host ("Current Mode     : {0} x {1} @ {2} Hz ({3} bpp)" -f $gpu.CurrentHorizontalResolution, $gpu.CurrentVerticalResolution, $gpu.CurrentRefreshRate, $gpu.CurrentBitsPerPixel)
        Write-Host ("Status           : {0}" -f $gpu.Status)
        Write-Host ""
    }
}

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($tdrEvents.Count -gt 0) {
    Write-Host ("[BAD]  CRITICAL: System has suffered {0} GPU driver TDR crash(es) in the last {1} days." -f $tdrEvents.Count, $days)
    Write-Host "       RECOMMENDED TRIAGE WORKFLOW:"
    Write-Host "       1. CLEAN DRIVER REINSTALLATION:"
    Write-Host "          - Boot Windows into Safe Mode."
    Write-Host "          - Execute Display Driver Uninstaller (DDU) to wipe existing display driver and registry state."
    Write-Host "          - Reinstall latest WHQL driver package without bundled telemetry / bloatware."
    Write-Host "       2. PURGE CORRUPT SHADER CACHES:"
    Write-Host "          - Delete contents of %LOCALAPPDATA%\D3DSCache and GPU vendor cache directories."
    Write-Host "          - Corrupt shader caches cause recurrent compilation hangs and WDDM resets."
    Write-Host "       3. WDDM TDR TIMEOUT ADJUSTMENT (FOR HEAVY 3D / UNREAL ENGINE 5 WORKLOADS):"
    Write-Host "          - If running high-load compute or intensive Ray Tracing shaders, adjust TdrDelay to 8 or 10 seconds."
    Write-Host "          - Path: HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers (DWORD TdrDelay = 8)."
    Write-Host "       4. HARDWARE & POWER INTEGRITY CHECKS:"
    Write-Host "          - Remove aggressive GPU memory/core overclocks in MSI Afterburner."
    Write-Host "          - Check 12VHPWR / 8-pin PCIe power cable seating; 12V rail voltage drops under transient loads cause instant TDRs."
} else {
    Write-Host "[OK]   STATUS: GPU stability is healthy. Zero TDR crashes detected in the 14-day telemetry window."
    Write-Host "       If experiencing game crashes without Event 4101, investigate game-specific crash dumps or memory instability."
}
""",
    ),
]
