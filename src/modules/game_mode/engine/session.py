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
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import catalog
from .journal import Journal
from .system import BELOW_NORMAL, NORMAL, PRIORITY_NAMES, Proc, split_command_line

logger = logging.getLogger(__name__)

OPERATION_LOCK = threading.Lock()
LONG_SESSION_S = 6 * 3600

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
GAMEBAR_KEY = r"Software\Microsoft\GameBar"
MAINTENANCE_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\Maintenance"
THEMES_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
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


_FLAGS = ("plan", "gamebar", "vcache", "lower", "tasks", "maintenance", "visuals", "explorer")

#: How many times `enforce` stops a service Windows keeps restarting before it
#: leaves it running -- past that, something genuinely needs it.
MAX_RESTOPS = 3


@dataclass
class Choices:
    plan: bool = True
    gamebar: bool = True
    vcache: bool = True
    services: Set[str] = field(default_factory=lambda: {s.name for s in catalog.SERVICES
                                                         if s.default})
    close: Set[str] = field(default_factory=lambda: {a.exe for a in catalog.APPS if a.close})
    lower: bool = True
    #: Pause the updater/maintenance scheduled tasks in `catalog.TASKS`.
    tasks: bool = True
    #: Catalog patterns selected individually, like services and apps.
    paused_tasks: Set[str] = field(default_factory=lambda: {t.pattern for t in catalog.TASKS
                                                             if t.level == catalog.REC})
    #: Pause Windows' Automatic Maintenance (one HKLM value).
    maintenance: bool = True
    #: Transparency and window animations off.
    visuals: bool = False
    #: Restart Explorer at the end of ON: a fresh shell, without the handles
    #: a long-running one accumulates (11,273 here). Open folder windows close.
    explorer: bool = False
    #: REC or MAX while the ticks are exactly a preset's -- then they are
    #: recomputed from the catalog every time, so an app or service added to
    #: the catalog later is included without pressing the button again.
    #: "custom" once anything is ticked by hand.
    preset: str = catalog.REC

    def to_config(self) -> dict:
        out = {flag: getattr(self, flag) for flag in _FLAGS}
        out.update(services=sorted(self.services), close=sorted(self.close), preset=self.preset,
                   paused_tasks=sorted(self.paused_tasks))
        return out


def preset(level: str, groups: List["AppGroup"]) -> Choices:
    """Presets select catalogued programs; Explorer always requires opt-in."""
    wanted = {catalog.REC} if level == catalog.REC else {catalog.REC, catalog.MAX}
    # A program an AI agent runs inside is never ticked by a preset.
    hosting = {g.exe for g in groups if g.hosts}
    return Choices(services={s.name for s in catalog.SERVICES if s.level in wanted},
                   paused_tasks={t.pattern for t in catalog.TASKS if t.level in wanted},
                   close={a.exe for a in catalog.APPS if a.level in wanted and a.exe not in hosting},
                   visuals=level == catalog.MAX, explorer=False, preset=level)


def choices_from_config(data) -> Choices:
    c = Choices()
    if not isinstance(data, dict):
        return c
    for flag in _FLAGS:
        if isinstance(data.get(flag), bool):
            setattr(c, flag, data[flag])
    if isinstance(data.get("services"), list):
        c.services = {s for s in data["services"] if isinstance(s, str)}
    if isinstance(data.get("close"), list):
        c.close = {s.lower() for s in data["close"] if isinstance(s, str)}
    c.preset = data.get("preset") if data.get("preset") in (catalog.REC, catalog.MAX) else "custom"
    if isinstance(data.get("paused_tasks"), list):
        c.paused_tasks = {s for s in data["paused_tasks"] if isinstance(s, str)}
    elif c.preset == catalog.MAX:
        c.paused_tasks = {t.pattern for t in catalog.TASKS if t.level in (catalog.REC, catalog.MAX)}
    return c


@dataclass
class AppGroup:
    """Every process of one program in the signed-in session."""
    exe: str
    label: str
    spec: Optional[catalog.AppSpec]
    procs: List[Proc]
    roots: List[Proc]
    #: AI agents running under this program (closing it ends them). Presets
    #: never tick such a program; the person may.
    hosts: Tuple[str, ...] = ()

    @property
    def handles(self) -> int:
        return sum(p.handles for p in self.procs)


