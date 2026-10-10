r"""Remove, clean up, install, update, modify -- and prove each one happened.

Qt-free. Every action ends in an ``Outcome`` decided by READING THE MACHINE
BACK, never by an exit code: ``Remove-AppxPackage`` and ``winget`` both exit
0 having done nothing (CLAUDE.md, Apps tab), and an uninstaller's exit code
says only that its window closed.

Removal scopes (Windows apps; desktop apps always run their own uninstaller):

* ``user``  -- Current user. ``Remove-AppxPackage -Package <full name>``.
* ``all``   -- All users. Adds ``-AllUsers`` and removes the provisioned copy,
  so new accounts do not get it either. Needs administrator.
* ``pc``    -- Entire PC, including files. All users, plus every profile's
  ``AppData\Local\Packages\<family>`` data folder. Needs administrator.

Locked files never stall a batch: the app is reported LOCKED with the
programs holding it, the batch moves on, and the caller asks the person what
to do -- close those programs and retry, remove it at the next sign-in /
restart, or skip it.

Deletion guards (cleanup of Orphaned / Defect rows deletes what no
uninstaller vouches for): a registry key is deleted only if it is an
Uninstall entry or a Windows Installer product registration, and is exported
to a .reg backup first; a folder only if it is at least two levels below a
drive root and outside the Windows directory, or is a package data folder
named like a real package family.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import subprocess
import time
import winreg
from datetime import datetime
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from core.windows_utils import ps_quote, system_drive, system_root

from . import model as m
from .windows_apps import is_family_name

logger = logging.getLogger(__name__)

SCOPE_USER, SCOPE_ALL, SCOPE_PC = "user", "all", "pc"
SCOPE_LABELS = {SCOPE_USER: "Current user", SCOPE_ALL: "All users",
                SCOPE_PC: "Entire PC — including files"}

REMOVED, SKIPPED, LOCKED, DEFERRED, INSTALLED, UPDATED, DONE, FAILED = (
    "removed", "skipped", "locked", "deferred", "installed", "updated", "done", "failed")

IN_USE = 0x80073D02
REASON_IN_USE = "A file is in use by another process"
REASON_PROTECTED = "Couldn't be removed"

Log = Callable[[str], None]
Cancelled = Callable[[], bool]


class Outcome:
    def __init__(self, rec: m.AppRecord, state: str, reason: str = "",
                 blockers: Sequence[Tuple[int, str]] = ()) -> None:
        self.rec, self.state, self.reason, self.blockers = rec, state, reason, list(blockers)

    @property
    def ok(self) -> bool:
        return self.state in (REMOVED, INSTALLED, UPDATED, DONE, DEFERRED)

    def __repr__(self) -> str:
        return f"Outcome({self.rec.name!r}, {self.state}, {self.reason!r})"


# ---- running things --------------------------------------------------------------------------

class Runner:
    """Process launching, isolated so tests can stand in for it."""

    def powershell(self, script: str, timeout: float = 180) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                  capture_output=True, text=True, timeout=timeout,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return 1, "", f"PowerShell did not finish within {timeout:.0f}s"
        except OSError as e:
            return 1, "", str(e)
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def run(self, args: List[str], timeout: float = 1800) -> Tuple[int, str]:
        try:
            proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return 1, f"{args[0]} did not finish within {timeout:.0f}s"
        except OSError as e:
            return 1, str(e)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def interactive(self, command: str, cancelled: Cancelled, timeout: float = 7200) -> int:
        """Run an uninstaller / modify command WITH its own windows, and wait
        for it and every process it started (NSIS copies itself to %TEMP% and
        exits at once; the real work is the child)."""
        # Registry-derived command from an Uninstall entry -- the same trust
        # level as the Software pane's Uninstall button (see CLAUDE.md,
        # shell=True discipline); never user-typed text.
        proc = subprocess.Popen(command, shell=True)
        return wait_tree(proc.pid, lambda: proc.poll() is not None, cancelled, timeout) or (proc.returncode or 0)

    def installed_families(self) -> Optional[set]:
        rc, out, err = self.powershell("Get-AppxPackage | Select-Object -ExpandProperty PackageFamilyName")
        names = {ln.strip().lower() for ln in out.splitlines() if ln.strip()}
        if rc != 0 or err.strip() or not names:
            return None            # an unread list is not an empty one
        return names

    def all_user_families(self) -> Optional[set]:
        rc, out, err = self.powershell(
            "Get-AppxPackage -AllUsers | Select-Object -ExpandProperty PackageFamilyName")
        names = {ln.strip().lower() for ln in out.splitlines() if ln.strip()}
        if rc != 0 or err.strip() or not names:
            return None
        return names


def wait_tree(pid: int, exited: Callable[[], bool], cancelled: Cancelled, timeout: float) -> int:
    """Wait for `pid` and every descendant it was seen to start."""
    try:
        import psutil
    except ImportError:
        psutil = None
    seen = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if psutil is not None:
            try:
                for child in psutil.Process(pid).children(recursive=True):
                    seen[child.pid] = child
            except psutil.Error as e:   # the parent is gone; its children are already in `seen`
                logger.debug("uninstaller %s no longer readable: %s", pid, e)
        alive = [c for c in seen.values() if _alive(c)]
        if exited() and not alive:
            return 0
        if cancelled():
            return 0               # stop waiting; an uninstaller's own window stays the person's
        time.sleep(0.5)
    return 1


def _alive(proc) -> bool:
    try:
        return proc.is_running() and proc.status() != "zombie"
    except Exception:              # psutil.NoSuchProcess / AccessDenied: gone or not ours to watch
        return False


# ---- who holds the files ---------------------------------------------------------------------

def blockers_under(folder: str) -> List[Tuple[int, str]]:
    """Running processes whose program lives under `folder`."""
    if not folder:
        return []
    try:
        import psutil
    except ImportError:
        return []
    root = os.path.normcase(os.path.abspath(folder)) + os.sep
    out = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        exe = proc.info.get("exe") or ""
        if exe and os.path.normcase(exe).startswith(root):
            out.append((proc.info["pid"], proc.info.get("name") or os.path.basename(exe)))
    return out


def close_programs(blockers: Iterable[Tuple[int, str]], log: Log) -> List[str]:
    """End the blocking programs (no save prompt -- the dialog says so).
    Returns the ones that could not be ended."""
    try:
        import psutil
    except ImportError:
        return [name for _pid, name in blockers]
    failed = []
    procs = []
    for pid, name in blockers:
        try:
            p = psutil.Process(pid)
            p.terminate()
            procs.append((p, name))
        except psutil.NoSuchProcess:
            logger.debug("%s (%s) had already exited", name, pid)
            continue
        except psutil.Error as e:
            failed.append(f"{name} ({pid}): {e}")
    _gone, still = psutil.wait_procs([p for p, _n in procs], timeout=5)
    for p in still:
        try:
            p.kill()
        except psutil.Error as e:
            failed.append(f"{p.pid}: {e}")
    for line in failed:
        log(f"could not end {line}")
    return failed


# ---- guards ----------------------------------------------------------------------------------

_KEY_ALLOWED = (
    re.compile(r"^HK(LM|CU)\\SOFTWARE\\(WOW6432Node\\)?Microsoft\\Windows\\CurrentVersion\\Uninstall\\[^\\]+$",
               re.I),
    re.compile(r"^HKLM\\SOFTWARE\\Classes\\Installer\\(Products|Features)\\[0-9A-F]{32}$", re.I),
    re.compile(r"^HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Installer\\UserData\\S-1-5-[0-9-]+"
               r"\\Products\\[0-9A-F]{32}$", re.I),
)


def key_allowed(key: str) -> bool:
    return any(p.match(key) for p in _KEY_ALLOWED)


def _resolved_path(path: str) -> str:
    full = os.path.realpath(os.path.expandvars(path))
    if full.lower().startswith("\\\\?\\unc\\"):
        full = "\\\\" + full[8:]
    elif full.startswith("\\\\?\\"):
        full = full[4:]
    return os.path.normcase(full)


def _profile_roots() -> Optional[List[str]]:
    roots = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList") as profiles:
            for index in range(winreg.QueryInfoKey(profiles)[0]):
                subkey = winreg.EnumKey(profiles, index)
                try:
                    with winreg.OpenKey(profiles, subkey) as profile:
                        path, _kind = winreg.QueryValueEx(profile, "ProfileImagePath")
                        if path:
                            roots.append(_resolved_path(path))
                except OSError as e:
                    logger.warning("cannot read profile root for subkey %s: %s", subkey, e)
    except OSError as e:
        logger.warning("cannot read profile roots: %s", e)
        return None
    return roots


def _has_link_ancestor(path: str) -> bool:
    current = os.path.abspath(path)
    while current:
        if os.path.isjunction(current) or os.path.islink(current):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return False


def folder_allowed(path: str) -> Tuple[bool, str]:
    """Is this a folder a cleanup may delete? (allowed, why not)."""
    if not path:
        return False, "no folder"
    full = _resolved_path(path)
    drive, rest = os.path.splitdrive(full)
    parts = [p for p in rest.split(os.sep) if p]
    windir = _resolved_path(system_root())
    if full == windir or full.startswith(windir + os.sep):
        return False, "inside the Windows directory"
    if _has_link_ancestor(path) or _has_link_ancestor(full):
        return False, "a junction or symbolic link in the path"
    roots = _profile_roots()
    if roots is None:
        return False, "the list of user profiles could not be read, so no folder can be checked against it"
    if any(root == full or root.startswith(full.rstrip(os.sep) + os.sep) for root in roots):
        return False, "a whole user profile or its parent"
    parent = os.path.basename(os.path.dirname(full))
    if parent == "packages" and is_family_name(os.path.basename(full)):
        return True, ""
    protected = {_resolved_path(os.environ[v]) if os.environ.get(v) else "" for v in
                 ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "USERPROFILE",
                  "LOCALAPPDATA", "APPDATA", "PUBLIC", "SystemDrive")}
    protected.discard("")
    if full in protected or len(parts) < 2:
        return False, "too close to the root of a drive or a system folder"
    users = os.path.normcase(os.path.join(drive + os.sep, "Users"))
    if os.path.dirname(full) == users:
        return False, "a whole user profile"
    return True, ""


# ---- registry --------------------------------------------------------------------------------

_HIVES = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}


def key_exists(key: str) -> Optional[bool]:
    hive, _, path = key.partition("\\")
    try:
        winreg.CloseKey(winreg.OpenKey(_HIVES[hive], path))
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning("cannot read registry key %s: %s", key, e)
        return None


def backup_key(key: str, folder: str, runner: Runner) -> Tuple[bool, str]:
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[-120:]
    target = os.path.join(folder, f"{datetime.now():%Y%m%d-%H%M%S}-{safe}.reg")
    rc, out = runner.run(["reg", "export", key, target, "/y"], timeout=60)
    if rc != 0 or not os.path.isfile(target):
        return False, out.strip() or "reg export failed"
    return True, target


def delete_key(key: str) -> Tuple[bool, str]:
    """RegDeleteTreeW on the key, then confirm it is gone."""
    if not key_allowed(key):
        return False, f"refused: {key} is not an uninstall or installer registration"
    hive, _, path = key.partition("\\")
    parent, _, leaf = path.rpartition("\\")
    try:
        with winreg.OpenKey(_HIVES[hive], parent, 0, winreg.KEY_ALL_ACCESS) as h:
            rc = ctypes.windll.advapi32.RegDeleteTreeW(ctypes.c_void_p(h.handle), leaf)
    except PermissionError:
        return False, "needs administrator"
    except OSError as e:
        return False, str(e)
    if rc == 5:
        return False, "needs administrator"
    if rc not in (0, 2):
        return False, f"RegDeleteTree failed ({rc})"
    gone = key_exists(key) is False
    return gone, "" if gone else "the key is still there or could not be read"


# ---- folders ---------------------------------------------------------------------------------

def delete_folder(path: str) -> Tuple[bool, List[str]]:
    """(gone, files that could not be deleted)."""
    ok, why = folder_allowed(path)
    if not ok:
        return False, [f"refused: {path} is {why}"]
    if not os.path.exists(path):
        return True, []
    stuck: List[str] = []

    def onerror(_fn, p, _exc):
        try:
            os.chmod(p, 0o666)
            os.remove(p)
        except OSError:
            stuck.append(p)

    shutil.rmtree(path, onerror=onerror)
    return not os.path.exists(path), stuck


MOVEFILE_DELAY_UNTIL_REBOOT = 0x4


def _restart_entries(path: str):
    with os.scandir(path) as entries:
        for entry in entries:
            if not entry.is_junction() and not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                yield from _restart_entries(entry.path)
            else:
                yield entry.path
    yield path


def delete_at_restart(path: str) -> Tuple[bool, str]:
    """Ask Windows to delete a folder's files, then the folders, at the next
    boot (PendingFileRenameOperations; needs administrator)."""
    ok, why = folder_allowed(path)
    if not ok:
        return False, f"refused: {path} is {why}"
    try:
        entries = list(_restart_entries(path))
    except OSError as e:
        logger.warning("cannot list restart deletion target %s: %s", path, e)
        return False, str(e)
    move = ctypes.windll.kernel32.MoveFileExW
    for p in entries:
        if not move(p, None, MOVEFILE_DELAY_UNTIL_REBOOT):
            err = ctypes.GetLastError()
            return False, "needs administrator" if err == 5 else f"MoveFileEx failed ({err})"
    return True, ""


def profile_data_folders(family: str) -> List[str]:
    """Every profile's AppData\\Local\\Packages\\<family>."""
    users = os.path.join(system_drive() + os.sep, "Users")
    out = []
    try:
        names = os.listdir(users)
    except OSError as e:
        logger.warning("cannot list %s: %s", users, e)
        return out
    for name in names:
        path = os.path.join(users, name, "AppData", "Local", "Packages", family)
        if os.path.isdir(path):
            out.append(path)
    return out


