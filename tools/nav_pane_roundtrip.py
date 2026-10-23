"""EXPLICIT real-machine harness. Never imported or run by automated tests.

Creates only throwaway folders/namespace GUIDs. Existing namespace registrations
are exported before each case and compared structurally and byte-for-byte after.
"""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modules.nav_pane.engine.model import CLSID, HIDE, NAMESPACE, Verification
from modules.nav_pane.engine.operations import Operations
from modules.nav_pane.engine.registry import optional, snapshot


def capture(engine, directory):
    state, exports = {}, {}
    directory.mkdir(parents=True)
    for guid in engine.registry.children(NAMESPACE):
        state[guid] = {}
        for label, key in (("namespace", NAMESPACE + "\\" + guid), ("clsid", CLSID + "\\" + guid)):
            tree = snapshot(engine.registry, key)
            state[guid][label] = tree
            if tree is not None:
                destination = directory / f"{guid}-{label}.reg"
                engine.registry.export(key, destination)
                exports[(guid, label)] = destination.read_bytes()
        state[guid]["hidden"] = optional(engine.registry, HIDE, guid)
    return state, exports


def check_verified(engine, guid):
    result = engine.verify(guid)
    print(result.status, result.detail)
    if result.status != Verification.VERIFIED:
        raise RuntimeError("Fresh-process shell verification did not confirm the name AND target.")


def one_case(engine, target, work):
    before, exports = capture(engine, work / "before")
    guid = None
    record = None
    failure = None
    try:
        guid = engine.add_entry(str(target), "WCT NavPane Test")
        record = engine.journal.read()["entries"][guid]
        check_verified(engine, guid)
        engine.update_entry(guid, str(target), "WCT NavPane Test țșă renamed")
        check_verified(engine, guid)
    except Exception as error:
        failure = error
    finally:
        if guid:
            if guid in engine.journal.read()["transactions"]:
                engine.repair(guid, finish=False)
            engine.remove_entry(guid)
        else:
            # An add can fail after writing: recover only this harness's own new GUID.
            for pending_guid, pending in engine.journal.read()["entries"].items():
                if pending.get("name") == "WCT NavPane Test" and pending.get("resolved_target") == str(target):
                    engine.repair(pending_guid, finish=False)
    if guid:
        assert snapshot(engine.registry, NAMESPACE + "\\" + guid) is None
        assert snapshot(engine.registry, CLSID + "\\" + guid) is None
        assert optional(engine.registry, HIDE, guid) is None
        if record and record.get("junction"):
            assert not os.path.lexists(record["junction"])
    after, exported_after = capture(engine, work / "after")
    assert before == after, "An existing namespace entry changed"
    assert exports == exported_after, "An existing registry export changed byte-for-byte"
    assert target.is_dir(), "The real folder was touched"
    if failure:
        raise failure


def main():
    engine = Operations()
    reason = engine.account_check()
    if reason:
        raise PermissionError(reason)
    if engine.journal.read()["transactions"]:
        raise RuntimeError("Repair existing incomplete operations before running the harness.")
    roots = engine.roots()
    if not roots:
        raise RuntimeError("No real OneDrive root found; both round-trip cases are required.")
    with tempfile.TemporaryDirectory(prefix="wct-navpane-roundtrip-") as temporary:
        work = Path(temporary)
        target = work / "local-folder"
        target.mkdir()
        one_case(engine, target, work / "local")
        target.rmdir()
        cloud = Path(roots[0]) / ("WCT-NavPane-Roundtrip-" + uuid.uuid4().hex)
        cloud.mkdir()
        try:
            one_case(engine, cloud, work / "cloud")
        finally:
            cloud.rmdir()  # Empty test folder only; never recursive.
    print("PASS: local and OneDrive round trips; existing entries byte-identical.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
