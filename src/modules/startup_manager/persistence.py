"""Everything that runs at logon or boot, in one list -- Qt-free.

Sources read: HKCU/HKLM Run and the 32-bit Run key, RunOnce, both Startup
folders, scheduled tasks with a logon or boot trigger, automatic services,
the Winlogon Shell/Userinit values when they are not the defaults, Image
File Execution Options Debugger hijacks, and a populated + active
AppInit_DLLs list.

Each item is enriched with: the executable it resolves to, whether that file
exists, its Authenticode/catalog trust (`trust.py`), the publisher, when this
tool first saw it, and how much Windows' own boot trace says it slowed a boot
(Diagnostics-Performance events 101/103).

Notes are FINDINGS with a reason, not verdicts: "runs from a per-user
folder" describes where the file is; plenty of honest software lives there.

A source we could not read is reported in `Inventory.problems`, never as an
empty result.
"""
import csv
import io
import json
import logging
import os
import shutil
import winreg
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from modules.startup_manager import trust as trustlib
from core.windows_utils import program_data, system_root

logger = logging.getLogger(__name__)

RECENT_DAYS = 7
HISTORY_CAP = 200

_CV = r"SOFTWARE\Microsoft\Windows\CurrentVersion"
_APPROVED = _CV + r"\Explorer\StartupApproved"

#: Programs that run other programs. Not bad; worth knowing when THEY are the
#: startup command, because what actually runs is in the arguments.
LOLBINS = {"powershell.exe", "pwsh.exe", "cmd.exe", "wscript.exe", "cscript.exe",
           "mshta.exe", "rundll32.exe", "regsvr32.exe", "msiexec.exe",
           "certutil.exe", "bitsadmin.exe", "wmic.exe", "curl.exe", "forfiles.exe",
           "msbuild.exe", "installutil.exe", "cmstp.exe"}

SUSPICIOUS_ARGS = ("-enc", "-encodedcommand", "frombase64string", "downloadstring",
                   "invoke-expression", "iex ", "http://", "https://", "-windowstyle hidden",
                   "-w hidden", "bypass")


@dataclass
class Note:
    code: str          # missing | unsigned | invalid | userdir | tempdir | lolbin | args | recent | sigunknown | winlogon
    severity: str      # "warn" | "info"
    text: str


@dataclass
class Item:
    name: str
    command: str
    source: str            # "Run", "Run (32-bit)", "RunOnce", "Startup folder", "Scheduled task", "Service",
                           # "Winlogon", "IFEO", "AppInit"
    scope: str             # "User" | "Machine"
    location: str          # registry key / folder / task folder
    enabled: Optional[bool] = True
    exe: str = ""
    exists: Optional[bool] = None
    trust: str = trustlib.UNKNOWN
    trust_reason: Optional[str] = None
    publisher: Optional[str] = None
    first_seen: Optional[datetime] = None
    baseline: bool = False
    impact_ms: Optional[int] = None
    impact_count: int = 0
    extra: str = ""
    notes: List[Note] = field(default_factory=list)

    @property
    def key(self) -> str:
        return "|".join((self.source, self.scope, self.location, self.name)).lower()

    @property
    def is_microsoft(self) -> bool:
        return bool(self.publisher) and "microsoft" in self.publisher.lower() \
            and self.trust in (trustlib.SIGNED, trustlib.SIGNED_CATALOG)

    @property
    def flagged(self) -> bool:
        return any(n.severity == "warn" for n in self.notes)

    def has(self, code: str) -> bool:
        return any(n.code == code for n in self.notes)


@dataclass
class Inventory:
    items: List[Item] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


# ---- command -> executable -------------------------------------------------

def resolve_command(command: str) -> Tuple[str, str]:
    """`(exe path, arguments)`. The path is "" when nothing could be made of it.

    Handles quoted paths, unquoted paths containing spaces (the longest
    prefix that names a file wins), env vars, a missing ".exe", and bare names
    found on PATH. A path that does not exist is still returned -- that is
    the "missing file" finding.
    """
    text = os.path.expandvars((command or "").strip()).replace('""', '"')
    if not text:
        return "", ""
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1:].strip()
    words = text.split(" ")
    for count in range(len(words), 0, -1):
        candidate = " ".join(words[:count])
        for guess in (candidate, candidate + ".exe"):
            if os.path.isfile(guess):
                return guess, " ".join(words[count:]).strip()
    first = words[0]
    found = shutil.which(first)
    if found:
        return found, " ".join(words[1:]).strip()
    return first, " ".join(words[1:]).strip()


