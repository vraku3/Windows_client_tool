"""Game Mode on and off. Qt-free; every Windows call goes through `system`.

ON, in this order, each step journaled before it is made:
  1. the power plan the CPU wants for games (see `recommended_plan`),
  2. Windows' own Game Mode setting on,
  3. the AMD 3D V-Cache optimizer service started, if it is stopped,
  4. the ticked background services STOPPED (start types never touched),
  5. the ticked background apps closed (politely, then ended),
  6. the apps left open lowered to Below Normal priority.
OFF walks the journal backwards and puts each thing back exactly as it was,
then reads it back. "Back" means the recorded BEFORE state, nothing else: a
service that was already stopped is not started, an app that is already
running again is not opened twice, a priority someone changed since is left
alone.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import catalog
from .journal import Journal
from .system import BELOW_NORMAL, NORMAL, PRIORITY_NAMES, Proc, split_command_line

logger = logging.getLogger(__name__)

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
GAMEBAR_KEY = r"Software\Microsoft\GameBar"
REG_DWORD = 4

OK, INFO, WARN, BAD = "ok", "info", "warn", "bad"

#: Dual-CCD Ryzen with 3D V-Cache on ONE of the two CCDs. AMD's guidance for
#: these is the Balanced plan, so Windows can park the cores without the cache
#: while a game runs; High/Ultimate Performance keep every core awake and
#: let the game land on the wrong CCD. Single-CCD X3D parts (Ryzen 7) are not
#: affected.
_DUAL_CCD_X3D = re.compile(r"Ryzen 9\s+\d{4}X3D", re.IGNORECASE)

Log = Callable[[str, str], None]


def dual_ccd_x3d(cpu: str) -> bool:
    return bool(_DUAL_CCD_X3D.search(cpu or ""))


def recommended_plan(cpu: str, plans: Dict[str, str], vcache_installed: bool
                     ) -> Tuple[Optional[str], str]:
    """(plan guid or None, why)."""
    if dual_ccd_x3d(cpu) and vcache_installed:
        if BALANCED in plans:
            return BALANCED, (f"{cpu.strip()} has 3D V-Cache on one of its two CCDs; AMD's "
                              "optimizer needs the Balanced plan to keep games on that CCD")
        return None, "Balanced plan not found -- the V-Cache optimizer needs it"
    ultimate = [g for g, n in plans.items() if "ultimate" in n.lower()]
    if ultimate:
        return ultimate[0], "Ultimate Performance keeps every core at full clock"
    if HIGH_PERFORMANCE in plans:
        return HIGH_PERFORMANCE, "High performance keeps the cores at full clock"
    return None, "no performance plan on this PC"


@dataclass
class Choices:
    plan: bool = True
    gamebar: bool = True
    vcache: bool = True
    services: Set[str] = field(default_factory=lambda: {s.name for s in catalog.SERVICES
                                                         if s.default})
    close: Set[str] = field(default_factory=lambda: {a.exe for a in catalog.APPS if a.close})
    lower: bool = True

    def to_config(self) -> dict:
        return {"plan": self.plan, "gamebar": self.gamebar, "vcache": self.vcache,
                "services": sorted(self.services), "close": sorted(self.close),
                "lower": self.lower}


def choices_from_config(data) -> Choices:
    c = Choices()
    if not isinstance(data, dict):
        return c
    for flag in ("plan", "gamebar", "vcache", "lower"):
        if isinstance(data.get(flag), bool):
            setattr(c, flag, data[flag])
    if isinstance(data.get("services"), list):
        c.services = {s for s in data["services"] if isinstance(s, str)}
    if isinstance(data.get("close"), list):
        c.close = {s.lower() for s in data["close"] if isinstance(s, str)}
    return c


@dataclass
class AppGroup:
    """Every process of one program in the signed-in session."""
    exe: str
    label: str
    spec: Optional[catalog.AppSpec]
    procs: List[Proc]
    roots: List[Proc]

    @property
    def handles(self) -> int:
        return sum(p.handles for p in self.procs)


def running_apps(procs: List[Proc], session: int, own_pid: int,
                 path_of: Callable[[int], Optional[str]]) -> List[AppGroup]:
    """Programs in `session` that Game Mode could close, biggest first.
    Windows' own (anything under %SystemRoot%) and NEVER_CLOSE are left out."""
    windir = os.path.normcase(os.environ.get("SystemRoot") or os.environ.get("windir") or "\\Windows")
    by_name: Dict[str, List[Proc]] = {}
    for p in procs:
        if p.session == session and p.pid != own_pid and p.session != 0:
            by_name.setdefault(p.name.lower(), []).append(p)
    pids_by_name = {n: {p.pid for p in ps} for n, ps in by_name.items()}
    groups = []
    for name, ps in by_name.items():
        if name in catalog.NEVER_CLOSE or any(p.pid == own_pid for p in ps):
            continue
        roots = [p for p in ps if p.ppid not in pids_by_name[name]] or ps[:1]
        path = path_of(roots[0].pid) or ""
        if not path or os.path.normcase(path).startswith(windir):
            continue
        spec = catalog.spec_for(name)
        groups.append(AppGroup(name, spec.label if spec else os.path.basename(path), spec,
                               ps, roots))
    return sorted(groups, key=lambda g: -g.handles)


