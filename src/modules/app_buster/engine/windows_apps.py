r"""Windows (Store / UWP / MSIX) apps: installed for you, installable, staged.

Qt-free. What answers unelevated was measured on this machine (2026-10-10):

* ``Get-AppxPackage`` (own registrations) -- 154 packages in ~0.3 s.
  ``-AllUsers`` and ``Get-AppxProvisionedPackage`` are refused ("Access is
  denied" / "requires elevation"), so neither is the source of what is
  INSTALLABLE.
* ``HKLM\...\Appx\AppxAllUserStore`` IS readable. ``Applications`` lists the
  64 packages provisioned for the whole PC, ``Staged`` the 4 whose files are
  on disk but registered for nobody, and one ``S-1-5-21-...`` key per user
  names that user's registrations (124 for this account, 37 for the second).
  ``Applications`` carries BUNDLE names (``Microsoft.WindowsCalculator_
  2021.2607.0.0_neutral_~_8wekyb3d8bbwe``) where Get-AppxPackage reports the
  main package (``..._11.2607.0.0_x64__8wekyb3d8bbwe``), so the two are
  compared by PACKAGE FAMILY, never by full name -- by full name 46 apps
  looked "not installed for you" that plainly were.
* Each package's ``AppxManifest.xml`` is readable in place: display name,
  description, logo, and whether any of its apps appears in Start
  (``AppListEntry="none"`` on every app, or no apps at all, is a HIDDEN app).
  Names given as ``ms-resource:`` resolve through ``SHLoadIndirectString``
  (70 of 75 here; the 5 that do not are nameless system stubs and keep their
  package name).
* Installed date: the creation time of the per-user data folder
  ``%LocalAppData%\Packages\<family>`` -- made on first install for this
  account and kept across updates. The package folder's own date moves with
  every update, so it is only the fallback.
"""
from __future__ import annotations

import ctypes
import glob
import json
import logging
import os
import re
import subprocess
import winreg
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple

from core.windows_utils import program_files, system_root

from . import model as m

logger = logging.getLogger(__name__)

STORE_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore"

_QUERY = ("Get-AppxPackage | Select-Object Name, PackageFullName, PackageFamilyName, "
          "Publisher, PublisherId, Version, InstallLocation, IsFramework, "
          "IsResourcePackage, NonRemovable, "
          "@{Name='Architecture';Expression={[string]$_.Architecture}}, "
          "@{Name='SignatureKind';Expression={[string]$_.SignatureKind}} "
          "| ConvertTo-Json -Compress")

#: Package families whose publisher id names the publisher outright.
KNOWN_PUBLISHER_IDS = {"8wekyb3d8bbwe": "Microsoft Corporation",
                       "cw5n1h2txyewy": "Microsoft Windows"}

#: Windows' own package-family suffix: 13 characters of Crockford base32.
_FAMILY_RE = re.compile(r"^(?P<name>[^_]+)_(?P<pid>[0-9a-hjkmnp-tv-z]{13})$")


class ReadRefused(Exception):
    """The package list could not be read. Never an empty list."""


# ---- names ---------------------------------------------------------------------------------

def split_full_name(full_name: str) -> Tuple[str, str, str, str]:
    """(name, version, architecture, publisher id) from a package full name.

    ``Name_Version_Arch_ResourceId_PublisherId``; ResourceId may be empty or
    ``~`` (a bundle)."""
    parts = full_name.split("_")
    if len(parts) < 5:
        return full_name, "", "", ""
    return parts[0], parts[1], parts[2], parts[-1]


def family_of(full_name: str) -> str:
    name, _v, _a, pid = split_full_name(full_name)
    return f"{name}_{pid}" if pid else ""


def is_family_name(text: str) -> bool:
    """A real package family name -- ``Name_<13 base32>`` -- as opposed to
    other folders that live under ``%LocalAppData%\\Packages``. Chrome keeps
    its sandbox AppContainer profiles there (``cr.sb.cdm0B41...``), and so
    does IE (``windows_ie_ac_001``); neither is a package, so neither may
    ever be called an orphan of one."""
    return bool(_FAMILY_RE.match(text.lower()))


_CATALOG_NAMES: Optional[Dict[str, str]] = None


