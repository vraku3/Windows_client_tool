"""Read-only entry classification; no ownership inferred from a folder's name."""
import os
import stat

from . import junction
from .model import CLSID, FOLDER_SHORTCUT, HIDE, NAMESPACE, Health, Kind, NavEntry
from .onedrive import normal
from .registry import optional


def inspect_entry(registry, guid, record=None):
    entry = NavEntry(guid=guid, name=guid)
    try:
        root = CLSID + "\\" + guid
        value = optional(registry, root)
        entry.name = value[0] if value else guid
        shortcut = optional(registry, root + r"\Instance", "CLSID")
        destination = optional(registry, root + r"\Instance\InitPropertyBag", "Target")
        if shortcut and str(shortcut[0]).upper() == FOLDER_SHORTCUT and destination:
            entry.kind = Kind.HANDMADE
            entry.target = destination[0]
        if record and record.get("owned"):
            entry.kind = Kind.MANAGED
        order = optional(registry, root, "SortOrderIndex")
        icon = optional(registry, root + r"\DefaultIcon")
        entry.sort_order = (int(order[0]) & 0xFFFFFFFF) if order else 48
        entry.icon = icon[0] if icon else entry.icon
        if entry.target:
            _inspect_target(entry, record)
        if entry.health == Health.OK and entry.kind != Kind.SYSTEM and optional(registry, HIDE, guid) != (1, 4):
            entry.health = Health.HIDDEN_ON_DESKTOP
        if record and record.get("incomplete"):
            if not entry.target:
                entry.target = record.get("target", "")
                entry.resolved_target = record.get("resolved_target", "")
                entry.name = record.get("name", entry.name)
            entry.health = Health.INCOMPLETE
            entry.detail = "A change was interrupted. Choose Finish or Roll back."
    except (OSError, ValueError, TypeError) as error:
        entry.health = Health.UNREADABLE
        entry.detail = str(error)
    return entry


def _inspect_target(entry, record):
    try:
        info = os.lstat(entry.target)  # PermissionError must reach the caller.
    except FileNotFoundError:
        entry.health = Health.TARGET_MISSING
        return
    if getattr(info, "st_reparse_tag", 0) == junction.MOUNT_POINT:
        entry.junction = entry.target
        entry.resolved_target = junction.target(entry.target) or ""
        expected = (record or {}).get("resolved_target", entry.resolved_target)
        if normal(entry.resolved_target) != normal(expected) or not _directory(entry.resolved_target):
            entry.health = Health.JUNCTION_BROKEN
    else:
        entry.resolved_target = os.path.realpath(entry.target)
        if not stat.S_ISDIR(info.st_mode):
            entry.health = Health.TARGET_MISSING


def _directory(path):
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except FileNotFoundError:
        return False


def list_entries(registry, data):
    try:
        guids = registry.children(NAMESPACE)
    except FileNotFoundError:
        guids = []
    # Fake and real backends need the same GUID casing.
    guids = {g.upper() for g in guids}
    records = data["entries"]
    guids.update(g for g, r in records.items() if r.get("incomplete"))
    entries = []
    for guid in guids:
        entry = inspect_entry(registry, guid, records.get(guid))
        entries.append(entry)
    return sorted(entries, key=lambda e: (e.sort_order, e.name.casefold()))