# ---- removal ---------------------------------------------------------------------------------

def _first_hresult(text: str) -> Optional[int]:
    from modules.store_apps.appx_errors import first_hresult
    return first_hresult(text)


def remove_windows_app(rec: m.AppRecord, scope: str, runner: Runner, log: Log) -> Outcome:
    if not rec.removable or not rec.full_name:
        return Outcome(rec, SKIPPED, REASON_PROTECTED + " (Windows protects this app).")
    before = runner.installed_families()
    present = None if before is None else rec.family.lower() in before
    script = f"Remove-AppxPackage -Package '{ps_quote(rec.full_name)}'"
    if scope in (SCOPE_ALL, SCOPE_PC):
        script += (f" -AllUsers; Get-AppxProvisionedPackage -Online | Where-Object DisplayName -eq "
                   f"'{ps_quote(rec.package_name)}' | Remove-AppxProvisionedPackage -Online -AllUsers")
    if scope in (SCOPE_ALL, SCOPE_PC) or present is not False:
        rc, out, err = runner.powershell(script)
        for line in (out + err).splitlines():
            if line.strip():
                log(line.rstrip())
        if _first_hresult(out + err) == IN_USE:
            return Outcome(rec, LOCKED, REASON_IN_USE, blockers_under(rec.install_location))
    after = runner.installed_families()
    if after is None:
        return Outcome(rec, SKIPPED, "Could not read the app list back, so the removal is unconfirmed.")
    if rec.family.lower() in after:
        blockers = blockers_under(rec.install_location)
        if blockers:
            return Outcome(rec, LOCKED, REASON_IN_USE, blockers)
        return Outcome(rec, SKIPPED, f"{REASON_PROTECTED}: it is still installed after the removal.")
    if scope in (SCOPE_ALL, SCOPE_PC):
        everyone = runner.all_user_families()
        if everyone is None:
            return Outcome(rec, SKIPPED, "Removed for you, but the all-users list could not be read, "
                           "so removal for other accounts is unconfirmed")
        if rec.family.lower() in everyone:
            return Outcome(rec, SKIPPED, "Removed for you, but still installed for another account.")
    if scope == SCOPE_PC:
        stuck = []
        for folder in profile_data_folders(rec.family):
            gone, left = delete_folder(folder)
            log(f"{'deleted' if gone else 'could not fully delete'} {folder}")
            stuck += left
        if stuck:
            return Outcome(rec, LOCKED, f"Removed; {len(stuck)} data file(s) are in use",
                           blockers_under(rec.install_location))
    return Outcome(rec, REMOVED)