def catalog_name(package_name: str) -> str:
    """The friendly name the debloat catalog gives a package ('Media Player'
    for Microsoft.ZuneMusic). Used for packages whose manifest cannot be read
    -- provisioned bundles are not registered for this account."""
    global _CATALOG_NAMES
    if _CATALOG_NAMES is None:
        _CATALOG_NAMES = {}
        path = os.path.join(os.path.dirname(__file__), "..", "..", "tweaks", "definitions", "debloat.json")
        try:
            with open(path, encoding="utf-8") as f:
                for entry in json.load(f):
                    if entry.get("package") and entry.get("name"):
                        _CATALOG_NAMES[entry["package"].lower()] = entry["name"]
        except (OSError, ValueError) as e:
            logger.warning("debloat catalog unreadable for app names: %s", e)
    return _CATALOG_NAMES.get(package_name.lower(), "")


def pretty_package_name(name: str) -> str:
    """'Microsoft.BingWeather' -> 'Bing Weather' when nothing better exists."""
    tail = name.split(".")[-1] if "." in name else name
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail)
    return spaced or name


def _load_indirect(text: str) -> Optional[str]:
    buf = ctypes.create_unicode_buffer(1024)
    try:
        hr = ctypes.windll.shlwapi.SHLoadIndirectString(text, buf, len(buf), None)
    except OSError as e:
        logger.debug("SHLoadIndirectString unavailable: %s", e)
        return None
    return buf.value if hr == 0 and buf.value else None


def resolve_resource(value: str, package_name: str, full_name: str) -> Optional[str]:
    """A manifest string, with ``ms-resource:`` references resolved."""
    if not value:
        return None
    if not value.startswith("ms-resource:"):
        return value
    rest = value[len("ms-resource:"):]
    if rest.startswith("//"):
        uris = ["ms-resource:" + rest]
    elif rest.startswith("/"):
        uris = [f"ms-resource://{package_name}{rest}"]
    else:
        uris = [f"ms-resource://{package_name}/resources/{rest}",
                f"ms-resource://{package_name}/{rest}"]
    for uri in uris:
        text = _load_indirect(f"@{{{full_name}?{uri}}}")
        if text:
            return text
    return None


# ---- manifest ------------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node, name: str):
    return next((c for c in node if _local(c.tag) == name), None) if node is not None else None


def read_manifest(location: str) -> Dict[str, object]:
    """{display_name, description, logo, hidden} from AppxManifest.xml;
    {} when it cannot be read (a refusal is not a hidden app)."""
    path = os.path.join(location or "", "AppxManifest.xml")
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        logger.debug("manifest %s unreadable: %s", path, e)
        return {}
    props = _child(root, "Properties")
    out: Dict[str, object] = {}
    for key, tag in (("display_name", "DisplayName"), ("description", "Description"),
                     ("logo", "Logo"), ("publisher", "PublisherDisplayName")):
        node = _child(props, tag)
        out[key] = (node.text or "").strip() if node is not None else ""
    apps = _child(root, "Applications")
    entries = []
    for app in (apps if apps is not None else []):
        visual = next((c for c in app if _local(c.tag) == "VisualElements"), None)
        entry = (visual.get("AppListEntry") or "").lower() if visual is not None else ""
        entries.append(entry != "none")
    out["hidden"] = not any(entries)
    return out


def logo_file(location: str, logo: str) -> str:
    """The real PNG behind a manifest logo. Manifests name ``Assets\\Logo.png``
    and ship ``Logo.scale-100.png``, ``Logo.scale-200.png`` ...; prefer the
    smallest scale at or above 100 that exists."""
    if not location or not logo:
        return ""
    exact = os.path.join(location, logo)
    if os.path.isfile(exact):
        return exact
    stem, ext = os.path.splitext(exact)
    found = glob.glob(glob.escape(stem) + ".*" + ext)
    if not found:
        return ""

    def scale(path: str) -> int:
        hit = re.search(r"scale-(\d+)", path)
        return int(hit.group(1)) if hit else 999

    ranked = sorted(found, key=lambda p: (scale(p) < 100, scale(p)))
    return ranked[0]


# ---- registry: who has what ----------------------------------------------------------------

def _subkeys(path: str) -> Optional[List[str]]:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
    except OSError as e:
        logger.debug("cannot read %s: %s", path, e)
        return None


