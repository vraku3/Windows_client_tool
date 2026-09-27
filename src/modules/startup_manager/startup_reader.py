import logging
import os
import glob
import json
import re
import winreg
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

ENABLED_BYTES = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
DISABLED_BYTES = bytes([0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])


@dataclass
class StartupEntry:
    name: str
    command: str
    enabled: bool
    source: str  # "registry_run" | "startup_folder" | "task" | "service" | "browser_ext"
    extra: str = ""  # publisher, service name, etc.


_RUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_RUN_APPROVED_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_RUNONCE_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"
_RUNONCE_APPROVED_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\RunOnce"
_STARTUP_FOLDER_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"



def last_run_text(value) -> str:
    """'Last: 2026-09-23', or 'Never run'. The Task Scheduler reports a task
    that has not run as 1999-11-30 (its zero date), which read as a real
    date twenty-five years ago."""
    text = str(value or "")[:10]
    if not text or text < "2000-01-01":
        return "Never run"
    return f"Last: {text}"


def _collect_disabled_names(approved_key: str) -> set:
    """Return the set of entry names marked disabled (0x03) under StartupApproved."""
    disabled = set()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_key) as k:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(k, i)
                    if isinstance(data, bytes) and len(data) >= 1 and data[0] == 0x03:
                        disabled.add(name)
                    i += 1
                except OSError:
                    # OSError here is how winreg says the enumeration is
                    # finished. Control flow, not a failure.
                    logger.debug("_collect_disabled_names: registry enumeration finished", exc_info=True)
                    break
    except OSError:
        logger.debug("Ignored OSError", exc_info=True)
    return disabled


def _read_run_values(hive, key_path: str, source: str,
                     disabled: set) -> List[StartupEntry]:
    entries = []
    try:
        with winreg.OpenKey(hive, key_path) as k:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(k, i)
                    entries.append(StartupEntry(
                        name=name,
                        command=str(data),
                        enabled=(name not in disabled),
                        source=source,
                    ))
                    i += 1
                except OSError:
                    # OSError here is how winreg says the enumeration is
                    # finished. Control flow, not a failure.
                    logger.debug("_read_run_values: registry enumeration finished", exc_info=True)
                    break
    except OSError:
        logger.debug("Ignored OSError", exc_info=True)
    return entries


def get_registry_entries() -> List[StartupEntry]:
    """HKCU\\...\\Run values, enabled/disabled via StartupApproved."""
    return _read_run_values(
        winreg.HKEY_CURRENT_USER, _RUN_KEY,
        source="registry_run",
        disabled=_collect_disabled_names(_RUN_APPROVED_KEY),
    )


def get_machine_registry_entries() -> List[StartupEntry]:
    """HKLM\\...\\Run values (machine-wide startup). Read-only source."""
    return _read_run_values(
        winreg.HKEY_LOCAL_MACHINE, _RUN_KEY,
        source="registry_run_machine",
        disabled=set(),
    )


def get_runonce_entries() -> List[StartupEntry]:
    """HKCU + HKLM RunOnce values. These run once and are auto-cleared."""
    hkcu = _read_run_values(
        winreg.HKEY_CURRENT_USER, _RUNONCE_KEY,
        source="runonce",
        disabled=set(),
    )
    hklm = _read_run_values(
        winreg.HKEY_LOCAL_MACHINE, _RUNONCE_KEY,
        source="runonce",
        disabled=set(),
    )
    return hkcu + hklm


def set_registry_entry_enabled(name: str, enabled: bool) -> None:
    value = ENABLED_BYTES if enabled else DISABLED_BYTES
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_APPROVED_KEY,
        0, winreg.KEY_SET_VALUE | winreg.KEY_CREATE_SUB_KEY
    ) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, value)


