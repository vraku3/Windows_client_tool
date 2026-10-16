"""Collect a support bundle: one zip with what anyone helping with this PC
asks for first. Qt-free.

Every step is listed in MANIFEST.txt as OK, FAILED (with the exit code and
the tool's own last words), SKIPPED (with why -- usually "needs
administrator") or TIMED OUT. A bundle with a step missing and no line
saying so is how a helper concludes "no errors in the System log" from a
log that was never exported.

Measured on this machine (2026-10-16), standard user vs elevated:
`wevtutil epl System|Application` works for a standard user (0.06 s, 2 MB
for 7 days); `Security` needs administrator. `gpresult /h` exits 0 for a
standard user but writes only the USER half (the computer half is refused
silently -- see CLAUDE.md "Group Policy"), so the manifest says so.
`powercfg /requests` refuses a standard user with exit 1. The Script
Library's checks are included as text, each with its own admin note.

It contains machine and user names, IP addresses, installed software, event
logs and recently resolved DNS names. It never contains passwords, Wi-Fi
keys, browser data or documents.
"""
from __future__ import annotations

import ctypes
import logging
import os
import socket
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Sequence

from . import library

logger = logging.getLogger(__name__)

OK, FAILED, SKIPPED, TIMED_OUT, CANCELLED = "OK", "FAILED", "SKIPPED", "TIMED OUT", "CANCELLED"
_WEEK_MS = 7 * 86_400_000
_EVTX_QUERY = f"/q:*[System[TimeCreated[timediff(@SystemTime) <= {_WEEK_MS}]]]"


#: Library checks the bundle already has as a raw export of its own.
_ALREADY_COLLECTED = ("ipconfig", "routes")


@dataclass(frozen=True)
class Step:
    name: str
    filename: str
    argv: Sequence[str] = ()             # "{out}" is replaced by the output file's path
    writes_file: bool = False            # the tool writes {out} itself (else: stdout -> file)
    admin: bool = False                  # skipped unelevated
    timeout: float = 120.0
    note: str = ""                       # said in the manifest whatever happens
    script: Optional[library.Script] = None


@dataclass
class StepResult:
    step: Step
    status: str
    seconds: float = 0.0
    detail: str = ""


@dataclass
class Bundle:
    path: str = ""                       # the zip
    results: List[StepResult] = field(default_factory=list)
    manifest: str = ""

    @property
    def counts(self):
        out = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out


def steps() -> List[Step]:
    ps = ["powershell.exe", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-Command"]
    out = [
        Step("System information", "systeminfo.txt", ["systeminfo"], timeout=180),
        Step("Network configuration", "ipconfig_all.txt", ["ipconfig", "/all"]),
        Step("Routing table", "route_print.txt", ["route", "print"]),
        Step("Connections and listening ports", "netstat.txt", ["netstat", "-ano"]),
        Step("Drivers", "driverquery.csv", ["driverquery", "/v", "/fo", "csv"], timeout=180),
        Step("Services", "services.csv", ps + [
            "Get-CimInstance Win32_Service | Select-Object Name, DisplayName, State, StartMode, "
            "StartName, PathName, ExitCode | Export-Csv -NoTypeInformation -Encoding UTF8 '{out}'"],
            writes_file=True),
        Step("Installed programs", "installed_programs.csv", ps + [
            "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
            "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
            "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' "
            "-ErrorAction SilentlyContinue | Where-Object DisplayName | Select-Object DisplayName, "
            "DisplayVersion, Publisher, InstallDate, InstallLocation | Sort-Object DisplayName | "
            "Export-Csv -NoTypeInformation -Encoding UTF8 '{out}'"], writes_file=True),
        Step("System event log (7 days)", "System.evtx",
             ["wevtutil", "epl", "System", "{out}", _EVTX_QUERY, "/ow:true"], writes_file=True),
        Step("Application event log (7 days)", "Application.evtx",
             ["wevtutil", "epl", "Application", "{out}", _EVTX_QUERY, "/ow:true"], writes_file=True),
        Step("Security event log (7 days)", "Security.evtx",
             ["wevtutil", "epl", "Security", "{out}", _EVTX_QUERY, "/ow:true"], writes_file=True,
             admin=True),
        Step("Group Policy result", "gpresult.html", ["gpresult", "/h", "{out}", "/f"],
             writes_file=True, timeout=180,
             note="Unelevated, gpresult writes only the USER half and refuses the computer "
                  "half without saying so."),
        Step("Power requests (what keeps the PC awake)", "powercfg_requests.txt",
             ["powercfg", "/requests"], admin=True),
        Step("Available sleep states", "powercfg_sleepstates.txt", ["powercfg", "/a"]),
        Step("DirectX diagnostics", "dxdiag.txt", ["dxdiag", "/t", "{out}"], writes_file=True,
             timeout=240),
    ]
    out += [Step(f"Check: {s.title}", f"checks/{s.id}.txt", script=s, timeout=s.timeout)
            for s in library.BUILT_IN if s.id not in _ALREADY_COLLECTED]
    return out


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def default_folder() -> str:
    """The Desktop -- the REAL one (OneDrive redirects it here), else Documents, else home."""
    try:
        from modules.cleanup.cleanup_scanner.known_folders import known_folder
        for name in ("Desktop", "Documents"):
            path = known_folder(name)
            if path and os.path.isdir(path):
                return path
    except ImportError as e:
        logger.warning("support bundle: known folders unavailable: %s", e)
    return os.path.expanduser("~")


def _tail(text: str, n: int = 2) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:])[:300]


