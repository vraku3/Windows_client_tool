r"""Desktop (Win32 / MSI) apps, and the ones whose uninstall metadata is broken.

Qt-free. The installed list is the same Uninstall-key read the Software pane
uses (``software_reader.fetch_software_all``); this module adds what App
Buster needs on top: an icon, a Modify command, a size when the installer
never wrote one, and the ORPHANED / DEFECT verdicts.

The verdicts are deliberately narrow, because a cleanup of either deletes
things no uninstaller will vouch for:

* **Defect** -- the app is there but cannot uninstall itself: an MSI product
  whose cached installer (``InstallProperties\LocalPackage`` under
  ``C:\Windows\Installer``) is gone, or a non-MSI entry whose uninstaller
  program is gone while the install folder (or its icon's program) still
  exists. Measured here: 75 MSI products, 0 missing a cached installer; the
  WiX/Burn bundles whose Package Cache copies were deleted are the second
  kind.
* **Orphaned** -- nothing is installed behind the entry: the uninstaller is
  gone AND the install folder is gone (or never recorded and the icon's
  program is gone too), or a Windows Installer product registered under
  ``HKLM\SOFTWARE\Classes\Installer\Products`` with no Uninstall entry, no
  cached installer and no install folder.

An MSI product with no Uninstall entry but a cached installer present is
neither: that is how suites hide their components (the three Bitdefender
products here have no Uninstall entry of their own), so it is left alone.
"""
from __future__ import annotations

import logging
import os
import re
import winreg
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from modules.software_inventory.software_analysis import (
    parse_install_date, uninstaller_target_status,
)
from modules.software_inventory.software_reader import SoftwareEntry, fetch_software_all
from modules.startup_manager.persistence import resolve_command

from . import model as m

logger = logging.getLogger(__name__)

HIVE_NAMES = {"User": "HKCU", "64-bit": "HKLM", "32-bit": "HKLM"}
HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
USERDATA = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\UserData"
CLASSES_PRODUCTS = r"SOFTWARE\Classes\Installer\Products"


def squish_guid(code: str) -> str:
    """'{12345678-ABCD-...}' -> Windows Installer's packed form (the key name
    under Installer\\Products): each group reversed, the last two by byte."""
    hexes = re.sub(r"[^0-9A-Fa-f]", "", code).upper()
    if len(hexes) != 32:
        return ""
    first = hexes[0:8][::-1] + hexes[8:12][::-1] + hexes[12:16][::-1]
    rest = "".join(hexes[i:i + 2][::-1] for i in range(16, 32, 2))
    return first + rest


def unsquish_guid(packed: str) -> str:
    if len(packed) != 32 or not re.fullmatch(r"[0-9A-Fa-f]{32}", packed):
        return ""
    p = packed.upper()
    groups = [p[0:8][::-1], p[8:12][::-1], p[12:16][::-1],
              "".join(p[i:i + 2][::-1] for i in range(16, 20, 2)),
              "".join(p[i:i + 2][::-1] for i in range(20, 32, 2))]
    return "{" + "-".join(groups) + "}"


def _open(hive, path):
    return winreg.OpenKey(hive, path)


