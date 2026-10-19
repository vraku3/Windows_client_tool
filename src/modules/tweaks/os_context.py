# src/modules/tweaks/os_context.py
"""What this Windows install actually *is* — so a tweak can answer
"not applicable here" instead of shrugging with "unknown".

Every field is read once and cached for the life of the process. None of it
changes without a reboot (build, edition, architecture) or without the user
installing something (a service appearing), and the status sweep asks these
questions once per tweak across hundreds of tweaks — re-reading the registry
that many times for the same build number is pure waste.

`applies_to` blocks in the tweak JSON are matched against this. See
`OSContext.evaluate()` for the supported keys.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import platform
import subprocess
import sys
import threading
import winreg
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

logger = logging.getLogger(__name__)

_CV_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"

#: Windows 11 starts at build 22000. Anything below is Windows 10 (or older).
WIN11_MIN_BUILD = 22000

#: Named feature-update builds, so a JSON definition can say
#: `"min_build": "22H2"` instead of memorising five-digit numbers.
BUILD_ALIASES: Dict[str, int] = {
    "WIN10": 10240,
    "1507": 10240, "1511": 10586, "1607": 14393, "1703": 15063,
    "1709": 16299, "1803": 17134, "1809": 17763, "1903": 18362,
    "1909": 18363, "2004": 19041, "20H2": 19042, "21H1": 19043,
    "21H2": 19044, "22H2": 19045,
    "WIN11": 22000,
    "WIN11_21H2": 22000, "WIN11_22H2": 22621, "WIN11_23H2": 22631,
    "WIN11_24H2": 26100, "WIN11_25H2": 26200,
    "23H2": 22631, "24H2": 26100, "25H2": 26200,
}

#: EditionID values that do NOT process Group-Policy-backed registry keys the
#: way Pro/Enterprise do. gpedit.msc is absent on these SKUs; many (not all)
#: policy values under SOFTWARE\\Policies are simply ignored by the OS there.
HOME_EDITIONS: FrozenSet[str] = frozenset({
    "core", "corecountryspecific", "coren", "coresinglelanguage",
    "cloud", "cloudn",  # Windows 11 SE / S mode
})


def _reg_str(key: str, value: str, hive=winreg.HKEY_LOCAL_MACHINE) -> str:
    try:
        with winreg.OpenKey(hive, key) as k:
            data, _ = winreg.QueryValueEx(k, value)
        return str(data)
    except OSError:
        return ""


def _reg_int(key: str, value: str, hive=winreg.HKEY_LOCAL_MACHINE) -> Optional[int]:
    try:
        with winreg.OpenKey(hive, key) as k:
            data, _ = winreg.QueryValueEx(k, value)
        return int(data)
    except (OSError, ValueError, TypeError):
        return None


def resolve_build(spec) -> Optional[int]:
    """Turn 22631, "22631" or "23H2" into a build number.

    Returns None for anything unrecognised, which callers treat as
    "no constraint" rather than "never matches" — a typo in a definition file
    must not silently hide a working tweak.
    """
    if spec is None:
        return None
    if isinstance(spec, int):
        return spec
    text = str(spec).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    alias = BUILD_ALIASES.get(text.upper().replace(" ", "_").replace("-", "_"))
    if alias is None:
        logger.warning("Unknown build alias in tweak definition: %r", spec)
    return alias


@dataclass(frozen=True)
class TaskInfo:
    """What one `schtasks /query` told us. `exists` is None when the query
    itself failed — "I could not look" is not "it is not there"."""
    exists: Optional[bool]
    status: str = ""


@dataclass(frozen=True)
class Applicability:
    """Answer to "does this tweak make sense on this machine?"."""
    applicable: bool
    reason: str = ""


APPLICABLE = Applicability(True)


class OSContext:
    """Cached facts about the running Windows install."""

    _instance: Optional["OSContext"] = None

    def __init__(self) -> None:
        self.build: int = self._read_build()
        self.ubr: int = _reg_int(_CV_KEY, "UBR") or 0
        self.display_version: str = (
            _reg_str(_CV_KEY, "DisplayVersion")
            or _reg_str(_CV_KEY, "ReleaseId")
        )
        self.edition_id: str = _reg_str(_CV_KEY, "EditionID")
        self.product_name: str = _reg_str(_CV_KEY, "ProductName")
        self.install_type: str = _reg_str(_CV_KEY, "InstallationType")  # Client/Server
        self.arch: str = (os.environ.get("PROCESSOR_ARCHITEW6432")
                          or os.environ.get("PROCESSOR_ARCHITECTURE")
                          or platform.machine()).upper()

        self._service_cache: Dict[str, Optional[bool]] = {}
        self._task_cache: Dict[str, TaskInfo] = {}
        self._appx_names: Optional[FrozenSet[str]] = None
        self._appx_failed = False
        # Detection runs many probes at once; two threads racing on the same
        # miss only costs a duplicate subprocess, but the appx enumeration is
        # expensive enough to be worth serialising.
        self._appx_lock = threading.Lock()

    # -- derived properties ------------------------------------------------

    @property
    def is_win11(self) -> bool:
        return self.build >= WIN11_MIN_BUILD

    @property
    def is_win10(self) -> bool:
        return 10000 <= self.build < WIN11_MIN_BUILD

    @property
    def is_server(self) -> bool:
        return self.install_type.lower().startswith("server")

    @property
    def is_home_edition(self) -> bool:
        return self.edition_id.lower() in HOME_EDITIONS

    @property
    def is_64bit(self) -> bool:
        return self.arch in ("AMD64", "ARM64", "IA64")

    @property
    def is_arm(self) -> bool:
        return self.arch.startswith("ARM")

    @property
    def friendly_name(self) -> str:
        gen = "Windows 11" if self.is_win11 else "Windows 10"
        ver = f" {self.display_version}" if self.display_version else ""
        ed = f" {self.edition_id}" if self.edition_id else ""
        return f"{gen}{ver}{ed} (build {self.build}.{self.ubr}, {self.arch})"

    # -- lookups -----------------------------------------------------------

    @staticmethod
    def _read_build() -> int:
        """CurrentBuildNumber is a string; on Win11 CurrentMajorVersionNumber
        still reads 10, so the build number is the only honest discriminator."""
        raw = _reg_str(_CV_KEY, "CurrentBuildNumber")
        if raw.isdigit():
            return int(raw)
        try:
            return int(sys.getwindowsversion().build)
        except Exception:
            return 0

    def service_exists(self, name: str) -> Optional[bool]:
        """True / False, or None when we could not tell (access denied).

        None matters: "I am not allowed to look" must not be reported to the
        user as "this service is not on your system".
        """
        key = name.lower()
        if key in self._service_cache:
            return self._service_cache[key]
        result: Optional[bool]
        try:
            import win32service
            import pywintypes
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                try:
                    hs = win32service.OpenService(
                        hscm, name, win32service.SERVICE_QUERY_CONFIG)
                    win32service.CloseServiceHandle(hs)
                    result = True
                except pywintypes.error as e:
                    # 1060 = ERROR_SERVICE_DOES_NOT_EXIST
                    result = False if e.winerror == 1060 else None
            finally:
                win32service.CloseServiceHandle(hscm)
        except Exception as e:
            logger.debug("service_exists(%s) failed: %s", name, e)
            result = None
        self._service_cache[key] = result
        return result

    def scheduled_task_query(self, task_name: str) -> "TaskInfo":
        """One schtasks call answers both "does it exist" and "is it disabled".

        Detection needs both, and schtasks costs a process launch each time —
        asking twice per task doubled the cost of a status sweep for nothing.
        """
        key = task_name.lower()
        cached = self._task_cache.get(key)
        if cached is not None:
            return cached
        info: TaskInfo
        try:
            proc = subprocess.run(
                ["schtasks", "/query", "/tn", task_name, "/fo", "LIST"],
                capture_output=True, text=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if proc.returncode == 0:
                status = ""
                for line in proc.stdout.splitlines():
                    if line.strip().lower().startswith("status:"):
                        status = line.split(":", 1)[1].strip()
                        break
                info = TaskInfo(True, status)
            else:
                err = (proc.stderr or proc.stdout or "").lower()
                if "access is denied" in err:
                    info = TaskInfo(None, "")
                else:
                    # "cannot find the file specified" / "does not exist"
                    info = TaskInfo(False, "")
        except Exception as e:
            logger.debug("scheduled_task_query(%s) failed: %s", task_name, e)
            info = TaskInfo(None, "")
        self._task_cache[key] = info
        return info

    def scheduled_task_exists(self, task_name: str) -> Optional[bool]:
        return self.scheduled_task_query(task_name).exists

    def appx_packages(self) -> Optional[FrozenSet[str]]:
        """Every installed UWP package name, lowercased — fetched with ONE
        PowerShell call and cached.

        The old per-tweak `Get-AppxPackage <name>` cost one PowerShell launch
        per row; a debloat list of ninety apps meant ninety process spawns per
        status sweep. Returns None if the enumeration itself failed, which the
        caller must not confuse with "nothing is installed".
        """
        if self._appx_names is not None:
            return self._appx_names
        if self._appx_failed:
            return None
        with self._appx_lock:
            if self._appx_names is not None:
                return self._appx_names
            if self._appx_failed:
                return None
            return self._enumerate_appx()

    def _enumerate_appx(self) -> Optional[FrozenSet[str]]:
        try:
            # Shared core.appx_service enumeration: one query, cached briefly,
            # with the -AllUsers fallback for unelevated runs.
            from core.appx_service import installed_names_or_none
            listed = installed_names_or_none()
            if listed is None:     # refused / failed: unknown, never "none installed"
                self._appx_failed = True
                logger.warning("Get-AppxPackage enumeration could not be completed")
                return None
            names = {line.strip().lower() for line in listed if line.strip()}
            self._appx_names = frozenset(names)
            return self._appx_names
        except Exception as e:
            self._appx_failed = True
            logger.warning("Get-AppxPackage enumeration failed: %s", e)
            return None

    def appx_installed(self, package: str) -> Optional[bool]:
        """Is this package present? `package` may contain `*` wildcards, which
        is how most debloat definitions are written."""
        names = self.appx_packages()
        if names is None:
            return None
        needle = package.strip().lower()
        if not needle:
            return False
        if "*" in needle or "?" in needle:
            return any(fnmatch.fnmatchcase(n, needle) for n in names)
        return needle in names

    def invalidate_appx_cache(self) -> None:
        """Call after removing a package so the next sweep sees the change."""
        self._appx_names = None
        self._appx_failed = False
        # The shared service caches too; a removal must not serve stale names.
        try:
            from core.appx_service import invalidate_cache
            invalidate_cache()
        except Exception:
            logger.debug("Ignored appx_service invalidate failure", exc_info=True)

    # -- applies_to evaluation --------------------------------------------

    def evaluate(self, applies_to: Optional[Dict]) -> Applicability:
        """Match an `applies_to` block from a tweak definition.

        Supported keys (all optional, all AND-ed together):

        | key               | meaning                                       |
        |-------------------|-----------------------------------------------|
        | `min_build`       | build number or alias ("22H2"); inclusive     |
        | `max_build`       | build number or alias; inclusive              |
        | `os`              | "win10" / "win11" / "any"                     |
        | `editions`        | list of EditionID values that DO apply        |
        | `not_editions`    | list of EditionID values that do NOT apply    |
        | `arch`            | list of "AMD64" / "ARM64" / "X86"             |
        | `requires_gpedit` | true -> not applicable on Home/S editions     |
        | `client_only`     | true -> not applicable on Server SKUs         |
        | `server_only`     | true -> not applicable on desktop Windows     |
        """
        if not applies_to:
            return APPLICABLE

        min_b = resolve_build(applies_to.get("min_build"))
        if min_b is not None and self.build < min_b:
            return Applicability(
                False, f"needs build {min_b} or newer — this PC is on {self.build}")

        max_b = resolve_build(applies_to.get("max_build"))
        if max_b is not None and self.build > max_b:
            return Applicability(
                False, f"gone after build {max_b} — this PC is on {self.build}")

        want_os = str(applies_to.get("os", "any")).lower()
        if want_os in ("win10", "windows10") and not self.is_win10:
            return Applicability(False, "Windows 10 only — this PC runs Windows 11")
        if want_os in ("win11", "windows11") and not self.is_win11:
            return Applicability(False, "Windows 11 only — this PC runs Windows 10")

        editions = [e.lower() for e in applies_to.get("editions", [])]
        if editions and self.edition_id.lower() not in editions:
            return Applicability(
                False,
                f"only on {', '.join(applies_to['editions'])} — this PC is "
                f"{self.edition_id or 'an unknown edition'}")

        not_editions = [e.lower() for e in applies_to.get("not_editions", [])]
        if not_editions and self.edition_id.lower() in not_editions:
            return Applicability(False, f"not supported on {self.edition_id}")

        arches = [a.upper() for a in applies_to.get("arch", [])]
        if arches and self.arch not in arches:
            return Applicability(
                False, f"{'/'.join(arches)} only — this PC is {self.arch}")

        if applies_to.get("requires_gpedit") and self.is_home_edition:
            return Applicability(
                False,
                f"needs Group Policy, which the {self.edition_id} edition does "
                "not process — the value would be written but ignored")

        if applies_to.get("client_only") and self.is_server:
            return Applicability(False, "desktop Windows only — this is a Server SKU")

        if applies_to.get("server_only") and not self.is_server:
            return Applicability(False, "Windows Server only")

        return APPLICABLE


def get_os_context() -> OSContext:
    """Process-wide singleton. Cheap after the first call."""
    if OSContext._instance is None:
        OSContext._instance = OSContext()
        logger.info("OS context: %s", OSContext._instance.friendly_name)
    return OSContext._instance


def reset_os_context() -> None:
    """Drop the cache. Tests use this; the app does not need it."""
    OSContext._instance = None
