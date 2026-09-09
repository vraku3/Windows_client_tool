"""Named driver-set snapshots and diffing, for spotting drift after a
problematic Windows Update or driver install. Same shape as
gpresult/rsop_snapshot.py -- small JSON payload files plus a cheap
.meta.json sidecar so listing never deserializes a full payload -- but
without that module's custom to-dict/from-dict machinery, since every
DriverInfo field is already a JSON-native primitive.
"""
import dataclasses
import datetime
import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Tuple

from modules.driver_manager.driver_reader import DriverInfo

logger = logging.getLogger(__name__)


def default_baseline_dir() -> str:
    """`%APPDATA%/WindowsTweaker/driver_baselines`. Computed independently
    of app.py (which builds a Qt-dragging singleton), the same way
    rsop_snapshot.default_snapshot_dir() does, so this module stays
    testable with no display."""
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(base, "WindowsTweaker", "driver_baselines")


@dataclass
class BaselineMeta:
    name: str = ""
    taken_at: str = ""
    driver_count: int = 0
    error: str = ""  # non-empty means this baseline's sidecar could not
                      # be read -- every other field is then a guess


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def save_baseline(name: str, drivers: List[DriverInfo]) -> None:
    directory = default_baseline_dir()
    os.makedirs(directory, exist_ok=True)
    stem = _safe_filename(name)
    payload = [dataclasses.asdict(d) for d in drivers]
    with open(os.path.join(directory, f"{stem}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)
    meta = {
        "name": name,
        "taken_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "driver_count": len(drivers),
    }
    with open(os.path.join(directory, f"{stem}.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)


def list_baselines() -> List[BaselineMeta]:
    directory = default_baseline_dir()
    if not os.path.isdir(directory):
        return []
    metas: List[BaselineMeta] = []
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".meta.json"):
            continue
        path = os.path.join(directory, entry)
        stem = entry[: -len(".meta.json")]
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            metas.append(BaselineMeta(
                name=raw.get("name", stem),
                taken_at=raw.get("taken_at", ""),
                driver_count=int(raw.get("driver_count", 0)),
            ))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Could not read baseline sidecar %s: %s", path, exc)
            metas.append(BaselineMeta(name=stem, error=str(exc)))
    return metas


def load_baseline(name: str) -> List[DriverInfo]:
    directory = default_baseline_dir()
    stem = _safe_filename(name)
    path = os.path.join(directory, f"{stem}.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [DriverInfo(**d) for d in raw]


@dataclass
class DriverDiff:
    added: List[DriverInfo] = field(default_factory=list)
    removed: List[DriverInfo] = field(default_factory=list)
    changed: List[Tuple[DriverInfo, DriverInfo]] = field(default_factory=list)


def diff_against_baseline(baseline: List[DriverInfo],
                          current: List[DriverInfo]) -> DriverDiff:
    """Matches by device_name -- a same-machine, same-hardware-set
    comparison over time, not a cross-machine one (a cross-machine
    "known-good profile" comparison is a different, harder feature and
    out of scope here)."""
    old_by_name = {d.device_name: d for d in baseline}
    new_by_name = {d.device_name: d for d in current}
    added = [d for name, d in new_by_name.items() if name not in old_by_name]
    removed = [d for name, d in old_by_name.items() if name not in new_by_name]
    changed = [
        (old_by_name[name], new_by_name[name])
        for name in old_by_name.keys() & new_by_name.keys()
        if old_by_name[name].version != new_by_name[name].version
        or old_by_name[name].publisher != new_by_name[name].publisher
    ]
    return DriverDiff(added=added, removed=removed, changed=changed)