def total_handles(procs: List[Proc]) -> int:
    return sum(p.handles for p in procs)


# ==== ON ========================================================================================
class _On:
    def __init__(self, system, journal: Journal, log: Log) -> None:
        self.s, self.j, self.log = system, journal, log

    def plan(self, cpu: str) -> None:
        plans = self.s.plans()
        target, why = recommended_plan(cpu, plans, self.s.service(catalog.VCACHE_SERVICE) is not None)
        before = self.s.active_plan()
        if target is None or before is None:
            self.log(INFO, f"Power plan left alone: {why if target is None else 'active plan unreadable'}")
            return
        if before == target:
            self.log(OK, f"Power plan already {plans.get(target, target)} ({why})")
            return
        entry = self.j.record({"kind": "plan", "before": before, "before_name": plans.get(before, ""),
                               "after": target})
        error = self.s.set_plan(target)
        if error:
            self.log(BAD, f"Power plan not changed: {error}")
            return
        self.j.mark_done(entry)
        self.log(OK, f"Power plan {plans.get(before, before)} -> {plans.get(target, target)} ({why})")

    def gamebar(self) -> None:
        changed = 0
        for name in ("AutoGameModeEnabled", "AllowAutoGameMode"):
            exists, value, kind = self.s.reg_get(GAMEBAR_KEY, name)
            if exists is None:
                self.log(WARN, f"Game Mode setting {name} unreadable -- left alone")
                continue
            if exists and value == 1:
                continue
            entry = self.j.record({"kind": "registry", "path": GAMEBAR_KEY, "name": name,
                                   "existed": exists, "value": value, "type": kind})
            error = self.s.reg_set(GAMEBAR_KEY, name, 1, REG_DWORD)
            if error:
                self.log(BAD, f"Windows Game Mode setting {name} not set: {error}")
                continue
            self.j.mark_done(entry)
            changed += 1
            self.log(OK, f"Windows Game Mode setting {name} turned on")
        if not changed:
            self.log(OK, "Windows Game Mode setting was already on")

    def vcache(self, elevated: bool) -> None:
        state = self.s.service(catalog.VCACHE_SERVICE)
        if state is None or state.running:
            return
        if not elevated:
            self.log(WARN, "AMD 3D V-Cache optimizer is stopped; starting it needs administrator")
            return
        entry = self.j.record({"kind": "service_start", "name": catalog.VCACHE_SERVICE})
        error = self.s.start_service(catalog.VCACHE_SERVICE)
        if error:
            self.log(BAD, f"AMD 3D V-Cache optimizer not started: {error}")
            return
        self.j.mark_done(entry)
        self.log(OK, "AMD 3D V-Cache optimizer service started")

    def services(self, names: Set[str], elevated: bool) -> None:
        for spec in catalog.SERVICES:
            if spec.name not in names:
                continue
            state = self.s.service(spec.name)
            if state is None or state.running is False:
                continue
            if state.running is None:
                self.log(WARN, f"{spec.label}: state unreadable ({state.error}) -- left alone")
                continue
            if not elevated:
                self.log(WARN, f"{spec.label}: running; stopping it needs administrator")
                continue
            deps = self.s.running_dependents(spec.name)
            if deps:
                self.log(INFO, f"{spec.label}: left running, {', '.join(deps)} depends on it")
                continue
            entry = self.j.record({"kind": "service_stop", "name": spec.name, "label": spec.label,
                                   "start_type": state.start_type})
            error = self.s.stop_service(spec.name)
            if error:
                self.log(BAD, f"{spec.label}: not stopped ({error})")
                continue
            self.j.mark_done(entry)
            self.log(OK, f"{spec.label}: stopped")

    def close_apps(self, groups: List[AppGroup], wanted: Set[str]) -> None:
        for g in groups:
            if g.exe not in wanted:
                continue
            path, cmdline, elevated = self.s.details(g.roots[0].pid)
            program, args = split_command_line(cmdline or "")
            reopen = g.spec.reopen if g.spec else True
            entry = self.j.record({
                "kind": "app_close", "exe": g.exe, "label": g.label, "path": path or program,
                "args": args, "cwd": os.path.dirname(path or program), "aumid": self.s.aumid(g.roots[0].pid),
                "reopen": reopen and elevated is not True, "was_elevated": elevated,
                "handles": g.handles, "processes": len(g.procs)})
            error = self.s.close([p.pid for p in g.procs], path)
            if error:
                self.log(BAD, f"{g.label}: not closed ({error})")
                continue
            self.j.mark_done(entry)
            self.log(OK, f"{g.label}: closed ({len(g.procs)} process(es), {g.handles:,} handles)")

    def lower(self, groups: List[AppGroup], closed: Set[str]) -> None:
        for g in groups:
            if g.exe in closed or not (g.spec and g.spec.lower):
                continue
            lowered = 0
            for p in g.procs:
                if self.s.priority(p.pid) != NORMAL:       # only Normal is lowered
                    continue
                entry = self.j.record({"kind": "priority", "pid": p.pid, "create_time": p.create_time,
                                       "exe": g.exe, "before": NORMAL})
                if self.s.set_priority(p.pid, BELOW_NORMAL) is None:
                    self.j.mark_done(entry)
                    lowered += 1
            if lowered:
                self.log(OK, f"{g.label}: left open, {lowered} process(es) at Below Normal priority")


