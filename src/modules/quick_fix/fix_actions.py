import glob as _glob
import os
import shutil
import subprocess
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


def _run_cmd(cmd: List[str], output_cb: Callable[[str], None],
             input_bytes: bytes = None) -> int:
    """Run a command, streaming output line by line via output_cb. Returns exit code."""
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


def _stop_service(name: str, output_cb: Callable[[str], None]) -> None:
    output_cb(f"Stopping {name}...")
    try:
        import win32serviceutil
        win32serviceutil.StopService(name)
        output_cb(f"{name} stopped.")
    except Exception as e:
        output_cb(f"Could not stop {name}: {e}")


def _start_service(name: str, output_cb: Callable[[str], None]) -> None:
    output_cb(f"Starting {name}...")
    try:
        import win32serviceutil
        win32serviceutil.StartService(name)
        output_cb(f"{name} started.")
    except Exception as e:
        output_cb(f"Could not start {name}: {e}")


def rebuild_icon_cache(output_cb: Callable[[str], None]) -> None:
    output_cb("Stopping Explorer...")
    subprocess.run(
        ["taskkill", "/f", "/im", "explorer.exe"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    time.sleep(1)
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
    output_cb("Restarting Explorer...")
    subprocess.Popen(["explorer.exe"], creationflags=subprocess.CREATE_NO_WINDOW)
    output_cb("Done.")


def clear_thumbnail_cache(output_cb: Callable[[str], None]) -> None:
    output_cb("Stopping Explorer...")
    subprocess.run(
        ["taskkill", "/f", "/im", "explorer.exe"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    time.sleep(1)
    local = os.environ.get("LOCALAPPDATA", "")
    pattern = os.path.join(local, r"Microsoft\Windows\Explorer\thumbcache_*.db")
    for f in _glob.glob(pattern):
        try:
            os.remove(f)
            output_cb(f"Deleted: {f}")
        except OSError as e:
            output_cb(f"Skip {f}: {e}")
    output_cb("Restarting Explorer...")
    subprocess.Popen(["explorer.exe"], creationflags=subprocess.CREATE_NO_WINDOW)
    output_cb("Done.")


def restart_explorer(output_cb: Callable[[str], None]) -> None:
    output_cb("Killing Explorer...")
    subprocess.run(
        ["taskkill", "/f", "/im", "explorer.exe"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    time.sleep(1)
    output_cb("Starting Explorer...")
    subprocess.Popen(["explorer.exe"], creationflags=subprocess.CREATE_NO_WINDOW)
    output_cb("Done.")


def flush_dns(output_cb: Callable[[str], None]) -> None:
    _run_cmd(["ipconfig", "/flushdns"], output_cb)


def reset_winsock(output_cb: Callable[[str], None]) -> None:
    _run_cmd(["netsh", "winsock", "reset"], output_cb)
    output_cb("Reboot required to complete.")


def reset_tcpip(output_cb: Callable[[str], None]) -> None:
    _run_cmd(["netsh", "int", "ip", "reset"], output_cb)
    output_cb("Reboot required to complete.")


def ip_release_renew(output_cb: Callable[[str], None]) -> None:
    _run_cmd(["ipconfig", "/release"], output_cb)
    _run_cmd(["ipconfig", "/renew"], output_cb)


def reset_windows_update(output_cb: Callable[[str], None]) -> None:
    services = ["wuauserv", "cryptsvc", "bits", "msiserver"]
    for svc in services:
        _stop_service(svc, output_cb)
    dirs_to_clear = [
        r"C:\Windows\SoftwareDistribution",
        r"C:\Windows\System32\catroot2",
    ]
    for d in dirs_to_clear:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
                output_cb(f"Deleted: {d}")
            except OSError as e:
                output_cb(f"Error deleting {d}: {e}")
    for svc in services:
        _start_service(svc, output_cb)
    output_cb("Windows Update reset complete.")


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
        rc = _run_cmd(["regsvr32", "/s", dll], output_cb)
        output_cb(f"regsvr32 {dll}: {'OK' if rc == 0 else 'Failed'}")
    output_cb("Done.")


def clear_print_queue(output_cb: Callable[[str], None]) -> None:
    """Stop the spooler, clear queued jobs, and ALWAYS restart the
    spooler even if a spool file was locked and could not be deleted --
    an all-or-nothing chain here would leave printing broken with just a
    generic error. This function has had this unconditional-restart
    behavior since it was created. Quick Cleanup's own `_clear_print_queue`
    needed a similar fix later (commit f954f63, 2026-09-14) after shipping
    with an all-`&&` chain that could leave the spooler stopped on a
    failed delete."""
    _stop_service("Spooler", output_cb)
    spool_dir = r"C:\Windows\System32\spool\PRINTERS"
    if os.path.isdir(spool_dir):
        for f in os.listdir(spool_dir):
            fpath = os.path.join(spool_dir, f)
            try:
                os.remove(fpath)
                output_cb(f"Deleted: {f}")
            except OSError as e:
                output_cb(f"Skip {f}: {e}")
    _start_service("Spooler", output_cb)
    output_cb("Print queue cleared.")


def run_disk_cleanup(output_cb: Callable[[str], None]) -> None:
    """Run cleanmgr /d C: /sagerun:1 with predefined cleanup."""
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
    prefetch_dir = r"C:\Windows\Prefetch"
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
        ["powershell", "-NoProfile", "-Command", script],
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
    _run_cmd(["cmd", "/c", "echo off | clip"], output_cb)
    output_cb("Clipboard cleared.")


def reset_search(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _reset_search."""
    _stop_service("WSearch", output_cb)
    _start_service("WSearch", output_cb)
    output_cb("Windows Search reset.")


def clear_font_cache(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _clear_font_cache."""
    _stop_service("FontCache", output_cb)
    _start_service("FontCache", output_cb)
    output_cb("Font cache cleared.")


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


ALL_ACTIONS: List[FixAction] = [
    # System Repairs
    FixAction("cleanmgr", "Disk Cleanup", "Run Windows Disk Cleanup on C: drive",
              "System Repairs", fn=run_disk_cleanup),
    FixAction("perf_reset", "Reset Performance Counters",
              "Rebuild corrupted performance counter registry keys",
              "System Repairs", reboot_required=False, fn=reset_perf_counters),
    # Cache & UI
    FixAction("icon_cache", "Rebuild Icon Cache", "Delete and rebuild Windows icon cache",
              "Cache & UI", fn=rebuild_icon_cache),
    FixAction("thumb_cache", "Clear Thumbnail Cache", "Delete Windows thumbnail cache files",
              "Cache & UI", fn=clear_thumbnail_cache),
    FixAction("explorer", "Restart Explorer", "Kill and restart Windows Explorer",
              "Cache & UI", fn=restart_explorer),
    FixAction("prefetch_clear", "Clear Prefetch", "Clear the Windows Prefetch cache",
              "Cache & UI", reboot_required=False, fn=clear_prefetch),
    FixAction("recent_clear", "Clear Recent Files", "Clear recent documents and jump lists",
              "Cache & UI", fn=clear_recent_files),
    # Network
    FixAction("flush_dns", "Flush DNS", "Clear the DNS resolver cache",
              "Network", fn=flush_dns),
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
    FixAction("network_reset", "Network Reset", "Reset all network adapters to default (reboot required)",
              "Network", reboot_required=True, fn=network_reset,
              confirm_text="This will reset Winsock and the TCP/IP stack. "
                           "Your network connection will briefly drop. This cannot be undone. Continue?"),
    # Windows Update
    FixAction("wu_reset", "Reset Windows Update",
              "Stop WU services, clear caches, restart",
              "Windows Update", fn=reset_windows_update,
              confirm_text="This will reset the Windows Update client, clear the "
                           "SoftwareDistribution and catroot2 caches, and restart the "
                           "affected services. This cannot be undone. Continue?"),
    FixAction("wu_dlls", "Re-register WU DLLs",
              "Re-register all Windows Update DLL files",
              "Windows Update", fn=reregister_wu_dlls),
    FixAction("wu_scan", "Check for Updates", "Manually trigger Windows Update scan",
              "Windows Update", fn=check_wu_updates),
    # Print
    FixAction("print_queue", "Clear Print Queue",
              "Stop Spooler, delete print jobs, restart",
              "Print", fn=clear_print_queue,
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
              confirm_text="This runs DISM /StartComponentCleanup which can take "
                           "10–30 minutes. The system will remain usable. Continue?"),
    FixAction("wu_deep_clean", "WU Deep Clean",
              "Deep Windows Update component-store cleanup",
              "Cleanup", fn=wu_deep_clean, long_running=True,
              confirm_text="This runs a deep Windows Update cleanup which may take "
                           "10–20 minutes. Continue?"),
    FixAction("clear_clipboard", "Clear Clipboard", "Clear the Windows clipboard content",
              "Cleanup", fn=clear_clipboard),
    FixAction("reset_search", "Reset Windows Search",
              "Restart the Windows Search service and clear its database",
              "Cleanup", fn=reset_search,
              confirm_text="This will restart the Windows Search service and clear its "
                           "database. Search may be briefly unavailable. Continue?"),
    FixAction("clear_font_cache", "Clear Font Cache",
              "Flush the Windows Font Cache service",
              "Cleanup", fn=clear_font_cache,
              confirm_text="This will flush the Windows Font Cache by stopping the "
                           "FontCache service. Applications may briefly re-render text. Continue?"),
    FixAction("resize_hibernation", "Right-size Hibernation File",
              "Shrink hiberfil.sys to 50% of RAM without disabling hibernation",
              "Cleanup", fn=resize_hibernation, precondition=_hibernation_precondition,
              confirm_text="This shrinks hiberfil.sys to 50% of RAM (Windows' own default "
                           "since Windows 10) without disabling hibernation. Continue?"),
]
