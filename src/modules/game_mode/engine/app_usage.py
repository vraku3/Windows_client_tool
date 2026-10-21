r"""Optimise OS "Apps you may not use": usage EVIDENCE, suggestions only. Qt-free.

Evidence Windows already keeps, never anything this app records:

* UserAssist (HKCU ...\Explorer\UserAssist\{GUID}\Count): value names are
  ROT13; the 72-byte data holds the run count at offset 4 and the last-run
  FILETIME at offset 60. `UEME_*` names are control records, and a zero or
  implausible FILETIME is skipped (UEME_CTLSESSION here decodes to 1641).
  Names may start with a KNOWNFOLDERID ({6D809377-...} = Program Files).
* BAM (HKLM\SYSTEM\CurrentControlSet\Services\bam\State\UserSettings\<SID>):
  last execution per program, as an NT device path (\Device\HarddiskVolume3\
  ...), translated with QueryDosDevice. Needs elevation.
* Prefetch (C:\Windows\Prefetch\NAME.EXE-HASH.pf) when present: only an exe
  NAME, so it can only ever make an app look USED, never unused.

An app is matched by FULL path: its Uninstall entry's DisplayIcon program or
anything inside its InstallLocation, or an AppX package's family / install
folder. Three states, and "no record" is never "unused":

* USED     -- seen in the last 90 days (date + source shown)
* STALE    -- last seen over 90 days ago: the only suggestion
* UNKNOWN  -- no usage record; shown with how far back the records reach

Never suggested: drivers, runtimes, redistributables, frameworks, security
software, AMD/chipset, launchers and games, and the owner's protected list.
The only action is "Open in App Buster", which removes with its own read-back.
"""
from __future__ import annotations

import logging
import os
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from core.security_vendors import SECURITY_VENDORS

logger = logging.getLogger(__name__)

USED = "Used in the last 90 days"
STALE = "Last seen over 90 days ago"
UNKNOWN = "No usage record"
WINDOW = timedelta(days=90)
_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_EARLIEST = datetime(2000, 1, 1, tzinfo=timezone.utc)

#: KNOWNFOLDERID prefixes UserAssist uses -> environment variable (or a path under one).
KNOWN_FOLDERS = {
    "{6d809377-6af0-444b-8957-a3773f02200e}": ("ProgramW6432", ""),
    "{7c5a40ef-a0fb-4bfc-874a-c0f2e0b9fa8e}": ("ProgramFiles(x86)", ""),
    "{f38bf404-1d43-42f2-9305-67de0b28fc23}": ("SystemRoot", ""),
    "{1ac14e77-02e7-4e5d-b744-2eb1ae5198b7}": ("SystemRoot", "System32"),
    "{d65231b0-b2f1-4857-a4ce-a8e7c6ea7d27}": ("SystemRoot", "SysWOW64"),
    "{f1b32785-6fba-4fcf-9d55-7b8e7f157091}": ("LOCALAPPDATA", ""),
    "{3eb685db-65f9-4cf6-a03a-e3ef65729f3d}": ("APPDATA", ""),
}

