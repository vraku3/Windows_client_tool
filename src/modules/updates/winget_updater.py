import logging
import os
import re
import shutil
import subprocess
import threading
import time
import glob
from datetime import datetime
from dataclasses import dataclass
from typing import List, Callable, Optional

from core import winget_list
from core.blocklist import is_blocked

logger = logging.getLogger(__name__)


@dataclass
class AppUpdate:
    name: str
    winget_id: str
    installed_version: str
    available_version: str
    source: str


def _run_winget(
    args: List[str],
    output_cb: Optional[Callable[[str], None]] = None,
    is_cancelled: Callable[[], bool] = lambda: False,
    returncode_out: Optional[List[Optional[int]]] = None,
    timeout_s: Optional[float] = None,
) -> str:
    """Run winget, stream output via output_cb, return full output.

    `worker.is_cancelled` was only ever checked BETWEEN winget calls (see
    `updates_module.py`'s per-package loops) -- a single stuck call (a
    network stall, an install silently waiting on an interactive prompt)
    left "Update All"'s Cancel button inert until that one call resolved on
    its own. A side thread polls `is_cancelled` and kills the process tree
    if it fires, the same pattern `windows_features/features_module.py`'s
    `_run_dism_action` uses for the identical shape of bug.

    **`--disable-interactivity` is forced onto EVERY call here**, so no
    subcommand can ever block on a prompt. Measured 2026-08-15: an
    unattended run of `winget upgrade --id Blizzard.BattleNet` printed
    "Specify the install root:" and died with `0x8a150042 : Error reading
    input in prompt`, because that package needs an install location and
    winget asked a question no caller in a headless run can answer. With
    the flag, winget refuses cleanly with a message `winget_list.refusal`
    can read and report, instead of hanging or half-installing.
    """
    args = list(args)
    if "--disable-interactivity" not in args:
        args.append("--disable-interactivity")
    try:
        proc = subprocess.Popen(
            ["winget"] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stop_watching = threading.Event()

        deadline = time.monotonic() + timeout_s if timeout_s else None

        def _watch_for_cancel() -> None:
            while not stop_watching.wait(0.5):
                timed_out = deadline is not None and time.monotonic() > deadline
                if timed_out:
                    logger.error("winget %s still running after %ss -- ending it",
                                 " ".join(args[:3]), timeout_s)
                if is_cancelled() or timed_out:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    return

        watcher = threading.Thread(target=_watch_for_cancel, daemon=True)
        watcher.start()
        lines = []
        try:
            for line in proc.stdout:
                line = line.rstrip()
                lines.append(line)
                if output_cb:
                    output_cb(line)
            proc.wait()
        finally:
            stop_watching.set()
        if returncode_out is not None:
            returncode_out.append(proc.returncode)
        return "\n".join(lines)
    except FileNotFoundError:
        if returncode_out is not None:
            returncode_out.append(None)
        msg = "winget not found. Please install App Installer from the Microsoft Store."
        if output_cb:
            output_cb(msg)
        return msg
    except Exception as e:
        if returncode_out is not None:
            returncode_out.append(None)
        msg = f"Error running winget: {e}"
        if output_cb:
            output_cb(msg)
        return msg


def fetch_updates(patterns: Optional[List[str]] = None) -> List[AppUpdate]:
    """Run winget upgrade --include-unknown, parse and return list of updates.

    patterns: optional blocklist patterns (id/name/source-agnostic) — matching
    packages are excluded from the result.
    """
    rc: List[Optional[int]] = []
    output = _run_winget(["upgrade", "--include-unknown", "--accept-source-agreements"],
                         returncode_out=rc)
    if not rc or rc[0] != 0:
        # winget missing, failed to start, or refused: a failed scan, never
        # "everything is up to date" (measured: a working listing exits 0).
        tail = " ".join(output.strip().splitlines()[-2:])[:300]
        raise RuntimeError(f"winget could not list upgrades (exit {rc[0] if rc else 'n/a'}): {tail}")
    updates = _parse_upgrade_output(output)
    if patterns:
        updates = [u for u in updates if not is_blocked(u.name, u.winget_id, patterns)]
    return updates


def _parse_upgrade_output(output: str) -> List[AppUpdate]:
    """Parse winget upgrade output table into AppUpdate list."""
    updates = []
    lines = output.splitlines()

    # Find the header line (contains "Name" and "Id" and "Version" etc.)
    header_idx = -1
    for i, line in enumerate(lines):
        if "Name" in line and "Id" in line and "Version" in line:
            header_idx = i
            break

    if header_idx < 0:
        return updates

    # Find separator line (dashes)
    sep_idx = -1
    for i in range(header_idx + 1, min(header_idx + 3, len(lines))):
        if lines[i].strip().startswith("-"):
            sep_idx = i
            break

    if sep_idx < 0:
        return updates

    # Determine column positions from header
    header = lines[header_idx]
    # Typical columns: Name, Id, Version, Available, Source
    col_name = header.index("Name") if "Name" in header else 0
    col_id = header.index("Id") if "Id" in header else 30
    col_ver = header.index("Version") if "Version" in header else 60
    col_avail = header.index("Available") if "Available" in header else 80
    col_source = header.index("Source") if "Source" in header else 100

    for line in lines[sep_idx + 1:]:
        if not line.strip() or line.strip().startswith("-"):
            continue
        # Skip summary lines
        if "upgrades available" in line.lower():
            continue

        def _extract(start: int, end: int) -> str:
            if start >= len(line):
                return ""
            return line[start:end].strip() if end <= len(line) else line[start:].strip()

        name = _extract(col_name, col_id)
        winget_id = _extract(col_id, col_ver)
        installed = _extract(col_ver, col_avail)
        available = _extract(col_avail, col_source)
        source = _extract(col_source, len(line))

        if winget_id and available and available != "Unknown":
            updates.append(AppUpdate(
                name=name, winget_id=winget_id,
                installed_version=installed, available_version=available,
                source=source,
            ))

    return updates


@dataclass
class UpdateOutcome:
    """What ONE package's update did, decided by reading its version back.

    `state` is one of core.winget_list's UPDATED / UNCONFIRMED / NOT_APPLICABLE /
    NEEDS_USER / NEEDS_INPUT / FAILED; `detail` is the sentence the table and the
    report show.
    Truthy only when the read-back proved the update.
    """
    winget_id: str
    state: str
    detail: str

    def __bool__(self) -> bool:
        return self.state == winget_list.UPDATED


#: An install still running after this long is stuck, not slow.
INSTALL_TIMEOUT_S = 45 * 60


def _arp_entries():
    """Only machine uninstall entries, in both registry views."""
    import winreg
    root = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root, 0, winreg.KEY_READ | view) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    name = winreg.EnumKey(key, i)
                    try:
                        with winreg.OpenKey(key, name, 0, winreg.KEY_READ | view) as sub:
                            row = {"key": name}
                            for field in ("InstallLocation", "WinGetPackageIdentifier", "DisplayName"):
                                try:
                                    row[field] = str(winreg.QueryValueEx(sub, field)[0])
                                except FileNotFoundError:
                                    row[field] = ""
                            yield row
                    except OSError:
                        logger.debug("Uninstall entry %s unreadable", name, exc_info=True)
        except OSError:
            logger.debug("Uninstall hive/view unreadable", exc_info=True)