def turn_on(system, choices: Choices, journal: Journal, log: Log, own_pid: int,
            session: int) -> dict:
    """Apply `choices`. Returns {handles_before, handles_after, processes_before, ...}."""
    elevated = system.is_admin()
    procs = system.processes()
    journal.begin(handles_before=total_handles(procs), processes_before=len(procs),
                  elevated=elevated)
    on = _On(system, journal, log)
    if choices.plan:
        on.plan(system.cpu_name())
    if choices.gamebar:
        on.gamebar()
    if choices.vcache:
        on.vcache(elevated)
    on.services(choices.services, elevated)
    groups = running_apps(procs, session, own_pid, lambda pid: system.details(pid)[0])
    on.close_apps(groups, choices.close)
    if choices.lower:
        on.lower(groups, choices.close)
    time.sleep(1.0)
    after = system.processes()
    journal.data.update(handles_after=total_handles(after), processes_after=len(after))
    journal.save()
    return dict(journal.data)


def enforce(system, journal: Journal, log: Log) -> int:
    """While ON: stop again the services Windows restarted on its own."""
    count = 0
    for entry in journal.actions:
        spec = catalog.SERVICE_BY_NAME.get(entry.get("name", "").lower())
        if entry.get("kind") != "service_stop" or not entry.get("done") or not (spec and spec.restop):
            continue
        state = system.service(entry["name"])
        if state is not None and state.running and system.stop_service(entry["name"]) is None:
            count += 1
            log(INFO, f"{spec.label} was started again by Windows -- stopped it again")
    return count


# ==== OFF =======================================================================================
def turn_off(system, journal: Journal, log: Log, settle: float = 10.0) -> List[dict]:
    """Put back everything the journal recorded; return the report lines."""
    report: List[dict] = []

    def say(level: str, text: str) -> None:
        report.append({"level": level, "text": text})
        log(level, text)

    if journal.data.get("corrupt"):
        say(BAD, f"The Game Mode journal could not be read ({journal.data['corrupt']}); "
                 "nothing could be put back automatically.")
    ctx: dict = {"reopened": [], "priority": {}}
    previous_kind = None
    for entry in reversed(journal.actions):
        if previous_kind == "priority" and entry.get("kind") != "priority":
            _say_priorities(ctx, say)
        previous_kind = entry.get("kind")
        handler = _RESTORE.get(entry.get("kind"))
        if handler is None:
            say(WARN, f"Unknown journal entry {entry.get('kind')!r} skipped")
            continue
        try:
            handler(system, entry, say, ctx)
        except Exception as error:  # noqa: BLE001 -- one failed restore must not stop the rest
            logger.exception("game mode: restoring %s failed", entry)
            say(BAD, f"{entry.get('label') or entry.get('name') or entry.get('kind')}: "
                     f"restore failed ({error})")
    _say_priorities(ctx, say)
    _verify_reopened(system, ctx["reopened"], say, settle)
    journal.finish(report)
    return report