def _value(hive, path: str, name: str):
    try:
        with _open(hive, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _subkeys(hive, path: str) -> Optional[List[str]]:
    try:
        with _open(hive, path) as key:
            return [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
    except OSError as e:
        logger.debug("cannot read %s: %s", path, e)
        return None


def full_key(entry: SoftwareEntry) -> str:
    """'HKLM\\SOFTWARE\\...\\Uninstall\\<subkey>' -- the reader drops the hive."""
    return f"{HIVE_NAMES.get(entry.type_, 'HKLM')}\\{entry.registry_key}"


def split_key(key: str) -> Tuple[int, str]:
    hive, _, path = key.partition("\\")
    return HIVES[hive], path


def _icon_path(display_icon: str) -> str:
    """DisplayIcon is 'path[,index]', sometimes quoted."""
    text = os.path.expandvars((display_icon or "").strip().strip('"'))
    text = re.sub(r",\s*-?\d+$", "", text).strip().strip('"')
    return text if text and os.path.isfile(text) else ""


def _exists(path: str) -> Optional[bool]:
    if not path:
        return None
    try:
        os.stat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.debug("could not stat %s: %s", path, e)
        return None


def msi_local_packages() -> Dict[str, Tuple[str, str, str]]:
    """{packed product code: (LocalPackage, DisplayName, InstallLocation)} over
    every per-user and per-machine Windows Installer registration."""
    out: Dict[str, Tuple[str, str, str]] = {}
    hklm = winreg.HKEY_LOCAL_MACHINE
    for sid in _subkeys(hklm, USERDATA) or []:
        for packed in _subkeys(hklm, f"{USERDATA}\\{sid}\\Products") or []:
            props = f"{USERDATA}\\{sid}\\Products\\{packed}\\InstallProperties"
            out[packed.upper()] = (str(_value(hklm, props, "LocalPackage") or ""),
                                   str(_value(hklm, props, "DisplayName") or ""),
                                   str(_value(hklm, props, "InstallLocation") or ""))
    return out


def judge(entry: SoftwareEntry, local_package: str = "",
          icon_exe: str = "", bundle: bool = False) -> Tuple[str, str]:
    """(type, reason) for one Uninstall entry: DESKTOP / DEFECT / ORPHANED.

    A WiX/Burn BUNDLE (``BundleUpgradeCode`` / ``BundleCachePath``) records no
    install folder of its own: its files belong to the MSI packages it
    installed, which keep their own Uninstall entries. So a bundle whose
    cached copy under ``Package Cache`` is gone is DEFECT, never "its files
    are gone" -- measured here, 16 such bundles (VC++ 2013/v14 redists, the
    ENE/Corsair RGB stacks, the ADK) whose components are all still present."""
    location = (entry.install_location or "").strip().strip('"')
    location_there = _exists(location)
    if entry.windows_installer and entry.product_code and local_package:
        if _exists(local_package) is False:
            if location_there is False:
                return m.ORPHANED, ("Its cached Windows Installer package and its install folder "
                                    "are both gone.")
            return m.DEFECT, (f"Its cached Windows Installer package ({local_package}) is missing, "
                              "so msiexec cannot uninstall or repair it.")
    if uninstaller_target_status(entry) is not True:
        return m.DESKTOP, ""
    exe, _args = resolve_command(entry.uninstall_string)
    if bundle:
        return m.DEFECT, (f"Its installer bundle's cached copy ({exe}) is gone, so it cannot "
                          "uninstall or repair itself. The components it installed are separate "
                          "entries and are not touched by cleaning this one up.")
    if location_there is True or (not location and icon_exe):
        return m.DEFECT, (f"Its uninstaller ({exe}) no longer exists, but the program's files do. "
                          "Uninstall cannot run; the entry has to be cleaned up directly.")
    return m.ORPHANED, (f"Its uninstaller ({exe}) and its program files are gone; only the "
                        "Uninstall entry is left.")


def record_from_entry(entry: SoftwareEntry, msi: Dict[str, Tuple[str, str, str]]) -> m.AppRecord:
    key = full_key(entry)
    hive, path = split_key(key)
    icon = _icon_path(str(_value(hive, path, "DisplayIcon") or ""))
    modify = "" if _value(hive, path, "NoModify") == 1 else str(_value(hive, path, "ModifyPath") or "")
    local_package = msi.get(squish_guid(entry.product_code), ("", "", ""))[0] if entry.product_code else ""
    bundle = any(_value(hive, path, v) for v in ("BundleUpgradeCode", "BundleCachePath",
                                                    "BundleProviderKey"))
    kind, reason = judge(entry, local_package, icon, bundle)
    try:
        size_kb = int(_value(hive, path, "EstimatedSize") or 0)
    except (TypeError, ValueError):
        size_kb = 0
    installed = parse_install_date(entry.install_date.replace("-", ""))
    leftovers: Tuple[str, ...] = (key,)
    if kind != m.DESKTOP and entry.product_code:
        leftovers += msi_registration_keys(entry.product_code)
    location = (entry.install_location or "").strip().strip('"')
    if kind == m.DEFECT and location and os.path.isdir(location):
        leftovers += (location,)
    return m.AppRecord(
        key="arp:" + key.lower(), name=entry.name, type=kind,
        publisher=entry.publisher, version=entry.version,
        architecture={"64-bit": "x64", "32-bit": "x86"}.get(entry.type_, ""),
        installed=datetime.combine(installed, datetime.min.time()) if installed else None,
        files_bytes=size_kb * 1024 if size_kb > 0 else None,
        available=m.FOR_YOU if entry.type_ == "User" else m.FOR_PC,
        hidden=entry.system_component, removable=True, install_location=location,
        icon_path=icon, registry_key=key, product_code=entry.product_code,
        uninstall_string=entry.uninstall_string, modify_path=modify,
        windows_installer=entry.windows_installer,
        leftover=m.LEFTOVER_UNINSTALL_ENTRY if kind != m.DESKTOP else "",
        leftover_paths=leftovers if kind != m.DESKTOP else (),
        reason=reason, recommendation=m.OPTIONAL if kind != m.DESKTOP else m.KEEP,
        confidence="High" if kind != m.DESKTOP else "",
        vendor=entry.publisher if kind != m.DESKTOP else "",
        purpose=("Installer bundle registration" if bundle else "Leftover program registration")
        if kind != m.DESKTOP else "",
        extra={"bundle": "yes"} if bundle else {},
    )


def msi_registration_keys(product_code: str) -> Tuple[str, ...]:
    """Every Windows Installer key that registers this product, as it exists now."""
    packed = squish_guid(product_code)
    if not packed:
        return ()
    hklm = winreg.HKEY_LOCAL_MACHINE
    keys = []
    for path in (f"{CLASSES_PRODUCTS}\\{packed}", f"SOFTWARE\\Classes\\Installer\\Features\\{packed}"):
        if _subkeys(hklm, path) is not None:
            keys.append("HKLM\\" + path)
    for sid in _subkeys(hklm, USERDATA) or []:
        path = f"{USERDATA}\\{sid}\\Products\\{packed}"
        if _subkeys(hklm, path) is not None:
            keys.append("HKLM\\" + path)
    return tuple(keys)


def ghost_registrations(arp_codes: set, msi: Dict[str, Tuple[str, str, str]]) -> List[m.AppRecord]:
    """Windows Installer products with no Uninstall entry, no cached installer
    and no install folder: registered, and nothing behind it."""
    out: List[m.AppRecord] = []
    for packed in _subkeys(winreg.HKEY_LOCAL_MACHINE, CLASSES_PRODUCTS) or []:
        code = unsquish_guid(packed)
        if not code or code.upper() in arp_codes:
            continue
        local_package, display, location = msi.get(packed.upper(), ("", "", ""))
        if local_package and _exists(local_package) is not False:
            continue        # a suite's hidden component: installed, and repairable
        if location and _exists(location) is not False:
            continue
        name = str(_value(winreg.HKEY_LOCAL_MACHINE, f"{CLASSES_PRODUCTS}\\{packed}", "ProductName")
                   or display or "")
        out.append(m.AppRecord(
            key="msi:" + code.lower(), name=name or "Unknown orphaned application entry",
            type=m.ORPHANED, product_code=code, windows_installer=True, hidden=False,
            leftover=m.LEFTOVER_MSI_REGISTRATION, leftover_paths=msi_registration_keys(code),
            reason="A Windows Installer product is registered, but there is no Uninstall entry, "
                   "no cached installer and no program folder behind it.",
            purpose="Ghost installer registration", recommendation=m.OPTIONAL,
            confidence="Medium" if name else "Unknown", available=m.FOR_PC,
        ))
    return out


def read_desktop_apps() -> List[m.AppRecord]:
    msi = msi_local_packages()
    seen = set()
    rows: List[m.AppRecord] = []
    codes = set()
    for entry in fetch_software_all():
        if entry.is_update:
            continue
        if entry.product_code:
            codes.add(entry.product_code.upper())
        ident = (entry.name.lower(), entry.version, entry.type_)
        if ident in seen:
            continue
        seen.add(ident)
        rows.append(record_from_entry(entry, msi))
    rows += ghost_registrations(codes, msi)
    return rows
