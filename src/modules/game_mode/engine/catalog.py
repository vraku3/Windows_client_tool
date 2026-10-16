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
    #: Kept for the catalog's readers; while Game Mode is on and the app is
    #: open, EVERY service it stopped is stopped again if Windows restarts it
    #: (at most `session.MAX_RESTOPS` times each).
    restop: bool = False
    #: "no_wifi": only stopped when no Wi-Fi adapter is connected.
    when: str = ""

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
    ServiceSpec("InventorySvc", "Inventory and Compatibility Appraisal",
                "Scans installed programs for telemetry and upgrade checks.", REC),
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
    ServiceSpec("FileSyncHelper", "OneDrive file sync helper", "OneDrive's service.", MAX),
    ServiceSpec("Bonjour Service", "Bonjour (mDNS)", "Apple/Blizzard network discovery.", MAX),
    ServiceSpec("appcservice", "AppControl Service",
                "Restarts the AppControl window within a second of it closing (measured).", MAX),
    ServiceSpec("WlanSvc", "WLAN AutoConfig",
                "Background Wi-Fi scans cause latency spikes; stopped only when no Wi-Fi "
                "adapter is connected (this PC plays on Ethernet).", MAX, when="no_wifi"),
    ServiceSpec("DisplayEnhancementService", "Display Enhancement",
                "Adaptive brightness for built-in panels.", MAX),
    ServiceSpec("CloudBackupRestoreSvc_*", "Cloud Backup and Restore", "Windows Backup sync.", MAX),
    ServiceSpec("NPSMSvc_*", "Now Playing Session Manager", "The media flyout.", MAX),
    ServiceSpec("RmSvc", "Radio Management",
                "Airplane-mode switch. Left alone: it did not stop within 25 s here (measured), "
                "which only delays Game Mode.", OPT),
    ServiceSpec("DusmSvc", "Data Usage", "Counts network data per app.", MAX),
    ServiceSpec("ShellHWDetection", "Shell Hardware Detection", "AutoPlay pop-ups.", MAX),
    ServiceSpec("whesvc", "Windows Health and Optimized Experiences", "Background health checks.",
                MAX),
    ServiceSpec("DevicesFlowUserSvc_*", "Devices Flow", "Device pairing pop-ups.", MAX),
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
_SYNC = "Syncs files: disk and network bursts."
_TORRENT = "Torrent upload fills the line: ping spikes."
_CHAT = "Closed by Maximum; messages arrive after Game Mode is off."


def _a(exe, label, why, level, **kw) -> AppSpec:
    return AppSpec(exe, label, why, level, **kw)