def remove_registry_entry(name: str) -> None:
    """Delete a HKCU Run value (plus its StartupApproved marker if present)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        logger.debug("Run value not present: %s", name)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_APPROVED_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        logger.warning("remove_registry_entry: could not remove %r from the registry; the entry stays enabled", name, exc_info=True)
        pass


def add_registry_entry(name: str, command: str) -> None:
    """Add a new HKCU Run value."""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY,
        0, winreg.KEY_SET_VALUE | winreg.KEY_CREATE_SUB_KEY
    ) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, command)


def get_startup_folder_entries() -> List[StartupEntry]:
    entries = []
    folder = os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )
    disabled_names = _collect_disabled_names(_STARTUP_FOLDER_KEY)
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            if f.lower().endswith((".lnk", ".url", ".bat", ".cmd", ".exe")):
                entries.append(StartupEntry(
                    name=f,
                    command=os.path.join(folder, f),
                    enabled=(f not in disabled_names),
                    source="startup_folder",
                ))
    return entries


def set_startup_folder_entry_enabled(name: str, enabled: bool) -> None:
    value = ENABLED_BYTES if enabled else DISABLED_BYTES
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _STARTUP_FOLDER_KEY,
        0, winreg.KEY_SET_VALUE | winreg.KEY_CREATE_SUB_KEY
    ) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, value)


def get_startup_folder_path() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )


def remove_startup_folder_entry(name: str) -> None:
    """Delete a file from the Startup folder (plus its StartupApproved marker)."""
    folder = get_startup_folder_path()
    target = os.path.join(folder, name)
    if os.path.isfile(target):
        os.remove(target)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_FOLDER_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        logger.warning("remove_startup_folder_entry: could not remove %r from the Startup folder", name, exc_info=True)
        pass


def get_scheduled_task_entries() -> List[StartupEntry]:
    """Uses win32com Schedule.Service. Must be called from a COMWorker thread."""
    import win32com.client
    entries = []
    try:
        svc = win32com.client.Dispatch("Schedule.Service")
        svc.Connect()
        root = svc.GetFolder("\\")
        tasks = root.GetTasks(0)
        for i in range(tasks.Count):
            task = tasks.Item(i + 1)
            try:
                path = task.Definition.Actions.Item(1).Path \
                    if task.Definition.Actions.Count > 0 else ""
            except Exception:
                path = ""
            entries.append(StartupEntry(
                name=task.Name,
                command=path,
                enabled=task.Enabled,
                source="task",
                extra=last_run_text(task.LastRunTime),
            ))
    except Exception as e:
        logger.warning("Failed to load scheduled task entries: %s", e)
    return entries


def get_service_entries() -> List[StartupEntry]:
    """Get auto-start services."""
    import win32service
    entries = []
    try:
        scm = win32service.OpenSCManager(
            None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE
        )
        svcs = win32service.EnumServicesStatus(
            scm,
            win32service.SERVICE_WIN32,
            win32service.SERVICE_STATE_ALL,
        )
        for name, display_name, status in svcs:
            try:
                hs = win32service.OpenService(
                    scm, name, win32service.SERVICE_QUERY_CONFIG
                )
                config = win32service.QueryServiceConfig(hs)
                start_type = config[1]
                win32service.CloseServiceHandle(hs)
                if start_type in (
                    win32service.SERVICE_AUTO_START,
                    win32service.SERVICE_BOOT_START,
                ):
                    running = status[1] == win32service.SERVICE_RUNNING
                    entries.append(StartupEntry(
                        name=display_name,
                        command=name,
                        enabled=(start_type == win32service.SERVICE_AUTO_START),
                        source="service",
                        extra="Running" if running else "Stopped",
                    ))
            except Exception:
                logger.debug("get_service_entries: skipping an item that could not be read", exc_info=True)
                continue
        win32service.CloseServiceHandle(scm)
    except Exception as e:
        logger.warning("Failed to load service entries: %s", e)
    return entries


def get_browser_extensions() -> List[StartupEntry]:
    """Read Chrome + Edge extensions from disk (read-only)."""
    entries = []
    local = os.environ.get("LOCALAPPDATA", "")
    browsers = [
        ("Chrome", os.path.join(local, r"Google\Chrome\User Data\Default\Extensions")),
        ("Edge", os.path.join(local, r"Microsoft\Edge\User Data\Default\Extensions")),
    ]
    for browser, ext_dir in browsers:
        if not os.path.isdir(ext_dir):
            continue
        for ext_id in os.listdir(ext_dir):
            ext_path = os.path.join(ext_dir, ext_id)
            if not os.path.isdir(ext_path):
                continue
            # Find manifest.json in version subfolder first, then root
            manifests = glob.glob(os.path.join(ext_path, "*", "manifest.json"))
            if not manifests:
                manifests = glob.glob(os.path.join(ext_path, "manifest.json"))
            for manifest_path in manifests[:1]:
                try:
                    with open(manifest_path, encoding="utf-8", errors="replace") as f:
                        manifest = json.load(f)
                    name = manifest.get("name", ext_id)
                    version = manifest.get("version", "")
                    entries.append(StartupEntry(
                        name=name,
                        command=ext_id,
                        enabled=True,
                        source="browser_ext",
                        extra=f"{browser} v{version}",
                    ))
                    break
                except Exception:
                    logger.debug("get_browser_extensions: skipping an item that could not be read", exc_info=True)
                    continue
    return entries


#: Every registry location Explorer consults for a right-click context menu
#: handler. Confirmed live: 37 real handlers across these roots on this
#: machine, all healthy.
_SHELLEX_ROOTS = (
    r"*\shellex\ContextMenuHandlers",
    r"Directory\shellex\ContextMenuHandlers",
    r"Directory\Background\shellex\ContextMenuHandlers",
    r"Folder\shellex\ContextMenuHandlers",
    r"AllFilesystemObjects\shellex\ContextMenuHandlers",
    r"Drive\shellex\ContextMenuHandlers",
)
_GUID_RE = re.compile(r"^\{[0-9A-Fa-f-]{36}\}$")


def _clsid_dll(clsid: str) -> Tuple[Optional[str], Optional[str]]:
    """(dll_path, None) for a CLSID's own InProcServer32/LocalServer32
    default value, or (None, reason) when neither subkey exists."""
    for sub in ("InProcServer32", "LocalServer32"):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"CLSID\\{clsid}\\{sub}") as key:
                return str(winreg.QueryValueEx(key, "")[0] or ""), None
        except OSError as exc:
            logger.debug("CLSID %s has no %s: %s", clsid, sub, exc)
            continue
    return None, "the CLSID has no InProcServer32 or LocalServer32 registered"


def get_shell_extensions() -> List[StartupEntry]:
    """Explorer right-click context menu handlers, across every registry
    root that offers them (a file, a folder, a folder's empty background,
    a drive, and every filesystem object).

    The handler subkey's NAME is the authoritative CLSID by Windows'
    convention ("{GUID}"); its own DEFAULT VALUE is documentation only and
    is not always a repeated CLSID -- confirmed live: this machine's
    "Taskband Pin" and "Start Menu Pin" handlers have that default value
    set to their own FRIENDLY NAME instead. Treating that value as the
    CLSID whenever the subkey name itself isn't GUID-shaped is what makes
    both conventions resolve; assuming the default value is always the
    CLSID produced two false "orphaned" positives here for handlers that
    are actually Windows' own built-in Pin-to-Start/Taskbar verbs.

    Deduplicates by CLSID: many handlers are registered once and apply
    to several roots (a handler under `AllFilesystemObjects` also matches
    everything `*` does).
    """
    entries: List[StartupEntry] = []
    seen = set()
    for root in _SHELLEX_ROOTS:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, root) as key:
                names = []
                index = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(key, index))
                    except OSError:
                        logger.debug("%s enumeration finished at %d", root, index)
                        break
                    index += 1
        except OSError as exc:
            logger.debug("Cannot open %s: %s", root, exc)
            continue
        for name in names:
            clsid = name if _GUID_RE.match(name) else None
            friendly = ""
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, root + "\\" + name) as key:
                    friendly = str(winreg.QueryValueEx(key, "")[0] or "")
                    if clsid is None and _GUID_RE.match(friendly):
                        clsid = friendly
            except OSError as exc:
                logger.debug("Cannot read default value of %s\\%s: %s", root, name, exc)
            dedupe_key = (clsid or name).lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            display_name = friendly if friendly and not _GUID_RE.match(friendly) else name
            if not clsid:
                entries.append(StartupEntry(
                    display_name, name, False, "shell_ext",
                    "ORPHANED: handler is not a CLSID and has no CLSID default value"))
                continue
            dll, problem = _clsid_dll(clsid)
            if problem:
                entries.append(StartupEntry(display_name, clsid, False, "shell_ext", "ORPHANED: " + problem))
                continue
            dll_path = os.path.expandvars(dll.strip('"').split(",")[0])
            if not os.path.isfile(dll_path):
                entries.append(StartupEntry(
                    display_name, dll_path, False, "shell_ext", "ORPHANED: file does not exist"))
                continue
            entries.append(StartupEntry(display_name, dll_path, True, "shell_ext", "OK"))
    return entries


_ICON_OVERLAY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellIconOverlayIdentifiers"
#: Windows has long documented a hard cap on how many overlay handlers it
#: will actually load (commonly cited as 15, in alphabetical order by
#: subkey name) -- not independently re-measured here, so this is reported
#: as a documented limit approaching, never as something this tool caught
#: happening live.
ICON_OVERLAY_SLOT_LIMIT = 15


def get_icon_overlay_handlers() -> List[StartupEntry]:
    """Explorer icon overlay identifiers (the little badge on a OneDrive-synced
    or version-controlled file/folder). Same CLSID-resolution shape as
    `get_shell_extensions`, over a single flat key rather than several roots.
    Confirmed live: 9 real entries here (all 7 OneDrive slots plus Offline
    Files and Enhanced Storage), comfortably under the documented ~15-slot
    limit -- so the slot-count note never fires on this machine, only the
    per-handler orphan check does anything visible here.
    """
    entries: List[StartupEntry] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _ICON_OVERLAY_KEY) as key:
            names = []
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                except OSError:
                    logger.debug("Icon overlay enumeration finished at %d", index)
                    break
                index += 1
    except OSError as exc:
        logger.warning("Cannot open %s: %s", _ICON_OVERLAY_KEY, exc)
        return entries
    for name in sorted(names, key=str.lower):
        clsid = None
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _ICON_OVERLAY_KEY + "\\" + name) as key:
                value = str(winreg.QueryValueEx(key, "")[0] or "")
                clsid = value if _GUID_RE.match(value) else None
        except OSError as exc:
            logger.debug("Cannot read %s\\%s: %s", _ICON_OVERLAY_KEY, name, exc)
        display_name = name.strip()
        if not clsid:
            entries.append(StartupEntry(
                display_name, "", False, "icon_overlay", "ORPHANED: no CLSID registered"))
            continue
        dll, problem = _clsid_dll(clsid)
        if problem:
            entries.append(StartupEntry(display_name, clsid, False, "icon_overlay", "ORPHANED: " + problem))
            continue
        dll_path = os.path.expandvars(dll.strip('"').split(",")[0])
        if not os.path.isfile(dll_path):
            entries.append(StartupEntry(
                display_name, dll_path, False, "icon_overlay", "ORPHANED: file does not exist"))
            continue
        note = "OK"
        if len(names) > ICON_OVERLAY_SLOT_LIMIT:
            note = f"OK ({len(names)} registered; Windows documents a ~{ICON_OVERLAY_SLOT_LIMIT}-slot limit, alphabetical)"
        entries.append(StartupEntry(display_name, dll_path, True, "icon_overlay", note))
    return entries