def find_install_location(winget_id: str, package_name: str = "") -> Optional[str]:
    """An exact machine ARP identity inside a real Program Files folder."""
    roots = [os.path.normcase(os.path.realpath(value)) for name in
             ("ProgramFiles", "ProgramFiles(x86)") if (value := os.environ.get(name))]
    matches = []
    for row in _arp_entries():
        if not (any(row.get(field, "").casefold() == winget_id.casefold()
                    for field in ("key", "WinGetPackageIdentifier")) or
                (package_name and row.get("DisplayName", "").casefold() == package_name.casefold())):
            continue
        location = os.path.expandvars(row.get("InstallLocation", "")).strip().strip('"')
        if not location or not os.path.isabs(location) or not os.path.isdir(location):
            continue
        try:
            real = os.path.normcase(os.path.realpath(location))
            if any(os.path.commonpath([real, root]) == root and real != root for root in roots):
                matches.append(real)
        except (OSError, ValueError):
            logger.warning("Cannot validate install location %s", location, exc_info=True)
    return matches[0] if len(matches) == 1 else None


def _exclusive_available(path: str) -> bool:
    """Probe a staged file with zero sharing; never modify or delete it."""
    import win32file
    import win32con
    import pywintypes
    try:
        handle = win32file.CreateFile(path, win32con.GENERIC_READ, 0, None,
                                      win32con.OPEN_EXISTING, 0, None)
        handle.Close()
        return True
    except (OSError, pywintypes.error):
        return not os.path.exists(path)