# ---- StartupApproved ---------------------------------------------------------

def read_approved(hive: int, subkey: str) -> Optional[Dict[str, bool]]:
    """name(lowercase) -> disabled?  {} when the key does not exist; None when refused."""
    result: Dict[str, bool] = {}
    try:
        with winreg.OpenKey(hive, subkey) as key:
            index = 0
            while True:
                try:
                    name, data, _kind = winreg.EnumValue(key, index)
                except OSError:
                    logger.debug("StartupApproved enumeration finished at %d", index)
                    break          # end of enumeration
                index += 1
                if isinstance(data, bytes) and data:
                    result[name.lower()] = bool(data[0] & 1)
    except FileNotFoundError:
        return {}
    except OSError as error:
        logger.warning("StartupApproved %s unreadable: %s", subkey, error)
        return None
    return result


def _disabled(name: str, *maps: Optional[Dict[str, bool]]) -> Optional[bool]:
    """Disabled in any readable map; None only when every map was refused."""
    readable = [m for m in maps if m is not None]
    if not readable:
        return None
    return any(m.get(name.lower(), False) for m in readable)


def _read_values(hive: int, subkey: str) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    out: List[Tuple[str, str]] = []
    try:
        with winreg.OpenKey(hive, subkey) as key:
            index = 0
            while True:
                try:
                    name, data, _kind = winreg.EnumValue(key, index)
                except OSError:
                    logger.debug("%s enumeration finished at %d", subkey, index)
                    break
                index += 1
                out.append((name, str(data)))
    except FileNotFoundError:
        return [], None
    except OSError as error:
        return [], f"{subkey}: {error}"
    return out, None


