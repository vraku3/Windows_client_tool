from core.system_paths import cmd_exe, powershell_exe
import glob as _glob
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional
import logging
logger = logging.getLogger(__name__)


@dataclass
class FixAction:
    key: str                          # unique identifier
    title: str
    description: str
    category: str
    reboot_required: bool = False
    fn: Optional[Callable[[Callable[[str], None]], None]] = field(default=None, repr=False)
    # fn signature: fn(output_cb: Callable[[str], None]) -> None
    # output_cb is called with each line of output
    confirm_text: Optional[str] = None
    # When set, _FixCard shows an Ok/Cancel QMessageBox with this text
    # (default button Cancel) before running -- ports over Quick
    # Cleanup's per-action confirm dialogs during the Part B merge.
    precondition: Optional[Callable[[], Optional[str]]] = field(default=None, repr=False)
    # Called just before running (before any confirm). Returning None
    # means proceed; returning a string shows it in the card's status
    # label instead of running -- e.g. "hibernation is off, nothing to
    # resize" for the migrated Resize Hibernation action.
    long_running: bool = False
    # True routes the worker to core.long_op_pool.get_long_op_pool()
    # instead of the shared global QThreadPool -- for actions that can
    # run 10-30 minutes (Compact WinSxS, WU Deep Clean), matching how
    # Quick Cleanup and System Health already isolate their own
    # long-running DISM calls from everything else's worker pool.


_tracking = threading.local()


def begin_tracking() -> None:
    """Start collecting failed steps for the fix running on this thread.

    The card used to record "ok" whenever the function returned, while most
    fixes ignored their commands' exit codes -- a refused `netsh` or `DISM`
    run was logged as a success. `_run_cmd` and the service helpers now note
    every failure here, and the card fails the run when there are any."""
    _tracking.failures = []


def end_tracking() -> List[str]:
    failures = list(getattr(_tracking, "failures", None) or [])
    _tracking.failures = None
    return failures


def _note_failure(message: str) -> None:
    failures = getattr(_tracking, "failures", None)
    if failures is not None:
        failures.append(message)


def _run_cmd(cmd: List[str], output_cb: Callable[[str], None],
             input_bytes: bytes = None, tolerate_failure: bool = False) -> int:
    """Run a command, streaming output line by line via output_cb. Returns
    exit code; a non-zero one is noted as a failed step unless the caller
    reports it itself (`tolerate_failure`)."""
    rc = _run_cmd_raw(cmd, output_cb, input_bytes)
    if rc != 0 and not tolerate_failure:
        _note_failure(f"{os.path.basename(cmd[0])} exited with code {rc}")
    return rc


def _run_cmd_raw(cmd: List[str], output_cb: Callable[[str], None],
                 input_bytes: bytes = None) -> int:
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if input_bytes else None,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if input_bytes:
            proc.stdin.write(input_bytes.decode())
            proc.stdin.close()
        for line in proc.stdout:
            output_cb(line.rstrip())
        proc.wait()
        return proc.returncode
    except Exception as e:
        output_cb(f"Error: {e}")
        return -1


_SERVICE_WAIT_S = 30


def _stop_service(name: str, output_cb: Callable[[str], None]) -> bool:
    """Stop a service and WAIT until it is stopped. True once it is.

    `StopService` only sends the control: the service is often still
    STOP_PENDING when it returns, so whatever ran next (deleting its cache)
    raced it. A service that was already stopped is fine."""
    output_cb(f"Stopping {name}...")
    try:
        import win32service
        import win32serviceutil
        if win32serviceutil.QueryServiceStatus(name)[1] != win32service.SERVICE_STOPPED:
            win32serviceutil.StopService(name)
            win32serviceutil.WaitForServiceStatus(name, win32service.SERVICE_STOPPED,
                                                  _SERVICE_WAIT_S)
        output_cb(f"{name} stopped.")
        return True
    except Exception as e:
        output_cb(f"Could not stop {name}: {e}")
        _note_failure(f"could not stop {name}: {e}")
        return False


def _start_service(name: str, output_cb: Callable[[str], None]) -> None:
    output_cb(f"Starting {name}...")
    try:
        import win32service
        import win32serviceutil
        if win32serviceutil.QueryServiceStatus(name)[1] != win32service.SERVICE_RUNNING:
            win32serviceutil.StartService(name)
        output_cb(f"{name} started.")
    except Exception as e:
        output_cb(f"Could not start {name}: {e}")
        _note_failure(f"could not start {name}: {e}")


def _own_session() -> int:
    import ctypes
    sid = ctypes.c_uint32()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
        raise OSError("could not read the session id of this process")
    return sid.value


def _end_explorer(output_cb: Callable[[str], None]):
    """End Explorer in THIS session only -- never another signed-in user's
    shell (taskkill /im explorer.exe from an elevated process ended them all).
    Returns (system, session, old pids) for `_bring_explorer_back`."""
    from modules.game_mode.engine.system import WinSystem
    system, session = WinSystem(), _own_session()
    old = system.end_explorer(session)
    if isinstance(old, str):
        output_cb(f"Could not stop Explorer: {old}")
        _note_failure(old)
        old = set()
    return system, session, old


def _bring_explorer_back(shell, output_cb: Callable[[str], None]) -> None:
    """Windows restarts the shell itself (AutoRestartShell); if it does not,
    it is started UNELEVATED -- an explorer.exe started by this elevated app
    would run elevated."""
    system, session, old = shell
    output_cb("Waiting for Explorer to come back...")
    problem = system.ensure_shell(old, session)
    if problem:
        output_cb(f"Explorer: {problem}")
        _note_failure(problem)
    else:
        output_cb("Done.")


def rebuild_icon_cache(output_cb: Callable[[str], None]) -> None:
    output_cb("Stopping Explorer...")
    shell = _end_explorer(output_cb)
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, "IconCache.db"),
        *_glob.glob(os.path.join(local, r"Microsoft\Windows\Explorer\iconcache*")),
    ]
    for t in targets:
        try:
            os.remove(t)
            output_cb(f"Deleted: {t}")
        except OSError as e:
            output_cb(f"Could not delete {t}: {e}")
    _bring_explorer_back(shell, output_cb)