APPS = (
    # -- sync and cloud: disk + network bursts (Recommended) -------------------------------------
    _a("onedrive.exe", "OneDrive", _SYNC, REC),
    _a("onedrive.sync.service.exe", "OneDrive sync service", "OneDrive's helper; OneDrive starts it.",
       REC, reopen=False),
    _a("filecoauth.exe", "OneDrive co-authoring", "OneDrive's helper; started when needed.", REC,
       reopen=False),
    _a("dropbox.exe", "Dropbox", _SYNC, REC),
    _a("googledrivefs.exe", "Google Drive", _SYNC, REC),
    _a("icloudservices.exe", "iCloud", _SYNC, REC),
    _a("iclouddrive.exe", "iCloud Drive", _SYNC, REC),
    _a("megasync.exe", "MEGA", _SYNC, REC),
    _a("pcloud.exe", "pCloud", _SYNC, REC),
    _a("box.exe", "Box Drive", _SYNC, REC),
    _a("nextcloud.exe", "Nextcloud", _SYNC, REC),
    _a("owncloud.exe", "ownCloud", _SYNC, REC),
    _a("syncthing.exe", "Syncthing", _SYNC, REC),
    _a("synologydrive.exe", "Synology Drive", _SYNC, REC),
    # -- torrents and download managers ----------------------------------------------------------
    _a("deluge.exe", "Deluge (torrents)", _TORRENT, REC),
    _a("qbittorrent.exe", "qBittorrent", _TORRENT, REC),
    _a("utorrent.exe", "uTorrent", _TORRENT, REC),
    _a("bittorrent.exe", "BitTorrent", _TORRENT, REC),
    _a("transmission-qt.exe", "Transmission", _TORRENT, REC),
    _a("tixati.exe", "Tixati", _TORRENT, REC),
    _a("idman.exe", "Internet Download Manager", "Downloads fill the line.", REC),
    _a("fdm.exe", "Free Download Manager", "Downloads fill the line.", REC),
    # -- updaters and resident helpers -----------------------------------------------------------
    _a("creative cloud.exe", "Adobe Creative Cloud", "Background updater and sync.", REC),
    _a("adobe desktop service.exe", "Adobe Desktop Service", "Creative Cloud's engine.", REC,
       reopen=False),
    _a("ccxprocess.exe", "Adobe CCX Process", "Creative Cloud helper.", REC, reopen=False),
    _a("coresync.exe", "Adobe Core Sync", "Creative Cloud file sync.", REC, reopen=False),
    _a("adobeipcbroker.exe", "Adobe IPC Broker", "Creative Cloud helper.", REC, reopen=False),
    _a("jusched.exe", "Java Update Scheduler", "Checks for Java updates.", REC),
    _a("mathworksservicehost.exe", "MathWorks Service Host",
       "MATLAB's background host (licensing, updates); MATLAB starts it again.", MAX, reopen=False),
    _a("widgetboard.exe", "Widgets", "News feed kept alive in the background.", REC, reopen=False),
    _a("widgets.exe", "Widgets", "News feed kept alive in the background.", REC, reopen=False),
    _a("widgetservice.exe", "Widgets service", "Widgets' background runtime.", REC, reopen=False),
    _a("snippingtool.exe", "Snipping Tool",
       "Stays resident after a screenshot; Print Screen starts it again.", REC, reopen=False),
    _a("m365copilot.exe", "Microsoft 365 Copilot", "Background web app.", REC),
    _a("phoneexperiencehost.exe", "Phone Link", "Keeps a link to your phone open.", REC),
    _a("yourphone.exe", "Phone Link (older)", "Keeps a link to your phone open.", REC),
    _a("wallpaper32.exe", "Wallpaper Engine", "Animated wallpaper: GPU time behind the game.", REC),
    _a("wallpaper64.exe", "Wallpaper Engine", "Animated wallpaper: GPU time behind the game.", REC),
    _a("lively.exe", "Lively Wallpaper", "Animated wallpaper: GPU time behind the game.", REC),
    # -- Windows' own on-demand helpers (Maximum) ------------------------------------------------
    _a("crossdeviceresume.exe", "Cross-device resume",
       "Phone-to-PC handoff; Windows starts it again when needed.", MAX, reopen=False),
    _a("searchhost.exe", "Windows Search box",
       "Left open: Windows starts it again within a second (measured), so closing it only "
       "costs CPU.", OPT, reopen=False),
    # -- chat and voice (Maximum) ----------------------------------------------------------------
    _a("discord.exe", "Discord", _CHAT + " Reopened after.", MAX),
    _a("ms-teams.exe", "Microsoft Teams", "Closed by Maximum; you will miss calls meanwhile.", MAX,
       lower=True),
    _a("teams.exe", "Microsoft Teams (classic)", "Closed by Maximum; you will miss calls meanwhile.",
       MAX, lower=True),
    _a("whatsapp.root.exe", "WhatsApp", _CHAT, MAX, lower=True),
    _a("whatsapp.exe", "WhatsApp", _CHAT, MAX, lower=True),
    _a("telegram.exe", "Telegram", _CHAT, MAX, lower=True),
    _a("signal.exe", "Signal", _CHAT, MAX, lower=True),
    _a("slack.exe", "Slack", _CHAT, MAX, lower=True),
    _a("skype.exe", "Skype", _CHAT, MAX, lower=True),
    _a("zoom.exe", "Zoom", _CHAT, MAX, lower=True),
    _a("viber.exe", "Viber", _CHAT, MAX, lower=True),
    _a("messenger.exe", "Messenger", _CHAT, MAX, lower=True),
    # -- browsers (Maximum, reopened with their tabs) ----------------------------------------------
    _a("chrome.exe", "Google Chrome", "Closed by Maximum; reopened with your tabs.", MAX, lower=True,
       reopen_args=_TABS),
    _a("msedge.exe", "Microsoft Edge", "Closed by Maximum; reopened with your tabs.", MAX, lower=True,
       reopen_args=_TABS),
    _a("brave.exe", "Brave", "Closed by Maximum; reopened with your tabs.", MAX, lower=True,
       reopen_args=_TABS),
    _a("vivaldi.exe", "Vivaldi", "Closed by Maximum; reopened with your tabs.", MAX, lower=True,
       reopen_args=_TABS),
    _a("opera.exe", "Opera", "Closed by Maximum; Opera restores its own tabs.", MAX, lower=True),
    _a("firefox.exe", "Firefox", "Closed by Maximum; Firefox restores tabs if it is set to.", MAX,
       lower=True),
    # -- work apps (Maximum) ---------------------------------------------------------------------
    _a("chatgpt.exe", "ChatGPT / Codex", "Closed by Maximum.", MAX, lower=True),
    _a("code.exe", "Visual Studio Code",
       "Closed by Maximum; it reopens its windows and unsaved files itself.", MAX, lower=True),
    _a("cursor.exe", "Cursor", "Closed by Maximum; it reopens its windows itself.", MAX, lower=True),
    _a("notepad.exe", "Notepad", "Closed by Maximum; it keeps unsaved tabs itself.", MAX),
    _a("notion.exe", "Notion", "Closed by Maximum.", MAX, lower=True),
    _a("obsidian.exe", "Obsidian", "Closed by Maximum.", MAX, lower=True),
    _a("evernote.exe", "Evernote", "Closed by Maximum.", MAX, lower=True),
    _a("postman.exe", "Postman", "Closed by Maximum.", MAX, lower=True),
    _a("docker desktop.exe", "Docker Desktop", "Closed by Maximum (its containers keep running).",
       MAX),
    _a("appcontrol.exe", "AppControl", "Process monitor window; its service keeps running.", MAX),
    _a("powertoys.exe", "PowerToys", "Closed by Maximum; its shortcuts return after.", MAX),
    _a("rainmeter.exe", "Rainmeter", "Desktop widgets redrawing behind the game.", MAX),
    _a("everything.exe", "Everything (search)", "Keeps a live file index.", MAX),
    _a("anydesk.exe", "AnyDesk", "Remote access client.", MAX),
    _a("teamviewer.exe", "TeamViewer", "Remote access client.", MAX),
    _a("itunes.exe", "iTunes", "Closed by Maximum.", MAX),
    # -- left open unless you tick them ----------------------------------------------------------
    _a("dashboard.exe", "CyberGhost", "Left open: closing it may drop the VPN.", OPT),
    _a("spotify.exe", "Spotify", "Left open: music while you play.", OPT),
    _a("sharex.exe", "ShareX", "Left open: screenshot and clip hotkeys while you play.", OPT),
    _a("overwolf.exe", "Overwolf", "Left open: in-game add-ons.", OPT),
    _a("vlc.exe", "VLC", "Left open.", OPT),
)

