"""Script Library -- the read-only checks an admin types by hand on a machine
they do not know, as named, verified scripts with live output. Qt-free.

Every built-in script here is READ-ONLY: it reads state and prints it, and
changes nothing. Fixes live in Quick Fix, where each one has a confirmation
and a verification. Every script was run on this machine (2026-10-16), both
elevated and with a standard-user token, before it was added; `admin=True`
marks the ones whose answer is incomplete without elevation, and the script
itself SAYS so in its output rather than printing a shorter list as if it
were the whole one.

User scripts: `.ps1`, `.cmd` and `.bat` files dropped into
`%APPDATA%\\WindowsTweaker\\scripts` appear under "My scripts". Their first
comment block is their description. They are the user's own code and can do
anything, so the pane shows the whole file before running it.
"""
from __future__ import annotations
from core.system_paths import cmd_exe, powershell_exe

import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
POWERSHELL, CMD = "powershell", "cmd"

#: PowerShell's own formatter truncates at the console width; a pipe has none,
#: so every table is formatted wide on purpose.
_WIDE = "| Format-Table -AutoSize -Wrap | Out-String -Width 220"
_ADMIN_NOTE = ("if (-not ([Security.Principal.WindowsPrincipal][Security.Principal."
               "WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal."
               "WindowsBuiltInRole]::Administrator)) { 'NOTE: not running as "
               "administrator -- {0}' -f $args[0] }")


_IS_ADMIN = ("$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal."
             "WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal."
             "WindowsBuiltInRole]::Administrator)\n")


@dataclass(frozen=True)
class Script:
    id: str
    title: str
    category: str
    description: str
    body: str
    kind: str = POWERSHELL
    admin: bool = False              # answer is incomplete without elevation
    read_only: bool = True
    timeout: float = 120.0
    path: str = ""                   # set for user scripts
    server: bool = False             # Windows Server branch of the tree
    #: What the script needs that THIS PC may not have ("Active Directory
    #: module", "Hyper-V role"). Non-empty means it could only be syntax-checked
    #: here; the script prints NOT AVAILABLE instead of failing when it is absent.
    requires: str = ""


def _ps(body: str, admin_note: str = "") -> str:
    """A script body with strict errors off and, when the answer is partial
    unelevated, a first line that says what is missing."""
    head = "$ErrorActionPreference = 'Continue'\n$ProgressPreference = 'SilentlyContinue'\n"
    if admin_note:
        quoted = admin_note.replace("'", "''")      # PowerShell single-quote escaping
        head += f"& {{ {_ADMIN_NOTE} }} '{quoted}'\n"
    # Errors as one readable line each, on stdout -- otherwise a redirected
    # PowerShell serialises them to stderr as CLIXML (measured: an unelevated
    # `Get-Process -IncludeUserName` came back as a raw <Objs ...> blob).
    return (head + "& {\n" + body.strip() + "\n} 2>&1 | ForEach-Object { "
            "if ($_ -is [System.Management.Automation.ErrorRecord]) { "
            "'ERROR: ' + $_.Exception.Message } else { $_ } }\n")


# ---- helpers the catalogs use ---------------------------------------------------------------

WIDE = _WIDE
LIST = "| Format-List | Out-String -Width 220"
ps = _ps


def need_cmd(cmdlet: str, what: str) -> str:
    """A body prefix that stops with NOT AVAILABLE when `cmdlet` is missing --
    a role, a feature or an RSAT module this PC does not have."""
    what = what.replace("'", "''")
    return (f"if (-not (Get-Command {cmdlet} -ErrorAction SilentlyContinue)) {{ "
            f"'NOT AVAILABLE: {what} is not installed on this PC.'; return }}\n")


def need_admin(why: str) -> str:
    """A body prefix that refuses unelevated, for reads Windows answers with
    an empty list instead of an error (Get-DnsClientCache did, measured)."""
    why = why.replace("'", "''")
    return (_IS_ADMIN + f"if (-not $isAdmin) {{ 'NOT AVAILABLE: needs administrator "
            f"rights -- {why}'; return }}\n")


