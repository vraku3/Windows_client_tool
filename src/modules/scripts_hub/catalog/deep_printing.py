"""Print Subsystem & Spooler Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

PRI = maker("Printing")

_SPOOL_GUARD = r"""$spool = Get-Service Spooler -ErrorAction SilentlyContinue
if (-not $spool -or $spool.Status -ne 'Running') {
    Write-Host "[WARN] Print Spooler service (Get-Service Spooler) is NOT RUNNING. Print subsystem is offline."
    return
}
"""

SCRIPTS = [
    PRI(
        "printers",
        "Printers & Spooler Subsystem Status",
        "Audits registered printers, driver names, port names, shared status, and spooler health.",
        _SPOOL_GUARD + r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Installed Printers & Spooler Subsystem Health"
Write-Host "PURPOSE   : Audits local & network printer queues, default printer, and port bindings"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRINTER PRIMER:"
Write-Host "  • Spooler Service: Windows service managing all print jobs and driver communication."
Write-Host "  • Default Printer: Marked [DEFAULT]; Windows can automatically manage this if enabled."
Write-Host "  • Offline Status: If printer says 'Offline', verify network cable, IP, or power state."
Write-Host ""

Write-Host "=== Phase 1: Installed Printers & Device Status ==="
try {
    $printers = Get-Printer -ErrorAction Stop
    Write-Host ("[INFO] Installed Printers Count: {0}" -f $printers.Count)
    foreach ($p in $printers) {
        $status = $p.PrinterStatus
        $isDef = if ($p.Default) { "[DEFAULT]" } else { "         " }
        $shared = if ($p.Shared) { "Shared: $($p.ShareName)" } else { "Local" }
        $tag = if ($status -match 'Offline|Error') { '[WARN]' } else { '[OK]  ' }
        Write-Host ("  {0} {1} {2,-28} Status: {3,-10} Port: {4,-16} ({5})" -f $tag, $isDef, $p.Name, $status, $p.PortName, $shared)
    }
} catch {
    Write-Host "[INFO] Printer query note: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "=== Phase 2: Spooler Directory Backlog Check ==="
$spoolDir = "$env:SystemRoot\System32\spool\PRINTERS"
try {
    if (Test-Path $spoolDir) {
        $spoolFiles = Get-ChildItem -Path $spoolDir -Filter "*.*" -ErrorAction SilentlyContinue
        if ($spoolFiles) {
            Write-Host ("[WARN] Found {0} stranded spool file(s) in {1}:" -f $spoolFiles.Count, $spoolDir)
            $spoolFiles | Select-Object -First 5 | ForEach-Object {
                Write-Host ("         • {0} ({1} bytes, modified {2:yyyy-MM-dd HH:mm})" -f $_.Name, $_.Length, $_.LastWriteTime)
            }
        } else {
            Write-Host "[OK]   Spooler directory is clean (zero orphaned temporary spool files)."
        }
    }
} catch {
    Write-Host "[INFO] Spooler folder query note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] If diagnosing printing failures:"
Write-Host "       1. If printer status is 'Offline': verify IP ping or USB connection."
Write-Host "       2. If jobs are not printing despite printer online: run 'Print Queue & Stuck Jobs Diagnostic'."
Write-Host "       3. To restart print subsystem: restart the Print Spooler service via services.msc."
""",
    ),
    PRI(
        "print_jobs",
        "Print Queue & Stuck Jobs Diagnostic",
        "Audits all active and stalled print spooler jobs across all installed printers.",
        _SPOOL_GUARD + r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Active Print Queue & Stuck Jobs Post-Mortem"
Write-Host "PURPOSE   : Pinpoints jammed documents blocking subsequent print operations"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRINT QUEUE PRIMER:"
Write-Host "  • When a document fails to print (e.g. out of paper, paper jam, or invalid font data),"
Write-Host "    it remains in the queue with status 'Error' or 'Deleting'."
Write-Host "  • ALL subsequent print jobs wait behind the stuck document until it is purged!"
Write-Host ""

Write-Host "=== Phase 1: Active Print Queue Job Roster ==="
$hasStuck = $false
try {
    $jobs = Get-PrintJob -PrinterName * -ErrorAction SilentlyContinue
    if ($jobs) {
        $hasStuck = $true
        Write-Host ("[WARN] Found {0} active or stuck print job(s) in queue:" -f $jobs.Count)
        foreach ($j in $jobs) {
            $status = if ($j.JobStatus) { $j.JobStatus } else { 'Queued/Spooling' }
            $tag = if ($status -match 'Error|Deleting|Paused') { '[BAD] ' } else { '[WARN]' }
            Write-Host ("  {0} Printer: {1,-20} ID: {2,-5} Doc: {3,-26} Status: {4} (Pages: {5})" -f $tag, $j.PrinterName, $j.Id, $j.DocumentName, $status, $j.TotalPages)
        }
    } else {
        Write-Host "[OK]   Zero queued or stuck print jobs across all print queues."
    }
} catch {
    Write-Host "[INFO] Print job query note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($hasStuck) {
    Write-Host "[BAD]  PRINT JOBS ARE BLOCKING QUEUES!"
    Write-Host "       WHAT HELPDESK SHOULD DO:"
    Write-Host "       1. Try cancelling stuck job via Printers & Scanners GUI."
    Write-Host "       2. If job is stuck on 'Deleting': Stop Spooler service, delete files in C:\Windows\System32\spool\PRINTERS, then start Spooler."
} else {
    Write-Host "[OK]   STATUS: ALL PRINT QUEUES ARE CLEAR AND READY."
}
""",
    ),
    PRI(
        "print_drivers",
        "Installed Print Drivers & Architecture Audit",
        "Inventories all installed printer drivers, versions, environment architectures (Type 3 vs Type 4), and driver file paths.",
        _SPOOL_GUARD + r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Installed Print Drivers & Architecture Audit"
Write-Host "PURPOSE   : Differentiates modern isolated Type 4 drivers from legacy Type 3 drivers"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRINT DRIVER PRIMER:"
Write-Host "  • Type 3 (v3) Drivers: Legacy user-mode drivers; bugs in Type 3 can crash the entire Spooler!"
Write-Host "  • Type 4 (v4) Drivers: Modern isolated architecture; cannot crash the Spooler service."
Write-Host "  • Point & Print Restrictions: MS update requires Administrator rights to install Type 3 drivers."
Write-Host ""

Write-Host "=== Phase 1: Installed Printer Drivers Inventory ==="
try {
    $drivers = Get-PrinterDriver -ErrorAction Stop
    Write-Host ("[INFO] Installed Print Drivers Count: {0}" -f $drivers.Count)
    foreach ($d in $drivers) {
        $majorVer = $d.MajorVersion
        $typeStr = switch ($majorVer) {
            4 { "[OK]   Type 4 (Modern/Isolated)" }
            3 { "[INFO] Type 3 (Standard/Legacy)" }
            default { "[INFO] Type $majorVer" }
        }
        Write-Host ("  Driver: {0,-36} {1} | Environment: {2}" -f $d.Name, $typeStr, $d.PrinterEnvironment)
    }
} catch {
    Write-Host "[INFO] Driver query note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] Print Driver Maintenance:"
Write-Host "       1. When deploying corporate printers, prefer Type 4 (v4) or Vendor Class drivers."
Write-Host "       2. Outdated Type 3 drivers are the leading cause of spoolsv.exe crashes."
""",
    ),
    PRI(
        "printer_ports",
        "Printer Ports & Network Spooler Endpoints",
        "Audits printer port bindings, TCP/IP raw port/LPR configurations, and WSD endpoints.",
        _SPOOL_GUARD + r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Printer Ports & Network Endpoint Bindings"
Write-Host "PURPOSE   : Identifies unreliable WSD ports vs reliable Standard TCP/IP (Port 9100) connections"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRINTER PORT PRIMER:"
Write-Host "  • Standard TCP/IP Port (Port 9100 RAW): Robust, reliable enterprise network printing."
Write-Host "  • WSD Ports (Web Services for Devices): Uses broadcast discovery; notorious for dropping"
Write-Host "    printers to 'Offline' status whenever the printer enters sleep or deep standby mode!"
Write-Host ""

Write-Host "=== Phase 1: Printer Ports & Network Connections ==="
$wsdCount = 0
try {
    $ports = Get-PrinterPort -ErrorAction Stop
    Write-Host ("[INFO] Active Printer Ports Count: {0}" -f $ports.Count)
    foreach ($pt in $ports) {
        $desc = if ($pt.Description) { $pt.Description } else { "Standard Port" }
        $isWsd = $pt.Name -like 'WSD*'
        if ($isWsd) { $wsdCount++ }
        $tag = if ($isWsd) { '[WARN]' } else { '[OK]  ' }
        Write-Host ("  {0} Port: {1,-26} Type: {2}" -f $tag, $pt.Name, $desc)
    }
} catch {
    Write-Host "[INFO] Port query note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($wsdCount -gt 0) {
    Write-Host ("[WARN] Found {0} printer port(s) using WSD discovery. If printer goes offline intermittently:" -f $wsdCount)
    Write-Host "       1. Find the printer's static IP address on the local network."
    Write-Host "       2. Change the printer port in Printer Properties -> Ports -> Add Port -> Standard TCP/IP Port."
} else {
    Write-Host "[OK]   STATUS: All ports configured with standard or dedicated endpoints."
}
""",
    ),
]