def read_run_keys(inv: Inventory) -> None:
    hkcu, hklm = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE
    user_ok = read_approved(hkcu, _APPROVED + r"\Run")
    mach_ok = read_approved(hklm, _APPROVED + r"\Run")
    m32_ok = read_approved(hklm, _APPROVED + r"\Run32")
    spec = [
        ("Run", "User", hkcu, "HKCU\\" + _CV + r"\Run", _CV + r"\Run", (user_ok,)),
        ("Run", "Machine", hklm, "HKLM\\" + _CV + r"\Run", _CV + r"\Run", (mach_ok, user_ok)),
        ("Run (32-bit)", "Machine", hklm, r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", (m32_ok, user_ok)),
        ("RunOnce", "User", hkcu, "HKCU\\" + _CV + r"\RunOnce", _CV + r"\RunOnce", ()),
        ("RunOnce", "Machine", hklm, "HKLM\\" + _CV + r"\RunOnce", _CV + r"\RunOnce", ()),
    ]
    for source, scope, hive, label, subkey, maps in spec:
        values, problem = _read_values(hive, subkey)
        if problem:
            inv.problems.append(problem)
        for name, data in values:
            enabled = None if not maps else _disabled(name, *maps)
            state = True if not maps else (None if enabled is None else not enabled)
            inv.items.append(Item(name, data, source, scope, label, enabled=state))
    for label, approved in (("HKCU StartupApproved\\Run", user_ok), ("HKLM StartupApproved\\Run", mach_ok)):
        if approved is None:
            inv.problems.append(f"{label} could not be read: enabled/disabled state for those Run entries is unknown")


def startup_folders() -> List[Tuple[str, str]]:
    appdata = os.environ.get("APPDATA", "")
    programdata = program_data()
    tail = os.path.join("Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    return [("User", os.path.join(appdata, tail)), ("Machine", os.path.join(programdata, tail))]


def lnk_target(path: str) -> Optional[str]:
    """Shortcut target via WScript.Shell, or None when it cannot be resolved."""
    if not path.lower().endswith(".lnk"):
        return path
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortCut(path)
        target = link.TargetPath
        if not target:
            return None
        args = link.Arguments
        return f'"{target}" {args}'.strip()
    except Exception as error:  # noqa: BLE001 - COM raises pywintypes.com_error
        logger.warning("Could not resolve shortcut %s: %s", path, error)
        return None


def read_startup_folders(inv: Inventory) -> None:
    user_ok = read_approved(winreg.HKEY_CURRENT_USER, _APPROVED + r"\StartupFolder")
    mach_ok = read_approved(winreg.HKEY_LOCAL_MACHINE, _APPROVED + r"\StartupFolder")
    for scope, folder in startup_folders():
        if not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError as error:
            inv.problems.append(f"{folder}: {error}")
            continue
        maps = (user_ok,) if scope == "User" else (mach_ok, user_ok)
        for fname in names:
            if fname.lower() == "desktop.ini":
                continue
            full = os.path.join(folder, fname)
            disabled = _disabled(fname, *maps)
            target = lnk_target(full)
            command = target if target else full
            item = Item(fname, command, "Startup folder", scope, folder,
                        enabled=None if disabled is None else not disabled)
            if target is None:
                item.extra = "shortcut target could not be resolved"
            inv.items.append(item)


def read_tasks(inv: Inventory) -> None:
    """Logon/boot scheduled tasks. Needs COM initialised on this thread."""
    try:
        import win32com.client
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
    except Exception as error:  # noqa: BLE001
        logger.warning("Task Scheduler unavailable: %s", error)
        inv.problems.append(f"Scheduled tasks could not be read: {error}")
        return
    unreadable = [0]

    def walk(folder) -> None:
        for task in folder.GetTasks(1):
            try:
                _add_task(inv, folder.Path, task)
            except Exception as error:  # noqa: BLE001
                unreadable[0] += 1
                logger.debug("Task %s unreadable: %s", getattr(task, "Name", "?"), error)
        for sub in folder.GetFolders(0):
            walk(sub)

    walk(service.GetFolder("\\"))
    if unreadable[0]:
        inv.problems.append(f"{unreadable[0]} scheduled task(s) could not be read (protected definitions)")


def _add_task(inv: Inventory, folder_path: str, task) -> None:
    definition = task.Definition
    kinds = [definition.Triggers.Item(i + 1).Type for i in range(definition.Triggers.Count)]
    when = [label for code, label in ((8, "boot"), (9, "logon")) if code in kinds]
    if not when:
        return
    command = ""
    for i in range(definition.Actions.Count):
        action = definition.Actions.Item(i + 1)
        if getattr(action, "Type", 0) == 0:            # TASK_ACTION_EXEC
            path = str(action.Path).strip().strip('"')   # the XML often carries its own quotes
            command = f'"{path}" {action.Arguments}'.strip() if " " in path \
                else f"{path} {action.Arguments}".strip()
            break
    scope = "User" if str(getattr(definition.Principal, "UserId", "")).lower() not in (
        "system", "nt authority\\system", "") else "Machine"
    last = str(task.LastRunTime)[:10]
    extra = f"at {' + '.join(when)}; last run {last}"
    if not command:
        extra += "; runs a COM handler, not a program"
    inv.items.append(Item(task.Name, command, "Scheduled task", scope, folder_path,
                          enabled=bool(task.Enabled), extra=extra))


def read_services(inv: Inventory) -> None:
    try:
        import win32service
        manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
    except Exception as error:  # noqa: BLE001
        logger.warning("Service manager unavailable: %s", error)
        inv.problems.append(f"Services could not be read: {error}")
        return
    try:
        for name, display, status in win32service.EnumServicesStatus(
                manager, win32service.SERVICE_WIN32, win32service.SERVICE_STATE_ALL):
            _add_service(inv, win32service, manager, name, display, status)
    finally:
        win32service.CloseServiceHandle(manager)


def _add_service(inv, win32service, manager, name, display, status) -> None:
    try:
        handle = win32service.OpenService(manager, name, win32service.SERVICE_QUERY_CONFIG)
    except Exception as error:  # noqa: BLE001
        logger.debug("Service %s not openable: %s", name, error)
        return
    try:
        config = win32service.QueryServiceConfig(handle)
    finally:
        win32service.CloseServiceHandle(handle)
    if config[1] != win32service.SERVICE_AUTO_START:
        return
    running = status[1] == win32service.SERVICE_RUNNING
    item = Item(display, config[3], "Service", "Machine", name, enabled=True,
                extra=f"{name}; {'running' if running else 'stopped'}; account {config[7]}")
    inv.items.append(item)


def service_dll(service_name: str) -> Optional[str]:
    """svchost-hosted services name their real code in Parameters\\ServiceDll."""
    base = rf"SYSTEM\CurrentControlSet\Services\{service_name}"
    for sub in ("\\Parameters", ""):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + sub) as key:
                value, _kind = winreg.QueryValueEx(key, "ServiceDll")
                return os.path.expandvars(str(value))
        except OSError:
            logger.debug("No ServiceDll under %s%s", base, sub)
            continue
    return None


def read_winlogon(inv: Inventory) -> None:
    """Shell and Userinit are persistence points; only a non-default value is listed."""
    defaults = {"shell": ["explorer.exe"], "userinit": [r"c:\windows\system32\userinit.exe"]}
    path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    values, problem = _read_values(winreg.HKEY_LOCAL_MACHINE, path)
    if problem:
        inv.problems.append(problem)
    for name, data in values:
        expected = defaults.get(name.lower())
        if expected is None:
            continue
        parts = [p.strip().lower() for p in data.split(",") if p.strip()]
        if parts != expected:
            inv.items.append(Item(name, data, "Winlogon", "Machine",
                                  "HKLM" + chr(92) + path, enabled=True,
                                  extra="differs from the Windows default"))


def read_ifeo_hijacks(inv: Inventory) -> None:
    """Image File Execution Options `Debugger` values -- the classic hijack
    (attach a different program to whatever launches the named one; the
    "sticky keys backdoor" sets sethc.exe's Debugger to cmd.exe). Confirmed
    live: this machine carries 116 IFEO subkeys and only ONE unreadable
    unelevated (a per-subkey ACL, not a blanket refusal) -- most subkeys use
    IFEO for GlobalFlag or silent process exit and carry no Debugger value
    at all, so only the ones that do are a redirection worth listing.
    """
    path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError as error:
        inv.problems.append(f"{path}: {error}")
        return
    names: List[str] = []
    with root:
        index = 0
        while True:
            try:
                names.append(winreg.EnumKey(root, index))
            except OSError:
                logger.debug("IFEO enumeration finished at %d", index)
                break
            index += 1
        for name in names:
            try:
                with winreg.OpenKey(root, name) as key:
                    try:
                        debugger = winreg.QueryValueEx(key, "Debugger")[0]
                    except FileNotFoundError:
                        logger.debug("IFEO subkey %s has no Debugger value (GlobalFlag/silent-exit use)", name)
                        continue
            except OSError as error:
                inv.problems.append(f"{path}{chr(92)}{name}: {error}")
                continue
            inv.items.append(Item(
                name, str(debugger), "IFEO", "Machine", "HKLM" + chr(92) + path + chr(92) + name,
                enabled=True, extra=f"redirects launches of {name} to this program"))


def _split_appinit(value: str) -> List[str]:
    """AppInit_DLLs entries: space/comma separated, except a quoted entry
    (needed for a path containing a space) which may contain either."""
    out: List[str] = []
    text = value.strip()
    i = 0
    while i < len(text):
        if text[i] == '"':
            end = text.find('"', i + 1)
            out.append(text[i + 1:end if end != -1 else len(text)])
            i = (end + 1) if end != -1 else len(text)
        else:
            j = i
            while j < len(text) and text[j] not in " ,":
                j += 1
            if j > i:
                out.append(text[i:j])
            i = j
        while i < len(text) and text[i] in " ,":
            i += 1
    return [o for o in out if o.strip()]


def read_appinit_dlls(inv: Inventory) -> None:
    """AppInit_DLLs: DLLs loaded into every process that links user32.dll.
    Deprecated and IGNORED under Secure Boot/code integrity, which is most
    modern machines including this one (confirmed: empty and
    LoadAppInit_DLLs=0 here) -- but whether it is actually inert depends on
    both values being read, not assumed, since a machine with Secure Boot
    off would have it work exactly as it did in 2005.
    """
    path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows"
    values, problem = _read_values(winreg.HKEY_LOCAL_MACHINE, path)
    if problem:
        inv.problems.append(problem)
        return
    data = dict(values)
    dlls = (data.get("AppInit_DLLs") or "").strip()
    if not dlls:
        return
    loaded = (data.get("LoadAppInit_DLLs") or "0").strip() not in ("", "0")
    for entry in _split_appinit(dlls):
        inv.items.append(Item(
            os.path.basename(entry) or entry, entry, "AppInit", "Machine", "HKLM" + chr(92) + path,
            enabled=loaded,
            extra="loaded into every process that links user32.dll" if loaded
            else "LoadAppInit_DLLs is 0: Windows ignores this list"))


# ---- enrichment ------------------------------------------------------------

def _under(path: str, roots: List[str]) -> bool:
    low = path.lower()
    return any(r and low.startswith(os.path.normcase(r).lower().rstrip("\\") + "\\") for r in roots)


def _env(name: str) -> str:
    return os.environ.get(name, "")


def location_note(exe: str) -> Optional[Note]:
    if not exe:
        return None
    hot = [_env("TEMP"), _env("TMP"), os.path.join(_env("USERPROFILE"), "Downloads"),
           os.path.join(_env("LOCALAPPDATA"), "Temp"), _env("PUBLIC"), os.path.join(system_root(), "Temp")]
    if _under(exe, hot):
        return Note("tempdir", "warn", "Runs from a temporary, Downloads or Public folder, "
                    "which is unusual for anything meant to start with Windows.")
    warm = [_env("APPDATA"), _env("LOCALAPPDATA"), _env("ProgramData")]
    if _under(exe, warm):
        return Note("userdir", "info", "Runs from a per-user or ProgramData folder "
                    "(common for self-updating apps, and also where unwanted software lives).")
    return None


def command_notes(exe: str, args: str) -> List[Note]:
    notes = []
    base = os.path.basename(exe).lower()
    if base in LOLBINS:
        notes.append(Note("lolbin", "info", f"Starts through {base}; what actually runs is in the arguments."))
        low = args.lower()
        hits = [s for s in SUSPICIOUS_ARGS if s in low]
        if hits:
            notes.append(Note("args", "warn", "Arguments contain: " + ", ".join(hits)
                              + " (encoded, hidden or remote content is worth reading before trusting)."))
    return notes


def assess(item: Item, now: datetime) -> None:
    """Fill `item.notes` from what is already known about it."""
    item.notes = []
    if item.exists is False:
        item.notes.append(Note("missing", "warn", f"The file does not exist: {item.exe or item.command}. "
                               "The entry is orphaned and does nothing but cost a failed launch."))
    if item.trust == trustlib.UNSIGNED:
        item.notes.append(Note("unsigned", "warn", "No Authenticode or catalog signature."))
    elif item.trust == trustlib.INVALID:
        item.notes.append(Note("invalid", "warn", "The signature is present but does not validate"
                               + (f": {item.trust_reason}." if item.trust_reason else ".")))
    elif item.trust == trustlib.UNKNOWN and item.exists:
        item.notes.append(Note("sigunknown", "info", "Signature could not be checked"
                               + (f": {item.trust_reason}." if item.trust_reason else ".")))
    loc = location_note(item.exe)
    if loc:
        item.notes.append(loc)
    args = item.command.replace(item.exe, "", 1) if item.exe else item.command
    item.notes.extend(command_notes(item.exe, args))
    if item.first_seen and not item.baseline and now - item.first_seen <= timedelta(days=RECENT_DAYS):
        item.notes.append(Note("recent", "info", f"First seen by this tool {item.first_seen:%Y-%m-%d %H:%M}."))
    if item.source == "Winlogon":
        item.notes.append(Note("winlogon", "warn", "Winlogon Shell/Userinit differs from the Windows default."))
    if item.source == "IFEO":
        item.notes.append(Note("ifeo", "warn",
                               f"Image File Execution Options Debugger hijack: launching {item.name} "
                               "actually runs this instead."))
    if item.source == "AppInit" and item.enabled:
        item.notes.append(Note("appinit", "warn",
                               "AppInit_DLLs loads into every process that links user32.dll -- "
                               "a broad, old-style injection point."))


def enrich(inv: Inventory, impact: Optional[Dict[str, Tuple[int, int]]], now: datetime) -> None:
    for item in inv.items:
        exe, _args = resolve_command(item.command)
        if item.source == "Service":
            exe = _service_exe(item, exe)
        item.exe = exe
        item.exists = os.path.isfile(exe) if exe else None
    paths = sorted({i.exe for i in inv.items if i.exists})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(zip(paths, pool.map(trustlib.check, paths)))
    for item in inv.items:
        result = results.get(item.exe)
        if result:
            item.trust, item.trust_reason, item.publisher = result.status, result.reason, result.publisher
        _apply_impact(item, impact)
        assess(item, now)


def _service_exe(item: Item, exe: str) -> str:
    if os.path.basename(exe).lower() == "svchost.exe":
        return service_dll(item.location) or exe
    return exe


def _apply_impact(item: Item, impact: Optional[Dict[str, Tuple[int, int]]]) -> None:
    if not impact:
        return
    keys = []
    if item.source == "Service":
        keys.append("svc:" + item.location.lower())
    # svchost hosts dozens of services: its own boot event says nothing about one of them.
    if item.exe and os.path.basename(item.exe).lower() != "svchost.exe":
        keys.append(item.exe.lower())
    for key in keys:
        if key in impact:
            item.impact_ms, item.impact_count = impact[key]
            return


def impact_map(slow) -> Dict[str, Tuple[int, int]]:
    """Worst degraded time and event count per path / service name, from boot traces."""
    out: Dict[str, Tuple[int, int]] = {}
    for s in slow:
        keys = ["svc:" + s.name.lower()] if s.kind == "Service" else [s.path.lower()]
        for key in keys:
            worst, count = out.get(key, (0, 0))
            out[key] = (max(worst, s.total_ms), count + 1)
    return out


# ---- first-seen history and change log ---------------------------------------

def _load_json(path: Optional[str]) -> Optional[dict]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError) as error:
        logger.warning("Startup history %s unreadable (%s); starting a new baseline", path, error)
        return None