def clear_thumbnail_cache(output_cb: Callable[[str], None]) -> None:
    output_cb("Stopping Explorer...")
    shell = _end_explorer(output_cb)
    local = os.environ.get("LOCALAPPDATA", "")
    pattern = os.path.join(local, r"Microsoft\Windows\Explorer\thumbcache_*.db")
    for f in _glob.glob(pattern):
        try:
            os.remove(f)
            output_cb(f"Deleted: {f}")
        except OSError as e:
            output_cb(f"Skip {f}: {e}")
    _bring_explorer_back(shell, output_cb)


def restart_explorer(output_cb: Callable[[str], None]) -> None:
    output_cb("Killing Explorer...")
    shell = _end_explorer(output_cb)
    _bring_explorer_back(shell, output_cb)


def flush_dns(output_cb: Callable[[str], None]) -> None:
    _run_cmd(["ipconfig", "/flushdns"], output_cb)


def flush_and_register_dns(output_cb: Callable[[str], None]) -> None:
    """Flush DNS cache, purge NetBIOS names, and trigger dynamic DNS registration."""
    output_cb("Flushing DNS resolver cache...")
    _run_cmd(["ipconfig", "/flushdns"], output_cb)
    output_cb("Purging and reloading remote NetBIOS name cache...")
    _run_cmd(["nbtstat", "-R"], output_cb, tolerate_failure=True)
    output_cb("Releasing and refreshing NetBIOS name registrations...")
    _run_cmd(["nbtstat", "-RR"], output_cb, tolerate_failure=True)
    output_cb("Triggering dynamic DNS registration (ipconfig /registerdns)...")
    _run_cmd(["ipconfig", "/registerdns"], output_cb, tolerate_failure=True)
    output_cb("DNS and NetBIOS resolution stack refreshed.")


def reset_winsock(output_cb: Callable[[str], None]) -> None:
    _run_cmd(["netsh", "winsock", "reset"], output_cb)
    output_cb("Reboot required to complete.")


def reset_tcpip(output_cb: Callable[[str], None]) -> None:
    _run_cmd(["netsh", "int", "ip", "reset"], output_cb)
    output_cb("Reboot required to complete.")


def ip_release_renew(output_cb: Callable[[str], None]) -> None:
    _run_cmd(["ipconfig", "/release"], output_cb)
    _run_cmd(["ipconfig", "/renew"], output_cb)


def flush_arp_cache(output_cb: Callable[[str], None]) -> None:
    """Flush IPv4 ARP cache to remove stale IP-to-MAC mappings."""
    output_cb("Flushing ARP cache (netsh interface ip delete arpcache)...")
    rc = _run_cmd(["netsh", "interface", "ip", "delete", "arpcache"], output_cb)
    if rc == 0:
        output_cb("ARP cache flushed successfully.")
    else:
        output_cb(f"ARP cache flush finished with code {rc}.")


def reset_firewall(output_cb: Callable[[str], None]) -> None:
    """Reset Windows Defender Firewall to its factory default state."""
    output_cb("Resetting Windows Firewall to factory defaults (netsh advfirewall reset)...")
    rc = _run_cmd(["netsh", "advfirewall", "reset"], output_cb)
    if rc == 0:
        output_cb("Windows Firewall configuration restored to default.")
    else:
        output_cb(f"Firewall reset finished with code {rc}.")


def reset_windows_update(output_cb: Callable[[str], None]) -> None:
    """Microsoft's documented reset: stop the services, RENAME
    SoftwareDistribution and catroot2 (Windows recreates both), start them
    again. Renaming, not deleting, so the old folders stay as the way back,
    and only once every service has actually stopped -- deleting while one
    was still STOP_PENDING left half-removed folders."""
    services = ["wuauserv", "cryptsvc", "bits", "msiserver"]
    stopped = [_stop_service(svc, output_cb) for svc in services]
    if all(stopped):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        for d in (os.path.join(windir, "SoftwareDistribution"),
                  os.path.join(windir, "System32", "catroot2")):
            if not os.path.isdir(d):
                continue
            target = f"{d}.old-{stamp}"
            try:
                os.rename(d, target)
                output_cb(f"Renamed {d} -> {target} (delete it once updates work again)")
            except OSError as e:
                output_cb(f"Could not rename {d}: {e}")
                _note_failure(f"could not rename {d}: {e}")
    else:
        output_cb("Not every service stopped, so nothing was renamed.")
    for svc in services:
        _start_service(svc, output_cb)
    output_cb("Windows Update reset finished.")


def reregister_wu_dlls(output_cb: Callable[[str], None]) -> None:
    dlls = [
        "atl.dll", "urlmon.dll", "mshtml.dll", "shdocvw.dll", "browseui.dll",
        "jscript.dll", "vbscript.dll", "scrrun.dll", "msxml.dll", "msxml3.dll",
        "msxml6.dll", "actxprxy.dll", "softpub.dll", "wintrust.dll", "dssenh.dll",
        "rsaenh.dll", "gpkcsp.dll", "sccbase.dll", "slbcsp.dll", "cryptdlg.dll",
        "oleaut32.dll", "ole32.dll", "shell32.dll", "initpki.dll", "wuapi.dll",
        "wuaueng.dll", "wuaueng1.dll", "wucltui.dll", "wups.dll", "wups2.dll", "wuweb.dll",
    ]
    for dll in dlls:
        # Several of these DLLs no longer exist on Windows 10/11; a failure
        # is reported per DLL below, not as a failed fix.
        rc = _run_cmd(["regsvr32", "/s", dll], output_cb, tolerate_failure=True)
        output_cb(f"regsvr32 {dll}: {'OK' if rc == 0 else 'Failed'}")
    output_cb("Done.")


