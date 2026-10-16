"""Recommendations for gaming on THIS PC. Qt-free.

`collect()` reads the machine (on a worker -- one PowerShell call, the rest
registry and native reads); `findings()` turns those facts into advice,
each one stating the value it read. A fact that could not be read is
`None` and becomes an "unknown" line, never a guess. Nothing here changes
anything: what Game Mode changes is in `session`, and the rest is advice.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import catalog
from .session import BALANCED, dual_ccd_x3d, freed_estimate, preset, running_apps

logger = logging.getLogger(__name__)

GOOD, INFO, WARN, ACT, UNKNOWN = "good", "info", "warn", "action", "unknown"
ORDER = {ACT: 0, WARN: 1, UNKNOWN: 2, INFO: 3, GOOD: 4}

_VPN = re.compile(r"cyberghost|wireguard|openvpn|nordlynx|proton|tap-windows|wintun|mullvad|expressvpn",
                  re.IGNORECASE)
_TORRENT = ("deluge.exe", "qbittorrent.exe", "utorrent.exe", "bittorrent.exe")


@dataclass
class Finding:
    level: str
    title: str
    detail: str
    fix: str = ""


@dataclass
class Display:
    name: str
    width: int
    height: int
    hz: Optional[float]
    max_hz: Optional[float]


@dataclass
class Facts:
    cpu: str = ""
    vcache: Optional[bool] = None          # running; None = absent or unreadable
    vcache_installed: bool = False
    plan_guid: Optional[str] = None
    plan_name: str = ""
    gamebar_version: Optional[str] = None  # "" = not installed, None = unreadable
    auto_game_mode: Optional[int] = None
    game_dvr: Optional[int] = None
    app_capture: Optional[int] = None
    hags: Optional[int] = None
    hvci_running: Optional[bool] = None
    mouse_speed: Optional[str] = None
    displays: List[Display] = field(default_factory=list)
    restart_needed: Optional[bool] = None
    ram_free_gb: Optional[float] = None
    ram_total_gb: Optional[float] = None
    handles: int = 0
    processes: int = 0
    #: What the Maximum preset would leave, and what nothing can touch.
    max_after: Optional[int] = None
    kernel_handles: int = 0
    windows_service_handles: int = 0
    antivirus_handles: int = 0
    heavy_apps: List[tuple] = field(default_factory=list)   # (label, exe, instances, handles)
    torrents: List[str] = field(default_factory=list)
    vpn_up: List[str] = field(default_factory=list)
    bitdefender: bool = False


# ---- reading -------------------------------------------------------------------------------------
_PS = r"""
$o = @{}
try { $p = Get-AppxPackage Microsoft.XboxGamingOverlay -EA Stop | Select-Object -First 1
      $o.gamebar = if ($p) { [string]$p.Version } else { '' } } catch { $o.gamebar = $null }
try { $g = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -EA Stop
      $o.hvci = [bool](@($g.SecurityServicesRunning) -contains 2) } catch { $o.hvci = $null }
