"""Persistent OS preferences with an exact, journal-before-change undo. Qt-free.

Deliberately excluded:
NetworkThrottlingIndex: undocumented gaming folklore is not a measured improvement.
SystemResponsiveness: reserving scheduler capacity is not a general desktop fix.
Win32PrioritySeparation: Windows owns foreground scheduling policy.
Disabling Prefetch / SysMain / Search / pagefile: removes useful Windows facilities.
LargeSystemCache: a server cache policy is inappropriate for this desktop.
Timer resolution: a persistent timer request wastes power and belongs to applications.
bcdedit useplatformclock/disabledynamictick: boot timer overrides can regress performance.
Core parking: this dual-CCD X3D needs Windows parking and Balanced during Game Mode.
Mitigations: performance preferences must not remove exploit protection.
Defender/HVCI/VBS/firewall: security controls are outside this tab.
GPU/driver/HAGS/TDR changes: driver policy is outside this tab.
Power plan switching: the active plan is observed, never replaced here.

Windows capabilities NEVER offered for removal (`NEVER_OFFER`, enforced by a test):
Hello.Face (the owner signs in with the Lenovo IR camera); Language.Basic / OCR /
Speech / TextToSpeech (the local TTS pipeline and dictation use them);
PowerShell.ISE (the owner's script editor); OneCoreUAP.OneSync (Mail/Calendar/People
sync); NetFX3 (old installers and tools need .NET 3.5); OpenSSH.Client;
Notepad.System; Wallpapers.Extended; DirectX.Configuration.Database (games read
it); every Wifi.Client / Ethernet.Client driver capability (that a NIC is absent
cannot be proven from the capability name); Sense.Client (Defender for Endpoint).
"""
from __future__ import annotations

import ctypes
from core.system_paths import powershell_exe
from core.operation_lock import operation_lock
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from .journal import Journal, default_dir

logger = logging.getLogger(__name__)

DESKTOP = r"Control Panel\Desktop"
MOUSE = r"Control Panel\Mouse"
ADVANCED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
EXPLORER = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
USB_SUBGROUP = "2a737441-1930-4402-8d77-b2bebba308a3"
USB_SETTING = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"
_LOCK = threading.RLock()
#: Kinds whose target can be absent from this PC (service, optional feature,
#: capability): absent -> "Not applicable", never journaled.
PRESENCE_KINDS = ("service", "feature", "capability")
PENDING_STATES = {"enablepending", "disablepending", "installpending", "uninstallpending"}
#: Capability names (prefix, case-insensitive) that are never offered -- see the
#: module docstring for why each one stays.
NEVER_OFFER = ("hello.face", "language.basic", "language.ocr", "language.speech",
               "language.texttospeech", "microsoft.windows.powershell.ise", "onecoreuap.onesync",
               "netfx3", "openssh.client", "microsoft.windows.notepad.system",
               "microsoft.wallpapers.extended", "directx.configuration.database",
               "microsoft.windows.wifi.client", "microsoft.windows.ethernet.client",
               "microsoft.windows.sense.client")
#: Feature / capability / service names are interpolated into PowerShell only
#: after matching this.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._~-]+$")
#: The newest generally released Windows 11 build as of 2026-10 (25H2). A
#: higher build is an Insider flight; a dated snapshot, not a rule.
RELEASED_BUILD_MAX = 26200
CAPABILITY_REVERT_NOTE = (
    "Revert re-adds it with Add-WindowsCapability, which NEEDS Windows Update / internet: "
    "the payload is not kept on this PC. On an Insider build the payload may be unavailable; "
    "a failed re-add stays in the journal with its error and is never marked reverted.")


#: Apply skips these while an active Game Mode session overlaps with them.
GAME_MODE_HOLDS = {
    "sysmain": "Game Mode is holding SysMain stopped -- applied after Game Mode OFF",
    "usb_suspend": "Game Mode switched the power plan -- applied after Game Mode OFF",
}


class OptimiseError(RuntimeError):
    """A refusal which must be shown in the tab, with the journal kept."""


@dataclass(frozen=True)
class Spec:
    id: str
    label: str
    level: str
    kind: str
    value: object
    why: str
    key: str = ""
    name: str = ""
    reg_type: int = 4
    hive: str = "HKCU"
    live: int = 0
    admin: bool = False
    read_only: bool = False
    #: Service / optional feature / capability name for those kinds.
    target_name: str = ""
    #: Offered only when `system.precondition(requires)` is True; None
    #: (unreadable) means not offered.
    requires: str = ""

    def read(self, system, scheme=None) -> dict:
        state = system.read(self, scheme)
        _validate_state(self, state)
        return state

    def target(self, before: dict) -> dict:
        if self.kind == "registry":
            state = {"exists": True, "key_exists": True, "value": self.value, "type": self.reg_type}
            if self.live:
                state["live"] = int(self.value)
            return state
        if self.kind == "service":
            if not before.get("exists"):
                return before
            if self.value == 4:
                return {"exists": True, "start_type": 4, "delayed": False, "running": False}
            return {"exists": True, "start_type": 2, "delayed": False, "running": True}
        if self.kind == "feature":
            return {"exists": True, "enabled": False} if before.get("exists") else before
        if self.kind == "capability":
            return {"exists": True, "installed": False} if before.get("exists") else before
        if self.kind == "power":
            return {"scheme": before["scheme"], "ac": 0}
        return before

    def apply(self, system, before: dict):
        """Returns what the write reported (e.g. {"restart": True}), or None."""
        return system.write(self, self.target(before))

    def revert(self, system, before: dict):
        return system.write(self, before)