BUILT_IN: List[Script] = [
    # ---- Troubleshooting ------------------------------------------------------------------
    Script("restarts", "Who restarted this PC, and why", "Troubleshooting",
           "Every shutdown and restart in the last 30 days from the System log: planned ones "
           "(1074 -- which process asked, for which user, and the reason it gave), "
           "unexpected ones (6008 'the previous shutdown was unexpected', 41 Kernel-Power "
           "'rebooted without cleanly shutting down') and every boot (6005). The first "
           "question after 'it rebooted overnight'.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: System Reboot & Shutdown History (Last 30 Days)"
Write-Host "PURPOSE   : Correlates who initiated restarts, patch reboots vs dirty shutdowns"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK EVENT REFERENCE GUIDE:"
Write-Host "  • Event 1074: Clean planned shutdown or reboot by user, Windows Update, or an app."
Write-Host "  • Event 6006: Clean shutdown -- Event Log service stopped gracefully."
Write-Host "  • Event 6005: System Boot -- Event Log service started."
Write-Host "  • Event 6008: [BAD]  DIRTY SHUTDOWN -- previous shutdown was unexpected (hang/power cut)."
Write-Host "  • Event 41  : [BAD]  KERNEL-POWER -- system rebooted without cleanly shutting down first."
Write-Host ""
$since = (Get-Date).AddDays(-30)
try {
  $events = Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074,6008,41,6005,6006; StartTime=$since} -ErrorAction Stop
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {
    Write-Host "[OK]   No shutdown or restart events in the last 30 days."
    return
  }
  Write-Host ("ERROR: System event log could not be read: {0}" -f $_.Exception.Message)
  return
}

$unexpectedCount = 0
$kernelPowerCount = 0
$plannedCount = 0

Write-Host "=== Phase 1: Chronological Event Timeline (Newest First) ==="
$events | ForEach-Object {
  $id = $_.Id
  $time = $_.TimeCreated
  if ($id -eq 1074) {
    $plannedCount++
    $p = $_.Properties
    $proc = if ($p[0].Value) { $p[0].Value } else { 'Unknown' }
    $user = if ($p[6].Value) { $p[6].Value } else { 'System/Unknown' }
    $reason = if ($p[2].Value) { $p[2].Value } else { 'None' }
    $action = if ($p[4].Value) { $p[4].Value } else { 'restart' }
    Write-Host ("[OK]   [{0:yyyy-MM-dd HH:mm:ss}] Event 1074 (Planned {1}) by '{2}' via {3}" -f $time, $action, $user, $proc)
    Write-Host ("       Reason Given: {0}" -f $reason)
  } elseif ($id -eq 6008) {
    $unexpectedCount++
    Write-Host ("[BAD]  [{0:yyyy-MM-dd HH:mm:ss}] Event 6008 (DIRTY SHUTDOWN): Previous shutdown was unexpected!" -f $time)
    Write-Host "       WHAT THIS MEANS: The OS lost power abruptly or crashed before writing dirty pages to disk."
  } elseif ($id -eq 41) {
    $kernelPowerCount++
    $xml = [xml]$_.ToXml()
    $bc = $xml.Event.EventData.Data | Where-Object { $_.Name -eq 'BugcheckCode' } | Select-Object -ExpandProperty '#text'
    $bcHex = if ($bc -and $bc -ne '0') { '0x{0:X}' -f [int64]$bc } else { '0x0 (None/Hard Loss)' }
    Write-Host ("[BAD]  [{0:yyyy-MM-dd HH:mm:ss}] Event 41 (Kernel-Power): Unclean reboot detected! BugCheck Code: {1}" -f $time, $bcHex)
    if ($bcHex -eq '0x0 (None/Hard Loss)') {
      Write-Host "       WHAT THIS MEANS: Power cut, PSU failure, or power button held down (no BSOD dump written)."
    } else {
      Write-Host "       WHAT THIS MEANS: Blue-Screen (BSOD) kernel crash occurred! Inspect minidump file."
    }
  } elseif ($id -eq 6006) {
    Write-Host ("[INFO] [{0:yyyy-MM-dd HH:mm:ss}] Event 6006: Clean shutdown completed successfully." -f $time)
  } elseif ($id -eq 6005) {
    Write-Host ("[INFO] [{0:yyyy-MM-dd HH:mm:ss}] Event 6005: System booted (Event Log service initialized)." -f $time)
  }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & VERDICT ==="
Write-Host "================================================================================"
Write-Host ("Planned Reboots/Shutdowns (1074) : {0}" -f $plannedCount)
Write-Host ("Unexpected Shutdowns (6008)      : {0}" -f $unexpectedCount)
Write-Host ("Kernel-Power Dirty Resets (41)   : {0}" -f $kernelPowerCount)
Write-Host ""
if ($unexpectedCount -eq 0 -and $kernelPowerCount -eq 0) {
  Write-Host "[OK]   STATUS: HEALTHY -- 100% of shutdowns and reboots were clean and planned."
  Write-Host "       Helpdesk Note: If user complained of unexpected restart, check who initiated Event 1074 above (e.g. Windows Update or Explorer)."
} else {
  Write-Host "[BAD]  STATUS: UNSTABLE -- System has experienced unexpected power losses or crashes!"
  Write-Host "       WHAT HELPDESK SHOULD DO NEXT:"
  if ($kernelPowerCount -gt 0) {
    Write-Host "       1. Run 'Kernel BugCheck Minidump Triage' script to identify the faulting driver."
    Write-Host "       2. If BugCheck is 0x0, check physical power cord, UPS, battery health, and PSU voltage."
    Write-Host "       3. Check if user is holding the power button to force shutdown during application freezes."
  }
}
""")),
    Script("uptime", "Uptime, last boot and Fast Startup", "Troubleshooting",
           "When Windows last really booted and how long it has been up. With Fast Startup "
           "on, 'Shut down' is a hibernation of the kernel, so uptime keeps growing and "
           "driver/update problems that a restart would clear survive a shutdown -- this "
           "says whether it is on.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: System Uptime, Boot Hygiene & Fast Startup Status"
Write-Host "PURPOSE   : Verifies true kernel reset vs hibernation across shutdowns"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIMER:"
Write-Host "  • UPTIME measures how long the kernel and drivers have been continuously running."
Write-Host "  • FAST STARTUP (Hiberboot): When ON, clicking 'Shut down' DOES NOT reset the kernel!"
Write-Host "    Instead, Windows hibernates kernel session to hiberfil.sys. Stale driver states,"
Write-Host "    leaked resources, and pending updates persist. Only 'Restart' resets the kernel!"
Write-Host ""
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
if (-not $os) {
  Write-Host "ERROR: Unable to query Win32_OperatingSystem via CIM."
  return
}
$up = (Get-Date) - $os.LastBootUpTime
$fsKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
$fs = (Get-ItemProperty $fsKey -ErrorAction SilentlyContinue).HiberbootEnabled
$hiberKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Power'
$hiber = (Get-ItemProperty $hiberKey -ErrorAction SilentlyContinue).HibernateEnabled

Write-Host "=== Phase 1: Operating System & Boot Telemetry ==="
Write-Host ("Last Boot Time   : {0:yyyy-MM-dd HH:mm:ss}" -f $os.LastBootUpTime)
Write-Host ("Continuous Uptime: {0} days, {1} hours, {2} minutes" -f $up.Days, $up.Hours, $up.Minutes)
Write-Host ("OS Install Date  : {0:yyyy-MM-dd}" -f $os.InstallDate)
Write-Host ""

Write-Host "=== Phase 2: Fast Startup & Hibernation Configuration ==="
if ($fs -eq 1) {
  Write-Host "[WARN] Fast Startup (Hiberboot) is ENABLED."
  Write-Host "       CRITICAL HELPDESK NOTE: 'Shut down' hibernates the kernel. Tell users to click RESTART to clear problems!"
} elseif ($fs -eq 0) {
  Write-Host "[OK]   Fast Startup is DISABLED. 'Shut down' performs a genuine cold kernel reboot."
} else {
  Write-Host "[INFO] Fast Startup policy key not set (Windows default is active if hibernation supported)."
}

if ($hiber -eq 0) {
  Write-Host "[INFO] System Hibernation is disabled (Fast Startup cannot engage without hibernation)."
} else {
  Write-Host "[INFO] System Hibernation is enabled on system drive."
}

Write-Host ""
Write-Host "=== Phase 3: Recent Wake Source (Last Sleep Transition) ==="
try {
  $wakeEvents = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Power-Troubleshooter'; Id=1} -MaxEvents 1 -ErrorAction SilentlyContinue
  if ($wakeEvents) {
    $wakeMsg = ($wakeEvents[0].Message -split "`r?`n") | Where-Object { $_ -match 'Wake Source' }
    if ($wakeMsg) {
      Write-Host ("Last Wake Event  : {0:yyyy-MM-dd HH:mm:ss} -- {1}" -f $wakeEvents[0].TimeCreated, $wakeMsg.Trim())
    } else {
      Write-Host ("Last Wake Event  : {0:yyyy-MM-dd HH:mm:ss}" -f $wakeEvents[0].TimeCreated)
    }
  } else {
    Write-Host "[INFO] Zero sleep/wake events recorded in System log."
  }
} catch {
  Write-Host "[INFO] Wake source query note: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & VERDICT ==="
Write-Host "================================================================================"
if ($up.Days -ge 30) {
  Write-Host ("[BAD]  UPTIME ALERT: {0} days without a true reboot!" -f $up.Days)
  Write-Host "       WHAT THIS MEANS: High risk of pool exhaustion, unpatched security updates, and driver instability."
  Write-Host "       ACTION REQUIRED: Instruct user to perform a full system restart."
} elseif ($up.Days -ge 14) {
  Write-Host ("[WARN] ELEVATED UPTIME: {0} days since last restart." -f $up.Days)
  Write-Host "       WHAT THIS MEANS: Operating within acceptable range, but recommend scheduled reboot for patch hygiene."
} else {
  Write-Host ("[OK]   UPTIME HEALTHY: {0}d {1}h -- kernel was recently reset." -f $up.Days, $up.Hours)
}
if ($fs -eq 1) {
  Write-Host "[WARN] REMINDER: If diagnosing intermittent network, audio, or peripheral bugs, always trigger RESTART."
}
""")),
    Script("auto_stopped", "Automatic services that are not running", "Troubleshooting",
           "Services set to start automatically that are stopped right now, with their last "
           "exit code. Trigger-start and delayed services stop on their own by design, so "
           "they are marked; a plain Automatic service with a non-zero exit code is the one "
           "that crashed.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Automatic Services That Are Stopped"
Write-Host "PURPOSE   : Distinguishes benign on-demand triggers from crashed background services"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK SERVICE TRIAGE PRIMER:"
Write-Host "  • [INFO] Trigger-Start: Starts ONLY when a hardware/network trigger fires (e.g. Bluetooth,"
Write-Host "    Smart Card, VPN). Stops automatically when idle -- COMPLETELY NORMAL by design!"
Write-Host "  • [INFO] Delayed-Start: Configured to start 2-3 minutes after boot to avoid CPU spikes."
Write-Host "  • [BAD]  Non-Zero Exit Code: Service crashed or terminated abnormally (e.g. error 1067)."
Write-Host "  • [WARN] Clean Stopped: Automatic service stopped with ExitCode 0 (requires review)."
Write-Host ""

$trigger = @{}
try {
  Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services' -ErrorAction SilentlyContinue | ForEach-Object {
    if (Test-Path (Join-Path $_.PSPath 'TriggerInfo')) { $trigger[$_.PSChildName.ToLower()] = $true }
  }
} catch {
  Write-Host "[INFO] Registry trigger info query note: $($_.Exception.Message)"
}

try {
  $services = Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State<>'Running'" -ErrorAction Stop
} catch {
  Write-Host ("ERROR: Could not query Win32_Service: {0}" -f $_.Exception.Message)
  return
}

if (-not $services) {
  Write-Host "[OK]   STATUS: ALL automatic services are currently running. Zero stopped services."
  return
}

$crashedServices = @()
$triggerServices = @()
$delayedServices = @()
$otherStopped = @()

foreach ($svc in $services) {
  $nameLower = $svc.Name.ToLower()
  $isTrigger = [bool]$trigger[$nameLower]
  $isDelayed = [bool]$svc.DelayedAutoStart
  $exitCode = $svc.ExitCode

  if ($exitCode -ne 0) {
    $crashedServices += $svc
  } elseif ($isTrigger) {
    $triggerServices += $svc
  } elseif ($isDelayed) {
    $delayedServices += $svc
  } else {
    $otherStopped += $svc
  }
}

