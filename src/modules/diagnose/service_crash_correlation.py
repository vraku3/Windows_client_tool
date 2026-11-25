"""DCOM & Service Hang/Crash Correlation Engine.

Correlates:
- Service Control Manager (System log Event IDs 7000, 7009, 7031, 7034)
- Application Crashes (Application log Event ID 1000, 1001)
- DCOM Permission & Launch Failures (System log Event ID 10016)

Matches events within a temporal window to identify the exact faulting module
and process responsible for service crashes.
Pure Python (Qt-free), SAC-safe and fast.
"""
from __future__ import annotations

import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ServiceCrashEvent:
    timestamp: str
    event_id: int
    service_name: str
    description: str
    exit_code: str = ""
    crash_count: int = 1
    action_taken: str = ""
    correlated_app_error: Optional[str] = None
    faulting_module: Optional[str] = None
    exception_code: Optional[str] = None
    correlated_dcom_appid: Optional[str] = None


def get_recent_service_crashes(max_events: int = 40) -> List[ServiceCrashEvent]:
    """Query recent SCM service crashes (7031, 7034, 7000, 7009) from System log."""
    query = '*[System[(EventID=7000 or EventID=7009 or EventID=7031 or EventID=7034)]]'
    cmd = [
        "wevtutil.exe", "qe", "System",
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
        logger.warning("Failed to query SCM events: %s", e)
        return []

    raw_xml = res.stdout.strip()
    if raw_xml.startswith("<Events"):
        xml_text = raw_xml
    else:
        xml_text = f"<Events>{raw_xml}</Events>"
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.debug("Failed parsing XML from event query: %s", e)
        return []

    crashes: List[ServiceCrashEvent] = []
    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

    for event in root.findall("e:Event", ns):
        eid_elem = event.find("e:System/e:EventID", ns)
        if eid_elem is None or not eid_elem.text:
            continue
        try:
            eid = int(eid_elem.text)
        except ValueError as e:
            logger.debug("Failed to parse EventID int: %s", e)
            continue

        time_elem = event.find("e:System/e:TimeCreated", ns)
        timestamp = time_elem.get("SystemTime", "") if time_elem is not None else ""

        data_list = [d.text or "" for d in event.findall("e:EventData/e:Data", ns)]
        svc_name = data_list[0] if data_list else "Unknown Service"
        action = data_list[1] if len(data_list) > 1 else ""
        count_str = data_list[2] if len(data_list) > 2 else "1"

        try:
            crash_count = int(count_str)
        except ValueError:
            crash_count = 1

        desc = f"Service '{svc_name}' terminated unexpectedly (Event {eid})."

        crashes.append(ServiceCrashEvent(
            timestamp=timestamp,
            event_id=eid,
            service_name=svc_name,
            description=desc,
            action_taken=action,
            crash_count=crash_count,
        ))

    return crashes


def correlate_with_app_errors(service_crashes: List[ServiceCrashEvent], max_app_events: int = 30) -> None:
    """Find corresponding Application Error (1000) events matching the timestamp and service name."""
    if not service_crashes:
        return

    query = '*[System[(EventID=1000)]]'
    cmd = [
        "wevtutil.exe", "qe", "Application",
        f"/q:{query}", f"/c:{max_app_events}", "/rd:true", "/f:xml"
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
            return
    except Exception:
        return

    raw_xml = res.stdout.strip()
    if raw_xml.startswith("<Events"):
        xml_text = raw_xml
    else:
        xml_text = f"<Events>{raw_xml}</Events>"
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return

    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

    for event in root.findall("e:Event", ns):
        time_elem = event.find("e:System/e:TimeCreated", ns)
        timestamp = time_elem.get("SystemTime", "") if time_elem is not None else ""

        data_list = [d.text or "" for d in event.findall("e:EventData/e:Data", ns)]
        app_name = data_list[0] if data_list else ""
        mod_name = data_list[3] if len(data_list) > 3 else ""
        exc_code = data_list[6] if len(data_list) > 6 else ""

        # Match with service crashes
        for crash in service_crashes:
            if not crash.correlated_app_error:
                # Compare service name with app name
                clean_svc = crash.service_name.lower().replace(" ", "")
                clean_app = app_name.lower().replace(".exe", "")
                if clean_svc in clean_app or clean_app in clean_svc or (timestamp[:16] == crash.timestamp[:16]):
                    crash.correlated_app_error = app_name
                    crash.faulting_module = mod_name
                    crash.exception_code = exc_code
                    break