def remove_desktop_app(rec: m.AppRecord, runner: Runner, log: Log, cancelled: Cancelled) -> Outcome:
    command = (f"msiexec /x {rec.product_code}" if rec.windows_installer and rec.product_code
               else rec.uninstall_string)
    if not command:
        return Outcome(rec, SKIPPED, "It has no uninstall command.")
    log(f"running its uninstaller: {command}")
    runner.interactive(command, cancelled)
    for _ in range(20):                       # some uninstallers clean the key a moment later
        registered = key_exists(rec.registry_key)
        if registered is False:
            return Outcome(rec, REMOVED)
        time.sleep(0.5)
    if registered is None:
        return Outcome(rec, SKIPPED, "Could not read the registry back, so the uninstall is unconfirmed")
    location_gone = bool(rec.install_location) and not os.path.exists(rec.install_location)
    from modules.startup_manager.persistence import resolve_command
    exe, _a = resolve_command(rec.uninstall_string)
    if location_gone and exe and not os.path.exists(exe):
        ok, why = _clean_keys([rec.registry_key], runner, log)
        if ok:
            log("the uninstaller left its Uninstall entry behind; cleared it")
            return Outcome(rec, REMOVED)
        return Outcome(rec, SKIPPED, f"Uninstalled, but its leftover entry could not be cleared: {why}")
    return Outcome(rec, SKIPPED, "The uninstaller finished but the app is still registered -- it may "
                                 "have been cancelled.")