def _key_time(path: str) -> Optional[datetime]:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            stamp = winreg.QueryInfoKey(key)[2]
    except OSError as e:
        logger.debug("cannot read %s: %s", path, e)
        return None
    return filetime_to_datetime(stamp)


def filetime_to_datetime(stamp: int) -> Optional[datetime]:
    if not stamp:
        return None
    try:
        return datetime.fromtimestamp((stamp - 116444736000000000) / 10_000_000)
    except (OverflowError, OSError, ValueError):
        return None


class StoreView:
    """What the all-user store says: provisioned, staged, and per-user families."""

    def __init__(self, provisioned: Dict[str, str], staged: Set[str],
                 per_user: Dict[str, Set[str]], provisioned_at: Dict[str, Optional[datetime]],
                 readable: bool) -> None:
        self.provisioned = provisioned          # family -> bundle full name
        self.staged = staged                    # families
        self.per_user = per_user                # sid -> families
        self.provisioned_at = provisioned_at
        self.readable = readable

    def users_with(self, family: str) -> int:
        return sum(1 for fams in self.per_user.values() if family in fams)


def read_store() -> StoreView:
    apps = _subkeys(STORE_KEY + r"\Applications")
    staged = _subkeys(STORE_KEY + r"\Staged") or []
    provisioned: Dict[str, str] = {}
    when: Dict[str, Optional[datetime]] = {}
    for full in apps or []:
        fam = family_of(full)
        if fam:
            provisioned[fam.lower()] = full
            when[fam.lower()] = _key_time(STORE_KEY + "\\Applications\\" + full)
    per_user: Dict[str, Set[str]] = {}
    for sid in _subkeys(STORE_KEY) or []:
        if sid.startswith("S-1-5-21-"):
            per_user[sid] = {family_of(f).lower() for f in (_subkeys(STORE_KEY + "\\" + sid) or [])}
    staged_fams = {s.lower() for s in staged}
    return StoreView(provisioned, staged_fams, per_user, when, readable=apps is not None)


# ---- the package list ----------------------------------------------------------------------

def query_packages(timeout: float = 90) -> List[dict]:
    """Get-AppxPackage for this account. Raises ReadRefused instead of
    returning [] -- an unread list is not an empty machine."""
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", _QUERY],
                              capture_output=True, text=True, timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReadRefused(f"Get-AppxPackage did not answer: {e}") from e
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ReadRefused((proc.stderr or "Get-AppxPackage returned nothing").strip()[:300])
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ReadRefused(f"Get-AppxPackage output was not JSON: {e}") from e
    return data if isinstance(data, list) else [data]


def data_folder(family: str) -> str:
    base = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(base, "Packages", family) if base and family else ""


def _created(path: str) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(os.stat(path).st_ctime)
    except OSError:
        return None


def _is_system(pkg: dict) -> bool:
    location = (pkg.get("InstallLocation") or "").lower()
    windir = system_root().lower()
    return bool(pkg.get("NonRemovable")) or location.startswith(windir + "\\systemapps")