APP_BY_EXE: Dict[str, AppSpec] = {a.exe: a for a in APPS}

#: Windows' own programs (under %SystemRoot%) that MAY be closed, because
#: Windows starts them again when they are next needed.
WINDOWS_ON_DEMAND = frozenset({"searchhost.exe", "crossdeviceresume.exe"})

#: Game launchers. Anything they started -- the game itself -- is never
#: closed, however it is named (see `session.running_apps`).
LAUNCHERS = (
    "steam.exe", "battle.net.exe", "agent.exe", "epicgameslauncher.exe", "eadesktop.exe",
    "ealauncher.exe", "origin.exe", "ubisoftconnect.exe", "upc.exe", "galaxyclient*.exe",
    "riotclient*.exe", "xboxpcapp.exe", "gamelaunchhelper.exe", "playnite*.exe", "heroic.exe",
    "rockstarlauncher.exe", "launcher.exe", "bsglauncher.exe", "wargaminggamecenter.exe",
)

#: Folders games live in: nothing running from one is ever closed.
GAME_FOLDERS = (
    "\\steamapps\\", "\\epic games\\", "\\xboxgames\\", "\\gog galaxy\\games\\", "\\riot games\\",
    "\\ea games\\", "\\ubisoft game launcher\\games\\", "\\battle.net\\", "\\blizzard",
    "\\rockstar games\\", "\\games\\", "\\windowsapps\\microsoft.gaming",
)