def backup_folder() -> str:
    base = os.environ.get("APPDATA", "")
    return os.path.join(base, "WindowsTweaker", "app_buster", "registry-backups")


def _clean_keys(keys: Sequence[str], runner: Runner, log: Log) -> Tuple[bool, str]:
    for key in keys:
        if key_exists(key) is False:
            continue
        if not key_allowed(key):
            return False, f"refused to delete {key}"
        saved, where = backup_key(key, backup_folder(), runner)
        if not saved:
            return False, f"no backup of {key} could be made ({where}), so it was left alone"
        log(f"backed up {key} to {where}")
        ok, why = delete_key(key)
        if not ok:
            return False, why
        log(f"deleted {key}")
    return True, ""


def clean_up(rec: m.AppRecord, runner: Runner, log: Log) -> Outcome:
    """Remove an Orphaned / Defect entry's leftovers directly."""
    keys = [p for p in rec.leftover_paths if p.upper().startswith(("HKLM\\", "HKCU\\"))]
    folders = [p for p in rec.leftover_paths if p not in keys]
    ok, why = _clean_keys(keys, runner, log)
    if not ok:
        return Outcome(rec, SKIPPED, why)
    stuck: List[str] = []
    for folder in folders:
        gone, left = delete_folder(folder)
        log(f"{'deleted' if gone else 'could not fully delete'} {folder}")
        stuck += left
    if stuck:
        if any(s.startswith("refused") for s in stuck):
            return Outcome(rec, SKIPPED, stuck[0])
        return Outcome(rec, LOCKED, f"{len(stuck)} file(s) are in use",
                       blockers_under(folders[0] if folders else ""))
    return Outcome(rec, REMOVED)


