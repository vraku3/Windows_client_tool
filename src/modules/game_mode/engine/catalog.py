"""What Game Mode may touch, and why. Qt-free, hand-written.

Two lists. SERVICES are only ever STOPPED (never reconfigured: the start type
is not touched, so Windows' own idea of what should run is unchanged and a
reboot alone brings everything back). APPS are closed and reopened, or left
open at a lower priority. Nothing here is security software: the antivirus,
firewall and Defender are never on either list, whatever they cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    label: str
    why: str
    #: Ticked the first time someone opens the tab.
    default: bool
    #: Windows starts these again on its own (a trigger, a schedule); while
    #: Game Mode is on and the app is open they are stopped again.
    restop: bool = False


SERVICES = (
    ServiceSpec("WSearch", "Windows Search indexer",
                "Re-reads files as they change -- disk bursts while a game streams assets. "
                "Search still works; new files are indexed after Game Mode is off.", True, True),
    ServiceSpec("wuauserv", "Windows Update",
                "Downloads and installs updates in the background.", True, True),
    ServiceSpec("UsoSvc", "Update Orchestrator",
                "Schedules update scans and installs, and is what starts Windows Update.", True, True),
    ServiceSpec("BITS", "Background Intelligent Transfer",
                "Background downloads for Windows Update, the Store and app updaters.", True, True),
    ServiceSpec("DoSvc", "Delivery Optimization",
                "Downloads update pieces and can UPLOAD them to other PCs -- bandwidth "
                "and latency in an online game.", True, True),
    ServiceSpec("DiagTrack", "Connected User Experiences and Telemetry",
                "Collects and uploads diagnostic data.", True),
    ServiceSpec("dmwappushservice", "Device management WAP push",
                "Routes telemetry and device-management messages.", True),
    ServiceSpec("MapsBroker", "Downloaded Maps Manager",
                "Updates offline maps in the background.", True),
    ServiceSpec("TrkWks", "Distributed Link Tracking Client",
                "Keeps shortcuts pointing at files moved across NTFS volumes.", True),
    ServiceSpec("stisvc", "Windows Image Acquisition",
                "Scanners and cameras. Not needed while playing.", True),
    ServiceSpec("Spooler", "Print Spooler", "Printing. Not needed while playing.", True),
    ServiceSpec("Fax", "Fax", "Fax. Not needed while playing.", True),
    ServiceSpec("SysMain", "SysMain (Superfetch)",
                "Preloads programs into RAM. Can help load times, so it is left alone "
                "unless you tick it.", False),
    ServiceSpec("WerSvc", "Windows Error Reporting",
                "Collects crash reports. Left running so a game crash still leaves a report.",
                False),
    ServiceSpec("PcaSvc", "Program Compatibility Assistant",
                "Watches programs as they start. Harmless; tick it to stop it anyway.", False),
    ServiceSpec("SSDPSRV", "SSDP Discovery",
                "Finds UPnP devices on the network. Left alone: some games and consoles "
                "on the network rely on UPnP.", False),
    ServiceSpec("WpnService", "Windows Push Notifications",
                "Notification pop-ups. Stopping it silences them, including chat apps.", False),
)

SERVICE_BY_NAME: Dict[str, ServiceSpec] = {s.name.lower(): s for s in SERVICES}

#: The AMD 3D V-Cache Performance Optimizer: on a dual-CCD X3D processor it is
#: what steers a game onto the CCD with the extra cache. Game Mode STARTS it
#: if it is stopped -- the one service it ever starts.
VCACHE_SERVICE = "amd3dvcacheSvc"


@dataclass(frozen=True)
class AppSpec:
    exe: str
    label: str
    why: str
    #: Ticked to close the first time someone opens the tab.
    close: bool
    #: Reopen it when Game Mode is turned off. False for apps Windows starts
    #: again on demand (Widgets, Snipping Tool) -- reopening them would add a
    #: process nobody asked for.
    reopen: bool = True
    #: Left open, it runs at Below Normal priority while Game Mode is on.
    lower: bool = False


APPS = (
    AppSpec("onedrive.exe", "OneDrive", "Syncs files: disk and network bursts.", True),
    AppSpec("deluge.exe", "Deluge (torrents)", "Torrent upload fills the line: ping spikes.", True),
    AppSpec("qbittorrent.exe", "qBittorrent", "Torrent upload fills the line: ping spikes.", True),
    AppSpec("utorrent.exe", "uTorrent", "Torrent upload fills the line: ping spikes.", True),
    AppSpec("bittorrent.exe", "BitTorrent", "Torrent upload fills the line: ping spikes.", True),
    AppSpec("dropbox.exe", "Dropbox", "Syncs files: disk and network bursts.", True),
    AppSpec("googledrivefs.exe", "Google Drive", "Syncs files: disk and network bursts.", True),
    AppSpec("creative cloud.exe", "Adobe Creative Cloud", "Background updater and sync.", True),
    AppSpec("widgetboard.exe", "Widgets", "News feed kept alive in the background.", True,
            reopen=False),
    AppSpec("widgets.exe", "Widgets", "News feed kept alive in the background.", True, reopen=False),
    AppSpec("snippingtool.exe", "Snipping Tool",
            "Stays resident after a screenshot; Print Screen starts it again.", True, reopen=False),
    AppSpec("m365copilot.exe", "Microsoft 365 Copilot", "Background web app.", True),
    AppSpec("phoneexperiencehost.exe", "Phone Link", "Keeps a link to your phone open.", True),
    AppSpec("ms-teams.exe", "Microsoft Teams", "Left open by default -- you may get a call.",
            False, lower=True),
    AppSpec("whatsapp.root.exe", "WhatsApp", "Left open by default -- messages.", False, lower=True),
    AppSpec("whatsapp.exe", "WhatsApp", "Left open by default -- messages.", False, lower=True),
    AppSpec("chatgpt.exe", "ChatGPT / Codex", "Left open by default.", False, lower=True),
    AppSpec("chrome.exe", "Google Chrome", "Left open by default -- your tabs.", False, lower=True),
    AppSpec("msedge.exe", "Microsoft Edge", "Left open by default -- your tabs.", False, lower=True),
    AppSpec("firefox.exe", "Firefox", "Left open by default -- your tabs.", False, lower=True),
    AppSpec("brave.exe", "Brave", "Left open by default -- your tabs.", False, lower=True),
    AppSpec("code.exe", "Visual Studio Code", "Left open by default -- unsaved work.", False,
            lower=True),
    AppSpec("dashboard.exe", "CyberGhost", "Left open: closing it may drop the VPN.", False),
    AppSpec("spotify.exe", "Spotify", "Left open: music while you play.", False),
    AppSpec("discord.exe", "Discord", "Left open: voice chat. Never lowered (audio would crackle).",
            False),
)

APP_BY_EXE: Dict[str, AppSpec] = {a.exe: a for a in APPS}

#: Never offered for closing, whatever is ticked: game launchers (the game
#: needs them), the shell, input, security and this app itself.
NEVER_CLOSE = frozenset({
    "steam.exe", "steamwebhelper.exe", "battle.net.exe", "agent.exe", "epicgameslauncher.exe",
    "eadesktop.exe", "ubisoftconnect.exe", "upc.exe", "galaxyclient.exe", "riotclientservices.exe",
    "xboxpcapp.exe", "gamingservices.exe", "explorer.exe", "dwm.exe", "textinputhost.exe",
    "ctfmon.exe", "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "msedgewebview2.exe", "bdservicehost.exe", "bdagent.exe", "msmpeng.exe", "securityhealthsystray.exe",
    "audiodg.exe", "rtkauduservice64.exe", "amdrsserv.exe", "radeonsoftware.exe", "atieclxx.exe",
    "nvcontainer.exe", "nvidia app.exe", "python.exe", "pythonw.exe", "winclienttool-portable.exe",
    "winclienttool.exe", "obs64.exe", "gamebar.exe", "gamebarftserver.exe",
})


def spec_for(exe: str) -> Optional[AppSpec]:
    return APP_BY_EXE.get(exe.lower())
