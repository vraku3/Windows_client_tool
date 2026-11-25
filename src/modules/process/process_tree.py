"""Live Process Tree & Anomaly Hunter Engine.

Builds a process forest (Parent -> Children) and evaluates MITRE ATT&CK heuristics:
1. Office apps or Browsers spawning execution shells (cmd, powershell, mshta, cscript).
2. Fake/Masquerading system binaries (e.g. svchost.exe outside System32).
3. Processes executing out of temporary or world-writable directories (Temp, Users\\Public).
4. Obfuscated / Encoded command lines (-enc, DownloadString, IEX).

Qt-free, thread-safe, and testable.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import psutil

logger = logging.getLogger(__name__)

# Suspicious parent processes
OFFICE_PROCS = {"winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe"}
BROWSER_PROCS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
PDF_PROCS = {"acrord32.exe", "acrobat.exe", "foxitreader.exe"}

# Execution shells and script engines
SHELL_PROCS = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe",
    "mshta.exe", "certutil.exe", "rundll32.exe", "regsvr32.exe", "bitsadmin.exe",
}

# Windows Core System Binaries expected exclusively in System32
SYSTEM32_EXCLUSIVES = {
    "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
    "lsass.exe", "svchost.exe", "winlogon.exe",
}


@dataclass
class ProcessNode:
    pid: int
    ppid: int
    name: str
    exe: str = ""
    cmdline: str = ""
    username: str = ""
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    parent_name: str = ""
    children: List[ProcessNode] = field(default_factory=list)
    anomalies: List[str] = field(default_factory=list)
    risk_level: str = "CLEAN"  # "CRITICAL", "HIGH", "MEDIUM", "LOW", "CLEAN"

    @property
    def has_anomalies(self) -> bool:
        return len(self.anomalies) > 0


def _get_system32_path() -> str:
    from core.windows_utils import system32
    return system32().lower()


def evaluate_anomalies(
    name: str,
    exe: str,
    cmdline: str,
    parent_name: str,
    system32_dir: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Evaluate MITRE heuristics on a process and return (risk_level, list_of_anomalies)."""
    anomalies: List[str] = []
    name_low = name.lower()
    exe_low = exe.lower()
    cmd_low = cmdline.lower()
    parent_low = parent_name.lower()
    sys32 = (system32_dir or _get_system32_path()).lower()

    # 1. Office app spawning a shell or script runner
    if parent_low in OFFICE_PROCS and name_low in SHELL_PROCS:
        anomalies.append(f"CRITICAL: Office parent '{parent_name}' spawned shell binary '{name}' (Macro/Exploit indicator).")

    # 2. Browser spawning a shell
    if parent_low in BROWSER_PROCS and name_low in SHELL_PROCS:
        anomalies.append(f"HIGH: Web browser '{parent_name}' spawned execution shell '{name}' (Drive-by download / RCE indicator).")

    # 3. PDF reader spawning a shell
    if parent_low in PDF_PROCS and name_low in SHELL_PROCS:
        anomalies.append(f"HIGH: Document reader '{parent_name}' spawned execution shell '{name}'.")

    # 4. Masquerading Windows System Executables outside System32
    if name_low in SYSTEM32_EXCLUSIVES and exe_low:
        exe_dir = os.path.dirname(exe_low)
        if exe_dir != sys32:
            anomalies.append(f"CRITICAL: System binary '{name}' running outside System32 (Location: '{exe}').")

    # 5. Execution out of temporary or world-writable directories
    temp_indicators = ["\\temp\\", "\\appdata\\local\\temp\\", "\\users\\public\\"]
    for ti in temp_indicators:
        if ti in exe_low:
            anomalies.append(f"HIGH: Process executing out of suspicious/temporary directory ('{exe}').")
            break

    # 6. Encoded or obfuscated PowerShell execution
    if "powershell" in name_low or "pwsh" in name_low:
        if re.search(r"(-enc|-encodedcommand|-e\s)", cmd_low):
            anomalies.append("MEDIUM: PowerShell executing with Base64 EncodedCommand.")
        if "downloadstring" in cmd_low or "iex" in cmd_low:
            anomalies.append("HIGH: In-memory remote payload download execution (IEX/DownloadString).")

    # Determine risk level
    if any(a.startswith("CRITICAL") for a in anomalies):
        risk = "CRITICAL"
    elif any(a.startswith("HIGH") for a in anomalies):
        risk = "HIGH"
    elif any(a.startswith("MEDIUM") for a in anomalies):
        risk = "MEDIUM"
    elif anomalies:
        risk = "LOW"
    else:
        risk = "CLEAN"

    return risk, anomalies


def build_process_tree() -> Tuple[List[ProcessNode], List[ProcessNode]]:
    """Scan all running processes and construct a parent-child forest.

    Returns:
        (roots, anomalous_procs)
    """
    node_map: Dict[int, ProcessNode] = {}
    proc_dict: Dict[int, dict] = {}
    sys32 = _get_system32_path()

    # First pass: collect raw process attributes
    for p in psutil.process_iter(attrs=["pid", "ppid", "name", "exe", "cmdline", "username"]):
        try:
            info = p.info
            pid = info["pid"]
            name = info.get("name") or "Unknown"
            exe = info.get("exe") or ""
            cmdline = " ".join(info.get("cmdline") or [])
            username = info.get("username") or ""

            proc_dict[pid] = {
                "pid": pid,
                "ppid": info.get("ppid") or 0,
                "name": name,
                "exe": exe,
                "cmdline": cmdline,
                "username": username,
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.debug("Process %s skipped: %s", getattr(p, "pid", "?"), e)
            continue

    # Second pass: evaluate anomalies and build ProcessNodes
    anomalous_nodes: List[ProcessNode] = []

    for pid, data in proc_dict.items():
        ppid = data["ppid"]
        parent_name = proc_dict.get(ppid, {}).get("name", "")

        risk, anomalies = evaluate_anomalies(
            name=data["name"],
            exe=data["exe"],
            cmdline=data["cmdline"],
            parent_name=parent_name,
            system32_dir=sys32,
        )

        node = ProcessNode(
            pid=pid,
            ppid=ppid,
            name=data["name"],
            exe=data["exe"],
            cmdline=data["cmdline"],
            username=data["username"],
            parent_name=parent_name,
            anomalies=anomalies,
            risk_level=risk,
        )
        node_map[pid] = node
        if node.has_anomalies:
            anomalous_nodes.append(node)

    # Third pass: stitch parent -> child hierarchy
    roots: List[ProcessNode] = []
    for pid, node in node_map.items():
        ppid = node.ppid
        if ppid in node_map and ppid != pid:
            node_map[ppid].children.append(node)
        else:
            roots.append(node)

    # Sort anomalous nodes by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "CLEAN": 4}
    anomalous_nodes.sort(key=lambda n: (severity_order.get(n.risk_level, 5), n.name.lower()))

    return roots, anomalous_nodes