def running_apps(procs: List[Proc], session: int, own_pid: int,
                 path_of: Callable[[int], Optional[str]],
                 protect: Optional[Set[int]] = None) -> List[AppGroup]:
    """Programs in `session` that Game Mode could close, biggest first.

    Left out: Windows' own (anything under %SystemRoot%, except the few it
    starts again on demand), NEVER_CLOSE, and -- so a game that is already
    running is never closed -- anything a game launcher started, anything
    running from a game folder, and the pids in `protect` (the full-screen
    foreground window). A helper whose parent is another listed program is
    folded into that program: VS Code's language servers are VS Code.
    """
    windir = os.path.normcase(os.environ.get("SystemRoot") or os.environ.get("windir") or "\\Windows")
    by_pid = {p.pid: p for p in procs}
    protect = protect or set()
    by_name: Dict[str, List[Proc]] = {}
    for p in procs:
        if p.session == session and p.pid != own_pid and p.session != 0:
            by_name.setdefault(p.name.lower(), []).append(p)
    pids_by_name = {n: {p.pid for p in ps} for n, ps in by_name.items()}
    hosting = _hosts_of_work_in_progress(procs, session, by_pid)
    children: Dict[int, List[Proc]] = {}
    for p in procs:
        children.setdefault(p.ppid, []).append(p)
    groups = []
    for name, ps in by_name.items():
        closable_launcher = (catalog.is_idle_closable_launcher(name)
                             and not any(_runs_a_game(p, children) for p in ps))
        if (catalog.never_close(name) and not closable_launcher) or \
                any(p.pid == own_pid or p.pid in protect for p in ps):
            continue
        roots = [p for p in ps if p.ppid not in pids_by_name[name]] or ps[:1]
        if not closable_launcher and any(_started_by_launcher(r, by_pid) for r in roots):
            continue
        path = path_of(roots[0].pid) or ""
        if not path or catalog.in_game_folder(path) or (
                os.path.normcase(path).startswith(windir) and name not in catalog.WINDOWS_ON_DEMAND):
            continue
        spec = catalog.spec_for(name)
        hosted = sorted({hosting[p.pid] for p in ps if p.pid in hosting})
        label = spec.label if spec else os.path.basename(path)
        if hosted:
            label += f"  [runs {', '.join(hosted)} -- closing it ends that]"
        groups.append(AppGroup(name, label, spec, ps, roots, tuple(hosted)))
    return sorted(_fold_helpers(groups), key=lambda g: -g.handles)


def _hosts_of_work_in_progress(procs: List[Proc], session: int,
                               by_pid: Dict[int, Proc]) -> Dict[int, str]:
    """{pid: agent exe} for every ancestor of an AI agent in `session`.
    A parent only counts while it is OLDER than its child (PIDs are reused)."""
    hosts: Dict[int, str] = {}
    for p in procs:
        if p.session != session or not catalog.is_work_in_progress(p.name):
            continue
        seen, child, cur = set(), p, by_pid.get(p.ppid)
        while cur is not None and cur.pid not in seen and cur.create_time <= child.create_time:
            hosts.setdefault(cur.pid, p.name.lower())
            seen.add(cur.pid)
            child, cur = cur, by_pid.get(cur.ppid)
    return hosts


def _runs_a_game(launcher: Proc, children: Dict[int, List[Proc]]) -> bool:
    """Is anything that is not the launcher's own helper running under it? That
    is a game (or something it started that the user is using): not idle."""
    stack, seen = list(children.get(launcher.pid, ())), set()
    while stack:
        p = stack.pop()
        if p.pid in seen or p.create_time < launcher.create_time:
            continue
        seen.add(p.pid)
        if not catalog.is_launcher_helper(p.name):
            return True
        stack.extend(children.get(p.pid, ()))
    return False