#: Never suggested, whatever the evidence says. Matched against name,
#: publisher and install folder, case-insensitive.
NEVER_SUGGEST = (
    # drivers, runtimes, redistributables, frameworks
    r"driver", r"runtime", r"redistributable", r"framework", r"visual c\+\+", r"\.net\b",
    r"\bsdk\b", r"directx", r"webview2", r"node\.js", r"pawnio", r"compatibility", r"handwriting",
    r"windows package manager", r"winget", r"extension",
    # hardware vendors and chipset software
    r"\bamd\b", r"advanced micro devices", r"radeon", r"ryzen", r"chipset", r"realtek", r"\bintel\b",
    r"nvidia", r"\basrock\b", r"lenovo",
    # security software
    *(re.escape(vendor) for vendor in SECURITY_VENDORS),
    r"antivirus", r"anti-virus", r"cyberghost", r"\bvpn\b",
    # launchers and games
    r"steam", r"epic games", r"battle\.net", r"blizzard", r"\bea app\b", r"electronic arts", r"ubisoft",
    r"\bgog\b", r"riot", r"xbox", r"gaming", r"\bgame", r"warcraft", r"rockstar", r"launcher",
    r"steam app \d+", r"sechealth",
    # the owner's protected list
    r"office", r"microsoft 365", r"onenote", r"onedrive", r"\bteams\b", r"\bword\b", r"\bexcel\b",
    r"outlook", r"chrome", r"brave", r"stremio", r"ollama", r"espeak",
    r"visual studio code", r"\bvs ?code\b", r"python", r"\bgit\b", r"github", r"codex", r"claude",
    r"openai", r"chatgpt", r"opencode",
)
_NEVER = re.compile("|".join(NEVER_SUGGEST), re.IGNORECASE)

#: AppX packages that are Windows itself or plumbing, never apps to judge.
_APPX_PLUMBING = ("microsoft.windows.", "microsoftwindows.", "windows.", "microsoft.ui.",
                  "microsoft.vclibs", "microsoft.net", "microsoft.services.store", "microsoft.windowsappruntime",
                  "microsoft.directx", "microsoft.advertising", "microsoft.desktopappinstaller",
                  "microsoft.storepurchaseapp", "microsoft.windowsstore", "microsoft.winappruntime",
                  "microsoft.secure", "microsoft.aad.", "microsoft.accountscontrol", "microsoft.lockapp")


@dataclass
class Evidence:
    key: str            # "path:<normcased full path>", "family:<family>" or "exe:<name>"
    when: datetime
    source: str


@dataclass
class AppUsage:
    name: str
    kind: str           # "Desktop app" | "Windows app"
    state: str
    last: datetime | None
    source: str
    location: str
    publisher: str = ""


@dataclass
class UsageReport:
    apps: list[AppUsage] = field(default_factory=list)
    reach: datetime | None = None
    protected: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def suggestions(self) -> list[AppUsage]:
        return [a for a in self.apps if a.state == STALE]

    def reach_text(self) -> str:
        if self.reach is None:
            return "No usage records could be read."
        return f"Usage records only go back to {self.reach:%Y-%m-%d}."

    def summary(self) -> str:
        unknown = sum(1 for a in self.apps if a.state == UNKNOWN)
        used = sum(1 for a in self.apps if a.state == USED)
        stale = len(self.suggestions)
        head = (f"{stale} suggestion(s): last seen over 90 days ago." if stale else
                "No suggestions: no app on this PC was last seen more than 90 days ago.")
        lines = [head, f"{used} used in the last 90 days; {unknown} with no usage record "
                       "(unknown -- never called unused). " + self.reach_text(),
                 f"{self.protected} app(s) are never suggested (drivers, runtimes, frameworks, "
                 "security, AMD/chipset, launchers and games, your protected list)."]
        lines += [f"Not read: {e}" for e in self.errors]
        return "\n".join(lines)


def rot13(text: str) -> str:
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - 97 + 13) % 26 + 97))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - 65 + 13) % 26 + 65))
        else:
            out.append(ch)
    return "".join(out)


def filetime(value: int, now: datetime | None = None) -> datetime | None:
    """A FILETIME, or None when it is zero or implausible."""
    if not value:
        return None
    try:
        when = _EPOCH + timedelta(microseconds=value // 10)
    except OverflowError:
        return None
    now = now or datetime.now(timezone.utc)
    if when < _EARLIEST or when > now + timedelta(days=1):
        return None
    return when


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip().strip('"')))


def expand_known_folder(name: str, env=os.environ) -> str:
    match = re.match(r"^(\{[0-9a-fA-F-]{36}\})(\\.*)?$", name)
    if not match:
        return name
    known = KNOWN_FOLDERS.get(match.group(1).lower())
    if known is None or not env.get(known[0]):
        return ""
    base = os.path.join(env[known[0]], known[1]) if known[1] else env[known[0]]
    return base + (match.group(2) or "")


