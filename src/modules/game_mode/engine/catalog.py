"""What Game Mode may touch, and why. Qt-free, hand-written.

Three lists, each entry with a LEVEL: REC (ticked by the Recommended preset
and by Maximum), MAX (ticked by Maximum only) or OPT (never ticked by a
preset -- you tick it yourself).

- SERVICES are only ever STOPPED. Their start type is never changed, so
  Windows' own idea of what should run is unchanged and a reboot alone
  brings everything back. A name may be a pattern (`OneSyncSvc_*` matches
  the per-user instance, `GoogleUpdaterService*` the versioned updater).
- TASKS are scheduled tasks DISABLED for the session and enabled again by
  Game Mode OFF -- updaters and maintenance that would otherwise start a
  download or a disk scan mid-game.
- APPS are closed and reopened, or left open at a lower priority.

Nothing here is security software: the antivirus, firewall and Defender are
on no list, and are in NEVER_CLOSE. Neither is Outlook or Office -- their
data files (.pst/.ost) are never put at risk by a forced close.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Dict, Optional

REC, MAX, OPT = "recommended", "maximum", "optional"


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    label: str
    why: str
    level: str
    #: Windows starts these again on its own (a trigger, a schedule); while
    #: Game Mode is on and the app is open they are stopped again.
    restop: bool = False

    @property
    def default(self) -> bool:
        return self.level == REC


_UPDATER = "Starts on a schedule to download updates."
SERVICES = (
    ServiceSpec("WSearch", "Windows Search indexer",
                "Re-reads files as they change -- disk bursts while a game streams assets. "
                "Search still works; new files are indexed after Game Mode is off.", REC, True),
    ServiceSpec("wuauserv", "Windows Update", "Downloads and installs updates in the background.",
                REC, True),
    ServiceSpec("UsoSvc", "Update Orchestrator",
                "Schedules update scans and installs, and is what starts Windows Update.", REC, True),
    ServiceSpec("BITS", "Background Intelligent Transfer",
                "Background downloads for Windows Update, the Store and app updaters.", REC, True),
    ServiceSpec("DoSvc", "Delivery Optimization",
                "Downloads update pieces and can UPLOAD them to other PCs -- bandwidth "
                "and latency in an online game.", REC, True),
    ServiceSpec("InstallService", "Microsoft Store Install Service",
                "Store app downloads and updates.", REC, True),
    ServiceSpec("edgeupdate*", "Microsoft Edge Update", _UPDATER, REC, True),
    ServiceSpec("GoogleUpdater*", "Google Updater", _UPDATER, REC, True),
    ServiceSpec("gupdate*", "Google Update (legacy)", _UPDATER, REC, True),
    ServiceSpec("brave", "Brave Update", _UPDATER, REC, True),
    ServiceSpec("bravem", "Brave Update (manual)", _UPDATER, REC, True),
    ServiceSpec("MozillaMaintenance", "Mozilla Maintenance", _UPDATER, REC, True),
    ServiceSpec("AdobeARMservice", "Adobe Acrobat Update", _UPDATER, REC, True),
    ServiceSpec("OneDrive Updater Service", "OneDrive Updater", _UPDATER, REC, True),
    ServiceSpec("uhssvc", "Microsoft Update Health", "Update health checks.", REC),
    ServiceSpec("DiagTrack", "Connected User Experiences and Telemetry",
                "Collects and uploads diagnostic data.", REC),
    ServiceSpec("dmwappushservice", "Device management WAP push",
                "Routes telemetry and device-management messages.", REC),
    ServiceSpec("MapsBroker", "Downloaded Maps Manager", "Updates offline maps in the background.", REC),
    ServiceSpec("TrkWks", "Distributed Link Tracking Client",
                "Keeps shortcuts pointing at files moved across NTFS volumes.", REC),
    ServiceSpec("stisvc", "Windows Image Acquisition", "Scanners and cameras.", REC),
    ServiceSpec("Spooler", "Print Spooler", "Printing.", REC),
    ServiceSpec("Fax", "Fax", "Fax.", REC),
    ServiceSpec("WSAIFabricSvc", "Windows AI Fabric", "Background AI features (Recall, Click to Do).",
                MAX),
    ServiceSpec("DPS", "Diagnostic Policy Service", "Problem detection and troubleshooters.", MAX),
    ServiceSpec("WdiServiceHost", "Diagnostic Service Host", "Diagnostics.", MAX),
    ServiceSpec("WdiSystemHost", "Diagnostic System Host", "Diagnostics.", MAX),
    ServiceSpec("PcaSvc", "Program Compatibility Assistant", "Watches programs as they start.", MAX),
    ServiceSpec("PimIndexMaintenanceSvc_*", "Contact Data indexing", "Indexes contacts for Mail/People.",
                MAX),
    ServiceSpec("OneSyncSvc_*", "Sync Host", "Syncs mail, contacts and calendar.", MAX),
    ServiceSpec("MessagingService_*", "Messaging Service", "Text messaging sync.", MAX),
    ServiceSpec("CDPUserSvc_*", "Connected Devices Platform (user)", "Phone Link and nearby devices.", MAX),
    ServiceSpec("NcdAutoSetup", "Network Connected Devices Auto-Setup",
                "Looks for printers and TVs on the network.", MAX),
    ServiceSpec("lfsvc", "Geolocation", "Location for apps.", MAX),
    ServiceSpec("SensorDataService", "Sensor Data", "Sensors (none on a desktop).", MAX),
    ServiceSpec("SensorService", "Sensor Service", "Sensors (none on a desktop).", MAX),
    ServiceSpec("SensrSvc", "Sensor Monitoring", "Sensors (none on a desktop).", MAX),
    ServiceSpec("SysMain", "SysMain (Superfetch)",
                "Preloads programs into RAM. Can help load times, so it is left alone "
                "unless you tick it.", OPT),
    ServiceSpec("WerSvc", "Windows Error Reporting",
                "Left running so a game crash still leaves a report.", OPT),
    ServiceSpec("SSDPSRV", "SSDP Discovery",
                "Finds UPnP devices; some games and consoles on the network rely on UPnP.", OPT),
    ServiceSpec("WpnService", "Windows Push Notifications",
                "Notification pop-ups, including chat apps.", OPT),
    ServiceSpec("WpnUserService_*", "Windows Push Notifications (user)",
                "Notification pop-ups, including chat apps.", OPT),
    ServiceSpec("CDPSvc", "Connected Devices Platform", "Phone Link, nearby sharing.", OPT),
)

SERVICE_BY_NAME: Dict[str, ServiceSpec] = {s.name.lower(): s for s in SERVICES}


def service_spec(name: str) -> Optional[ServiceSpec]:
    """The spec an INSTALLED service name falls under (patterns included)."""
    low = name.lower()
    if low in SERVICE_BY_NAME:
        return SERVICE_BY_NAME[low]
    return next((s for s in SERVICES if "*" in s.name and fnmatch.fnmatch(low, s.name.lower())), None)


#: The AMD 3D V-Cache Performance Optimizer: on a dual-CCD X3D processor it is
#: what steers a game onto the CCD with the extra cache. Game Mode STARTS it
#: if it is stopped -- the one service it ever starts.
VCACHE_SERVICE = "amd3dvcacheSvc"


@dataclass(frozen=True)
class TaskSpec:
    pattern: str        # fnmatch against the task's full path, case-insensitive
    label: str
    level: str


TASKS = (
    TaskSpec(r"\GoogleSystem\GoogleUpdater\*", "Google Updater", REC),
    TaskSpec(r"\GoogleUpdateTask*", "Google Update", REC),
    TaskSpec(r"\MicrosoftEdgeUpdateTask*", "Edge Update", REC),
    TaskSpec(r"\BraveSoftwareUpdateTask*", "Brave Update", REC),
    TaskSpec(r"\OneDrive*Update Task*", "OneDrive Update", REC),
    TaskSpec(r"\OneDrive Reporting Task*", "OneDrive Reporting", REC),
    TaskSpec(r"\Adobe Acrobat Update Task", "Adobe Acrobat Update", REC),
    TaskSpec(r"\Mozilla\*", "Firefox maintenance", REC),
    TaskSpec(r"\Opera*autoupdate*", "Opera update", REC),
    TaskSpec(r"\Microsoft\Office\Office Automatic Updates 2.0", "Office Automatic Updates", REC),
    TaskSpec(r"\Microsoft\Office\Office Feature Updates", "Office Feature Updates", REC),
    TaskSpec(r"\Microsoft\Office\Office Feature Updates Logon", "Office Feature Updates (logon)", REC),
    TaskSpec(r"\Microsoft\Office\Office Background Push Maintenance", "Office push maintenance", REC),
    TaskSpec(r"\Microsoft\Windows\Defrag\ScheduledDefrag", "Drive optimization (defrag/TRIM)", REC),
    TaskSpec(r"\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser*",
             "Compatibility Appraiser (telemetry, heavy disk)", REC),
    TaskSpec(r"\Microsoft\Windows\Application Experience\MareBackup", "Compatibility backup", REC),
    TaskSpec(r"\Microsoft\Windows\Application Experience\StartupAppTask", "Startup app scan", REC),
    TaskSpec(r"\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
             "Disk diagnostic data collector", REC),
    TaskSpec(r"\Microsoft\Windows\Power Efficiency Diagnostics\AnalyzeSystem", "Power efficiency scan",
             REC),
    TaskSpec(r"\Microsoft\Windows\Windows Error Reporting\QueueReporting", "Error report upload", REC),
    TaskSpec(r"\Microsoft\Windows\Feedback\Siuf\*", "Feedback surveys", REC),
    TaskSpec(r"\Microsoft\Windows\Customer Experience Improvement Program\*", "CEIP telemetry", REC),
    TaskSpec(r"\Microsoft\Windows\Maintenance\WinSAT", "WinSAT benchmark", REC),
)


def task_spec(path: str) -> Optional[TaskSpec]:
    low = path.lower()
    return next((t for t in TASKS if fnmatch.fnmatch(low, t.pattern.lower())), None)


@dataclass(frozen=True)
class AppSpec:
    exe: str
    label: str
    why: str
    #: REC: closed by both presets; MAX: closed by Maximum; OPT: no preset closes it.
    level: str
    #: Reopen it when Game Mode is turned off. False for apps Windows starts
    #: again on demand (Widgets, Snipping Tool, Search) -- reopening them would
    #: add a process nobody asked for.
    reopen: bool = True
    #: Left open, it runs at Below Normal priority while Game Mode is on.
    lower: bool = False
    #: Added when it is reopened (Chromium browsers: bring the tabs back).
    reopen_args: str = ""

    @property
    def close(self) -> bool:
        return self.level == REC


_TABS = "--restore-last-session"
APPS = (
    AppSpec("onedrive.exe", "OneDrive", "Syncs files: disk and network bursts.", REC),
    AppSpec("deluge.exe", "Deluge (torrents)", "Torrent upload fills the line: ping spikes.", REC),
    AppSpec("qbittorrent.exe", "qBittorrent", "Torrent upload fills the line: ping spikes.", REC),
    AppSpec("utorrent.exe", "uTorrent", "Torrent upload fills the line: ping spikes.", REC),
    AppSpec("bittorrent.exe", "BitTorrent", "Torrent upload fills the line: ping spikes.", REC),
    AppSpec("dropbox.exe", "Dropbox", "Syncs files: disk and network bursts.", REC),
    AppSpec("googledrivefs.exe", "Google Drive", "Syncs files: disk and network bursts.", REC),
    AppSpec("creative cloud.exe", "Adobe Creative Cloud", "Background updater and sync.", REC),
    AppSpec("widgetboard.exe", "Widgets", "News feed kept alive in the background.", REC, reopen=False),
    AppSpec("widgets.exe", "Widgets", "News feed kept alive in the background.", REC, reopen=False),
    AppSpec("snippingtool.exe", "Snipping Tool",
            "Stays resident after a screenshot; Print Screen starts it again.", REC, reopen=False),
    AppSpec("m365copilot.exe", "Microsoft 365 Copilot", "Background web app.", REC),
    AppSpec("phoneexperiencehost.exe", "Phone Link", "Keeps a link to your phone open.", REC),
    AppSpec("crossdeviceresume.exe", "Cross-device resume",
            "Phone-to-PC handoff; Windows starts it again when needed.", MAX, reopen=False),
    AppSpec("searchhost.exe", "Windows Search box",
            "The search flyout; Windows starts it again the next time you search.", MAX, reopen=False),
    AppSpec("ms-teams.exe", "Microsoft Teams", "Closed by Maximum; you will miss calls meanwhile.",
            MAX, lower=True),
    AppSpec("whatsapp.root.exe", "WhatsApp", "Closed by Maximum; messages arrive after.", MAX, lower=True),
    AppSpec("whatsapp.exe", "WhatsApp", "Closed by Maximum; messages arrive after.", MAX, lower=True),
    AppSpec("chatgpt.exe", "ChatGPT / Codex", "Closed by Maximum.", MAX, lower=True),
    AppSpec("chrome.exe", "Google Chrome", "Closed by Maximum; reopened with your tabs.", MAX,
            lower=True, reopen_args=_TABS),
    AppSpec("msedge.exe", "Microsoft Edge", "Closed by Maximum; reopened with your tabs.", MAX,
            lower=True, reopen_args=_TABS),
    AppSpec("brave.exe", "Brave", "Closed by Maximum; reopened with your tabs.", MAX, lower=True,
            reopen_args=_TABS),
    AppSpec("firefox.exe", "Firefox", "Closed by Maximum; Firefox restores tabs if it is set to.",
            MAX, lower=True),
    AppSpec("code.exe", "Visual Studio Code",
            "Closed by Maximum; it reopens its windows and unsaved files itself.", MAX, lower=True),
    AppSpec("notepad.exe", "Notepad", "Closed by Maximum; it keeps unsaved tabs itself.", MAX),
    AppSpec("dashboard.exe", "CyberGhost", "Left open: closing it may drop the VPN.", OPT),
    AppSpec("spotify.exe", "Spotify", "Left open: music while you play.", OPT),
    AppSpec("discord.exe", "Discord", "Left open: voice chat. Never lowered (audio would crackle).", OPT),
)

APP_BY_EXE: Dict[str, AppSpec] = {a.exe: a for a in APPS}

#: Windows' own programs (under %SystemRoot%) that MAY be closed, because
#: Windows starts them again when they are next needed.
WINDOWS_ON_DEMAND = frozenset({"searchhost.exe", "crossdeviceresume.exe"})

#: Never offered for closing, whatever is ticked: game launchers (the game
#: needs them), the shell, input, security, Outlook/Office (their .pst/.ost
#: data files), peripherals (mouse DPI, macros, RGB), monitoring and fan
#: tools, terminals and AI agents that may be mid-task, and this app itself.
NEVER_CLOSE = (
    "steam.exe", "steamwebhelper.exe", "steamservice.exe", "battle.net.exe", "agent.exe",
    "epicgameslauncher.exe", "epicwebhelper.exe", "eadesktop.exe", "eabackgroundservice.exe",
    "ubisoftconnect.exe", "upc.exe", "galaxyclient*.exe", "riotclient*.exe", "vgtray.exe",
    "xboxpcapp.exe", "gamingservices*.exe", "gamebar*.exe",
    "explorer.exe", "dwm.exe", "textinputhost.exe", "ctfmon.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "shellhost.exe", "sihost.exe", "msedgewebview2.exe",
    "bd*.exe", "seccenter.exe", "vsserv*.exe", "msmpeng.exe", "securityhealth*.exe", "nissrv.exe",
    "outlook.exe", "olk.exe", "winword.exe", "excel.exe", "powerpnt.exe", "onenote*.exe",
    "msaccess.exe", "mspub.exe", "lync.exe", "officeclicktorun.exe",
    "audiodg.exe", "rtkauduservice64.exe", "amdrsserv.exe", "radeonsoftware.exe", "atieclxx.exe",
    "amdow.exe", "nvcontainer.exe", "nvidia*.exe", "nvsphelper64.exe",
    "lghub*.exe", "logi*.exe", "razer*.exe", "steelseries*.exe", "icue*.exe", "corsair*.exe",
    "armourycrate*.exe", "wooting*.exe", "signalrgb*.exe", "openrgb*.exe",
    "rtss*.exe", "msiafterburner.exe", "hwinfo*.exe", "fancontrol.exe", "obs64.exe", "obs32.exe",
    "claude.exe", "node.exe", "codex.exe", "python.exe", "pythonw.exe", "windowsterminal.exe",
    "openconsole.exe", "conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "wsl*.exe",
    "winclienttool*.exe",
)


def never_close(exe: str) -> bool:
    low = exe.lower()
    return any(fnmatch.fnmatch(low, pattern) for pattern in NEVER_CLOSE)


def spec_for(exe: str) -> Optional[AppSpec]:
    return APP_BY_EXE.get(exe.lower())