def _started_by_launcher(proc: Proc, by_pid: Dict[int, Proc]) -> bool:
    """Is a game launcher among `proc`'s ancestors? A parent is only believed
    while it is OLDER than its child -- Windows reuses PIDs."""
    seen, child, cur = set(), proc, by_pid.get(proc.ppid)
    while cur is not None and cur.pid not in seen and cur.create_time <= child.create_time:
        if catalog.is_launcher(cur.name):
            return True
        seen.add(cur.pid)
        child, cur = cur, by_pid.get(cur.ppid)
    return False


def _fold_helpers(groups: List[AppGroup]) -> List[AppGroup]:
    changed = True
    while changed:
        changed = False
        owner = {p.pid: g for g in groups for p in g.procs}
        for g in groups:
            if g.spec and g.spec.when:   # keep its per-app close condition in force
                continue
            parents = {id(owner[r.ppid]): owner[r.ppid] for r in g.roots if r.ppid in owner}
            if len(parents) == 1 and len(g.roots) == sum(1 for r in g.roots if r.ppid in owner):
                parent = next(iter(parents.values()))
                if parent is not g:
                    parent.procs.extend(g.procs)
                    groups = [x for x in groups if x is not g]
                    changed = True
                    break
    return groups


def total_handles(procs: List[Proc]) -> int:
    return sum(p.handles for p in procs)


_HELPERS = frozenset({"msedgewebview2.exe", "conhost.exe"})