def _name_key(name: str) -> str | None:
    """An evidence key for a program name UserAssist or BAM recorded."""
    if "!" in name and "\\" not in name:
        return "family:" + name.split("!", 1)[0].casefold()
    if "\\" not in name and re.match(r"^[\w.-]+_[a-z0-9]{13}$", name, re.IGNORECASE):
        return "family:" + name.casefold()
    if name.casefold().endswith(".exe") and re.match(r"^[a-zA-Z]:\\", name):
        return "path:" + _norm(name)
    return None


def userassist_evidence(records, now=None) -> list[Evidence]:
    """records: (raw ROT13 value name, data bytes)."""
    out = []
    for raw, data in records:
        name = rot13(raw)
        if name.upper().startswith("UEME_") or not isinstance(data, (bytes, bytearray)) or len(data) < 68:
            continue
        when = filetime(struct.unpack_from("<Q", data, 60)[0], now)
        if when is None:
            continue
        key = _name_key(expand_known_folder(name))
        if key:
            out.append(Evidence(key, when, "UserAssist"))
    return out


def bam_evidence(records, devices: dict[str, str], now=None) -> list[Evidence]:
    """records: (value name, data bytes); devices: '\\device\\harddiskvolume3' -> 'C:'."""
    out = []
    for name, data in records:
        if name in ("Version", "SequenceNumber") or not isinstance(data, (bytes, bytearray)) or len(data) < 8:
            continue
        when = filetime(struct.unpack_from("<Q", data, 0)[0], now)
        if when is None:
            continue
        path = name
        if name.casefold().startswith("\\device\\"):
            parts = name.split("\\", 3)
            if len(parts) < 4:
                continue
            drive = devices.get(("\\" + parts[1] + "\\" + parts[2]).casefold())
            if drive is None:
                continue
            path = drive + "\\" + parts[3]
        key = _name_key(path)
        if key:
            out.append(Evidence(key, when, "BAM"))
    return out


def prefetch_evidence(records, now=None) -> list[Evidence]:
    """records: (file name like NAME.EXE-1A2B3C4D.pf, modified datetime)."""
    out = []
    for name, when in records:
        match = re.match(r"^(.+\.exe)-[0-9A-F]{8}\.pf$", name, re.IGNORECASE)
        if match and when and _EARLIEST <= when <= (now or datetime.now(timezone.utc)) + timedelta(days=1):
            out.append(Evidence("exe:" + match.group(1).casefold(), when, "Prefetch"))
    return out


def _icon_exe(display_icon: str) -> str:
    text = (display_icon or "").strip()
    if text.startswith('"'):
        text = text[1:].split('"', 1)[0]
    else:
        text = re.sub(r",\s*-?\d+$", "", text)
    return _norm(text) if text.casefold().endswith(".exe") else ""


def _usable_folder(path: str) -> str:
    """An install folder, or "" when it is too broad to stand for one app."""
    if not path or not path.strip().strip('"'):
        return ""
    norm = _norm(path)
    broad = {_norm(os.environ.get(v, "")) for v in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)",
                                                    "SystemRoot", "LOCALAPPDATA", "APPDATA", "ProgramData")
             if os.environ.get(v)}
    if len(norm) <= 3 or norm in broad or norm.startswith(_norm(os.environ.get("SystemRoot", r"C:\Windows")) + "\\"):
        return ""
    return norm


def protected(*texts: str) -> bool:
    from .catalog import GAME_FOLDERS
    joined = " | ".join(t for t in texts if t)
    if _NEVER.search(joined):
        return True
    lowered = joined.casefold()
    return any(folder in lowered for folder in GAME_FOLDERS)