def _decode(raw: bytes) -> str:
    return library.decode(raw or b"")


def _run_step(step: Step, out_path: str, elevated: bool,
              is_cancelled: Callable[[], bool]) -> StepResult:
    if step.admin and not elevated:
        return StepResult(step, SKIPPED, detail="needs administrator rights")
    start = time.monotonic()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if step.script is not None:
        res = library.run(step.script, lambda _l: None, is_cancelled)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# {step.script.title}\n# {step.script.description}\n\n")
            f.write("\n".join(res.lines) + "\n")
        status = (CANCELLED if res.cancelled else TIMED_OUT if res.timed_out
                  else OK if res.returncode == 0 else FAILED)
        detail = "" if status == OK else f"exit {res.returncode}: {_tail(chr(10).join(res.lines))}"
        return StepResult(step, status, time.monotonic() - start, detail)
    argv = [a.replace("{out}", out_path) for a in step.argv]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=step.timeout,
                              stdin=subprocess.DEVNULL, creationflags=library.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return StepResult(step, TIMED_OUT, time.monotonic() - start,
                          f"no answer within {step.timeout:.0f} s")
    except OSError as e:
        return StepResult(step, FAILED, time.monotonic() - start, f"could not start: {e}")
    text = _decode(proc.stdout) + _decode(proc.stderr)
    if not step.writes_file:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    produced = os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    if proc.returncode == 0 and produced:
        return StepResult(step, OK, time.monotonic() - start)
    why = f"exit {proc.returncode}" + ("" if produced else ", no output file")
    return StepResult(step, FAILED, time.monotonic() - start, f"{why}: {_tail(text)}")


def manifest_text(results: List[StepResult], elevated: bool, started: datetime) -> str:
    lines = [
        "WinClientTool support bundle",
        f"Computer : {socket.gethostname()}",
        f"User     : {os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}",
        f"Elevated : {'yes' if elevated else 'NO -- steps that need administrator were skipped'}",
        f"Created  : {started:%Y-%m-%d %H:%M:%S}",
        "Contains machine and user names, IP addresses, installed software, event logs and",
        "recently resolved DNS names. No passwords, Wi-Fi keys, browser data or documents.",
        "",
    ]
    for r in results:
        lines.append(f"[{r.status:9}] {r.step.name}  ->  {r.step.filename}  ({r.seconds:.1f} s)")
        if r.detail:
            lines.append(f"            {r.detail}")
        if r.step.note and not elevated:
            lines.append(f"            note: {r.step.note}")
    return "\n".join(lines) + "\n"


def collect(dest_folder: Optional[str] = None,
            progress: Callable[[int, int, str], None] = lambda i, n, name: None,
            is_cancelled: Callable[[], bool] = lambda: False,
            plan: Optional[List[Step]] = None,
            run_step=_run_step) -> Bundle:
    """Run every step into a temporary folder, then zip it next to `dest_folder`.
    Cancelling stops after the current step; what was collected is still zipped."""
    import shutil
    import tempfile
    started = datetime.now()
    elevated = is_admin()
    plan = plan if plan is not None else steps()
    dest = dest_folder or default_folder()
    stem = f"SupportBundle_{socket.gethostname()}_{started:%Y%m%d-%H%M%S}"
    work = tempfile.mkdtemp(prefix="wct-bundle-")
    bundle = Bundle(os.path.join(dest, stem + ".zip"))
    try:
        for i, step in enumerate(plan):
            if is_cancelled():
                bundle.results.append(StepResult(step, CANCELLED, detail="cancelled before it ran"))
                continue
            progress(i, len(plan), step.name)
            bundle.results.append(run_step(step, os.path.join(work, step.filename), elevated,
                                           is_cancelled))
        bundle.manifest = manifest_text(bundle.results, elevated, started)
        with open(os.path.join(work, "MANIFEST.txt"), "w", encoding="utf-8") as f:
            f.write(bundle.manifest)
        progress(len(plan), len(plan), "Compressing")
        with zipfile.ZipFile(bundle.path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _dirs, files in os.walk(work):
                for name in files:
                    full = os.path.join(root, name)
                    z.write(full, os.path.relpath(full, work))
    finally:
        shutil.rmtree(work, ignore_errors=False, onerror=lambda f, p, e: logger.warning(
            "support bundle: could not remove temp %s: %s", p, e[1]))
    return bundle