$o | ConvertTo-Json -Compress
"""


def _ps_facts() -> Dict[str, object]:
    from core.run import run_ps
    res = run_ps(_PS, timeout=60)
    try:
        return json.loads(res.stdout.strip().splitlines()[-1]) if res.stdout.strip() else {}
    except (ValueError, IndexError) as error:
        logger.warning("game mode: facts script unreadable (%s): %r", error, res.stdout[:200])
        return {}


def _hklm_dword(path: str, name: str) -> Optional[int]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return int(winreg.QueryValueEx(key, name)[0])
    except FileNotFoundError:
        return 0
    except OSError as error:
        logger.warning("game mode: HKLM\\%s\\%s unreadable: %s", path, name, error)
        return None


def _displays() -> List[Display]:
    from modules.monitor_control import display_modes as dm
    out = []
    for name, desc, active, _primary in dm.attached_devices():
        cur = dm.current_mode(name) if active else None
        if cur is None:
            continue
        rates = [r for r in dm.refresh_rates_for(name, cur.width, cur.height) if r]
        out.append(Display(desc or name, cur.width, cur.height, cur.refresh_hz,
                           max(rates) if rates else None))
    return out


def _floor(f: Facts, procs) -> None:
    """Where the handles nobody may close live."""
    import fnmatch
    for p in procs:
        name = p.name.lower()
        if p.pid == 4:
            f.kernel_handles += p.handles
        elif any(fnmatch.fnmatch(name, pat) for pat in catalog.ANTIVIRUS_PATTERNS):
            f.antivirus_handles += p.handles
        elif p.session == 0:
            f.windows_service_handles += p.handles


def _reg_value(system, path: str, name: str):
    exists, value, _ = system.reg_get(path, name)
    if exists is None:
        return None
    return value if exists else 0


def collect(system, own_pid: int, session: int) -> Facts:
    import psutil
    from core import pending_reboot
    f = Facts(cpu=system.cpu_name())
    vc = system.service(catalog.VCACHE_SERVICE)
    f.vcache_installed, f.vcache = vc is not None, (vc.running if vc else None)
    plans = system.plans()
    f.plan_guid = system.active_plan()
    f.plan_name = plans.get(f.plan_guid or "", "")
    ps = _ps_facts()
    f.gamebar_version, f.hvci_running = ps.get("gamebar"), ps.get("hvci")
    f.auto_game_mode = _reg_value(system, r"Software\Microsoft\GameBar", "AutoGameModeEnabled")
    f.game_dvr = _reg_value(system, r"System\GameConfigStore", "GameDVR_Enabled")
    f.app_capture = _reg_value(system, r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                               "AppCaptureEnabled")
    f.hags = _hklm_dword(r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode")
    speed = _reg_value(system, r"Control Panel\Mouse", "MouseSpeed")
    f.mouse_speed = None if speed is None else str(speed)
    f.displays = _displays()
    restart = pending_reboot.check()
    f.restart_needed = None if restart.unreadable and not restart.needed else restart.needed
    mem = psutil.virtual_memory()
    f.ram_free_gb, f.ram_total_gb = mem.available / 2 ** 30, mem.total / 2 ** 30
    procs = system.processes()
    f.handles, f.processes = sum(p.handles for p in procs), len(procs)
    groups = running_apps(procs, session, own_pid, lambda pid: system.details(pid)[0],
                          system.fullscreen_pids())
    f.heavy_apps = [(g.label, g.exe, len(g.roots), g.handles) for g in groups[:12]]
    f.max_after = f.handles - freed_estimate(procs, groups, preset(catalog.MAX, groups).close)
    _floor(f, procs)
    names = {p.name.lower() for p in procs}
    f.torrents = [n for n in _TORRENT if n in names]
    f.bitdefender = "bdservicehost.exe" in names
    f.vpn_up = [n for n, st in psutil.net_if_stats().items() if st.isup and _VPN.search(n)]
    return f


# ---- judging -------------------------------------------------------------------------------------
def findings(f: Facts) -> List[Finding]:
    out: List[Finding] = []
    for check in (_cpu_and_plan, _gamebar, _displays_check, _capture, _hags, _hvci, _mouse,
                  _restart, _memory, _apps, _network, _antivirus):
        out.extend(check(f))
    return sorted(out, key=lambda x: ORDER.get(x.level, 9))


def _cpu_and_plan(f: Facts) -> List[Finding]:
    out = []
    if f.vcache_installed and f.vcache is None:
        out.append(Finding(UNKNOWN, "AMD 3D V-Cache optimizer: state could not be read",
                           "The optimizer is installed, but its running state is unreadable."))
    if not dual_ccd_x3d(f.cpu):
        return out + [Finding(INFO, f"Power plan: {f.plan_name or 'unreadable'}",
                        f"{f.cpu or 'CPU unreadable'} -- Game Mode switches to the fastest plan "
                        "this PC has while you play.")]
    if not f.vcache_installed:
        out.append(Finding(ACT, "AMD 3D V-Cache optimizer is not installed",
                           f"{f.cpu} has the extra cache on ONE of its two CCDs. Without AMD's "
                           "optimizer driver Windows does not know which CCD games belong on.",
                           "Install the AMD chipset driver for your motherboard (it includes the "
                           "3D V-Cache Performance Optimizer)."))
    elif f.vcache is False:
        out.append(Finding(WARN, "AMD 3D V-Cache optimizer service is stopped",
                           "amd3dvcacheSvc is installed but not running.",
                           "Game Mode starts it while you play."))
    elif f.vcache is True:
        out.append(Finding(GOOD, "AMD 3D V-Cache optimizer is running",
                           f"{f.cpu}: amd3dvcacheSvc is running."))
    if f.plan_guid != BALANCED:
        out.append(Finding(ACT, f"Power plan is {f.plan_name or 'unreadable'}, not Balanced",
                           f"On {f.cpu} AMD recommends Balanced: it is the plan that lets Windows "
                           "park the CCD without the cache while a game runs. High/Ultimate "
                           "Performance keep both CCDs awake, so a game can land on the slower one.",
                           "Game Mode switches to Balanced while you play and back afterwards. "
                           "Consider making Balanced your everyday plan too."))
    else:
        out.append(Finding(GOOD, "Power plan is Balanced", "What AMD recommends for this CPU."))
    return out


def _gamebar(f: Facts) -> List[Finding]:
    out = []
    if f.gamebar_version is None:
        out.append(Finding(UNKNOWN, "Xbox Game Bar: could not check", "Get-AppxPackage did not answer."))
    elif f.gamebar_version == "":
        out.append(Finding(ACT, "Xbox Game Bar is not installed",
                           "Windows uses Game Bar to recognise a game"
                           + (" -- and AMD's V-Cache optimizer waits for that signal." if dual_ccd_x3d(f.cpu)
                              else "."), "Install Xbox Game Bar from the Microsoft Store."))
    else:
        out.append(Finding(GOOD, f"Xbox Game Bar {f.gamebar_version} is installed",
                           "Windows uses it to recognise games."
                           + (" If a game still runs on the wrong CCD, open Game Bar (Win+G) in "
                              "the game > Settings > 'Remember this is a game'." if dual_ccd_x3d(f.cpu)
                              else "")))
    if f.auto_game_mode == 0:
        out.append(Finding(WARN, "Windows Game Mode is off", "AutoGameModeEnabled = 0.",
                           "Game Mode turns it on while you play."))
    elif f.auto_game_mode:
        out.append(Finding(GOOD, "Windows Game Mode is on", "AutoGameModeEnabled = 1."))
    return out


def _displays_check(f: Facts) -> List[Finding]:
    out = []
    for d in f.displays:
        if d.hz and d.max_hz and d.max_hz - d.hz >= 1:
            out.append(Finding(ACT, f"{d.name} runs at {d.hz:g} Hz -- it can do {d.max_hz:g} Hz",
                               f"{d.width}x{d.height} at {d.hz:g} Hz. Every game on this screen is "
                               f"capped at {d.hz:g} frames you can see.",
                               "Monitor Control > Refresh rate, or Settings > Display > Advanced "
                               f"display > {d.max_hz:g} Hz."))
        elif d.hz:
            out.append(Finding(GOOD, f"{d.name}: {d.width}x{d.height} at {d.hz:g} Hz",
                               "The highest refresh rate it offers at this resolution."))
    return out


def _capture(f: Facts) -> List[Finding]:
    if f.game_dvr or f.app_capture:
        return [Finding(ACT, "Background game recording is on",
                        f"GameDVR_Enabled = {f.game_dvr}, AppCaptureEnabled = {f.app_capture}: "
                        "Windows keeps recording the last minutes of play, which costs frames.",
                        "Settings > Gaming > Captures > 'Record what happened' off.")]
    if f.game_dvr is None:
        return [Finding(UNKNOWN, "Game recording: could not check", "")]
    return [Finding(GOOD, "Background game recording is off", "GameDVR_Enabled = 0.")]


def _hags(f: Facts) -> List[Finding]:
    if f.hags == 2:
        return [Finding(INFO, "Hardware-accelerated GPU scheduling is requested in Windows "
                        "(HwSchMode = 2); whether the GPU driver uses it depends on the driver", "")]
    if f.hags is None:
        return [Finding(UNKNOWN, "GPU scheduling: could not check", "")]
    return [Finding(INFO, "Hardware-accelerated GPU scheduling: Windows setting",
                    f"HwSchMode = {f.hags}. Windows is not explicitly requesting it with "
                    "HwSchMode = 2; whether the GPU driver uses it depends on the driver.")]


def _hvci(f: Facts) -> List[Finding]:
    if f.hvci_running is None:
        return [Finding(UNKNOWN, "Memory integrity: could not check", "Win32_DeviceGuard did not answer.")]
    if f.hvci_running:
        return [Finding(INFO, "Memory integrity (HVCI) is on",
                        "It protects the kernel and can cost a few percent in some games. "
                        "This is a security trade-off; Game Mode does not touch it.")]
    return [Finding(INFO, "Memory integrity (HVCI) is off",
                    "This kernel protection is not running. Game Mode does not change it.")]


def _mouse(f: Facts) -> List[Finding]:
    if f.mouse_speed == "1":
        return [Finding(INFO, "'Enhance pointer precision' is on",
                        "Mouse acceleration: the same hand movement turns a different amount "
                        "depending on speed. Most shooters read raw input and ignore it; menus "
                        "and older games do not.",
                        "Settings > Bluetooth & devices > Mouse > Additional mouse settings.")]
    return []


def _restart(f: Facts) -> List[Finding]:
    if f.restart_needed:
        return [Finding(WARN, "A restart is pending", "Windows is waiting to finish an update.",
                        "Restart before a long session, not during it.")]
    return []


def _memory(f: Facts) -> List[Finding]:
    if f.ram_free_gb is None:
        return []
    level = GOOD if f.ram_free_gb >= 8 else WARN
    return [Finding(level, f"{f.ram_free_gb:.1f} GB of {f.ram_total_gb:.1f} GB memory free",
                    "Plenty for any game." if level == GOOD else
                    "Close what you are not using before you play -- Game Mode closes the "
                    "background apps ticked below.")]


def _apps(f: Facts) -> List[Finding]:
    out = [Finding(INFO, f"{f.handles:,} handles in {f.processes} processes right now",
                   "Game Mode shows how many it frees when you turn it on.")]
    if f.max_after is not None:
        out.append(Finding(
            INFO, f"Maximum leaves about {f.max_after:,} handles -- 4,000 is not reachable on Windows",
            f"The kernel alone (System) holds {f.kernel_handles:,}, Windows' own services "
            f"{f.windows_service_handles:,}, the antivirus {f.antivirus_handles:,} -- none of which "
            "anything may close -- plus the desktop (Explorer, DWM, input), your game launcher and "
            "the game itself. Handles cost almost nothing on their own; what Game Mode removes is "
            "what WAKES UP during a game: syncs, updaters, indexers, web views.",
            "Use the Maximum preset on 'What Game Mode changes'."))
    for label, exe, instances, handles in f.heavy_apps:
        if instances > 1 and exe in catalog.APP_BY_EXE and not catalog.APP_BY_EXE[exe].lower:
            out.append(Finding(WARN, f"{instances} copies of {label} are running ({handles:,} handles)",
                               "Leftover instances doing nothing.", "Game Mode closes them."))
    if f.torrents:
        out.append(Finding(ACT, "A torrent client is running", ", ".join(f.torrents) +
                           ": upload saturates the line and ping spikes follow.",
                           "Game Mode closes it while you play and reopens it after."))
    return out


def _network(f: Facts) -> List[Finding]:
    if f.vpn_up:
        return [Finding(INFO, "A VPN adapter is up: " + ", ".join(f.vpn_up),
                        "If the VPN is connected, game traffic goes through it -- more latency.",
                        "Disconnect it (or split-tunnel the game) before online play.")]
    return []


def _antivirus(f: Facts) -> List[Finding]:
    if f.bitdefender:
        return [Finding(INFO, "Bitdefender is your antivirus",
                        "Game Mode never touches it. Its own Game profile postpones scans and "
                        "updates while a game is fullscreen.",
                        "Bitdefender > Utilities > Profiles > Game profile: automatic.")]
    return []