def _judge(evidence: list[Evidence], now: datetime) -> tuple[str, datetime | None, str]:
    if not evidence:
        return UNKNOWN, None, ""
    best = max(evidence, key=lambda e: e.when)
    state = USED if now - best.when <= WINDOW else STALE
    return state, best.when, best.source


def analyse(evidence: list[Evidence], desktop: list[dict], appx: list[dict],
            now: datetime | None = None, errors=()) -> UsageReport:
    now = now or datetime.now(timezone.utc)
    report = UsageReport(errors=list(errors))
    if evidence:
        report.reach = min(e.when for e in evidence)
    by_key: dict[str, list[Evidence]] = {}
    paths: list[tuple[str, Evidence]] = []
    for item in evidence:
        by_key.setdefault(item.key, []).append(item)
        if item.key.startswith("path:"):
            paths.append((item.key[5:], item))
    seen = set()
    for entry in desktop:
        name = (entry.get("name") or "").strip()
        if not name or entry.get("system_component") or entry.get("is_update"):
            continue
        location = entry.get("install_location") or ""
        ident = name.casefold()
        if ident in seen:
            continue
        seen.add(ident)
        if protected(name, entry.get("publisher", ""), location, entry.get("key", "")):
            report.protected += 1
            continue
        exe = _icon_exe(entry.get("display_icon", ""))
        folder = _usable_folder(location)
        found = []
        if exe:
            found += by_key.get("path:" + exe, [])
            found += by_key.get("exe:" + os.path.basename(exe), [])
        if folder:
            found += [e for p, e in paths if p.startswith(folder + "\\")]
        state, last, source = _judge(found, now)
        if not exe and not folder:
            source = "no program or install folder recorded to match against"
        report.apps.append(AppUsage(name, "Desktop app", state, last, source,
                                    location or entry.get("display_icon", ""), entry.get("publisher", "")))
    for pkg in appx:
        full = pkg.get("full_name") or ""
        parts = full.split("_")
        if len(parts) < 5:
            continue
        package, resource, publisher_id = parts[0], parts[3], parts[-1]
        root = pkg.get("root") or ""
        lowered = package.casefold()
        if (resource not in ("", "~") or "\\windowsapps\\" not in root.casefold()
                or lowered.startswith(_APPX_PLUMBING)):
            continue
        family = f"{package}_{publisher_id}".casefold()
        if family in seen:
            continue
        seen.add(family)
        if pkg.get("game") or protected(package, pkg.get("display", ""), root):
            report.protected += 1
            continue
        folder = _norm(root)
        found = by_key.get("family:" + family, []) + [e for p, e in paths if p.startswith(folder + "\\")]
        state, last, source = _judge(found, now)
        report.apps.append(AppUsage(pkg.get("display") or package, "Windows app", state, last, source, root))
    order = {STALE: 0, UNKNOWN: 1, USED: 2}
    report.apps.sort(key=lambda a: (order[a.state], a.name.casefold()))
    return report


class UsageReader:
    """Reads the evidence and the installed apps from this PC (read-only)."""

    def __init__(self, backend=None) -> None:
        self.backend = backend if backend is not None else RegistryBackend()

    def analyse(self, now: datetime | None = None) -> UsageReport:
        errors, evidence = [], []
        now = now or datetime.now(timezone.utc)
        for label, read in (("UserAssist", lambda: userassist_evidence(self.backend.userassist(), now)),
                            ("BAM", lambda: bam_evidence(self.backend.bam(), self.backend.devices(), now)),
                            ("Prefetch", lambda: prefetch_evidence(self.backend.prefetch(), now))):
            try:
                evidence += read()
            except OSError as error:
                logger.warning("optimise OS: %s unreadable: %s", label, error)
                errors.append(f"{label} ({error})")
        desktop, appx = [], []
        try:
            desktop = self.backend.uninstall()
        except OSError as error:
            errors.append(f"installed programs ({error})")
        try:
            appx = self.backend.appx()
        except OSError as error:
            errors.append(f"Windows apps ({error})")
        return analyse(evidence, desktop, appx, now, errors)


