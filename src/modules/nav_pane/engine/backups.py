"""Scoped exports and exact, journaled restores. No Qt or registry import side effects."""
import json
import uuid
from pathlib import Path

from .journal import atomic_json, canonical, encode_value, loads
from .model import CLSID, HIDE, NAMESPACE
from .registry import optional, snapshot
from .transactions import LOCK


def quoted(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def reg_value(name, value):
    prefix = (quoted(name) if name else "@") + "="
    if value is None:
        return prefix + "-"
    data, kind = value
    if kind == 1 and not any(c in data for c in "\r\n\x00"):
        return prefix + quoted(data)
    if kind == 4:
        return prefix + f"dword:{data & 0xFFFFFFFF:08x}"
    if kind in (1, 2):
        raw = (data + "\x00").encode("utf-16le")
    elif kind == 7:
        raw = ("\x00".join(data) + "\x00\x00").encode("utf-16le")
    elif kind == 11:
        raw = data.to_bytes(8, "little", signed=False)
    elif isinstance(data, bytes):
        raw = data
    else:
        raise ValueError(f"Cannot export registry type {kind}")
    tag = "hex" if kind == 3 else f"hex({kind:x})"
    return prefix + tag + ":" + ",".join(f"{b:02x}" for b in raw)


def reg_tree(key, tree):
    if tree is None:
        return [f"[-HKEY_CURRENT_USER\\{key}]", ""]
    lines = [f"[HKEY_CURRENT_USER\\{key}]"]
    lines.extend(reg_value(name, value) for name, value in tree["values"].items())
    lines.append("")
    for name, child in tree["children"].items():
        lines.extend(reg_tree(key + "\\" + name, child))
    return lines


def export_snapshot(guid, state, destination):
    lines = ["Windows Registry Editor Version 5.00", ""]
    for key, tree in zip((CLSID + "\\" + guid, NAMESPACE + "\\" + guid), state["trees"]):
        lines.extend(reg_tree(key, tree))
    lines += [f"[HKEY_CURRENT_USER\\{HIDE}]", reg_value(guid, state["hidden"]), ""]
    Path(destination).write_text("\r\n".join(lines), encoding="utf-16", newline="")


class Backups:
    def _snapshot_entry(self, guid):
        return {"guid": guid, "trees": [snapshot(self.registry, p + "\\" + guid) for p in (CLSID, NAMESPACE)],
                "hidden": optional(self.registry, HIDE, guid),
                "record": self.journal.read()["entries"].get(guid)}

    def export_entry(self, guid, destination):
        with LOCK, self.journal.lock():
            guid = "{" + str(uuid.UUID(guid.strip("{}"))).upper() + "}"
            export_snapshot(guid, self._snapshot_entry(guid), destination)

    def _backup(self, guid):
        state = self._snapshot_entry(guid)
        folder = self.journal.directory / "backups" / (guid + "-" + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        export_snapshot(guid, state, folder / "entry.reg")
        atomic_json(folder / "hidden-value.json", {"key": HIDE, "name": guid, "value": state["hidden"]})
        atomic_json(folder / "snapshot.json", state)  # Complete marker is written last.

    def list_backups(self, guid):
        guid = "{" + str(uuid.UUID(guid.strip("{}"))).upper() + "}"
        root = self.journal.directory / "backups"
        return sorted(root.glob(guid + "-*/snapshot.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)

    def _read_backup(self, guid, path):
        path = Path(path).resolve()
        if path not in [p.resolve() for p in self.list_backups(guid)]:
            raise ValueError("Choose an automatic backup for this entry.")
        state = loads(path.read_text(encoding="utf-8"))
        if state.get("guid") != guid or len(state.get("trees", [])) != 2:
            raise ValueError("Backup identity does not match this entry.")
        return state

    def _tree_plan(self, key, wanted, steps):
        current = canonical(snapshot(self.registry, key))
        if current == canonical(wanted):
            return
        if wanted is None:
            steps.append(self._step("delete_tree", key=key))
            return
        if current is None:
            steps.append(self._step("create_key", key=key))
        current = current or {"values": {}, "children": {}}
        for name in current["values"].keys() - wanted["values"].keys():
            steps.append(self._step("delete_value", key=key, name=name))
        for name, (value, kind) in wanted["values"].items():
            if canonical(current["values"].get(name)) != [value, kind]:
                steps.append(self._step("set", key=key, name=name, value=value, type=kind))
        for name in current["children"].keys() | wanted["children"].keys():
            self._tree_plan(key + "\\" + name, wanted["children"].get(name), steps)

    def _restore_plan(self, guid, state):
        steps = []
        for prefix, tree in zip((CLSID, NAMESPACE), state["trees"]):
            key = prefix + "\\" + guid
            self._tree_plan(key, tree, steps)
            steps.append(self._step("verify_tree", key=key, tree=tree))
        hidden = state["hidden"]
        steps.append(self._step("set", key=HIDE, name=guid, value=hidden[0], type=hidden[1]) if hidden
                     else self._step("delete_value", key=HIDE, name=guid))
        old = self.journal.read()["entries"].get(guid) or {}
        record = state.get("record") or {}
        if record.get("owned") and record.get("junction"):
            steps.insert(0, self._step("junction_create", path=record["junction"], target=record["resolved_target"]))
        if old.get("owned") and old.get("junction") and old["junction"] != record.get("junction"):
            steps.append(self._step("junction_remove", path=old["junction"], target=old["resolved_target"]))
        for step in steps:
            self._validate_step(guid, step)
        return steps

    def preview_restore(self, guid, path):
        with LOCK, self.journal.lock():
            return self._restore_plan(guid, self._read_backup(guid, path))

    def restore_backup(self, guid, path, expected):
        with LOCK, self.journal.lock():
            self._guard()
            state = self._read_backup(guid, path)
            steps = self._restore_plan(guid, state)
            if canonical(steps) != canonical(expected):
                raise OSError("The entry changed since the preview. Review the backup again.")
            self._backup(guid)
            self._transact(guid, steps, state.get("record"), operation="restore")
            self.notify()


def describe_plan(steps):
    """Show every typed before/after value, deleted subtree and junction operation."""
    return json.dumps(steps, ensure_ascii=False, indent=2, default=encode_value)