Write-Host "=== Phase 1: Crashed Services (Exit Code != 0) ==="
if ($crashedServices.Count -gt 0) {
  Write-Host ("[BAD]  Found {0} automatic service(s) that terminated with a failure exit code!" -f $crashedServices.Count)
  foreach ($s in $crashedServices) {
    Write-Host ("  [BAD]  Service: {0,-24} ExitCode: {1,-10} Name: {2}" -f $s.Name, $s.ExitCode, $s.DisplayName)
    Write-Host ("         WHAT THIS MEANS: Service crashed on startup or runtime. Check System Event 7034/7031.")
  }
} else {
  Write-Host "[OK]   Zero automatic services have non-zero crash exit codes."
}
Write-Host ""

Write-Host "=== Phase 2: Other Stopped Automatic Services (Exit Code = 0) ==="
if ($otherStopped.Count -gt 0) {
  Write-Host ("[WARN] Found {0} automatic service(s) stopped cleanly without active trigger info:" -f $otherStopped.Count)
  foreach ($s in $otherStopped) {
    Write-Host ("  [WARN] Service: {0,-24} Name: {1}" -f $s.Name, $s.DisplayName)
  }
} else {
  Write-Host "[OK]   Zero unexpected stopped automatic services."
}
Write-Host ""

Write-Host "=== Phase 3: Benign On-Demand & Delayed Services (Informational) ==="
Write-Host ("Trigger-Start services stopped by design: {0}" -f $triggerServices.Count)
Write-Host ("Delayed-Auto-Start services currently idle: {0}" -f $delayedServices.Count)
if ($triggerServices.Count -gt 0) {
  $triggerNames = ($triggerServices | Select-Object -First 8 | ForEach-Object { $_.Name }) -join ', '
  Write-Host ("  Examples: {0}..." -f $triggerNames)
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & VERDICT ==="
Write-Host "================================================================================"
if ($crashedServices.Count -gt 0) {
  Write-Host "[BAD]  ACTION REQUIRED: Crashed services detected!"
  Write-Host "       WHAT HELPDESK SHOULD DO NEXT:"
  Write-Host "       1. Note the service name and check Windows Event Viewer -> System log."
  Write-Host "       2. Filter for Event ID 7034 or 7031 (Service Control Manager)."
  Write-Host "       3. Attempt manual service start via Services console to observe the exact failure reason."
} elseif ($otherStopped.Count -gt 0) {
  Write-Host "[WARN] ATTENTION: Some automatic services are stopped. Verify if their features are used."
} else {
  Write-Host "[OK]   STATUS: HEALTHY. Stopped services are trigger-start services sleeping by design."
}
""")),
    Script("recent_software", "Software installed in the last 14 days", "Troubleshooting",
           "Programs whose install date is in the last two weeks (both registry views and "
           "per-user installs). 'It broke after I installed something' starts here.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Software Installed in the Last 14 Days"
Write-Host "PURPOSE   : Root-cause analysis for 'it was working until recently' tickets"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK TRIAGE PRIMER:"
Write-Host "  • Checks 64-bit system programs, 32-bit (WOW6432Node), and per-user installs."
Write-Host "  • Correlates MSI installer events from the Application log (Event 11707)."
Write-Host "  • If a machine developed glitches recently, match ticket creation time to install dates!"
Write-Host ""

$cut = (Get-Date).AddDays(-14).ToString('yyyyMMdd')
$cutDate = (Get-Date).AddDays(-14)
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'

Write-Host "=== Phase 1: Registry-Tracked Application Installs ==="
$rows = @()
try {
  $rows = Get-ItemProperty $keys -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -and $_.InstallDate -match '^\d{8}$' -and $_.InstallDate -ge $cut } |
    Sort-Object InstallDate -Descending |
    Select-Object @{n='Installed';e={$_.InstallDate}}, DisplayName, DisplayVersion, Publisher
} catch {
  Write-Host "[INFO] Registry scan query note: $($_.Exception.Message)"
}

if ($rows) {
  Write-Host ("[INFO] Found {0} software installation(s) in registry over the past 14 days:" -f $rows.Count)
  foreach ($r in $rows) {
    $formattedDate = $r.Installed.Insert(4, '-').Insert(7, '-')
    Write-Host ("  [{0}] {1,-36} v{2,-14} ({3})" -f $formattedDate, $r.DisplayName, $r.DisplayVersion, $r.Publisher)
  }
} else {
  Write-Host "[OK]   Zero software installations recorded with explicit InstallDate in the last 14 days."
}
Write-Host ""

Write-Host "=== Phase 2: Windows Installer (MsiInstaller) Logged Events ==="
try {
  $msiEvents = Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='MsiInstaller'; Id=11707; StartTime=$cutDate} -MaxEvents 15 -ErrorAction Stop
  if ($msiEvents) {
    Write-Host ("[INFO] Recorded {0} MSI package installation success event(s) in Application log:" -f $msiEvents.Count)
    foreach ($evt in $msiEvents) {
      $firstLine = ($evt.Message -split "`r?`n")[0]
      Write-Host ("  [{0:yyyy-MM-dd HH:mm}] {1}" -f $evt.TimeCreated, $firstLine)
    }
  } else {
    Write-Host "[OK]   Zero MSI installer events recorded in the last 14 days."
  }
} catch {
  if ($_.Exception.Message -match 'No events were found') {
    Write-Host "[OK]   Zero MSI installer events recorded in the last 14 days."
  } else {
    Write-Host "[INFO] Application log MsiInstaller check note: $($_.Exception.Message)"
  }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($rows.Count -gt 0) {
  Write-Host ("[WARN] RECENT CHANGES DETECTED: {0} programs were installed or updated." -f $rows.Count)
  Write-Host "       WHAT HELPDESK SHOULD DO:"
  Write-Host "       1. Ask the user if the problem began right after any of the dates listed above."
  Write-Host "       2. Check if newly installed software included security agents, VPNs, or virtual display drivers."
  Write-Host "       3. To test isolation, test uninstalling suspect app or rolling back using System Restore."
} else {
  Write-Host "[OK]   STATUS: NO RECENT SOFTWARE INSTALLS. Issue is likely caused by updates, hardware, or config."
}
""")),
    Script("hotfixes", "Installed Windows updates", "Troubleshooting",
           "Every installed update (KB) with its install date, newest first -- for 'which "
           "update came in before this started'. Cumulative updates replace each other, so "
           "only the latest few appear.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Installed Windows Updates & Servicing Hotfixes"
Write-Host "PURPOSE   : Verifies patch cadence and pinpoints recently applied KBs"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK UPDATES PRIMER:"
Write-Host "  • Cumulative Updates (LCU) replace previous months' updates; only recent ones show."
Write-Host "  • If a system broke after Patch Tuesday (second Tuesday of the month), check the KBs below."
Write-Host "  • To roll back an update causing driver/network outages: wusa.exe /uninstall /kb:XXXXXXX"
Write-Host ""

Write-Host "=== Phase 1: Installed Quality Updates (Get-HotFix) ==="
try {
  $fixes = Get-HotFix -ErrorAction Stop | Sort-Object InstalledOn -Descending
  if ($fixes) {
    Write-Host ("[INFO] Total HotFix packages detected: {0}" -f $fixes.Count)
    foreach ($fix in $fixes) {
      $dateStr = if ($fix.InstalledOn) { $fix.InstalledOn.ToString('yyyy-MM-dd') } else { 'Unknown' }
      Write-Host ("  [{0}] {1,-14} {2,-28} by {3}" -f $dateStr, $fix.HotFixID, $fix.Description, $fix.InstalledBy)
    }
  } else {
    Write-Host "[WARN] No hotfixes reported by Win32_QuickFixEngineering."
  }
} catch {
  Write-Host ("ERROR: Failed to retrieve hotfixes: {0}" -f $_.Exception.Message)
}
Write-Host ""

Write-Host "=== Phase 2: Recent Windows Update Client Activity ==="
try {
  $wuEvents = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WindowsUpdateClient'; Id=19,20; StartTime=(Get-Date).AddDays(-30)} -MaxEvents 10 -ErrorAction Stop
  if ($wuEvents) {
    Write-Host "[INFO] Recent Windows Update installation outcomes in System log:"
    foreach ($evt in $wuEvents) {
      $tag = if ($evt.Id -eq 19) { '[OK]  ' } else { '[BAD] ' }
      $desc = if ($evt.Id -eq 19) { 'SUCCESS' } else { 'FAILED ' }
      Write-Host ("  {0} [{1:yyyy-MM-dd HH:mm}] Update {2}: {3}" -f $tag, $evt.TimeCreated, $desc, ($evt.Message -split "`r?`n")[0])
    }
  } else {
    Write-Host "[INFO] No Windows Update installation events recorded in the last 30 days."
  }
} catch {
  if ($_.Exception.Message -match 'No events were found') {
    Write-Host "[OK]   Zero Windows Update failure events recorded in the last 30 days."
  } else {
    Write-Host "[INFO] Windows Update log check note: $($_.Exception.Message)"
  }
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] If a recent Windows Update broke printing, Wi-Fi, or software compatibility:"
Write-Host "       1. Check the newest KB number from Phase 1."
Write-Host "       2. Search Microsoft Release Health dashboard for known issues with that KB."
Write-Host "       3. If required, remove via Command Prompt: wusa.exe /uninstall /kb:XXXXXXX /promptrestart"
""")),
    # ---- Network --------------------------------------------------------------------------
    Script("listening", "Listening ports and the program behind each", "Network",
           "Every TCP port waiting for connections and every UDP endpoint, with the process "
           "name and path. Anything listening on 0.0.0.0 or :: is reachable from the network "
           "unless the firewall blocks it -- the list a hardening review starts from.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Listening Ports & Process Attribution Matrix"
Write-Host "PURPOSE   : Identifies open network doors and the applications listening on them"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK LISTENING PORTS PRIMER:"
Write-Host "  • 0.0.0.0 / [::] : Bound to ALL network interfaces -- reachable from LAN/Internet (EXPOSED)!"
Write-Host "  • 127.0.0.1 / ::1: Bound to LOCALHOST only -- cannot be reached from outside the machine."
Write-Host "  • High-Risk Ports: 3389 (RDP), 445 (SMB), 5985 (WinRM), 22 (SSH), 80/443 (Web)."
Write-Host ""
$procs = @{}
try {
  Get-Process -ErrorAction SilentlyContinue | ForEach-Object { $procs[$_.Id] = $_ }
} catch {
  Write-Host "[INFO] Process inventory note: $($_.Exception.Message)"
}
$tcp = @()
$udp = @()
try {
  $tcp = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $procs[[int]$_.OwningProcess]
    $pName = if ($p) { $p.ProcessName } else { 'System/Protected' }
    $pPath = if ($p -and $p.Path) { $p.Path } else { 'N/A' }
    [pscustomobject]@{ Proto='TCP'; Address=$_.LocalAddress; Port=$_.LocalPort; PID=$_.OwningProcess; Process=$pName; Path=$pPath }
  }
} catch {
  Write-Host "[INFO] TCP query note: $($_.Exception.Message)"
}
try {
  $udp = Get-NetUDPEndpoint -ErrorAction SilentlyContinue | ForEach-Object {
    $p = $procs[[int]$_.OwningProcess]
    $pName = if ($p) { $p.ProcessName } else { 'System/Protected' }
    $pPath = if ($p -and $p.Path) { $p.Path } else { 'N/A' }
    [pscustomobject]@{ Proto='UDP'; Address=$_.LocalAddress; Port=$_.LocalPort; PID=$_.OwningProcess; Process=$pName; Path=$pPath }
  }
} catch {
  Write-Host "[INFO] UDP query note: $($_.Exception.Message)"
}

$all = @($tcp) + @($udp)
$exposed = $all | Where-Object { $_.Address -in ('0.0.0.0', '::') }

Write-Host "=== Phase 1: High-Exposure Network-Accessible Endpoints (0.0.0.0 / ::) ==="
if ($exposed) {
  Write-Host ("[WARN] Found {0} endpoint(s) listening on all external interfaces:" -f $exposed.Count)
  foreach ($ep in ($exposed | Sort-Object Port)) {
    $tag = if ($ep.Port -in (3389, 445, 135, 5985)) { '[WARN]' } else { '[INFO]' }
    Write-Host ("  {0} {1,-4} Port {2,-5} PID: {3,-6} Process: {4,-22} ({5})" -f $tag, $ep.Proto, $ep.Port, $ep.PID, $ep.Process, $ep.Path)
  }
} else {
  Write-Host "[OK]   Zero endpoints listening on 0.0.0.0 or ::."
}
Write-Host ""

Write-Host "=== Phase 2: Localhost-Only Internal Endpoints (127.0.0.1 / ::1) ==="
$local = $all | Where-Object { $_.Address -in ('127.0.0.1', '::1') }
Write-Host ("[OK]   Found {0} safe internal-only listening endpoint(s)." -f $local.Count)
foreach ($ep in ($local | Sort-Object Port | Select-Object -First 10)) {
  Write-Host ("  [INFO] {0,-4} Port {1,-5} PID: {2,-6} Process: {3}" -f $ep.Proto, $ep.Port, $ep.PID, $ep.Process)
}

Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($exposed) {
  Write-Host "[INFO] Review Phase 1 endpoints to confirm all open ports are legitimate business tools."
  Write-Host "       If an unexpected port or unknown process is listening on 0.0.0.0:"
  Write-Host "       1. Check the executable path in Phase 1."
  Write-Host "       2. If unneeded, close application or set service to Manual/Disabled."
  Write-Host "       3. Verify Windows Firewall profile is set to Block inbound by default."
} else {
  Write-Host "[OK]   STATUS: HARDENED. No unexpected ports are listening externally."
}
"""),
           admin=True),
    Script("ipconfig", "Full network configuration", "Network",
           "ipconfig /all: every adapter, its addresses, DNS servers, DHCP lease and MAC.",
           "ipconfig /all", kind=CMD),
    Script("dns_cache", "DNS client cache", "Network",
           "Names this PC resolved recently and what they resolved to. Useful when one app "
           "reaches a site and another does not, and for spotting an unexpected domain. "
           "Needs administrator: for a standard user Get-DnsClientCache returns an EMPTY "
           "list without any error (measured 2026-10-16: 0 entries unelevated, 94 "
           "elevated; ipconfig /displaydns says 'requires elevation'), so this script "
           "refuses rather than report an empty cache.",
           _ps(_IS_ADMIN + r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: DNS Client Cache & Name Resolution History"
Write-Host "PURPOSE   : Inspects locally cached DNS lookups and domain attribution"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK DNS PRIMER:"
Write-Host "  • Windows caches every DNS query to speed up web browsing and network traffic."
Write-Host "  • Stale DNS cache causes: 'Website unreachable in Chrome, but works on other PCs'."
Write-Host "  • TTL (Time-To-Live): Seconds remaining before Windows must re-query the DNS server."
Write-Host ""
if (-not $isAdmin) {
  Write-Host "NOT AVAILABLE: needs administrator rights -- Windows returns an empty list instead of an error unelevated."
  return
}
try {
  $c = Get-DnsClientCache -ErrorAction Stop
  if (-not $c) {
    Write-Host "[OK]   The DNS client cache is currently empty."
  } else {
    Write-Host ("[INFO] Cached DNS records detected: {0}" -f $c.Count)
    Write-Host "=== Phase 1: Recently Resolved Hostnames & IPs ==="
    foreach ($entry in ($c | Sort-Object Entry | Select-Object -First 30)) {
      $data = if ($entry.Data) { $entry.Data } else { 'NXDOMAIN / Empty' }
      Write-Host ("  [INFO] Host: {0,-36} TTL: {1,4}s | Data: {2}" -f $entry.Entry, $entry.TimeToLive, $data)
    }
  }
} catch {
  Write-Host ("ERROR: Failed to read DNS cache: {0}" -f $_.Exception.Message)
  return
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] If diagnosing website or intranet connection failures:"
Write-Host "       1. If an IP in Phase 1 is outdated: flush cache via Command Prompt: ipconfig /flushdns"
Write-Host "       2. Test fresh DNS lookup: Resolve-DnsName <hostname> or nslookup <hostname>"
"""),
           admin=True),
    Script("routes", "Routing table", "Network",
           "IPv4 and IPv6 routes with their interface and metric. A VPN's 0.0.0.0/1 + "
           "128.0.0.0/1 pair, or a second default route, explains most 'traffic goes the "
           "wrong way' problems.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: IPv4 / IPv6 Routing Table & Default Gateway Metric"
Write-Host "PURPOSE   : Resolves 'traffic routed to wrong network adapter or broken VPN'"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK ROUTING PRIMER:"
Write-Host "  • 0.0.0.0/0 (IPv4) & ::/0 (IPv6) are the DEFAULT GATEWAYS to the Internet."
Write-Host "  • METRIC: The lower the metric number, the HIGHER the priority! If Wi-Fi has metric 20"
Write-Host "    and Ethernet has metric 35, Windows will route internet over Wi-Fi instead of cable!"
Write-Host "  • VPN Split-Tunnel: 0.0.0.0/1 and 128.0.0.0/1 routes route half the internet each."
Write-Host ""
try {
  $routes = Get-NetRoute -ErrorAction Stop
  $defaultRoutes = $routes | Where-Object { $_.DestinationPrefix -in ('0.0.0.0/0', '::/0') } | Sort-Object RouteMetric
  Write-Host "=== Phase 1: Default Gateways (Internet Exit Points) ==="
  if ($defaultRoutes) {
    foreach ($dr in $defaultRoutes) {
      Write-Host ("  [OK]   AddressFamily: {0,-6} Gateway: {1,-20} Interface: {2,-18} Metric: {3}" -f $dr.AddressFamily, $dr.NextHop, $dr.InterfaceAlias, $dr.RouteMetric)
    }
  } else {
    Write-Host "[BAD]  NO DEFAULT GATEWAY ROUTE DETECTED! System cannot route to the Internet."
  }
  Write-Host ""
  Write-Host "=== Phase 2: Active Subnet & VPN Routes (Sample) ==="
  $subnets = $routes | Where-Object { $_.DestinationPrefix -notin ('0.0.0.0/0', '::/0') } | Sort-Object InterfaceAlias, RouteMetric
  foreach ($r in ($subnets | Select-Object -First 15)) {
    Write-Host ("  [INFO] Prefix: {0,-22} NextHop: {1,-18} Interface: {2,-18} Metric: {3}" -f $r.DestinationPrefix, $r.NextHop, $r.InterfaceAlias, $r.RouteMetric)
  }
} catch {
  Write-Host ("ERROR: Failed to retrieve routing table: {0}" -f $_.Exception.Message)
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] If Internet traffic is slow or taking the wrong network path:"
Write-Host "       1. Check Phase 1: Ensure the faster physical connection (Ethernet) has the lowest metric."
Write-Host "       2. To adjust priority, modify adapter properties or run set-netipinterface -InterfaceAlias '<name>' -InterfaceMetric <number>."
""")),
    Script("wifi_profiles", "Saved Wi-Fi networks", "Network",
           "The Wi-Fi networks this PC remembers and will join automatically. Names only -- "
           "this never prints a password.",
           "netsh wlan show profiles", kind=CMD),
    # ---- Security -------------------------------------------------------------------------
    Script("local_admins", "Members of the local Administrators group", "Security",
           "Who has full control of this PC. Looked up by the group's well-known SID "
           "(S-1-5-32-544), so it works on any Windows language.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Local Administrators Group Members (SID S-1-5-32-544)"
Write-Host "PURPOSE   : Audits accounts with full administrative control over this machine"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PRIVILEGE PRIMER:"
Write-Host "  • Well-known SID S-1-5-32-544 is the universal SID for Administrators across all languages."
Write-Host "  • Principle of Least Privilege: Standard users should NOT belong to this group."
Write-Host "  • Local admin accounts represent elevated risk if left unmanaged (e.g. without LAPS)."
Write-Host ""
try {
  $admins = Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop
  Write-Host "=== Phase 1: Administrators Group Roster ==="
  if ($admins) {
    Write-Host ("[INFO] Total Administrators detected: {0}" -f $admins.Count)
    foreach ($m in $admins) {
      $source = if ($m.PrincipalSource) { $m.PrincipalSource } else { 'Local' }
      $tag = if ($m.ObjectClass -eq 'Group') { '[INFO]' } else { '[WARN]' }
      Write-Host ("  {0} Name: {1,-32} Class: {2,-10} Source: {3,-12} SID: {4}" -f $tag, $m.Name, $m.ObjectClass, $source, $m.SID)
    }
  } else {
    Write-Host "[WARN] No members found in Administrators group."
  }
} catch {
  Write-Host ("ERROR: Failed to query Local Administrators group: {0}" -f $_.Exception.Message)
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] Security Audit Guidance:"
Write-Host "       1. Verify every user account listed in Phase 1 is authorized for full local control."
Write-Host "       2. In corporate environments, domain users should rarely be direct members; use LAPS or PIM."
""")),
    Script("third_party_tasks", "Scheduled tasks not shipped by Microsoft", "Security",
           "Every scheduled task outside \\Microsoft\\, with what it runs and as whom. Updaters "
           "live here -- and so does malware that wants to come back after a reboot.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Third-Party Scheduled Tasks (Outside \\Microsoft\\)"
Write-Host "PURPOSE   : Identifies non-OS background persistence, updaters, and rogue tasks"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK PERSISTENCE PRIMER:"
Write-Host "  • Scheduled Tasks outside \Microsoft\ are created by third-party applications or malware."
Write-Host "  • Common legitimate updaters: GoogleUpdate, Adobe, EdgeUpdate, OneDrive."
Write-Host "  • SUSPICIOUS: Tasks executing from %TEMP%, %APPDATA%, or running encoded scripts."
Write-Host ""
try {
  $tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -notlike '\Microsoft\*' }
  Write-Host "=== Phase 1: Non-Microsoft Task Inventory ==="
  if ($tasks) {
    Write-Host ("[INFO] Found {0} third-party scheduled task(s):" -f $tasks.Count)
    foreach ($t in ($tasks | Sort-Object TaskPath, TaskName)) {
      $fullName = $t.TaskPath + $t.TaskName
      $actions = ($t.Actions | ForEach-Object { ($_.Execute + ' ' + $_.Arguments).Trim() }) -join ' ; '
      $runAs = if ($t.Principal.UserId) { $t.Principal.UserId } else { 'System/Unknown' }
      $tag = if ($t.State -eq 'Running') { '[INFO]' } elseif ($t.State -eq 'Ready') { '[OK]  ' } else { '[INFO]' }
      Write-Host ("  {0} Task: {1,-36} State: {2,-8} RunAs: {3}" -f $tag, $fullName, $t.State, $runAs)
      if ($actions) {
        Write-Host ("       Action: {0}" -f $actions)
      }
    }
  } else {
    Write-Host "[OK]   Zero third-party scheduled tasks registered outside \Microsoft\."
  }
} catch {
  Write-Host ("ERROR: Failed to enumerate scheduled tasks: {0}" -f $_.Exception.Message)
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] If investigating high background CPU or suspicious popups:"
Write-Host "       1. Look for tasks whose Action points to user AppData or Temp folders."
Write-Host "       2. To disable an unwanted task, use Task Scheduler (taskschd.msc) or unregister-scheduledtask."
"""),
           admin=True),
    Script("expiring_certs", "Certificates expiring within 60 days", "Security",
           "Personal certificates (this user and the computer) that expire in the next 60 days "
           "or already have -- VPN, Wi-Fi (802.1X) and code-signing breakage, before it happens.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Personal Certificates Expiring Within 60 Days"
Write-Host "PURPOSE   : Prevents Wi-Fi 802.1X, VPN client, and code-signing authentication outages"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK CERTIFICATE PRIMER:"
Write-Host "  • User and Computer Personal stores hold authentication certificates (Wi-Fi, VPN, EFS, Email)."
Write-Host "  • When a client cert expires, the user suddenly cannot connect to corporate Wi-Fi or VPN!"
Write-Host ""
$limit = (Get-Date).AddDays(60)
$now = Get-Date
$stores = 'Cert:\CurrentUser\My', 'Cert:\LocalMachine\My'
$found = @()
try {
  foreach ($s in $stores) {
    if (Test-Path $s) {
      Get-ChildItem $s -ErrorAction SilentlyContinue | Where-Object { $_.NotAfter -lt $limit } | ForEach-Object {
        $found += [pscustomobject]@{ Store=$s; Subject=$_.Subject; NotAfter=$_.NotAfter; Issuer=$_.Issuer; Thumbprint=$_.Thumbprint }
      }
    }
  }
  Write-Host "=== Phase 1: Expiring / Expired Certificates Inventory ==="
  if ($found) {
    Write-Host ("[WARN] Found {0} certificate(s) expired or expiring within 60 days:" -f $found.Count)
    foreach ($c in ($found | Sort-Object NotAfter)) {
      $daysLeft = [math]::Round(($c.NotAfter - $now).TotalDays, 0)
      $tag = if ($daysLeft -lt 0) { '[BAD] ' } elseif ($daysLeft -le 14) { '[BAD] ' } else { '[WARN]' }
      $status = if ($daysLeft -lt 0) { "EXPIRED ($([math]::Abs($daysLeft)) days ago)" } else { "Expires in $daysLeft days" }
      Write-Host ("  {0} [{1:yyyy-MM-dd}] ({2}) Store: {3}" -f $tag, $c.NotAfter, $status, $c.Store)
      Write-Host ("       Subject: {0}" -f $c.Subject)
      Write-Host ("       Issuer : {0}" -f $c.Issuer)
    }
  } else {
    Write-Host "[OK]   Zero personal certificates are expired or expiring in the next 60 days."
  }
} catch {
  Write-Host ("ERROR: Failed to scan certificate stores: {0}" -f $_.Exception.Message)
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($found) {
  Write-Host "[WARN] ACTION REQUIRED: Renew expiring certificates before authentication fails."
  Write-Host "       1. Check whether certificate is issued by internal Enterprise PKI or public CA."
  Write-Host "       2. To request auto-renewal via Active Directory: certreq -enroll or certmgr.msc."
} else {
  Write-Host "[OK]   STATUS: CERTIFICATE HEALTHY. All personal certificates have valid lifetimes."
}
""")),
    Script("firewall_profiles", "Firewall profiles", "Security",
           "Whether the Domain, Private and Public firewall profiles are on, their default "
           "inbound/outbound actions, and which profile each network is in right now. A "
           "home network sitting in the Public profile (or a public one in Private) is a "
           "common surprise.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Windows Firewall Profiles & Network Location State"
Write-Host "PURPOSE   : Audits Domain, Private, and Public perimeter security postures"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK FIREWALL PRIMER:"
Write-Host "  • Public Profile: Strictest rules -- blocks all unsolicited incoming traffic (cafes/airports)."
Write-Host "  • Private Profile: Allows local network sharing (printers, home media, file sharing)."
Write-Host "  • Domain Profile: Active when connected to corporate Active Directory domain network."
Write-Host "  • CRITICAL RULE: Inbound action MUST be 'Block' across all three profiles!"
Write-Host ""
try {
  $profiles = Get-NetFirewallProfile -ErrorAction Stop
  $connections = Get-NetConnectionProfile -ErrorAction SilentlyContinue

  Write-Host "=== Phase 1: Active Network Connection Locations ==="
  if ($connections) {
    foreach ($conn in $connections) {
      Write-Host ("  [INFO] Interface: {0,-20} SSID/Network: {1,-20} Category: {2}" -f $conn.InterfaceAlias, $conn.Name, $conn.NetworkCategory)
    }
  } else {
    Write-Host "[INFO] No active network connections reported."
  }
  Write-Host ""

  Write-Host "=== Phase 2: Windows Firewall Profile Configuration ==="
  $anyDisabled = $false
  $anyInboundAllow = $false
  foreach ($p in $profiles) {
    $enabledTag = if ($p.Enabled -eq 'True') { '[OK]  ' } else { $anyDisabled = $true; '[BAD] ' }
    $inboundTag = if ($p.DefaultInboundAction -eq 'Block') { '[OK]  ' } else { $anyInboundAllow = $true; '[BAD] ' }
    Write-Host ("  Profile: {0}" -f $p.Name)
    Write-Host ("    {0} State          : {1}" -f $enabledTag, $(if ($p.Enabled -eq 'True') { 'ACTIVE (ON)' } else { 'DISABLED (OFF)!' }))
    Write-Host ("    {1} Inbound Action : {0}" -f $p.DefaultInboundAction, $inboundTag)
    Write-Host ("    [INFO] Outbound Action: {0}" -f $p.DefaultOutboundAction)
    Write-Host ("    [INFO] Logging Blocked: {0}" -f $p.LogBlocked)
  }
} catch {
  Write-Host ("ERROR: Failed to query Windows Firewall: {0}" -f $_.Exception.Message)
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($anyDisabled) {
  Write-Host "[BAD]  SECURITY COMPROMISE: One or more firewall profiles are DISABLED!"
  Write-Host "       ACTION REQUIRED: Re-enable Windows Firewall immediately via Windows Security or netsh."
} elseif ($anyInboundAllow) {
  Write-Host "[BAD]  OVER-PERMISSIVE: Inbound action set to Allow! System is vulnerable to port probes."
} else {
  Write-Host "[OK]   STATUS: FIREWALL POSTURE HARDENED. All profiles are active with Inbound Block policy."
}
""")),
    Script("shares", "Shared folders", "Security",
           "Every SMB share this PC offers, including the hidden administrative ones (C$, "
           "ADMIN$), with path and description.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Local SMB Network Shares (Server Message Block)"
Write-Host "PURPOSE   : Audits shared directories and administrative shares exposed on LAN"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK SMB PRIMER:"
Write-Host "  • Administrative Shares (C$, ADMIN$, IPC$): Built-in Windows shares accessible only by Admins."
Write-Host "  • Custom Shares: User-created folders shared across the local network."
Write-Host "  • Risk: Over-permissive NTFS or share permissions can expose sensitive corporate files."
Write-Host ""
try {
  $shares = Get-SmbShare -ErrorAction Stop
  Write-Host "=== Phase 1: Local SMB Shares Inventory ==="
  if ($shares) {
    Write-Host ("[INFO] Total SMB Shares detected: {0}" -f $shares.Count)
    foreach ($s in ($shares | Sort-Object Special, Name)) {
      $kind = if ($s.Special) { 'Admin / Special' } else { 'Custom Share' }
      $tag = if ($s.Special) { '[INFO]' } else { '[WARN]' }
      Write-Host ("  {0} Share: {1,-14} Kind: {2,-16} Path: {3}" -f $tag, $s.Name, $kind, $s.Path)
      if ($s.Description) {
        Write-Host ("       Description: {0}" -f $s.Description)
      }
    }
  } else {
    Write-Host "[OK]   Zero SMB shares found on this system."
  }
} catch {
  Write-Host ("ERROR: Failed to query SMB shares: {0}" -f $_.Exception.Message)
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
Write-Host "[INFO] Security Audit Guidance:"
Write-Host "       1. Special shares (C$, ADMIN$) are normal for Windows domain management."
Write-Host "       2. Verify that any non-special (custom) shares listed above have legitimate business requirements."
""")),
    # ---- Inventory ------------------------------------------------------------------------
    Script("system_summary", "System summary", "Inventory",
           "Edition, version and build, computer name, domain or workgroup, manufacturer and "
           "model, BIOS version and date, CPU, memory -- the header of every support ticket.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Comprehensive System Hardware & OS Summary"
Write-Host "PURPOSE   : Standardized system profile for Helpdesk tickets and escalation"
Write-Host "================================================================================"
try {
  $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
  $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
  $bios = Get-CimInstance Win32_BIOS -ErrorAction Stop
  $cpu = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1
  $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction SilentlyContinue

  Write-Host "=== Phase 1: Operating System & Build Baseline ==="
  Write-Host ("OS Name         : {0}" -f $os.Caption)
  Write-Host ("OS Version      : {0} (Build {1}.{2})" -f $cv.DisplayVersion, $cv.CurrentBuild, $cv.UBR)
  Write-Host ("Architecture    : {0}" -f $os.OSArchitecture)
  Write-Host ("Install Date    : {0:yyyy-MM-dd}" -f $os.InstallDate)
  Write-Host ""

  Write-Host "=== Phase 2: Hardware Platform & Motherboard ==="
  Write-Host ("Manufacturer    : {0}" -f $cs.Manufacturer)
  Write-Host ("Model           : {0}" -f $cs.Model)
  Write-Host ("System Type     : {0}" -f $cs.SystemType)
  Write-Host ("BIOS Version    : {0} ({1:yyyy-MM-dd})" -f $bios.SMBIOSBIOSVersion, $bios.ReleaseDate)
  Write-Host ""

  Write-Host "=== Phase 3: CPU & Memory Capacity ==="
  Write-Host ("Processor       : {0}" -f $cpu.Name.Trim())
  Write-Host ("Cores / Threads : {0} Cores, {1} Logical Processors" -f $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors)
  Write-Host ("Total RAM       : {0:N1} GB ({1:N0} MB)" -f ($cs.TotalPhysicalMemory / 1GB), ($cs.TotalPhysicalMemory / 1MB))
  Write-Host ""

  Write-Host "=== Phase 4: Identity & Domain Membership ==="
  Write-Host ("Computer Name   : {0}" -f $cs.Name)
  $domainStr = if ($cs.PartOfDomain) { "Domain ({0})" -f $cs.Domain } else { "Workgroup ({0})" -f $cs.Workgroup }
  Write-Host ("Membership      : {0}" -f $domainStr)
} catch {
  Write-Host ("ERROR: Failed to retrieve system summary: {0}" -f $_.Exception.Message)
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TICKET HEADER (READY TO COPY/PASTE) ==="
Write-Host "================================================================================"
Write-Host ("[INFO] OS: {0} {1} (Build {2}) | CPU: {3} | RAM: {4:N1}GB | PC: {5}" -f $os.Caption, $cv.DisplayVersion, $cv.CurrentBuild, $cpu.Name.Trim(), ($cs.TotalPhysicalMemory / 1GB), $cs.Name)
""")),
    Script("third_party_drivers", "Third-party drivers", "Inventory",
           "Drivers installed from outside Windows (oem*.inf), with provider, version and "
           "date -- the usual suspects for a blue screen or a device that stopped working.",
           _ps(r"""
Write-Host "================================================================================"
Write-Host "DIAGNOSTIC: Third-Party OEM Drivers (oem*.inf)"
Write-Host "PURPOSE   : Audits installed vendor drivers to pinpoint outdated crash culprits"
Write-Host "================================================================================"
Write-Host "[INFO] HELPDESK DRIVER PRIMER:"
Write-Host "  • 'oem*.inf' drivers are installed by hardware vendors (GPU, Audio, Wi-Fi, Chipset)."
Write-Host "  • Over 80% of kernel BSODs (IRQL, Page Fault, TDR) stem from buggy or outdated OEM drivers."
Write-Host "  • Compare driver dates against vendor release notes when troubleshooting hardware freezes."
Write-Host ""
try {
  $drivers = Get-CimInstance Win32_PnPSignedDriver -ErrorAction Stop |
    Where-Object { $_.InfName -like 'oem*.inf' } |
    Sort-Object DriverProviderName, DeviceName

  Write-Host "=== Phase 1: Third-Party Driver Roster ==="
  if ($drivers) {
    Write-Host ("[INFO] Found {0} third-party OEM driver package(s):" -f $drivers.Count)
    $now = Get-Date
    $agingCount = 0
    foreach ($d in $drivers) {
      $dDate = if ($d.DriverDate) { $d.DriverDate.ToString('yyyy-MM-dd') } else { 'Unknown' }
      $ageYears = if ($d.DriverDate) { [math]::Round(($now - $d.DriverDate).TotalDays / 365, 1) } else { 0 }
      $tag = if ($ageYears -ge 4) { $agingCount++; '[WARN]' } else { '[INFO]' }
      Write-Host ("  {0} Device: {1,-36} Provider: {2,-18} v{3,-14} ({4}, INF: {5})" -f $tag, $d.DeviceName, $d.DriverProviderName, $d.DriverVersion, $dDate, $d.InfName)
    }
  } else {
    Write-Host "[OK]   Zero third-party OEM drivers detected (using in-box Microsoft drivers)."
  }
} catch {
  Write-Host ("ERROR: Failed to query signed drivers: {0}" -f $_.Exception.Message)
}
Write-Host ""
Write-Host "================================================================================"
Write-Host "=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ==="
Write-Host "================================================================================"
if ($agingCount -gt 0) {
  Write-Host ("[WARN] Found {0} driver(s) older than 4 years. Outdated drivers are prime suspects for sleep/BSOD issues." -f $agingCount)
  Write-Host "       WHAT HELPDESK SHOULD DO:"
  Write-Host "       1. Check device vendor site (AMD, Intel, Realtek, NVIDIA) for updated drivers."
  Write-Host "       2. Open Driver Manager tab in WinClientTool to export or inspect inf packages."
} else {
  Write-Host "[OK]   STATUS: DRIVERS HEALTHY. OEM drivers are modern and signed."
}
"""), timeout=180),
]