def freed_estimate(procs: List[Proc], groups: List[AppGroup], close: Set[str]) -> int:
    """Handles held by the programs in `close` PLUS the helper processes they
    own -- WebView2 is a separate process tree under its host app (40 of them,
    28,212 handles, measured here) and goes when the host goes."""
    owners = {p.pid for g in groups if g.exe in close for p in g.procs}
    by_pid = {p.pid: p for p in procs}
    total = sum(g.handles for g in groups if g.exe in close)
    def helper(name: str) -> bool:      # WebView2/conhost, and a launcher's own helpers
        low = name.lower()
        return low in _HELPERS or (catalog.is_launcher_helper(low) and not catalog.is_launcher(low))

    for p in procs:
        if not helper(p.name):
            continue
        seen, cur = set(), p
        while cur is not None and helper(cur.name) and cur.pid not in seen:
            seen.add(cur.pid)
            cur = by_pid.get(cur.ppid)
        if cur is not None and cur.pid in owners:
            total += p.handles
    return total


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

    def registry(self, hive: str, path: str, name: str, value: int, label: str,
                 broadcast: str = "") -> Optional[bool]:
        """Set one DWORD, journaled. True = changed, False = already so, None = not done."""
        exists, before, kind = self.s.reg_get(path, name, hive)
        if exists is None:
            self.log(WARN, f"{label}: unreadable -- left alone")
            return None
        if exists and before == value:
            return False
        entry = self.j.record({"kind": "registry", "hive": hive, "path": path, "name": name,
                               "label": label, "existed": exists, "value": before, "type": kind,
                               "broadcast": broadcast})
        error = self.s.reg_set(path, name, value, REG_DWORD, hive)
        if error:
            self.log(BAD, f"{label}: not set ({error})")
            return None
        if broadcast:
            self.s.broadcast_setting_change(broadcast)
        self.j.mark_done(entry)
        return True

    def gamebar(self) -> None:
        changed = [self.registry("HKCU", GAMEBAR_KEY, name, 1, f"Windows Game Mode setting {name}")
                   for name in ("AutoGameModeEnabled", "AllowAutoGameMode")]
        if any(value is None for value in changed):
            self.log(WARN, "Windows Game Mode setting could not be fully read or applied")
            return
        self.log(OK, "Windows Game Mode setting turned on" if any(changed)
                 else "Windows Game Mode setting was already on")

    def maintenance(self, elevated: bool) -> None:
        if not elevated:
            self.log(WARN, "Automatic Maintenance: pausing it needs administrator")
            return
        done = self.registry("HKLM", MAINTENANCE_KEY, "MaintenanceDisabled", 1,
                             "Windows Automatic Maintenance")
        if done is not None:
            self.log(OK, "Windows Automatic Maintenance paused (no defrag, scans or updates "
                         "started by the maintenance window)")

    def visuals(self) -> None:
        changed = self.registry("HKCU", THEMES_KEY, "EnableTransparency", 0, "Transparency effects",
                                broadcast="ImmersiveColorSet")
        if changed is not None:
            self.log(OK, "Transparency effects " + ("turned off" if changed else "were already off"))
        animations = self.s.animations()
        if animations is None:
            self.log(WARN, "Window animations: setting unreadable -- left alone")
            return
        if not animations:
            self.log(OK, "Window animations were already off")
            return
        entry = self.j.record({"kind": "animations", "before": True})
        error = self.s.set_animations(False)
        if error:
            self.log(BAD, f"Window animations: not turned off ({error})")
            return
        self.j.mark_done(entry)
        self.log(OK, "Window animations turned off")

    def tasks(self, elevated: bool, choices: Choices) -> None:
        wanted = choices.paused_tasks
        if choices.preset in (catalog.REC, catalog.MAX):
            levels = {catalog.REC, catalog.MAX} if choices.preset == catalog.MAX else {catalog.REC}
            wanted = {t.pattern for t in catalog.TASKS if t.level in levels}
        try:
            found = self.s.tasks()
        except Exception as error:  # noqa: BLE001 -- the Task Scheduler COM API raises com_error
            self.log(WARN, f"Scheduled tasks could not be listed ({error}) -- left alone")
            return
        paused, refused = 0, []
        for path, enabled in found:
            spec = catalog.task_spec(path)
            if spec is None or spec.pattern not in wanted or not enabled:
                continue
            running = choices.preset == catalog.MAX and self.s.task_running(path) is True
            entry = self.j.record({"kind": "task", "path": path, "label": spec.label})
            error = self.s.set_task_enabled(path, False)
            if error:
                refused.append(f"{spec.label} ({error})")
                continue
            self.j.mark_done(entry)
            paused += 1
            if running:
                error = self.s.stop_task(path)
                if error:
                    self.log(WARN, f"{spec.label}: paused for future triggers; {error}")
                else:
                    self.log(INFO, f"{spec.label}: stopped a run in progress; "
                                   "it runs again at its next trigger")
        self.log(OK, f"{paused} scheduled updater/maintenance task(s) paused")
        if refused:
            self.log(INFO if not elevated else WARN,
                     "Left alone (Windows protects them" + ("" if elevated else ", or needs administrator")
                     + "): " + "; ".join(refused[:6]) + (" ..." if len(refused) > 6 else ""))

    def vcache(self, elevated: bool) -> None:
        state = self.s.service(catalog.VCACHE_SERVICE)
        if state is None or state.running:
            return
        if state.running is None:
            self.log(WARN, f"AMD 3D V-Cache optimizer: state unreadable ({state.error}) -- left alone")
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
            if spec.when == "no_wifi" and self.s.wifi_connected() is not False:
                self.log(INFO, f"{spec.label}: left running -- Wi-Fi is in use (or could not be read)")
                continue
            for name in self.s.service_names(spec.name):
                self._stop(name, spec, elevated)

    def explorer(self, session: int) -> None:
        """Last step of ON. Nothing to put back: Windows restarts the shell
        itself (AutoRestartShell), and `restart_explorer` falls back to
        starting it as the signed-in user if it does not."""
        before = sum(p.handles for p in self.s.processes()
                     if p.name.lower() == "explorer.exe" and p.session == session)
        entry = self.j.record({"kind": "explorer_restart", "handles_before": before})
        error = self.s.restart_explorer(session)
        if error:
            self.log(BAD, f"Explorer: {error}")
            return
        self.j.mark_done(entry)
        after = sum(p.handles for p in self.s.processes()
                    if p.name.lower() == "explorer.exe" and p.session == session)
        self.log(OK, f"Explorer restarted fresh: {before:,} -> {after:,} handles "
                     "(open File Explorer windows were closed)")

    def _stop(self, name: str, spec: catalog.ServiceSpec, elevated: bool) -> None:
        state = self.s.service(name)
        if state is None or state.running is False:
            return
        if state.running is None:
            self.log(WARN, f"{spec.label}: state unreadable ({state.error}) -- left alone")
            return
        if not elevated:
            self.log(WARN, f"{spec.label}: running; stopping it needs administrator")
            return
        deps = self.s.running_dependents(name)
        if deps:
            self.log(INFO, f"{spec.label}: left running, {', '.join(deps)} depends on it")
            return
        entry = self.j.record({"kind": "service_stop", "name": name, "label": spec.label,
                               "start_type": state.start_type})
        error = self.s.stop_service(name)
        if error:
            self.log(BAD, f"{spec.label}: not stopped ({error})")
            return
        self.j.mark_done(entry)
        self.log(OK, f"{spec.label}: stopped")

    def close_apps(self, groups: List[AppGroup], wanted: Set[str], maximum: bool = False) -> None:
        """Every program is journaled first, then all are closed together."""
        chosen = [g for g in groups if g.exe in wanted]
        entries, jobs, closing = [], [], []
        for g in chosen:
            if g.spec and g.spec.when == "spareprocess":
                if (not maximum or any("spareprocess" not in (self.s.details(p.pid)[1] or "").lower()
                                       for p in g.procs)
                        or self.s.has_visible_window([p.pid for p in g.procs]) is not False):
                    self.log(INFO, f"{g.label}: left open -- not a hidden spare process in Maximum")
                    continue
            path, cmdline, elevated = self.s.details(g.roots[0].pid)
            program, args = split_command_line(cmdline or "")
            extra = g.spec.reopen_args if g.spec else ""
            if extra and extra not in args:
                args = f"{args} {extra}".strip()
            reopen = g.spec.reopen if g.spec else True
            state = getattr(self.s, "window_state", lambda _pids: "normal")([p.pid for p in g.procs])
            entries.append(self.j.record({
                "kind": "app_close", "exe": g.exe, "label": g.label, "path": path or program,
                "args": args, "cwd": os.path.dirname(path or program), "aumid": self.s.aumid(g.roots[0].pid),
                "reopen": reopen and elevated is not True, "was_elevated": elevated,
                "handles": g.handles, "processes": len(g.procs), "window": state}))
            jobs.append(([(p.pid, p.create_time) for p in g.procs], path))
            closing.append(g)
        if not jobs:
            return
        self.log(INFO, f"Closing {len(jobs)} program(s) together...")
        for g, entry, error in zip(closing, entries, self.s.close_many(jobs)):
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
                if self.s.alive(p.pid, p.create_time) and self.s.set_priority(p.pid, BELOW_NORMAL) is None:
                    self.j.mark_done(entry)
                    lowered += 1
            if lowered:
                self.log(OK, f"{g.label}: left open, {lowered} process(es) at Below Normal priority")