ITEMS = (
    Spec("menu_delay", "Menu open delay", "T", "registry", "0",
         "Opens menus without a wait. Immediate, including the live desktop setting; "
         "menus can open accidentally as the pointer passes.", DESKTOP, "MenuShowDelay", 1, live=0x006B),
    Spec("hover_delay", "Hover delay", "T", "registry", "100",
         "Shows hover feedback after 100 ms. Immediate; tips may appear while moving the pointer.",
         MOUSE, "MouseHoverTime", 1, live=0x0067),
    Spec("explorer_home", "Explorer opens to This PC", "T", "registry", 1,
         "Avoids Home loading recent and cloud files. Takes effect in new Explorer windows; "
         "the recent-files landing page is no longer the default.", ADVANCED, "LaunchTo"),
    Spec("folder_tips", "No folder-size computing in hover tips", "T", "registry", 0,
         "Avoids counting a folder's contents just to show a tip. Takes effect in new Explorer "
         "windows; folder-size information disappears from tips.", ADVANCED, "FolderContentsInfoTip"),
    Spec("sysmain", "Re-enable SysMain", "M", "service", None,
         "Restores Windows' automatic app preloading and starts SysMain immediately. "
         "Can improve repeat launches, at the cost of background disk activity and cache memory.", admin=True,
         target_name="SysMain"),
    Spec("usb_suspend", "USB selective suspend off (AC only)", "M", "power", 0,
         "Keeps USB devices awake on mains power on the measured active scheme. Immediate; "
         "uses more power. Battery settings and other schemes are untouched.", admin=True),
    Spec("background_apps", "Background Windows apps off", "O", "registry", 1,
         "Optional: stops packaged apps working in the background. Teams/WhatsApp may stop "
         "notifying. Apps may need to be reopened; neither profile selects this.",
         r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", "GlobalUserDisabled"),
    Spec("remote_assistance", "Remote Assistance off", "T", "registry", 0,
         "Stops this PC accepting Remote Assistance invitations (Quick Assist is separate and "
         "keeps working). Immediate; someone helping you through an invitation can no longer connect.",
         r"SYSTEM\CurrentControlSet\Control\Remote Assistance", "fAllowToGetHelp",
         hive="HKLM", admin=True),
    Spec("retail_demo", "Retail Demo service disabled", "T", "service", 4,
         "The shop-display demo mode has no use on a personal PC. Revert restores the exact "
         "recorded start type and delayed flag.", admin=True, target_name="RetailDemo"),
    Spec("chrome_background", "Chrome background apps off", "M", "registry", 0,
         "Policy BackgroundModeEnabled=0: Chrome stops running after its last window closes. "
         "PWAs and extensions stop running when the browser is closed (no background "
         "notifications). Takes effect at the next Chrome start.",
         r"SOFTWARE\Policies\Google\Chrome", "BackgroundModeEnabled", hive="HKLM", admin=True,
         requires="chrome"),
    Spec("brave_background", "Brave background apps off", "M", "registry", 0,
         "Policy BackgroundModeEnabled=0: Brave stops running after its last window closes. "
         "PWAs and extensions stop running when the browser is closed (no background "
         "notifications). Takes effect at the next Brave start.",
         r"SOFTWARE\Policies\BraveSoftware\Brave", "BackgroundModeEnabled", hive="HKLM", admin=True,
         requires="brave"),
    Spec("ink_workspace", "Windows Ink Workspace off", "O", "registry", 0,
         "Optional: removes the pen workspace. Offered only because no pen or touch device is "
         "present; a pen added later will have no Ink Workspace until this is reverted.",
         r"SOFTWARE\Policies\Microsoft\WindowsInkWorkspace", "AllowWindowsInkWorkspace",
         hive="HKLM", admin=True, requires="no_pen_touch"),
    Spec("work_folders", "Work Folders client off", "O", "feature", None,
         "Optional: Work Folders syncs files from a company file server. Nothing on this PC "
         "can use it without that server; turning it off removes the client. May need a restart.",
         admin=True, target_name="WorkFolders-Client"),
    Spec("smb_direct", "SMB Direct off", "O", "feature", None,
         "Optional: SMB Direct is SMB over RDMA network adapters. Offered only because no "
         "RDMA-capable adapter is present; an RDMA adapter added later falls back to plain SMB. "
         "May need a restart.", admin=True, target_name="SmbDirect", requires="no_rdma"),
    Spec("ie_mode", "Internet Explorer mode capability", "O", "capability", None,
         "Optional: removes the Internet Explorer engine. Edge's IE mode stops working, and "
         "so do old intranet sites, ActiveX pages and apps that embed the IE control. "
         + CAPABILITY_REVERT_NOTE, admin=True, target_name="Browser.InternetExplorer~~~~0.0.11.0"),
    Spec("legacy_wmp", "Legacy Windows Media Player capability", "O", "capability", None,
         "Optional: removes the old Windows Media Player (the new Media Player app stays). "
         "Apps that embed the WMP control and .wpl playlists stop working. "
         + CAPABILITY_REVERT_NOTE, admin=True, target_name="Media.WindowsMediaPlayer~~~~0.0.12.0"),
    Spec("vbscript", "VBScript capability", "O", "capability", None,
         "Optional: removes VBScript. What breaks: every .vbs/.vbe script (logon scripts, old "
         "admin tools), and installers whose MSI custom actions are VBScript fail mid-install. "
         + CAPABILITY_REVERT_NOTE, admin=True, target_name="VBSCRIPT~~~~"),
    Spec("snmp_trap", "SNMP Trap service disabled", "O", "service", 4,
         "Optional: SNMP Trap receives network-management traps for local SNMP programs. "
         "Monitoring software that listens for traps stops receiving them. Revert restores the "
         "exact recorded start type and delayed flag.", admin=True, target_name="SNMPTrap"),
    Spec("cap_handwriting", "Remove unused feature: English handwriting", "M", "capability", None,
         "No pen or touch device here, so handwriting recognition has nothing to read. "
         + CAPABILITY_REVERT_NOTE, admin=True, target_name="Language.Handwriting~~~en-US~0.0.1.0",
         requires="no_pen_touch"),
    Spec("cap_math", "Remove unused feature: Math Recognizer", "M", "capability", None,
         "The Math Input Panel recognises handwritten maths; it needs a pen. "
         + CAPABILITY_REVERT_NOTE, admin=True, target_name="MathRecognizer~~~~0.0.1.0",
         requires="no_pen_touch"),
    Spec("cap_steps", "Remove unused feature: Steps Recorder", "M", "capability", None,
         "Steps Recorder is deprecated by Microsoft; Snipping Tool's recording replaces it. "
         + CAPABILITY_REVERT_NOTE, admin=True, target_name="App.StepsRecorder~~~~0.0.1.0"),
    Spec("cap_print_console", "Remove unused feature: Print Management console", "O", "capability", None,
         "Optional: printmanagement.msc manages print servers and queues; Settings > Printers "
         "keeps working. " + CAPABILITY_REVERT_NOTE, admin=True,
         target_name="Print.Management.Console~~~~0.0.1.0"),
    Spec("window_animations", "Window animations", "R", "registry", "0",
         "Read only: disabled animations avoid transition time. Tweaks owns this setting.",
         DESKTOP + r"\WindowMetrics", "MinAnimate", 1, read_only=True),
    Spec("taskbar_animations", "Taskbar animations", "R", "registry", 0,
         "Read only: removes taskbar animation time. Tweaks owns this setting.",
         ADVANCED, "TaskbarAnimations", read_only=True),
    Spec("transparency", "Transparency", "R", "registry", 0,
         "Read only: opaque surfaces reduce visual effects. Tweaks owns this setting.",
         r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", read_only=True),
    Spec("aero_peek", "Aero Peek", "R", "registry", 0,
         "Read only: Peek previews are a visual preference. Tweaks owns this setting.",
         r"Software\Microsoft\Windows\DWM", "EnableAeroPeek", read_only=True),
    Spec("keyboard_delay", "Keyboard repeat delay", "R", "registry", "0",
         "Read only: 0 is the shortest repeat delay. Tweaks owns this setting.",
         r"Control Panel\Keyboard", "KeyboardDelay", 1, read_only=True),
    Spec("keyboard_speed", "Keyboard repeat speed", "R", "registry", "31",
         "Read only: 31 is the fastest repeat rate. Tweaks owns this setting.",
         r"Control Panel\Keyboard", "KeyboardSpeed", 1, read_only=True),
    Spec("startup_delay", "Startup app delay", "R", "registry", 0,
         "Read only: 0 removes Explorer's startup-app delay. Tweaks owns this setting.",
         EXPLORER + r"\Serialize", "StartupDelayInMSec", read_only=True),
    Spec("game_dvr", "Game DVR capture", "R", "registry", 0,
         "Read only: disabled capture avoids recording overhead. Tweaks owns this setting.",
         r"System\GameConfigStore", "GameDVR_Enabled", read_only=True),
    Spec("app_capture", "Game DVR app capture", "R", "registry", 0,
         "Read only: reports the companion capture switch. Tweaks owns this setting.",
         r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", read_only=True),
    Spec("hibernation", "Hibernation", "R", "registry", 0,
         "Read only: disabling hibernation frees its disk file but loses Hibernate and Fast Startup. "
         "Tweaks owns this setting; this is not a claim about frame rates.",
         r"SYSTEM\CurrentControlSet\Control\Power", "HibernateEnabled", hive="HKLM", read_only=True),
    Spec("power_plan", "Active power plan", "R", "plan", None,
         "Read only: shows the plan Windows is using. This tab never switches plans.", read_only=True),
)
BY_ID = {spec.id: spec for spec in ITEMS}


def profile_ids(profile: str) -> set[str]:
    if profile not in ("tailored", "maximum"):
        raise ValueError(f"Unknown profile: {profile}")
    levels = {"T", "M"} if profile == "maximum" else {"T"}
    return {spec.id for spec in ITEMS if spec.level in levels}


def _uint(value) -> bool:
    return type(value) is int and 0 <= value <= 0xFFFFFFFF


def _validate_state(spec: Spec, state: dict) -> None:
    """Reject incomplete before-reads and malformed recovery data, never guess."""
    if not isinstance(state, dict):
        raise ValueError(f"{spec.label}: missing state")
    if spec.kind == "registry":
        if type(state.get("exists")) is not bool or type(state.get("key_exists")) is not bool:
            raise ValueError(f"{spec.label}: missing registry presence")
        if state["exists"]:
            value, kind = state.get("value"), state.get("type")
            if not state["key_exists"] or type(kind) is not int or kind not in (1, 2, 4, 11):
                raise ValueError(f"{spec.label}: unsupported registry type")
            if kind in (1, 2) and (not isinstance(value, str) or not value.isdecimal()):
                raise ValueError(f"{spec.label}: unparseable registry value")
            if kind in (4, 11) and (type(value) is not int or value < 0 or value >= 2 ** (32 if kind == 4 else 64)):
                raise ValueError(f"{spec.label}: unparseable registry number")
        if spec.live and not _uint(state.get("live")):
            raise ValueError(f"{spec.label}: live delay unreadable")
    elif spec.kind == "service":
        if type(state.get("exists")) is not bool:
            raise ValueError(f"{spec.target_name} presence unreadable")
        if state["exists"] and (type(state.get("start_type")) is not int
                                or state["start_type"] not in (2, 3, 4)
                                or type(state.get("delayed")) is not bool
                                or type(state.get("running")) is not bool):
            raise ValueError(f"{spec.target_name} configuration/state unreadable")
    elif spec.kind in ("feature", "capability"):
        flag = "enabled" if spec.kind == "feature" else "installed"
        if type(state.get("exists")) is not bool:
            raise ValueError(f"{spec.target_name} presence unreadable")
        if state["exists"] and type(state.get(flag)) is not bool and not _pending(state):
            raise ValueError(f"{spec.target_name} state unreadable")
    else:
        if not isinstance(state.get("scheme"), str) or str(uuid.UUID(state["scheme"])) != state["scheme"].lower():
            raise ValueError("Power scheme GUID unreadable")
        if spec.kind == "power" and not _uint(state.get("ac")):
            raise ValueError("USB AC setting unreadable")
        if spec.kind == "plan" and not isinstance(state.get("name"), str):
            raise ValueError("Power plan name unreadable")


def _pending(state: dict) -> bool:
    return any(isinstance(state.get(flag), str) and state[flag].casefold() in PENDING_STATES
               for flag in ("enabled", "installed"))


def display(state: dict | None) -> str:
    if state is None:
        return "Could not read"
    if _pending(state):
        return "Pending restart"
    if "ac" in state:
        return f"AC {state['ac']} (scheme {state['scheme']})"
    if "name" in state:
        return f"{state['name']} ({state['scheme']})"
    if "enabled" in state:
        return "Enabled" if state["enabled"] else "Disabled"
    if "installed" in state:
        return "Installed" if state["installed"] else "Not present"
    if not state.get("exists"):
        return "Absent (Windows default)"
    if "start_type" in state:
        start = {2: "Automatic", 3: "Manual", 4: "Disabled"}[state["start_type"]]
        return f"{start}, delayed={state['delayed']}, " + ("Running" if state["running"] else "Stopped")
    value = f"{state['value']} (type {state['type']})"
    return value + (f", live {state['live']} ms" if "live" in state else "")


@dataclass
class Row:
    id: str
    before: dict | None
    target: dict | None
    status: str
    why: str
    #: False when this PC does not qualify (browser not installed, a pen is
    #: present, the item's service/feature/capability is absent, or the check
    #: was unreadable): never ticked, never applied.
    offered: bool = True


@dataclass
class Report:
    changed: list[str] = field(default_factory=list)
    optimal: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    restart: list[str] = field(default_factory=list)

    def text(self) -> str:
        def labels(ids):
            return ", ".join(BY_ID[i].label for i in ids) or "none"
        lines = [f"Changed/restored: {labels(self.changed)}.", f"Already optimal/restored: {labels(self.optimal)}."]
        lines.extend(f"{BY_ID[i].label}: {why}" for i, why in {**self.skipped, **self.failed}.items())
        if self.restart:
            lines.append(f"A restart is needed to finish: {labels(self.restart)}.")
        lines.append("Menu/hover, SysMain and USB changes take effect immediately. Explorer settings "
                     "take effect in new Explorer windows. Background apps may need reopening. "
                     "Browser policies apply at the next browser start.")
        return "\n".join(lines)


_PRECONDITION_TEXT = {
    "chrome": ("Google Chrome is not installed -- not offered.", "Could not tell whether Chrome is installed -- not offered."),
    "brave": ("Brave is not installed -- not offered.", "Could not tell whether Brave is installed -- not offered."),
    "no_pen_touch": ("A pen or touch device is present -- not offered.",
                     "Pen/touch devices could not be read -- not offered."),
    "no_rdma": ("An RDMA-capable network adapter is present -- not offered.",
                "RDMA adapters could not be read (Get-NetAdapterRdma) -- not offered."),
}


class Optimiser:
    def __init__(self, system=None, folder: str | None = None, space=None, usage=None) -> None:
        """`space` / `usage` (Free up space, Apps you may not use) default to the
        real engines only with the real system layer; a fake system gets None
        unless the test hands its own fakes in, so no test reads this PC."""
        real = system is None
        self.system = system if system is not None else WindowsSystem()
        self.path = Path(folder or default_dir()) / "optimise.json"
        if space is None and real:
            from .space import SpaceCleaner
            space = SpaceCleaner()
        if usage is None and real:
            from .app_usage import UsageReader
            usage = UsageReader()
        self.space = space
        self.usage = usage

    def _offered(self, spec: Spec, before: dict) -> tuple[bool, str]:
        """(offered, why not). A check that cannot be read never offers the item."""
        if spec.kind in PRESENCE_KINDS and not before.get("exists"):
            return False, f"{spec.target_name} is not on this PC."
        if not spec.requires:
            return True, ""
        try:
            answer = self.system.precondition(spec.requires)
        except (OSError, ValueError) as error:
            logger.warning("optimise OS: precondition %s unreadable: %s", spec.requires, error)
            answer = None
        if answer is True:
            return True, ""
        return False, _PRECONDITION_TEXT[spec.requires][0 if answer is False else 1]

    def notes(self) -> list[str]:
        """Read-only observations, read live; nothing here is ever changed."""
        lines = []
        try:
            insider = self.system.insider()
        except (OSError, ValueError) as error:
            logger.warning("optimise OS: Insider state unreadable: %s", error)
            insider = None
        for name, text in (
                ("wisvc", "Windows Insider service (wisvc) is Disabled{insider} -- Insider updates may not arrive."),
                ("XboxGipSvc", "Xbox Accessory service (XboxGipSvc) is Disabled -- wired Xbox controllers / "
                               "accessory firmware need it.")):
            try:
                state = self.system.service_state(name)
            except (OSError, ValueError) as error:
                logger.warning("optimise OS: %s unreadable: %s", name, error)
                lines.append(f"{name}: could not be read ({error}).")
                continue
            if not state.get("exists") or state.get("start_type") != 4:
                continue
            if name == "wisvc":
                if insider is False:
                    continue
                text = text.format(insider=" on an Insider build" if insider
                                   else " (could not tell whether this is an Insider build)")
            lines.append(text)
        return lines

    def game_mode_holds(self) -> dict[str, str]:
        """{item id: why it waits} for the only two items an active Game Mode
        session overlaps with. Everything else applies normally while it is ON.

        M1 waits only when Game Mode's journal records that IT stopped SysMain;
        M2 waits whenever Game Mode switched the power plan, because the active
        scheme is then Game Mode's, not the owner's. A journal that cannot be
        read is treated as holding both -- for these two items only."""
        journal = Journal(str(self.path.parent))
        if not journal.load():
            return {}
        corrupt = "corrupt" in journal.data
        actions = [a for a in journal.actions if isinstance(a, dict)]
        holds = {}
        if corrupt or any(a.get("kind") == "service_stop" and str(a.get("name", "")).lower() == "sysmain"
                          for a in actions):
            holds["sysmain"] = GAME_MODE_HOLDS["sysmain"]
        if corrupt or any(a.get("kind") == "plan" for a in actions):
            holds["usb_suspend"] = GAME_MODE_HOLDS["usb_suspend"]
        return holds

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "kind": "optimise_os", "items": []}
        except (OSError, ValueError) as error:
            raise OptimiseError(f"Optimise OS journal unreadable; left untouched: {error}") from error
        try:
            if (not isinstance(data, dict) or data.get("version") != 1
                    or data.get("kind") != "optimise_os" or not isinstance(data.get("items"), list)):
                raise ValueError("invalid header")
            seen = set()
            for entry in data["items"]:
                if (not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
                        or entry["id"] not in BY_ID or entry["id"] in seen
                        or BY_ID[entry["id"]].read_only or type(entry.get("done")) is not bool):
                    raise ValueError("invalid or duplicate item")
                spec = BY_ID[entry["id"]]
                _validate_state(spec, entry.get("before"))
                if spec.kind in PRESENCE_KINDS and not entry["before"]["exists"]:
                    raise ValueError(f"cannot restore a missing {spec.kind}")
                seen.add(entry["id"])
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise OptimiseError(f"Optimise OS journal corrupt; left untouched: {error}") from error
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Journal._write(str(self.path), data)

    def inspect(self, selected: set[str]) -> list[Row]:
        with _LOCK:
            data = self._load()
            journaled = {e["id"]: e for e in data["items"]}
            admin = self.system.is_admin()
            holds = self.game_mode_holds() if selected & set(GAME_MODE_HOLDS) else {}
            rows = []
            for spec in ITEMS:
                try:
                    before = spec.read(self.system)
                    target = spec.target(before)
                except (OSError, ValueError) as error:
                    logger.warning("optimise OS: %s could not read: %s", spec.id, error)
                    rows.append(Row(spec.id, None, None, "Could not read", f"Could not read -- left alone: {error}"))
                    continue
                why = spec.why
                if spec.kind in ("feature", "capability") and _pending(before):
                    rows.append(Row(spec.id, before, target, "Pending restart",
                                    f"{why} Waiting for restart -- left alone.", False))
                    continue
                offered, reason = (True, "") if spec.read_only else self._offered(spec, before)
                entry = journaled.get(spec.id)
                if not offered and not (entry and before == target):
                    rows.append(Row(spec.id, before, target, "Not applicable", f"{why} {reason}", False))
                    continue
                if spec.kind == "plan":
                    status = "Already optimal"
                    why += " Observed only; no preferred plan is imposed."
                elif before == target:
                    entry = journaled.get(spec.id)
                    status = "Changed by Optimise OS" if entry and entry["done"] else "Already optimal"
                elif spec.read_only:
                    status = "Not applicable"
                    why += " Not at the fast target; left alone here. Use Tweaks to change it."
                elif spec.admin and not admin:
                    status = "Needs administrator"
                elif spec.id in holds and spec.id in selected:
                    status = "After Game Mode OFF"
                    why += f" {holds[spec.id]}."
                elif spec.id in selected:
                    status = "Will change"
                else:
                    status = "Not applicable"
                    why += " Not selected by this profile; tick it to opt in."
                rows.append(Row(spec.id, before, target, status, why))
            return rows

    def apply(self, selected: set[str], expected: dict[str, dict] | None = None, cancelled=lambda: False) -> Report:
        """Re-read before each step; an approved preview may also pin its exact before-state."""
        with _LOCK, operation_lock("Optimise OS apply"):
            data = self._load()
            entries = {e["id"]: e for e in data["items"]}
            report = Report()
            admin = self.system.is_admin()
            for spec in ITEMS:
                if spec.id not in selected:
                    continue
                if cancelled():
                    report.skipped[spec.id] = "Cancelled -- left alone"
                    continue
                if spec.read_only:
                    report.skipped[spec.id] = "Read only -- use Tweaks"
                    continue
                if spec.admin and not admin:
                    report.skipped[spec.id] = "Needs administrator"
                    continue
                held = self.game_mode_holds().get(spec.id) if spec.id in GAME_MODE_HOLDS else None
                if held:
                    report.skipped[spec.id] = held
                    continue
                try:
                    before = spec.read(self.system)
                except (OSError, ValueError) as error:
                    logger.warning("optimise OS: %s before-read refused: %s", spec.id, error)
                    report.skipped[spec.id] = f"Could not read -- left alone: {error}"
                    continue
                if spec.kind in ("feature", "capability") and _pending(before):
                    report.skipped[spec.id] = "Pending restart; waiting for restart -- left alone"
                    continue
                offered, reason = self._offered(spec, before)
                if not offered:
                    report.skipped[spec.id] = "Not applicable" + (f" -- {reason}" if spec.requires else "")
                    continue
                target = spec.target(before)
                if before == target:
                    report.optimal.append(spec.id)
                    continue
                if expected is not None and expected.get(spec.id) != before:
                    report.skipped[spec.id] = "Changed since confirmation -- refresh and confirm again"
                    continue
                entry = entries.get(spec.id)
                if entry is None:
                    entry = {"id": spec.id, "before": before, "done": False}
                    data["items"].append(entry)
                    entries[spec.id] = entry
                elif spec.kind == "power" and before["scheme"] != entry["before"]["scheme"]:
                    report.skipped[spec.id] = "Active scheme changed -- Revert the recorded scheme first"
                    continue
                entry["done"] = False
                self._save(data)                  # failure here must prevent the write
                try:
                    outcome = spec.apply(self.system, before)
                    after = spec.read(self.system, before.get("scheme"))
                    if after != target:
                        raise OSError(f"read-back mismatch: {display(after)}")
                    entry["done"] = True
                    self._save(data)
                    logger.info("optimise OS: %s: %s -> %s", spec.id, display(before), display(after))
                    report.changed.append(spec.id)
                    if isinstance(outcome, dict) and outcome.get("restart"):
                        report.restart.append(spec.id)
                except (OSError, ValueError) as error:
                    logger.warning("optimise OS: %s apply incomplete: %s", spec.id, error)
                    report.failed[spec.id] = f"Apply incomplete; before-state kept for Revert: {error}"
            from core.timeline import record
            record(self.path.parent.parent, "optimise", "apply", f"Optimise OS: {len(report.changed)} changed, {len(report.failed)} failed", {"changed": report.changed, "failed": list(report.failed), "skipped": len(report.skipped), "restart": report.restart}, "warning" if report.failed or report.skipped else "info")
            return report

    def revert(self, cancelled=lambda: False) -> Report:
        with _LOCK, operation_lock("Optimise OS revert"):
            data = self._load()
            report = Report()
            admin = self.system.is_admin()
            for entry in list(reversed(data["items"])):
                spec, before = BY_ID[entry["id"]], entry["before"]
                if cancelled():
                    report.failed[spec.id] = "Cancelled; journal kept"
                    continue
                if spec.admin and not admin:
                    report.failed[spec.id] = "Needs administrator; journal kept"
                    continue
                held = self.game_mode_holds().get(spec.id) if spec.id in GAME_MODE_HOLDS else None
                if held:
                    entry["error"] = "held by Game Mode; revert after Game Mode OFF"
                    report.skipped[spec.id] = entry["error"]
                    self._save(data)
                    continue
                try:
                    current = spec.read(self.system, before.get("scheme"))
                    if spec.kind in ("feature", "capability") and _pending(current):
                        entry["error"] = "waiting for restart"
                        report.skipped[spec.id] = entry["error"]
                        self._save(data)
                        continue
                    if current != before:
                        outcome = spec.revert(self.system, before)
                        after = spec.read(self.system, before.get("scheme"))
                        if after != before:
                            raise OSError(f"restore read-back mismatch: {display(after)}")
                        logger.info("optimise OS: restore %s: %s -> %s", spec.id, display(current), display(after))
                        report.changed.append(spec.id)
                        if isinstance(outcome, dict) and outcome.get("restart"):
                            report.restart.append(spec.id)
                    else:
                        report.optimal.append(spec.id)
                    data["items"].remove(entry)
                    self._save(data)              # checkpoint each successful recovery
                except (OSError, ValueError) as error:
                    logger.warning("optimise OS: %s restore incomplete: %s", spec.id, error)
                    if spec.kind == "capability":
                        # A re-add that failed (no Windows Update / internet, or no payload
                        # for this Insider build) stays pending WITH its error.
                        report.failed[spec.id] = (f"Re-adding failed; kept in the journal: {error}. "
                                                  "Add-WindowsCapability needs Windows Update / internet.")
                        entry["error"] = str(error)
                        try:
                            self._save(data)
                        except OSError as save_error:
                            logger.warning("optimise OS: could not record %s error: %s", spec.id, save_error)
                        continue
                    report.failed[spec.id] = f"Restore incomplete; journal kept: {error}"
            if not data["items"] and self.path.exists():
                self.path.unlink()
            from core.timeline import record
            record(self.path.parent.parent, "optimise", "revert", f"Optimise OS revert: {len(report.changed)} restored, {len(report.failed)} failed", {"restored": report.changed, "failed": list(report.failed), "skipped": len(report.skipped), "restart": report.restart}, "warning" if report.failed or report.skipped else "info")
            return report


class _GUID(ctypes.Structure):
    _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD),
                ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)

    def text(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(self)))