CLIENT_CATEGORIES = ("Troubleshooting", "System", "Hardware", "Storage", "Performance",
                     "Event logs", "Network", "Security", "Users & groups",
                     "Updates & servicing", "Services & processes", "Startup & tasks",
                     "Applications", "Power", "Printing", "Group Policy", "Remote access",
                     "Inventory")
SERVER_CATEGORIES = ("Active Directory", "Group Policy (domain)", "DNS Server", "DHCP Server",
                     "File server", "Storage & free space", "IIS", "Hyper-V", "Failover Cluster",
                     "Remote Desktop Services", "WSUS", "Certificate Services", "Print server",
                     "Log parsing", "Fleet (remote servers)", "Roles & features")
USER_CATEGORY = "My scripts"
CATEGORIES = CLIENT_CATEGORIES + SERVER_CATEGORIES + (USER_CATEGORY,)


# ---- user scripts ----------------------------------------------------------------------------

#: The folder the person chose in the pane (persisted in the app config as
#: `modules.scripts.folder`); None means the default below.
_folder_override: Optional[str] = None

#: Child processes see the folder in this variable, so a script (the fleet
#: checks read servers.txt from it) follows the person's choice.
FOLDER_ENV = "WCT_SCRIPTS_DIR"


def default_user_scripts_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "WindowsTweaker", "scripts")


