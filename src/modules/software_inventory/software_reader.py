"""Installed-software reader (Qt-free).  ``SoftwareEntry`` and ``fetch_software``
are re-exported by ``software_module`` under their original import path."""
from __future__ import annotations

import logging
import re
import winreg
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

_GUID = re.compile(r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$")

UNINSTALL_ROOTS = (
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "64-bit"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "32-bit"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "User"),
)


@dataclass
class SoftwareEntry:
    name: str
    version: str
    publisher: str
    install_date: str
    size_mb: str
    type_: str          # "64-bit" | "32-bit" | "User"
    source: str         # "registry"
    uninstall_string: str = ""
    # Added for asset inventory; every one has a default so older callers
    # constructing the first eight fields keep working.
    product_code: str = ""          # MSI product GUID when the key is one
    quiet_uninstall: str = ""
    install_location: str = ""
    registry_key: str = ""          # full path, for "where did this come from"
    system_component: bool = False  # hidden from Programs and Features
    windows_installer: bool = False
    is_update: bool = False         # a patch (ParentKeyName / ReleaseType)

    @property
    def msi_uninstall(self) -> str:
        """The documented MSI removal command, when there is a product code."""
        return f"msiexec /x {self.product_code}" if self.product_code else ""


def _value(key, name: str, default=""):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def _size_text(raw) -> str:
    """EstimatedSize is in KB.  Absent or 0 means the installer never said, so
    it is blank rather than '0.0 MB'."""
    try:
        kb = int(raw)
    except (TypeError, ValueError):
        return ""
    return f"{kb / 1024:.1f} MB" if kb > 0 else ""


def _entry_from_key(sk, subkey_name: str, root: str, type_label: str) -> Optional[SoftwareEntry]:
    name = str(_value(sk, "DisplayName"))
    if not name:
        return None
    release = str(_value(sk, "ReleaseType"))
    return SoftwareEntry(
        name=name,
        version=str(_value(sk, "DisplayVersion")),
        publisher=str(_value(sk, "Publisher")),
        install_date=str(_value(sk, "InstallDate")),
        size_mb=_size_text(_value(sk, "EstimatedSize", 0)),
        type_=type_label,
        source="registry",
        uninstall_string=str(_value(sk, "UninstallString")),
        product_code=subkey_name if _GUID.match(subkey_name) else "",
        quiet_uninstall=str(_value(sk, "QuietUninstallString")),
        install_location=str(_value(sk, "InstallLocation")),
        registry_key=f"{root}\\{subkey_name}",
        system_component=bool(_value(sk, "SystemComponent", 0)),
        windows_installer=bool(_value(sk, "WindowsInstaller", 0)),
        is_update=bool(_value(sk, "ParentKeyName")) or release in ("Security Update", "Update Rollup", "Hotfix"),
    )


def _read_registry_uninstall(hive, key_path: str, type_label: str) -> List[SoftwareEntry]:
    entries: List[SoftwareEntry] = []
    try:
        root = winreg.OpenKey(hive, key_path)
    except OSError as e:
        logger.warning("Cannot open %s: %s", key_path, e)
        return entries
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                sub = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, sub) as sk:
                    entry = _entry_from_key(sk, sub, key_path, type_label)
            except OSError as e:
                logger.debug("skipping unreadable uninstall subkey #%d: %s", i, e)
                continue
            if entry is not None:
                entries.append(entry)
    return entries


def fetch_software_all() -> List[SoftwareEntry]:
    """Every named uninstall entry from all three hives, NOT de-duplicated."""
    found: List[SoftwareEntry] = []
    for hive, path, label in UNINSTALL_ROOTS:
        found += _read_registry_uninstall(hive, path, label)
    return found


def fetch_software() -> List[SoftwareEntry]:
    """De-duplicated by name (the original behaviour other panes rely on)."""
    seen: set = set()
    deduped: List[SoftwareEntry] = []
    for e in fetch_software_all():
        key = e.name.lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    deduped.sort(key=lambda e: e.name.lower())
    return deduped


def fetch_software_inventory() -> List[SoftwareEntry]:
    """Every entry, keeping different versions / architectures of one name (so
    duplicates are visible) and dropping only exact repeats."""
    seen: set = set()
    out: List[SoftwareEntry] = []
    for e in fetch_software_all():
        key = (e.name.lower().strip(), e.version, e.type_)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    out.sort(key=lambda e: e.name.lower())
    return out