def turn_on(system, choices: Choices, journal: Journal, log: Log, own_pid: int,
            session: int) -> dict:
    with OPERATION_LOCK:
        if journal.active:
            raise RuntimeError("Game Mode is already ON; use OFF to restore it first")
        return _turn_on(system, choices, journal, log, own_pid, session)


def _turn_on(system, choices: Choices, journal: Journal, log: Log, own_pid: int,
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
    if choices.maintenance:
        on.maintenance(elevated)
    if choices.tasks:
        on.tasks(elevated, choices)
    if choices.visuals:
        on.visuals()
    on.services(choices.services, elevated)
    groups = running_apps(procs, session, own_pid, lambda pid: system.details(pid)[0],
                          system.fullscreen_pids())
    on.close_apps(groups, choices.close, choices.preset == catalog.MAX)
    if choices.lower:
        on.lower(groups, choices.close)
    if choices.explorer:
        on.explorer(session)
    time.sleep(1.0)
    after = system.processes()
    journal.data.update(handles_after=total_handles(after), processes_after=len(after))
    journal.save()
    return dict(journal.data)


def enforce(system, journal: Journal, log: Log) -> int:
    if not OPERATION_LOCK.acquire(blocking=False):
        return 0
    try:
        return _enforce(system, journal, log)
    finally:
        OPERATION_LOCK.release()


def session_age(journal: Journal) -> Optional[int]:
    started = journal.data.get("started")
    if not isinstance(started, (int, float)) or started <= 0 or not started < float("inf"):
        return None
    return max(0, int(time.time() - started))


def age_text(journal: Journal) -> str:
    age = session_age(journal)
    if age is None:
        return "ON (duration unknown)"
    return f"ON for {age // 3600} h {age % 3600 // 60} min"


def long_session_warning(journal: Journal) -> str:
    return (f"Game Mode is {age_text(journal)}. Windows Update, maintenance and updater tasks "
            "are still paused. Game Mode OFF is recommended.")


def _enforce(system, journal: Journal, log: Log) -> int:
    """While ON: stop again any service Game Mode stopped that Windows has
    started again -- at most MAX_RESTOPS times each, then it is left running
    and said so once (something genuinely needs it)."""
    age = session_age(journal)
    if age is not None and age > LONG_SESSION_S:
        log(WARN, long_session_warning(journal))
    count = 0
    for entry in journal.actions:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        if catalog.service_spec(entry["name"]) is None:
            continue
        if entry.get("kind") != "service_stop" or not entry.get("done") or entry.get("gave_up"):
            continue
        state = system.service(entry["name"])
        if state is None or not state.running:
            continue
        label = entry.get("label") or entry["name"]
        if entry.get("restops", 0) >= MAX_RESTOPS:
            entry["gave_up"] = True
            journal.save()
            log(WARN, f"{label} keeps being started by Windows -- left running from now on")
            continue
        if system.stop_service(entry["name"]) is None:
            entry["restops"] = entry.get("restops", 0) + 1
            journal.save()
            count += 1
            log(INFO, f"{label} was started again by Windows -- stopped it again")
    return count


# ==== OFF =======================================================================================
def turn_off(system, journal: Journal, log: Log, settle: float = 10.0) -> List[dict]:
    """Serialize OFF after ON/enforce, retaining every unresolved change."""
    with OPERATION_LOCK:
        return _turn_off(system, journal, log, settle)


def _turn_off(system, journal: Journal, log: Log, settle: float) -> List[dict]:
    report: List[dict] = []

    def say(level: str, text: str) -> None:
        report.append({"level": level, "text": text})
        log(level, text)

    if journal.data.get("corrupt"):
        say(BAD, f"The Game Mode journal could not be read ({journal.data['corrupt']}); "
                 "nothing could be put back automatically. Game Mode stays ON; journal kept.")
        return report
    ctx: dict = {"reopened": [], "priority": {}, "tasks": 0}
    failed, validated = [], []
    for entry in reversed(journal.actions):
        try:
            if not _valid_entry(system, entry):
                say(BAD, f"Rejected journal entry {entry!r}: not something Game Mode changes")
                continue
            validated.append(entry)
            if not _RESTORE[entry["kind"]](system, entry, say, ctx):
                failed.append(entry)
        except Exception as error:  # noqa: BLE001 -- keep the way back after any refusal
            logger.exception("game mode: restoring %s failed", entry)
            say(BAD, f"{entry!r}: restore failed ({error})")
            failed.append(entry)
    _say_priorities(ctx, say)
    if ctx.get("quiet") and hasattr(system, "keep_minimized"):
        # On its own thread: OFF does not wait the 15 s it watches for.
        threading.Thread(target=system.keep_minimized, args=(list(ctx["quiet"]),),
                         daemon=True, name="game-mode-keep-minimized").start()
    if ctx["tasks"]:
        say(OK, f"{ctx['tasks']} scheduled task(s) enabled again")
    pending = _verify_reopened(system, ctx["reopened"], say, settle)
    failed.extend(e for e in validated if e.get("kind") == "app_close"
                  and e.get("exe") in pending and e not in failed)
    if failed:
        journal.data["actions"] = [e for e in journal.actions if e in failed]
        journal.save()
        say(BAD, f"Recovery incomplete: {len(failed)} change(s) could not be put back -- "
                 "Game Mode stays ON so OFF can be tried again (for example as administrator)")
    else:
        journal.finish(report)
    return report


def _valid_registry(e: dict) -> bool:
    allowed = {("HKCU", GAMEBAR_KEY, "AutoGameModeEnabled"),
               ("HKCU", GAMEBAR_KEY, "AllowAutoGameMode"),
               ("HKLM", MAINTENANCE_KEY, "MaintenanceDisabled"),
               ("HKCU", THEMES_KEY, "EnableTransparency")}
    triple = (e.get("hive"), e.get("path"), e.get("name"))
    if not all(isinstance(x, str) for x in triple) or triple not in allowed:
        return False
    if e.get("existed") is False:
        return e.get("value") is None and e.get("type") == 0
    return (e.get("existed") is True and type(e.get("value")) is int
            and e.get("type") == REG_DWORD)


def _valid_entry(system, e) -> bool:
    """User-writable recovery data must never grant arbitrary elevated writes."""
    if not isinstance(e, dict) or not isinstance(e.get("kind"), str):
        return False
    kind = e["kind"]
    if kind == "registry":
        return _valid_registry(e)
    if kind == "service_stop":
        return isinstance(e.get("name"), str) and catalog.service_spec(e["name"]) is not None
    if kind == "service_start":
        return e.get("name") == catalog.VCACHE_SERVICE
    if kind == "task":
        return isinstance(e.get("path"), str) and catalog.task_spec(e["path"]) is not None
    if kind == "plan":
        before = e.get("before")
        return (isinstance(before, str) and re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", before) is not None
                and before in system.plans())
    if kind == "priority":
        return type(e.get("before")) is int and e["before"] in PRIORITY_NAMES
    if kind == "app_close":
        path, aumid = e.get("path", ""), e.get("aumid")
        return (isinstance(path, str) and (not path or os.path.isabs(path))
                and (aumid in (None, "") or (isinstance(aumid, str)
                     and re.fullmatch(r"[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", aumid) is not None)))
    return kind in ("animations", "explorer_restart")


def _restore_priority(system, e, say, ctx) -> bool:
    """Counted per program; exited processes and subsequent user changes resolve it."""
    if not system.alive(e["pid"], e["create_time"]):
        return True
    current = system.priority(e["pid"])
    if current is None:
        say(BAD, f"{e['exe']} ({e['pid']}): priority unreadable -- restore can be retried")
        return False
    if current != BELOW_NORMAL:
        say(INFO, f"{e['exe']} ({e['pid']}): priority changed since -- left as it is")
        return True
    if not system.alive(e["pid"], e["create_time"]):
        return True
    error = system.set_priority(e["pid"], e["before"])
    if error:
        say(BAD, f"{e['exe']} ({e['pid']}): priority not restored ({error})")
        return False
    key = (e["exe"], PRIORITY_NAMES[e["before"]])
    ctx["priority"][key] = ctx["priority"].get(key, 0) + 1
    return True


def _say_priorities(ctx: dict, say) -> None:
    for (exe, name), count in sorted(ctx["priority"].items()):
        say(OK, f"{exe}: {count} process(es) back to {name} priority")
    ctx["priority"].clear()


def _restore_app(system, e, say, ctx) -> bool:
    running = any(p.name.lower() == e["exe"] for p in system.processes())
    if not e.get("done") and running:
        return True                           # the close never happened
    if not e.get("reopen"):
        why = "it ran elevated" if e.get("was_elevated") else "Windows starts it again when needed"
        say(INFO, f"{e['label']}: not reopened ({why})")
        return True
    if running:
        say(OK, f"{e['label']}: already running again")
        return True
    # Minimized or in the tray before Game Mode: back the same way, not in front.
    quiet = e.get("window") in ("minimized", "hidden")
    if e.get("aumid"):
        error = system.launch_aumid(e["aumid"], quiet=True) if quiet else system.launch_aumid(e["aumid"])
    elif quiet:
        error = system.launch(e["path"], e.get("args", ""), e.get("cwd", ""), quiet=True)
    else:
        error = system.launch(e["path"], e.get("args", ""), e.get("cwd", ""))
    if error:
        say(BAD, f"{e['label']}: could not reopen ({error})")
        return False
    ctx["reopened"].append((e["exe"], e["label"]))
    if quiet:
        ctx.setdefault("quiet", []).append(e["exe"])
    return True


def _restore_service_stop(system, e, say, _ctx) -> bool:
    """It WAS running (only running services are recorded): make it run."""
    label = e.get("label") or e["name"]
    state = system.service(e["name"])
    if state is None:
        say(WARN, f"{label}: no longer installed")
        return True
    if state.running is None:
        say(BAD, f"{label}: state unreadable ({state.error})")
        return False
    if state.running:
        say(OK, f"{label}: already running")
        return True
    if state.start_type == "Disabled":
        say(BAD, f"{label}: was running, but is now Disabled -- not started")
        return False
    error = system.start_service(e["name"])
    say(BAD if error else OK, f"{label}: " + (f"not restarted ({error})" if error
                                             else "running again"))
    return not error


def _restore_service_start(system, e, say, _ctx) -> bool:
    """Return the optimizer to STOPPED, including an interrupted start."""
    state = system.service(e["name"])
    if state is None or state.running is False:
        return True
    if state.running is None:
        say(BAD, f"AMD 3D V-Cache optimizer: state unreadable ({state.error})")
        return False
    error = system.stop_service(e["name"])
    say(BAD if error else OK, "AMD 3D V-Cache optimizer: " + (f"not stopped again ({error})" if error
                                                               else "stopped again, as it was"))
    return not error


def _restore_registry(system, e, say, _ctx) -> bool:
    hive = e.get("hive", "HKCU")
    label = e.get("label") or f"Windows Game Mode setting {e['name']}"
    exists, value, kind = system.reg_get(e["path"], e["name"], hive)
    if exists is e["existed"] and (not exists or (value == e["value"] and kind == e["type"])):
        say(OK, f"{label}: already restored")
        return True
    if e.get("existed"):
        error = system.reg_set(e["path"], e["name"], e["value"], e["type"], hive)
    else:
        error = system.reg_delete(e["path"], e["name"], hive)
    if e.get("broadcast"):
        system.broadcast_setting_change(e["broadcast"])
    exists, value, kind = system.reg_get(e["path"], e["name"], hive)
    back = exists is e["existed"] and (not exists or (value == e["value"] and kind == e["type"]))
    if error or not back:
        say(BAD, f"{label}: not restored ({error or 'read-back differs'})")
    else:
        say(OK, f"{label}: back to " + (str(e["value"]) if e.get("existed") else "not set"))
    return not error and back


def _restore_task(system, e, say, ctx) -> bool:
    """Every task recorded was ENABLED before; counted, only problems said."""
    if system.task_enabled(e["path"]) is True:
        ctx["tasks"] += 1
        return True
    error = system.set_task_enabled(e["path"], True)
    if error:
        say(BAD, f"Scheduled task {e['path']}: not enabled again ({error})")
    else:
        ctx["tasks"] += 1
    return not error


def _restore_animations(system, e, say, _ctx) -> bool:
    if system.animations() == bool(e["before"]):
        say(OK, "Window animations already restored")
        return True
    error = system.set_animations(bool(e["before"]))
    back = system.animations() == bool(e["before"])
    if error or not back:
        say(BAD, f"Window animations: not restored ({error or 'read-back differs'})")
    else:
        say(OK, "Window animations back on")
    return not error and back


def _restore_plan(system, e, say, _ctx) -> bool:
    name = e.get("before_name") or e["before"]
    if system.active_plan() == e["before"]:
        say(OK, f"Power plan already back to {name}")
        return True
    error = system.set_plan(e["before"])
    back = system.active_plan() == e["before"]
    if error or not back:
        say(BAD, f"Power plan: not restored to {name} ({error or 'read-back differs'})")
    else:
        say(OK, f"Power plan back to {name}")
    return not error and back


def _restore_explorer(_system, e, say, _ctx) -> bool:
    if e.get("done"):
        say(OK, "Explorer was restarted fresh during Game Mode -- nothing to put back")
    return True


_RESTORE = {"explorer_restart": _restore_explorer, "priority": _restore_priority,
            "app_close": _restore_app,
            "service_stop": _restore_service_stop, "service_start": _restore_service_start,
            "registry": _restore_registry, "plan": _restore_plan, "task": _restore_task,
            "animations": _restore_animations}


def _verify_reopened(system, reopened, say, settle: float) -> Set[str]:
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
    return set(pending)