def set_user_scripts_dir(path: Optional[str]) -> None:
    """Use `path` as the scripts folder (None or "" = back to the default)."""
    global _folder_override
    _folder_override = os.path.normpath(path) if path else None


def user_scripts_dir() -> str:
    return _folder_override or default_user_scripts_dir()


_PS_BLOCK = re.compile(r"<#(.*?)#>", re.S)


def describe_file(path: str, text: str) -> str:
    """A user script's description: its leading comment block, or its first
    comment lines; "" when it has none."""
    head = text.lstrip("﻿").lstrip()
    if path.lower().endswith(".ps1"):
        m = _PS_BLOCK.match(head)
        if m:
            lines = [ln.strip() for ln in m.group(1).splitlines()]
            lines = [ln for ln in lines if ln and not ln.startswith(".")]
            return " ".join(lines)
        prefix = "#"
    else:
        prefix = "rem"
    out = []
    for line in head.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("@echo"):
            continue
        if low.startswith(prefix) or low.startswith("::"):
            out.append(re.sub(r"^(#|rem\b|::)\s*", "", s, flags=re.I))
        elif s:
            break
    return " ".join(x for x in out if x)


def load_user_scripts(folder: Optional[str] = None) -> List[Script]:
    """Every .ps1/.cmd/.bat in the user's scripts folder, sorted by name.
    A file that cannot be read is listed with the reason, never dropped."""
    folder = folder or user_scripts_dir()
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("scripts: cannot list %s: %s", folder, e)
        return []
    out = []
    for name in names:
        path = os.path.join(folder, name)
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".ps1", ".cmd", ".bat") or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
            desc = describe_file(path, text) or "Your own script (no description comment)."
        except OSError as e:
            logger.warning("scripts: cannot read %s: %s", path, e)
            text, desc = "", f"Could not read this file: {e}"
        out.append(Script(f"user:{name.lower()}", name, "My scripts", desc, text,
                          kind=POWERSHELL if ext == ".ps1" else CMD, read_only=False,
                          timeout=1800.0, path=path))
    return out


