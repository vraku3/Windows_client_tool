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
$since = (Get-Date).AddDays(-30)
try {
  $events = Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074,6008,41,6005,6006; StartTime=$since} -ErrorAction Stop
} catch {
  # "No events match" is an answer; a refused or failed read is not.
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { 'No shutdown or restart events in the last 30 days.'; return }
  "ERROR: the System log could not be read: $($_.Exception.Message)"; return
}
$events | ForEach-Object {
  $what = switch ($_.Id) {
    1074 { 'Planned shutdown/restart' } 6008 { 'UNEXPECTED shutdown' }
    41 { 'Kernel-Power: rebooted without a clean shutdown' }
    6005 { 'Boot (event log started)' } 6006 { 'Clean shutdown (event log stopped)' } }
  $detail = ''
  if ($_.Id -eq 1074) {
    $p = $_.Properties
    $detail = "by $($p[6].Value) via $($p[0].Value) -- $($p[4].Value) ($($p[2].Value))"
  }
  [pscustomobject]@{ Time = $_.TimeCreated; Event = $_.Id; What = $what; Detail = $detail }
} """ + _WIDE)),
    Script("uptime", "Uptime, last boot and Fast Startup", "Troubleshooting",
           "When Windows last really booted and how long it has been up. With Fast Startup "
           "on, 'Shut down' is a hibernation of the kernel, so uptime keeps growing and "
           "driver/update problems that a restart would clear survive a shutdown -- this "
           "says whether it is on.",
           _ps(r"""
$os = Get-CimInstance Win32_OperatingSystem
$up = (Get-Date) - $os.LastBootUpTime
$fs = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -ErrorAction SilentlyContinue).HiberbootEnabled
[pscustomobject]@{
  'Last boot' = $os.LastBootUpTime
  'Uptime' = '{0}d {1}h {2}m' -f $up.Days, $up.Hours, $up.Minutes
  'Fast Startup' = $(if ($fs -eq 1) { 'ON -- Shut down does not reset the kernel; use Restart' } elseif ($fs -eq 0) { 'off' } else { 'not set (Windows default: on where hibernation is available)' })
  'Installed' = $os.InstallDate
} | Format-List | Out-String -Width 220""")),
    Script("auto_stopped", "Automatic services that are not running", "Troubleshooting",
           "Services set to start automatically that are stopped right now, with their last "
           "exit code. Trigger-start and delayed services stop on their own by design, so "
           "they are marked; a plain Automatic service with a non-zero exit code is the one "
           "that crashed.",
           _ps(r"""
$trigger = @{}
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services' -ErrorAction SilentlyContinue | ForEach-Object {
  if (Test-Path (Join-Path $_.PSPath 'TriggerInfo')) { $trigger[$_.PSChildName.ToLower()] = $true } }
$rows = Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State<>'Running'" | ForEach-Object {
  [pscustomobject]@{ Name = $_.Name; 'Display name' = $_.DisplayName; 'Exit code' = $_.ExitCode
    Kind = $(if ($trigger[$_.Name.ToLower()]) { 'trigger-start (stops by design)' } elseif ($_.DelayedAutoStart) { 'delayed' } else { 'automatic' }) } }
if (-not $rows) { 'Every automatic service is running.' } else { $rows | Sort-Object Kind, Name """ + _WIDE + "}")),
    Script("recent_software", "Software installed in the last 14 days", "Troubleshooting",
           "Programs whose install date is in the last two weeks (both registry views and "
           "per-user installs). 'It broke after I installed something' starts here.",
           _ps(r"""
$cut = (Get-Date).AddDays(-14).ToString('yyyyMMdd')
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
$rows = Get-ItemProperty $keys -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -and $_.InstallDate -match '^\d{8}$' -and $_.InstallDate -ge $cut } |
  Sort-Object InstallDate -Descending |
  Select-Object @{n='Installed';e={$_.InstallDate}}, DisplayName, DisplayVersion, Publisher