def clear_print_queue(output_cb: Callable[[str], None]) -> None:
    """Stop the spooler, clear lingering spool jobs (.SHD/.SPL/.TMP), and
    ALWAYS restart the spooler even if a file was locked or missing."""
    from core.windows_utils import system_root

    output_cb("Stopping Print Spooler (spoolsv)...")
    if not _stop_service("Spooler", output_cb):
        output_cb("Print queue NOT cleared: the Print Spooler did not stop.")
        _start_service("Spooler", output_cb)
        return

    spool_dir = os.path.join(system_root(), "System32", "spool", "PRINTERS")
    deleted_count = 0
    try:
        if os.path.isdir(spool_dir):
            entries = os.listdir(spool_dir)
            targets = [
                f for f in entries
                if f.lower().endswith((".shd", ".spl", ".tmp"))
                or os.path.isfile(os.path.join(spool_dir, f))
            ]
            for f in targets:
                fpath = os.path.join(spool_dir, f)
                if not os.path.isfile(fpath):
                    continue
                try:
                    os.remove(fpath)
                    output_cb(f"Deleted: {f}")
                    deleted_count += 1
                except OSError as e:
                    output_cb(f"Skip {f}: {e}")
            if deleted_count > 0:
                output_cb(f"Cleared {deleted_count} lingering spool file(s).")
            else:
                output_cb("Print queue was already empty.")
    except OSError as e:
        output_cb(f"Could not read the spool folder: {e}")
        _note_failure(f"could not read the spool folder: {e}")
    finally:
        _start_service("Spooler", output_cb)
    output_cb("Print queue cleared.")


def _ensure_disk_cleanup_flags() -> None:
    """Set StateFlags0001 = 2 for standard safe volume cache handlers.
    Without this, cleanmgr /sagerun:1 finds 0 flags configured and cleans nothing."""
    import winreg
    safe_caches = (
        "Active Setup Temp Folders",
        "Downloaded Program Files",
        "Internet Cache Files",
        "Old ChkDsk Files",
        "Recycle Bin",
        "System error memory dump files",
        "System error minidump files",
        "Temporary Files",
        "Temporary Setup Files",
        "Thumbnail Cache",
        "Windows Error Reporting Files",
    )
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches"
    for name in safe_caches:
        try:
            with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{name}") as k:
                winreg.SetValueEx(k, "StateFlags0001", 0, winreg.REG_DWORD, 2)
        except OSError as exc:
            logger.debug("Could not set cleanmgr flag for %s: %s", name, exc)


def run_disk_cleanup(output_cb: Callable[[str], None]) -> None:
    """Run cleanmgr /d C: /sagerun:1 with predefined cleanup."""
    output_cb("Configuring safe cleanup targets for cleanmgr...")
    _ensure_disk_cleanup_flags()
    output_cb("Starting Disk Cleanup on C: ...")
    try:
        # Use predefined SAGERUN value to auto-select recommended cleanup
        rc = _run_cmd(
            ["cleanmgr", "/d", "C:", "/sagerun:1", "/lowdisk"],
            output_cb
        )
        if rc == 0:
            output_cb("Disk Cleanup completed.")
        else:
            output_cb(f"Disk Cleanup exited with code {rc}. Run cleanmgr manually for more options.")
    except Exception as e:
        output_cb(f"Could not run Disk Cleanup: {e}")


def reset_perf_counters(output_cb: Callable[[str], None]) -> None:
    """Rebuild performance counter registry keys."""
    output_cb("Rebuilding performance counters...")
    try:
        rc = _run_cmd(
            ["lodctr", "/r"],
            output_cb
        )
        if rc == 0:
            output_cb("Performance counters rebuilt.")
        else:
            output_cb(f"Rebuilding performance counters exited with code {rc}. "
                      f"This needs an elevated process.")
    except Exception as e:
        output_cb(f"Error rebuilding counters: {e}")


def clear_prefetch(output_cb: Callable[[str], None]) -> None:
    """Clear the Windows Prefetch folder."""
    from core import windows_utils
    prefetch_dir = os.path.join(windows_utils.system_root(), "Prefetch")
    output_cb(f"Clearing Prefetch folder: {prefetch_dir}")
    count = 0
    for f in _glob.glob(os.path.join(prefetch_dir, "*.pf")):
        try:
            os.remove(f)
            count += 1
        except OSError as e:
            output_cb(f"Skip {f}: {e}")
    output_cb(f"Cleared {count} Prefetch files.")


def clear_recent_files(output_cb: Callable[[str], None]) -> None:
    """Clear recent documents and Explorer jump list."""
    recent = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent")
    output_cb(f"Clearing Recent files: {recent}")
    count = 0
    for f in _glob.glob(os.path.join(recent, "*.lnk")):
        try:
            os.remove(f)
            count += 1
        except OSError as e:
            output_cb(f"Skip {f}: {e}")
    # Also clear thumbnail cache
    thumb_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Explorer")
    thumb_files = [f for f in _glob.glob(os.path.join(thumb_dir, "thumbcache_*.db"))]
    output_cb(f"Clearing {len(thumb_files)} thumbnail cache entries")
    for f in thumb_files:
        try:
            os.remove(f)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
    output_cb(f"Cleared {count} Recent shortcuts.")


def network_reset(output_cb: Callable[[str], None]) -> None:
    """Reset all network adapters via netsh."""
    output_cb("Resetting all network adapters...")
    _run_cmd(["netsh", "winsock", "reset"], output_cb)
    _run_cmd(["netsh", "int", "ip", "reset"], output_cb)
    output_cb("Network adapters reset. REBOOT REQUIRED.")


def check_wu_updates(output_cb: Callable[[str], None]) -> None:
    """Trigger Windows Update scan using PowerShell."""
    output_cb("Checking for Windows updates (this may take a minute)...")
    script = (
        "$UpdateSession = New-Object -ComObject Microsoft.Update.Session; "
        "$UpdateSearcher = $UpdateSession.CreateUpdateSearcher(); "
        "try { "
        "  $Result = $UpdateSearcher.Search('IsInstalled=0'); "
        "  $count = $Result.Updates.Count; "
        "  if ($count -eq 0) { 'All updates are installed.' } "
        "  else { \"$count update(s) available:\"; "
        "    foreach ($u in $Result.Updates) { \"  - $($u.Title)\" } } "
        "} catch { 'Error checking updates: ' + $_.Exception.Message }"
    )
    _run_cmd(
        [powershell_exe(), "-NoProfile", "-Command", script],
        output_cb
    )


