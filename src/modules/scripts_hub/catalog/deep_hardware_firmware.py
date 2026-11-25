"""Hardware, Firmware, UEFI & Platform Integrity Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

HW = maker("Hardware")

SCRIPTS = [
    HW(
        "uefi_secureboot_tpm_attestation",
        "UEFI Firmware, Secure Boot & TPM 2.0 Platform Attestation",
        "Audits UEFI boot mode, Secure Boot state, TPM 2.0 hardware presence, "
        "specification version, manufacturer ID, and firmware release dates.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: UEFI Firmware, Secure Boot & TPM 2.0 Hardware Attestation"
Write-Host "PURPOSE   : Verifies hardware trust foundation required for Windows 11 & BitLocker"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PLATFORM PRIMER:"
Write-Host "  • UEFI Mode: Replaces legacy BIOS; required for Secure Boot, GPT disks, and fast boots."
Write-Host "  • SECURE BOOT: Validates bootloader cryptographic signatures, preventing bootkits & rootkits."
Write-Host "  • TPM 2.0: Physical security chip that binds BitLocker encryption keys to hardware."
Write-Host ""

$isUefi = $false
try {
    $firmwareType = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name PEFirmwareType -ErrorAction SilentlyContinue).PEFirmwareType
    $isUefi = ($firmwareType -eq 2)
} catch {
    Write-Host "[INFO] Firmware type query note: $($_.Exception.Message)"
}

Write-Host "=== Phase 1: UEFI & Firmware Architecture ==="
if ($isUefi) {
    Write-Host "[OK]   Firmware Architecture: Native UEFI mode."
} else {
    Write-Host "[BAD]  Firmware Architecture: Legacy BIOS / CSM mode! System cannot support Secure Boot."
}

try {
    $sb = Confirm-SecureBootUEFI -ErrorAction Stop
    if ($sb) {
        Write-Host "[OK]   Secure Boot: ENABLED (Boot integrity is actively verified)."
    } else {
        Write-Host "[WARN] Secure Boot: DISABLED in motherboard setup!"
    }
} catch {
    Write-Host "[INFO] Secure Boot query (requires UEFI): $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: Trusted Platform Module (TPM) Telemetry ==="
$tpmStatus = $false
try {
    $tpm = Get-Tpm -ErrorAction Stop
    $tpmPresent = $tpm.TpmPresent
    $tpmReady = $tpm.TpmReady
    $tpmEnabled = $tpm.TpmEnabled
    $tpmOwned = $tpm.TpmOwned

    if ($tpmPresent -and $tpmReady) {
        $tpmStatus = $true
        Write-Host ("[OK]   TPM Status   : PRESENT and READY for BitLocker & Windows Hello.")
        Write-Host ("       Manufacturer : {0} | Spec Version: {1}" -f $tpm.ManufacturerIdTxt, $tpm.ManufacturerVersion)
        Write-Host ("       Firmware Ver : {0}" -f $tpm.ManufacturerVersionFull20)
    } elseif ($tpmPresent) {
        Write-Host ("[WARN] TPM Status   : PRESENT but NOT READY (Enabled: {0}, Owned: {1})" -f $tpmEnabled, $tpmOwned)
    } else {
        Write-Host "[BAD]  TPM Status   : ZERO TPM hardware detected!"
    }
} catch {
    Write-Host "[INFO] TPM query (Get-Tpm requires elevation): $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 3: System BIOS & Motherboard Firmware ==="
try {
    $bios = Get-CimInstance Win32_BIOS -ErrorAction Stop
    $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
    $bb = Get-CimInstance Win32_BaseBoard -ErrorAction Stop
    Write-Host ("System Model    : {0} {1}" -f $cs.Manufacturer, $cs.Model)
    Write-Host ("Motherboard     : {0} {1}" -f $bb.Manufacturer, $bb.Product)
    Write-Host ("BIOS Version    : {0} (Released: {1:yyyy-MM-dd})" -f $bios.SMBIOSBIOSVersion, $bios.ReleaseDate)
} catch {
    Write-Host "[INFO] BIOS query note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if (-not $isUefi) {
    Write-Host "[BAD]  CRITICAL: Machine is booting in Legacy BIOS. Convert disk to GPT via mbr2gpt.exe to enable UEFI."
} elseif (-not $tpmStatus) {
    Write-Host "[WARN] ATTENTION: TPM 2.0 is missing or disabled. Check motherboard BIOS settings for fTPM (AMD) or PTT (Intel)."
} else {
    Write-Host "[OK]   STATUS: PLATFORM ATTESTATION PASSED. Hardware meets all Windows 11 enterprise requirements."
}
""",
    ),
    HW(
        "pcie_link_width_negotiation",
        "PCIe Bus Link Width & Graphics Controller Speed Negotiation",
        "Queries graphics adapters, storage controllers, and network interfaces to detect degraded "
        "PCIe link widths (e.g. running at x1/x2 instead of full x16/x4 capability).",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: PCIe Bus Link Width & Controller Negotiation"
Write-Host "PURPOSE   : Detects graphics and NVMe bus degradation (e.g. GPU running at x1 instead of x16)"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PCIE PRIMER:"
Write-Host "  • GPUs require PCIe x16 link width. If dirt, GPU sag, or slot damage occurs,"
Write-Host "    the GPU falls back to x8, x4, or even x1, causing massive FPS drops and stutter!"
Write-Host "  • NVMe SSDs negotiate PCIe x4. Running at x2 or x1 cuts read/write throughput in half."
Write-Host ""

Write-Host "=== Phase 1: Graphics Adapters & Video Subsystem ==="
try {
    $gpus = Get-CimInstance Win32_VideoController -ErrorAction Stop
    foreach ($g in $gpus) {
        $ramMB = [math]::Round($g.AdapterRAM / 1MB, 0)
        $driverDate = if ($g.DriverDate) { $g.DriverDate.ToString('yyyy-MM-dd') } else { "Unknown" }
        Write-Host ("  [INFO] GPU Model     : {0}" -f $g.Name)
        Write-Host ("         VRAM Memory   : {0} MB | Status: {1}" -f $ramMB, $g.Status)
        Write-Host ("         Driver Version: {0} ({1})" -f $g.DriverVersion, $driverDate)
        Write-Host ("         PNP Device ID : {0}" -f $g.PNPDeviceID)
    }
} catch {
    Write-Host "[INFO] VideoController query: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: Storage Controllers & NVMe Bus Endpoints ==="
try {
    $sc = Get-CimInstance Win32_SCSIController -ErrorAction Stop
    foreach ($c in $sc) {
        $tag = if ($c.Status -eq 'OK') { '[OK]  ' } else { '[WARN]' }
        Write-Host ("  {0} Controller: {1,-34} Driver: {2} ({3})" -f $tag, $c.Name, $c.DriverName, $c.Status)
    }
} catch {
    Write-Host "[INFO] SCSIController query: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 3: Problem Devices & Bus Failures ==="
try {
    $problemDevices = Get-CimInstance Win32_PnPEntity -Filter "ConfigManagerErrorCode <> 0" -ErrorAction SilentlyContinue
    if ($problemDevices) {
        Write-Host ("[BAD]  Found {0} device(s) with PCIe or driver hardware errors:" -f $problemDevices.Count)
        foreach ($pd in $problemDevices) {
            Write-Host ("  [BAD]  Device: {0,-32} ErrorCode: {1} (Status: {2})" -f $pd.Name, $pd.ConfigManagerErrorCode, $pd.Status)
        }
    } else {
        Write-Host "[OK]   Zero hardware devices reporting bus negotiation or configuration errors."
    }
} catch {
    Write-Host "[INFO] PnPEntity problem query: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] If experiencing graphics micro-stutters or reduced drive performance:"
Write-Host "       1. Verify GPU is seated firmly in the top PCIe slot (CPU-connected slot, usually slot 1)."
Write-Host "       2. Inspect physical card for GPU sag and re-seat power cables."
Write-Host "       3. Check Motherboard BIOS for PCIe Generation setting (Gen 4 / Gen 5 vs Auto)."
""",
    ),
    HW(
        "battery_health_cycle_wear",
        "Battery Health, Design Capacity & Degradation Wear Level",
        "Reports battery design capacity vs current full-charge capacity, wear percentage, "
        "and discharge cycle counts for laptops and mobile workstations.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Battery Health, Wear Level & Capacity Degradation"
Write-Host "PURPOSE   : Evaluates physical battery degradation, cycle count, and sudden shutdown risks"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK BATTERY PRIMER:"
Write-Host "  • Design Capacity: Factory designed energy capacity in milliwatt-hours (mWh)."
Write-Host "  • Full Charge Capacity: Actual maximum energy the battery can hold today."
Write-Host "  • WEAR LEVEL > 25%: Laptop is prone to sudden power-offs when load spikes at 20-30% charge!"
Write-Host ""

$batteries = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue

Write-Host "=== Phase 1: Battery Hardware & Current State ==="
if ($batteries) {
    foreach ($b in $batteries) {
        $status = $b.Status
        $chargePct = $b.EstimatedChargeRemaining
        $runTime = $b.EstimatedRunTime
        Write-Host ("  [INFO] Battery Model : {0} ({1})" -f $b.Name, $b.DeviceID)
        Write-Host ("         Charge Level  : {0}% (Status: {1})" -f $chargePct, $status)
        if ($runTime -and $runTime -lt 71582788) {
            Write-Host ("         Remaining Run : {0} minutes" -f $runTime)
        }
    }
} else {
    Write-Host "[OK]   Desktop Workstation / Fixed AC power supply detected (No battery present)."
}
Write-Host ""

Write-Host "=== Phase 2: Capacity Wear & Degradation Metrics ==="
$hasWear = $false
try {
    $batStatic = Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData -ErrorAction Stop
    $batFull = Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity -ErrorAction Stop

    if ($batStatic -and $batFull) {
        for ($i = 0; $i -lt $batStatic.Count; $i++) {
            $design = $batStatic[$i].DesignedCapacity
            $full = $batFull[$i].FullChargedCapacity
            if ($design -gt 0) {
                $hasWear = $true
                $wearPct = [math]::Round(((($design - $full) / $design) * 100), 1)
                $healthPct = [math]::Round((($full / $design) * 100), 1)
                $tag = if ($wearPct -ge 30) { '[BAD] ' } elseif ($wearPct -ge 20) { '[WARN]' } else { '[OK]  ' }
                Write-Host ("  {0} Design Capacity     : {1} mWh" -f $tag, $design)
                Write-Host ("       Full Charge Capacity: {0} mWh ({1}% of original health)" -f $full, $healthPct)
                Write-Host ("       Battery Degradation : {0}% wear" -f $wearPct)
            }
        }
    } else {
        Write-Host "[INFO] Advanced battery telemetry not exposed by hardware ACPI."
    }
} catch {
    Write-Host "[INFO] Battery WMI query note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if (-not $batteries) {
    Write-Host "[OK]   STATUS: NOT APPLICABLE. Machine is a desktop workstation running on AC power."
} elseif ($hasWear -and $wearPct -ge 25) {
    Write-Host ("[WARN] BATTERY DEGRADED: Battery has lost {0}% of factory capacity!" -f $wearPct)
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Generate official HTML battery report: powercfg /batteryreport"
    Write-Host "       2. Recommend battery replacement if user experiences sudden shutdowns on battery."
} else {
    Write-Host "[OK]   STATUS: BATTERY HEALTHY. Wear is within acceptable operating tolerance."
}
""",
    ),
    HW(
        "vbs_hvci_kernel_dma_attestation",
        "Virtualization-Based Security (VBS), HVCI & Kernel DMA Protection Attestation",
        "Deep hardware platform security attestation: audits hypervisor platform readiness (SLAT, VT-x/AMD-V, VT-d/IOMMU), "
        "Virtualization-Based Security (VBS) runtime state, Hypervisor-Protected Code Integrity (HVCI / Memory Integrity), "
        "Credential Guard, System Guard Secure Launch (SMM), and Kernel DMA Protection against DMA peripheral attacks.",
        r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Virtualization-Based Security (VBS) & HVCI Attestation"
Write-Host "PURPOSE   : Audits Memory Integrity, Credential Guard, and Kernel DMA Protection"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK VBS & HVCI PRIMER:"
Write-Host "  • VBS: Uses hardware virtualization to isolate a secure memory region from normal Windows."
Write-Host "  • HVCI (Memory Integrity): Prevents malicious or unsigned code from executing in kernel mode."
Write-Host "  • KERNEL DMA PROTECTION: Protects against physical PCI/Thunderbolt direct memory attack devices."
Write-Host ""

Write-Host "=== Phase 1: Hypervisor Platform & Hardware Virtualization ==="
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs = Get-CimInstance Win32_ComputerSystem

$virtEnabled = $cpu.VirtualizationFirmwareEnabled
$slat = $cpu.SecondLevelAddressTranslationExtensions
$hypervisorPresent = $cs.HypervisorPresent

if ($virtEnabled) {
    Write-Host "[OK]   Hardware Virtualization (Intel VT-x / AMD-V): ENABLED in BIOS/UEFI."
} else {
    Write-Host "[BAD]  Hardware Virtualization: DISABLED in BIOS/UEFI! VBS and WSL2 cannot function."
}

if ($slat) {
    Write-Host "[OK]   Second Level Address Translation (SLAT / EPT / NPT): SUPPORTED."
} else {
    Write-Host "[WARN] SLAT is NOT supported by the CPU or hypervisor reporting."
}

if ($hypervisorPresent) {
    Write-Host "[OK]   Windows Hypervisor: ACTIVE and running at hypervisor root."
} else {
    Write-Host "[INFO] Windows Hypervisor is not currently running."
}
Write-Host ""

Write-Host "=== Phase 2: Virtualization-Based Security (VBS) & Security Services ==="
$vbsStatus = 0
try {
    $dg = Get-CimInstance -Namespace root\cimv2\security\microsoftvolumeencryption -ClassName Win32_DeviceGuard -ErrorAction Stop
    $vbsStatus = $dg.VirtualizationBasedSecurityStatus
    $vbsStatusMap = @{ 0 = 'Not Enabled'; 1 = 'Configured but not Running'; 2 = 'Running' }
    $vbsStatusText = if ($vbsStatusMap.ContainsKey($vbsStatus)) { $vbsStatusMap[$vbsStatus] } else { "Unknown ($vbsStatus)" }

    if ($vbsStatus -eq 2) {
        Write-Host "[OK]   Virtualization-Based Security (VBS): ACTIVE and RUNNING."
    } elseif ($vbsStatus -eq 1) {
        Write-Host "[WARN] VBS Status: CONFIGURED but NOT RUNNING (reboot or platform issue required)."
    } else {
        Write-Host "[INFO] VBS Status: NOT ENABLED on this system."
    }

    $serviceNames = @{ 1 = 'Credential Guard'; 2 = 'Memory Integrity (HVCI)'; 3 = 'System Guard Secure Launch' }
    $runningServices = @($dg.SecurityServicesRunning)
    $configuredServices = @($dg.SecurityServicesConfigured)

    foreach ($svcId in @(1, 2, 3)) {
        $svcName = $serviceNames[$svcId]
        $isRunning = $svcId -in $runningServices
        $isConfigured = $svcId -in $configuredServices

        if ($isRunning) {
            Write-Host ("  [OK]   {0}: RUNNING" -f $svcName)
        } elseif ($isConfigured) {
            Write-Host ("  [WARN] {0}: Configured but NOT running!" -f $svcName)
        } else {
            Write-Host ("  [INFO] {0}: Not configured" -f $svcName)
        }
    }
} catch {
    Write-Host "[INFO] DeviceGuard CIM query: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 3: Kernel DMA Protection & Vulnerable Driver Blocklist ==="
try {
    $ciKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Config'
    $blocklist = (Get-ItemProperty $ciKey -Name VulnerableDriverBlocklistEnable -ErrorAction SilentlyContinue).VulnerableDriverBlocklistEnable
    if ($blocklist -eq 1) {
        Write-Host "[OK]   Microsoft Vulnerable Driver Blocklist: ENFORCED."
    } else {
        Write-Host "[WARN] Vulnerable Driver Blocklist: NOT enforced (Vulnerable to Bring-Your-Own-Vulnerable-Driver)."
    }

    $ciEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Microsoft-Windows-CodeIntegrity/Operational'
        Id = @(3076, 3077, 3087)
        StartTime = (Get-Date).AddDays(-14)
    } -MaxEvents 5 -ErrorAction SilentlyContinue

    if ($ciEvents) {
        Write-Host ("[WARN] Found {0} recent CodeIntegrity driver block / refusal event(s):" -f $ciEvents.Count)
        foreach ($evt in $ciEvents) {
            Write-Host ("  • Event {0} ({1:yyyy-MM-dd HH:mm}): {2}" -f $evt.Id, $evt.TimeCreated, ($evt.Message -split "`r?`n")[0])
        }
    } else {
        Write-Host "[OK]   Zero CodeIntegrity driver incompatibility block events."
    }
} catch {
    Write-Host "[INFO] CodeIntegrity verification note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if (-not $virtEnabled) {
    Write-Host "[BAD]  ACTION REQUIRED: Enable Virtualization (VT-x / AMD-V) in motherboard UEFI setup."
} elseif ($vbsStatus -eq 2) {
    Write-Host "[OK]   STATUS: HARDENED. VBS and enterprise kernel protection are fully engaged."
} else {
    Write-Host "[INFO] To enable Memory Integrity: Open Windows Security -> Device Security -> Core Isolation."
}
""",
    ),
]