def remove(rec: m.AppRecord, scope: str, runner: Runner, log: Log, cancelled: Cancelled) -> Outcome:
    try:
        if rec.type in (m.ORPHANED, m.DEFECT):
            return clean_up(rec, runner, log)
        if rec.type == m.DESKTOP:
            return remove_desktop_app(rec, runner, log, cancelled)
        return remove_windows_app(rec, scope, runner, log)
    except Exception as e:                      # one app's failure must not end the batch
        logger.error("removing %s failed", rec.name, exc_info=True)
        return Outcome(rec, FAILED, f"{type(e).__name__}: {e}")


# ---- locked: at the next sign-in / restart ---------------------------------------------------

RUNONCE = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"


def remove_at_restart(rec: m.AppRecord, scope: str) -> Outcome:
    """Windows apps: a RunOnce entry removes the package when you next sign in
    (before the app can start). Leftover folders: delete at the next boot."""
    if rec.type == m.WINDOWS and rec.full_name:
        command = ("powershell -NoProfile -WindowStyle Hidden -Command \"Remove-AppxPackage -Package "
                   f"'{ps_quote(rec.full_name)}'\"")
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUNONCE, 0, winreg.KEY_SET_VALUE) as k:
                name = "AppBuster_" + re.sub(r"[^A-Za-z0-9]", "", rec.package_name)[:40]
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, command)
        except OSError as e:
            return Outcome(rec, SKIPPED, f"Could not schedule the removal: {e}")
        if scope in (SCOPE_ALL, SCOPE_PC):
            return Outcome(rec, DEFERRED, "Will be removed for your account after restart; run the removal "
                           "again with All users after restarting to finish it for other accounts")
        return Outcome(rec, DEFERRED, "Will be removed after restart")
    folders = [p for p in rec.leftover_paths if not p.upper().startswith(("HKLM\\", "HKCU\\"))]
    for folder in folders:
        ok, why = delete_at_restart(folder)
        if not ok:
            return Outcome(rec, SKIPPED, f"Could not schedule the deletion: {why}")
    if not folders:
        return Outcome(rec, SKIPPED, "Nothing here can be deferred to a restart.")
    return Outcome(rec, DEFERRED, "Will be removed after restart")