#: Shared by process protection and the advice handle counts.
ANTIVIRUS_PATTERNS = (
    "bd*.exe", "vsserv*.exe", "seccenter.exe", "avp*.exe", "avast*.exe", "avg*.exe", "mbam*.exe",
    "ekrn.exe", "egui.exe", "mcshield.exe", "sophos*.exe", "defender*.exe", "msmpeng.exe",
    "nissrv.exe", "mpdefender*.exe", "securityhealth*.exe", "smartscreen.exe", "mpcmdrun.exe",
)

#: Never offered for closing, whatever is ticked: game launchers (the game
#: needs them), the shell, input, security, Outlook/Office (their .pst/.ost
#: data files), peripherals (mouse DPI, macros, RGB), controllers and game
#: input, monitoring and fan tools, streaming hosts, terminals and AI agents
#: that may be mid-task, and this app itself.
NEVER_CLOSE = LAUNCHERS + ANTIVIRUS_PATTERNS + (
    "steamwebhelper.exe", "steamservice.exe", "gameoverlayui*.exe", "epicwebhelper.exe",
    "eabackgroundservice.exe", "vgtray.exe", "gamingservices*.exe", "gamebar*.exe", "gameinput*.exe",
    "explorer.exe", "dwm.exe", "textinputhost.exe", "ctfmon.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "shellhost.exe", "sihost.exe", "msedgewebview2.exe",
    "outlook.exe", "olk.exe", "winword.exe", "excel.exe", "powerpnt.exe", "onenote*.exe",
    "msaccess.exe", "mspub.exe", "lync.exe", "officeclicktorun.exe",
    "audiodg.exe", "rtkauduservice64.exe", "amdrsserv.exe", "radeonsoftware.exe", "atieclxx.exe",
    "amdow.exe", "nvcontainer.exe", "nvidia*.exe", "nvsphelper64.exe",
    "lghub*.exe", "logi*.exe", "razer*.exe", "steelseries*.exe", "icue*.exe", "corsair*.exe",
    "armourycrate*.exe", "wooting*.exe", "signalrgb*.exe", "openrgb*.exe", "ds4windows.exe",
    "dsx*.exe", "x360ce*.exe", "joytokey*.exe", "8bitdo*.exe",
    "rtss*.exe", "msiafterburner.exe", "hwinfo*.exe", "fancontrol.exe", "obs64.exe", "obs32.exe",
    "parsec*.exe", "sunshine*.exe", "moonlight*.exe",
    "claude.exe", "node.exe", "codex.exe", "python.exe", "pythonw.exe", "windowsterminal.exe",
    "openconsole.exe", "conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "wsl*.exe",
    "bash.exe", "sh.exe", "mintty.exe", "git.exe", "ssh.exe",
    "winclienttool*.exe",
)


def is_launcher(exe: str) -> bool:
    low = exe.lower()
    return any(fnmatch.fnmatch(low, pattern) for pattern in LAUNCHERS)


def in_game_folder(path: str) -> bool:
    low = (path or "").lower()
    return any(folder in low for folder in GAME_FOLDERS)


def never_close(exe: str) -> bool:
    low = exe.lower()
    return any(fnmatch.fnmatch(low, pattern) for pattern in NEVER_CLOSE)


def spec_for(exe: str) -> Optional[AppSpec]:
    return APP_BY_EXE.get(exe.lower())
