"""Client USB, DirectStorage BypassIO, DWM MPO & LiveKernelReports Diagnostics. Read-only.

Target: Standalone Windows Client (Windows 10 / 11, Workgroup/Home/Gaming/Power-user).
Strictly NO Active Directory, NO Entra ID, NO Intune.
"""
from ._base import CHK, L, T, maker, need_admin

HW = maker("Hardware")
STO = maker("Storage")
TRO = maker("Troubleshooting")

SCRIPTS = [
    HW(
        "usb_xhci_controller_drop_triage",
        "USB xHCI Controller, Selective Suspend & Device Drop Forensic",
        "Audits USB xHCI controllers, root hub power transitions, USB selective suspend state, descriptor drop events, and USB-USBHUB3 error logs.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: USB xHCI Controller, Selective Suspend & Device Drop Forensic"
Write-Host "PURPOSE   : Audits USB root hubs, power transitions, selective suspend, and drop events"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: USB xHCI & CONTROLLER POWER ARCHITECTURE"
Write-Host "  * USB xHCI Architecture: Modern motherboards employ eXtensible Host Controller"
Write-Host "    Interface (xHCI) root hubs supporting USB 3.x and USB 2.0. High-polling devices"
Write-Host "    (1000-8000 Hz mice, 4K webcams, audio DACs) rapidly consume xHCI hardware"
Write-Host "    endpoints and bus bandwidth."
Write-Host "  * USB Selective Suspend: An ACPI power-saving mechanism that transitions idle USB"
Write-Host "    ports into a low-power suspend state (D2/D3). When waking, high-draw or latency-"
Write-Host "    sensitive devices can timeout during descriptor negotiation, triggering disconnect"
Write-Host "    chimes, audio crackle, or device drop loops."
Write-Host "  * Device Drop Chime Loops: Repeated disconnect and reconnect audio chimes usually"
Write-Host "    indicate VBUS rail voltage sag, USB hub endpoint exhaustion, or aggressive power"
Write-Host "    state switching by the root hub driver."
Write-Host ""

$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "=== Phase 1: USB Host Controllers & Root Hubs ==="
try {
    $controllers = Get-CimInstance -ClassName Win32_USBController -ErrorAction Stop
    $ctrlCount = @($controllers).Count
    Write-Host ("  [INFO] Detected {0} USB Host Controller(s) on system bus." -f $ctrlCount)

    foreach ($c in $controllers) {
        $cName = $c.Name
        $cStatus = $c.Status
        $cMfr = $c.Manufacturer
        $cDevId = $c.DeviceID

        if ($cStatus -eq 'OK') {
            Write-Host ("  [OK]   Controller: {0} ({1})" -f $cName, $cMfr)
        } else {
            Write-Host ("  [BAD]  Controller: {0} (Status: {1}, DeviceID: {2})" -f $cName, $cStatus, $cDevId)
        }
    }
} catch {
    Write-Host "  [WARN] Unable to enumerate USB controllers via CIM: $($_.Exception.Message)"
}

# Enumerate USB Root Hubs & Connected PnP Devices
try {
    $usbDevices = Get-CimInstance -ClassName Win32_PnPEntity -Filter "PNPClass = 'USB'" -ErrorAction SilentlyContinue
    $totalUsb = @($usbDevices).Count
    Write-Host ("`n  [INFO] Total USB PnP Infrastructure Devices: {0}" -f $totalUsb)

    $errorDevices = @($usbDevices | Where-Object { $_.ConfigManagerErrorCode -ne 0 -or $_.Status -ne 'OK' })
    if ($errorDevices.Count -gt 0) {
        foreach ($ed in $errorDevices) {
            Write-Host ("  [BAD]  USB Device with Hardware Error: {0} (Error Code: {1}, Status: {2})" -f $ed.Name, $ed.ConfigManagerErrorCode, $ed.Status)
        }
    } else {
        Write-Host "  [OK]   Zero USB infrastructure devices reporting hardware error codes."
    }

    # Query all active connected USB peripherals (HID, Audio, Video, Storage)
    $allPnp = Get-CimInstance -ClassName Win32_PnPEntity -ErrorAction SilentlyContinue
    $connectedUsb = @($allPnp | Where-Object { $_.PNPDeviceID -like 'USB\*' -and $_.Status -eq 'OK' })
    Write-Host ("`n  [INFO] Connected USB Peripherals ({0} detected):" -f $connectedUsb.Count)

    foreach ($dev in $connectedUsb) {
        $dName = $dev.Name
        $dClass = $dev.PNPClass
        $dId = $dev.DeviceID

        $isHighDemand = $dName -match 'Audio|DAC|Camera|Webcam|Mouse|Gaming|Headset|Microphone|Dongle|Wireless|Receiver'
        $tag = if ($isHighDemand) { '[INFO] (High-Demand Peripheral)' } else { '      ' }
        Write-Host ("    {0} {1,-42} Class: {2,-15}" -f $tag, $dName, $dClass)
    }
} catch {
    Write-Host "  [INFO] Note enumerating USB peripherals: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: USB Selective Suspend & Power Management Policy ==="
# 1. Global registry override for USB selective suspend
$usbServiceReg = 'HKLM:\SYSTEM\CurrentControlSet\Services\USB'
if (Test-Path $usbServiceReg) {
    $globalDisable = (Get-ItemProperty -Path $usbServiceReg -Name DisableSelectiveSuspend -ErrorAction SilentlyContinue).DisableSelectiveSuspend
    if ($globalDisable -eq 1) {
        Write-Host "  [OK]   Global USB Selective Suspend: Disabled in registry (DisableSelectiveSuspend = 1)."
    } else {
        Write-Host "  [INFO] Global USB Selective Suspend: Enabled / Windows default (DisableSelectiveSuspend not set)."
    }
} else {
    Write-Host "  [INFO] USB Service registry key: Default Windows driver configuration."
}

# 2. Powercfg query for active power scheme USB selective suspend setting
try {
    $subUsbGuid = '2a737441-1930-4402-8d77-b2bebba308a3'
    $settingUsbGuid = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'
    $pcfgOut = & powercfg.exe /query SCHEME_CURRENT $subUsbGuid $settingUsbGuid 2>&1
    $pcfgText = $pcfgOut -join "`n"

    $acMatch = [regex]::Match($pcfgText, 'Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)')
    $dcMatch = [regex]::Match($pcfgText, 'Current DC Power Setting Index:\s*0x([0-9a-fA-F]+)')

    if ($acMatch.Success) {
        $acVal = [int]("0x" + $acMatch.Groups[1].Value)
        if ($acVal -eq 0) {
            Write-Host "  [OK]   Power Plan AC (Plugged In): USB Selective Suspend is DISABLED."
        } else {
            Write-Host "  [WARN] Power Plan AC (Plugged In): USB Selective Suspend is ENABLED (May cause device drops during idle)."
        }
    } else {
        Write-Host "  [INFO] Power Plan AC setting for USB selective suspend: Not reported."
    }

    if ($dcMatch.Success) {
        $dcVal = [int]("0x" + $dcMatch.Groups[1].Value)
        if ($dcVal -eq 0) {
            Write-Host "  [OK]   Power Plan DC (Battery): USB Selective Suspend is DISABLED."
        } else {
            Write-Host "  [INFO] Power Plan DC (Battery): USB Selective Suspend is ENABLED (Conserves mobile battery)."
        }
    }
} catch {
    Write-Host "  [INFO] Powercfg query note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 3: USB Diagnostic Event Log Triage (Last 7 Days) ==="
$daysBack = 7
$startDate = (Get-Date).AddDays(-$daysBack)
$totalDropEvents = 0

# 1. Query System Log for USB-USBHUB3, USB-USBXHCI, BTHUSB and Kernel-PnP errors
try {
    $usbEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = @('USB-USBHUB3', 'USB-USBXHCI', 'BTHUSB', 'Microsoft-Windows-Kernel-PnP');
        StartTime = $startDate
    } -MaxEvents 100 -ErrorAction Stop

    $problemEvents = @($usbEvents | Where-Object { $_.Level -le 3 -or $_.Id -in @(1, 4, 144, 219, 2003, 2005) })
    if ($problemEvents.Count -gt 0) {
        $totalDropEvents += $problemEvents.Count
        Write-Host ("  [BAD]  Found {0} USB warning or error event(s) in System log over the last {1} days:" -f $problemEvents.Count, $daysBack)
        foreach ($ev in ($problemEvents | Select-Object -First 10)) {
            $firstMsg = ($ev.Message -split "`r?`n")[0]
            Write-Host ("         * {0:yyyy-MM-dd HH:mm:ss} [ID {1,4}] {2,-18} | {3}" -f $ev.TimeCreated, $ev.Id, $ev.ProviderName, $firstMsg)
        }
        if ($problemEvents.Count -gt 10) {
            Write-Host ("         ... and {0} additional events." -f ($problemEvents.Count - 10))
        }
    } else {
        Write-Host "  [OK]   Zero USB-USBHUB3 or xHCI disconnect errors recorded in System log (Last 7 Days)."
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "  [OK]   Zero USB error events found in System log (Last 7 Days)."
    } else {
        Write-Host "  [INFO] System log USB event query: $($_.Exception.Message)"
    }
}

# 2. Check Microsoft-Windows-USB-USBHUB3/Operational if available
try {
    $hubLog = Get-WinEvent -ListLog 'Microsoft-Windows-USB-USBHUB3/Operational' -ErrorAction SilentlyContinue
    if ($hubLog -and $hubLog.IsEnabled) {
        $hubEvents = Get-WinEvent -FilterHashtable @{
            LogName = 'Microsoft-Windows-USB-USBHUB3/Operational';
            StartTime = $startDate
        } -MaxEvents 50 -ErrorAction SilentlyContinue

        $hubWarns = @($hubEvents | Where-Object { $_.Level -le 3 })
        if ($hubWarns.Count -gt 0) {
            $totalDropEvents += $hubWarns.Count
            Write-Host ("  [WARN] Detected {0} operational warning(s) in USBHUB3 Operational log." -f $hubWarns.Count)
        } else {
            Write-Host "  [OK]   USB-USBHUB3 Operational log reports no warnings or errors."
        }
    } else {
        Write-Host "  [INFO] Microsoft-Windows-USB-USBHUB3/Operational log is disabled or unconfigured (Windows default)."
    }
} catch {
    Write-Host "  [INFO] USBHUB3 operational query note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($totalDropEvents -gt 0) {
    Write-Host "[BAD]  RECURRENT USB DEVICE DROPS OR DESCRIPTOR TIMEOUTS DETECTED:"
    Write-Host "       1. Disable selective suspend: Go to Control Panel -> Power Options ->"
    Write-Host "          Change plan settings -> Change advanced power settings -> USB settings,"
    Write-Host "          and set 'USB selective suspend setting' to 'Disabled' on AC power."
    Write-Host "       2. Rebalance xHCI Root Hub Endpoints: High-polling gaming mice (1000-8000 Hz)"
    Write-Host "          and USB audio DACs should be connected to separate root hubs. Connect the"
    Write-Host "          mouse to direct rear motherboard CPU-laned ports and DACs to chipset ports."
    Write-Host "       3. External Powered Hubs: If using high-draw RGB peripherals, webcams, or"
    Write-Host "          audio interfaces, connect them through an externally powered USB 3.0 hub"
    Write-Host "          to prevent 5V VBUS rail voltage droop."
    Write-Host "       4. Motherboard Firmware: On AMD AM4 and AM5 platforms, update motherboard BIOS"
    Write-Host "          to AGESA 1.2.0.2 or newer to resolve known xHCI PCIe packet drop bugs."
} else {
    Write-Host "[OK]   USB xHCI CONTROLLERS & ROOT HUBS HEALTHY:"
    Write-Host "       No hardware errors or recurring device drop events recorded in telemetry."
    Write-Host "       If peripheral stutter occurs in specific games, verify USB selective suspend"
    Write-Host "       is set to Disabled in your active power plan."
}
""",
    ),
    STO(
        "directstorage_bypassio_audit",
        "DirectStorage & NVMe BypassIO Storage Stack Audit",
        "Audits Windows 11 BypassIO state on NVMe volumes (fsutil bypassio state), identifies storage filter drivers blocking BypassIO, and checks DirectStorage readiness.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: DirectStorage & NVMe BypassIO Storage Stack Audit"
Write-Host "PURPOSE   : Audits Windows 11 BypassIO state, minifilter drivers & DirectStorage readiness"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: DIRECTSTORAGE & BYPASSIO ARCHITECTURE"
Write-Host "  * DirectStorage Architecture: Modern gaming storage API designed to stream assets"
Write-Host "    directly from NVMe solid-state storage to GPU VRAM using GDeflate GPU decompression,"
Write-Host "    bypassing traditional CPU asset loading bottlenecks."
Write-Host "  * BypassIO Fast Path: Introduced in Windows 11, BypassIO creates an optimized low-"
Write-Host "    overhead path for file reads that bypasses the traditional Windows I/O stack and"
Write-Host "    filesystem filter manager, reducing CPU overhead during high-IOPS streaming."
Write-Host "  * Blocking Minifilters: Any filesystem filter driver (antivirus, legacy backup,"
Write-Host "    disk encryption, or virtualization filters) that does not explicitly support"
Write-Host "    BypassIO causes Windows to disable BypassIO and fall back to the legacy I/O path."
Write-Host ""

$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "=== Phase 1: Volume BypassIO Support & Driver Query ==="
$os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction SilentlyContinue
$buildNumber = [int]$os.BuildNumber
Write-Host ("  [INFO] Windows Kernel Build: {0} ({1})" -f $buildNumber, $os.Caption)

$supportsBypassIO = ($buildNumber -ge 22000)
if ($supportsBypassIO) {
    Write-Host "  [OK]   Operating System: Windows 11 kernel supports BypassIO architecture."
} else {
    Write-Host "  [WARN] Operating System: Windows 10 or earlier. BypassIO is not supported on this kernel (DirectStorage falls back to standard Win32 I/O path)."
}

# Audit fixed volumes
$fixedVolumes = Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.DriveLetter } | Sort-Object DriveLetter
$bypassIoActiveCount = 0
$bypassIoBlockedCount = 0

foreach ($vol in $fixedVolumes) {
    $drive = "$($vol.DriveLetter):"
    $fs = $vol.FileSystem
    $freeGB = [math]::Round($vol.SizeRemaining / 1GB, 1)
    $totalGB = [math]::Round($vol.Size / 1GB, 1)

    Write-Host ("`n  Volume [{0}] FileSystem: {1} | Capacity: {2} GB (Free: {3} GB)" -f $drive, $fs, $totalGB, $freeGB)

    if ($supportsBypassIO -and $isAdm) {
        try {
            $fsutilOut = & fsutil.exe bypassio state $drive 2>&1
            $fsutilText = $fsutilOut -join "`n"

            $isSupported = $fsutilText -match 'is currently supported'
            $driverComp = $fsutilText -match 'Storage Driver\s*:\s*Compatible'
            $volSupported = $fsutilText -match 'Volume\s*:\s*Supported'

            if ($isSupported) {
                $bypassIoActiveCount++
                Write-Host ("    [OK]   BypassIO on {0} is SUPPORTED and ACTIVE for DirectStorage." -f $drive)
            } else {
                $bypassIoBlockedCount++
                Write-Host ("    [WARN] BypassIO on {0} is NOT ACTIVE." -f $drive)

                # Extract failure reasons or blocking drivers
                foreach ($line in $fsutilOut) {
                    $trim = "$line".Trim()
                    if ($trim -match '^(Storage Driver|Volume|Filter Drivers)\s*:' -or $trim -match 'Reason:') {
                        Write-Host ("           • {0}" -f $trim)
                    }
                }
            }
        } catch {
            Write-Host "    [INFO] fsutil query error: $($_.Exception.Message)"
        }
    } elseif (-not $isAdm) {
        Write-Host "    [INFO] fsutil bypassio state query requires administrator privileges (skipped unelevated)."
    }
}
Write-Host ""

Write-Host "=== Phase 2: Filesystem Filter Drivers Stack ==="
if ($isAdm) {
    try {
        $fltOut = & fltmc.exe filters 2>&1
        $filterLines = @($fltOut | Where-Object { $_ -match '^[a-zA-Z0-9_]+\s+\d+' })
        Write-Host ("  [INFO] Active Filesystem Minifilter Drivers ({0} loaded):" -f $filterLines.Count)

        # Standard in-box Microsoft filters certified for BypassIO
        $standardMsFilters = @('FileInfo', 'Wof', 'bindflt', 'wcifs', 'storqosflt', 'luafv', 'npsvctrig', 'mssecflt', 'CldFlt', 'PrjFlt', 'FileCrypt')

        foreach ($flt in $filterLines) {
            $parts = ($flt.Trim() -split '\s+')
            if ($parts.Count -ge 3) {
                $fName = $parts[0]
                $fInstances = $parts[1]
                $fAltitude = $parts[2]

                $isMs = $standardMsFilters -contains $fName
                if ($isMs) {
                    Write-Host ("    [OK]   Filter: {0,-22} Altitude: {1,-10} (Standard Microsoft In-Box Driver)" -f $fName, $fAltitude)
                } else {
                    Write-Host ("    [INFO] Filter: {0,-22} Altitude: {1,-10} (Third-Party / Security Driver - check BypassIO support)" -f $fName, $fAltitude)
                }
            }
        }
    } catch {
        Write-Host "  [INFO] fltmc query error: $($_.Exception.Message)"
    }
} else {
    Write-Host "  [INFO] fltmc filter driver enumeration requires administrator privileges (skipped unelevated)."
}
Write-Host ""

Write-Host "=== Phase 3: NVMe Physical Drive & DirectStorage Hardware Verification ==="
try {
    $disks = Get-PhysicalDisk -ErrorAction SilentlyContinue
    $diskCount = @($disks).Count
    Write-Host ("  [INFO] Enumerated {0} Physical Storage Disk(s):" -f $diskCount)

    foreach ($d in $disks) {
        $dId = $d.DeviceId
        $dModel = $d.FriendlyName
        $dMedia = $d.MediaType
        $dBus = $d.BusType
        $dSizeGB = [math]::Round($d.Size / 1GB, 1)

        $isNvme = ($dBus -eq 'NVMe')
        $isSsd = ($dMedia -eq 'SSD')

        if ($isNvme -and $isSsd) {
            Write-Host ("  [OK]   Disk {0} [{1} - {2}] {3} GB: Optimal PCIe NVMe SSD for DirectStorage." -f $dId, $dModel, $dBus, $dSizeGB)
        } elseif ($isSsd) {
            Write-Host ("  [WARN] Disk {0} [{1} - {2}] {3} GB: SATA SSD. Supported by DirectStorage with reduced bandwidth." -f $dId, $dModel, $dBus, $dSizeGB)
        } else {
            Write-Host ("  [INFO] Disk {0} [{1} - {2}] {3} GB: HDD or generic storage." -f $dId, $dModel, $dBus, $dSizeGB)
        }

        # Check partition 4K sector alignment
        try {
            $parts = Get-Partition -DiskNumber [int]$dId -ErrorAction SilentlyContinue
            foreach ($p in $parts) {
                if ($p.Size -gt 1GB) {
                    $offset = $p.Offset
                    $isAligned = ($offset % 4096 -eq 0)
                    if ($isAligned) {
                        Write-Host ("         • Partition {0} ({1} GB): 4K Aligned (Offset {2} bytes)." -f $p.PartitionNumber, [math]::Round($p.Size/1GB, 1), $offset)
                    } else {
                        Write-Host ("         • [BAD] Partition {0} ({1} GB): MISALIGNED (Offset {2} bytes not divisible by 4096)!" -f $p.PartitionNumber, [math]::Round($p.Size/1GB, 1), $offset)
                    }
                }
            }
        } catch {
            Write-Host "         • Partition alignment check note: $($_.Exception.Message)"
        }
    }
} catch {
    Write-Host "  [INFO] Physical disk CIM query note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if (-not $supportsBypassIO) {
    Write-Host "[INFO] WINDOWS 10 / LEGACY KERNEL ENVIRONMENT:"
    Write-Host "       DirectStorage functions on Windows 10 via legacy file I/O fallback."
    Write-Host "       To enable full BypassIO kernel fast-path streaming, upgrade to Windows 11."
} elseif ($bypassIoBlockedCount -gt 0) {
    Write-Host "[WARN] BYPASSIO BLOCKED ON ONE OR MORE VOLUMES:"
    Write-Host "       1. Filter Driver Compatibility: Review Phase 2 filter drivers. Outdated"
    Write-Host "          antivirus, disk imaging, or virtualization software often block BypassIO."
    Write-Host "          Update third-party security software to Windows 11 certified releases."
    Write-Host "       2. File System Requirements: Ensure your game libraries reside on standard"
    Write-Host "          NTFS-formatted volumes (ReFS and exFAT do not support BypassIO)."
} elseif ($bypassIoActiveCount -gt 0) {
    Write-Host "[OK]   DIRECTSTORAGE & BYPASSIO FULLY OPERATIONAL:"
    Write-Host "       Volumes support optimized kernel BypassIO streaming directly to GPU VRAM."
    Write-Host "       Ensure DirectX 12 Ultimate GPU drivers are kept updated for GDeflate decompression."
} else {
    Write-Host "[INFO] DIRECTSTORAGE VERIFICATION COMPLETE:"
    Write-Host "       Review drive bus types and partition alignment above for optimal NVMe performance."
}
""",
    ),
    HW(
        "dwm_mpo_multimonitor_stutter",
        "DWM Multi-Monitor Refresh Mismatch & Multi-Plane Overlay (MPO) Sentinel",
        "Diagnoses desktop composition stutter across multi-monitor setups with mismatched refresh rates (144Hz + 60Hz), audits Multi-Plane Overlay (MPO) registry flags, and reports DWM presentation modes.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: DWM Multi-Monitor Refresh Mismatch & Multi-Plane Overlay (MPO) Sentinel"
Write-Host "PURPOSE   : Diagnoses DWM composition stutter, mismatched refresh rates & MPO flags"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: DWM COMPOSITION & MULTI-PLANE OVERLAYS (MPO)"
Write-Host "  * DWM Composition: Desktop Window Manager (DWM) composites all desktop windows into"
Write-Host "    a unified frame buffer. In multi-monitor setups with mismatched refresh rates (e.g."
Write-Host "    144Hz primary + 60Hz secondary), playing video (YouTube/Twitch/Discord) on the secondary"
Write-Host "    screen can force DWM to synchronize at 60Hz or hitch frame presentation on the gaming monitor."
Write-Host "  * Multi-Plane Overlay (MPO): A GPU display engine feature that allows planes (like a 3D game"
Write-Host "    or video stream) to scan out directly to the display without being composited by DWM."
Write-Host "  * MPO Driver Stutter / Flickering: Certain GPU driver versions (NVIDIA & AMD) contain"
Write-Host "    bugs in MPO plane management that cause desktop flickering, black screens, or micro-stutters."
Write-Host "    Disabling MPO via the Windows DWM registry test mode is a well-established remediation."
Write-Host ""

Write-Host "=== Phase 1: Active Displays & Refresh Rate Discrepancy ==="
$refreshRates = @()
try {
    $videoControllers = Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop
    foreach ($gpu in $videoControllers) {
        $gName = $gpu.Name
        $gDrv = $gpu.DriverVersion
        $gResX = $gpu.CurrentHorizontalResolution
        $gResY = $gpu.CurrentVerticalResolution
        $gRefresh = $gpu.CurrentRefreshRate

        Write-Host ("  [INFO] GPU: {0} (Driver: {1})" -f $gName, $gDrv)
        if ($gResX -and $gResY -and $gRefresh) {
            Write-Host ("         Resolution: {0}x{1} @ {2} Hz (Reported by Video Controller)" -f $gResX, $gResY, $gRefresh)
            $refreshRates += [int]$gRefresh
        }
    }
} catch {
    Write-Host "  [WARN] Video controller query error: $($_.Exception.Message)"
}

# Also inspect Display Settings registry configuration for active modes
try {
    $dispConfigKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers\Configuration'
    if (Test-Path $dispConfigKey) {
        $confs = Get-ChildItem -Path $dispConfigKey -ErrorAction SilentlyContinue
        Write-Host ("`n  [INFO] Stored Display Configurations ({0} profile(s) found in registry):" -f @($confs).Count)
    }
} catch {
    Write-Host "  [INFO] Display registry check note: $($_.Exception.Message)"
}

# Analyze refresh rate discrepancy across monitors
$uniqueRates = @($refreshRates | Select-Object -Unique)
if ($uniqueRates.Count -gt 1) {
    $minRate = ($uniqueRates | Measure-Object -Minimum).Minimum
    $maxRate = ($uniqueRates | Measure-Object -Maximum).Maximum
    $delta = $maxRate - $minRate

    Write-Host ("`n  [WARN] Multi-Monitor Refresh Rate Mismatch Detected!" )
    Write-Host ("         Active Refresh Rates: {0} Hz (Delta: {1} Hz difference)" -f ($uniqueRates -join ' Hz, '), $delta)
    Write-Host "         Hardware accelerated video playback on slower monitors can cause DWM pacing drops."
} elseif ($uniqueRates.Count -eq 1) {
    Write-Host ("`n  [OK]   All reported display refresh rates are synchronized at {0} Hz." -f $uniqueRates[0])
} else {
    Write-Host "`n  [INFO] Unable to determine discrete per-monitor refresh rates via standard CIM provider."
}
Write-Host ""

Write-Host "=== Phase 2: Multi-Plane Overlay (MPO) Registry Status ==="
# 1. DWM OverlayTestMode
$dwmReg = 'HKLM:\SOFTWARE\Microsoft\Windows\Dwm'
$mpoDisabledDwm = $false
if (Test-Path $dwmReg) {
    $overlayTestMode = (Get-ItemProperty -Path $dwmReg -Name OverlayTestMode -ErrorAction SilentlyContinue).OverlayTestMode
    if ($null -ne $overlayTestMode) {
        if ($overlayTestMode -eq 5) {
            $mpoDisabledDwm = $true
            Write-Host "  [INFO] DWM OverlayTestMode: 5 (Multi-Plane Overlays are DISABLED via DWM registry test mode)."
        } else {
            Write-Host ("  [INFO] DWM OverlayTestMode: {0} (Custom test mode active)." -f $overlayTestMode)
        }
    } else {
        Write-Host "  [OK]   DWM OverlayTestMode: Not configured (Default: MPO enabled in Windows DWM)."
    }
} else {
    Write-Host "  [OK]   DWM registry key: Default Windows behavior."
}

# 2. GraphicsDrivers DisableMPO
$gfxReg = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
$mpoDisabledGfx = $false
if (Test-Path $gfxReg) {
    $disableMPO = (Get-ItemProperty -Path $gfxReg -Name DisableMPO -ErrorAction SilentlyContinue).DisableMPO
    if ($null -ne $disableMPO -and $disableMPO -eq 1) {
        $mpoDisabledGfx = $true
        Write-Host "  [INFO] GraphicsDrivers DisableMPO: 1 (Multi-Plane Overlays globally disabled at kernel driver level)."
    } else {
        Write-Host "  [OK]   GraphicsDrivers DisableMPO: Not configured (Default: Hardware overlays enabled)."
    }
}
Write-Host ""

Write-Host "=== Phase 3: DWM Presentation Queue & Modern Flip Optimizations ==="
# 1. Optimizations for windowed games
$windowedOptKey = 'HKCU:\Control Panel\Graphics\WindowedOptimization'
if (Test-Path $windowedOptKey) {
    $optEnabled = (Get-ItemProperty -Path $windowedOptKey -Name OptimizationEnabled -ErrorAction SilentlyContinue).OptimizationEnabled
    if ($optEnabled -eq 1) {
        Write-Host "  [OK]   Optimizations for windowed games: Enabled (Independent Flip Model for legacy windowed apps)."
    } else {
        Write-Host "  [INFO] Optimizations for windowed games: Disabled in user profile."
    }
} else {
    Write-Host "  [INFO] Optimizations for windowed games: Default system behavior."
}

# 2. Hardware-Accelerated GPU Scheduling (HAGS)
if (Test-Path $gfxReg) {
    $hagsMode = (Get-ItemProperty -Path $gfxReg -Name HwSchMode -ErrorAction SilentlyContinue).HwSchMode
    if ($hagsMode -eq 2) {
        Write-Host "  [OK]   Hardware-Accelerated GPU Scheduling (HAGS): Enabled (Level 2 - GPU handles presentation scheduling)."
    } elseif ($hagsMode -eq 1) {
        Write-Host "  [INFO] Hardware-Accelerated GPU Scheduling (HAGS): Disabled (Level 1 - CPU handles presentation scheduling)."
    } else {
        Write-Host "  [INFO] Hardware-Accelerated GPU Scheduling (HAGS): Default."
    }
}

# 3. Variable Refresh Rate (VRR)
$vrrKey = 'HKCU:\Control Panel\Graphics\VRR'
if (Test-Path $vrrKey) {
    $vrrVal = (Get-ItemProperty -Path $vrrKey -Name Enabled -ErrorAction SilentlyContinue).Enabled
    if ($vrrVal -eq 1) {
        Write-Host "  [OK]   Variable Refresh Rate (VRR): Globally Enabled."
    } else {
        Write-Host "  [INFO] Variable Refresh Rate (VRR): Disabled or unconfigured."
    }
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($uniqueRates.Count -gt 1) {
    Write-Host "[WARN] MISMATCHED REFRESH RATES DETECTED ACROSS MONITORS:"
    Write-Host "       1. Refresh Rate Alignment: If experiencing stutter while watching video on a"
    Write-Host "          secondary monitor, set the high-refresh gaming monitor to an exact integer"
    Write-Host "          multiple of 60Hz (e.g. 120Hz instead of 144Hz or 165Hz)."
    Write-Host "       2. Turn Off App Hardware Acceleration: In Discord, Spotify, or web browsers"
    Write-Host "          running on secondary displays, turn off hardware acceleration in application"
    Write-Host "          settings to prevent secondary surfaces from seizing DWM presentation sync."
} else {
    Write-Host "[OK]   DISPLAY PRESENTATION BASELINE HEALTHY:"
    Write-Host "       No multi-monitor refresh discrepancies identified."
}

if ($mpoDisabledDwm -or $mpoDisabledGfx) {
    Write-Host "`n[INFO] MULTI-PLANE OVERLAY (MPO) WORKAROUND IS CURRENTLY ACTIVE:"
    Write-Host "       MPO is disabled via registry. Desktop composition uses standard DWM buffers."
    Write-Host "       This resolves driver flickering bugs but may slightly increase desktop VRAM overhead."
} else {
    Write-Host "`n[INFO] MULTI-PLANE OVERLAY (MPO) WORKAROUND GUIDANCE:"
    Write-Host "       If experiencing black screen flashes, desktop flickering, or stutter during"
    Write-Host "       video playback in Chromium browsers, disable MPO by creating DWORD 'OverlayTestMode'"
    Write-Host "       with value 5 under HKLM:\SOFTWARE\Microsoft\Windows\Dwm."
}
""",
    ),
    TRO(
        "livekernelreports_silent_hang_extractor",
        "LiveKernelReports & Silent Hardware Hang Extractor",
        "Extracts silent kernel dump telemetry from %SystemRoot%\\LiveKernelReports (WATCHDOG, WHEA, USBHUB3, POH) and correlates silent hardware recoveries without a full BlueScreen.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: LiveKernelReports & Silent Hardware Hang Extractor"
Write-Host "PURPOSE   : Extracts silent kernel dump telemetry & correlates hardware recoveries"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER: LIVEKERNELREPORTS & SILENT HARDWARE TIMEOUTS"
Write-Host "  * LiveKernelReports Overview: When hardware components (GPU, PCIe controller, USB,"
Write-Host "    Wi-Fi) freeze or stop responding, modern Windows kernels generate a diagnostic"
Write-Host "    Live Dump in %SystemRoot%\LiveKernelReports instead of crashing with a full BlueScreen."
Write-Host "  * Silent Subsystem Recovery: Windows silently resets the faulty driver or device"
Write-Host "    (e.g. GPU TDR recovery, WHEA PCIe packet replay). The user experiences a momentary"
Write-Host "    screen freeze, audio glitch, or USB reset, while a dump file is saved in the background."
Write-Host "  * Critical Telemetry: Recurring live dumps indicate marginal hardware stability --"
Write-Host "    unstable GPU overclock/VRAM, PCIe riser cable signal degradation, or memory errors."
Write-Host ""

$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$lkrPath = "$env:SystemRoot\LiveKernelReports"
$recentDumpCount = 0
$totalDumpCount = 0

Write-Host "=== Phase 1: LiveKernelReports Dump Inventory ==="
if ($isAdm) {
    if (Test-Path $lkrPath) {
        $dumpFiles = @(Get-ChildItem -Path $lkrPath -Filter *.dmp -Recurse -ErrorAction SilentlyContinue)
        $totalDumpCount = $dumpFiles.Count
        Write-Host ("  [INFO] Total LiveKernelReports Dump Files Found: {0}" -f $totalDumpCount)

        $subdirs = @('WATCHDOG', 'WHEA', 'USBHUB3', 'POH', 'NDIS')
        foreach ($sd in $subdirs) {
            $sdPath = Join-Path $lkrPath $sd
            if (Test-Path $sdPath) {
                $subDumps = @(Get-ChildItem -Path $sdPath -Filter *.dmp -ErrorAction SilentlyContinue)
                if ($subDumps.Count -gt 0) {
                    Write-Host ("`n  Subdirectory [{0}]: {1} dump file(s)" -f $sd, $subDumps.Count)
                    foreach ($d in ($subDumps | Sort-Object LastWriteTime -Descending | Select-Object -First 5)) {
                        $sizeMB = [math]::Round($d.Length / 1MB, 2)
                        $ageDays = [math]::Round(((Get-Date) - $d.LastWriteTime).TotalDays, 1)

                        if ($ageDays -le 14) {
                            $recentDumpCount++
                            Write-Host ("    [BAD]  {0} ({1} MB, {2:yyyy-MM-dd HH:mm}, {3} days ago)" -f $d.Name, $sizeMB, $d.LastWriteTime, $ageDays)
                        } else {
                            Write-Host ("    [INFO] {0} ({1} MB, {2:yyyy-MM-dd HH:mm}, {3} days ago)" -f $d.Name, $sizeMB, $d.LastWriteTime, $ageDays)
                        }
                    }
                }
            }
        }

        if ($recentDumpCount -gt 0) {
            Write-Host ("`n  [BAD]  {0} silent hardware recovery dump(s) created in the last 14 days!" -f $recentDumpCount)
        } elseif ($totalDumpCount -gt 0) {
            Write-Host "`n  [OK]   Historical dumps present, but zero dumps created in the last 14 days."
        } else {
            Write-Host "  [OK]   Zero LiveKernelReports dumps found. System hardware watchdog is pristine."
        }
    } else {
        Write-Host "  [OK]   LiveKernelReports directory does not exist (No silent dumps have occurred)."
    }
} else {
    Write-Host "  [INFO] %SystemRoot%\LiveKernelReports folder access requires administrator privileges (skipped unelevated; run elevated to inventory dump files)."
}
Write-Host ""

Write-Host "=== Phase 2: Silent Hardware Error (WHEA-Logger) Event Correlation ==="
$lookbackDays = 14
$lookbackDate = (Get-Date).AddDays(-$lookbackDays)
$wheaTotal = 0

try {
    $wheaEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Microsoft-Windows-WHEA-Logger';
        Id = @(17, 18, 19, 47);
        StartTime = $lookbackDate
    } -MaxEvents 100 -ErrorAction Stop

    $wheaTotal = @($wheaEvents).Count
    if ($wheaTotal -gt 0) {
        Write-Host ("  [BAD]  Detected {0} WHEA-Logger hardware error event(s) in the last {1} days!" -f $wheaTotal, $lookbackDays)

        $groupedWhea = $wheaEvents | Group-Object Id
        foreach ($grp in $groupedWhea) {
            $evId = [int]$grp.Name
            $evDesc = switch ($evId) {
                17 { "PCIe Corrected Error (Bus/Device/Function packet retry)" }
                18 { "Machine Check Exception (Processor Core hardware timeout)" }
                19 { "Processor Hardware Error (Cache hierarchy / interconnect)" }
                47 { "Memory Corrected Error (ECC / DRAM read retry)" }
                Default { "Hardware Error Architecture Event" }
            }
            $newest = ($grp.Group | Sort-Object TimeCreated -Descending)[0]
            Write-Host ("         • [ID {0,2}] Count: {1,3} | Newest: {2:yyyy-MM-dd HH:mm} | {3}" -f $evId, $grp.Count, $newest.TimeCreated, $evDesc)
        }
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "  [OK]   Zero WHEA-Logger hardware error events logged in the last 14 days."
    } else {
        Write-Host "  [INFO] WHEA event log query note: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "=== Phase 3: GPU & Storage Watchdog Silent Recoveries ==="
# 1. GPU TDR Recovery (Event 4101, Display)
try {
    $tdrEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'Display';
        Id = 4101;
        StartTime = $lookbackDate
    } -MaxEvents 50 -ErrorAction Stop

    $tdrCount = @($tdrEvents).Count
    if ($tdrCount -gt 0) {
        Write-Host ("  [BAD]  GPU Display Driver TDR Recoveries (Event 4101): {0} occurrence(s) in last {1} days!" -f $tdrCount, $lookbackDays)
        $lastTdr = ($tdrEvents | Sort-Object TimeCreated -Descending)[0]
        Write-Host ("         Latest TDR Event: {0:yyyy-MM-dd HH:mm:ss} | {1}" -f $lastTdr.TimeCreated, ($lastTdr.Message -split "`r?`n")[0])
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "  [OK]   Zero GPU Display Driver TDR recoveries (Event 4101) in last 14 days."
    } else {
        Write-Host "  [INFO] Display driver event query: $($_.Exception.Message)"
    }
}

# 2. Storage Disk IO Retries (Event 153, disk)
try {
    $disk153Events = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = 'disk';
        Id = 153;
        StartTime = $lookbackDate
    } -MaxEvents 50 -ErrorAction Stop

    $disk153Count = @($disk153Events).Count
    if ($disk153Count -gt 0) {
        Write-Host ("  [WARN] Storage Disk IO Retries (Event 153): {0} occurrence(s) in last {1} days." -f $disk153Count, $lookbackDays)
        $lastDisk = ($disk153Events | Sort-Object TimeCreated -Descending)[0]
        Write-Host ("         Latest Retry: {0:yyyy-MM-dd HH:mm:ss} | {1}" -f $lastDisk.TimeCreated, ($lastDisk.Message -split "`r?`n")[0])
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "  [OK]   Zero Storage Disk IO retries (Event 153) in last 14 days."
    } else {
        Write-Host "  [INFO] Disk event query: $($_.Exception.Message)"
    }
}

# 3. Storage Controller Resets (Event 129, storahci/stornvme/iaStorA)
try {
    $rstEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System';
        ProviderName = @('storahci', 'stornvme', 'iaStorA');
        Id = 129;
        StartTime = $lookbackDate
    } -MaxEvents 50 -ErrorAction Stop

    $rstCount = @($rstEvents).Count
    if ($rstCount -gt 0) {
        Write-Host ("  [BAD]  Storage Controller Resets (Event 129): {0} occurrence(s) in last {1} days!" -f $rstCount, $lookbackDays)
        $lastRst = ($rstEvents | Sort-Object TimeCreated -Descending)[0]
        Write-Host ("         Latest Reset: {0:yyyy-MM-dd HH:mm:ss} | {1}" -f $lastRst.TimeCreated, ($lastRst.Message -split "`r?`n")[0])
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "  [OK]   Zero Storage Controller resets (Event 129) in last 14 days."
    } else {
        Write-Host "  [INFO] Storage controller event query: $($_.Exception.Message)"
    }
}
Write-Host ""

Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($recentDumpCount -gt 0 -or $wheaTotal -gt 0) {
    Write-Host "[BAD]  HARDWARE INSTABILITY OR SILENT DRIVER HANGS DETECTED:"
    Write-Host "       1. Memory & CPU Stability Testing: If WHEA-Logger events 18/19 or WHEA dumps"
    Write-Host "          are present, run MemTest86 or TestMem5 to verify RAM integrity. Turn off"
    Write-Host "          or relax aggressive CPU Curve Optimizer, PBO, or XMP/EXPO memory profiles."
    Write-Host "       2. GPU & PCIe Riser Diagnostics: For WATCHDOG dumps or WHEA 17 errors, reseat"
    Write-Host "          the graphics card in the primary PCIe slot. If using a vertical GPU mount with"
    Write-Host "          a PCIe riser cable, set PCIe link speed in BIOS to Gen 3 instead of Gen 4/Auto."
    Write-Host "       3. Power Delivery: High-end graphics cards exhibit high transient power spikes."
    Write-Host "          Ensure separate dedicated 8-pin PCIe power cables are connected to the GPU"
    Write-Host "          rather than daisy-chained pigtail splitters."
    Write-Host "       4. Storage Health: If Disk 153 retries or Controller 129 resets occur, inspect"
    Write-Host "          M.2 NVMe SSD seating, monitor drive operating temperatures, and check SMART wear."
} else {
    Write-Host "[OK]   NO SILENT HARDWARE HANGS OR WHEA ERRORS:"
    Write-Host "       Kernel watchdog and hardware architecture telemetry report stable operation."
}
""",
    ),
]