class RegistryBackend:
    UA = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
    BAM = r"SYSTEM\CurrentControlSet\Services\bam\State\UserSettings"
    UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    REPOSITORY = (r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
                  r"\AppModel\Repository\Packages")

    @staticmethod
    def _values(key):
        import winreg
        index = 0
        while True:
            try:
                name, data, _kind = winreg.EnumValue(key, index)
            except OSError:
                return
            index += 1
            yield name, data

    @staticmethod
    def _subkeys(key):
        import winreg
        index = 0
        while True:
            try:
                yield winreg.EnumKey(key, index)
            except OSError:
                return
            index += 1

    def userassist(self):
        import winreg
        out = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.UA) as root:
            for guid in list(self._subkeys(root)):
                try:
                    with winreg.OpenKey(root, guid + r"\Count") as key:
                        out += list(self._values(key))
                except FileNotFoundError:
                    logger.debug("optimise OS: UserAssist %s has no Count", guid)
        return out

    def bam(self):
        """Only the current user's SID: other accounts' programs are not this owner's apps."""
        import winreg
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self.BAM + "\\" + sid) as key:
                return list(self._values(key))
        except PermissionError as error:
            raise OSError(f"needs administrator: {error}") from error

    def devices(self) -> dict[str, str]:
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        out = {}
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            buffer = ctypes.create_unicode_buffer(1024)
            if kernel.QueryDosDeviceW(f"{letter}:", buffer, 1024):
                out[buffer.value.casefold()] = f"{letter}:"
        return out

    def prefetch(self):
        folder = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Prefetch")
        out = []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.casefold().endswith(".pf"):
                        stamp = entry.stat(follow_symlinks=False).st_mtime
                        out.append((entry.name, datetime.fromtimestamp(stamp, timezone.utc)))
        except FileNotFoundError:
            return []
        return out

    def uninstall(self):
        import winreg
        out = []
        roots = ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                 (winreg.HKEY_CURRENT_USER, 0))
        for hive, view in roots:
            try:
                root = winreg.OpenKey(hive, self.UNINSTALL, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                logger.debug("app usage: no Uninstall key in hive %s view %s", hive, view)
                continue
            with root:
                for sub in list(self._subkeys(root)):
                    try:
                        with winreg.OpenKey(root, sub) as key:
                            values = dict(self._values(key))
                    except OSError as error:
                        logger.debug("optimise OS: uninstall %s unreadable: %s", sub, error)
                        continue
                    out.append({
                        "name": str(values.get("DisplayName") or ""),
                        "publisher": str(values.get("Publisher") or ""),
                        "install_location": str(values.get("InstallLocation") or ""),
                        "display_icon": str(values.get("DisplayIcon") or ""),
                        "system_component": values.get("SystemComponent") == 1,
                        "is_update": bool(values.get("ParentKeyName")) or values.get("ReleaseType") in (
                            "Update", "Hotfix", "Security Update"),
                        "key": sub,
                    })
        return out

    def appx(self):
        import winreg
        out = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.REPOSITORY) as root:
            for full in list(self._subkeys(root)):
                try:
                    with winreg.OpenKey(root, full) as key:
                        values = dict(self._values(key))
                except OSError as error:
                    logger.debug("optimise OS: package %s unreadable: %s", full, error)
                    continue
                display = str(values.get("DisplayName") or "")
                root_folder = str(values.get("PackageRootFolder") or "")
                out.append({"full_name": full, "root": root_folder,
                            "display": "" if display.startswith(("@", "ms-resource:")) else display,
                            # Gaming Services' marker: Store / PC Game Pass games carry it.
                            "game": bool(root_folder) and os.path.isfile(
                                os.path.join(root_folder, "MicrosoftGame.config"))})
        return out
