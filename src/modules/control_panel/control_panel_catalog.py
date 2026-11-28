"""Qt-free catalog and launcher for classic Windows Control Panel applets and tools.

Targets standalone Windows 10/11 client PCs (no domain/AD/MDM applets).
Every launch uses an explicit argv list with ``shell=False`` so nothing is
ever interpolated through ``cmd.exe``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple


CATEGORIES: Tuple[str, ...] = (
    "System & Hardware",
    "Network & Internet",
    "Programs & Features",
    "User Accounts & Security",
    "Clock, Region & Appearance",
    "Administrative Tools",
)


@dataclass(frozen=True)
class ControlPanelItem:
    id: str
    name: str
    category: str
    icon: str
    description: str
    command_label: str
    launch_args: Tuple[str, ...]
    keywords: Tuple[str, ...] = ()
    requires_admin: bool = False


ALL_APPLETS: Tuple[ControlPanelItem, ...] = (
    # ── System & Hardware ───────────────────────────────────────────────────
    ControlPanelItem(
        id="sysdm",
        name="System Properties",
        category="System & Hardware",
        icon="💻",
        description="Computer name, hardware, performance, environment variables, and startup/recovery.",
        command_label="sysdm.cpl",
        launch_args=("control.exe", "sysdm.cpl"),
        keywords=("system", "computer name", "performance", "env", "startup", "recovery", "hardware"),
    ),
    ControlPanelItem(
        id="devmgmt",
        name="Device Manager",
        category="System & Hardware",
        icon="🔌",
        description="Inspect hardware devices, update or roll back drivers, and spot yellow-bang errors.",
        command_label="devmgmt.msc",
        launch_args=("mmc.exe", "devmgmt.msc"),
        keywords=("devices", "drivers", "usb", "gpu", "hardware", "pci", "audio"),
    ),
    ControlPanelItem(
        id="powercfg",
        name="Power Options",
        category="System & Hardware",
        icon="🔋",
        description="Classic power plans, sleep/hibernate timers, lid/button actions, and advanced power settings.",
        command_label="powercfg.cpl",
        launch_args=("control.exe", "powercfg.cpl"),
        keywords=("power", "plan", "high performance", "balanced", "sleep", "hibernate", "fast startup", "usb selective suspend"),
    ),
    ControlPanelItem(
        id="mmsys",
        name="Sound (Classic)",
        category="System & Hardware",
        icon="🔊",
        description="Playback & recording devices, default audio, sample rate, enhancements, and communications.",
        command_label="mmsys.cpl",
        launch_args=("control.exe", "mmsys.cpl"),
        keywords=("audio", "microphone", "speakers", "headphones", "volume", "spatial", "recording", "playback"),
    ),
    ControlPanelItem(
        id="printers",
        name="Devices and Printers",
        category="System & Hardware",
        icon="🖨️",
        description="Classic view of connected printers, scanners, Bluetooth peripherals, and USB devices.",
        command_label="control printers",
        launch_args=("explorer.exe", "shell:::{A8A91A66-3A7D-4424-8D24-04E180695C7A}"),
        keywords=("printer", "scanner", "bluetooth", "usb", "peripherals", "hardware"),
    ),
    ControlPanelItem(
        id="mouse",
        name="Mouse Properties",
        category="System & Hardware",
        icon="🖱️",
        description="Pointer speed, enhance pointer precision, wheel scroll lines, double-click speed, and cursors.",
        command_label="main.cpl",
        launch_args=("control.exe", "main.cpl"),
        keywords=("mouse", "pointer", "cursor", "acceleration", "precision", "wheel", "gaming"),
    ),
    ControlPanelItem(
        id="keyboard",
        name="Keyboard Properties",
        category="System & Hardware",
        icon="⌨️",
        description="Character repeat delay, repeat rate, and cursor blink rate.",
        command_label="control keyboard",
        launch_args=("control.exe", "keyboard"),
        keywords=("keyboard", "repeat", "delay", "rate", "blink", "typing"),
    ),
    ControlPanelItem(
        id="joy",
        name="Game Controllers",
        category="System & Hardware",
        icon="🎮",
        description="Test, calibrate, and set preferred gamepads, joysticks, and racing wheels.",
        command_label="joy.cpl",
        launch_args=("control.exe", "joy.cpl"),
        keywords=("gamepad", "controller", "joystick", "xbox", "wheel", "calibrate", "gaming"),
    ),
    ControlPanelItem(
        id="colorcpl",
        name="Color Management",
        category="System & Hardware",
        icon="🎨",
        description="Manage ICC color profiles for monitors, HDR displays, and printers.",
        command_label="colorcpl.exe",
        launch_args=("colorcpl.exe",),
        keywords=("icc", "color", "profile", "monitor", "display", "srgb", "hdr"),
    ),
    ControlPanelItem(
        id="autoplay",
        name="AutoPlay",
        category="System & Hardware",
        icon="💿",
        description="Choose default actions for USB drives, memory cards, and media.",
        command_label="Microsoft.AutoPlay",
        launch_args=("control.exe", "/name", "Microsoft.AutoPlay"),
        keywords=("autoplay", "usb", "removable", "media", "sd card", "dvd"),
    ),
    ControlPanelItem(
        id="msinfo32",
        name="System Information",
        category="System & Hardware",
        icon="ℹ️",
        description="Detailed hardware resources, BIOS/UEFI version, Secure Boot, and software environment.",
        command_label="msinfo32.exe",
        launch_args=("msinfo32.exe",),
        keywords=("bios", "uefi", "motherboard", "secure boot", "irq", "dma", "specs"),
    ),

    # ── Network & Internet ──────────────────────────────────────────────────
    ControlPanelItem(
        id="ncpa",
        name="Network Connections",
        category="Network & Internet",
        icon="🌐",
        description="Classic adapter list for Ethernet, Wi-Fi, VPN, DNS, IPv4/IPv6, and adapter properties.",
        command_label="ncpa.cpl",
        launch_args=("control.exe", "ncpa.cpl"),
        keywords=("network", "adapter", "ethernet", "wifi", "dns", "ip", "ipv4", "ipv6", "vpn", "nic"),
    ),
    ControlPanelItem(
        id="netsharing",
        name="Network and Sharing Center",
        category="Network & Internet",
        icon="🔗",
        description="Active network profiles, advanced sharing settings, and connection status.",
        command_label="Microsoft.NetworkAndSharingCenter",
        launch_args=("control.exe", "/name", "Microsoft.NetworkAndSharingCenter"),
        keywords=("sharing", "lan", "workgroup", "private", "public", "network"),
    ),
    ControlPanelItem(
        id="inetcpl",
        name="Internet Options",
        category="Network & Internet",
        icon="🌍",
        description="System proxy/LAN settings, TLS protocol versions, security zones, and certificates.",
        command_label="inetcpl.cpl",
        launch_args=("control.exe", "inetcpl.cpl"),
        keywords=("proxy", "lan", "tls", "ssl", "certificates", "internet", "zones"),
    ),
    ControlPanelItem(
        id="firewall",
        name="Windows Defender Firewall",
        category="Network & Internet",
        icon="🧱",
        description="Classic firewall status, allowed apps, and quick enable/disable per network profile.",
        command_label="firewall.cpl",
        launch_args=("control.exe", "firewall.cpl"),
        keywords=("firewall", "defender", "ports", "allowed apps", "inbound", "outbound"),
    ),
    ControlPanelItem(
        id="wf_msc",
        name="Firewall with Advanced Security",
        category="Network & Internet",
        icon="🛡️",
        description="Full inbound/outbound rule editor, port filtering, and connection security rules.",
        command_label="wf.msc",
        launch_args=("mmc.exe", "wf.msc"),
        keywords=("firewall", "rules", "ports", "tcp", "udp", "advanced"),
        requires_admin=True,
    ),

    # ── Programs & Features ─────────────────────────────────────────────────
    ControlPanelItem(
        id="appwiz",
        name="Programs and Features",
        category="Programs & Features",
        icon="📦",
        description="Classic uninstall, change, and repair list for desktop Win32/MSI applications.",
        command_label="appwiz.cpl",
        launch_args=("control.exe", "appwiz.cpl"),
        keywords=("uninstall", "programs", "apps", "msi", "repair", "add remove"),
    ),
    ControlPanelItem(
        id="optionalfeatures",
        name="Turn Windows Features On or Off",
        category="Programs & Features",
        icon="🧩",
        description="Enable or disable Hyper-V, WSL, Windows Sandbox, .NET Framework, and SMB components.",
        command_label="optionalfeatures.exe",
        launch_args=("optionalfeatures.exe",),
        keywords=("features", "hyper-v", "wsl", "sandbox", "dotnet", "smb", "iis"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="defaultprograms",
        name="Default Programs",
        category="Programs & Features",
        icon="📎",
        description="Manage classic file-type associations and default applications.",
        command_label="Microsoft.DefaultPrograms",
        launch_args=("control.exe", "/name", "Microsoft.DefaultPrograms"),
        keywords=("defaults", "file associations", "extensions", "browser", "open with"),
    ),

    # ── User Accounts & Security ────────────────────────────────────────────
    ControlPanelItem(
        id="userpasswords",
        name="User Accounts (Classic)",
        category="User Accounts & Security",
        icon="👤",
        description="Change account type, manage passwords, and configure UAC notification level.",
        command_label="Microsoft.UserAccounts",
        launch_args=("control.exe", "/name", "Microsoft.UserAccounts"),
        keywords=("users", "accounts", "password", "uac", "administrator"),
    ),
    ControlPanelItem(
        id="netplwiz",
        name="Advanced User Accounts (netplwiz)",
        category="User Accounts & Security",
        icon="🔑",
        description="Classic user list, group membership, and automatic sign-in configuration.",
        command_label="netplwiz.exe",
        launch_args=("netplwiz.exe",),
        keywords=("autologin", "auto login", "netplwiz", "users", "groups"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="credman",
        name="Credential Manager",
        category="User Accounts & Security",
        icon="🔐",
        description="View, edit, or remove saved Windows, network share, and Web credentials.",
        command_label="Microsoft.CredentialManager",
        launch_args=("control.exe", "/name", "Microsoft.CredentialManager"),
        keywords=("credentials", "passwords", "vault", "smb", "saved logins"),
    ),
    ControlPanelItem(
        id="wscui",
        name="Security and Maintenance",
        category="User Accounts & Security",
        icon="✅",
        description="Classic security status, UAC settings, automatic maintenance, and reliability monitor link.",
        command_label="wscui.cpl",
        launch_args=("control.exe", "wscui.cpl"),
        keywords=("security", "maintenance", "uac", "action center", "reliability"),
    ),
    ControlPanelItem(
        id="bitlocker",
        name="BitLocker Drive Encryption",
        category="User Accounts & Security",
        icon="🔒",
        description="Manage drive encryption, recovery keys, and BitLocker protection status.",
        command_label="Microsoft.BitLockerDriveEncryption",
        launch_args=("control.exe", "/name", "Microsoft.BitLockerDriveEncryption"),
        keywords=("bitlocker", "encryption", "recovery key", "tpm", "drive"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="certmgr",
        name="Certificates (Current User)",
        category="User Accounts & Security",
        icon="📜",
        description="Manage personal, root, and intermediate certificates for the current user.",
        command_label="certmgr.msc",
        launch_args=("mmc.exe", "certmgr.msc"),
        keywords=("certificates", "ssl", "tls", "root ca", "personal"),
    ),
    ControlPanelItem(
        id="certlm",
        name="Certificates (Local Computer)",
        category="User Accounts & Security",
        icon="🏛️",
        description="Manage machine-wide trusted root and intermediate certificates.",
        command_label="certlm.msc",
        launch_args=("mmc.exe", "certlm.msc"),
        keywords=("certificates", "machine", "local computer", "trusted root"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="lusrmgr",
        name="Local Users and Groups",
        category="User Accounts & Security",
        icon="👥",
        description="Direct local user accounts and Administrators/Users group editor (Pro/Enterprise).",
        command_label="lusrmgr.msc",
        launch_args=("mmc.exe", "lusrmgr.msc"),
        keywords=("local users", "groups", "administrators", "accounts"),
        requires_admin=True,
    ),

    # ── Clock, Region & Appearance ──────────────────────────────────────────
    ControlPanelItem(
        id="timedate",
        name="Date and Time",
        category="Clock, Region & Appearance",
        icon="🕒",
        description="Time zone, additional tray clocks, and manual NTP internet time synchronization.",
        command_label="timedate.cpl",
        launch_args=("control.exe", "timedate.cpl"),
        keywords=("clock", "time", "date", "timezone", "ntp", "sync"),
    ),
    ControlPanelItem(
        id="intl",
        name="Region",
        category="Clock, Region & Appearance",
        icon="🗺️",
        description="Date/time/number formats, decimal separators, and non-Unicode/UTF-8 system locale.",
        command_label="intl.cpl",
        launch_args=("control.exe", "intl.cpl"),
        keywords=("region", "locale", "format", "date format", "unicode", "utf8", "language"),
    ),
    ControlPanelItem(
        id="folders",
        name="File Explorer Options",
        category="Clock, Region & Appearance",
        icon="📁",
        description="Show hidden files, file name extensions, launch folder views, and privacy history.",
        command_label="control folders",
        launch_args=("control.exe", "folders"),
        keywords=("explorer", "hidden files", "extensions", "folder options", "view"),
    ),
    ControlPanelItem(
        id="fonts",
        name="Fonts (Classic Folder)",
        category="Clock, Region & Appearance",
        icon="🔤",
        description="Classic installed fonts folder for quick drag-and-drop installation or inspection.",
        command_label="control fonts",
        launch_args=("control.exe", "fonts"),
        keywords=("fonts", "typography", "ttf", "otf"),
    ),
    ControlPanelItem(
        id="cttune",
        name="ClearType Text Tuner",
        category="Clock, Region & Appearance",
        icon="🔎",
        description="Fine-tune subpixel font smoothing for sharper text on LCD/OLED displays.",
        command_label="cttune.exe",
        launch_args=("cttune.exe",),
        keywords=("cleartype", "font", "smoothing", "text", "sharpness", "monitor"),
    ),
    ControlPanelItem(
        id="dccw",
        name="Display Color Calibration",
        category="Clock, Region & Appearance",
        icon="🖥️",
        description="Walk through gamma, brightness, contrast, and color balance calibration.",
        command_label="dccw.exe",
        launch_args=("dccw.exe",),
        keywords=("calibration", "gamma", "brightness", "contrast", "display", "monitor"),
    ),

    # ── Administrative Tools ────────────────────────────────────────────────
    ControlPanelItem(
        id="diskmgmt",
        name="Disk Management",
        category="Administrative Tools",
        icon="💾",
        description="Initialize disks, resize partitions, assign drive letters, and manage volumes.",
        command_label="diskmgmt.msc",
        launch_args=("mmc.exe", "diskmgmt.msc"),
        keywords=("disk", "partition", "drive letter", "volume", "nvme", "ssd", "format"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="compmgmt",
        name="Computer Management",
        category="Administrative Tools",
        icon="🧰",
        description="All-in-one classic console: Task Scheduler, Event Viewer, Disks, Devices, and Services.",
        command_label="compmgmt.msc",
        launch_args=("mmc.exe", "compmgmt.msc"),
        keywords=("computer management", "console", "mmc", "admin"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="services",
        name="Services",
        category="Administrative Tools",
        icon="⚙️",
        description="Start, stop, and configure startup types and recovery actions for Windows services.",
        command_label="services.msc",
        launch_args=("mmc.exe", "services.msc"),
        keywords=("services", "daemon", "startup type", "restart"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="taskschd",
        name="Task Scheduler",
        category="Administrative Tools",
        icon="📅",
        description="Inspect, trigger, disable, or author Windows scheduled tasks.",
        command_label="taskschd.msc",
        launch_args=("mmc.exe", "taskschd.msc"),
        keywords=("tasks", "scheduler", "cron", "triggers", "automation"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="eventvwr",
        name="Event Viewer",
        category="Administrative Tools",
        icon="📋",
        description="Classic Windows Event Logs for System, Application, Security, and Operational channels.",
        command_label="eventvwr.msc",
        launch_args=("mmc.exe", "eventvwr.msc"),
        keywords=("events", "logs", "crash", "errors", "warnings"),
    ),
    ControlPanelItem(
        id="resmon",
        name="Resource Monitor",
        category="Administrative Tools",
        icon="📊",
        description="Live per-process CPU, memory, disk I/O, file handles, and network socket monitor.",
        command_label="resmon.exe",
        launch_args=("resmon.exe",),
        keywords=("cpu", "ram", "disk", "network", "handles", "performance", "monitor"),
    ),
    ControlPanelItem(
        id="perfmon",
        name="Performance Monitor",
        category="Administrative Tools",
        icon="📈",
        description="Classic performance counters, data collector sets, and system diagnostics reports.",
        command_label="perfmon.msc",
        launch_args=("mmc.exe", "perfmon.msc"),
        keywords=("perfmon", "counters", "diagnostics", "benchmark"),
    ),
    ControlPanelItem(
        id="sysrestore",
        name="System Protection (Restore)",
        category="Administrative Tools",
        icon="⏪",
        description="Configure restore point disk space, create manual restore points, or roll back.",
        command_label="SystemPropertiesProtection.exe",
        launch_args=("SystemPropertiesProtection.exe",),
        keywords=("restore", "restore point", "vss", "shadow copy", "backup"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="sysperf",
        name="Performance Options (Pagefile & Visuals)",
        category="Administrative Tools",
        icon="⚡",
        description="Visual effects, processor scheduling, virtual memory / pagefile size, and DEP.",
        command_label="SystemPropertiesPerformance.exe",
        launch_args=("SystemPropertiesPerformance.exe",),
        keywords=("pagefile", "virtual memory", "visual effects", "animations", "dep", "performance"),
    ),
    ControlPanelItem(
        id="storage_spaces",
        name="Storage Spaces",
        category="Administrative Tools",
        icon="🗄️",
        description="Create and manage classic Windows Storage Pools and mirrored/parity spaces.",
        command_label="Microsoft.StorageSpaces",
        launch_args=("control.exe", "/name", "Microsoft.StorageSpaces"),
        keywords=("storage", "raid", "pool", "mirror", "parity", "disks"),
        requires_admin=True,
    ),
    ControlPanelItem(
        id="indexing",
        name="Indexing Options",
        category="Administrative Tools",
        icon="🔍",
        description="Choose indexed folders, file types, or rebuild the Windows Search index.",
        command_label="control srchadmin.dll",
        launch_args=("control.exe", "srchadmin.dll"),
        keywords=("search", "index", "rebuild", "windows search"),
    ),
    ControlPanelItem(
        id="dxdiag",
        name="DirectX Diagnostic Tool",
        category="Administrative Tools",
        icon="🎯",
        description="Inspect GPU WDDM driver model, DirectX feature levels, audio drivers, and input devices.",
        command_label="dxdiag.exe",
        launch_args=("dxdiag.exe",),
        keywords=("directx", "gpu", "wddm", "graphics", "sound", "gaming"),
    ),
    ControlPanelItem(
        id="odbc",
        name="ODBC Data Sources (64-bit)",
        category="Administrative Tools",
        icon="🛢️",
        description="Manage 64-bit ODBC database drivers and User/System DSNs.",
        command_label="odbcad32.exe",
        launch_args=("odbcad32.exe",),
        keywords=("odbc", "dsn", "database", "sql", "drivers"),
    ),
)


def category_counts(applets: Iterable[ControlPanelItem] = ALL_APPLETS) -> Dict[str, int]:
    items = list(applets)
    counts: Dict[str, int] = {"All": len(items)}
    for cat in CATEGORIES:
        counts[cat] = 0
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1
    return counts


def filter_applets(
    category: str = "All",
    query: str = "",
    applets: Iterable[ControlPanelItem] = ALL_APPLETS,
) -> List[ControlPanelItem]:
    q = (query or "").strip().lower()
    out: List[ControlPanelItem] = []
    for item in applets:
        if category and category != "All" and item.category != category:
            continue
        if q:
            haystack = " ".join((
                item.name,
                item.category,
                item.description,
                item.command_label,
                " ".join(item.keywords),
            )).lower()
            if q not in haystack:
                continue
        out.append(item)
    return out


def _resolve_executable(exe: str) -> str:
    """Resolve Windows System32/Windows binaries explicitly when possible."""
    if os.path.isabs(exe):
        return exe
    sys_root = os.environ.get("SystemRoot") or r"C:\Windows"
    for base in (os.path.join(sys_root, "System32"), sys_root):
        candidate = os.path.join(base, exe)
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which(exe)
    return found or exe


def _launch_argv(argv: Tuple[str, ...], label: str) -> Tuple[bool, str]:
    if not argv:
        return False, "No command configured."
    resolved = [_resolve_executable(argv[0]), *argv[1:]]
    try:
        subprocess.Popen(resolved, close_fds=True)
        return True, f"Opened {label}."
    except OSError as exc:
        return False, f"Could not open {label}: {exc}"


def launch_applet(item: ControlPanelItem) -> Tuple[bool, str]:
    """Launch a classic Control Panel applet without invoking a shell."""
    return _launch_argv(item.launch_args, f"{item.name} ({item.command_label})")


def open_classic_control_panel() -> Tuple[bool, str]:
    """Open the classic Windows Control Panel home window."""
    return _launch_argv(("control.exe",), "Classic Control Panel")


def open_god_mode() -> Tuple[bool, str]:
    """Open the Windows All Tasks ('God Mode') shell folder."""
    return _launch_argv(
        ("explorer.exe", "shell:::{ED7BA470-8E54-465E-825C-99712043E01C}"),
        "God Mode (All Control Panel Tasks)",
    )
