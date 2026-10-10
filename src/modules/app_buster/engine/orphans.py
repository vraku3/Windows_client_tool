r"""Orphaned Windows-app data: ``%LocalAppData%\Packages\<family>`` folders whose
package is not installed for this account.

Qt-free. Only real package-family folder names count (see
``windows_apps.is_family_name``): measured here, all 9 folders without a
matching package were Chrome's sandbox profiles (``cr.sb.*``) and IE's
AppContainer (``windows_ie_ac_001``) -- live, in use, and not orphans of
anything. A family that is still provisioned or staged on the PC is not an
orphan either: its data comes back into use the moment it is installed.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Iterable, List, Optional, Set

from . import model as m
from .windows_apps import KNOWN_PUBLISHER_IDS, StoreView, is_family_name, pretty_package_name

logger = logging.getLogger(__name__)

#: Family-name prefixes whose vendor and purpose are known; anything else is
#: "Unknown orphaned application entry" with Unknown confidence.
KNOWN_PREFIXES = {
    "microsoft.bing": ("Microsoft", "Bing content app (news, weather, finance ...)"),
    "microsoft.xbox": ("Microsoft", "Xbox / gaming component"),
    "microsoft.zune": ("Microsoft", "Media player"),
    "microsoft.office": ("Microsoft", "Office companion"),
    "microsoft.windows": ("Microsoft", "Windows inbox app"),
    "microsoft.": ("Microsoft", "Microsoft app"),
    "microsoftcorporationii.": ("Microsoft", "Microsoft app"),
    "microsoftwindows.": ("Microsoft", "Windows component"),
    "ad2f1837.": ("HP", "HP OEM utility"),
    "dellinc.": ("Dell", "Dell OEM utility"),
    "e046963f.": ("Lenovo", "Lenovo OEM utility"),
    "spotifyab.": ("Spotify", "Music streaming"),
    "king.com.": ("King", "Casual game"),
    "5319275a.": ("WhatsApp", "Messaging"),
}


def describe_family(family: str) -> tuple:
    """(readable name, vendor, purpose, confidence)."""
    name, _, pid = family.rpartition("_")
    low = name.lower()
    vendor, purpose = next(((v, p) for prefix, (v, p) in KNOWN_PREFIXES.items()
                            if low.startswith(prefix)), ("", ""))
    if not vendor and pid in KNOWN_PUBLISHER_IDS:
        vendor, purpose = KNOWN_PUBLISHER_IDS[pid], "Windows component"
    confidence = "High" if vendor else "Unknown"
    readable = pretty_package_name(name) if vendor else "Unknown orphaned application entry"
    return readable, vendor, purpose, confidence


def _created(path: str) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(os.stat(path).st_mtime)
    except OSError:
        return None


def find_orphaned_data(installed_families: Set[str], store: StoreView,
                       packages_dir: Optional[str] = None,
                       names: Optional[Iterable[str]] = None) -> List[m.AppRecord]:
    base = packages_dir or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages")
    try:
        folders = list(names) if names is not None else os.listdir(base)
    except OSError as e:
        logger.warning("cannot list %s: %s", base, e)
        return []
    out: List[m.AppRecord] = []
    for folder in folders:
        fam = folder.lower()
        if not is_family_name(fam) or fam in installed_families:
            continue
        if fam in store.provisioned or fam in store.staged:
            continue
        path = os.path.join(base, folder)
        readable, vendor, purpose, confidence = describe_family(folder)
        out.append(m.AppRecord(
            key="orphan:" + fam, name=readable, type=m.ORPHANED, family=folder,
            publisher=vendor, installed=_created(path), available=m.FOR_YOU,
            leftover=m.LEFTOVER_DATA_FOLDER, leftover_paths=(path,), install_location=path,
            reason="App data left behind: no package of this family is installed for your account.",
            purpose=purpose or "App settings, caches and saved state", vendor=vendor,
            confidence=confidence, recommendation=m.OPTIONAL,
            extra={"original identifier": folder},
        ))
    return out
