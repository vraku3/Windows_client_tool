"""HKCU namespace operations. Dependencies are injectable; tests never touch Windows state."""
import os
import stat
import uuid

from . import inspection, junction, onedrive, shell
from .journal import Journal
from .backups import Backups
from .model import ADVANCED, CLSID, DEFAULT_ICON, HIDE, NAMESPACE, Health, Kind, values_for
from .registry import Registry, optional, snapshot
from .transactions import LOCK, Transactions


class Operations(Backups, Transactions):
    def __init__(self, registry=None, directory=None, links=None, account_check=None,
                 profile=None, root_paths=None, notify=None):
        self.registry = registry if registry is not None else Registry()
        self.journal = Journal(directory)
        self.links = links if links is not None else junction
        self.account_check = account_check if account_check is not None else shell.write_refusal
        self.profile = profile or os.environ["USERPROFILE"]
        self.root_paths = root_paths
        self.notify = notify if notify is not None else shell.refresh_shell

    def _guard(self):
        reason = self.account_check()
        if reason:
            raise PermissionError(reason)
        self.journal.read()  # Corrupt/unreadable journal blocks every mutation.

    def roots(self):
        return onedrive.roots(self.registry) if self.root_paths is None else self.root_paths

    def list_entries(self):
        with LOCK, self.journal.lock():
            return inspection.list_entries(self.registry, self.journal.read())

    def show_all_folders(self):
        value = optional(self.registry, ADVANCED, "NavPaneShowAllFolders")
        if value is None:
            return False
        if value[1] != 4 or value[0] not in (0, 1):
            raise ValueError("NavPaneShowAllFolders could not be interpreted.")
        return bool(value[0])

    def validate(self, path, name, sort_order=48, icon=DEFAULT_ICON):
        if not name.strip() or any(ord(c) < 32 for c in name):
            raise ValueError("Enter a display name without control characters.")
        if not os.path.isabs(path) or path.startswith(("\\\\", "//")):
            raise ValueError("Choose an absolute local folder. Network/UNC paths are not supported.")
        info = os.stat(path)  # Refused read stays a refusal.
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("The selected path is not a folder.")
        if not 0 <= int(sort_order) <= 0xFFFFFFFF:
            raise ValueError("Position must be an unsigned DWORD.")
        if not icon.strip() or "\x00" in icon:
            raise ValueError("Choose a valid icon location.")
        inside = onedrive.contains(path, self.roots())
        if inside:
            junction.valid_name(name)
        return inside

    def _record(self, guid, path, name, sort_order, icon, old=None, entry=None):
        inside = self.validate(path, name, sort_order, icon)
        link = None
        target = path
        if entry and entry.target and entry.target != (old or {}).get("junction"):
            try:
                actual = self.links.target(entry.target)
            except FileNotFoundError:
                actual = None
            if actual and onedrive.normal(actual) == onedrive.normal(path):
                # This link belongs to the user, not the app's recovery journal.
                target = entry.target
                inside = False
        if inside:
            if old and old.get("junction") and onedrive.normal(old["resolved_target"]) == onedrive.normal(path):
                link = old["junction"]
                actual = self.links.target(link)
                if not actual or onedrive.normal(actual) != onedrive.normal(path):
                    raise OSError("The recorded junction has changed. Refusing to reuse it.")
            else:
                link = self.links.choose(self.profile, name)
        return {"owned": (old or {}).get("owned", True), "name": name, "target": link or target,
                "resolved_target": os.path.realpath(path), "junction": link,
                "sort_order": int(sort_order), "icon": icon}

    def _plan(self, guid, record, old=None):
        steps = []
        if record["junction"] and record["junction"] != (old or {}).get("junction"):
            steps.append(self._step("junction_create", path=record["junction"], target=record["resolved_target"]))
        for key, name, value, kind in values_for(guid, record["name"], record["target"], record["sort_order"], record["icon"]):
            steps.append(self._step("set", key=key, name=name, value=value, type=kind))
        if old and old.get("junction") and old["junction"] != record["junction"]:
            steps.append(self._step("junction_remove", path=old["junction"], target=old["resolved_target"]))
        return steps

    def add_entry(self, path, name, sort_order=48, icon=DEFAULT_ICON):
        with LOCK, self.journal.lock():
            self._guard()
            guid = "{" + str(uuid.uuid4()).upper() + "}"
            record = self._record(guid, path, name, sort_order, icon)
            self._transact(guid, self._plan(guid, record), record)
            self.notify()
            return guid

    def _editable(self, guid):
        guid = "{" + str(uuid.UUID(guid.strip("{}"))).upper() + "}"
        entry = next((e for e in self.list_entries() if e.guid == guid), None)
        if entry is None or entry.kind == Kind.SYSTEM:
            raise PermissionError("Windows / OneDrive entries are read-only.")
        if entry.health in (Health.UNREADABLE, Health.INCOMPLETE):
            raise PermissionError(entry.detail or "Entry cannot be safely read. Repair it first.")
        self._backup(guid)
        return entry

    def repair_orphan(self, guid):
        with LOCK, self.journal.lock():
            self._guard()
            entry = self._editable(guid)
            if entry.health != Health.ORPHAN:
                raise ValueError("This entry has no missing NameSpace key.")
            if snapshot(self.registry, CLSID + "\\" + guid) is None:
                raise ValueError("The CLSID is missing too. Restore a backup or remove leftovers.")
            step = self._step("set", key=NAMESPACE + "\\" + guid, name="", value=entry.name, type=1)
            record = self.journal.read()["entries"][guid]
            self._transact(guid, [step], record, operation="repair NameSpace")
            self.notify()

    def update_entry(self, guid, path, name, sort_order=48, icon=DEFAULT_ICON):
        with LOCK, self.journal.lock():
            self._guard()
            entry = self._editable(guid)
            guid = entry.guid
            old = self.journal.read()["entries"].get(guid)
            record = self._record(guid, path, name, sort_order, icon, old, entry)
            if entry.kind == Kind.HANDMADE:
                record["owned"] = False
            self._transact(guid, self._plan(guid, record, old), record, operation="edit")
            self.notify()
            return guid

    def remove_entry(self, guid):
        with LOCK, self.journal.lock():
            self._guard()
            entry = self._editable(guid)
            guid = entry.guid
            old = self.journal.read()["entries"].get(guid)
            steps = [self._step("delete_tree", key=NAMESPACE + "\\" + guid),
                     self._step("delete_tree", key=CLSID + "\\" + guid),
                     self._step("delete_value", key=HIDE, name=guid)]
            if old and old.get("junction"):
                steps.append(self._step("junction_remove", path=old["junction"], target=old["resolved_target"]))
            self._transact(guid, steps, None, operation="removal",
                           entry={"name": entry.name, "target": entry.target,
                                  "resolved_target": entry.resolved_target, "junction": entry.junction})
            self.notify()

    def set_show_all_folders(self, enabled):
        with LOCK, self.journal.lock():
            self._guard()
            data = self.journal.read()
            if "settings_before" not in data:
                data["settings_before"] = optional(self.registry, ADVANCED, "NavPaneShowAllFolders")
                self.journal.save(data)
            step = self._step("set", key=ADVANCED, name="NavPaneShowAllFolders", value=int(enabled), type=4)
            self._transact("settings", [step])
            self.notify()

    def revert_show_all_folders(self):
        with LOCK, self.journal.lock():
            self._guard()
            data = self.journal.read()
            if "settings_before" not in data:
                raise ValueError("No original navigation setting has been recorded.")
            before = data["settings_before"]
            fields = {"key": ADVANCED, "name": "NavPaneShowAllFolders"}
            step = self._step("set", **fields, value=before[0], type=before[1]) if before else self._step("delete_value", **fields)
            self._transact("settings", [step])
            data = self.journal.read()
            del data["settings_before"]
            self.journal.save(data)
            self.notify()

    def verify(self, guid):
        entry = next(e for e in self.list_entries() if e.guid == guid)
        if entry.health == Health.UNREADABLE:
            raise PermissionError(entry.detail)
        return shell.verify(entry)

    def refresh_shell(self):
        self.notify()
