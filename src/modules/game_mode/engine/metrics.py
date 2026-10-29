"""A small, failure-tolerant metrics snapshot for a Game Mode session."""
from __future__ import annotations

from .session import total_handles


def snapshot(system, psutil_module=None) -> dict:
    data = {"handles": None, "processes": None, "ram_percent": None,
            "ram_used_mb": None, "ram_total_mb": None, "cpu_percent": None}
    try:
        procs = system.processes()
        data["handles"] = total_handles(procs)
        data["processes"] = len(procs)
    except Exception:
        pass
    try:
        if psutil_module is None:
            import psutil as psutil_module
        memory = psutil_module.virtual_memory()
        data["ram_percent"] = float(memory.percent)
        data["ram_used_mb"] = round(float(memory.used) / (1024 * 1024), 1)
        data["ram_total_mb"] = round(float(memory.total) / (1024 * 1024), 1)
        data["cpu_percent"] = float(psutil_module.cpu_percent(interval=None))
    except Exception:
        pass
    return data
