"""Which PowerShell module or tool a Script Library script needs, whether this
PC has it, and how Windows itself installs it. Qt-free.

The modules (ActiveDirectory, DnsServer, GroupPolicy, Hyper-V...) are
Microsoft components: they cannot be bundled into this app, and they would
not work loose anyway -- they ship as Windows features. The supported way to
get them is the one used here:

* Windows 10/11: RSAT as Features on Demand, `Add-WindowsCapability -Online
  -Name Rsat.<x>.Tools~~~~0.0.1.0` (every name below was read from
  `Get-WindowsCapability -Online -Name Rsat*` on this machine, 2026-10-16),
  or an optional feature (`Enable-WindowsOptionalFeature`) for the Hyper-V and
  IIS management modules.
* Windows Server: `Install-WindowsFeature <name>` -- the server-side names are
  Microsoft's documented feature names; they were not run here (a client).

Some checks need the ROLE itself (a CA, deduplication, MPIO, Windows Server
Backup); those have no client tool and say so.

Installing needs administrator rights and Windows Update or a configured
source; a PC that gets updates from WSUS fails Add-WindowsCapability with
0x800F0954 unless the "Specify settings for optional component installation
and component repair" policy allows Windows Update.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import library

logger = logging.getLogger(__name__)

CAPABILITY, OPTIONAL_FEATURE, SERVER_ONLY = "capability", "optional_feature", "server_only"


@dataclass(frozen=True)
class Requirement:
    key: str
    label: str
    probe: str                         # a command that exists once it is installed
    client_kind: str                   # CAPABILITY / OPTIONAL_FEATURE / SERVER_ONLY
    client_names: Tuple[str, ...]      # capability or optional-feature names
    server_features: Tuple[str, ...]   # Install-WindowsFeature names
    note: str = ""


REQUIREMENTS: Dict[str, Requirement] = {r.key: r for r in (
    Requirement("ad", "Active Directory tools (ActiveDirectory module, repadmin, dcdiag)",
                "Get-ADDomain", CAPABILITY, ("Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0",),
                ("RSAT-AD-PowerShell", "RSAT-ADDS-Tools")),
    Requirement("gpo", "Group Policy Management tools (GroupPolicy module)", "Get-GPO", CAPABILITY,
                ("Rsat.GroupPolicy.Management.Tools~~~~0.0.1.0",), ("GPMC",)),
    Requirement("dns", "DNS Server tools (DnsServer module)", "Get-DnsServerZone", CAPABILITY,
                ("Rsat.Dns.Tools~~~~0.0.1.0",), ("RSAT-DNS-Server",)),
    Requirement("dhcp", "DHCP Server tools (DhcpServer module)", "Get-DhcpServerv4Scope", CAPABILITY,
                ("Rsat.DHCP.Tools~~~~0.0.1.0",), ("RSAT-DHCP",)),
    Requirement("cluster", "Failover Clustering tools (FailoverClusters module)", "Get-Cluster",
                CAPABILITY, ("Rsat.FailoverCluster.Management.Tools~~~~0.0.1.0",),
                ("RSAT-Clustering-PowerShell",)),
    Requirement("fileservices", "File Services tools (FSRM, DFS Namespaces, DFS Replication)",
                "Get-FsrmQuota", CAPABILITY, ("Rsat.FileServices.Tools~~~~0.0.1.0",),
                ("RSAT-FSRM-Mgmt", "RSAT-DFS-Mgmt-Con")),
    Requirement("hyperv", "Hyper-V PowerShell module", "Get-VM", OPTIONAL_FEATURE,
                ("Microsoft-Hyper-V-Management-PowerShell",), ("Hyper-V-PowerShell",)),
    Requirement("iis", "IIS management scripts (WebAdministration module)", "Get-Website",
                OPTIONAL_FEATURE, ("IIS-WebServerManagementTools", "IIS-ManagementScriptingTools"),
                ("Web-Scripting-Tools",)),
    Requirement("rds", "Remote Desktop Services tools (RemoteDesktop module)", "Get-RDServer",
                CAPABILITY, ("Rsat.RemoteDesktop.Services.Tools~~~~0.0.1.0",), ("RSAT-RDS-Tools",)),
    Requirement("wsus", "WSUS tools (UpdateServices module)", "Get-WsusServer", CAPABILITY,
                ("Rsat.WSUS.Tools~~~~0.0.1.0",), ("UpdateServices-RSAT",)),
    Requirement("servermanager", "Server Manager tools (ServerManager module)",
                "Get-WindowsFeature", CAPABILITY, ("Rsat.ServerManager.Tools~~~~0.0.1.0",), (),
                note="Built into Windows Server."),
    Requirement("dedup", "Data Deduplication", "Get-DedupVolume", SERVER_ONLY, (),
                ("FS-Data-Deduplication",), note="A server role; it has no client tool."),
    Requirement("mpio", "Multipath I/O", "mpclaim.exe", SERVER_ONLY, (), ("Multipath-IO",),
                note="A server feature; it has no client tool."),
    Requirement("backup", "Windows Server Backup", "wbadmin.exe", SERVER_ONLY, (),
                ("Windows-Server-Backup",), note="A server feature; it has no client tool."),
)}

#: The guard cmdlet a script checks first -> the requirement that provides it.
_GUARD = re.compile(r"Get-Command (\S+) -ErrorAction SilentlyContinue")
_PROVIDES = (
    (re.compile(r"^(Get-AD|repadmin|dcdiag)", re.I), "ad"),
    (re.compile(r"^Get-GP", re.I), "gpo"),
    (re.compile(r"^Get-DnsServer", re.I), "dns"),
    (re.compile(r"^Get-Dhcp", re.I), "dhcp"),
    (re.compile(r"^Get-Cluster", re.I), "cluster"),
    (re.compile(r"^Get-(Fsrm|Dfsn|Dfsr)", re.I), "fileservices"),
    (re.compile(r"^Get-VM", re.I), "hyperv"),
    (re.compile(r"^Get-(Website|IIS)", re.I), "iis"),
    (re.compile(r"^Get-RD", re.I), "rds"),
    (re.compile(r"^Get-Wsus", re.I), "wsus"),
    (re.compile(r"^Get-WindowsFeature", re.I), "servermanager"),
    (re.compile(r"^Get-Dedup", re.I), "dedup"),
    (re.compile(r"^mpclaim", re.I), "mpio"),
    (re.compile(r"^wbadmin", re.I), "backup"),
)


def requirement_for(script: library.Script) -> Optional[Requirement]:
    """The module/tool a script needs, from the guard it starts with; None when
    it needs nothing Windows does not ship with."""
    for cmd in _GUARD.findall(script.body):
        for pattern, key in _PROVIDES:
            if pattern.match(cmd):
                return REQUIREMENTS[key]
    return None


def scripts_needing(scripts: Sequence[library.Script]) -> Dict[str, List[library.Script]]:
    out: Dict[str, List[library.Script]] = {}
    for s in scripts:
        r = requirement_for(s)
        if r is not None:
            out.setdefault(r.key, []).append(s)
    return out


# ---- status ----------------------------------------------------------------------------------

Run = Callable[[List[str]], Tuple[int, str]]


def _default_run(argv: List[str]) -> Tuple[int, str]:
    p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120, creationflags=library.CREATE_NO_WINDOW)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def detect(run: Optional[Run] = None) -> Tuple[Dict[str, bool], bool]:
    """({requirement key: installed}, is_server) -- one PowerShell call that
    asks whether each probe command exists. A key missing from the answer
    means "could not tell", never "not installed"."""
    probes = "; ".join(f"'{r.key}=' + [int][bool](Get-Command '{r.probe}' -ErrorAction "
                       "SilentlyContinue)" for r in REQUIREMENTS.values())
    script = probes + "; 'server=' + [int]((Get-CimInstance Win32_OperatingSystem).ProductType -ne 1)"
    try:
        rc, out = (run or _default_run)(["powershell.exe", "-NoProfile", "-NonInteractive",
                                         "-Command", script])
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("modules: detection failed: %s", e)
        return {}, False
    found: Dict[str, bool] = {}
    server = False
    for line in out.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or value not in ("0", "1"):
            continue
        if key == "server":
            server = value == "1"
        elif key in REQUIREMENTS:
            found[key] = value == "1"
    return found, server


# ---- installing ------------------------------------------------------------------------------

def install_command(req: Requirement, server: bool) -> Optional[str]:
    """The PowerShell that installs `req` the way Windows supports, or None when
    this kind of PC cannot have it (a server role on a client)."""
    if server:
        if not req.server_features:
            return None
        names = ", ".join(f"'{n}'" for n in req.server_features)
        return f"Install-WindowsFeature -Name {names} -IncludeManagementTools"
    if req.client_kind == CAPABILITY:
        return "; ".join(f"Add-WindowsCapability -Online -Name '{n}'" for n in req.client_names)
    if req.client_kind == OPTIONAL_FEATURE:
        names = ", ".join(f"'{n}'" for n in req.client_names)
        return f"Enable-WindowsOptionalFeature -Online -FeatureName {names} -All -NoRestart"
    return None


def why_not_installable(req: Requirement, server: bool) -> str:
    if server:
        return "Windows Server has no feature to install for this."
    return req.note or "This needs the server role itself; there is no client tool for it."


def elevated_install(command: str) -> Tuple[bool, str]:
    """Open an elevated PowerShell window (UAC prompt) that runs `command` and
    stays open, so the person sees the progress and the result. Returns
    (started, message)."""
    import ctypes
    wrapped = (f"& {{ {command}; ''; 'Finished. Go back to WinClientTool and press Reload "
               "to see the scripts that can now run.' }")
    params = f'-NoExit -NoProfile -ExecutionPolicy Bypass -Command "{wrapped}"'
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", params, None, 1)
    except (AttributeError, OSError) as e:
        return False, f"Could not start PowerShell: {e}"
    if rc <= 32:
        return False, ("The installation was not started (the administrator prompt was "
                       f"cancelled or refused, code {rc}).")
    return True, "An administrator PowerShell window is installing it; follow it there."