class WindowsSystem:
    """Native registry, user32, SCM and power APIs; no machine reads in __init__."""

    def is_admin(self) -> bool:
        from core.admin_utils import is_admin
        return bool(is_admin())

    def __init__(self) -> None:
        self._lists: dict[str, tuple[float, dict | OSError]] = {}

    def read(self, spec: Spec, scheme=None) -> dict:
        if spec.kind == "registry":
            return self._read_registry(spec)
        if spec.kind == "service":
            return self._read_service(spec.target_name)
        if spec.kind in ("feature", "capability"):
            states = self._states(spec.kind)
            raw = states.get(spec.target_name.casefold())
            if raw is None:
                return {"exists": False}
            flag = "enabled" if spec.kind == "feature" else "installed"
            return {"exists": True, flag: _package_state(spec, raw)}
        guid = scheme or self._active_scheme()
        if spec.kind == "power":
            power = self._power()
            value = wintypes.DWORD()
            code = power.PowerReadACValueIndex(None, ctypes.byref(_GUID.parse(guid)),
                                               ctypes.byref(_GUID.parse(USB_SUBGROUP)),
                                               ctypes.byref(_GUID.parse(USB_SETTING)), ctypes.byref(value))
            self._check(code, "read USB AC value")
            return {"scheme": guid, "ac": value.value}
        size = wintypes.DWORD()
        power = self._power()
        code = power.PowerReadFriendlyName(None, ctypes.byref(_GUID.parse(guid)), None, None, None, ctypes.byref(size))
        if code not in (0, 234):
            self._check(code, "read power plan name size")
        if not size.value:
            raise OSError("Power plan name is empty")
        buffer = (ctypes.c_ubyte * size.value)()
        self._check(power.PowerReadFriendlyName(None, ctypes.byref(_GUID.parse(guid)), None, None,
                                                buffer, ctypes.byref(size)), "read power plan name")
        return {"scheme": guid, "name": bytes(buffer).decode("utf-16-le").rstrip("\0")}

    def write(self, spec: Spec, state: dict) -> None:
        _validate_state(spec, state)
        if spec.read_only:
            raise ValueError("Read-only item")
        if spec.kind == "registry":
            self._write_registry(spec, state)
        elif spec.kind == "service":
            self._write_service(spec.target_name, state)
        elif spec.kind in ("feature", "capability"):
            return self._write_package(spec, state)
        elif spec.kind == "power":
            power = self._power()
            guid = state["scheme"]
            self._check(power.PowerWriteACValueIndex(None, ctypes.byref(_GUID.parse(guid)),
                                                    ctypes.byref(_GUID.parse(USB_SUBGROUP)),
                                                    ctypes.byref(_GUID.parse(USB_SETTING)), state["ac"]),
                        "write USB AC value")
            # Refresh only a scheme which is STILL active. Never switch plans on recovery.
            if self._active_scheme() == guid:
                self._check(power.PowerSetActiveScheme(None, ctypes.byref(_GUID.parse(guid))), "refresh active scheme")

    @staticmethod
    def _check(code: int, operation: str) -> None:
        if code:
            raise OSError(code, f"Could not {operation}: {ctypes.FormatError(code).strip()}")

    @staticmethod
    def _power():
        dll = ctypes.WinDLL("powrprof", use_last_error=True)
        pointer = ctypes.POINTER(_GUID)
        signatures = {
            "PowerGetActiveScheme": [wintypes.HKEY, ctypes.POINTER(pointer)],
            "PowerReadACValueIndex": [wintypes.HKEY, pointer, pointer, pointer, ctypes.POINTER(wintypes.DWORD)],
            "PowerWriteACValueIndex": [wintypes.HKEY, pointer, pointer, pointer, wintypes.DWORD],
            "PowerSetActiveScheme": [wintypes.HKEY, pointer],
            "PowerReadFriendlyName": [wintypes.HKEY, pointer, pointer, pointer,
                                      ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)],
        }
        for name, args in signatures.items():
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, wintypes.DWORD
        return dll

    def _active_scheme(self) -> str:
        pointer = ctypes.POINTER(_GUID)()
        self._check(self._power().PowerGetActiveScheme(None, ctypes.byref(pointer)), "read active scheme")
        if not pointer:
            raise OSError("Active power scheme is unavailable")
        try:
            return pointer.contents.text()
        finally:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
            kernel.LocalFree(pointer)

    @staticmethod
    def _read_registry(spec: Spec) -> dict:
        import winreg
        hive = winreg.HKEY_CURRENT_USER if spec.hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        state = {"exists": False, "key_exists": False}
        try:
            with winreg.OpenKey(hive, spec.key, 0, winreg.KEY_READ) as key:
                state["key_exists"] = True
                try:
                    value, kind = winreg.QueryValueEx(key, spec.name)
                    state.update(exists=True, value=value, type=kind)
                except FileNotFoundError:
                    logger.debug("optimise OS: %s value absent", spec.id)
        except FileNotFoundError:
            logger.debug("optimise OS: %s key absent", spec.id)
        if spec.live:
            value = wintypes.UINT()
            WindowsSystem._spi(spec.live - 1, 0, ctypes.byref(value), 0)
            state["live"] = value.value
        return state

    @staticmethod
    def _spi(action, param, pointer, flags) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        fn = user.SystemParametersInfoW
        fn.argtypes, fn.restype = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT], wintypes.BOOL
        if not fn(action, param, pointer, flags):
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _write_registry(spec: Spec, state: dict) -> None:
        import winreg
        hive = winreg.HKEY_CURRENT_USER if spec.hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        # SPI with UPDATEINIFILE may write a value itself. Restore raw type/absence AFTER it.
        if spec.live:
            WindowsSystem._spi(spec.live, state["live"], None, 0x01 | 0x02)
        if state["exists"]:
            with winreg.CreateKeyEx(hive, spec.key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, spec.name, 0, state["type"], state["value"])
        else:
            try:
                with winreg.OpenKey(hive, spec.key, 0, winreg.KEY_SET_VALUE) as key:
                    try:
                        winreg.DeleteValue(key, spec.name)
                    except FileNotFoundError:
                        logger.debug("optimise OS: %s already absent", spec.id)
                if not state["key_exists"]:
                    with winreg.OpenKey(hive, spec.key, 0, winreg.KEY_READ) as key:
                        children, values, _stamp = winreg.QueryInfoKey(key)
                    if not children and not values:
                        winreg.DeleteKey(hive, spec.key)
            except FileNotFoundError:
                logger.debug("optimise OS: %s key already absent", spec.id)

    def service_state(self, name: str) -> dict:
        """Read-only notes: the same exact service read the items use."""
        return self._read_service(name)

    def insider(self) -> bool | None:
        """True on an Insider flight: a WindowsSelfHost BranchName, or a build
        above the newest general release. None when unreadable."""
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\WindowsSelfHost\Applicability") as key:
                try:
                    if winreg.QueryValueEx(key, "BranchName")[0]:
                        return True
                except FileNotFoundError:
                    logger.debug("optimise OS: no Insider BranchName")
        except FileNotFoundError:
            logger.debug("optimise OS: no WindowsSelfHost key")
        except OSError as error:
            logger.warning("optimise OS: WindowsSelfHost unreadable: %s", error)
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
                build = int(winreg.QueryValueEx(key, "CurrentBuild")[0])
        except (OSError, ValueError) as error:
            logger.warning("optimise OS: build unreadable: %s", error)
            return None
        return build > RELEASED_BUILD_MAX

    def precondition(self, name: str) -> bool | None:
        if name in ("chrome", "brave"):
            return _browser_installed(name)
        if name == "no_pen_touch":
            present = _pen_or_touch_present()
            return None if present is None else not present
        if name == "no_rdma":
            count = _rdma_adapter_count()
            return None if count is None else count == 0
        raise ValueError(f"Unknown precondition: {name}")

    def _states(self, kind: str) -> dict:
        """One PowerShell listing per kind, reused for 15 s (a read of all
        capabilities costs seconds); every write drops it."""
        cached = self._lists.get(kind)
        if cached and time.monotonic() - cached[0] < 15:
            if isinstance(cached[1], OSError):
                raise OSError(str(cached[1]))          # a refusal is not bought once per item
            return cached[1]
        try:
            return self._list_states(kind)
        except OSError as error:
            self._lists[kind] = (time.monotonic(), error)
            raise

    def _list_states(self, kind: str) -> dict:
        if kind == "feature":
            script = ("Get-WindowsOptionalFeature -Online -ErrorAction Stop | "
                      "ForEach-Object { $_.FeatureName + '|' + $_.State }")
        else:
            script = ("Get-WindowsCapability -Online -ErrorAction Stop | "
                      "ForEach-Object { $_.Name + '|' + $_.State }")
        out = _powershell(script, 180)
        states = {}
        for line in out.splitlines():
            name, sep, state = line.strip().partition("|")
            if sep:
                states[name.casefold()] = state.strip()
        if not states:
            raise OSError(f"No Windows {kind}s listed -- the read was refused or failed")
        self._lists[kind] = (time.monotonic(), states)
        return states

    def _write_package(self, spec: Spec, state: dict) -> dict:
        name = spec.target_name
        if not _SAFE_NAME.match(name) or any(name.casefold().startswith(p) for p in NEVER_OFFER):
            raise ValueError(f"Refusing {name}")
        self._lists.pop(spec.kind, None)
        if spec.kind == "feature":
            verb = "Enable" if state["enabled"] else "Disable"
            script = (f"$r = {verb}-WindowsOptionalFeature -Online -FeatureName '{name}' -NoRestart "
                      "-ErrorAction Stop; 'RESTART|' + [bool]$r.RestartNeeded")
        else:
            verb = "Add" if state["installed"] else "Remove"
            script = (f"$r = {verb}-WindowsCapability -Online -Name '{name}' -ErrorAction Stop; "
                      "'RESTART|' + [bool]$r.RestartNeeded")
        try:
            out = _powershell(script, 900)
        finally:
            self._lists.pop(spec.kind, None)
        restart = any(line.strip().casefold() == "restart|true" for line in out.splitlines())
        return {"restart": restart}

    @staticmethod
    def _read_service(name: str) -> dict:
        import pywintypes
        import win32service as svc
        manager = handle = None
        try:
            manager = svc.OpenSCManager(None, None, svc.SC_MANAGER_CONNECT)
            handle = svc.OpenService(manager, name, svc.SERVICE_QUERY_CONFIG | svc.SERVICE_QUERY_STATUS)
            start = svc.QueryServiceConfig(handle)[1]
            delayed = bool(svc.QueryServiceConfig2(handle, svc.SERVICE_CONFIG_DELAYED_AUTO_START_INFO))
            state = svc.QueryServiceStatus(handle)[1]
            if state not in (svc.SERVICE_RUNNING, svc.SERVICE_STOPPED):
                raise OSError(f"{name} is transitioning or paused; retry when stable")
            return {"exists": True, "start_type": start, "delayed": delayed, "running": state == svc.SERVICE_RUNNING}
        except pywintypes.error as error:
            if error.winerror == 1060:
                return {"exists": False}
            raise OSError(f"{name} read refused: {error}") from error
        finally:
            if handle is not None:
                svc.CloseServiceHandle(handle)
            if manager is not None:
                svc.CloseServiceHandle(manager)

    @staticmethod
    def _write_service(name: str, state: dict) -> None:
        import pywintypes
        import win32service as svc
        manager = handle = None
        try:
            manager = svc.OpenSCManager(None, None, svc.SC_MANAGER_CONNECT)
            access = svc.SERVICE_QUERY_CONFIG | svc.SERVICE_QUERY_STATUS | svc.SERVICE_CHANGE_CONFIG | svc.SERVICE_START | svc.SERVICE_STOP
            handle = svc.OpenService(manager, name, access)

            def wait(wanted):
                deadline = time.monotonic() + 30
                while svc.QueryServiceStatus(handle)[1] != wanted:
                    if time.monotonic() >= deadline:
                        raise OSError(f"Timed out waiting for {name}")
                    time.sleep(0.2)

            current = svc.QueryServiceStatus(handle)[1]
            if current not in (svc.SERVICE_RUNNING, svc.SERVICE_STOPPED):
                raise OSError(f"{name} is transitioning or paused; retry when stable")
            if not state["running"] and current == svc.SERVICE_RUNNING:
                svc.ControlService(handle, svc.SERVICE_CONTROL_STOP)
                wait(svc.SERVICE_STOPPED)
            start = state["start_type"]
            # Set the delayed-auto flag while Automatic, then restore the exact start type.
            # This also permits recovery of a service which was running but Disabled.
            temporary = svc.SERVICE_AUTO_START
            svc.ChangeServiceConfig(handle, svc.SERVICE_NO_CHANGE, temporary, svc.SERVICE_NO_CHANGE,
                                    None, None, 0, None, None, None, None)
            svc.ChangeServiceConfig2(handle, svc.SERVICE_CONFIG_DELAYED_AUTO_START_INFO, state["delayed"])
            if state["running"] and current == svc.SERVICE_STOPPED:
                svc.StartService(handle, None)
                wait(svc.SERVICE_RUNNING)
            if temporary != start:
                svc.ChangeServiceConfig(handle, svc.SERVICE_NO_CHANGE, start, svc.SERVICE_NO_CHANGE,
                                        None, None, 0, None, None, None, None)
        except pywintypes.error as error:
            raise OSError(f"{name} change refused: {error}") from error
        finally:
            if handle is not None:
                svc.CloseServiceHandle(handle)
            if manager is not None:
                svc.CloseServiceHandle(manager)