def record_from_package(pkg: dict, store: StoreView) -> Optional[m.AppRecord]:
    """One installed package as a row; None for resource packages."""
    if pkg.get("IsResourcePackage"):
        return None
    name = pkg.get("Name") or ""
    full = pkg.get("PackageFullName") or ""
    family = pkg.get("PackageFamilyName") or family_of(full)
    location = pkg.get("InstallLocation") or ""
    manifest = read_manifest(location)
    display = resolve_resource(str(manifest.get("display_name") or ""), name, full)
    description = resolve_resource(str(manifest.get("description") or ""), name, full) or ""
    if pkg.get("IsFramework"):
        kind = m.FRAMEWORK
    elif _is_system(pkg):
        kind = m.SYSTEM
    else:
        kind = m.WINDOWS
    removable = kind == m.WINDOWS
    data_dir = data_folder(family)
    fam = family.lower()
    available = (m.FOR_PC if fam in store.provisioned
                 else m.FOR_SEVERAL if store.users_with(fam) > 1 else m.FOR_YOU)
    # The manifest's PublisherDisplayName is what Settings shows; the
    # certificate subject can be a bare GUID (ChatGPT's is CN=50BDFD77-...).
    publisher = resolve_resource(str(manifest.get("publisher") or ""), name, full) or         _publisher_name(pkg.get("Publisher") or "", pkg.get("PublisherId") or "")
    return m.AppRecord(
        # Frameworks install side by side per architecture under ONE family
        # (15 x86/x64 pairs here: VCLibs, UI.Xaml, WindowsAppRuntime ...).
        key="appx:" + fam + ("|" + str(pkg.get("Architecture") or "").lower() if kind == m.FRAMEWORK else ""),
        name=display or pretty_package_name(name), type=kind,
        status=m.INSTALLED if removable else m.UNREMOVABLE,
        publisher=publisher, version=str(pkg.get("Version") or ""),
        architecture=str(pkg.get("Architecture") or ""), description=description,
        installed=(_created(data_dir) if data_dir and os.path.isdir(data_dir) else None)
        or _created(location),
        available=available, hidden=bool(manifest.get("hidden")) if manifest else False,
        removable=removable, install_location=location,
        icon_path=logo_file(location, str(manifest.get("logo") or "")),
        package_name=name, full_name=full, family=family,
        publisher_id=pkg.get("PublisherId") or "", signature=str(pkg.get("SignatureKind") or ""),
        extra={"data_folder": data_dir} if data_dir and os.path.isdir(data_dir) else {},
    )


def _publisher_name(publisher: str, publisher_id: str) -> str:
    """'CN=Microsoft Corporation, O=Microsoft Corporation, ...' -> the O= or CN=
    value. Quoted values keep their commas (Gigabyte's is
    '"GIGA-BYTE TECHNOLOGY CO., LTD."')."""
    fields = dict(re.findall(r'(\w+)=("[^"]*"|[^,]*)', publisher))
    for key in ("O", "CN"):
        value = fields.get(key, "").strip().strip('"').strip()
        if value and not re.fullmatch(r"[0-9A-F-]{36}", value, re.I):
            return value
    return KNOWN_PUBLISHER_IDS.get(publisher_id, publisher)


def installable_records(store: StoreView, mine: Set[str]) -> List[m.AppRecord]:
    """Provisioned or staged packages that are not registered for this
    account: the ones "Install" can add back without a download."""
    out: List[m.AppRecord] = []
    for fam, full in sorted(store.provisioned.items()):
        if fam in mine:
            continue
        out.append(_unregistered(full, m.INSTALLABLE, store.provisioned_at.get(fam)))
    for fam in sorted(store.staged - mine - set(store.provisioned)):
        name, pid = fam.rsplit("_", 1)
        out.append(_unregistered(f"{name}__{pid}", m.STAGED, None, family=fam))
    return out


def _unregistered(full: str, status: str, when: Optional[datetime], family: str = "") -> m.AppRecord:
    name, version, arch, pid = split_full_name(full)
    family = family or family_of(full)
    location = os.path.join(program_files(), "WindowsApps", full)
    return m.AppRecord(
        key="appx:" + family.lower(), name=catalog_name(name) or pretty_package_name(name),
        type=m.WINDOWS,
        status=status, publisher=KNOWN_PUBLISHER_IDS.get(pid, ""), version=version,
        architecture=arch, installed=when, available=m.FOR_PC if status == m.INSTALLABLE else "",
        hidden=True, removable=False, install_location=location if os.path.isdir(location) else "",
        package_name=name, full_name=full if status == m.INSTALLABLE else "", family=family,
        publisher_id=pid,
        description="On this PC but not installed for your account." if status == m.INSTALLABLE
        else "Its files are staged on this PC, but it is registered for no account.",
    )


def read_windows_apps() -> Tuple[List[m.AppRecord], StoreView]:
    """(rows for every installed + installable package, the store view)."""
    store = read_store()
    rows: List[m.AppRecord] = []
    mine: Set[str] = set()
    for pkg in query_packages():
        rec = record_from_package(pkg, store)
        if rec is not None:
            rows.append(rec)
            mine.add(rec.family.lower())
    rows += installable_records(store, mine)
    return rows, store


def families_installed(rows: Iterable[m.AppRecord]) -> Set[str]:
    return {r.family.lower() for r in rows if r.family and r.status != m.INSTALLABLE
            and r.status != m.STAGED}