def _save_json(path: Optional[str], data: dict) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        os.replace(tmp, path)
    except OSError as error:
        logger.warning("Could not save startup history %s: %s", path, error)


def apply_first_seen(inv: Inventory, path: Optional[str], now: datetime) -> None:
    """Stamp each item with when this tool first saw it. The very first scan is
    a baseline: everything then present is "at first scan", never "recent"."""
    data = _load_json(path)
    fresh = data is None
    data = data or {"baseline_at": now.isoformat(), "items": {}, "changes": []}
    data.setdefault("items", {})
    baseline_at = data.get("baseline_at", now.isoformat())
    for item in inv.items:
        stamp = data["items"].get(item.key)
        if stamp is None:
            stamp = now.isoformat()
            data["items"][item.key] = stamp
        item.first_seen = datetime.fromisoformat(stamp)
        item.baseline = (stamp == baseline_at) or (fresh and stamp == now.isoformat())
    _save_json(path, data)


def record_change(path: Optional[str], entry: dict) -> None:
    data = _load_json(path) or {"baseline_at": datetime.now().isoformat(), "items": {}, "changes": []}
    changes = data.setdefault("changes", [])
    changes.append(entry)
    data["changes"] = changes[-HISTORY_CAP:]
    _save_json(path, data)


def read_changes(path: Optional[str]) -> List[dict]:
    data = _load_json(path)
    return list(reversed((data or {}).get("changes", [])))


