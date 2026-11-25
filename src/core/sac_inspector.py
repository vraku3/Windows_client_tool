"""Smart App Control (SAC) and Windows Defender Application Control (WDAC) Inspector.

Provides pure Python (Qt-free) system queries to inspect:
1. SAC operational state (Enforced, Evaluation, Off) via Windows Registry.
2. PowerShell language mode (FullLanguage vs ConstrainedLanguage).
3. Active WDAC / SAC CodeIntegrity policy files (.cip).
4. Recent Code Integrity block events (Event IDs 3077, 3118) via wevtutil XML parsing.
5. Cloud trust evaluation via Defender MpCmdRun.exe -TrustCheck.
"""
from __future__ import annotations

import logging
import os
import subprocess
import winreg
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.windows_utils import program_files, system32

logger = logging.getLogger(__name__)

SAC_POLICY_GUID = "{0283ac0f-fff1-49ae-ada1-8a933130cad6}"
MPCMDRUN_PATH = os.path.join(program_files(), "Windows Defender", "MpCmdRun.exe")
CI_POLICIES_PATH = os.path.join(system32(), "CodeIntegrity", "CIPolicies", "Active")


@dataclass
class CIBlockEvent:
    event_id: int
    timestamp: str
    target_file: str
    process_name: str
    policy_name: str
    policy_guid: str
    sha256: str
    is_sac: bool
    status_code: str


def get_sac_state() -> Dict[str, Any]:
    """Read Smart App Control policy status from the system registry.

    VerifiedAndReputablePolicyState:
      0: Off
      1: Enforced (On)
      2: Evaluation
    """
    state_code = -1
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\CI\Policy",
            0,
            winreg.KEY_READ,
        ) as key:
            val, _ = winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")
            state_code = int(val)
    except OSError as e:
        logger.debug("Could not read VerifiedAndReputablePolicyState: %s", e)

    state_labels = {
        0: "Off",
        1: "Enforced (On)",
        2: "Evaluation",
    }
    label = state_labels.get(state_code, "Unknown" if state_code != -1 else "Not Configured")

    # Read PowerShell Language Mode
    ps_mode = "Unknown"
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "$ExecutionContext.SessionState.LanguageMode"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            ps_mode = proc.stdout.strip()
    except Exception as e:
        logger.debug("Failed to query PS LanguageMode: %s", e)

    # Active policies in System32
    active_policies: List[str] = []
    if os.path.exists(CI_POLICIES_PATH):
        try:
            for item in os.listdir(CI_POLICIES_PATH):
                if item.lower().endswith(".cip"):
                    active_policies.append(item)
        except OSError as e:
            logger.debug("Failed to list CI policies from %s: %s", CI_POLICIES_PATH, e)

    return {
        "state_code": state_code,
        "state_label": label,
        "is_enforced": state_code == 1,
        "is_eval": state_code == 2,
        "ps_language_mode": ps_mode,
        "active_policies": active_policies,
        "has_sac_policy": any(SAC_POLICY_GUID.strip("{}").lower() in p.lower() for p in active_policies),
    }


def get_recent_ci_blocks(max_events: int = 50) -> List[CIBlockEvent]:
    """Query recent 3077/3118 block events from Microsoft-Windows-CodeIntegrity/Operational."""
    query = '*[System[(EventID=3077 or EventID=3118)]]'
    cmd = [
        "wevtutil.exe", "qe", "Microsoft-Windows-CodeIntegrity/Operational",
        f"/q:{query}", f"/c:{max_events}", "/rd:true", "/f:xml"
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode != 0 or not res.stdout.strip():
            return []
    except Exception as e:
        logger.warning("Failed to query CI events: %s", e)
        return []

    xml_text = f"<Events>{res.stdout}</Events>"
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.debug("XML parse error on CI events: %s", e)
        return []

    blocks: List[CIBlockEvent] = []
    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

    for event in root.findall("e:Event", ns):
        eid_elem = event.find("e:System/e:EventID", ns)
        if eid_elem is None or not eid_elem.text:
            continue
        try:
            eid = int(eid_elem.text)
        except ValueError as e:
            logger.debug("Failed parsing EventID in CI event: %s", e)
            continue

        time_elem = event.find("e:System/e:TimeCreated", ns)
        timestamp = time_elem.get("SystemTime", "") if time_elem is not None else ""

        data_dict: Dict[str, str] = {}
        for d in event.findall("e:EventData/e:Data", ns):
            name = d.get("Name", "")
            if name:
                data_dict[name] = d.text or ""

        target = data_dict.get("File Name") or data_dict.get("FileNameBuffer") or ""
        proc_name = data_dict.get("Process Name") or ""
        pol_name = data_dict.get("PolicyName") or ""
        pol_guid = (data_dict.get("PolicyGUID") or "").lower()
        sha256 = data_dict.get("SHA256 Flat Hash") or data_dict.get("SHA256FlatHash") or ""
        status = data_dict.get("Status") or ""

        # Normalize NT device paths like \Device\HarddiskVolume3\...
        if target.startswith(r"\Device\HarddiskVolume"):
            parts = target.split("\\", 3)
            if len(parts) >= 4:
                # Map to relative or keep clean
                target_display = "\\" + parts[3]
            else:
                target_display = target
        else:
            target_display = target

        is_sac = (pol_guid == SAC_POLICY_GUID.lower() or "verifiedandreputable" in pol_name.lower())

        blocks.append(CIBlockEvent(
            event_id=eid,
            timestamp=timestamp,
            target_file=target_display,
            process_name=proc_name,
            policy_name=pol_name or ("Smart App Control" if is_sac else "WDAC"),
            policy_guid=pol_guid,
            sha256=sha256,
            is_sac=is_sac,
            status_code=status,
        ))

    return blocks


def check_file_trust(file_path: str) -> Tuple[bool, str]:
    """Run MpCmdRun -TrustCheck on a local file to determine Defender / SAC cloud trust."""
    if not os.path.isfile(file_path):
        return False, f"File does not exist: {file_path}"

    if not os.path.isfile(MPCMDRUN_PATH):
        return False, "MpCmdRun.exe not found at standard Defender path"

    try:
        res = subprocess.run(
            [MPCMDRUN_PATH, "-TrustCheck", "-File", file_path],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (res.stdout or "") + (res.stderr or "")
        output_lower = output.lower()

        if "file is trusted" in output_lower:
            return True, "File is Trusted by Smart App Control / Microsoft Cloud."
        elif "not a known good file" in output_lower:
            return False, "Untrusted / Unknown file (will be blocked if SAC is Enforced)."
        elif "threat" in output_lower:
            return False, "Defender detected a security threat in this file."
        else:
            return False, f"Trust status unconfirmed: {output.strip()}"
    except subprocess.TimeoutExpired:
        return False, "Cloud trust check timed out after 20 seconds."
    except Exception as e:
        return False, f"Error querying MpCmdRun: {e}"