if (-not $rows) { 'Nothing recorded an install date in the last 14 days.' } else { $rows """ + _WIDE + "}")),
    Script("hotfixes", "Installed Windows updates", "Troubleshooting",
           "Every installed update (KB) with its install date, newest first -- for 'which "
           "update came in before this started'. Cumulative updates replace each other, so "
           "only the latest few appear.",
           _ps("Get-HotFix | Sort-Object InstalledOn -Descending | "
               "Select-Object HotFixID, Description, InstalledOn, InstalledBy " + _WIDE)),
    # ---- Network --------------------------------------------------------------------------
    Script("listening", "Listening ports and the program behind each", "Network",
           "Every TCP port waiting for connections and every UDP endpoint, with the process "
           "name and path. Anything listening on 0.0.0.0 or :: is reachable from the network "
           "unless the firewall blocks it -- the list a hardening review starts from.",
           _ps(r"""
$procs = @{}
Get-Process -ErrorAction SilentlyContinue | ForEach-Object { $procs[$_.Id] = $_ }
$tcp = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
  $p = $procs[[int]$_.OwningProcess]
  [pscustomobject]@{ Proto='TCP'; Address=$_.LocalAddress; Port=$_.LocalPort; PID=$_.OwningProcess
    Process=$(if ($p) { $p.ProcessName } else { '?' }); Path=$(if ($p) { $p.Path } else { '' }) } }
$udp = Get-NetUDPEndpoint -ErrorAction SilentlyContinue | ForEach-Object {
  $p = $procs[[int]$_.OwningProcess]
  [pscustomobject]@{ Proto='UDP'; Address=$_.LocalAddress; Port=$_.LocalPort; PID=$_.OwningProcess
    Process=$(if ($p) { $p.ProcessName } else { '?' }); Path=$(if ($p) { $p.Path } else { '' }) } }
@($tcp) + @($udp) | Sort-Object Proto, Port """ + _WIDE,
               "the paths of system and other users' processes are blank"),
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
           _ps(_IS_ADMIN + "if (-not $isAdmin) { 'Cannot read the DNS cache: it needs "
               "administrator rights. (Windows returns an empty list instead of an error, "
               "so an empty result here would not mean the cache is empty.)'; return }\n"
               "$c = Get-DnsClientCache -ErrorAction SilentlyContinue\n"
               "if (-not $c) { 'The DNS cache is empty.' } else { $c | Sort-Object Entry | "
               "Select-Object Entry, RecordName, Data, TimeToLive " + _WIDE + " }"),
           admin=True),
    Script("routes", "Routing table", "Network",
           "IPv4 and IPv6 routes with their interface and metric. A VPN's 0.0.0.0/1 + "
           "128.0.0.0/1 pair, or a second default route, explains most 'traffic goes the "
           "wrong way' problems.",
           _ps("Get-NetRoute -ErrorAction SilentlyContinue | Sort-Object AddressFamily, "
               "RouteMetric | Select-Object AddressFamily, DestinationPrefix, NextHop, "
               "InterfaceAlias, RouteMetric " + _WIDE)),
    Script("wifi_profiles", "Saved Wi-Fi networks", "Network",
           "The Wi-Fi networks this PC remembers and will join automatically. Names only -- "
           "this never prints a password.",
           "netsh wlan show profiles", kind=CMD),
    # ---- Security -------------------------------------------------------------------------
    Script("local_admins", "Members of the local Administrators group", "Security",
           "Who has full control of this PC. Looked up by the group's well-known SID "
           "(S-1-5-32-544), so it works on any Windows language.",
           _ps("Get-LocalGroupMember -SID S-1-5-32-544 | Select-Object Name, ObjectClass, "
               "PrincipalSource " + _WIDE)),
    Script("third_party_tasks", "Scheduled tasks not shipped by Microsoft", "Security",
           "Every scheduled task outside \\Microsoft\\, with what it runs and as whom. Updaters "
           "live here -- and so does malware that wants to come back after a reboot.",
           _ps(r"""