# ---- enable / disable ------------------------------------------------------

@dataclass
class ToggleResult:
    ok: bool
    enabled_after: Optional[bool]
    message: str


def toggle_block_reason(item: Item) -> Optional[str]:
    """Why this item cannot be switched here, or None when it can."""
    if item.source == "Run" and item.scope == "User":
        return None
    if item.source == "Startup folder" and item.scope == "User":
        return None
    if item.scope == "Machine" and item.source in ("Run", "Run (32-bit)", "Startup folder"):
        return "Machine-wide entries are switched with administrator rights; use Task Manager > Startup apps."
    if item.source == "Scheduled task":
        return "Use System Management > Scheduled Tasks to change a task."
    if item.source == "Service":
        return "Use System Management > Services to change a service."
    return "This kind of entry cannot be switched from here."


def _approved_key(item: Item) -> str:
    return _APPROVED + (r"\Run" if item.source == "Run" else r"\StartupFolder")


def _state_bytes(enabled: bool) -> bytes:
    if enabled:
        return bytes([2]) + bytes(11)
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    ticks = int((datetime.now(timezone.utc) - epoch).total_seconds() * 10_000_000)
    return bytes([3, 0, 0, 0]) + ticks.to_bytes(8, "little")


def set_enabled(item: Item, enabled: bool) -> ToggleResult:
    """Flip the StartupApproved marker and READ IT BACK -- the same switch Task Manager uses."""
    reason = toggle_block_reason(item)
    if reason:
        return ToggleResult(False, item.enabled, reason)
    subkey = _approved_key(item)
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE)
        with key:
            winreg.SetValueEx(key, item.name, 0, winreg.REG_BINARY, _state_bytes(enabled))
    except OSError as error:
        logger.warning("Could not write StartupApproved for %s: %s", item.name, error)
        return ToggleResult(False, item.enabled, f"Windows refused the change: {error}")
    after = read_approved(winreg.HKEY_CURRENT_USER, subkey)
    if after is None:
        return ToggleResult(False, None, "The change was written but could not be read back.")
    now_enabled = not after.get(item.name.lower(), False)
    if now_enabled != enabled:
        return ToggleResult(False, now_enabled, "Read-back disagrees: Windows did not keep the change.")
    return ToggleResult(True, now_enabled, "Enabled" if enabled else "Disabled")


