"""The "About" text for every row: what the app is, what it does, and what
removing it costs -- or why it cannot be removed. Qt-free.

A curated entry (`app_info_data`) wins. Anything else gets a description
composed ONLY from facts the scan read (type, publisher, install folder,
manifest description, who it is installed for), never a guess at what an
unknown program does -- the wording says so when that is all there is.
"""
from __future__ import annotations

import re
from typing import Optional

from . import model as m
from .app_info_data import DESKTOP, PACKAGE_PREFIXES, PACKAGES, About

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def curated(rec: m.AppRecord) -> Optional[About]:
    """The hand-written entry for this row, or None."""
    if rec.type == m.DESKTOP:
        from .autodesk_info import ENTRIES, trusted
        if any(re.search(pattern, rec.name or "", re.I) for pattern, *_ in ENTRIES) and not trusted(rec.publisher, rec.registry_key):
            return None
        return next((a for pattern, a in DESKTOP if pattern.search(rec.name or "")), None)
    pkg = (rec.package_name or "").lower()
    if not pkg:
        return None
    hit = PACKAGES.get(pkg)
    if hit is not None:
        return hit
    return next((a for prefix, a in PACKAGE_PREFIXES if pkg.startswith(prefix)), None)


def _publisher(rec: m.AppRecord) -> str:
    return f" published by {rec.publisher}" if rec.publisher else " with no publisher recorded"


def _manifest(rec: m.AppRecord) -> str:
    desc = (rec.description or "").strip()
    if not desc or desc.lower() == (rec.name or "").lower():
        return ""
    return f" Its own description: \u201c{desc}\u201d."


def _windows_app(rec: m.AppRecord) -> About:
    where = {m.FOR_YOU: "your account", m.FOR_SEVERAL: "several accounts on this PC",
             m.FOR_PC: "every account on this PC"}.get(rec.available, "your account")
    if rec.status == m.INSTALLABLE:
        does = ("It is provisioned on this PC -- Windows would install it for a new account -- "
                "but it is not installed for you." + _manifest(rec))
    elif rec.status == m.STAGED:
        does = "Its files are staged on this PC but it is registered for no account." + _manifest(rec)
    else:
        does = f"It is installed for {where}." + _manifest(rec)
    return About(f"A Windows (Store / MSIX) app{_publisher(rec)}.", does,
                 "Removing it deletes the app and its data folder in %LOCALAPPDATA%\\Packages. "
                 "Whether it can be put back depends on whether the publisher still lists it in "
                 "the Microsoft Store.")


def _desktop_app(rec: m.AppRecord) -> About:
    where = f" into {rec.install_location}" if rec.install_location else ""
    when = f" on {rec.installed:%Y-%m-%d}" if rec.installed else ""
    how = ("Windows Installer (MSI) package" if rec.windows_installer
           else "program with its own uninstaller")
    hidden = (" It is marked as a system component, so Settings > Apps hides it -- usually a "
              "part installed by another program." if rec.hidden else "")
    return About(f"A desktop {how}{_publisher(rec)}.",
                 f"Installed{when}{where}.{hidden} This entry is not in App Buster's catalog, "
                 "so there is no curated description of what it does.",
                 "Uninstalling runs the program's own uninstaller. Settings and data it keeps in "
                 "your profile (AppData) usually remain. If you do not recognise it, look up the "
                 "publisher before removing it -- a driver utility or a component another "
                 "program installed is easy to mistake for clutter.")


def _system_app(rec: m.AppRecord) -> About:
    return About(f"Part of Windows{_publisher(rec)}.",
                 ("A component of the Windows shell or one of its built-in experiences."
                  + _manifest(rec)),
                 "It is marked NonRemovable or lives under %SystemRoot%\\SystemApps, so Windows "
                 "refuses to remove it. Forcing it out breaks the feature it backs and the next "
                 "feature update reinstalls it.")


def _framework(rec: m.AppRecord) -> About:
    return About(f"A framework (shared runtime package){_publisher(rec)}.",
                 ("Store apps declare it as a dependency and Windows loads it into them; "
                  "x86 and x64 copies coexist for 32- and 64-bit apps." + _manifest(rec)),
                 "Windows keeps a framework while any installed app needs it and removes it by "
                 "itself after the last one goes, so it is never removed by hand.")


def _leftover(rec: m.AppRecord) -> About:
    what = ("A leftover: nothing is installed behind it." if rec.type == m.ORPHANED
            else "An installed app whose uninstall information is broken.")
    purpose = f" Likely purpose: {rec.purpose}." if rec.purpose else ""
    vendor = f" Presumed vendor: {rec.vendor}." if rec.vendor else ""
    return About(what, (rec.reason or "") + purpose + vendor,
                 "Cleaning it up removes only the leftover folders and registry entries listed "
                 "in Properties; each registry key is exported to a backup first.")


def composed(rec: m.AppRecord) -> About:
    """A description built from what the scan read about this row."""
    if rec.type in (m.ORPHANED, m.DEFECT):
        return _leftover(rec)
    if rec.type == m.SYSTEM:
        return _system_app(rec)
    if rec.type == m.FRAMEWORK:
        return _framework(rec)
    if rec.type == m.DESKTOP:
        return _desktop_app(rec)
    return _windows_app(rec)


def _unreadable_name(rec: m.AppRecord) -> bool:
    name = (rec.name or "").strip()
    return (not name or bool(_GUID.match(name)) or "__" in name
            or name.lower() == (rec.package_name or "").lower() and "." not in name)


def about(rec: m.AppRecord) -> About:
    hit = curated(rec)
    return hit if hit is not None else composed(rec)


def apply(rows) -> None:
    """Attach `about` to every row, and give an unreadable name a readable one."""
    for rec in rows:
        info = about(rec)
        rec.about = (info.what, info.does, info.remove)
        rec.extra["about_curated"] = "yes" if curated(rec) is not None else "no"
        if info.name and _unreadable_name(rec):
            rec.extra.setdefault("original identifier", rec.name)
            rec.name = info.name
    _disambiguate(rows)


def _disambiguate(rows) -> None:
    """Several Windows parts share one display name ("Windows Feature Experience
    Pack" is nine packages here). Add the part of the package name that tells
    them apart, so the list is not nine identical rows."""
    by_name = {}
    for rec in rows:
        if rec.type == m.SYSTEM and rec.package_name:
            by_name.setdefault(rec.name, []).append(rec)
    for name, group in by_name.items():
        if len({r.package_name.lower() for r in group}) < 2:
            continue
        for rec in group:
            tail = rec.package_name.split(".", 1)[1] if "." in rec.package_name else rec.package_name
            rec.name = f"{name} ({tail})"


def about_text(rec: m.AppRecord) -> str:
    """The three paragraphs as plain text (Properties, clipboard, tooltip)."""
    if not rec.about:
        return ""
    what, does, remove = rec.about
    label = ("Why it cannot be removed" if rec.type in (m.SYSTEM, m.FRAMEWORK)
             else "If you remove it")
    return f"What it is: {what}\n\nWhat it does: {does}\n\n{label}: {remove}"