def _wait_staged_installer(winget_id: str, version: str, log: Callable[[str], None]) -> bool:
    # Version text comes from a table, never use it as a glob/path token.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,199}", winget_id):
        log("Staged installer wait refused: invalid package identifier")
        return False
    root = os.path.join(os.environ.get("TEMP", ""), "WinGet")
    # Match all this package's version directories, including winget's
    # normalized four-component version (which can differ from the scan).
    started = time.monotonic()
    while True:
        unreadable = []
        files = [os.path.join(base, name) for folder in glob.glob(os.path.join(root, winget_id + ".*"))
                 for base, _dirs, names in os.walk(folder, onerror=unreadable.append) for name in names]
        released = not unreadable and all(_exclusive_available(path) for path in files)
        waited = time.monotonic() - started
        if released:
            log(f"Staged installer released after {waited:.0f} s")
            return True
        if waited >= 60:
            break
        time.sleep(min(1, 60 - waited))
    log("Staged installer still held after 60 s; LIMITED retry not launched")
    return False


def _ps_literal(token: str) -> str:
    # PowerShell recognises typographic quotes too. Refuse these in executable,
    # location and generated output paths; ASCII apostrophes are doubled.
    if any(c in token for c in "\u2018\u2019\u201a\u201b\r\n\x00"):
        raise ValueError("unsupported character in task token")
    return "'" + token.replace("'", "''") + "'"


def _diagnostic_tail(start: float, end: float) -> str:
    root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages",
                        "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe", "LocalState", "DiagOutputDir")
    try:
        paths = [p for p in glob.glob(os.path.join(root, "WinGet-*.log"))
                 if start <= os.path.getctime(p) <= end]
        if not paths:
            return "winget diagnostic log: no log created during the task window"
        path = max(paths, key=os.path.getmtime)
        with open(path, "rb") as stream:
            stream.seek(max(0, os.path.getsize(path) - 65536))
            tail = stream.read(65536).decode("utf-8-sig", errors="replace").splitlines()[-40:]
        return "winget diagnostic log: " + path + "\n" + "\n".join(tail)
    except OSError as exc:
        logger.warning("Could not read winget diagnostic log", exc_info=True)
        return f"winget diagnostic log: could not check ({exc})"


