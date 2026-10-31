"""Quarantine verified recovered steps without reapplying Windows settings.

Input is a saved recovery plan, validated against BackupService's read-only
history. Only successfully reverted registry/service steps may be protected.
The per-machine policy survives application builds in the app data directory.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from modules.tweaks.safety_policy import SafetyPolicy


def make_blocks(data_dir, plan, incident):
    blocks = {}
    with sqlite3.connect((data_dir / "tweaks.db").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for entry in plan:
            row = db.execute("SELECT * FROM tweak_steps WHERE id=?", (entry["id"],)).fetchone()
            if row is None or not row["reverted_at"] or row["revert_error"]:
                raise ValueError(f"Step {entry['id']} is not a successfully recorded recovery")
            if json.loads(row["before_value"]) != entry["restore_value"]:
                raise ValueError("Recovery value differs from the saved history")
            desired = json.loads(row["after_value"])
            if row["step_type"] == "registry" and row["reg_kind"] == 4:
                step = {
                    "type": "registry",
                    "key": row["target"],
                    "value": row["value_name"],
                    "kind": "DWORD",
                    "data": desired,
                }
            elif row["step_type"] == "service" and desired == 4:
                step = {"type": "service", "name": row["target"], "start_type": "disabled"}
            else:
                raise ValueError(
                    "This recovery protection supports DWORD registry values and service disabling"
                )
            block = blocks.setdefault(
                row["tweak_id"],
                {
                    "tweak_id": row["tweak_id"],
                    "reason": f"Protected after {incident}. Grouped rollback was followed by restored stability; no individual cause was proven. Keep the recovered setting.",
                    "steps": [],
                },
            )
            if step not in block["steps"]:
                block["steps"].append(step)
    return list(blocks.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.environ["APPDATA"]) / "WindowsTweaker"
    )
    parser.add_argument("--recovery-plan", type=Path, required=True)
    parser.add_argument("--incident", required=True)
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    plan = json.loads(args.recovery_plan.read_text(encoding="utf-8"))
    blocks = make_blocks(data_dir, plan, args.incident)
    path = data_dir / "tweak_safety.json"
    policy = {"schema_version": 1, "machine": platform.node(), "blocks": []}
    if path.exists():
        if SafetyPolicy.from_file(path).error:
            raise ValueError("Repair the existing policy before merging recovery records")
        policy = json.loads(path.read_text(encoding="utf-8"))
        if policy["machine"].casefold() != platform.node().casefold():
            raise ValueError("Existing policy belongs to a different PC")
    merged = {b["tweak_id"]: b for b in policy["blocks"]}
    for block in blocks:
        existing = merged.setdefault(block["tweak_id"], block)
        for step in block["steps"]:
            if step not in existing["steps"]:
                existing["steps"].append(step)
    policy["blocks"] = list(merged.values())
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(policy, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    if SafetyPolicy.from_file(temp).error:
        raise ValueError("Generated policy failed validation")
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    temp.replace(path)
    print(
        json.dumps(
            {
                "policy": str(path),
                "machine": policy["machine"],
                "protected_ids": [b["tweak_id"] for b in blocks],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