$rows = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
  [pscustomobject]@{ Task = $_.TaskPath + $_.TaskName; State = $_.State; 'Runs as' = $_.Principal.UserId
    Action = (($_.Actions | ForEach-Object { ($_.Execute + ' ' + $_.Arguments).Trim() }) -join ' ; ') } }
if (-not $rows) { 'No scheduled tasks outside \Microsoft\.' } else { $rows | Sort-Object Task | Format-List | Out-String -Width 220 }""",
               "tasks registered by other users may be missing"), admin=True),
    Script("expiring_certs", "Certificates expiring within 60 days", "Security",
           "Personal certificates (this user and the computer) that expire in the next 60 days "
           "or already have -- VPN, Wi-Fi (802.1X) and code-signing breakage, before it happens.",
           _ps(r"""
$limit = (Get-Date).AddDays(60)
$rows = 'Cert:\CurrentUser\My','Cert:\LocalMachine\My' | ForEach-Object {
  $store = $_; Get-ChildItem $_ -ErrorAction SilentlyContinue | Where-Object { $_.NotAfter -lt $limit } |
  ForEach-Object { [pscustomobject]@{ Store=$store; Subject=$_.Subject; Expires=$_.NotAfter; Issuer=$_.Issuer; Thumbprint=$_.Thumbprint } } }
if (-not $rows) { 'No personal certificate expires in the next 60 days.' } else { $rows | Sort-Object Expires """ + _WIDE + "}")),
    Script("firewall_profiles", "Firewall profiles", "Security",
           "Whether the Domain, Private and Public firewall profiles are on, their default "
           "inbound/outbound actions, and which profile each network is in right now. A "
           "home network sitting in the Public profile (or a public one in Private) is a "
           "common surprise.",
           _ps("Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction, "
               "DefaultOutboundAction, LogBlocked " + _WIDE + "\n"
               "Get-NetConnectionProfile | Select-Object InterfaceAlias, Name, "
               "NetworkCategory " + _WIDE)),
    Script("shares", "Shared folders", "Security",
           "Every SMB share this PC offers, including the hidden administrative ones (C$, "
           "ADMIN$), with path and description.",
           _ps("Get-SmbShare | Select-Object Name, Path, Description, CurrentUsers " + _WIDE)),
    # ---- Inventory ------------------------------------------------------------------------
    Script("system_summary", "System summary", "Inventory",
           "Edition, version and build, computer name, domain or workgroup, manufacturer and "
           "model, BIOS version and date, CPU, memory -- the header of every support ticket.",
           _ps(r"""
$os = Get-CimInstance Win32_OperatingSystem; $cs = Get-CimInstance Win32_ComputerSystem
$bios = Get-CimInstance Win32_BIOS; $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
[pscustomobject]@{
  'Windows' = "$($os.Caption) $($cv.DisplayVersion) (build $($cv.CurrentBuild).$($cv.UBR))"
  'Computer' = $cs.Name
  'Domain / workgroup' = $(if ($cs.PartOfDomain) { "domain $($cs.Domain)" } else { "workgroup $($cs.Workgroup)" })
  'Manufacturer / model' = "$($cs.Manufacturer) $($cs.Model)"
  'BIOS' = "$($bios.SMBIOSBIOSVersion) ($($bios.ReleaseDate.ToString('yyyy-MM-dd')))"
  'CPU' = $cpu.Name.Trim()
  'Memory' = '{0:N1} GB' -f ($cs.TotalPhysicalMemory / 1GB)
  'Architecture' = $os.OSArchitecture
} | Format-List | Out-String -Width 220""")),
    Script("third_party_drivers", "Third-party drivers", "Inventory",
           "Drivers installed from outside Windows (oem*.inf), with provider, version and "
           "date -- the usual suspects for a blue screen or a device that stopped working.",
           _ps("Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue | "
               "Where-Object { $_.InfName -like 'oem*.inf' } | Sort-Object DriverProviderName, "
               "DeviceName | Select-Object DeviceName, DriverProviderName, DriverVersion, "
               "@{n='Date';e={if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') }}}, InfName "
               + _WIDE), timeout=180),
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
