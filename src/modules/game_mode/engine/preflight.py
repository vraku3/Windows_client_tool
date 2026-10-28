from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

from . import catalog
from .system import NORMAL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreflightItem:
    category: str
    name: str
    action: str
    status: str
    detail: str
    requires_admin: bool = False


def preflight(system, choices, *, own_pid=None, session_id=None) -> list[PreflightItem]:
    from . import session as engine
    own_pid = os.getpid() if own_pid is None else own_pid
    if session_id is None:
        sid = ctypes.c_uint()
        if not ctypes.windll.kernel32.ProcessIdToSessionId(own_pid, ctypes.byref(sid)):
            raise OSError("Cannot read the current Windows session")
        session_id = sid.value
    admin = system.is_admin()
    items = []
    service = lru_cache(None)(system.service)

    def add(category, name, action, check, requires_admin=False):
        try:
            status, detail = check()
        except Exception as error:
            logger.warning("Game Mode preflight: %s: %s", action, error)
            status, detail = "unknown", f"Could not check: {error}"
        items.append(PreflightItem(category, name, action, status, detail,
                                   requires_admin or status == "needs_admin"))

    def ready(detail, elevated=False):
        if elevated and not admin:
            return "needs_admin", f"{detail}; requires administrator, will be skipped"
        return "ready", detail

    if choices.plan:
        def power():
            plans = system.plans()
            target, why = engine.recommended_plan(system.cpu_name(), plans,
                                                  service(catalog.VCACHE_SERVICE) is not None)
            current = system.active_plan()
            if target is None:
                return "not_found", why
            if current is None:
                return "unknown", "Active power plan unreadable"
            return ("already_done", f"Already {plans[target]}") if current == target else ready(
                f"{plans.get(current, current)} -> {plans[target]}; {why}")
        add("power", "Gaming power plan", "Switch to the recommended power plan", power)

    def registry(hive, key, name, value, label):
        def check():
            exists, current, _ = system.reg_get(key, name, hive)
            if exists is None:
                return "unknown", "Registry value unreadable; will be skipped"
            if exists and current == value:
                return "already_done", f"{hive} {name} already {value}"
            return ready(f"{hive} {name}: {current if exists else 'absent'} -> {value}", hive == "HKLM")
        add("registry", label, f"Set {name} to {value}", check, hive == "HKLM")

    if choices.gamebar:
        for name in ("AutoGameModeEnabled", "AllowAutoGameMode"):
            registry("HKCU", engine.GAMEBAR_KEY, name, 1, f"Windows Game Mode: {name}")
    if choices.maintenance:
        registry("HKLM", engine.MAINTENANCE_KEY, "MaintenanceDisabled", 1, "Automatic Maintenance")
    if choices.visuals:
        registry("HKCU", engine.THEMES_KEY, "EnableTransparency", 0, "Transparency effects")
        def animations():
            current = system.animations()
            if current is None:
                return "unknown", "Window animation setting unreadable; will be skipped"
            return ready("Currently enabled") if current else ("already_done", "Already disabled")
        add("registry", "Window animations", "Disable window animations", animations)

    def service_check(name, start=False, spec=None):
        state = service(name)
        if state is None:
            return "not_found", "Not installed"
        if state.running is None:
            return "unknown", state.error or "Service state unreadable"
        if state.running == start:
            return "already_done", "Already running" if start else "Already stopped"
        if start and state.start_type == "Disabled":
            return "protected", "Service is disabled; start type will not be changed"
        if spec and spec.when == "no_wifi":
            wifi = system.wifi_connected()
            if wifi is not False:
                return ("unknown", "Wi-Fi state unreadable; will be skipped") if wifi is None else (
                    "protected", "Wi-Fi is in use; will be skipped")
        if not admin:
            return ready("Currently stopped" if start else "Currently running", True)
        if not start:
            dependents = system.running_dependents(name)
            if dependents:
                return "protected", f"Running dependents: {', '.join(dependents)}; will be skipped"
        return ready("Currently stopped" if start else "Currently running", True)

    if choices.vcache:
        add("service", "AMD 3D V-Cache optimizer", f"Start {catalog.VCACHE_SERVICE}",
            lambda: service_check(catalog.VCACHE_SERVICE, start=True), True)
    for spec in catalog.SERVICES:
        if spec.name not in choices.services:
            continue
        try:
            names = system.service_names(spec.name) if "*" in spec.name else [spec.name]
        except Exception as error:
            logger.warning("Game Mode preflight: service inventory: %s", error)
            items.append(PreflightItem("service", spec.label, f"Stop {spec.name}", "unknown", str(error), True))
            continue
        for name in names or [spec.name]:
            add("service", spec.label, f"Stop {name}", lambda n=name, s=spec: service_check(n, spec=s), True)

    if choices.tasks:
        wanted = choices.paused_tasks
        if choices.preset in (catalog.REC, catalog.MAX):
            levels = {catalog.REC, catalog.MAX} if choices.preset == catalog.MAX else {catalog.REC}
            wanted = {t.pattern for t in catalog.TASKS if t.level in levels}
        try:
            tasks, task_error = system.tasks(), None
        except Exception as error:
            logger.warning("Game Mode preflight: task inventory: %s", error)
            tasks, task_error = [], str(error)
        for spec in catalog.TASKS:
            if spec.pattern not in wanted:
                continue
            matches = [(p, e) for p, e in tasks if catalog.task_spec(p) == spec]
            if not matches:
                items.append(PreflightItem("task", spec.label, f"Pause {spec.pattern}",
                                          "unknown" if task_error else "not_found",
                                          task_error or "Not installed", True))
            for path, enabled in matches:
                detail = f"Enabled: {path}"
                if choices.preset == catalog.MAX:
                    detail += "; any current run will also be stopped"
                status, detail = ready(detail, True) if enabled else ("already_done", f"Already disabled: {path}")
                items.append(PreflightItem("task", spec.label, f"Pause {path}", status, detail, True))

    try:
        procs = system.processes()
        details = lru_cache(None)(system.details)
        groups = engine.running_apps(procs, session_id, own_pid, lambda pid: details(pid)[0],
                                     system.fullscreen_pids())
        app_error = None
    except Exception as error:
        logger.warning("Game Mode preflight: process inventory: %s", error)
        procs, groups, app_error = [], [], str(error)
    by_name = {g.exe: g for g in groups}
    for exe in sorted(choices.close):
        spec = catalog.spec_for(exe)
        def app_check():
            if catalog.never_close(exe) and exe not in by_name:
                return "protected", "Protected program; will never be closed"
            if app_error:
                return "unknown", app_error
            current = [p for p in procs if p.name.lower() == exe and p.session == session_id]
            if not current:
                return "already_done", "Not running in this Windows session"
            group = by_name.get(exe)
            if group is None:
                if any(details(p.pid)[0] is None for p in current):
                    return "unknown", "Process path unreadable; will be skipped"
                return "protected", "Game, foreground window, shell, or helper of another program; will be skipped"
            if group.spec and group.spec.when == "spareprocess":
                if (choices.preset != catalog.MAX
                        or any("spareprocess" not in (details(p.pid)[1] or "").lower() for p in group.procs)
                        or system.has_visible_window([p.pid for p in group.procs]) is not False):
                    return "protected", "Not a hidden spare process in Maximum; will be skipped"
            elevations = [details(p.pid)[2] for p in group.procs]
            if not admin and any(e is True for e in elevations):
                return ready("Program is elevated", True)
            if not admin and any(e is None for e in elevations):
                return "unknown", "Process privilege unreadable; closing may be refused"
            return ready(f"{len(group.procs)} process(es) running; close after confirmation")
        add("app", spec.label if spec else exe, f"Close {exe}", app_check)
    if choices.lower:
        if app_error:
            items.append(PreflightItem("priority", "Background app priorities", "Lower Normal priorities",
                                      "unknown", app_error))
        for group in groups:
            if group.exe in choices.close or not (group.spec and group.spec.lower):
                continue
            for proc in group.procs:
                def priority_check():
                    current = system.priority(proc.pid)
                    if current is None:
                        return "unknown", "Priority unreadable; will be skipped"
                    if current != NORMAL:
                        return "already_done", "Only Normal priority is lowered; will be skipped"
                    elevated = details(proc.pid)[2]
                    if elevated is None and not admin:
                        return "unknown", "Process privilege unreadable"
                    return ready("Currently Normal", elevated is True)
                add("priority", group.label, f"Lower {group.exe} (PID {proc.pid}) to Below Normal", priority_check)
    if choices.explorer:
        items.append(PreflightItem("app", "Windows Explorer", "Restart Explorer", "ready",
                                  "Interrupts File Explorer copies and closes folder windows"))
    return items