# ---- collect ---------------------------------------------------------------

def collect(history_path: Optional[str] = None, slow=None, now: Optional[datetime] = None) -> Inventory:
    """Read every source. Call from a COMWorker (tasks and shortcuts use COM)."""
    now = now or datetime.now()
    inv = Inventory()
    read_run_keys(inv)
    read_startup_folders(inv)
    read_tasks(inv)
    read_services(inv)
    read_winlogon(inv)
    read_ifeo_hijacks(inv)
    read_appinit_dlls(inv)
    apply_first_seen(inv, history_path, now)
    enrich(inv, impact_map(slow or []), now)
    return inv


# ---- filters and export ------------------------------------------------------

CHIPS = ["All", "Flagged", "Not signed", "Missing file", "Not Microsoft", "Disabled", "Recently added", "Slowed boot"]


def matches_chip(item: Item, chip: str) -> bool:
    return {
        "All": True,
        "Flagged": item.flagged,
        "Not signed": item.trust in (trustlib.UNSIGNED, trustlib.INVALID),
        "Missing file": item.exists is False,
        "Not Microsoft": not item.is_microsoft,
        "Disabled": item.enabled is False,
        "Recently added": item.has("recent"),
        "Slowed boot": bool(item.impact_ms),
    }.get(chip, True)