# ---- editing ---------------------------------------------------------------------------------

_WRAP_OPEN = "& {\n"
_WRAP_CLOSE = "\n} 2>&1 | ForEach-Object { "


def editable_source(script: Script) -> str:
    """A script as a person would write it: a built-in's body without the
    error/progress wrapper `ps()` adds (the copy is run the same way as any
    user script, so it needs none). A user script is its own file."""
    body = script.body
    if script.path or script.kind == CMD:
        return body
    start = body.find(_WRAP_OPEN)
    end = body.rfind(_WRAP_CLOSE)
    if start < 0 or end < start:
        return body
    head = body[:start]
    keep = [ln for ln in head.splitlines() if ln.startswith("& {") or ln.startswith("$Progress")]
    return "\n".join(keep + [body[start + len(_WRAP_OPEN):end]]).strip() + "\n"


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text).strip("_") or "script"


def export_for_editing(script: Script, folder: Optional[str] = None) -> tuple:
    """Copy a built-in into the user's scripts folder so it can be edited, and
    return (path, created). An existing copy is never overwritten -- it may
    hold the user's edits -- and is returned with created=False."""
    folder = folder or user_scripts_dir()
    os.makedirs(folder, exist_ok=True)
    ext = ".cmd" if script.kind == CMD else ".ps1"
    path = os.path.join(folder, _safe_name(script.id) + ext)
    if os.path.exists(path):
        return path, False
    if script.kind == CMD:
        text = (f"@echo off\nrem {script.title}\nrem {script.description}\n"
                f"rem Copied from WinClientTool's built-in '{script.id}'.\n{script.body}\n")
    else:
        desc = script.description.replace("#>", "# >")
        text = (f"<#\n.SYNOPSIS\n{script.title}\n\n.DESCRIPTION\n{desc}\n\n"
                f".NOTES\nCopied from WinClientTool's built-in script '{script.id}'. Edit freely;\n"
                f"the original stays in the app.\n#>\n\n{editable_source(script)}")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)
    return path, True


