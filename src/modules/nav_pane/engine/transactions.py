"""Serialized write-ahead steps, resumable in either direction."""
import json
import logging
import os
import threading
import uuid

from . import junction
from .model import ADVANCED, CLSID, HIDE, NAMESPACE
from .registry import optional, snapshot

LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def canonical(data):
    return json.loads(json.dumps(data))


def restore_tree(registry, key, tree):
    if tree is None:
        registry.delete_tree(key)
        return
    registry.create_key(key)
    for name, (value, kind) in tree["values"].items():
        registry.write(key, name, value, kind)
    for name, child in tree["children"].items():
        restore_tree(registry, key + "\\" + name, child)
    if canonical(snapshot(registry, key)) != canonical(tree):
        raise OSError(f"Restored key did not match backup: {key}")


def tree_subset(current, original):
    """A partially deleted tree can resume, but external additions cannot be erased."""
    if current is None:
        return True
    if original is None:
        return False
    return (all(original["values"].get(name) == value for name, value in current["values"].items())
            and all(name in original["children"] and tree_subset(child, original["children"][name])
                    for name, child in current["children"].items()))


class Transactions:
    def _step(self, action, **fields):
        step = {"action": action, "done": False, **fields}
        key = fields.get("key")
        if action in ("set", "delete_value"):
            step["before"] = optional(self.registry, key, fields["name"])
            # Keep exact key presence so undo removes only app-created empty leaves.
            missing = []
            cursor = key
            while cursor and snapshot(self.registry, cursor) is None:
                missing.append(cursor)
                cursor = cursor.rpartition("\\")[0]
            step["missing_keys"] = missing
        elif action == "delete_tree":
            step["before"] = snapshot(self.registry, key)
        return canonical(step)

    def _transact(self, guid, steps, record=None):
        data = self.journal.read()
        if guid in data["transactions"]:
            raise ValueError("Repair the incomplete operation before making another change.")
        transaction = {"steps": steps, "before_record": data["entries"].get(guid), "record": record}
        data["transactions"][guid] = transaction
        if guid != "settings":
            data["entries"][guid] = dict(record or data["entries"].get(guid) or {}, incomplete=True)
        self.journal.save(data)  # Entire plan durable before even the first write.
        try:
            self._finish(guid, data)
        except Exception as error:
            logger.error("Navigation pane transaction %s incomplete: %s", guid, error)
            if guid in data["transactions"]:
                data["transactions"][guid]["error"] = str(error)
                self.journal.save(data)
            raise

    def _finish(self, guid, data):
        transaction = data["transactions"][guid]
        if transaction.get("rolling_back"):
            raise ValueError("Rollback has started. Continue Roll back to restore the before-state.")
        for step in transaction["steps"]:
            if not step["done"]:
                self._execute(guid, step)
                step["done"] = True
                self.journal.save(data)
        if guid != "settings":
            record = transaction["record"]
            if record is None:
                data["entries"].pop(guid, None)
            else:
                data["entries"][guid] = dict(record, incomplete=False)
        del data["transactions"][guid]
        self.journal.save(data)

    def _validate_step(self, guid, step):
        if guid == "settings":
            if step.get("key") != ADVANCED or step.get("name") != "NavPaneShowAllFolders":
                raise ValueError("Invalid settings journal target")
            return
        uuid.UUID(guid.strip("{}"))
        key = step.get("key", "")
        allowed = [CLSID + "\\" + guid, NAMESPACE + "\\" + guid]
        if key and not (any(key == p or key.startswith(p + "\\") for p in allowed)
                        or key == HIDE and step.get("name") == guid):
            raise ValueError("Journal target is outside this navigation entry")
        if step["action"].startswith("junction"):
            # Resolve the PARENT only: normal() uses realpath, which follows the
            # junction itself and turned every OneDrive link into its target
            # (measured 2026-10-23: removal refused in the real round trip).
            path = step["path"]
            parent, leaf = os.path.split(os.path.normpath(path))
            if (junction.normal(parent) != junction.normal(self.profile)
                    or not leaf.casefold().startswith(".navpane-")):
                raise ValueError("Journal junction is outside the reserved link location")

    def _execute(self, guid, step):
        self._validate_step(guid, step)
        action = step["action"]
        logger.info("nav pane: %s %s", action, step.get("key", step.get("path")))
        if action == "set":
            current = canonical(optional(self.registry, step["key"], step["name"]))
            if current not in (step["before"], [step["value"], step["type"]]):
                raise OSError("Registry value changed outside this operation; refusing to overwrite it.")
            self.registry.write(step["key"], step["name"], step["value"], step["type"])
            if canonical(self.registry.read(step["key"], step["name"])) != [step["value"], step["type"]]:
                raise OSError("Registry read-back mismatch")
        elif action == "delete_value":
            if canonical(optional(self.registry, step["key"], step["name"])) not in (None, step["before"]):
                raise OSError("Registry value changed outside this operation.")
            self.registry.delete_value(step["key"], step["name"])
            if optional(self.registry, step["key"], step["name"]) is not None:
                raise OSError("Deleted value is still present.")
        elif action == "delete_tree":
            if not tree_subset(canonical(snapshot(self.registry, step["key"])), step["before"]):
                raise OSError("Registry key changed outside this operation.")
            self.registry.delete_tree(step["key"])
            if snapshot(self.registry, step["key"]) is not None:
                raise OSError("Deleted registry key is still present.")
        elif action == "junction_create":
            self.links.create(step["path"], step["target"])
        elif action == "junction_remove":
            self.links.remove(step["path"], step["target"])
        else:
            raise ValueError(f"Unknown journal action: {action}")

    def repair(self, guid, finish=True):
        with LOCK:
            self._guard()
            data = self.journal.read()
            if guid not in data["transactions"]:
                raise ValueError("No incomplete operation exists for this entry.")
            if finish:
                self._finish(guid, data)
            else:
                self._rollback(guid, data)
            self.notify()

    def _rollback(self, guid, data):
        transaction = data["transactions"][guid]
        transaction["rolling_back"] = True
        self.journal.save(data)
        for step in reversed(transaction["steps"]):
            self._validate_step(guid, step)
            if step.get("undone"):
                continue
            self._undo(guid, step)
            step["undone"] = True
            self.journal.save(data)
        if transaction["before_record"] is None:
            data["entries"].pop(guid, None)
        else:
            data["entries"][guid] = transaction["before_record"]
        del data["transactions"][guid]
        self.journal.save(data)

    def _undo(self, guid, step):
        action = step["action"]
        if action in ("set", "delete_value"):
            before = step["before"]
            current = canonical(optional(self.registry, step["key"], step["name"]))
            after = [step["value"], step["type"]] if action == "set" else None
            if current not in (before, after):
                raise OSError("Registry value changed since the interrupted operation. Rollback refused.")
            if before is None:
                self.registry.delete_value(step["key"], step["name"])
            else:
                self.registry.write(step["key"], step["name"], *before)
            for key in step.get("missing_keys", []):
                allowed = (CLSID + "\\" + guid, NAMESPACE + "\\" + guid)
                if not any(key == p or key.startswith(p + "\\") for p in allowed) and key not in (HIDE, ADVANCED):
                    continue
                tree = snapshot(self.registry, key)
                if tree == {"values": {}, "children": {}}:
                    self.registry.delete_tree(key)
        elif action == "delete_tree":
            if not tree_subset(canonical(snapshot(self.registry, step["key"])), step["before"]):
                raise OSError("Registry key changed since the operation. Rollback refused.")
            restore_tree(self.registry, step["key"], step["before"])
        elif action == "junction_create":
            self.links.remove(step["path"], step["target"])
        elif action == "junction_remove":
            self.links.create(step["path"], step["target"])
