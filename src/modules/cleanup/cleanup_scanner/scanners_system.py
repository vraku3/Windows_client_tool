"""Cleanup scanners: system category (auto-split from cleanup_scanner.py)."""
import logging
import os
import re
import shutil
import glob
import string
import subprocess
import time
from typing import List, Callable, Optional, Tuple

from modules.cleanup.cleanup_scanner._common import (
    ScanItem, ScanResult, get_dir_size, _make_item, _make_item_with_age,
)

from modules.cleanup.cleanup_scanner import (
    drives, known_folders, virtual_disks)

logger = logging.getLogger(__name__)

def scan_prefetch(min_age_days: int = 0) -> ScanResult:
    """Windows Prefetch .pf files — safe to delete, will be re-created as needed."""
    result = ScanResult()
    pf_dir = r"C:\Windows\Prefetch"
    for pf in glob.glob(os.path.join(pf_dir, "*.pf")):
        item = _make_item_with_age(pf, safety="safe", min_age_days=min_age_days)
        if item:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_recycle_bin(min_age_days: int = 0) -> ScanResult:
    """Recycle Bin on all fixed drives."""
    result = ScanResult()
    for drive in string.ascii_uppercase:
        rb = f"{drive}:\\$Recycle.Bin"
        if os.path.exists(rb):
            item = _make_item(rb, safety="safe", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_delivery_optimization(min_age_days: int = 0) -> ScanResult:
    """Delivery Optimization peer-to-peer update cache."""
    result = ScanResult()
    targets = [
        r"C:\Windows\SoftwareDistribution\DeliveryOptimization\Cache",
    ]
    for t in targets:
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_logs(min_age_days: int = 0) -> ScanResult:
    """CBS, DISM, Windows Update, and setup log files."""
    result = ScanResult()
    log_patterns = [
        (r"C:\Windows\Logs\CBS", "*.log"),
        (r"C:\Windows\Logs\DISM", "dism.log"),
        (r"C:\Windows\Logs\MoSetup", "*.log"),
        (r"C:\Windows", "setupapi.*.log"),
        (r"C:\Windows", "WindowsUpdate.log"),
    ]
    for dir_path, pattern in log_patterns:
        for f in glob.glob(os.path.join(dir_path, pattern)):
            item = _make_item_with_age(f, safety="caution", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_windows_old(min_age_days: int = 0) -> ScanResult:
    """Windows.old folder left after an in-place upgrade (often 10-30 GB).

    safety="caution", not "safe": Microsoft does not support deleting
    this manually (it shares hardlinked files with the live OS); the
    documented removal path is Disk Cleanup / Storage Sense. Reported
    and selectable here, but QuickCleanupTab's dashboard-level "Clean
    All Safe" only sweeps safety=="safe" items, so this is never
    silently deleted by that one button anymore.
    """
    result = ScanResult()
    system_drive = os.environ.get("SystemDrive", "C:")
    path = os.path.join(system_drive + "\\", "Windows.old")
    item = _make_item(path, safety="caution", min_age_days=min_age_days)
    if item:
        result.items.append(item)
        result.total_size = item.size
    return result

def scan_app_caches(min_age_days: int = 0) -> ScanResult:
    """Common app caches: Teams, Discord, Slack, Spotify, VS Code."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    targets = [
        os.path.join(appdata,  r"Microsoft\Teams\Cache"),
        os.path.join(appdata,  r"Microsoft\Teams\blob_storage"),
        os.path.join(appdata,  r"Microsoft\Teams\databases"),
        os.path.join(appdata,  r"Microsoft\Teams\GPUCache"),
        os.path.join(appdata,  r"discord\Cache"),
        os.path.join(appdata,  r"discord\GPUCache"),
        os.path.join(appdata,  r"Slack\Cache"),
        os.path.join(appdata,  r"Slack\GPUCache"),
        os.path.join(local,    r"Spotify\Data"),
        os.path.join(local,    r"Microsoft\VSCode\Cache"),
        os.path.join(local,    r"Microsoft\VSCode\CachedExtensionVSIXs"),
        os.path.join(local,    r"Microsoft\VSCode\CachedData"),
    ]
    for t in targets:
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_appdata_autodiscover(min_age_days: int = 0) -> ScanResult:
    """Auto-discover cache folders under %LOCALAPPDATA% and %APPDATA% (up to 3 dirs deep).
    Skips known browser paths (covered by browser_scanner) and returns only non-empty dirs.
    """
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")

    _CACHE_DIR_NAMES = {
        "cache", "cache2", "cacheddata", "gpucache", "code cache",
        "blob_storage", "crashpad", "crash reports",
        "grshaderCache", "shadercache", "media cache",
        # The rest of this set was compiled from a live directory-name
        # census of %LOCALAPPDATA% (`Get-ChildItem -Recurse -Depth 3
        # -Filter '*cache*'`) rather than guessed -- every name here was
        # observed as a REAL folder on a real machine, not assumed from a
        # generic "sounds like a cache" pattern. Deliberately NOT included:
        # "Package Cache" (MSI/Burn's own installer cache -- removing it
        # can break a future repair/uninstall of an unrelated app) and
        # anything without "cache" unambiguously in its own name.
        "caches", "cachestorage", "d3dscache", "dx9cache", "dxcache",
        "dxccache", "dawncache", "dawngraphitecache", "dawnwebgpucache",
        "graphitedawncache", "fontcache", "gpupersistentcache", "oglcache",
        "vkcache", "persistentmapcache", "iecompatcache", "iecompatuacache",
        "ppbcompatcache", "ppbcompatuacache", "component_crx_cache",
        "extensions_crx_cache", "node-compile-cache", "qmlcache",
        "apicache", "webservicecache", "resourceinfocache",
        "targetedcontentcache", "optimization_guide_hint_cache_store",
        "actioncentercache", "browsercache", "browsercaches",
        "transcoded files cache", "htmlcache", "cl.cache",
        "mruservicecache",
    }
    _CACHE_DIR_NAMES = {s.lower() for s in _CACHE_DIR_NAMES}

    _BROWSER_MARKERS = {
        "bravesoftware", "google", "microsoftedge", "vivaldi", "thorium",
        "chromium", "yandex", "opera software", "mozilla", "librewolf",
        "waterfox", "moonchild productions",
    }

    seen: set = set()

    def _is_browser_path(path_lower: str) -> bool:
        return any(m in path_lower for m in _BROWSER_MARKERS)

    def _walk(root: str, depth: int) -> None:
        if depth < 0:
            return
        try:
            for entry in os.scandir(root):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if _is_browser_path(entry.path.lower()):
                    continue
                if entry.name.lower() in _CACHE_DIR_NAMES:
                    rp = os.path.realpath(entry.path)
                    if rp not in seen:
                        seen.add(rp)
                        item = _make_item(entry.path, safety="safe", min_age_days=min_age_days)
                        if item and item.size > 0:
                            result.items.append(item)
                            result.total_size += item.size
                else:
                    _walk(entry.path, depth - 1)
        except (PermissionError, OSError):
            logger.debug("Ignored (PermissionError, OSError)", exc_info=True)

    for base in (local, appdata):
        if base and os.path.isdir(base):
            _walk(base, 2)

    result.items.sort(key=lambda x: x.size, reverse=True)
    return result

def scan_dmf_logs(min_age_days: int = 0) -> ScanResult:
    """Diagnostic Module Framework logs in Windows\\Logs\\DMF — can be large."""
    result = ScanResult()
    dmf_dir = r"C:\Windows\Logs\DMF"
    if not os.path.isdir(dmf_dir):
        return result
    for ext in ("*.log", "*.etl"):
        for f in glob.glob(os.path.join(dmf_dir, ext)):
            item = _make_item_with_age(f, safety="caution", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    return result

def cleanup_winsxs(progress_cb: Optional[Callable[[int, int], None]] = None) -> bool:
    """Run Dism.exe /StartComponentCleanup to reduce WinSxS superseded components.

    This operation can take 30 minutes or more.
    Returns True on success, False on failure.
    """
    try:
        proc = subprocess.Popen(
            ["Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # Poll periodically so we can report progress
        while True:
            retcode = proc.poll()
            if retcode is not None:
                break
            if progress_cb:
                progress_cb(-1, 0)  # indeterminate
            time.sleep(5)
        stdout, stderr = proc.communicate()
        if progress_cb:
            progress_cb(1, 1)
        logger.info("WinSxS cleanup finished: rc=%s", retcode)
        return retcode == 0
    except Exception as e:
        logger.error("WinSxS cleanup failed: %s", e)
        return False

def scan_store_app_caches(min_age_days: int = 0) -> ScanResult:
    """UWP / Store app local caches under %LocalAppData%\\Packages\\*\\LocalCache. Skips items < 1 MB."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    packages_dir = os.path.join(local, "Packages")
    if not os.path.isdir(packages_dir):
        return result
    try:
        for pkg in os.scandir(packages_dir):
            if not pkg.is_dir(follow_symlinks=False):
                continue
            cache_path = os.path.join(pkg.path, "LocalCache")
            if not os.path.isdir(cache_path):
                continue
            size = get_dir_size(cache_path)
            if size < 1024 * 1024:
                continue
            result.items.append(ScanItem(path=cache_path, size=size, is_dir=True, safety="safe"))
            result.total_size += size
    except (PermissionError, OSError):
        logger.debug("Ignored (PermissionError, OSError)", exc_info=True)
    result.items.sort(key=lambda x: x.size, reverse=True)
    return result

def scan_recent_files(min_age_days: int = 0) -> ScanResult:
    """Recent .lnk shortcuts and jump list destinations."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", "")
    targets = [
        os.path.join(appdata, r"Microsoft\Windows\Recent"),
        os.path.join(appdata, r"Microsoft\Windows\Recent\AutomaticDestinations"),
        os.path.join(appdata, r"Microsoft\Windows\Recent\CustomDestinations"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        for f in glob.glob(os.path.join(t, "*.lnk")):
            item = _make_item_with_age(f, safety="safe", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
        for f in glob.glob(os.path.join(t, "*.automaticDestinations-ms")):
            item = _make_item_with_age(f, safety="safe", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
        for f in glob.glob(os.path.join(t, "*.customDestinations-ms")):
            item = _make_item_with_age(f, safety="safe", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_game_caches(min_age_days: int = 0) -> ScanResult:
    """Caches for Steam, Epic, Xbox, Battle.net, EA app, Ubisoft Connect, GOG Galaxy, Discord."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    targets = [
        # Steam
        os.path.join(local, r"Programs\Steam\steamapps"),
        os.path.join(local, r"Programs\Steam\shadercache"),
        os.path.join(local, r"Programs\Steam\htmlcache"),
        os.path.join(local, r"Programs\Steam\downloads"),
        # Epic
        os.path.join(appdata, r"Epic\EpicGamesLauncher\Data\Manifests"),
        os.path.join(local, r"EpicGamesLauncher\Data\Portal\Cache"),
        # Xbox / Gaming Services
        os.path.join(local, r"Packages\Microsoft.GamingServices_*\LocalCache"),
        os.path.join(local, r"Packages\Microsoft.XboxGamingOverlay_*\LocalCache"),
        os.path.join(local, r"Packages\FamilyNotifications.*\LocalState"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), r"XboxLiveDeviceInfo"),
        # Battle.net
        os.path.join(appdata, r"Blizzard\Battle.net\Cache"),
        # EA app
        os.path.join(appdata, r"EA Desktop\Cache"),
        os.path.join(appdata, r"Electronic Arts\EA Desktop\Cache"),
        # Ubisoft Connect
        os.path.join(appdata, r"Ubisoft\Connect\cache"),
        # GOG Galaxy
        os.path.join(appdata, r"GOG.com\Galaxy\Cache"),
        # Discord
        os.path.join(appdata, r"discord\Cache"),
        os.path.join(appdata, r"discord\GPUCache"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        try:
            size = get_dir_size(t)
            if size > 0:
                item = ScanItem(path=t, size=size, is_dir=True, safety="safe")
                result.items.append(item)
                result.total_size += size
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
    return result

def scan_ide_caches(min_age_days: int = 0) -> ScanResult:
    """JetBrains, Visual Studio, Notepad++, FileZilla caches."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    temp = os.environ.get("TEMP", "")

    # JetBrains IDEs (find all IDE folders under JetBrains)
    jb_root = os.path.join(local, r"JetBrains")
    if os.path.isdir(jb_root):
        for ide in glob.glob(os.path.join(jb_root, "*IDE*")):
            for sub in ("caches", "index", "logs"):
                sub_path = os.path.join(ide, sub)
                if os.path.isdir(sub_path):
                    item = _make_item(sub_path, safety="safe", min_age_days=min_age_days)
                    if item and item.size > 0:
                        result.items.append(item)
                        result.total_size += item.size

    # Visual Studio .vs folder and component model cache
    vs_folder = os.path.join(local, r"Microsoft\VisualStudio")
    if os.path.isdir(vs_folder):
        for vs_ver in os.listdir(vs_folder):
            vs_path = os.path.join(vs_folder, vs_ver)
            if not os.path.isdir(vs_path):
                continue
            vs_items = [
                os.path.join(vs_path, ".vs"),
                os.path.join(vs_path, "ComponentModelCache"),
                os.path.join(vs_path, "Settings"),
            ]
            for vi in vs_items:
                if os.path.isdir(vi):
                    item = _make_item(vi, safety="safe", min_age_days=min_age_days)
                    if item and item.size > 0:
                        result.items.append(item)
                        result.total_size += item.size
    # VS ~vs* temp files
    if temp:
        for f in glob.glob(os.path.join(temp, "~vs*")):
            item = _make_item_with_age(f, safety="safe", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    # Notepad++ backups
    npp_dir = os.path.join(appdata, r"Notepad++\backup")
    if os.path.isdir(npp_dir):
        item = _make_item(npp_dir, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    # FileZilla
    fz_dir = os.path.join(appdata, r"FileZilla")
    if os.path.isdir(fz_dir):
        item = _make_item(fz_dir, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_print_spooler(min_age_days: int = 0) -> ScanResult:
    """Windows print spooler queue — only when spooler service is stopped."""
    result = ScanResult()
    spool_printers = r"C:\Windows\System32\spool\PRINTERS"
    spool_servers = r"C:\Windows\System32\spool\SERVERS"
    # Check service status
    try:
        proc = subprocess.run(
            ["sc", "query", "spooler"],
            capture_output=True, text=True, errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if "RUNNING" in proc.stdout.upper():
            # Service running — mark as danger so it's never auto-selected
            item = _make_item(spool_printers, safety="danger", min_age_days=min_age_days)
            if item and item.size > 0:
                result.items.append(item)
                result.total_size += item.size
            return result
    except Exception as e:
        logger.warning("Spooler scan failed: %s", e)
    # Spooler not running — safe to clean
    for t in [spool_printers, spool_servers]:
        if os.path.isdir(t):
            item = _make_item(t, safety="caution", min_age_days=min_age_days)
            if item and item.size > 0:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_etl_logs(min_age_days: int = 0) -> ScanResult:
    """WindowsUpdate ETL, DeliveryOptimization ETL, and ScriptArtifacts logs."""
    result = ScanResult()
    targets = [
        (r"C:\Windows\Logs\WindowsUpdate", "*.etl"),
        (r"C:\Windows\ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization\Logs", "*.log"),
        (r"C:\Windows\Temp\ScriptArtifacts", "*.log"),
    ]
    for dir_path, pattern in targets:
        if not os.path.isdir(dir_path):
            continue
        for f in glob.glob(os.path.join(dir_path, pattern)):
            item = _make_item_with_age(f, safety="caution", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_maps_cache(min_age_days: int = 0) -> ScanResult:
    """Windows Maps local tile cache and TileDataLayer database."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Local\Packages\Microsoft.WindowsMaps_*\LocalState"),
        os.path.join(local, r"TileDataLayer\Database"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        try:
            size = get_dir_size(t)
            if size > 0:
                result.items.append(ScanItem(path=t, size=size, is_dir=True, safety="safe"))
                result.total_size += size
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
    return result

def scan_delivery_opt_user(min_age_days: int = 0) -> ScanResult:
    """Per-user Delivery Optimization cache (separate from system-wide)."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\DeliveryOptimization\Cache"),
        os.path.join(local, r"Microsoft\Windows\DeliveryOptimization\Logs"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_crash_dumps_system(min_age_days: int = 0) -> ScanResult:
    """System-wide crash dumps: MEMORY.DMP, Minidump folder, LiveKernelReports."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"Minidump"),
        os.path.join(windir, r"LiveKernelReports"),
        os.path.join(windir, r"MEMORY.DMP"),
        os.path.join(windir, r"cluster.log"),
    ]
    for t in targets:
        if not os.path.exists(t):
            continue
        if os.path.isfile(t):
            item = _make_item_with_age(t, safety="caution", min_age_days=min_age_days)
        else:
            item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_bits_transfers(min_age_days: int = 0) -> ScanResult:
    """BITS (Background Intelligent Transfer Service) transfer job queue files."""
    result = ScanResult()
    targets = [
        r"C:\Windows\Tasks\BITS",
        r"C:\ProgramData\Microsoft\Windows\BITS",
        os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\BITS"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_perflogs(min_age_days: int = 0) -> ScanResult:
    """Windows Performance Logs — BLG files and output from scheduled perf monitoring."""
    result = ScanResult()
    targets = [
        r"C:\PerfLogs\Admin",
        r"C:\PerfLogs\Custom",
        r"C:\PerfLogs\System",
        r"C:\Windows\System32\LogFiles\WMI\RtTracking",
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_backup_files(min_age_days: int = 0) -> ScanResult:
    """Common backup file patterns: .bak, .tmp, ~, .old left behind after updates."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(windir, r"*.bak"),
        os.path.join(windir, r"*.old"),
        os.path.join(windir, r"*.tmp"),
        os.path.join(windir, r"Installer\*.bak"),
        os.path.join(local, r"Microsoft\Windows\*.bak"),
        os.path.join(local, r"Microsoft\Windows\*.old"),
    ]
    for t in targets:
        dir_path, pattern = os.path.split(t)
        if not os.path.isdir(dir_path):
            continue
        for f in glob.glob(os.path.join(dir_path, pattern)):
            item = _make_item_with_age(f, safety="safe", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    # Also scan Windows\Downloaded Program Files (orphaned installs)
    dpfs = os.path.join(windir, r"Downloaded Program Files")
    item = _make_item(dpfs, safety="caution", min_age_days=min_age_days)
    if item and item.size > 0:
        result.items.append(item)
        result.total_size += item.size
    return result

def scan_install_temp(min_age_days: int = 0) -> ScanResult:
    """Windows installation temp files and $INPLACE.~BT/~TT folders."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"$inplace.trinidad"),
        os.path.join(windir, r"$WINDOWS.~BT"),
        os.path.join(windir, r"$WINDOWS.~LS"),
        os.path.join(windir, r"DownloadedInstallations"),
        os.path.join(windir, r"Panther\*-ms"),
    ]
    for t in targets:
        if not os.path.isdir(t) and not os.path.isfile(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_search_index(min_age_days: int = 0) -> ScanResult:
    r"""Windows Search index database files and temp rebuilding data.

    The index is MACHINE-WIDE and lives under %ProgramData%, in
    `Microsoft\Search\Data\Applications\Windows` (Windows.edb and its
    logs). This scanner used to look only under %LOCALAPPDATA%, where
    nothing has lived since Windows 8, so it returned 0 items on every
    Windows 11 machine for the life of the feature — which is
    indistinguishable from "your search index is empty".

    The per-user paths are kept: a roaming profile still puts UsageEvents
    there.
    """
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    programdata = os.environ.get("ProgramData", r"C:\ProgramData")
    targets = [
        os.path.join(programdata, r"Microsoft\Search\Data\Applications\Windows"),
        os.path.join(programdata, r"Microsoft\Search\Data\Temp"),
        os.path.join(local, r"Microsoft\Search\Data\Applications"),
        os.path.join(local, r"Microsoft\Search\Data\Temp"),
        os.path.join(local, r"Microsoft\Search\Data\UsageEvents"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_diagnostic_data(min_age_days: int = 0) -> ScanResult:
    """Windows Diagnostic Data Viewer staged data and queued data files."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    progdata = os.environ.get("PROGRAMDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\DiagnosticDataViewer"),
        os.path.join(progdata, r"Microsoft\Windows\DiagnosticsDataViewer"),
        os.path.join(local, r"Microsoft\Windows\Feedback\FeedbackHub"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_powershell_logs(min_age_days: int = 0) -> ScanResult:
    """PowerShell transcription, module logging, and script execution logs."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(local, r"Microsoft\Windows\PowerShell\PSReadLine"),
        os.path.join(local, r"Microsoft\Windows\PowerShell\TranscriptLogs"),
        os.path.join(windir, r"System32\WindowsPowerShell\v1.0\Logs"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_group_policy_logs(min_age_days: int = 0) -> ScanResult:
    """Group Policy client-side extension logs and results."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(windir, r"Debug"),
        os.path.join(windir, r"Logs\GroupPolicy"),
        os.path.join(local, r"GroupPolicy\logs"),
        os.path.join(windir, r"System32\winevt\Logs\Microsoft-Windows-GroupPolicy%4Operational.evtx"),
    ]
    for t in targets:
        if not os.path.isfile(t):
            if not os.path.isdir(t):
                continue
        item = _make_item_with_age(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_installer_cache(min_age_days: int = 0) -> ScanResult:
    """Windows Installer download cache and patch removal queue."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\Installer"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        try:
            for entry in os.scandir(t):
                if entry.name.endswith('.msi') or entry.name.endswith('.msp'):
                    fpath = entry.path
                    try:
                        size = os.path.getsize(fpath)
                        if min_age_days > 0:
                            mtime = os.path.getmtime(fpath)
                            if (time.time() - mtime) < min_age_days * 86400:
                                continue
                        result.items.append(ScanItem(path=fpath, size=size, is_dir=False, safety="caution"))
                        result.total_size += size
                    except OSError:
                        logger.debug("Ignored OSError", exc_info=True)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
    return result

def scan_wmi_logs(min_age_days: int = 0) -> ScanResult:
    """WMI (Windows Management Instrumentation) logs and permanent event consumers."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"System32\wbem\Logs"),
        os.path.join(windir, r"System32\wbem\Repository\FS"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_print_nightmare_logs(min_age_days: int = 0) -> ScanResult:
    """Print spooler transaction logs and spooling queue residuals."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"System32\spool\PRINTERS"),
        os.path.join(windir, r"System32\spool\SERVERS"),
        os.path.join(windir, r"System32\spool\drivers"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_msi_logs(min_age_days: int = 0) -> ScanResult:
    r"""MSI installer verbose logs in Windows\Logs\MSI and Temp."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"Logs\MSI"),
        os.path.join(windir, r"Logs\WindowsUpdate"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        for f in glob.glob(os.path.join(t, "*.log")):
            item = _make_item_with_age(f, safety="safe", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
        for f in glob.glob(os.path.join(t, "*.etl")):
            item = _make_item_with_age(f, safety="safe", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_appx_logs(min_age_days: int = 0) -> ScanResult:
    """AppX/MSIX package installation logs and staging data."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\AppxPackages"),
        os.path.join(local, r"Microsoft\Windows\PackageManagement"),
        os.path.join(local, r"Microsoft\Windows\RemotePackages"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_defender_logs(min_age_days: int = 0) -> ScanResult:
    """Windows Defender operational logs, scan logs, and threat remediation logs."""
    result = ScanResult()
    progdata = os.environ.get("PROGRAMDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(progdata, r"Microsoft\Windows Defender\Logs"),
        os.path.join(progdata, r"Microsoft\Windows Defender\Support"),
        os.path.join(local, r"Microsoft\Windows Defender\Scans\History"),
        os.path.join(local, r"Microsoft\Windows Defender\Quarantine\Items"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_iso_vhd_files(min_age_days: int = 0) -> ScanResult:
    """Orphaned .iso and .vhd files in common download folders."""
    result = ScanResult()
    downloads = known_folders.known_folder("Downloads")
    if not downloads:
        return result
    targets = [os.path.join(downloads, pattern)
               for pattern in ("*.iso", "*.vhd", "*.vhdx")]
    for t in targets:
        dir_path, pattern = os.path.split(t)
        if not os.path.isdir(dir_path):
            continue
        for f in glob.glob(os.path.join(dir_path, pattern)):
            item = _make_item_with_age(f, safety="caution", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_msp_patches(min_age_days: int = 0) -> ScanResult:
    """Orphaned Windows Installer .msp patch files."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"Installer\*.msp"),
    ]
    for t in targets:
        dir_path, pattern = os.path.split(t)
        if not os.path.isdir(dir_path):
            continue
        for f in glob.glob(os.path.join(dir_path, pattern)):
            item = _make_item_with_age(f, safety="caution", min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_font_files_temp(min_age_days: int = 0) -> ScanResult:
    """Windows Font loader temp staging and fontinstaller temp files."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    temp = os.environ.get("TEMP", "")
    targets = [
        os.path.join(windir, r"ServiceProfiles\LocalService\AppData\Local\FontDrivers"),
        os.path.join(temp, r"Font*"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_optional_features(min_age_days: int = 0) -> ScanResult:
    """Windows optional features manifests backup (danger) and install temp (safe)."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        (os.path.join(windir, r"WinSxS\ManifestBackup"), "danger"),
        (os.path.join(windir, r"WinSxS\InstallTemp"), "safe"),
    ]
    for t, safety in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety=safety, min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_printer_driver_cache(min_age_days: int = 0) -> ScanResult:
    """Orphaned printer driver files and print capture archives."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"System32\spool\drivers"),
        os.path.join(windir, r"System32\spool\PRINTERS"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_dns_cache(min_age_days: int = 0) -> ScanResult:
    """Flush DNS resolver cache — safe operation, no file deletion needed."""
    result = ScanResult()
    return result

def scan_old_av_quarantine(min_age_days: int = 0) -> ScanResult:
    """Old antivirus quarantine files from expired/uninstalled AV products."""
    result = ScanResult()
    progdata = os.environ.get("PROGRAMDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(progdata, r"Avast\ quarantine"),
        os.path.join(progdata, r"AVG\ quarantine"),
        os.path.join(progdata, r"Malwarebytes\ quarantine"),
        os.path.join(local, r"Microsoft Windows Defender\Quarantine"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_virtual_drives(min_age_days: int = 0) -> ScanResult:
    """Daemon Tools, Alcohol 120%, and WinCDEmu virtual drive images and cfg files."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(appdata, r"DaemonBuilder\ImageCache"),
        os.path.join(appdata, r"DAEMON Tools Lite\ImageCache"),
        os.path.join(local, r"WinCDEmu"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_usb_shadow_copies(min_age_days: int = 0) -> ScanResult:
    """USB device shadow copies and ReadyBoost cache."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"System32\config\systemprofile\AppData\Local\Low\Microsoft\CryptnetUrlCache"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_sysinternals_logs(min_age_days: int = 0) -> ScanResult:
    """Sysinternals (Procmon, PsExec, etc.) log and database files."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Sysinternals"),
        os.path.join(appdata, r"Sysinternals"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_network_debug_logs(min_age_days: int = 0) -> ScanResult:
    """Network diagnostic ETL traces and packet capture log files."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(windir, r"System32\LogFiles\Nettettl"),
        os.path.join(local, r"Microsoft\Windows\Network Diagnostics"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_powershell_modules_cache(min_age_days: int = 0) -> ScanResult:
    """PowerShell module telemetry, PSModulePath download cache, and transcript logs."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(appdata, r"PowerShell\DownloadedModules"),
        os.path.join(local, r"Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt"),
    ]
    for t in targets:
        if not os.path.exists(t):
            continue
        if os.path.isfile(t):
            item = _make_item_with_age(t, safety="safe", min_age_days=min_age_days)
        else:
            item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_insider_logs(min_age_days: int = 0) -> ScanResult:
    """Windows Insider feedback hub diagnostic bundles and telemetry staging."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\FeedbackHub\data"),
        os.path.join(local, r"Microsoft\Windows\FeedbackHub\Logs"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_winSxS_temp(min_age_days: int = 0) -> ScanResult:
    """WinSxS pending file rename operations and install temp."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"WinSxS\InstallTemp"),
        os.path.join(windir, r"WinSxS\Temp"),
        os.path.join(windir, r"WinSxS\pisi.graph"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_userprofile_temp(min_age_days: int = 0) -> ScanResult:
    r"""Per-user profile temp files scattered across AppData\Local\Temp."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    temp = os.environ.get("TEMP", "")
    targets = [
        os.path.join(local, r"Temp"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        if t == temp:
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_downloads_folder_old(min_age_days: int = 0) -> ScanResult:
    """Old files in the Downloads folder (recursive) older than min_age_days."""
    result = ScanResult()
    dl = known_folders.known_folder("Downloads")
    if not dl or not os.path.isdir(dl):
        return result
    for entry in os.scandir(dl):
        try:
            if entry.is_file():
                if min_age_days > 0:
                    mtime = os.path.getmtime(entry.path)
                    if (time.time() - mtime) < min_age_days * 86400:
                        continue
                size = os.path.getsize(entry.path)
                result.items.append(ScanItem(path=entry.path, size=size, is_dir=False, safety="caution"))
                result.total_size += size
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
    return result

def scan_brackets_cache(min_age_days: int = 0) -> ScanResult:
    """Adobe Brackets extract folder, cache, and extension temp."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", "")
    targets = [
        os.path.join(appdata, r"Brackets\ext"),
        os.path.join(appdata, r"Brackets\cache"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_novatrons_cache(min_age_days: int = 0) -> ScanResult:
    """Nova launcher cache and extension data."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", "")
    targets = [
        os.path.join(appdata, r"Novatrons"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_inbox_apps_cache(min_age_days: int = 0) -> ScanResult:
    """Inbox Windows app data (Calculator, Photos, Mail) temp and sync cache. NOT cookies/history."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Local\Microsoft\Windows\IEDebar"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_insider_preview_cache(min_age_days: int = 0) -> ScanResult:
    """Windows Insider flight data, build staging, and reset packages."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"TEMP\WindowsInsider.Upgrade"),
        os.path.join(local, r"Microsoft\Windows\WindowsInsider"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_recovery_env_cache(min_age_days: int = 0) -> ScanResult:
    """Windows Recovery Environment (WinRE) diagnostics and ReAgentc log staging."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"System32\LogFiles\SM"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_maps_offline_cache(min_age_days: int = 0) -> ScanResult:
    """Windows Maps offline map tiles and navigation history cache."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Packages\Microsoft.BingMaps*\LocalState"),
    ]
    for t in targets:
        for found in glob.glob(t):
            item = _make_item(found, safety="safe", min_age_days=min_age_days)
            if item and item.size > 0:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_delivery_optimization_do(min_age_days: int = 0) -> ScanResult:
    """Delivery Optimization download session state and blob container cache."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    progdata = os.environ.get("PROGRAMDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\DeliveryOptimization\Cache"),
        os.path.join(local, r"Microsoft\Windows\DeliveryOptimization\Logs"),
        os.path.join(progdata, r"Microsoft\Windows\DeliveryOptimization\Cache"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_reliability_logs(min_age_days: int = 0) -> ScanResult:
    """Windows Reliability Monitor data and problem step recorder logs."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\Reliability"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_bitlocker_logs(min_age_days: int = 0) -> ScanResult:
    """BitLocker management logs and FVE reconfiguration history."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"System32\LogFiles\BitLocker"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_terminal_cache(min_age_days: int = 0) -> ScanResult:
    """Windows Terminal UWP local cache."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Packages\Microsoft.WindowsTerminal*\LocalCache"),
    ]
    import glob as _glob
    for pattern in targets:
        for t in _glob.glob(pattern):
            if not os.path.isdir(t):
                continue
            item = _make_item(t, safety="safe", min_age_days=min_age_days)
            if item and item.size > 0:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_powershell_ise_cache(min_age_days: int = 0) -> ScanResult:
    """PowerShell ISE saved scripts and IntelliSense cache."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", "")
    targets = [os.path.join(appdata, r"Microsoft\WindowsPowerShell\ISE")]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_terminal_settings_cache(min_age_days: int = 0) -> ScanResult:
    """Windows Terminal settings JSON cache and theme cache."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [os.path.join(local, r"Microsoft\WindowsTerminal")]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_printer_migration_cache(min_age_days: int = 0) -> ScanResult:
    """Printer migration XML backup files from printmanagement snapshots."""
    result = ScanResult()
    windir = os.environ.get("windir", r"C:\Windows")
    targets = [
        os.path.join(windir, r"System32\spool\PRINTERS"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="caution", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_ndis_cache(min_age_days: int = 0) -> ScanResult:
    """Network adapter configuration and protocol binding cache (NDIS intermediate drivers)."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\NetworkConnections"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_triumph_cache(min_age_days: int = 0) -> ScanResult:
    """Triumph! 2 CAD cache and calculation log."""
    result = ScanResult()
    appdata = os.environ.get("APPDATA", "")
    targets = [os.path.join(appdata, r"Triumph")]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_large_files(min_age_days: int = 0, min_size_mb: int = 100) -> ScanResult:
    """Find files larger than min_size_mb across common user directories.

    Targets: Downloads, Documents, Videos, Desktop, and common app data folders.
    Uses os.scandir() for performance — does NOT follow symlinks.
    """
    result = ScanResult()
    min_bytes = min_size_mb * 1024 * 1024
    # Use scandir for performance, walk for depth
    scan_dirs = known_folders.user_data_dirs() + [
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.path.join(os.environ.get("PROGRAMFILES(x86)", r"C:\Program Files (x86)"), "Steam", "steamapps", "common"),
    ]
    _scan_large_recursive(result, scan_dirs, min_bytes, min_age_days, depth=5)
    return result

def _scan_large_recursive(result: ScanResult, dirs: list, min_bytes: int, min_age_days: int, depth: int = 5):
    """Internal recursive scanner for large files."""
    if depth <= 0:
        return
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in (".git", "node_modules", "__pycache__", ".venv", "venv"):
                                continue  # skip known huge dirs that aren't our target
                            _scan_large_recursive(result, [entry.path], min_bytes, min_age_days, depth - 1)
                        elif entry.is_file(follow_symlinks=False):
                            try:
                                size = entry.stat().st_size
                                if size < min_bytes:
                                    continue
                                mtime = entry.stat().st_mtime
                                if min_age_days > 0 and (time.time() - mtime) < min_age_days * 86400:
                                    continue
                                result.items.append(ScanItem(
                                    path=entry.path,
                                    size=size,
                                    is_dir=False,
                                    selected=False,  # user selects which to delete
                                    safety="caution",
                                ))
                                result.total_size += size
                            except OSError:
                                logger.debug("Ignored OSError", exc_info=True)
                    except OSError:
                        logger.debug("Ignored OSError", exc_info=True)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)

def scan_duplicate_files(min_age_days: int = 0, min_size_kb: int = 100, max_depth_dirs: int = 3) -> ScanResult:
    """Find duplicate files by grouping by size then hashing.

    Phase 1: Group files by size (fast)
    Phase 2: Hash files with matching sizes (accurate)
    Only scans user directories to avoid system files.
    """
    result = ScanResult()
    scan_dirs = known_folders.user_data_dirs()
    min_bytes = min_size_kb * 1024

    # Phase 1: Group by size
    size_groups: dict[int, list[str]] = {}
    for directory in scan_dirs:
        if not os.path.isdir(directory):
            continue
        _group_by_size_recursive(directory, size_groups, min_bytes, max_depth_dirs)

    # Phase 2: Hash groups with 2+ files
    for size, paths in size_groups.items():
        if len(paths) < 2:
            continue
        hash_groups: dict[str, list[str]] = {}
        for path in paths:
            try:
                file_hash = _hash_file_fast(path)
                if file_hash:
                    hash_groups.setdefault(file_hash, []).append(path)
            except OSError:
                logger.debug("Ignored OSError", exc_info=True)

        for file_hash, dup_paths in hash_groups.items():
            if len(dup_paths) < 2:
                continue
            wasted = size * (len(dup_paths) - 1)
            # Create one item per duplicate set — path is the group name
            result.items.append(ScanItem(
                path=f"[{len(dup_paths)} duplicates] {dup_paths[0]}",
                size=wasted,
                is_dir=True,
                selected=False,
                safety="caution",
            ))
            result.total_size += wasted

    return result

def _group_by_size_recursive(directory: str, size_groups: dict, min_bytes: int, depth: int):
    """Phase 1: collect files and group by size."""
    if depth <= 0:
        return
    try:
        with os.scandir(directory) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        _group_by_size_recursive(entry.path, size_groups, min_bytes, depth - 1)
                    elif entry.is_file(follow_symlinks=False):
                        size = entry.stat().st_size
                        if size >= min_bytes:
                            size_groups.setdefault(size, []).append(entry.path)
                except OSError:
                    logger.debug("Ignored OSError", exc_info=True)
    except OSError:
        logger.debug("Ignored OSError", exc_info=True)

def _hash_file_fast(path: str, chunk_size: int = 8192) -> Optional[str]:
    """Fast hash: only hash first 64KB + last 64KB + file size for speed.

    A OneDrive Files On-Demand placeholder is skipped rather than opened:
    opening one downloads the whole file. Duplicate detection hashes every
    member of a size-collision group, so without this the scan quietly
    pulls gigabytes over the network — and now that the scanners reach the
    real (redirected) Documents and Pictures, that is where they look.
    """
    import hashlib
    if known_folders.is_cloud_placeholder(path):
        return None
    try:
        size = os.path.getsize(path)
        h = hashlib.md5()
        h.update(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(chunk_size))
            if size > chunk_size * 2:
                f.seek(-chunk_size, 2)
                h.update(f.read(chunk_size))
        return h.hexdigest()
    except OSError:
        return None

def scan_empty_folders(min_age_days: int = 0, min_depth: int = 2, max_depth: int = 10) -> ScanResult:
    """Find completely empty directories (no files, no subdirs with content).

    Scans user directories recursively between min_depth and max_depth levels.
    """
    result = ScanResult()
    scan_dirs = known_folders.user_data_dirs() + [
        os.environ.get("LOCALAPPDATA", ""),
    ]
    _find_empty_folders(result, scan_dirs, min_depth, max_depth)
    return result

def _find_empty_folders(result: ScanResult, dirs: list, min_depth: int, max_depth: int, current_depth: int = 0):
    """Recursively find and report empty folders."""
    if current_depth > max_depth:
        return
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        try:
            is_empty = True
            subdirs = []
            with os.scandir(directory) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            subdirs.append(entry.path)
                            is_empty = False
                        elif entry.is_file(follow_symlinks=False):
                            is_empty = False
                    except OSError:
                        logger.debug("Ignored OSError", exc_info=True)
            if is_empty and current_depth >= min_depth:
                # Check if it still exists and is empty (race condition guard)
                if os.path.isdir(directory) and not any(True for _ in os.scandir(directory)):
                    size = get_dir_size(directory)
                    result.items.append(ScanItem(
                        path=directory,
                        size=size,
                        is_dir=True,
                        selected=False,
                        safety="safe",
                    ))
                    result.total_size += size
            for subdir in subdirs:
                _find_empty_folders(result, [subdir], min_depth, max_depth, current_depth + 1)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)

def scan_old_files(min_age_days: int = 0, min_age_months: int = 6) -> ScanResult:
    """Find files not modified in min_age_months across user directories.

    Uses mtime (last modified) — Windows atime is unreliable so mtime is more practical.
    """
    result = ScanResult()
    min_age_seconds = min_age_months * 30 * 86400
    scan_dirs = known_folders.user_data_dirs()
    _scan_old_recursive(result, scan_dirs, min_age_seconds, depth=4)
    return result

def _scan_old_recursive(result: ScanResult, dirs: list, min_age_seconds: float, depth: int = 4):
    """Recursively find files not modified within age threshold (uses mtime, not atime)."""
    if depth <= 0:
        return
    cutoff = time.time() - min_age_seconds
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            _scan_old_recursive(result, [entry.path], min_age_seconds, depth - 1)
                        elif entry.is_file(follow_symlinks=False):
                            try:
                                mtime = entry.stat().st_mtime
                                if mtime < cutoff:
                                    size = entry.stat().st_size
                                    result.items.append(ScanItem(
                                        path=entry.path,
                                        size=size,
                                        is_dir=False,
                                        selected=False,
                                        safety="caution",
                                    ))
                                    result.total_size += size
                            except OSError:
                                logger.debug("Ignored OSError", exc_info=True)
                    except OSError:
                        logger.debug("Ignored OSError", exc_info=True)
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)

def scan_windows_shell_cache(min_age_days: int = 0) -> ScanResult:
    """Windows Shell Experience Host cache."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Packages\Microsoft.Windows.ShellExperienceHost*\LocalCache"),
    ]
    import glob as _glob
    for pattern in targets:
        for t in _glob.glob(pattern):
            if not os.path.isdir(t):
                continue
            item = _make_item(t, safety="safe", min_age_days=min_age_days)
            if item and item.size > 0:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_dbg_logs(min_age_days: int = 0) -> ScanResult:
    """Debug-logged application data from various dev tools."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"dbg\Logs"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_uwp_all_apps_cache(min_age_days: int = 0) -> ScanResult:
    """All UWP apps LocalCache folders (aggregate scanner)."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    packages = os.path.join(local, r"Packages")
    if not os.path.isdir(packages):
        return result
    import glob as _glob
    for cache_dir in _glob.glob(os.path.join(packages, "*", "LocalCache")):
        item = _make_item(cache_dir, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_installer_rollback(min_age_days: int = 0) -> ScanResult:
    """Windows Installer rollback/backout files (caution — may be needed)."""
    result = ScanResult()
    targets = [
        r"C:\Windows\Installer\Patch",
        r"C:\Windows\Installer\001",
    ]
    import glob as _glob
    for pattern in targets:
        for t in _glob.glob(pattern):
            if not os.path.isdir(t):
                continue
            item = _make_item(t, safety="caution", min_age_days=min_age_days)
            if item and item.size > 0:
                result.items.append(item)
                result.total_size += item.size
    return result

def scan_windows_app_extensions_cache(min_age_days: int = 0) -> ScanResult:
    """Windows App Extensions cache database."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\AppExtensionDatabase"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_connected_accounts_cache(min_age_days: int = 0) -> ScanResult:
    """Connected accounts (email, Azure AD) cached tokens and identity data."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\IdentityOLTCache"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

def scan_windows_compatibility_cache(min_age_days: int = 0) -> ScanResult:
    """Windows compatibility fixer's database cache."""
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA", "")
    targets = [
        os.path.join(local, r"Microsoft\Windows\CompatibilityExperience"),
    ]
    for t in targets:
        if not os.path.isdir(t):
            continue
        item = _make_item(t, safety="safe", min_age_days=min_age_days)
        if item and item.size > 0:
            result.items.append(item)
            result.total_size += item.size
    return result

#: How long to wait for a service to actually reach STOPPED. A busy wuauserv
#: mid-scan takes a few seconds; past this it is not going to stop, and
#: waiting longer just makes the clean look hung.
_SERVICE_STOP_WAIT_SECS = 20

#: Win32 ERROR_SERVICE_ALREADY_RUNNING. Not a failure — the desired end state.
ERROR_SERVICE_ALREADY_RUNNING = 1056


def delete_items(items: List[ScanItem],
                 on_progress: Optional[Callable[[int, int], None]] = None,
                 stop_wuauserv: bool = False) -> Tuple[int, int]:
    """Delete selected items. Returns (deleted_count, error_count).
    If stop_wuauserv=True, wraps deletions in _ServiceStopped("wuauserv")."""
    # Every cached measurement is now about a disk that no longer looks
    # like that. Serving one afterwards reports space as still reclaimable
    # when it has already been freed.
    from modules.cleanup.cleanup_scanner import scan_cache
    scan_cache.invalidate()

    class _ServiceStopped:
        def __init__(self, name):
            self.name = name
            self._stopped = False

        def __enter__(self):
            try:
                import win32service
                import win32serviceutil
                win32serviceutil.StopService(self.name)
                self._stopped = True
                # StopService only ASKS: it fires
                # ControlService(SERVICE_CONTROL_STOP) and returns the status
                # it saw, which is normally STOP_PENDING. Deleting the
                # download cache while the service is still letting go of
                # those files is the exact race this guard exists to prevent,
                # so wait for it to actually be STOPPED.
                try:
                    win32serviceutil.WaitForServiceStatus(
                        self.name, win32service.SERVICE_STOPPED, _SERVICE_STOP_WAIT_SECS)
                except Exception as e:
                    # Say so and clean anyway -- most of what is queued has
                    # nothing to do with this service.
                    logger.warning(
                        "Service %s did not reach STOPPED within %ss (%s) — "
                        "cleaning anyway; files it still holds open will be skipped",
                        self.name, _SERVICE_STOP_WAIT_SECS, e)
            except Exception as e:
                logger.warning("Failed to stop service %s: %s", self.name, e)

        def __exit__(self, exc_type, exc_val, exc_tb):
            if self._stopped:
                try:
                    import win32serviceutil
                    win32serviceutil.StartService(self.name)
                except Exception as e:
                    # wuauserv is trigger-started, so anything that touches
                    # Windows Update brings it back on its own and
                    # StartService answers 1056. That is the state we wanted;
                    # it is not a failure worth a warning.
                    if getattr(e, "winerror", None) == ERROR_SERVICE_ALREADY_RUNNING:
                        logger.debug("Service %s was already running again", self.name)
                    else:
                        logger.warning("Failed to start service %s: %s", self.name, e)
            return False  # do not suppress exceptions

    def _do_delete():
        deleted = 0
        errors = 0
        selected = [i for i in items if i.selected]
        total = len(selected)
        for idx, item in enumerate(selected):
            if on_progress:
                on_progress(idx + 1, total)
            try:
                if not os.path.exists(item.path):
                    continue  # already gone — not an error
                if item.is_dir:
                    shutil.rmtree(item.path, ignore_errors=True)
                else:
                    os.remove(item.path)
                deleted += 1
            except OSError:
                errors += 1
        return deleted, errors

    if stop_wuauserv:
        with _ServiceStopped("wuauserv"):
            return _do_delete()
    else:
        return _do_delete()


def _enum_subkeys(handle):
    """Yield a registry key's subkey names.

    winreg has no "how many subkeys" call that is worth using here: the
    idiom is to index upward until it raises. That makes an exception the
    loop's normal terminator, which is fine — but it belongs in one place
    with one explanation, not repeated as a bare `break` that reads like a
    swallowed error.
    """
    import winreg

    index = 0
    while True:
        try:
            yield winreg.EnumKey(handle, index)
        except OSError:
            logger.debug("End of subkey enumeration at index %d", index)
            return
        index += 1


def _installer_cache_dir() -> str:
    r"""Where Windows keeps the packages it needs to repair and uninstall."""
    return os.path.join(os.environ.get("windir", r"C:\Windows"),
                        "Installer")


def _referenced_installer_packages():
    r"""Every cached .msi/.msp an installed product still points at.

    Returns None when the reference list could not be read, and that
    distinction is the whole safety property of this scanner: an empty set
    means "nothing references anything" (so everything is an orphan), while
    None means "we could not look". Reporting the second as the first
    offers the entire installer cache for deletion, which is every
    installed product's repair and uninstall data.

    The list lives under Installer\UserData\<SID>\Products\<code>, in
    InstallProperties:LocalPackage for products and Patches:LocalPackage
    for patches. Reading it needs no elevation on a normal machine, but a
    locked-down one can refuse, hence the guard.
    """
    import winreg

    root = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
            r"\Installer\UserData")
    referenced = set()
    read_anything = False

    def _local_package(key, subpath):
        try:
            with winreg.OpenKey(key, subpath) as handle:
                value, _ = winreg.QueryValueEx(handle, "LocalPackage")
        except OSError:
            return None
        return value

    try:
        userdata = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
    except OSError:
        logger.warning("Installer UserData not readable; reporting no orphans",
                       exc_info=True)
        return None

    with userdata:
        for sid in _enum_subkeys(userdata):
            for bucket in ("Products", "Patches"):
                try:
                    container = winreg.OpenKey(userdata, sid + "\\" + bucket)
                except OSError:
                    logger.debug("No %s under %s", bucket, sid, exc_info=True)
                    continue
                with container:
                    read_anything = True
                    for code in _enum_subkeys(container):
                        candidates = ([code + r"\InstallProperties"]
                                      if bucket == "Products" else [code])
                        for candidate in candidates:
                            package = _local_package(container, candidate)
                            if package:
                                referenced.add(os.path.normcase(
                                    os.path.normpath(package)))

    if not read_anything:
        logger.warning("Installer UserData held no readable product list; "
                       "reporting no orphans")
        return None
    return referenced


def scan_orphaned_installer_packages(min_age_days: int = 0) -> ScanResult:
    r"""Cached .msi/.msp files no installed product references any more.

    C:\Windows\Installer was 1.02 GB here and nothing covered it -- only
    its $PatchCache$ subfolder had a scanner. It cannot be swept as a
    directory: these files are what Windows uses to repair and uninstall
    everything on the machine, and deleting a referenced one leaves a
    product that can neither be repaired nor removed.

    So this is a set difference against the registry, and it fails CLOSED:
    if the reference list cannot be read, nothing is offered.
    """
    result = ScanResult()
    referenced = _referenced_installer_packages()
    if referenced is None:
        return result

    cache = _installer_cache_dir()
    if not os.path.isdir(cache):
        return result

    try:
        entries = list(os.scandir(cache))
    except OSError:
        logger.debug("Installer cache not readable", exc_info=True)
        return result

    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            if os.path.splitext(entry.name)[1].lower() not in (".msi", ".msp"):
                continue
            if os.path.normcase(os.path.normpath(entry.path)) in referenced:
                continue
            item = _make_item_with_age(entry.path, safety="caution",
                                       min_age_days=min_age_days)
            if item:
                result.items.append(item)
                result.total_size += item.size
        except OSError:
            logger.debug("Ignored OSError", exc_info=True)
    result.items.sort(key=lambda i: i.size, reverse=True)
    return result


#: Below this, a virtual disk is not worth putting in front of anyone.
VIRTUAL_DISK_MIN_BYTES = 1024 * 1024 * 1024


def _collect_virtual_disks(min_age_days: int = 0):
    """Every large disk image on a fixed drive, as (path, size) pairs."""
    suffixes = (".vhd", ".vhdx", ".vdi", ".vmdk", ".qcow2")
    found = []
    for root in drives.fixed_drive_roots():
        for directory, dirnames, filenames in os.walk(root):
            depth = directory.rstrip(os.sep).count(os.sep)
            if depth - root.rstrip(os.sep).count(os.sep) >= 5:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d.lower() not in (
                "windows", "$recycle.bin", "system volume information",
                "program files", "program files (x86)", "programdata")]
            for name in filenames:
                if not name.lower().endswith(suffixes):
                    continue
                path = os.path.join(directory, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    logger.debug("Could not size %s", path, exc_info=True)
                    continue
                if size >= VIRTUAL_DISK_MIN_BYTES:
                    found.append((path, size))
    found.sort(key=lambda row: row[1], reverse=True)
    return found


def scan_virtual_disk_images(min_age_days: int = 0) -> ScanResult:
    r"""Large disk images whose hypervisor IS installed here.

    Reported and never pre-selected. A virtual disk grows and does not
    shrink back on its own -- a WSL ext4.vhdx that briefly held 40 GB stays
    40 GB -- so this is real reclaimable space, but the way to reclaim it
    is to COMPACT the image. Deleting one destroys a working VM, hence
    danger.
    """
    result = ScanResult()
    for path, size in _collect_virtual_disks(min_age_days):
        if virtual_disks.is_orphaned(path):
            continue
        result.items.append(ScanItem(path=path, size=size, is_dir=False,
                                     selected=False, safety="danger"))
        result.total_size += size
    return result


def scan_orphaned_virtual_disks(min_age_days: int = 0) -> ScanResult:
    r"""Large disk images nothing on this machine can open.

    E:\VMs\Ubuntu\Ubuntu.vdi was 11.05 GB here, dated Feb 2026, with no
    HKLM\SOFTWARE\Oracle, no ~\.VirtualBox, no "~\VirtualBox VMs" and no
    VBoxManage.exe anywhere: VirtualBox had been uninstalled and left the
    disk behind. There is no VM to destroy and no tool present to compact
    it, so "compact, do not delete" -- what scan_virtual_disk_images says
    -- is exactly the wrong advice for it.

    Caution rather than danger, and still never pre-selected: nothing is
    using it, but it is somebody's disk image and only they know whether
    the contents still matter.
    """
    result = ScanResult()
    for path, size in _collect_virtual_disks(min_age_days):
        if not virtual_disks.is_orphaned(path):
            continue
        result.items.append(ScanItem(path=path, size=size, is_dir=False,
                                     selected=False, safety="caution"))
        result.total_size += size
    return result

__all__ = [
    'scan_orphaned_virtual_disks',
    'scan_orphaned_installer_packages',
    'scan_virtual_disk_images',
    '_find_empty_folders',
    '_group_by_size_recursive',
    '_hash_file_fast',
    '_scan_large_recursive',
    '_scan_old_recursive',
    'cleanup_winsxs',
    'delete_items',
    'scan_app_caches',
    'scan_appdata_autodiscover',
    'scan_appx_logs',
    'scan_backup_files',
    'scan_bitlocker_logs',
    'scan_bits_transfers',
    'scan_brackets_cache',
    'scan_crash_dumps_system',
    'scan_dbg_logs',
    'scan_delivery_opt_user',
    'scan_delivery_optimization',
    'scan_delivery_optimization_do',
    'scan_diagnostic_data',
    'scan_dmf_logs',
    'scan_dns_cache',
    'scan_downloads_folder_old',
    'scan_duplicate_files',
    'scan_empty_folders',
    'scan_etl_logs',
    'scan_font_files_temp',
    'scan_game_caches',
    'scan_group_policy_logs',
    'scan_ide_caches',
    'scan_install_temp',
    'scan_iso_vhd_files',
    'scan_large_files',
    'scan_maps_cache',
    'scan_maps_offline_cache',
    'scan_msi_logs',
    'scan_msp_patches',
    'scan_ndis_cache',
    'scan_network_debug_logs',
    'scan_novatrons_cache',
    'scan_old_av_quarantine',
    'scan_old_files',
    'scan_perflogs',
    'scan_powershell_ise_cache',
    'scan_powershell_logs',
    'scan_powershell_modules_cache',
    'scan_prefetch',
    'scan_print_nightmare_logs',
    'scan_print_spooler',
    'scan_printer_driver_cache',
    'scan_recent_files',
    'scan_recycle_bin',
    'scan_search_index',
    'scan_store_app_caches',
    'scan_sysinternals_logs',
    'scan_triumph_cache',
    'scan_usb_shadow_copies',
    'scan_userprofile_temp',
    'scan_uwp_all_apps_cache',
    'scan_virtual_drives',
    'scan_winSxS_temp',
    'scan_windows_app_extensions_cache',
    'scan_windows_compatibility_cache',
    'scan_windows_connected_accounts_cache',
    'scan_windows_defender_logs',
    'scan_windows_inbox_apps_cache',
    'scan_windows_insider_logs',
    'scan_windows_insider_preview_cache',
    'scan_windows_installer_cache',
    'scan_windows_installer_rollback',
    'scan_windows_logs',
    'scan_windows_old',
    'scan_windows_optional_features',
    'scan_windows_printer_migration_cache',
    'scan_windows_recovery_env_cache',
    'scan_windows_reliability_logs',
    'scan_windows_shell_cache',
    'scan_windows_terminal_cache',
    'scan_windows_terminal_settings_cache',
    'scan_wmi_logs',
]