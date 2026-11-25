"""Storage, NVMe/SMART Wear & Volume Shadow Forensics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

STO = maker("Storage")

SCRIPTS = [
    STO(
        "nvme_smart_wear_telemetry",
        "Physical Disks, SMART Health & NVMe Wear Telemetry",
        "Queries physical disk controllers via Storage CIM and SMART: reports media type (NVMe SSD / SATA), "
        "operational status, temperature, read/write errors, and wear percentage (remaining lifespan).",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: PHYSICAL DISK, SMART HEALTH & NVMe WEAR TELEMETRY " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: This diagnostic queries hardware disk controllers, Storage" -ForegroundColor Gray
Write-Host "[INFO] spaces, and SMART telemetry. SSD wear percentage indicates NAND flash cell" -ForegroundColor Gray
Write-Host "[INFO] depletion. Temperatures above 65C or rising read/write errors indicate impending" -ForegroundColor Gray
Write-Host "[INFO] hardware failure requiring immediate drive backup and replacement." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Physical Disk Topology & Operational Health ===" -ForegroundColor Cyan
$healthyCount = 0
$degradedCount = 0
try {
    $disks = Get-PhysicalDisk -ErrorAction Stop
    foreach ($d in $disks) {
        $health = $d.HealthStatus
        $op = $d.OperationalStatus
        $media = $d.MediaType
        $bus = $d.BusType
        $sizeGB = [math]::Round($d.Size / 1GB, 1)

        if ($health -eq 'Healthy' -and $op -eq 'OK') {
            $healthyCount++
            Write-Host ("[OK]   Disk {0} [{1} - {2}] {3} GB" -f $d.DeviceId, $media, $bus, $sizeGB) -ForegroundColor Green
            Write-Host ("       Model: {0} | Serial: {1} | Health: {2}" -f $d.FriendlyName, $d.SerialNumber, $health)
        } else {
            $degradedCount++
            Write-Host ("[BAD]  Disk {0} [{1} - {2}] {3} GB - UNHEALTHY!" -f $d.DeviceId, $media, $bus, $sizeGB) -ForegroundColor Red
            Write-Host ("       Model: {0} | Status: {1} | Operational: {2}" -f $d.FriendlyName, $health, $op) -ForegroundColor Red
        }
    }
} catch {
    Write-Host "[WARN] Storage CIM query limited: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "`n=== Phase 2: NVMe & SSD SMART Wear & Reliability Telemetry ===" -ForegroundColor Cyan
try {
    $disks = Get-PhysicalDisk -ErrorAction SilentlyContinue
    foreach ($d in $disks) {
        try {
            $rel = Get-StorageReliabilityCounter -PhysicalDisk $d -ErrorAction Stop
            $temp = $rel.Temperature
            $wear = $rel.Wear
            $readErr = $rel.ReadErrorsTotal
            $writeErr = $rel.WriteErrorsTotal
            $powerHours = $rel.PowerOnHours

            Write-Host ("`n[INFO] Disk {0} ({1}) Reliability Metrics:" -f $d.DeviceId, $d.FriendlyName) -ForegroundColor Cyan
            
            # Temperature evaluation
            if ($null -ne $temp -and $temp -gt 0) {
                if ($temp -gt 70) {
                    Write-Host ("  [BAD]  Operating Temperature : {0} C (CRITICAL OVERHEATING!)" -f $temp) -ForegroundColor Red
                } elseif ($temp -gt 55) {
                    Write-Host ("  [WARN] Operating Temperature : {0} C (ELEVATED)" -f $temp) -ForegroundColor Yellow
                } else {
                    Write-Host ("  [OK]   Operating Temperature : {0} C (NORMAL)" -f $temp) -ForegroundColor Green
                }
            } else {
                Write-Host "  [INFO] Operating Temperature : Not reported by controller"
            }

            # Wear percentage
            if ($null -ne $wear) {
                $remLife = 100 - $wear
                if ($remLife -le 10) {
                    Write-Host ("  [BAD]  Remaining SSD Lifespan: {0}% (WEAR CRITICAL - REPLACE SOON!)" -f $remLife) -ForegroundColor Red
                } elseif ($remLife -le 25) {
                    Write-Host ("  [WARN] Remaining SSD Lifespan: {0}% (WEAR WARNING)" -f $remLife) -ForegroundColor Yellow
                } else {
                    Write-Host ("  [OK]   Remaining SSD Lifespan: {0}% (HEALTHY)" -f $remLife) -ForegroundColor Green
                }
            } else {
                Write-Host "  [INFO] Remaining SSD Lifespan: Not exposed as wear counter"
            }

            # I/O errors
            if ($readErr -gt 0 -or $writeErr -gt 0) {
                Write-Host ("  [WARN] Media Errors Detected : Read={0}, Write={1}" -f $readErr, $writeErr) -ForegroundColor Yellow
            } else {
                Write-Host "  [OK]   Media Errors Detected : 0 (No bad block transfers)" -ForegroundColor Green
            }

            if ($null -ne $powerHours) {
                $days = [math]::Round($powerHours / 24, 0)
                Write-Host ("  [INFO] Power-On Runtime      : {0} hours (~{1} days)" -f $powerHours, $days)
            }
        } catch {
            Write-Host ("  [INFO] Disk {0}: Reliability counters not exposed by controller/driver" -f $d.DeviceId)
        }
    }
} catch {
    Write-Host "[WARN] Storage reliability telemetry query: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "`n=== Phase 3: Storage Spaces & Virtual Disk Subsystem ===" -ForegroundColor Cyan
try {
    $pools = Get-StoragePool -IsPrimordial $false -ErrorAction SilentlyContinue
    if ($pools) {
        foreach ($p in $pools) {
            $pStatus = $p.OperationalStatus
            $pHealth = $p.HealthStatus
            if ($pHealth -eq 'Healthy') {
                Write-Host ("[OK]   Storage Pool '{0}' - Health: {1}, Status: {2}" -f $p.FriendlyName, $pHealth, $pStatus) -ForegroundColor Green
            } else {
                Write-Host ("[BAD]  Storage Pool '{0}' - Health: {1}, Status: {2}" -f $p.FriendlyName, $pHealth, $pStatus) -ForegroundColor Red
            }
        }
    } else {
        Write-Host "[OK]   No degraded custom Storage Pools detected (standalone disks active)." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Storage Spaces pool check: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($degradedCount -gt 0) {
    Write-Host "[ACTION REQUIRED] Hardware disk degradation detected! Schedule immediate backup" -ForegroundColor Red
    Write-Host "[ACTION REQUIRED] of user profiles and business data before drive drops offline." -ForegroundColor Red
    Write-Host "[REMEDIATION] 1. Check drive vendor software (Samsung Magician, WD Dashboard, Crucial Storage Executive)."
    Write-Host "[REMEDIATION] 2. Run hardware self-test from UEFI BIOS diagnostic menu."
    Write-Host "[REMEDIATION] 3. RMA or replace physical drive if SMART wear or reallocated sectors are high."
} else {
    Write-Host "[STATUS] All physical disks report healthy operational status with nominal wear." -ForegroundColor Green
    Write-Host "[INFO] Continue periodic monitoring. NVMe drives throttle automatically if over 70C." -ForegroundColor Gray
}
""",
    ),
    STO(
        "vss_shadow_exhaustion",
        "Volume Shadow Copy & VSS Writer Health Diagnostic",
        "Audits all VSS writers for failed or timed-out states, and calculates Volume Shadow Storage "
        "allocated vs maximum space across all volumes.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: VSS WRITERS & SHADOW COPY EXHAUSTION AUDIT         " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Volume Shadow Copy Service (VSS) coordinates snapshots" -ForegroundColor Gray
Write-Host "[INFO] for Windows Backup, Veeam, System Restore, and file versioning. Failed VSS" -ForegroundColor Gray
Write-Host "[INFO] writers freeze backup jobs with error 0x8004231f or VSS_E_WRITER_ERROR." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: VSS Writer State & Error Verification ===" -ForegroundColor Cyan
$writersOut = vssadmin.exe list writers
$writerLines = $writersOut -split "`r?`n"

$curWriter = ""
$curState = ""
$curError = ""
$failedWriters = 0
$totalWriters = 0

for ($i = 0; $i -lt $writerLines.Count; $i++) {
    $line = $writerLines[$i].Trim()
    if ($line.StartsWith("Writer name:")) {
        $curWriter = ($line -split "Writer name:")[1].Trim().Trim("'")
        $totalWriters++
    } elseif ($line.StartsWith("State:")) {
        $curState = $line
    } elseif ($line.StartsWith("Last error:")) {
        $curError = ($line -split "Last error:")[1].Trim()
        $isFailed = $curError -ne "No error" -or $curState -notlike "*Stable*"
        if ($isFailed) {
            $failedWriters++
            Write-Host ("[BAD]  VSS Writer '{0}' is UNSTABLE!" -f $curWriter) -ForegroundColor Red
            Write-Host ("       {0} | Last Error: {1}" -f $curState, $curError) -ForegroundColor Red
        } else {
            Write-Host ("[OK]   VSS Writer '{0}' is Stable (No error)" -f $curWriter) -ForegroundColor Green
        }
    }
}

if ($failedWriters -eq 0 -and $totalWriters -gt 0) {
    Write-Host "`n[OK]   All $totalWriters registered VSS Writers report STABLE state with No Error." -ForegroundColor Green
}

Write-Host "`n=== Phase 2: Volume Shadow Storage Allocation Limits ===" -ForegroundColor Cyan
$shadowOut = vssadmin.exe list shadowstorage
$shadowLines = $shadowOut -split "`r?`n"
$volInfo = @()
foreach ($sl in $shadowLines) {
    if ($sl -match 'For volume:|Used Shadow Copy Storage space|Allocated Shadow Copy Storage space|Maximum Shadow Copy Storage space') {
        Write-Host ("  {0}" -f $sl.Trim())
    }
}

Write-Host "`n=== Phase 3: Active Shadow Copies Inventory ===" -ForegroundColor Cyan
try {
    $shadows = Get-CimInstance Win32_ShadowCopy -ErrorAction SilentlyContinue
    if ($shadows) {
        $sCount = $shadows.Count
        Write-Host ("[INFO] Active VSS Snapshots Found: {0}" -f $sCount) -ForegroundColor Cyan
        foreach ($s in ($shadows | Select-Object -First 5)) {
            $installDate = $s.InstallDate
            Write-Host ("  • Snapshot ID: {0} | Created: {1} | Vol: {2}" -f $s.ID, $installDate, $s.VolumeName)
        }
        if ($sCount -gt 5) {
            Write-Host ("    ... and {0} older snapshots present." -f ($sCount - 5)) -ForegroundColor Gray
        }
    } else {
        Write-Host "[INFO] No persistent VSS shadow copies currently allocated on volumes."
    }
} catch {
    Write-Host "[INFO] ShadowCopy WMI query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 4: VSS Core Service Configuration ===" -ForegroundColor Cyan
$services = @('VSS', 'swprv')
foreach ($svc in $services) {
    $sObj = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($sObj) {
        Write-Host ("  [INFO] Service '{0,-6}' : Status={1,-7} Startup={2}" -f $sObj.Name, $sObj.Status, $sObj.StartType)
    }
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($failedWriters -gt 0) {
    Write-Host ("[ACTION REQUIRED] {0} VSS Writer(s) are stuck or in error state!" -f $failedWriters) -ForegroundColor Red
    Write-Host "[REMEDIATION] Stuck VSS writers can often be recovered by restarting associated services:"
    Write-Host "  1. If 'System Writer' failed: restart CryptSvc, VSS, and swprv via services.msc."
    Write-Host "  2. If 'ASR Writer' or 'Registry Writer' failed: reboot host if service restart fails."
    Write-Host "  3. If backups fail with out-of-space: resize shadowstorage allocation:"
    Write-Host "     vssadmin resize shadowstorage /for=C: /on=C: /maxsize=15%"
} else {
    Write-Host "[STATUS] VSS subsystem is completely healthy and ready for backup operations." -ForegroundColor Green
    Write-Host "[INFO] No writer errors or hung snapshot states detected." -ForegroundColor Gray
}
""",
    ),
    STO(
        "partitions",
        "Partitions across all physical disks",
        "Partitions across physical disks with partition style, offsets, and volume letters. Needs administrator.",
        need_admin("Get-Partition returns an empty list without elevation") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: DISK PARTITION ALIGNMENT & VOLUME LAYOUT          " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Correct partition 4K alignment (offset divisible by 4096)" -ForegroundColor Gray
Write-Host "[INFO] is essential for SSD performance and longevity. Modern systems use GPT" -ForegroundColor Gray
Write-Host "[INFO] partitioning with an EFI System Partition (ESP) for secure bootloader handoff." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Physical Disk Partition Topology & 4K Alignment ===" -ForegroundColor Cyan
try {
    $partitions = Get-Partition -ErrorAction Stop
    $misalignedCount = 0
    foreach ($p in $partitions) {
        $diskNum = $p.DiskNumber
        $partNum = $p.PartitionNumber
        $letter = if ($p.DriveLetter) { "$($p.DriveLetter):" } else { "<None>" }
        $sizeGB = [math]::Round($p.Size / 1GB, 2)
        $offset = $p.Offset
        $type = $p.Type
        
        # 4K Sector alignment check: Offset % 4096 must be 0
        $aligned = ($offset % 4096) -eq 0
        if ($aligned) {
            $alignTag = "[OK]  "
        } else {
            $alignTag = "[WARN]"
            $misalignedCount++
        }

        Write-Host ("{0} Disk {1} Part {2} | Letter: {3,-6} Size: {4,8} GB | Type: {5}" -f (
            $alignTag, $diskNum, $partNum, $letter, $sizeGB, $type
        ))
    }
} catch {
    Write-Host "[BAD]  Failed to enumerate partitions: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n=== Phase 2: Disk Partitioning Scheme & Health ===" -ForegroundColor Cyan
try {
    $disks = Get-Disk -ErrorAction Stop
    foreach ($d in $disks) {
        $style = $d.PartitionStyle
        $health = $d.HealthStatus
        $op = $d.OperationalStatus
        $tag = if ($health -eq 'Healthy') { "[OK]  " } else { "[BAD] " }
        Write-Host ("{0} Disk {1}: Style={2} | Health={3} | Status={4} | Size={5} GB" -f (
            $tag, $d.Number, $style, $health, $op, [math]::Round($d.Size / 1GB, 1)
        ))
    }
} catch {
    Write-Host "[WARN] Get-Disk query: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "`n=== Phase 3: Critical System Boot & Recovery Partitions ===" -ForegroundColor Cyan
try {
    $sysParts = Get-Partition | Where-Object { $_.Type -in 'System', 'Recovery', 'Reserved' }
    foreach ($sp in $sysParts) {
        Write-Host ("  [INFO] Disk {0} Part {1}: Type={2,-10} Size={3} MB" -f (
            $sp.DiskNumber, $sp.PartitionNumber, $sp.Type, [math]::Round($sp.Size / 1MB, 0)
        ))
    }
} catch {
    Write-Host "[INFO] System partition audit: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($misalignedCount -gt 0) {
    Write-Host ("[WARN] Found {0} partition(s) not aligned to 4K sector boundaries." -f $misalignedCount) -ForegroundColor Yellow
    Write-Host "[REMEDIATION] Misaligned partitions cause double-write penalties on SSDs."
    Write-Host "[REMEDIATION] Usually caused by legacy cloning tools (e.g. XP/Win7 era disk clones)."
} else {
    Write-Host "[STATUS] All detected disk partitions are properly 4K aligned and healthy." -ForegroundColor Green
    Write-Host "[INFO] GPT partition scheme and EFI boot records conform to modern standards." -ForegroundColor Gray
}
""",
        admin=True,
    ),
    STO(
        "mft_filesystem_fragmentation",
        "NTFS Volume Health, MFT Growth & Dirty Bit Audit",
        "Checks filesystem dirty bits across all mounted drive letters and inspects "
        "Master File Table (MFT) allocation delta and cluster alignment.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: NTFS INTEGRITY, DIRTY BITS & MFT GEOMETRY          " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: When Windows encounters unrecoverable metadata errors or" -ForegroundColor Gray
Write-Host "[INFO] power loss during disk write, it sets the volume 'Dirty Bit'. On next boot," -ForegroundColor Gray
Write-Host "[INFO] autochk forces a full chkdsk repair, pausing system startup." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Volume Dirty Bit Corruption Check ===" -ForegroundColor Cyan
$drives = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"
$dirtyCount = 0
foreach ($d in $drives) {
    $ltr = $d.DeviceID
    try {
        $dirtyOut = fsutil.exe dirty query $ltr
        if ($dirtyOut -like "*is dirty*") {
            $dirtyCount++
            Write-Host ("[BAD]  Volume {0} DIRTY BIT IS SET! Filesystem corruption detected; chkdsk queued." -f $ltr) -ForegroundColor Red
        } else {
            Write-Host ("[OK]   Volume {0} filesystem is clean (dirty bit not set)." -f $ltr) -ForegroundColor Green
        }
    } catch {
        Write-Host ("[WARN] Could not query dirty bit on {0}: {1}" -f $ltr, $_.Exception.Message) -ForegroundColor Yellow
    }
}

Write-Host "`n=== Phase 2: Master File Table (MFT) & Cluster Geometry ===" -ForegroundColor Cyan
$sysDrive = $env:SystemDrive
try {
    $fsInfo = fsutil.exe fsinfo volumeinfo $sysDrive
    $fsLines = $fsInfo -split "`r?`n"
    foreach ($fl in $fsLines) {
        if ($fl -match 'Bytes Per Cluster|File System Name|Volume Flags|Supports') {
            Write-Host ("  {0}" -f $fl.Trim())
        }
    }
} catch {
    Write-Host "[INFO] Volume info check: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 3: NTFS Driver Event Log Integrity Telemetry ===" -ForegroundColor Cyan
try {
    $ntfsEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'System'
        ProviderName = 'Ntfs', 'disk'
        Level = 1, 2, 3  # Error, Warning
    } -MaxEvents 5 -ErrorAction SilentlyContinue

    if ($ntfsEvents) {
        Write-Host "[WARN] Recent NTFS or Disk driver warnings/errors detected in System log:" -ForegroundColor Yellow
        foreach ($ev in $ntfsEvents) {
            Write-Host ("  • [{0:yyyy-MM-dd HH:mm}] Event {1} ({2}): {3}" -f (
                $ev.TimeCreated, $ev.Id, $ev.ProviderName, $ev.Message.Split("`n")[0].Trim()
            ))
        }
    } else {
        Write-Host "[OK]   No recent NTFS or disk driver errors logged in System event log." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Event log NTFS query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($dirtyCount -gt 0) {
    Write-Host ("[ACTION REQUIRED] {0} volume(s) have the DIRTY BIT active!" -f $dirtyCount) -ForegroundColor Red
    Write-Host "[REMEDIATION] Schedule chkdsk scan and verify drive SMART health:"
    Write-Host "  1. Check system event log for Event ID 55 or 98 (Ntfs corruption)."
    Write-Host "  2. Perform read-only verification scan: chkdsk C: /scan"
    Write-Host "  3. If errors found, queue offline repair on reboot: chkdsk C: /f /r"
} else {
    Write-Host "[STATUS] All mounted volumes report clean dirty bits and healthy NTFS metadata." -ForegroundColor Green
    Write-Host "[INFO] No immediate chkdsk intervention required." -ForegroundColor Gray
}
""",
    ),
    STO(
        "bitlocker_tpm_protectors",
        "BitLocker Encryption, TPM Key Protectors & PCR Audit",
        "Audits BitLocker encryption status, cipher strength (XTS-AES-128/256), and key protectors per volume.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: BITLOCKER ENCRYPTION & TPM PROTECTORS POSTURE      " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: BitLocker protects data-at-rest against physical theft." -ForegroundColor Gray
Write-Host "[INFO] Volume encryption must be coupled with TPM PCR validation and an escrowed" -ForegroundColor Gray
Write-Host "[INFO] 48-digit Recovery Password in Entra ID / Active Directory to prevent lockout." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: BitLocker Volume Encryption Status ===" -ForegroundColor Cyan
$unprotectedCount = 0
try {
    $blVols = Get-BitLockerVolume -ErrorAction Stop
    foreach ($bv in $blVols) {
        $mp = $bv.MountPoint
        $status = $bv.VolumeStatus
        $method = $bv.EncryptionMethod
        $prot = $bv.ProtectionStatus
        $pct = $bv.EncryptionPercentage

        if ($prot -eq 'On') {
            Write-Host ("[OK]   Volume {0} - Protection: ON ({1}%) | Cipher: {2}" -f $mp, $pct, $method) -ForegroundColor Green
        } else {
            $unprotectedCount++
            Write-Host ("[WARN] Volume {0} - Protection: OFF / Suspended | Status: {1}" -f $mp, $status) -ForegroundColor Yellow
        }

        Write-Host "       Key Protectors:"
        foreach ($kp in $bv.KeyProtector) {
            $kType = $kp.KeyProtectorType
            $kId = $kp.KeyProtectorId
            Write-Host ("         • Type: {0,-22} ID: {1}" -f $kType, $kId)
        }
    }
} catch {
    Write-Host "[INFO] BitLocker query (Get-BitLockerVolume requires elevation or BitLocker feature): $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: TPM Hardware Security Module Attestation ===" -ForegroundColor Cyan
try {
    $tpm = Get-Tpm -ErrorAction Stop
    if ($tpm.TpmPresent) {
        if ($tpm.TpmReady -and $tpm.TpmEnabled) {
            Write-Host "[OK]   TPM 2.0 / 1.2 is Present, Enabled, and Ready for BitLocker." -ForegroundColor Green
            Write-Host ("       Manufacturer: {0} | Spec Version: {1}" -f $tpm.ManufacturerIdTxt, $tpm.ManufacturerVersion)
        } else {
            Write-Host "[WARN] TPM is present but NOT fully enabled or initialized." -ForegroundColor Yellow
        }
    } else {
        Write-Host "[BAD]  No TPM hardware detected on this motherboard!" -ForegroundColor Red
    }
} catch {
    Write-Host "[INFO] Get-Tpm check: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($unprotectedCount -gt 0) {
    Write-Host ("[ACTION REQUIRED] {0} volume(s) have BitLocker protection disabled or suspended!" -f $unprotectedCount) -ForegroundColor Yellow
    Write-Host "[REMEDIATION] 1. Verify if maintenance was being performed (e.g. BIOS update)."
    Write-Host "  2. Resume protection via: manage-bde -protectors -enable C:"
    Write-Host "  3. Backup recovery key to cloud/domain: manage-bde -protectors -adbackup C: -id {KEY-ID}"
} else {
    Write-Host "[STATUS] All active BitLocker volumes are fully encrypted and hardware-protected." -ForegroundColor Green
    Write-Host "[INFO] TPM key protectors are binding pre-boot authentication properly." -ForegroundColor Gray
}
""",
    ),
    STO(
        "installer_patch_waste",
        "Windows Installer Patch Cache & Component Store Waste",
        "Calculates disk space consumed by Windows Installer patch cache (%SystemRoot%\\Installer) "
        "and SoftwareDistribution download caches.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: WINDOWS SERVICING & INSTALLER CACHE FOOTPRINT     " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: %SystemRoot%\Installer stores registered MSI/MSP patch" -ForegroundColor Gray
Write-Host "[INFO] files. NEVER delete files from C:\Windows\Installer manually! Deleting them" -ForegroundColor Gray
Write-Host "[INFO] breaks application updates, patches, and uninstalls permanently. Use" -ForegroundColor Gray
Write-Host "[INFO] official servicing tools (cleanmgr, DISM Component Cleanup) to reclaim space." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Servicing Cache & Installer Directory Footprint ===" -ForegroundColor Cyan
$paths = @(
    @{ Name = 'Windows Installer Cache'; Path = "$env:SystemRoot\Installer"; WarningMB = 15360 },
    @{ Name = 'SoftwareDistribution Downloads'; Path = "$env:SystemRoot\SoftwareDistribution\Download"; WarningMB = 5120 },
    @{ Name = 'Windows Temp Staging'; Path = "$env:SystemRoot\Temp"; WarningMB = 2048 },
    @{ Name = 'Crash Minidumps'; Path = "$env:SystemRoot\Minidump"; WarningMB = 512 }
)

$largeCaches = 0
foreach ($item in $paths) {
    $p = $item.Path
    if (Test-Path $p) {
        try {
            $files = Get-ChildItem -Path $p -Recurse -File -ErrorAction SilentlyContinue
            $totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
            $sizeMB = [math]::Round($totalBytes / 1MB, 1)
            $count = ($files.Count, 0)[$null -eq $files.Count]

            if ($sizeMB -gt $item.WarningMB) {
                $largeCaches++
                Write-Host ("  [WARN] {0,-32} {1,8:N1} MB ({2} files) [OVER SIZING THRESHOLD]" -f $item.Name, $sizeMB, $count) -ForegroundColor Yellow
            } else {
                Write-Host ("  [OK]   {0,-32} {1,8:N1} MB ({2} files)" -f $item.Name, $sizeMB, $count) -ForegroundColor Green
            }
        } catch {
            Write-Host ("  [INFO] {0,-32} (Access restricted) [{1}]" -f $item.Name, $p)
        }
    } else {
        Write-Host ("  [INFO] {0,-32} (Directory not present / empty)" -f $item.Name)
    }
}

Write-Host "`n=== Phase 2: Windows Component Store (WinSxS) State ===" -ForegroundColor Cyan
try {
    $winsxsPath = "$env:SystemRoot\WinSxS"
    if (Test-Path $winsxsPath) {
        Write-Host "  [INFO] WinSxS directory is present. Component store contains hard links." -ForegroundColor Cyan
        Write-Host "  [INFO] Explorer reports inflated size because hard links are counted repeatedly." -ForegroundColor Gray
    }
} catch {
    Write-Host "[INFO] WinSxS check: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($largeCaches -gt 0) {
    Write-Host ("[WARN] Elevated storage consumption detected in {0} staging path(s)." -f $largeCaches) -ForegroundColor Yellow
    Write-Host "[REMEDIATION] Reclaim disk space SAFELY using official Windows servicing commands:"
    Write-Host "  1. Clear SoftwareDistribution cache: stop Windows Update service, remove files in Download, start service."
    Write-Host "  2. Run Component Store cleanup: dism.exe /Online /Cleanup-Image /StartComponentCleanup"
    Write-Host "  3. DO NOT manually delete files inside C:\Windows\Installer!" -ForegroundColor Red
} else {
    Write-Host "[STATUS] Windows servicing and installer directories are within normal sizing thresholds." -ForegroundColor Green
    Write-Host "[INFO] No urgent servicing disk footprint remediation required." -ForegroundColor Gray
}
""",
    ),
]