def chip_counts(items: List[Item]) -> Dict[str, int]:
    return {chip: sum(1 for i in items if matches_chip(i, chip)) for chip in CHIPS}


def matches_search(item: Item, text: str) -> bool:
    if not text:
        return True
    hay = " ".join((item.name, item.command, item.exe, item.source, item.scope,
                    item.publisher or "", item.location, item.extra)).lower()
    return all(part in hay for part in text.lower().split())


def state_text(item: Item) -> str:
    return {True: "Enabled", False: "Disabled", None: "Unknown"}[item.enabled]


def trust_text(item: Item) -> str:
    return {trustlib.SIGNED: "Signed", trustlib.SIGNED_CATALOG: "Signed (Windows catalog)",
            trustlib.UNSIGNED: "Not signed", trustlib.INVALID: "Invalid signature",
            trustlib.UNKNOWN: ("No file" if item.exists is False
                               else "No program" if not item.exe else "Unknown")}[item.trust]


CSV_COLUMNS = ["Name", "Source", "Scope", "State", "Publisher", "Signature", "File exists",
               "Boot impact (ms)", "First seen", "Command", "Executable", "Location", "Findings"]


def to_csv(items: List[Item]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for i in items:
        writer.writerow([
            i.name, i.source, i.scope, state_text(i), i.publisher or "", trust_text(i),
            {True: "yes", False: "no", None: "unknown"}[i.exists], i.impact_ms or "",
            i.first_seen.isoformat(timespec="seconds") if i.first_seen else "",
            i.command, i.exe, i.location, " | ".join(n.text for n in i.notes)])
    return out.getvalue()


def summary(inv: Inventory, items: Optional[List[Item]] = None) -> str:
    items = inv.items if items is None else items
    counts = chip_counts(items)
    lines = [f"{len(items)} startup entries: " + ", ".join(
        f"{counts[c]} {c.lower()}" for c in CHIPS[1:]) + "."]
    for item in items:
        if item.flagged:
            lines.append(f"- {item.name} ({item.source}, {item.scope}): "
                         + " ".join(n.text for n in item.notes if n.severity == "warn"))
    for problem in inv.problems:
        lines.append(f"! not read: {problem}")
    return "\n".join(lines)