def _package_state(spec: Spec, raw: str) -> bool | str:
    """Keep pending states intact: the requested state is not yet effective."""
    state = raw.strip().casefold()
    if spec.kind == "feature":
        table = {"enabled": True, "disabled": False,
                 "disabledwithpayloadremoved": False}
        pending = {"enablepending", "disablepending"}
    else:
        table = {"installed": True, "notpresent": False}
        pending = {"installpending", "uninstallpending"}
    if state in pending:
        return raw.strip()
    if state not in table:
        raise OSError(f"{spec.target_name}: state {raw!r} -- not acted on")
    return table[state]


def _powershell(script: str, timeout: float) -> str:
    """Run one PowerShell script; a non-zero exit or a timeout is an OSError
    carrying PowerShell's own words."""
    try:
        proc = subprocess.run(
            [powershell_exe(), "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-Command",
             "$ProgressPreference = 'SilentlyContinue'; " + script],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise OSError(f"PowerShell timed out after {int(timeout)} s") from error
    if proc.returncode != 0:
        words = " ".join((proc.stderr or proc.stdout or "").split())[:400]
        raise OSError(f"PowerShell exit {proc.returncode}: {words or 'no output'}")
    return proc.stdout or ""


_BROWSER_EXE = {"chrome": ("chrome.exe", r"Google\Chrome\Application\chrome.exe"),
                "brave": ("brave.exe", r"BraveSoftware\Brave-Browser\Application\brave.exe")}


def _browser_installed(name: str) -> bool | None:
    """App Paths registration (machine or user) or the program at its usual
    install path. None only when every look was refused."""
    import os
    import winreg
    exe, relative = _BROWSER_EXE[name]
    refused = 0
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                path = winreg.QueryValueEx(key, "")[0]
                if path and os.path.isfile(str(path).strip('"')):
                    return True
        except FileNotFoundError:
            logger.debug("optimise OS: no App Paths entry for %s", exe)
        except OSError as error:
            refused += 1
            logger.warning("optimise OS: App Paths %s unreadable: %s", exe, error)
    for root in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if root and os.path.isfile(os.path.join(root, relative)):
            return True
    return None if refused == 2 else False


def _pen_or_touch_present() -> bool | None:
    """Digitizers from PnP (HID usage page 0x0D = digitizer: pen and touch,
    or a touch-screen / pen device name), plus GetSystemMetrics(SM_DIGITIZER).
    None when PnP could not be read."""
    try:
        out = _powershell(
            "Get-PnpDevice -PresentOnly -ErrorAction Stop | ForEach-Object { "
            "($_.FriendlyName + '|' + (($_.CompatibleID + $_.HardwareID) -join ';')) }", 60)
    except OSError as error:
        logger.warning("optimise OS: PnP devices unreadable: %s", error)
        return None
    if not out.strip():
        return None
    for line in out.splitlines():
        text = line.casefold()
        if "hid_device_up:000d" in text or re.search(r"touch ?screen|\bpen\b|digitizer", text):
            return True
    try:
        digitizer = ctypes.windll.user32.GetSystemMetrics(94)       # SM_DIGITIZER
    except OSError as error:
        logger.warning("optimise OS: SM_DIGITIZER unreadable: %s", error)
        return None
    return bool(digitizer & 0x0F)                                   # integrated/external touch or pen


def _rdma_adapter_count() -> int | None:
    try:
        out = _powershell("@(Get-NetAdapterRdma -ErrorAction Stop | Where-Object { $_.Enabled }).Count", 60)
        return int(out.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError) as error:
        logger.warning("optimise OS: RDMA adapters unreadable: %s", error)
        return None
