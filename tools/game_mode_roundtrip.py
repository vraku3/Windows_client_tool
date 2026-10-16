"""Game Mode ON -> OFF on the REAL machine, compared against a raw snapshot.

Run ELEVATED (services need it). The power plan, Windows' Game Mode values,
the background services and the priority of apps left open go through the
real pipeline; the only programs CLOSED are two probes this tool starts
itself -- Paint (a packaged app, reopened by AppUserModelID) and a copy
of Character Map in a temp folder (an ordinary exe, reopened with its
arguments) -- so nothing of yours is closed. Exits 1 on any difference
between the state before ON and after OFF, or if a probe did not come back
unelevated.

    python tools/game_mode_roundtrip.py
"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from modules.game_mode.engine import catalog, session  # noqa: E402
from modules.game_mode.engine.journal import Journal  # noqa: E402
from modules.game_mode.engine.system import WinSystem  # noqa: E402

PAINT_AUMID = "Microsoft.Paint_8wekyb3d8bbwe!App"
SERVICES = {"WSearch", "BITS", "DoSvc", "UsoSvc", "wuauserv", "TrkWks", "stisvc", "MapsBroker"}


def session_id() -> int:
    sid = ctypes.c_uint(0)
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    return sid.value


def snapshot(s: WinSystem) -> dict:
    lowered = {a.exe for a in catalog.APPS if a.lower}
    procs = s.processes()
    return {
        "plan": s.active_plan(),
        "services": {n: (s.service(n).running if s.service(n) else None) for n in sorted(SERVICES)},
        "gamebar": {n: s.reg_get(session.GAMEBAR_KEY, n)[:2]
                    for n in ("AutoGameModeEnabled", "AllowAutoGameMode")},
        "priority": {(p.pid, p.create_time): s.priority(p.pid) for p in procs
                     if p.name.lower() in lowered},
        "vcache": s.service(catalog.VCACHE_SERVICE).running if s.service(catalog.VCACHE_SERVICE) else None,
    }


def make_charmap_probe(folder: str) -> str:
    sysdir = os.path.join(os.environ["SystemRoot"], "System32")
    exe = os.path.join(folder, "gmprobe.exe")
    shutil.copy2(os.path.join(sysdir, "charmap.exe"), exe)
    for lang in ("en-US",):
        mui = os.path.join(sysdir, lang, "charmap.exe.mui")
        if os.path.exists(mui):
            os.makedirs(os.path.join(folder, lang), exist_ok=True)
            shutil.copy2(mui, os.path.join(folder, lang, "gmprobe.exe.mui"))
    return exe


def wait_for(s: WinSystem, exe: str, present: bool, seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(p.name.lower() == exe for p in s.processes()) == present:
            return True
        time.sleep(0.3)
    return False


def elevation_of(s: WinSystem, exe: str):
    for p in s.processes():
        if p.name.lower() == exe:
            return s.details(p.pid)
    return None


def main() -> int:
    s = WinSystem()
    if not s.is_admin():
        print("run this elevated")
        return 2
    if any(p.name.lower() == "mspaint.exe" for p in s.processes()):
        print("Paint is already open -- close it first (the probe closes every Paint)")
        return 2
    folder = tempfile.mkdtemp(prefix="gm_probe_")
    probe = make_charmap_probe(folder)
    s.launch(probe, "/probe-arg \"with space\"", folder)
    s.launch_aumid(PAINT_AUMID)
    ok = wait_for(s, "gmprobe.exe", True) and wait_for(s, "mspaint.exe", True)
    print("probes running:", ok, elevation_of(s, "gmprobe.exe"))
    before = snapshot(s)
    journal = Journal(os.path.join(folder, "journal"))
    choices = session.Choices(services=set(SERVICES), close={"gmprobe.exe", "mspaint.exe"})
    log = lambda level, text: print(f"  ON  [{level}] {text}")  # noqa: E731
    t = time.monotonic()
    data = session.turn_on(s, choices, journal, log, os.getpid(), session_id())
    print(f"ON took {time.monotonic() - t:.1f} s; handles {data['handles_before']:,} -> "
          f"{data['handles_after']:,}; processes {data['processes_before']} -> {data['processes_after']}")
    during = snapshot(s)
    gone = not any(p.name.lower() in ("gmprobe.exe", "mspaint.exe") for p in s.processes())
    print("during:", during["plan"], during["services"], "probes closed:", gone)
    time.sleep(3)
    t = time.monotonic()
    report = session.turn_off(s, journal, lambda level, text: print(f"  OFF [{level}] {text}"))
    print(f"OFF took {time.monotonic() - t:.1f} s")
    after = snapshot(s)
    failures = [k for k in before if before[k] != after[k]]
    for k in failures:
        print("DIFFERENT:", k, "\n  before:", before[k], "\n  after: ", after[k])
    back = wait_for(s, "gmprobe.exe", True) and wait_for(s, "mspaint.exe", True)
    probe_facts = elevation_of(s, "gmprobe.exe")
    print("probes back:", back, probe_facts)
    unelevated = probe_facts is not None and probe_facts[2] is False and "with space" in (probe_facts[1] or "")
    bad = [r for r in report if r["level"] == session.BAD]
    for p in s.processes():
        if p.name.lower() in ("gmprobe.exe", "mspaint.exe"):
            s.close([p.pid], None, grace=3)
    shutil.rmtree(folder, ignore_errors=True)
    verdict = not failures and back and unelevated and not bad and gone
    print("RESULT:", "PASS" if verdict else "FAIL")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