def _restore_priority(system, e, say, ctx) -> None:
    """Counted per program in `ctx["priority"]`; only problems are said here."""
    if not e.get("done"):
        return
    if not system.alive(e["pid"], e["create_time"]):
        return                                   # closed since: nothing to put back
    if system.priority(e["pid"]) != BELOW_NORMAL:
        say(INFO, f"{e['exe']} ({e['pid']}): priority changed since -- left as it is")
        return
    error = system.set_priority(e["pid"], e["before"])
    if error:
        say(BAD, f"{e['exe']} ({e['pid']}): priority not restored ({error})")
        return
    key = (e["exe"], PRIORITY_NAMES.get(e["before"], str(e["before"])))
    ctx["priority"][key] = ctx["priority"].get(key, 0) + 1


def _say_priorities(ctx: dict, say) -> None:
    for (exe, name), count in sorted(ctx["priority"].items()):
        say(OK, f"{exe}: {count} process(es) back to {name} priority")
    ctx["priority"].clear()


def _restore_app(system, e, say, ctx) -> None:
    running = any(p.name.lower() == e["exe"] for p in system.processes())
    if not e.get("done") and running:
        return                                   # the close never happened
    if not e.get("reopen"):
        why = "it ran elevated" if e.get("was_elevated") else "Windows starts it again when needed"
        say(INFO, f"{e['label']}: not reopened ({why})")
        return
    if running:
        say(OK, f"{e['label']}: already running again")
        return
    if e.get("aumid"):
        error = system.launch_aumid(e["aumid"])
    else:
        error = system.launch(e["path"], e.get("args", ""), e.get("cwd", ""))
    if error:
        say(BAD, f"{e['label']}: could not reopen ({error})")
    else:
        ctx["reopened"].append((e["exe"], e["label"]))


def _restore_service_stop(system, e, say, _ctx) -> None:
    """It WAS running (only running services are recorded): make it run."""
    state = system.service(e["name"])
    if state is None:
        say(WARN, f"{e['label']}: no longer installed")
        return
    if state.running:
        say(OK, f"{e['label']}: already running")
        return
    if state.start_type == "Disabled":
        say(WARN, f"{e['label']}: was running, but is now Disabled -- not started")
        return
    error = system.start_service(e["name"])
    say(BAD if error else OK, f"{e['label']}: " + (f"not restarted ({error})" if error
                                                    else "running again"))


def _restore_service_start(system, e, say, _ctx) -> None:
    """It was STOPPED before Game Mode started it: stop it again -- even when
    the start reported a failure, since a slow start may still have landed."""
    state = system.service(e["name"])
    if state is None or state.running is False:
        return
    error = system.stop_service(e["name"])
    say(BAD if error else OK, "AMD 3D V-Cache optimizer: " + (f"not stopped again ({error})" if error
                                                               else "stopped again, as it was"))


def _restore_registry(system, e, say, _ctx) -> None:
    if e.get("existed"):
        error = system.reg_set(e["path"], e["name"], e["value"], e["type"])
    else:
        error = system.reg_delete(e["path"], e["name"])
    exists, value, _kind = system.reg_get(e["path"], e["name"])
    back = exists == bool(e.get("existed")) and (not exists or value == e["value"])
    if error or not back:
        say(BAD, f"Game Mode setting {e['name']}: not restored ({error or 'read-back differs'})")
    else:
        say(OK, f"Windows Game Mode setting {e['name']}: back to "
                + (str(e["value"]) if e.get("existed") else "not set"))


def _restore_plan(system, e, say, _ctx) -> None:
    name = e.get("before_name") or e["before"]
    error = system.set_plan(e["before"])
    if error or system.active_plan() != e["before"]:
        say(BAD, f"Power plan: not restored to {name} ({error or 'read-back differs'})")
    else:
        say(OK, f"Power plan back to {name}")


_RESTORE = {"priority": _restore_priority, "app_close": _restore_app,
            "service_stop": _restore_service_stop, "service_start": _restore_service_start,
            "registry": _restore_registry, "plan": _restore_plan}


def _verify_reopened(system, reopened, say, settle: float) -> None:
    deadline = time.monotonic() + settle
    pending = dict(reopened)
    while pending and time.monotonic() < deadline:
        running = {p.name.lower() for p in system.processes()}
        for exe in [x for x in pending if x in running]:
            say(OK, f"{pending.pop(exe)}: reopened")
        if pending:
            time.sleep(0.5)
    for label in pending.values():
        say(WARN, f"{label}: asked to reopen, not seen running within {int(settle)} s")