# ---- install / update / modify ---------------------------------------------------------------

def install_windows_app(rec: m.AppRecord, runner: Runner, log: Log) -> Outcome:
    if not rec.family:
        return Outcome(rec, SKIPPED, "No package family to register.")
    rc, out, err = runner.powershell(
        f"Add-AppxPackage -RegisterByFamilyName -MainPackage '{ps_quote(rec.family)}'", timeout=600)
    for line in (out + err).splitlines():
        if line.strip():
            log(line.rstrip())
    after = runner.installed_families()
    if after is None:
        return Outcome(rec, SKIPPED, "Could not read the app list back, so the install is unconfirmed.")
    if rec.family.lower() in after:
        return Outcome(rec, INSTALLED)
    return Outcome(rec, FAILED, (err or out).strip().splitlines()[-1] if (err or out).strip()
                   else "Windows did not register it.")


def parse_list_row(output: str, winget_id: str) -> Optional[Tuple[str, str]]:
    """Read installed/available versions from an exact ID's table columns."""
    columns = None
    for line in output.splitlines():
        labels = list(re.finditer(r"\S+", line))
        names = [label.group() for label in labels]
        if names[:3] == ["Name", "Id", "Version"]:
            columns = {label.group(): label.start() for label in labels}
            continue
        if columns is None:
            continue
        version_start = columns["Version"]
        if line[columns["Id"]:version_start].strip() != winget_id:
            continue
        available_start = columns.get("Available", columns.get("Source", len(line)))
        installed = line[version_start:available_start].strip()
        available = (line[available_start:columns.get("Source", len(line))].strip()
                     if "Available" in columns else "")
        return (installed, available) if installed else None
    return None


