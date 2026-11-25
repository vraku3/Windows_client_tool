"""System Snapshot & Baseline Differential Engine ("Before & After").

Provides fast, lightweight system baseline capture and comparison across:
1. Windows Services (State, StartMode, BinaryPath)
2. Autostart Registry Entries (HKLM/HKCU Run and RunOnce)
3. Scheduled Tasks (State, Last Run, Next Run)
4. Kernel Drivers (Installed third-party driver packages)

Qt-free, headless, thread-safe, and fully testable.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import platform
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


@dataclass
class ServiceItem:
    name: str
    display_name: str
    state: str
    start_mode: str


@dataclass
class AutostartItem:
    hive: str
    path: str
    name: str
    command: str


@dataclass
class TaskItem:
    path: str
    state: str


@dataclass
class SystemSnapshot:
    label: str
    timestamp: str
    os_build: str
    computer_name: str
    services: Dict[str, Dict[str, str]] = field(default_factory=dict)
    autostarts: Dict[str, Dict[str, str]] = field(default_factory=dict)
    tasks: Dict[str, str] = field(default_factory=dict)
    drivers: List[str] = field(default_factory=list)


@dataclass
class SnapshotDiff:
    label_a: str
    label_b: str
    timestamp_a: str
    timestamp_b: str
    services_added: List[Dict[str, str]] = field(default_factory=list)
    services_removed: List[Dict[str, str]] = field(default_factory=list)
    services_modified: List[Dict[str, Any]] = field(default_factory=list)
    autostarts_added: List[Dict[str, str]] = field(default_factory=list)
    autostarts_removed: List[Dict[str, str]] = field(default_factory=list)
    autostarts_modified: List[Dict[str, Any]] = field(default_factory=list)
    tasks_added: List[str] = field(default_factory=list)
    tasks_removed: List[str] = field(default_factory=list)
    drivers_added: List[str] = field(default_factory=list)
    drivers_removed: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any([
            self.services_added, self.services_removed, self.services_modified,
            self.autostarts_added, self.autostarts_removed, self.autostarts_modified,
            self.tasks_added, self.tasks_removed,
            self.drivers_added, self.drivers_removed,
        ])


# ---------------------------------------------------------------------------
# Capture Functions
# ---------------------------------------------------------------------------

def capture_services() -> Dict[str, Dict[str, str]]:
    """Capture all Windows services state and start modes via sc.exe."""
    results: Dict[str, Dict[str, str]] = {}
    try:
        proc = subprocess.run(
            ["sc.exe", "queryex", "type=", "service", "state=", "all"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        current_name = ""
        current_display = ""
        current_state = ""

        for line in proc.stdout.splitlines():
            s = line.strip()
            if s.startswith("SERVICE_NAME:"):
                if current_name:
                    results[current_name.lower()] = {
                        "name": current_name,
                        "display": current_display,
                        "state": current_state,
                    }
                current_name = s.split(":", 1)[1].strip()
                current_display = ""
                current_state = ""
            elif s.startswith("DISPLAY_NAME:"):
                current_display = s.split(":", 1)[1].strip()
            elif s.startswith("STATE"):
                # e.g. STATE : 4 RUNNING
                parts = s.split(":", 1)[1].strip().split()
                if len(parts) >= 2:
                    current_state = parts[1]

        if current_name:
            results[current_name.lower()] = {
                "name": current_name,
                "display": current_display,
                "state": current_state,
            }
    except Exception as e:
        logger.warning("capture_services error: %s", e)

    return results


def capture_autostarts() -> Dict[str, Dict[str, str]]:
    """Capture Run and RunOnce autostart keys in HKLM and HKCU."""
    results: Dict[str, Dict[str, str]] = {}
    try:
        import winreg

        targets = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "HKCU"),
        ]

        for hkey, subkey, hive in targets:
            try:
                with winreg.OpenKey(hkey, subkey, 0, winreg.KEY_READ) as k:
                    i = 0
                    while True:
                        try:
                            name, val, _ = winreg.EnumValue(k, i)
                            key_id = f"{hive}\\{subkey}\\{name}".lower()
                            results[key_id] = {
                                "hive": hive,
                                "path": subkey,
                                "name": name,
                                "command": str(val),
                            }
                            i += 1
                        except OSError as e:
                            logger.debug("End of registry enum at %s index %d: %s", subkey, i, e)
                            break
            except OSError as e:
                logger.debug("Registry key %s\\%s not accessible: %s", hive, subkey, e)
                continue
    except Exception as e:
        logger.warning("capture_autostarts error: %s", e)

    return results


def capture_scheduled_tasks() -> Dict[str, str]:
    """Capture scheduled tasks state via schtasks /query /fo csv."""
    results: Dict[str, str] = {}
    try:
        proc = subprocess.run(
            ["schtasks.exe", "/Query", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
        reader = csv.reader(io.StringIO(proc.stdout))
        for row in reader:
            if row and len(row) >= 3:
                task_name = row[0].strip()
                state = row[2].strip() if len(row) > 2 else "Unknown"
                if task_name:
                    results[task_name.lower()] = state
    except Exception as e:
        logger.warning("capture_scheduled_tasks error: %s", e)

    return results


def capture_drivers() -> List[str]:
    """Capture third-party driver packages via pnputil."""
    results: List[str] = []
    try:
        proc = subprocess.run(
            ["pnputil.exe", "/enum-drivers"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in proc.stdout.splitlines():
            s = line.strip()
            if s.startswith("Published Name:") or s.startswith("Nume publicat:"):
                name = s.split(":", 1)[1].strip()
                if name:
                    results.append(name.lower())
    except Exception as e:
        logger.warning("capture_drivers error: %s", e)

    return sorted(list(set(results)))


def take_snapshot(label: str = "System Baseline") -> SystemSnapshot:
    """Take a full machine state snapshot."""
    return SystemSnapshot(
        label=label or f"Snapshot_{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        timestamp=datetime.now().isoformat(),
        os_build=platform.version(),
        computer_name=platform.node(),
        services=capture_services(),
        autostarts=capture_autostarts(),
        tasks=capture_scheduled_tasks(),
        drivers=capture_drivers(),
    )


# ---------------------------------------------------------------------------
# Comparison Engine
# ---------------------------------------------------------------------------

def compare_snapshots(snap_a: SystemSnapshot, snap_b: SystemSnapshot) -> SnapshotDiff:
    """Compare baseline Snapshot A against Snapshot B and compute the delta."""
    diff = SnapshotDiff(
        label_a=snap_a.label,
        label_b=snap_b.label,
        timestamp_a=snap_a.timestamp,
        timestamp_b=snap_b.timestamp,
    )

    # 1. Compare Services
    keys_a = set(snap_a.services.keys())
    keys_b = set(snap_b.services.keys())

    for k in (keys_b - keys_a):
        diff.services_added.append(snap_b.services[k])

    for k in (keys_a - keys_b):
        diff.services_removed.append(snap_a.services[k])

    for k in (keys_a & keys_b):
        svc_a = snap_a.services[k]
        svc_b = snap_b.services[k]
        if svc_a.get("state") != svc_b.get("state"):
            diff.services_modified.append({
                "name": svc_b.get("name", k),
                "display": svc_b.get("display", ""),
                "old_state": svc_a.get("state", ""),
                "new_state": svc_b.get("state", ""),
            })

    # 2. Compare Autostarts
    auto_a = set(snap_a.autostarts.keys())
    auto_b = set(snap_b.autostarts.keys())

    for k in (auto_b - auto_a):
        diff.autostarts_added.append(snap_b.autostarts[k])

    for k in (auto_a - auto_b):
        diff.autostarts_removed.append(snap_a.autostarts[k])

    for k in (auto_a & auto_b):
        cmd_a = snap_a.autostarts[k].get("command", "")
        cmd_b = snap_b.autostarts[k].get("command", "")
        if cmd_a != cmd_b:
            diff.autostarts_modified.append({
                "name": snap_b.autostarts[k].get("name", ""),
                "path": snap_b.autostarts[k].get("path", ""),
                "old_cmd": cmd_a,
                "new_cmd": cmd_b,
            })

    # 3. Compare Tasks
    task_a = set(snap_a.tasks.keys())
    task_b = set(snap_b.tasks.keys())
    diff.tasks_added = sorted(list(task_b - task_a))
    diff.tasks_removed = sorted(list(task_a - task_b))

    # 4. Compare Drivers
    drv_a = set(snap_a.drivers)
    drv_b = set(snap_b.drivers)
    diff.drivers_added = sorted(list(drv_b - drv_a))
    diff.drivers_removed = sorted(list(drv_a - drv_b))

    return diff


def format_diff_markdown(diff: SnapshotDiff) -> str:
    """Format a SnapshotDiff into a clean GitHub-style Markdown report."""
    lines = [
        f"# System Baseline Differential Report",
        f"**Baseline (A):** {diff.label_a} ({diff.timestamp_a})",
        f"**Comparison (B):** {diff.label_b} ({diff.timestamp_b})",
        "",
    ]

    if not diff.has_changes:
        lines.append("> [!NOTE] Zero changes detected between the two system snapshots.")
        return "\n".join(lines)

    # Services
    lines.append(f"## 1. Services Changes")
    if diff.services_added:
        lines.append(f"### Added Services ({len(diff.services_added)}):")
        for s in diff.services_added:
            lines.append(f"- **{s.get('name')}** ({s.get('display')}): State `{s.get('state')}`")
    if diff.services_removed:
        lines.append(f"### Removed Services ({len(diff.services_removed)}):")
        for s in diff.services_removed:
            lines.append(f"- **{s.get('name')}** ({s.get('display')})")
    if diff.services_modified:
        lines.append(f"### State Changes ({len(diff.services_modified)}):")
        for s in diff.services_modified:
            lines.append(f"- **{s.get('name')}**: `{s.get('old_state')}` ➔ `{s.get('new_state')}`")
    if not (diff.services_added or diff.services_removed or diff.services_modified):
        lines.append("No service changes.")

    # Autostarts
    lines.append("")
    lines.append("## 2. Autostart Entries (Run/RunOnce)")
    if diff.autostarts_added:
        lines.append(f"### Added Autostart Entries ({len(diff.autostarts_added)}):")
        for a in diff.autostarts_added:
            lines.append(f"- **{a.get('name')}** ({a.get('hive')}\\{a.get('path')}): `{a.get('command')}`")
    if diff.autostarts_removed:
        lines.append(f"### Removed Autostart Entries ({len(diff.autostarts_removed)}):")
        for a in diff.autostarts_removed:
            lines.append(f"- **{a.get('name')}**")
    if not (diff.autostarts_added or diff.autostarts_removed):
        lines.append("No autostart changes.")

    # Tasks
    lines.append("")
    lines.append("## 3. Scheduled Tasks")
    if diff.tasks_added:
        lines.append(f"### Added Tasks ({len(diff.tasks_added)}):")
        for t in diff.tasks_added:
            lines.append(f"- `{t}`")
    if diff.tasks_removed:
        lines.append(f"### Removed Tasks ({len(diff.tasks_removed)}):")
        for t in diff.tasks_removed:
            lines.append(f"- `{t}`")
    if not (diff.tasks_added or diff.tasks_removed):
        lines.append("No scheduled task changes.")

    # Drivers
    lines.append("")
    lines.append("## 4. Driver Packages")
    if diff.drivers_added:
        lines.append(f"### Added Driver Packages ({len(diff.drivers_added)}):")
        for d in diff.drivers_added:
            lines.append(f"- `{d}`")
    if diff.drivers_removed:
        lines.append(f"### Removed Driver Packages ({len(diff.drivers_removed)}):")
        for d in diff.drivers_removed:
            lines.append(f"- `{d}`")
    if not (diff.drivers_added or diff.drivers_removed):
        lines.append("No driver package changes.")

    return "\n".join(lines)