_NEW_TEMPLATE = """<#
.SYNOPSIS
{title}

.DESCRIPTION
What this script checks or does. This first comment block is the
description the Script Library shows.
#>

# Lines starting with [OK], [WARN], [BAD] or [INFO] are coloured in the app.
$os = Get-CimInstance Win32_OperatingSystem
"[INFO] $($os.Caption) build $($os.BuildNumber)"
"""


def new_script(title: str, folder: Optional[str] = None) -> str:
    """Create a new .ps1 from a template in the user's folder; never overwrite."""
    folder = folder or user_scripts_dir()
    os.makedirs(folder, exist_ok=True)
    stem = _safe_name(title)
    path = os.path.join(folder, stem + ".ps1")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem}_{n}.ps1")
        n += 1
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(_NEW_TEMPLATE.format(title=title))
    return path


#: (label, how to find it) in preference order. ISE is a Feature on Demand
#: on Windows 11 and may be absent; VS Code and Notepad are the fallbacks.
def editors() -> List[tuple]:
    import shutil
    windir = os.environ.get("SystemRoot") or os.environ.get("windir") or ""
    out = []
    ise = os.path.join(windir, "System32", "WindowsPowerShell", "v1.0", "powershell_ise.exe")
    if os.path.isfile(ise):
        out.append(("PowerShell ISE", ise))
    code = shutil.which("code")
    if code:
        out.append(("Visual Studio Code", code))
    out.append(("Notepad", os.path.join(windir, "System32", "notepad.exe")))
    return out