def update_app(rec: m.AppRecord, runner: Runner, log: Log) -> Outcome:
    if not rec.winget_id:
        return Outcome(rec, SKIPPED, "winget offers no update for it.")
    rc, out = runner.run(["winget", "upgrade", "--id", rec.winget_id, "--exact",
                          "--accept-source-agreements", "--accept-package-agreements",
                          "--disable-interactivity", "--include-unknown"], timeout=3600)
    for line in out.splitlines()[-15:]:
        if line.strip():
            log(line.rstrip())
    if "cannot be uninstalled when running with administrator privileges" in out.lower():
        return Outcome(rec, SKIPPED, "Installed for your account only, and winget will not update it "
                       "from an app running as administrator. Start the app without admin to update it.")
    if "no applicable upgrade found" in out.lower():
        return Outcome(rec, SKIPPED, "winget found no update that applies to this PC.")
    rc2, listed = runner.run(["winget", "list", "--id", rec.winget_id, "--exact",
                              "--disable-interactivity", "--accept-source-agreements"], timeout=120)
    row = parse_list_row(listed, rec.winget_id) if rc2 == 0 else None
    if row is None:
        return Outcome(rec, SKIPPED, "Could not read the installed version back, so the update is unconfirmed.")
    installed, available = row
    if installed.casefold() == "unknown" or installed[:1] in "<>":
        # Windows records no exact version for it, and an update does not add one.
        return Outcome(rec, SKIPPED, "Windows records no exact version for it, so the update is unconfirmed.")
    if rec.update and _same_version(installed, rec.update):
        return Outcome(rec, UPDATED, f"Now {installed}")
    if not available:
        # No newer version listed is not proof: the source may simply not have answered.
        return Outcome(rec, SKIPPED, f"{installed} is installed, not {rec.update or 'the new version'}; "
                       "the update is unconfirmed.")
    return Outcome(rec, FAILED, f"winget finished (exit {rc}), but {installed} is still installed "
                   f"({available} available).")


def _same_version(a: str, b: str) -> bool:
    """"v2.0", "2.0" and "2.0.0" are one version; anything non-numeric compares as text."""
    def parts(v):
        v = v.strip().casefold().removeprefix("v")
        if not re.fullmatch(r"\d+(\.\d+)*", v):
            return v
        nums = [int(p) for p in v.split(".")]
        while len(nums) > 1 and nums[-1] == 0:
            nums.pop()
        return tuple(nums)
    return parts(a) == parts(b)


def modify_app(rec: m.AppRecord, runner: Runner, log: Log, cancelled: Cancelled) -> Outcome:
    if not rec.modify_path:
        return Outcome(rec, SKIPPED, "Its installer offers no repair or change.")
    log(f"running: {rec.modify_path}")
    runner.interactive(rec.modify_path, cancelled)
    return Outcome(rec, DONE, "The installer's own Modify finished.")


# ---- the report ------------------------------------------------------------------------------

def summary(outcomes: Sequence[Outcome]) -> str:
    removed = [o for o in outcomes if o.state == REMOVED]
    deferred = [o for o in outcomes if o.state == DEFERRED]
    skipped = [o for o in outcomes if not o.ok]
    if not skipped and not deferred:
        return "App removed" if len(removed) == 1 else f"All {len(removed)} apps removed"
    text = f"{len(removed)} removed · {len(skipped)} skipped"
    if deferred:
        text += f" · {len(deferred)} after restart"
    return text


def report_text(outcomes: Sequence[Outcome], scope: str, when: Optional[datetime] = None) -> str:
    lines = [f"App Buster removal report -- {(when or datetime.now()):%Y-%m-%d %H:%M}",
             f"Scope: {SCOPE_LABELS.get(scope, scope)}", f"Result: {summary(outcomes)}", ""]
    for title, states in (("Removed", (REMOVED,)), ("Will be removed after restart", (DEFERRED,)),
                          ("Skipped", (SKIPPED, LOCKED, FAILED))):
        group = [o for o in outcomes if o.state in states]
        if not group:
            continue
        lines.append(f"{title} ({len(group)}):")
        for o in group:
            ident = o.rec.full_name or o.rec.registry_key or o.rec.product_code or o.rec.family
            lines.append(f"  {o.rec.name}  [{o.rec.type}]  {ident}")
            if o.reason:
                lines.append(f"      {o.reason}")
            for pid, name in o.blockers:
                lines.append(f"      held by {name} (PID {pid})")
        lines.append("")
    return "\n".join(lines)
