"""Read Windows Update service health without changing anything (Qt-free)."""
from __future__ import annotations

from typing import Callable, List, Optional

SERVICES = (
    ("wuauserv", "Windows Update"),
    ("bits", "Background Intelligent Transfer"),
    ("cryptsvc", "Cryptographic Services"),
    ("msiserver", "Windows Installer"),
    ("dosvc", "Delivery Optimization"),
    ("TrustedInstaller", "Windows Modules Installer"),
)

STATE_NAMES = {
    1: "Stopped", 2: "Start pending", 3: "Stop pending", 4: "Running",
    5: "Continue pending", 6: "Pause pending", 7: "Paused",
}
START_NAMES = {0: "Boot", 1: "System", 2: "Automatic", 3: "Manual", 4: "Disabled"}


def read_service(
    name: str,
    label: str,
    query_status: Optional[Callable] = None,
    query_config: Optional[Callable] = None,
) -> dict:
    """One service's state/start type, or an explicit unreadable reason."""
    try:
        if query_status is None or query_config is None:
            import win32serviceutil
            query_status = query_status or win32serviceutil.QueryServiceStatus
            query_config = query_config or win32serviceutil.QueryServiceConfig
    except Exception as exc:
        return {"service": name, "label": label, "state": "Unreadable",
                "start_type": "", "error": f"pywin32 unavailable: {exc}"}
    try:
        status = query_status(name)
        state_code = int(status[1])
        state = STATE_NAMES.get(state_code, f"State {state_code}")
    except Exception as exc:
        return {"service": name, "label": label, "state": "Unreadable",
                "start_type": "", "error": str(exc)}
    try:
        config = query_config(name)
        start_code = int(config[1])
        start_type = START_NAMES.get(start_code, f"Start type {start_code}")
    except Exception as exc:
        start_type = f"Unreadable ({exc})"
    return {"service": name, "label": label, "state": state,
            "start_type": start_type, "error": ""}


def read_all(
    query_status: Optional[Callable] = None,
    query_config: Optional[Callable] = None,
) -> List[dict]:
    return [read_service(name, label, query_status, query_config)
            for name, label in SERVICES]


def problems(rows: List[dict]) -> List[dict]:
    """Services that are not running or whose state could not be read."""
    return [row for row in rows
            if row.get("error") or row.get("state") != "Running"]


def summary(rows: List[dict]) -> str:
    bad = problems(rows)
    if not bad:
        return "All Windows Update services are running."
    return "; ".join(f"{row['label']}: {row['state']}"
                     + (f" ({row['error']})" if row.get("error") else "")
                     for row in bad)