def _run_user_upgrade(winget_id: str, location: Optional[str] = None,
                      output_cb: Optional[Callable[[str], None]] = None) -> tuple:
    """(exit code, task reason, capped winget log) from the LIMITED retry."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,199}", winget_id):
        return None, "package identifier cannot be used in an unelevated task", "no output captured"
    executable = shutil.which("winget")
    if not executable:
        return None, "winget executable could not be located for the unelevated task", "no output captured"
    from core.limited_task import run_limited
    try:
        folder = os.path.join(os.environ["APPDATA"], "WindowsTweaker", "updates")
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        log_path = os.path.join(folder, f"winget-user-{winget_id}-{stamp}.log")
        args = ["upgrade", "--id", winget_id, "--exact", "--silent", "--include-unknown",
                "--scope", "user", "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity"]
        if location:
            args += ["--location", location]
        logger.info("Unelevated winget log: %s", log_path)
        if output_cb:
            output_cb(f"Unelevated winget log: {log_path}")
        from core.system_paths import powershell_exe
        script = ("& " + " ".join(_ps_literal(token) for token in [executable] + args)
                  + " *>&1 | Out-File -Encoding utf8 " + _ps_literal(log_path) + "; exit $LASTEXITCODE")
        started = time.time()
        rc, reason = run_limited(os.path.abspath(powershell_exe()),
                                  ["-NoProfile", "-NonInteractive", "-Command", script], timeout_s=600)
        diagnostic = _diagnostic_tail(started, time.time())
        logger.info("%s", diagnostic)
        if output_cb:
            output_cb(diagnostic)
        output = ""
        try:
            with open(log_path, "rb") as log:
                bom = log.read(2)
                log.seek(0, os.SEEK_END)
                offset = max(0, log.tell() - (64 * 1024 - 2))
                encoding = {b"\xff\xfe": "utf-16-le", b"\xfe\xff": "utf-16-be"}.get(bom, "utf-8-sig")
                if encoding.startswith("utf-16"):
                    offset += offset % 2
                log.seek(offset)
                # Final refusal/restart wording is near the end of verbose
                # logs. Including the BOM read, read no more than 64 KiB.
                raw = log.read(64 * 1024 - 2)
            output = raw.decode(encoding, errors="replace")
        except OSError:
            logger.warning("Could not read unelevated winget log %s", log_path, exc_info=True)
        output = output.strip() or "no output captured"
        if output_cb:
            output_cb(output)
        return rc, reason, output
    except (OSError, KeyError, ValueError) as exc:
        logger.warning("Could not prepare unelevated winget log", exc_info=True)
        return None, f"unelevated task log unavailable: {exc}", "no output captured"


def install_update(
    winget_id: str,
    output_cb: Callable[[str], None],
    is_cancelled: Callable[[], bool] = lambda: False,
    target_version: str = "",
    before_version: str = "",
    verify: bool = True,
    package_name: str = "",
) -> UpdateOutcome:
    """Upgrade one package, then read `winget list` back to decide what happened.

    The upgrade's own words are not evidence: winget prints "Successfully
    installed" while nothing changed and "No applicable upgrade found" for a
    package it still lists as upgradable.
    """
    # A running installer is never killed: a cancel stops the batch BEFORE
    # the next package (the caller checks between packages). Killing winget's
    # tree mid-install can leave an application half-written.
    args = ["upgrade", "--id", winget_id, "--exact", "--silent", "--include-unknown",
            "--accept-source-agreements", "--accept-package-agreements"]
    upgrade_rc = []
    location = None
    user_retry = False
    portable_before = winget_list.portable_snapshot(winget_id)
    def outcome(state: str, detail: str, version: str = "") -> UpdateOutcome:
        snapshot = portable_before + winget_list.portable_snapshot(winget_id, "user" if user_retry else "")
        if user_retry:
            snapshot = [row for row in snapshot if row[0] == "user"]
        problem = winget_list.portable_problem(snapshot, winget_id, version or target_version)
        if problem and state != winget_list.STILL_RUNNING:
            state, detail = winget_list.BROKEN, problem
        if user_retry and output == "no output captured":
            detail += "; no output captured"
        return UpdateOutcome(winget_id, state, detail)
    output = _run_winget(
        args,
        returncode_out=upgrade_rc,
        output_cb=output_cb,
        # Never killed on Cancel; ended only if it is clearly stuck (an
        # installer waiting on a hidden dialog). The read-back then decides.
        timeout_s=INSTALL_TIMEOUT_S,
    )
    if winget_list.needs_install_location(output):
        location = (find_install_location(winget_id, package_name) if package_name
                    else find_install_location(winget_id))
        if location:
            output_cb(f"Retrying with the installed folder: {location}")
            upgrade_rc.clear()
            output = _run_winget(args + ["--location", location], output_cb=output_cb,
                                 returncode_out=upgrade_rc, timeout_s=INSTALL_TIMEOUT_S)
    if winget_list.PER_USER_REFUSAL in output.lower():
        if not _wait_staged_installer(winget_id, target_version, output_cb):
            return outcome(winget_list.NEEDS_USER, "Staged installer still held after 60 s; LIMITED retry not launched")
        output_cb("Retrying for the current user through a LIMITED one-shot task...")
        user_retry = True
        rc, reason, output = _run_user_upgrade(winget_id, location, output_cb)
        if rc is None:
            state = winget_list.STILL_RUNNING if reason.startswith("STILL RUNNING:") else winget_list.UNCONFIRMED
            return outcome(state, reason)
        upgrade_rc = [rc]
        output_cb(f"Unelevated winget finished (exit {rc}).")
    refused = winget_list.refusal(output)
    if refused:
        return outcome(*refused)
    if not verify:
        return outcome(winget_list.UNCONFIRMED,
                             "Not checked: verification after update is turned off in Settings.")
    list_rc = []
    list_args = ["list", "--id", winget_id, "--exact",
                 "--accept-source-agreements", "--disable-interactivity"]
    if user_retry:
        list_args += ["--scope", "user"]
    listed = _run_winget(list_args, returncode_out=list_rc)
    state, detail = winget_list.verdict(list_rc[0] if list_rc else 0, listed, winget_id, target_version,
                                        before=before_version, upgrade_output=output,
                                        upgrade_rc=upgrade_rc[0] if upgrade_rc else None)
    row = winget_list.parse_list_row(listed, winget_id)
    return outcome(state, detail, row[0] if row else "")


def install_all_updates(
    output_cb: Callable[[str], None],
    is_cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """Run winget upgrade --all."""
    _run_winget(
        ["upgrade", "--all", "--silent", "--accept-source-agreements",
         "--accept-package-agreements", "--include-unknown"],
        output_cb=output_cb, is_cancelled=is_cancelled,
    )


def show_package_details(winget_id: str, output_cb: Callable[[str], None]) -> None:
    """Run `winget show --id <id> --exact`, streaming output via output_cb."""
    _run_winget(
        ["show", "--id", winget_id, "--exact", "--accept-source-agreements"],
        output_cb=output_cb,
    )