def restart_print_spooler(output_cb: Callable[[str], None]) -> None:
    """Restart the Print Spooler service."""
    _stop_service("Spooler", output_cb)
    time.sleep(1)
    _start_service("Spooler", output_cb)
    output_cb("Print Spooler restarted.")


def clear_event_logs(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _clear_event_logs."""
    for log in ("System", "Application", "Security"):
        rc = _run_cmd(["wevtutil", "cl", log], output_cb)
        output_cb(f"Cleared {log} log" if rc == 0 else f"Could not clear {log} log (exit {rc})")


def compact_winsxs(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _compact_winsxs -- plain
    /StartComponentCleanup only, deliberately NOT /ResetBase (that flag
    lives solely in System Health's gated Reset Base, per the Sub-project
    4 safety fix -- do not reintroduce it here)."""
    _run_cmd(["Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], output_cb)


def wu_deep_clean(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _wu_deep_clean. Distinct from
    System Health's plain Component Cleanup: this adds
    /SuppressDefaultActions, and distinct from wu_reset above (that
    clears the WU cache/services; this reclaims WinSxS space)."""
    _run_cmd(["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup",
              "/SuppressDefaultActions"], output_cb)


def clear_clipboard(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _clear_clipboard."""
    _run_cmd([cmd_exe(), "/c", "echo off | clip"], output_cb)
    output_cb("Clipboard cleared.")


def reset_search(output_cb: Callable[[str], None]) -> None:
    """Restart the Windows Search service and reset its catalog database flag so Windows rebuilds it."""
    output_cb("Stopping Windows Search service (WSearch)...")
    _stop_service("WSearch", output_cb)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows Search", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "SetupCompletedSuccessfully", 0, winreg.REG_DWORD, 0)
        output_cb("Reset SetupCompletedSuccessfully = 0 (triggers fresh catalog database build).")
    except OSError as exc:
        output_cb(f"Could not reset search registry flag: {exc}")
    output_cb("Starting Windows Search service...")
    _start_service("WSearch", output_cb)
    output_cb("Windows Search reset: catalog database rebuild initiated.")


def clear_font_cache(output_cb: Callable[[str], None]) -> None:
    """Flush the Windows Font Cache service and purge cached font files."""
    output_cb("Stopping Windows Font Cache service...")
    _stop_service("FontCache", output_cb)
    local = os.environ.get("LOCALAPPDATA", "")
    font_cache_dir = os.path.join(local, "FontCache")
    purged = 0
    if os.path.isdir(font_cache_dir):
        for f in _glob.glob(os.path.join(font_cache_dir, "*")):
            try:
                if os.path.isfile(f):
                    os.remove(f)
                    purged += 1
            except OSError as exc:
                logger.debug("Could not purge font cache file %s: %s", f, exc)
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    fntcache_dat = os.path.join(windir, "System32", "FNTCACHE.DAT")
    if os.path.exists(fntcache_dat):
        try:
            os.remove(fntcache_dat)
            purged += 1
        except OSError as exc:
            logger.debug("Could not purge %s: %s", fntcache_dat, exc)
    output_cb("Starting Windows Font Cache service...")
    _start_service("FontCache", output_cb)
    output_cb(f"Font cache cleared ({purged} cache file(s) purged).")


def salvage_wmi_repository(output_cb: Callable[[str], None]) -> None:
    """Run winmgmt /salvagerepository to repair a corrupted or inconsistent WMI repository."""
    output_cb("Running WMI repository salvage (winmgmt /salvagerepository)...")
    rc = _run_cmd(["winmgmt", "/salvagerepository"], output_cb)
    if rc == 0:
        output_cb("WMI repository salvage completed successfully.")
    else:
        output_cb(f"winmgmt /salvagerepository returned exit code {rc}.")


def clear_wer_queue(output_cb: Callable[[str], None]) -> None:
    """Clear Windows Error Reporting queue and archives."""
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    wer_base = os.path.join(program_data, "Microsoft", "Windows", "WER")
    total_removed = 0
    for sub in ("ReportQueue", "ReportArchive", "Temp"):
        target = os.path.join(wer_base, sub)
        if not os.path.isdir(target):
            continue
        output_cb(f"Clearing WER {sub}...")
        removed_count = 0
        try:
            for item in os.listdir(target):
                item_path = os.path.join(target, item)
                try:
                    if os.path.isdir(item_path):
                        import shutil
                        shutil.rmtree(item_path, ignore_errors=True)
                    else:
                        os.remove(item_path)
                    removed_count += 1
                except OSError as exc:
                    output_cb(f"Could not remove {item}: {exc}")
                    logger.debug("Could not remove WER item %s: %s", item_path, exc)
        except OSError as exc:
            output_cb(f"Could not list {target}: {exc}")
            logger.debug("Could not list %s: %s", target, exc)
        output_cb(f"Removed {removed_count} item(s) from {sub}.")
        total_removed += removed_count
    output_cb(f"Windows Error Reporting queue cleared ({total_removed} total item(s) removed).")


def _wer_queue_precondition() -> Optional[str]:
    """Check if there are any crash reports or files in WER queues."""
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    wer_base = os.path.join(program_data, "Microsoft", "Windows", "WER")
    for sub in ("ReportQueue", "ReportArchive", "Temp"):
        target = os.path.join(wer_base, sub)
        if os.path.isdir(target):
            try:
                if os.listdir(target):
                    return None
            except OSError as exc:
                logger.debug("WER queue check failed for %s: %s", target, exc)
    return "WER ReportQueue and archives are already empty — nothing to clear"


def resize_hibernation(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _resize_hibernation. The hiberfil.sys
    existence gate moves to a `precondition` on the FixAction entry
    rather than living in this function, matching how every other
    precondition in this file's catalog works."""
    _run_cmd(["powercfg", "/hibernate", "/size", "50"], output_cb)
    output_cb("Hibernation file resized to 50% of RAM.")


def _hibernation_precondition() -> Optional[str]:
    system_drive = os.environ.get("SystemDrive", "C:")
    hiberfil = os.path.join(system_drive + "\\", "hiberfil.sys")
    if not os.path.exists(hiberfil):
        return "Hibernation is off on this machine — nothing to resize"
    return None


def restart_audio_service(output_cb: Callable[[str], None]) -> None:
    """Restart Windows Audio and Audio Endpoint Builder to resolve glitches and stuck sound endpoints."""
    output_cb("Stopping Windows Audio Endpoint Builder (AudioEndpointBuilder)...")
    _stop_service("AudioEndpointBuilder", output_cb)
    output_cb("Stopping Windows Audio (Audiosrv)...")
    _stop_service("Audiosrv", output_cb)
    output_cb("Starting Windows Audio Endpoint Builder...")
    _start_service("AudioEndpointBuilder", output_cb)
    output_cb("Starting Windows Audio...")
    _start_service("Audiosrv", output_cb)
    output_cb("Windows Audio engine restarted.")


def clear_store_cache(output_cb: Callable[[str], None]) -> None:
    """Reset Microsoft Store cache with wsreset to fix stuck app downloads and package store errors."""
    output_cb("Running wsreset to purge Microsoft Store cache...")
    rc = _run_cmd(["wsreset.exe", "-i"], output_cb, tolerate_failure=True)
    if rc != 0:
        rc = _run_cmd(["wsreset.exe"], output_cb, tolerate_failure=True)
    output_cb("Microsoft Store cache reset completed.")


def sync_system_clock(output_cb: Callable[[str], None]) -> None:
    """Force immediate time synchronization with NTP time server via w32tm."""
    output_cb("Ensuring Windows Time service (w32time) is running...")
    _start_service("w32time", output_cb)
    output_cb("Resynchronizing system clock (w32tm /resync /force)...")
    rc = _run_cmd(["w32tm", "/resync", "/force"], output_cb, tolerate_failure=True)
    if rc == 0:
        output_cb("System clock synchronized successfully.")
    else:
        output_cb("Refreshing time service configuration...")
        _run_cmd(["w32tm", "/config", "/update"], output_cb, tolerate_failure=True)
        _run_cmd(["w32tm", "/resync"], output_cb, tolerate_failure=True)
        output_cb("Time synchronization request submitted.")


def reset_crypto_bits(output_cb: Callable[[str], None]) -> None:
    """Restart Cryptographic Services (CryptSvc) and BITS to clear stuck background transfers and catalog locks."""
    output_cb("Stopping BITS and CryptSvc...")
    _stop_service("bits", output_cb)
    _stop_service("cryptsvc", output_cb)
    output_cb("Starting CryptSvc and BITS...")
    _start_service("cryptsvc", output_cb)
    _start_service("bits", output_cb)
    output_cb("Cryptographic Services and BITS restarted.")


def reset_vss_writers(output_cb: Callable[[str], None]) -> None:
    """Restart Volume Shadow Copy (VSS) and Software Shadow Copy Provider (swprv) to clear stuck writers."""
    output_cb("Stopping Volume Shadow Copy and Provider services...")
    _stop_service("swprv", output_cb)
    _stop_service("vss", output_cb)
    output_cb("Starting Volume Shadow Copy and Provider services...")
    _start_service("vss", output_cb)
    _start_service("swprv", output_cb)
    output_cb("VSS services restarted. Writers reset to stable state.")


def enable_tcp_autotuning(output_cb: Callable[[str], None]) -> None:
    """Enable TCP Window Auto-Tuning to normal via netsh."""
    output_cb("Enabling TCP Window Auto-Tuning...")
    cmd = ["netsh", "int", "tcp", "set", "global", "autotuninglevel=normal"]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0:
            output_cb("TCP Window Auto-Tuning set to 'normal'. Full network throughput restored.")
        else:
            output_cb(f"netsh failed (exit {res.returncode}): {res.stderr or res.stdout}")
    except Exception as exc:
        logger.debug("enable_tcp_autotuning failed: %s", exc)
        output_cb(f"Error configuring TCP Auto-Tuning: {exc}")


def fix_unquoted_service_paths(output_cb: Callable[[str], None]) -> None:
    """Scan and quote any unquoted service ImagePath values in HKLM\\SYSTEM\\CurrentControlSet\\Services."""
    import winreg
    from modules.services_manager.service_audit import is_unquoted_path, quote_image_path
    output_cb("Scanning services for unquoted executable paths...")
    key_path = r"SYSTEM\CurrentControlSet\Services"
    fixed = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as root:
            idx = 0
            while True:
                try:
                    svc_name = winreg.EnumKey(root, idx)
                    idx += 1
                except OSError:
                    logger.debug("Enumerated all services under %s (total: %d)", key_path, idx)
                    break
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{key_path}\\{svc_name}", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as sk:
                        try:
                            val, vtype = winreg.QueryValueEx(sk, "ImagePath")
                        except FileNotFoundError:
                            logger.debug("Service %s has no ImagePath", svc_name)
                            continue
                        if isinstance(val, str) and is_unquoted_path(val):
                            new_val = quote_image_path(val)
                            winreg.SetValueEx(sk, "ImagePath", 0, vtype, new_val)
                            fixed.append(svc_name)
                            output_cb(f"Quoted ImagePath for service '{svc_name}': {new_val}")
                except PermissionError:
                    logger.debug("Access denied opening service key %s for writing", svc_name)
                except OSError as exc:
                    logger.debug("Error checking service %s: %s", svc_name, exc)
    except Exception as exc:
        logger.debug("Error scanning services key: %s", exc)
        output_cb(f"Error scanning services: {exc}")
        return

    if fixed:
        output_cb(f"Successfully quoted ImagePath for {len(fixed)} service(s): {', '.join(fixed)}.")
    else:
        output_cb("All Windows service executable paths are already properly quoted. No changes needed.")


def reset_delivery_optimization(output_cb: Callable[[str], None]) -> None:
    """Reset Delivery Optimization cache and BranchCache."""
    output_cb("Stopping Delivery Optimization service (dosvc)...")
    _stop_service("dosvc", output_cb)
    from core.windows_utils import system_root
    do_cache = os.path.join(
        system_root(), "ServiceProfiles", "NetworkService",
        "AppData", "Local", "Microsoft", "Windows", "DeliveryOptimization", "Cache"
    )
    purged = 0
    if os.path.isdir(do_cache):
        for root, dirs, files in os.walk(do_cache, topdown=False):
            for f in files:
                try:
                    os.remove(os.path.join(root, f))
                    purged += 1
                except OSError as exc:
                    logger.debug("Could not remove DO cache file %s: %s", f, exc)
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError as exc:
                    logger.debug("Could not remove DO cache dir %s: %s", d, exc)
    output_cb(f"Cleared Delivery Optimization cache ({purged} files removed).")
    output_cb("Restarting Delivery Optimization service...")
    _start_service("dosvc", output_cb)
    output_cb("Resetting BranchCache via netsh...")
    _run_cmd(["netsh", "branchcache", "reset"], output_cb, tolerate_failure=True)
    output_cb("Delivery Optimization and BranchCache reset complete.")


def purge_bits_jobs(output_cb: Callable[[str], None]) -> None:
    """Purge and cancel all stuck background BITS transfer jobs."""
    output_cb("Purging BITS transfer queues via bitsadmin...")
    _run_cmd(["bitsadmin", "/reset", "/allusers"], output_cb, tolerate_failure=True)
    output_cb("Clearing PowerShell BITS transfers...")
    script = "Get-BitsTransfer -AllUsers -ErrorAction SilentlyContinue | Remove-BitsTransfer -ErrorAction SilentlyContinue"
    _run_cmd([powershell_exe(), "-NoProfile", "-Command", script], output_cb, tolerate_failure=True)
    output_cb("Restarting BITS service...")
    _stop_service("BITS", output_cb)
    _start_service("BITS", output_cb)
    output_cb("Stuck BITS jobs purged and service restarted.")


def reregister_shell_components(output_cb: Callable[[str], None]) -> None:
    """Re-register core Windows Shell and ActiveX COM components."""
    output_cb("Re-registering core Shell and System COM libraries...")
    dlls = [
        "shell32.dll", "ole32.dll", "oleaut32.dll", "actxprxy.dll",
        "atl.dll", "shdocvw.dll", "urlmon.dll", "mshtml.dll"
    ]
    registered = 0
    for dll in dlls:
        rc = _run_cmd(["regsvr32.exe", "/s", dll], output_cb, tolerate_failure=True)
        if rc == 0:
            registered += 1
    output_cb(f"Re-registered {registered}/{len(dlls)} core Shell libraries.")


def reset_w32tm(output_cb: Callable[[str], None]) -> None:
    """Complete re-registration and forced resynchronization of Windows Time service."""
    output_cb("Stopping Windows Time service (w32time)...")
    _stop_service("w32time", output_cb)
    output_cb("Unregistering Windows Time service...")
    _run_cmd(["w32tm", "/unregister"], output_cb, tolerate_failure=True)
    output_cb("Registering Windows Time service...")
    _run_cmd(["w32tm", "/register"], output_cb, tolerate_failure=True)
    output_cb("Starting Windows Time service...")
    _start_service("w32time", output_cb)
    output_cb("Forcing immediate time synchronization...")
    _run_cmd(["w32tm", "/resync", "/force"], output_cb, tolerate_failure=True)
    output_cb("Windows Time service reset and resynced.")


def enable_winre(output_cb: Callable[[str], None]) -> None:
    """Enable Windows Recovery Environment (reagentc /enable)."""
    output_cb("Enabling Windows Recovery Environment (reagentc /enable)...")
    _run_cmd(["reagentc", "/enable"], output_cb)
    output_cb("Querying WinRE status (reagentc /info)...")
    _run_cmd(["reagentc", "/info"], output_cb, tolerate_failure=True)


def enable_task_history(output_cb: Callable[[str], None]) -> None:
    """Enable Task Scheduler operational history logging via wevtutil."""
    output_cb("Enabling Task Scheduler operational history logging...")
    rc = _run_cmd(["wevtutil", "sl", "Microsoft-Windows-TaskScheduler/Operational", "/e:true"], output_cb)
    if rc == 0:
        output_cb("Task Scheduler history logging enabled successfully.")
    else:
        output_cb("Failed to enable Task Scheduler history logging.")


def _task_history_precondition() -> Optional[str]:
    """Check if Task Scheduler operational logging is already enabled."""
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\WINEVT\Channels\Microsoft-Windows-TaskScheduler/Operational",
            0,
            winreg.KEY_READ,
        ) as k:
            enabled, _ = winreg.QueryValueEx(k, "Enabled")
            if enabled == 1:
                return "Task Scheduler history logging is already enabled"
    except OSError as exc:
        logger.debug("Could not read TaskScheduler channel Enabled flag: %s", exc)
    return None


def _print_queue_precondition() -> Optional[str]:
    """Check if spool folder exists and has queued print files."""
    from core.windows_utils import system_root
    spool_dir = os.path.join(system_root(), "System32", "spool", "PRINTERS")
    if not os.path.isdir(spool_dir):
        return "Spool folder does not exist"
    try:
        files = [f for f in os.listdir(spool_dir) if os.path.isfile(os.path.join(spool_dir, f))]
        if not files:
            return "Print queue is already empty — nothing to clear"
    except OSError as exc:
        logger.debug("Could not inspect spool directory %s: %s", spool_dir, exc)
    return None


def _servicing_precondition() -> Optional[str]:
    """Matches system_health.findings.check_pending_servicing:
    DISM refuses component cleanup if pending.xml exists."""
    from core.windows_utils import system_root
    pending = os.path.join(system_root(), "WinSxS", "pending.xml")
    if os.path.exists(pending):
        return "A servicing transaction is pending (reboot required before running DISM cleanup)"
    return None


def _wu_reset_precondition() -> Optional[str]:
    """Matches Windows Update reset precondition:
    WU client reset should not run if a reboot is pending."""
    from core.windows_utils import is_reboot_pending
    if is_reboot_pending():
        return "A reboot is pending for Windows Update — reboot before resetting cache"
    return None


ALL_ACTIONS: List[FixAction] = [
    # System Repairs
    FixAction("cleanmgr", "Disk Cleanup", "Run Windows Disk Cleanup on C: drive",
              "System Repairs", fn=run_disk_cleanup),
    FixAction("perf_reset", "Reset Performance Counters",
              "Rebuild corrupted performance counter registry keys",
              "System Repairs", reboot_required=False, fn=reset_perf_counters),
    FixAction("wmi_salvage", "Salvage WMI Repository",
              "Check and salvage an inconsistent or corrupted WMI repository",
              "System Repairs", fn=salvage_wmi_repository,
              confirm_text="This will scan and salvage the WMI repository using winmgmt /salvagerepository. "
                           "Management tools and services depending on WMI will be refreshed. Continue?"),
    FixAction("audio_restart", "Restart Audio Engine",
              "Restart Windows Audio and Audio Endpoint Builder to resolve sound glitches",
              "System Repairs", fn=restart_audio_service,
              confirm_text="This will restart Windows Audio services. Active sound playback will briefly pause. Continue?"),
    FixAction("reset_crypto_bits", "Reset Cryptographic & BITS",
              "Restart Cryptographic Services and BITS to clear stuck background downloads",
              "System Repairs", fn=reset_crypto_bits),
    FixAction("reset_vss_writers", "Reset VSS Writers",
              "Restart Volume Shadow Copy and provider services to unblock stuck writers",
              "System Repairs", reboot_required=False, fn=reset_vss_writers,
              confirm_text="This will restart the Volume Shadow Copy and Shadow Provider services to clear any stuck writers. Continue?"),
    FixAction("quote_service_paths", "Fix Unquoted Service Paths",
              "Enclose unquoted service executable paths in quotes to eliminate privilege escalation risks",
              "System Repairs", reboot_required=False, fn=fix_unquoted_service_paths,
              confirm_text="This will scan all installed Windows services and enclose any unquoted paths containing spaces in double quotes. Continue?"),
    FixAction("enable_winre", "Enable Windows RE (Recovery)",
              "Enable the Windows Recovery Environment (reagentc /enable) for startup recovery and repairs",
              "System Repairs", reboot_required=False, fn=enable_winre,
              confirm_text="This will enable the Windows Recovery Environment (WinRE) on this PC so that "
                           "automatic startup repair, diagnostic recovery, and servicing updates can function. Continue?"),
    FixAction("enable_task_history", "Enable Task History",
              "Enable detailed operational history logging for all Scheduled Tasks",
              "System Repairs", reboot_required=False, fn=enable_task_history,
              precondition=_task_history_precondition,
              confirm_text="This will enable the Microsoft-Windows-TaskScheduler/Operational log channel "
                           "so that task runs, failures, and return codes are permanently recorded. Continue?"),
    # Cache & UI
    FixAction("icon_cache", "Rebuild Icon Cache", "Delete and rebuild Windows icon cache",
              "Cache & UI", fn=rebuild_icon_cache,
              confirm_text="This restarts Windows Explorer: open File Explorer windows close, a file copy in progress is interrupted, and the taskbar disappears for a few seconds."),
    FixAction("thumb_cache", "Clear Thumbnail Cache", "Delete Windows thumbnail cache files",
              "Cache & UI", fn=clear_thumbnail_cache,
              confirm_text="This restarts Windows Explorer: open File Explorer windows close, a file copy in progress is interrupted, and the taskbar disappears for a few seconds."),
    FixAction("explorer", "Restart Explorer", "Kill and restart Windows Explorer",
              "Cache & UI", fn=restart_explorer,
              confirm_text="This restarts Windows Explorer: open File Explorer windows close, a file copy in progress is interrupted, and the taskbar disappears for a few seconds."),
    FixAction("prefetch_clear", "Clear Prefetch", "Clear the Windows Prefetch cache",
              "Cache & UI", reboot_required=False, fn=clear_prefetch),
    FixAction("recent_clear", "Clear Recent Files", "Clear recent documents and jump lists",
              "Cache & UI", fn=clear_recent_files),
    FixAction("wsreset", "Clear Microsoft Store Cache",
              "Reset Windows Store cache with wsreset to fix stuck app downloads",
              "Cache & UI", fn=clear_store_cache,
              confirm_text="This runs wsreset to clear the Microsoft Store cache. Continue?"),
    FixAction("reregister_shell", "Re-register Core Shell Libraries",
              "Re-register core Windows Shell and ActiveX COM runtime components",
              "Cache & UI", fn=reregister_shell_components),
    # Network
    FixAction("flush_dns", "Flush DNS", "Clear the DNS resolver cache",
              "Network", fn=flush_dns),
    FixAction("flush_register_dns", "Flush & Register DNS/NetBIOS",
              "Flush DNS cache, purge NetBIOS names, and trigger dynamic DNS registration",
              "Network", fn=flush_and_register_dns),
    FixAction("winsock", "Reset Winsock", "Reset network socket catalog (reboot required)",
              "Network", reboot_required=True, fn=reset_winsock,
              confirm_text="This will reset the Winsock network socket catalog. "
                           "A reboot is required to complete. Continue?"),
    FixAction("tcpip", "Reset TCP/IP", "Reset TCP/IP stack (reboot required)",
              "Network", reboot_required=True, fn=reset_tcpip,
              confirm_text="This will reset all network adapter TCP/IP configurations. "
                           "Network adapters may briefly disconnect. This cannot be undone. Continue?"),
    FixAction("ip_renew", "IP Release/Renew", "Release and renew IP address",
              "Network", fn=ip_release_renew),
    FixAction("flush_arp", "Flush ARP Cache",
              "Purge all cached IP-to-MAC address mappings to resolve network conflicts",
              "Network", fn=flush_arp_cache),
    FixAction("reset_firewall", "Reset Windows Firewall",
              "Restore Windows Defender Firewall to factory default rules and settings",
              "Network", fn=reset_firewall,
              confirm_text="This will reset Windows Defender Firewall to its default configuration. "
                           "Custom inbound and outbound firewall rules added by third-party applications "
                           "will be removed. Continue?"),
    FixAction("network_reset", "Network Reset", "Reset all network adapters to default (reboot required)",
              "Network", reboot_required=True, fn=network_reset,
              confirm_text="This will reset Winsock and the TCP/IP stack. "
                           "Your network connection will briefly drop. This cannot be undone. Continue?"),
    FixAction("sync_time", "Synchronize System Clock",
              "Force immediate time synchronization with NTP time server via w32tm",
              "Network", fn=sync_system_clock),
    FixAction("reset_w32tm", "Reset Windows Time Service",
              "Unregister, re-register, restart w32time and force time resynchronization",
              "Network", fn=reset_w32tm,
              confirm_text="This will re-register the Windows Time service and force an immediate clock resynchronization with the configured NTP server. Continue?"),
    FixAction("enable_tcp_autotuning", "Enable TCP Auto-Tuning",
              "Set TCP Window Auto-Tuning to normal to restore full broadband throughput",
              "Network", reboot_required=False, fn=enable_tcp_autotuning),
    # Windows Update
    FixAction("wu_reset", "Reset Windows Update",
              "Stop WU services, clear caches, restart",
              "Windows Update", fn=reset_windows_update,
              precondition=_wu_reset_precondition,
              confirm_text="This will reset the Windows Update client, clear the "
                           "SoftwareDistribution and catroot2 caches, and restart the "
                           "affected services. Incomplete downloads will be reset. Continue?"),
    FixAction("wu_dlls", "Re-register WU DLLs",
              "Re-register all Windows Update DLL files",
              "Windows Update", fn=reregister_wu_dlls),
    FixAction("wu_scan", "Check for Updates", "Manually trigger Windows Update scan",
              "Windows Update", fn=check_wu_updates),
    FixAction("reset_do_branchcache", "Reset Delivery Optimization & BranchCache",
              "Purge Delivery Optimization cache and reset BranchCache configuration",
              "Windows Update", fn=reset_delivery_optimization,
              confirm_text="This will stop the Delivery Optimization service, clear its local peer cache, and reset BranchCache. In-progress peer downloads will restart from source. Continue?"),
    FixAction("purge_bits_jobs", "Purge Stuck BITS Jobs",
              "Cancel all pending background BITS transfers across all users and restart service",
              "Windows Update", fn=purge_bits_jobs,
              confirm_text="This will cancel and purge all currently queued BITS transfers across all user accounts and restart BITS. Continue?"),
    # Print
    FixAction("print_queue", "Clear Print Queue",
              "Stop Spooler, delete print jobs, restart",
              "Print", fn=clear_print_queue,
              precondition=_print_queue_precondition,
              confirm_text="This will stop the Print Spooler, clear all queued print "
                           "jobs, and restart it. Any job currently printing or queued "
                           "will be lost."),
    FixAction("print_spooler_restart", "Restart Print Spooler",
              "Stop and restart the Print Spooler service",
              "Print", reboot_required=False, fn=restart_print_spooler),
    # Cleanup
    FixAction("clear_event_logs", "Clear Event Logs",
              "Clear System, Application, and Security event logs",
              "Cleanup", fn=clear_event_logs,
              confirm_text="This will clear System, Application, and Security event logs. "
                           "They cannot be recovered. Continue?"),
    FixAction("compact_winsxs", "Compact WinSxS",
              "Run DISM component cleanup to reclaim WinSxS space",
              "Cleanup", fn=compact_winsxs, long_running=True,
              precondition=_servicing_precondition,
              confirm_text="This runs DISM /StartComponentCleanup which can take "
                           "10–30 minutes. The system will remain usable. Continue?"),
    FixAction("wu_deep_clean", "WU Deep Clean",
              "Deep Windows Update component-store cleanup",
              "Cleanup", fn=wu_deep_clean, long_running=True,
              precondition=_servicing_precondition,
              confirm_text="This runs a deep Windows Update cleanup which may take "
                           "10–20 minutes. Continue?"),
    FixAction("clear_clipboard", "Clear Clipboard", "Clear the Windows clipboard content",
              "Cleanup", fn=clear_clipboard),
    FixAction("reset_search", "Reset Windows Search",
              "Restart the Windows Search service and reset its catalog database",
              "Cleanup", fn=reset_search,
              confirm_text="This will restart the Windows Search service and reset its "
                           "catalog database so Windows rebuilds it. Continue?"),
    FixAction("clear_font_cache", "Clear Font Cache",
              "Flush the Windows Font Cache service and purge cached font files",
              "Cleanup", fn=clear_font_cache,
              confirm_text="This will flush the Windows Font Cache and remove cached "
                           "font files. Applications may briefly re-render text. Continue?"),
    FixAction("resize_hibernation", "Right-size Hibernation File",
              "Shrink hiberfil.sys to 50% of RAM without disabling hibernation",
              "Cleanup", fn=resize_hibernation, precondition=_hibernation_precondition,
              confirm_text="This shrinks hiberfil.sys to 50% of RAM (Windows' own default "
                           "since Windows 10) without disabling hibernation. Continue?"),
    FixAction("clear_wer_queue", "Clear WER Queue",
              "Clear pending crash reports and minidumps from Windows Error Reporting",
              "Cleanup", fn=clear_wer_queue, precondition=_wer_queue_precondition,
              confirm_text="This will delete pending crash report packages and minidumps from the Windows Error Reporting queue and archive. Continue?"),
]