def editor_command(path: str, editor: Optional[str] = None) -> List[str]:
    """argv that opens `path` for editing: the named editor, else ISE for a
    .ps1 when installed, else the first available."""
    choices = editors()
    if editor:
        exe = next((e for label, e in choices if label == editor), None)
    else:
        ps1 = path.lower().endswith(".ps1")
        exe = next((e for label, e in choices if ps1 or label != "PowerShell ISE"), None)
    exe = exe or choices[-1][1]
    if exe.lower().endswith(".cmd"):          # VS Code's launcher is a batch file
        return [cmd_exe(), "/c", exe, path]
    return [exe, path]


# ---- running ---------------------------------------------------------------------------------

@dataclass
class RunResult:
    script_id: str
    returncode: Optional[int]           # None = killed (cancelled or timed out)
    lines: List[str] = field(default_factory=list)
    cancelled: bool = False
    timed_out: bool = False
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled and not self.timed_out


def command_for(script: Script, body_file: str = "") -> List[str]:
    """argv for a script. User scripts run their own file; a built-in
    PowerShell body runs from `body_file`, a temporary copy `run` writes.

    Not -EncodedCommand: the antivirus here (Bitdefender) refused
    CreateProcess outright ("Access is denied") for an encoded command line
    that read browser profile folders, and for a command line that decoded
    Base64 -- a file on disk is what admin tools normally run."""
    if script.kind == CMD:
        if script.path:
            return [cmd_exe(), "/d", "/c", script.path]
        return [cmd_exe(), "/d", "/c", script.body]
    base = [powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-OutputFormat", "Text"]
    return base + ["-File", script.path or body_file]


def _body_file(script: Script) -> str:
    """Write a built-in's body to a private temp .ps1 (UTF-8 with BOM, which
    Windows PowerShell needs to read non-ASCII text correctly)."""
    import tempfile
    folder = os.path.join(tempfile.gettempdir(), "WinClientTool-scripts")
    os.makedirs(folder, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=re.sub(r"\W", "_", script.id) + "-", suffix=".ps1",
                                dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
        f.write(script.body)
    return path


def working_dir(script: Script) -> str:
    """Where a script runs: a user script in its own folder; a built-in in the
    user's profile, which is where PowerShell ISE starts too."""
    return os.path.dirname(script.path) if script.path else os.path.expanduser("~")


def prompt_line(script: Script) -> str:
    """The line ISE would show for running this script: `PS <dir>> <command>`."""
    where = working_dir(script)
    if script.path:
        command = f"& '{script.path}'" if script.kind == POWERSHELL else f"& '{cmd_exe()}' /c '{script.path}'"
    elif script.kind == CMD:
        command = script.body.strip().splitlines()[0]
    else:
        command = f".\\{_safe_name(script.id)}.ps1"
    return f"PS {where}> {command}"


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       timeout=10, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("scripts: could not kill process tree %s: %s", pid, e)


def run(script: Script, on_line: Callable[[str], None],
        is_cancelled: Callable[[], bool] = lambda: False,
        popen=subprocess.Popen) -> RunResult:
    """Run a script, streaming each output line. Cancel or timeout kills the
    whole process tree (a child holding the pipe would otherwise keep the read
    open long after the script was "stopped")."""
    import time
    start = time.monotonic()
    result = RunResult(script.id, None)
    body_file = _body_file(script) if script.kind == POWERSHELL and not script.path else ""
    env = dict(os.environ)
    env[FOLDER_ENV] = user_scripts_dir()
    try:
        proc = popen(command_for(script, body_file), stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     cwd=working_dir(script), env=env, creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        # An antivirus refusing the launch arrives here as "Access is denied".
        line = (f"ERROR: Windows refused to start the script: {e}. An antivirus may have "
                "blocked it -- check its quarantine or history.")
        logger.warning("scripts: %s could not start: %s", script.id, e)
        result.lines.append(line)
        on_line(line)
        result.returncode = -1
        _remove(body_file)
        return result
    done = threading.Event()

    def watch() -> None:
        while not done.wait(0.25):
            if is_cancelled():
                result.cancelled = True
            elif time.monotonic() - start > script.timeout:
                result.timed_out = True
            else:
                continue
            _kill_tree(proc.pid)
            return

    threading.Thread(target=watch, daemon=True, name=f"script-{script.id}").start()
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = decode(raw).rstrip("\r\n")
            result.lines.append(line)
            on_line(line)
        proc.wait(timeout=10)
        if not (result.cancelled or result.timed_out):
            result.returncode = proc.returncode
    except subprocess.TimeoutExpired:
        logger.warning("scripts: %s did not exit after its output closed", script.id)
        _kill_tree(proc.pid)
    finally:
        done.set()
        result.seconds = time.monotonic() - start
        _remove(body_file)
    return result


def _remove(path: str) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError as e:
        logger.warning("scripts: could not remove temp script %s: %s", path, e)


def decode(raw: bytes) -> str:
    """Console tools write the OEM code page, PowerShell's redirected output
    the ANSI one; UTF-8 first, then the console's own page."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        import ctypes
        try:
            cp = ctypes.windll.kernel32.GetOEMCP()
            return raw.decode(f"cp{cp}", errors="replace")
        except (AttributeError, LookupError):
            return raw.decode("mbcs", errors="replace")


def built_in() -> List[Script]:
    """Every shipped script: the core checks above plus the catalogs."""
    from .catalog import CATALOG
    return list(BUILT_IN) + list(CATALOG)


def all_scripts(user_folder: Optional[str] = None) -> List[Script]:
    return built_in() + load_user_scripts(user_folder)
