# Driver Manager Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only Driver Manager enrichments (Reliability cross-reference, per-device detail dialog, WHQL detection, duplicate-hardware-ID detection, flag explainers, hardware-ID copy, full inventory export, named baseline snapshots/diff, System Restore point listing, a Dashboard problem-count tile) with zero new writes to driver/system state.

**Architecture:** Extends `src/modules/driver_manager/`'s existing Qt-free reader / Qt UI split. Two new Qt-free files (`driver_diagnostics.py`, `driver_baselines.py`), one new Qt dialog (`driver_detail_dialog.py`), additions to the existing `driver_reader.py`/`driver_module.py`, and one small addition to the Dashboard module.

**Tech Stack:** Python 3.12, PyQt6, PowerShell (via `subprocess`) for the one new WMI field, no new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-09-09-driver-manager-phase0-design.md`

## Global Constraints

- Every new reader returns `None`/empty distinctly on a failed read — never collapsed into a value that looks like a real answer (this codebase's project-wide rule; see CLAUDE.md "Important Gotchas").
- No silent exception swallowing — every `except` logs via `logger.warning`/`logger.error`, never bare `pass`.
- Nothing in this phase writes to DriverStore, the registry, or runs an install/uninstall/delete `pnputil` call. `driver_store_size` and `list_restore_points` are read-only.
- New pure functions (`detect_duplicate_hardware_ids`, `DriverDiff` computation) get direct unit tests with fixture data — no real subprocess/WMI calls in their tests.
- Match this codebase's established idioms exactly: `centered_item`/`NumericSortItem` from `core/table_ui.py` for any new table cells, `_module()`/`_FakeApp` test harness already in `tests/test_driver_module.py`, `%APPDATA%/WindowsTweaker` for any new persisted file (computed independently of `app.py`, the way `rsop_snapshot.default_snapshot_dir()` already does, so the module stays headless-testable).
- Full test suite (`pytest tests/ -q`) must stay green after every task — the only expected pre-existing failures are the two documented, unrelated `tests/test_procengine_gpuinfo.py` real-GPU-hardware tests (confirmed via `git merge-base --is-ancestor` to predate this plan).

---

### Task 1: `HardWareID`/`Signer` fields — WHQL detection and hardware-ID foundation

**Files:**
- Modify: `src/modules/driver_manager/driver_reader.py`
- Test: `tests/test_driver_reader.py`

**Interfaces:**
- Produces: `DriverInfo.hardware_id: str = ""`, `DriverInfo.whql_certified: bool = False` (new trailing default fields, same pattern as the existing `inf_name`/`device_id` fields — positional test constructors elsewhere keep working unchanged).

Real, verified fact (checked live against `Win32_PnPSignedDriver` on this
machine): a WHQL-certified driver's `Signer` field is exactly
`"Microsoft Windows Hardware Compatibility Publisher"`; every genuine
third-party driver on a modern Windows 11 install carries this (Windows
won't load an unattested kernel driver by default), while Microsoft's own
inbox drivers show `Signer = "Microsoft Windows"`. This is a clean,
reliable distinction — not a heuristic.

`Win32_PnPSignedDriver.HardWareID` is a string array in real WMI; cast to
`[string]` in the PowerShell script (as every other field already is)
gives PowerShell's own array-to-string join (semicolon-separated). Store
the FIRST id (before the first `;`) as `DriverInfo.hardware_id` — it's the
most specific one and the one Windows actually matched the driver against;
the others are broader compatibility fallbacks.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_driver_reader.py — add these two tests
def test_whql_certified_true_only_for_the_real_whcp_signer(monkeypatch):
    def fake_run(cmd, **k):
        class R:
            returncode = 0
            stdout = json.dumps([
                {"Name": "AMD Radeon RX 7900 XTX", "Class": "Display",
                 "Version": "1.0", "Date": "", "Publisher": "AMD",
                 "IsSigned": True, "ErrorCode": 0, "InfName": "oem1.inf",
                 "DeviceID": "PCI\\VEN_1002", "HardWareID": "PCI\\VEN_1002;PCI\\VEN_1002&DEV_744C",
                 "Signer": "Microsoft Windows Hardware Compatibility Publisher"},
                {"Name": "WAN Miniport (IP)", "Class": "Net",
                 "Version": "1.0", "Date": "", "Publisher": "Microsoft",
                 "IsSigned": True, "ErrorCode": 0, "InfName": "netvmini.inf",
                 "DeviceID": "ROOT\\MS_NDISWANIP", "HardWareID": "ROOT\\MS_NDISWANIP",
                 "Signer": "Microsoft Windows"},
            ])
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    drivers = dr.fetch_drivers()
    by_name = {d.device_name: d for d in drivers}
    assert by_name["AMD Radeon RX 7900 XTX"].whql_certified is True
    assert by_name["WAN Miniport (IP)"].whql_certified is False


def test_hardware_id_takes_the_first_of_a_semicolon_joined_array():
    info = dr._build_driver_info({
        "Name": "Test Device", "Class": "Net", "Version": "1.0", "Date": "",
        "Publisher": "V", "IsSigned": True, "ErrorCode": 0,
        "HardWareID": "PCI\\VEN_1234&DEV_5678;PCI\\VEN_1234",
        "Signer": "Microsoft Windows Hardware Compatibility Publisher",
    })
    assert info.hardware_id == "PCI\\VEN_1234&DEV_5678"
    assert info.whql_certified is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -k "whql or hardware_id" -v`
Expected: FAIL — `AttributeError: 'DriverInfo' object has no attribute 'whql_certified'` (or similar, since these fields don't exist yet).

- [ ] **Step 3: Add the fields and PS_CMD query additions**

In `_PS_CMD` (the `[PSCustomObject]@{...}` block), add two lines after `DeviceID`:
```powershell
        DeviceID   = [string]$d.DeviceID
        HardWareID = [string]$d.HardWareID
        Signer     = [string]$d.Signer
    }
```

Add to `DriverInfo`:
```python
    device_id: str = ""  # PNP device instance id -- a real unique key,
                          # unlike device_name (see fetch_drivers' dedup)
    hardware_id: str = ""  # the FIRST (most specific) of the driver's
                            # HardWareID array -- the one Windows actually
                            # matched against, semicolon-joined by
                            # PowerShell's [string] cast on a string array
    whql_certified: bool = False  # Signer == the exact WHCP string below,
                                   # not a heuristic -- verified live
                                   # against this machine's real drivers
```

Add a module-level constant near `_OEM_INF_RE`:
```python
_WHCP_SIGNER = "Microsoft Windows Hardware Compatibility Publisher"
```

In `_build_driver_info`, after the existing field extractions, add:
```python
    hardware_id_raw = d.get("HardWareID") or ""
    hardware_id = hardware_id_raw.split(";")[0] if hardware_id_raw else ""
    whql_certified = (d.get("Signer") or "") == _WHCP_SIGNER
```

And in the final `return DriverInfo(...)` call, add:
```python
        hardware_id=hardware_id,
        whql_certified=whql_certified,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -k "whql or hardware_id" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full driver_reader suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py tests/test_driver_module.py -v`
Expected: all pass (existing tests are unaffected — new fields are trailing with defaults).

```bash
git add src/modules/driver_manager/driver_reader.py tests/test_driver_reader.py
git commit -m "feat(driver manager): detect WHQL certification and capture hardware ID

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Duplicate hardware-ID detection

**Files:**
- Modify: `src/modules/driver_manager/driver_reader.py`
- Test: `tests/test_driver_reader.py`

**Interfaces:**
- Consumes: `DriverInfo.hardware_id` (Task 1).
- Produces: `detect_duplicate_hardware_ids(drivers: List[DriverInfo]) -> Dict[str, List[DriverInfo]]`.

- [ ] **Step 1: Write the failing test**

```python
def test_duplicate_hardware_ids_groups_only_real_collisions():
    a = DriverInfo(device_name="Generic Driver A", driver_class="Net",
                   version="1.0", date="", publisher="X", signed=True,
                   error_code=0, flags="", hardware_id="PCI\\VEN_AAAA")
    b = DriverInfo(device_name="Generic Driver B", driver_class="Net",
                   version="1.0", date="", publisher="Y", signed=True,
                   error_code=0, flags="", hardware_id="PCI\\VEN_AAAA")
    c = DriverInfo(device_name="Unique Driver", driver_class="Net",
                   version="1.0", date="", publisher="Z", signed=True,
                   error_code=0, flags="", hardware_id="PCI\\VEN_BBBB")
    no_id = DriverInfo(device_name="No HWID", driver_class="Net",
                       version="1.0", date="", publisher="W", signed=True,
                       error_code=0, flags="", hardware_id="")
    groups = dr.detect_duplicate_hardware_ids([a, b, c, no_id])
    assert groups == {"PCI\\VEN_AAAA": [a, b]}
```

(Note: import `DriverInfo` at the top of `tests/test_driver_reader.py` if
not already present — check first; if the file only ever constructs via
`dr.DriverInfo`, use that form instead for consistency with the rest of
the file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -k duplicate_hardware -v`
Expected: FAIL — `AttributeError: module 'driver_reader' has no attribute 'detect_duplicate_hardware_ids'`.

- [ ] **Step 3: Implement**

Add to `driver_reader.py`, near `_dedup_key`:
```python
def detect_duplicate_hardware_ids(drivers: List[DriverInfo]) -> Dict[str, List[DriverInfo]]:
    """Two installed driver packages both claiming the same hardware ID is
    a real, if uncommon, source of instability (a generic driver and an
    OEM one both bound to the same device). Groups by `hardware_id`,
    excluding devices with no hardware_id at all (nothing to compare) and
    excluding groups of exactly one (the normal case)."""
    by_id: Dict[str, List[DriverInfo]] = {}
    for d in drivers:
        if not d.hardware_id:
            continue
        by_id.setdefault(d.hardware_id, []).append(d)
    return {hwid: group for hwid, group in by_id.items() if len(group) > 1}
```

Add `Dict` to the existing `from typing import List, Optional` import line.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -k duplicate_hardware -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/driver_reader.py tests/test_driver_reader.py
git commit -m "feat(driver manager): detect driver packages sharing a hardware ID

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Driver-store package size (reused from Cleanup's existing sizing code)

**Files:**
- Modify: `src/modules/driver_manager/driver_reader.py`
- Test: `tests/test_driver_reader.py`

**Interfaces:**
- Consumes: `cleanup.cleanup_scanner.driver_store.DriverPackage`, `.store_folder_for(published: str) -> Optional[str]`, `.file_repository() -> str`, `.package_size(package: DriverPackage) -> Optional[int]` (all existing, real, confirmed).
- Consumes: `published_name_for` (existing).
- Produces: `driver_store_size(driver: DriverInfo) -> Optional[int]`.

Real, confirmed cost: `package_size()` calls `get_dir_size(path)`, a real
filesystem walk — not a subprocess call, but not free either for a
package with many files. This function is called LAZILY (only when a
detail dialog opens for one specific device — Task 10), never for every
row on every refresh, matching the exact lesson already learned twice in
this codebase (Store Apps' Task 27, Debloat's still-open per-row `dir_size`
gap) about per-row synchronous I/O in a populate loop.

- [ ] **Step 1: Write the failing tests**

```python
def test_driver_store_size_returns_none_for_an_inbox_driver():
    d = DriverInfo(device_name="X", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0,
                   flags="", inf_name="usb.inf")  # inbox, no oem number
    assert dr.driver_store_size(d) is None


def test_driver_store_size_delegates_to_the_cleanup_scanners_sizing_code(monkeypatch):
    d = DriverInfo(device_name="X", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0,
                   flags="", inf_name="oem42.inf")
    from modules.cleanup.cleanup_scanner import driver_store as ds
    captured = {}
    def fake_package_size(package):
        captured["published"] = package.published
        return 12345
    monkeypatch.setattr(ds, "package_size", fake_package_size)
    assert dr.driver_store_size(d) == 12345
    assert captured["published"] == "oem42.inf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -k driver_store_size -v`
Expected: FAIL — `AttributeError: module 'driver_reader' has no attribute 'driver_store_size'`.

- [ ] **Step 3: Implement**

Add to `driver_reader.py`:
```python
def driver_store_size(driver: DriverInfo) -> Optional[int]:
    """Bytes this device's driver package occupies in the store, or None
    if it isn't an OEM-published package (nothing in the store to size)
    or the size genuinely can't be determined (see driver_store.package_size's
    own docstring -- None, never 0, when unmeasurable). Call this lazily,
    per device on demand, never for every row on every refresh -- it walks
    a real folder on disk."""
    published = published_name_for(driver.inf_name)
    if not published:
        return None
    from modules.cleanup.cleanup_scanner.driver_store import (
        DriverPackage, package_size,
    )
    import datetime as _dt
    package = DriverPackage(
        published=published, original=driver.inf_name, provider=driver.publisher,
        class_guid="", version=(0,), date=_dt.date.today(),
    )
    return package_size(package)
```

(The `DriverPackage` fields beyond `published` are unused by
`store_folder_for`/`package_size`'s actual logic — confirm this against
the real function bodies before finalizing; `store_folder_for` reads only
`.published`. If `package_size` or anything it calls turns out to touch
another field, populate it with the real value from `driver` instead of
a placeholder.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -k driver_store_size -v`
Expected: PASS.

- [ ] **Step 5: Run the full driver_reader suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -v`

```bash
git add src/modules/driver_manager/driver_reader.py tests/test_driver_reader.py
git commit -m "feat(driver manager): compute a device's driver-store package size on demand

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `driver_diagnostics.py` — Reliability cross-reference + suggested actions

**Files:**
- Create: `src/modules/driver_manager/driver_diagnostics.py`
- Test: `tests/test_driver_diagnostics.py`

**Interfaces:**
- Consumes: `core.types.LogEntry` — real, confirmed shape (`timestamp:
  datetime`, `source: str`, `level: str`, `message: str`, `raw: dict =
  field(default_factory=dict)`). `reliability_reader.read_reliability_records()`
  populates `raw["product_name"]` from WMI's `ProductName` field (confirmed
  by reading that function's body) — there is NO top-level `product_name`
  attribute on `LogEntry` itself, only inside `raw`. `message` itself also
  often already contains the product name verbatim (the reader falls back
  to it when there's no separate message text), so matching checks BOTH
  `raw.get("product_name", "")` and `message` for the device name
  substring.
- Produces: `crashes_for(device_name: str, records: List[LogEntry]) -> List[LogEntry]`, `suggested_action(error_code: int) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_driver_diagnostics.py
import datetime

from core.types import LogEntry
from modules.driver_manager import driver_diagnostics as dd


def _entry(product_name="", message=""):
    return LogEntry(
        timestamp=datetime.datetime.now(), source="Reliability",
        level="Error", message=message,
        raw={"product_name": product_name},
    )


def test_crashes_for_matches_by_substring_in_product_name():
    records = [
        _entry(product_name="AMD Radeon RX 7900 XTX", message="Driver crashed"),
        _entry(product_name="Realtek Audio", message="unrelated"),
    ]
    result = dd.crashes_for("AMD Radeon RX 7900 XTX", records)
    assert len(result) == 1
    assert result[0].message == "Driver crashed"


def test_crashes_for_also_matches_when_the_name_is_only_in_the_message():
    # reliability_reader falls back to product_name AS the message when
    # there's no separate WMI Message text -- so a record with an empty
    # raw["product_name"] but the device name in `message` must still match.
    records = [_entry(product_name="", message="AMD Radeon RX 7900 XTX")]
    assert len(dd.crashes_for("AMD Radeon RX 7900 XTX", records)) == 1


def test_crashes_for_returns_empty_when_nothing_matches():
    assert dd.crashes_for("Nonexistent Device", [_entry(product_name="Other")]) == []


def test_every_suggested_action_is_a_real_sentence():
    from modules.driver_manager.driver_reader import _ERROR_CODE_MEANINGS
    for code in _ERROR_CODE_MEANINGS:
        action = dd.suggested_action(code)
        assert isinstance(action, str) and len(action) > 10


def test_suggested_action_for_an_unknown_code_still_says_something():
    assert len(dd.suggested_action(99999)) > 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_diagnostics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.driver_manager.driver_diagnostics'`.

- [ ] **Step 3: Implement**

```python
# src/modules/driver_manager/driver_diagnostics.py
"""Cross-references Driver Manager's device list against Reliability
Monitor, and pairs each ConfigManagerErrorCode with a concrete next step.

Reliability Monitor records a device by free-text product/message fields,
not by hardware ID or device instance id -- there is no exact join
available. `crashes_for` is therefore substring matching, not an exact
match, and callers must present its result as approximate, not
authoritative.
"""
from typing import List

from core.types import LogEntry


def crashes_for(device_name: str, records: List[LogEntry]) -> List[LogEntry]:
    """Best-effort: checks both raw["product_name"] (the WMI ProductName
    field reliability_reader.py captures there) and `message` (which
    reliability_reader.py falls back to using AS the product name when
    WMI's own Message field is empty) for a substring match in either
    direction -- a truncated name on one side or the other is common in
    real Reliability Monitor data."""
    if not device_name:
        return []
    needle = device_name.lower()

    def _matches(text: str) -> bool:
        text = (text or "").lower()
        return bool(text) and (needle in text or text in needle)

    return [
        r for r in records
        if _matches(r.raw.get("product_name", "")) or _matches(r.message)
    ]


_SUGGESTED_ACTIONS = {
    1: "Reinstall the driver, or check for an update.",
    3: "Free up memory or reinstall the driver -- it may be corrupted.",
    10: "Try updating the driver; if that doesn't help, roll it back.",
    18: "Reinstall the driver for this device.",
    19: "The registry entry for this driver may be corrupted -- reinstall it.",
    21: "Wait for Windows to finish removing this device, then reconnect it.",
    22: "Re-enable this device in Device Manager if it should be active.",
    24: "Check for a driver update, or reinstall the driver.",
    28: "No driver is installed for this device -- check for an update.",
    31: "Update or reinstall the driver -- Windows can't load it as-is.",
    32: "A driver for this device was disabled -- re-enable or reinstall it.",
    37: "Reinstall the driver -- Windows can't initialize it.",
    39: "The driver may be corrupted or missing -- reinstall it.",
    43: "Check Device Manager's error details, then update or reinstall the driver.",
}


def suggested_action(error_code: int) -> str:
    return _SUGGESTED_ACTIONS.get(
        error_code,
        "No specific guidance for this error code -- check Device Manager "
        "for details, or try updating the driver.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_diagnostics.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/driver_diagnostics.py tests/test_driver_diagnostics.py
git commit -m "feat(driver manager): Reliability Monitor cross-reference and suggested actions per error code

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `driver_baselines.py` — named snapshots + diff

**Files:**
- Create: `src/modules/driver_manager/driver_baselines.py`
- Test: `tests/test_driver_baselines.py`

**Interfaces:**
- Consumes: `DriverInfo` (all fields are primitives — `dataclasses.asdict()`/`DriverInfo(**d)` round-trip cleanly with no custom to-dict/from-dict needed, unlike `rsop_snapshot.py`'s more complex nested dataclasses).
- Produces: `save_baseline(name, drivers) -> None`, `list_baselines() -> List[BaselineMeta]`, `load_baseline(name) -> List[DriverInfo]`, `diff_against_baseline(baseline, current) -> DriverDiff`, `default_baseline_dir() -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_driver_baselines.py
import json
import os

from modules.driver_manager import driver_baselines as db
from modules.driver_manager.driver_reader import DriverInfo


def _driver(name, version="1.0", publisher="V"):
    return DriverInfo(device_name=name, driver_class="Net", version=version,
                      date="2020-01-01", publisher=publisher, signed=True,
                      error_code=0, flags="")


def test_save_list_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    drivers = [_driver("A"), _driver("B")]
    db.save_baseline("my-baseline", drivers)

    metas = db.list_baselines()
    assert len(metas) == 1
    assert metas[0].name == "my-baseline"
    assert metas[0].driver_count == 2

    loaded = db.load_baseline("my-baseline")
    assert len(loaded) == 2
    assert {d.device_name for d in loaded} == {"A", "B"}


def test_list_baselines_survives_a_corrupt_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    os.makedirs(tmp_path, exist_ok=True)
    with open(os.path.join(str(tmp_path), "broken.meta.json"), "w") as f:
        f.write("{not valid json")
    metas = db.list_baselines()
    assert len(metas) == 1
    assert metas[0].error != ""


def test_diff_against_baseline_finds_added_removed_and_changed():
    baseline = [_driver("Kept Same"), _driver("Removed Device"),
                _driver("Changed Device", version="1.0")]
    current = [_driver("Kept Same"), _driver("Added Device"),
               _driver("Changed Device", version="2.0")]
    diff = db.diff_against_baseline(baseline, current)
    assert [d.device_name for d in diff.added] == ["Added Device"]
    assert [d.device_name for d in diff.removed] == ["Removed Device"]
    assert len(diff.changed) == 1
    old, new = diff.changed[0]
    assert old.version == "1.0" and new.version == "2.0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_baselines.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/modules/driver_manager/driver_baselines.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_baselines.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/driver_baselines.py tests/test_driver_baselines.py
git commit -m "feat(driver manager): named driver-set baselines with diff

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: System Restore point listing (read-only)

**Files:**
- Modify: `src/modules/driver_manager/driver_reader.py`
- Test: `tests/test_driver_reader.py`

**Interfaces:**
- Produces: `list_restore_points() -> Optional[List[dict]]`.

This duplicates the real, working `Get-ComputerRestorePoint` PowerShell
call from `src/modules/restore_manager/restore_module.py`'s
`_load_restore_points` (read that method first to confirm nothing has
changed) as a small, self-contained ~15-line function rather than
importing from that module — `restore_module.py`'s version is a bound
method entangled with its own Worker/UI state, not designed as a library
call, and CLAUDE.md's own principle is "don't propose unrelated
refactoring, stay focused on what serves the current goal." Returns
`None` on a failed read (subprocess error, bad JSON), an empty list `[]`
only when the read genuinely succeeded and there are no restore points —
those are different, real answers.

- [ ] **Step 1: Write the failing tests**

```python
def test_list_restore_points_returns_none_on_a_failed_read(monkeypatch):
    def fake_run(cmd, **k):
        class R:
            returncode = 1
            stdout = ""
            stderr = "Access is denied."
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    assert dr.list_restore_points() is None


def test_list_restore_points_returns_empty_list_when_none_exist(monkeypatch):
    def fake_run(cmd, **k):
        class R:
            returncode = 0
            stdout = ""
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    assert dr.list_restore_points() == []


def test_list_restore_points_parses_real_shaped_output(monkeypatch):
    def fake_run(cmd, **k):
        class R:
            returncode = 0
            stdout = json.dumps({
                "SequenceNumber": 42, "Description": "Driver update",
                "RestorePointType": 12, "CreationTime": "20260101120000.000000-000",
            })
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    points = dr.list_restore_points()
    assert points == [{
        "SequenceNumber": 42, "Description": "Driver update",
        "RestorePointType": 12, "CreationTime": "20260101120000.000000-000",
    }]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -k restore_points -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

```python
_PS_CMD_RESTORE_POINTS = (
    "Get-ComputerRestorePoint | "
    "Select-Object SequenceNumber, Description, RestorePointType, CreationTime | "
    "ConvertTo-Json -Compress"
)


def list_restore_points() -> Optional[List[dict]]:
    """None on a failed read (refused, timed out, unparseable) -- an
    empty list is the real, different answer "System Restore is off, or
    no points exist yet". Never collapse the two."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _PS_CMD_RESTORE_POINTS],
            capture_output=True, text=True, errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=30,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Restore point query timed out")
        return None
    if proc.returncode != 0:
        logger.warning("Restore point query failed: %s", proc.stderr.strip())
        return None
    raw = proc.stdout.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse restore point list: %s", exc)
        return None
    return [data] if isinstance(data, dict) else data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py -k restore_points -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/driver_reader.py tests/test_driver_reader.py
git commit -m "feat(driver manager): read-only System Restore point listing

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: "Copy Hardware ID" + "Why does this matter?" context-menu actions

**Files:**
- Modify: `src/modules/driver_manager/driver_module.py`
- Test: `tests/test_driver_module.py`

**Interfaces:**
- Consumes: `DriverInfo.hardware_id` (Task 1), `_row_dedup_key`/`_dedup_key` (existing).

`_on_context_menu` already resolves the clicked row to its real `DriverInfo`
via `driver` (see the existing body) — both new actions are added right
after the existing `act_copy`/`act_export_one` actions, reusing that same
`driver` lookup rather than re-resolving it.

- [ ] **Step 1: Write the failing tests**

```python
def test_copy_hardware_id_puts_the_devices_hardware_id_on_the_clipboard(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0,
                   flags="", hardware_id="PCI\\VEN_1234"),
    ]
    mod._populate(mod._drivers_ref[0], "")
    copied = []
    monkeypatch.setattr(dmod.QApplication, "clipboard",
                        lambda: type("C", (), {"setText": lambda self, t: copied.append(t)})())
    mod._copy_hardware_id("PCI\\VEN_1234")
    assert copied == ["PCI\\VEN_1234"]


def test_why_does_this_matter_explains_each_flag_present():
    mod = _module()
    text = mod._explain_flags("🔴 Unsigned (as reported by Windows) 🟠 Old")
    assert "unsigned" in text.lower()
    assert "old" in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "copy_hardware_id or why_does" -v`
Expected: FAIL — `AttributeError: 'DriverModule' object has no attribute '_copy_hardware_id'`.

- [ ] **Step 3: Implement**

In `_on_context_menu`, after the existing `act_export_one` block and
before `act_rollback`, add:
```python
        act_copy_hwid = menu.addAction("Copy Hardware ID")
        act_copy_hwid.setEnabled(bool(driver and driver.hardware_id))
        act_copy_hwid.triggered.connect(
            lambda: self._copy_hardware_id(driver.hardware_id if driver else ""))
```

After `act_cleanup` (before `menu.exec(...)`), add:
```python
        if driver and driver.flags.strip():
            act_explain = menu.addAction("Why does this matter?")
            act_explain.triggered.connect(
                lambda: self._show_flag_explanation(driver.flags))
```

New methods, near `_export_one_driver`:
```python
    def _copy_hardware_id(self, hardware_id: str) -> None:
        if hardware_id:
            QApplication.clipboard().setText(hardware_id)

    _FLAG_EXPLANATIONS = {
        "Unsigned": "This driver's publisher could not be verified by "
                    "Windows. It may still work fine, but an unsigned "
                    "driver is a common vector for malware pretending to "
                    "be a hardware driver.",
        "Error": "Windows reports a problem with this device right now -- "
                "see the exact error text in the Status column for what "
                "it is.",
        "Old": "This driver has not been updated in over two years. Many "
              "devices work fine on an old driver forever, but graphics, "
              "network and storage controllers benefit most from staying "
              "current.",
        "date unreadable": "Windows reported a driver date this app could "
                          "not parse -- not necessarily a problem, just "
                          "an unusual value.",
        "No driver installed": "Windows found this device but has no "
                              "driver for it at all -- it will not work "
                              "until one is installed.",
    }

    def _explain_flags(self, flags: str) -> str:
        matched = [text for key, text in self._FLAG_EXPLANATIONS.items()
                  if key in flags]
        return "\n\n".join(matched) if matched else (
            "This driver has a flag this app doesn't have an explanation for yet.")

    def _show_flag_explanation(self, flags: str) -> None:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(self._widget, "Why does this matter?",
                                self._explain_flags(flags))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "copy_hardware_id or why_does" -v`
Expected: PASS.

- [ ] **Step 5: Run the full driver_module suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -v`

```bash
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): copy hardware ID and explain flags from the context menu

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Full inventory export (CSV + HTML)

**Files:**
- Modify: `src/modules/driver_manager/driver_module.py`
- Test: `tests/test_driver_module.py`

**Interfaces:**
- Distinct from the existing `_do_export` (which respects the current filter/visible rows) — this exports the FULL current driver list (`self._drivers_ref[0]`, unfiltered) with the full field set including the new Task 1/2/3 fields.

- [ ] **Step 1: Write the failing tests**

```python
def test_export_inventory_csv_includes_every_driver_and_new_fields(tmp_path, monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0,
                   flags="", hardware_id="PCI\\VEN_1", whql_certified=True),
        DriverInfo(device_name="B", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=False, error_code=0, flags=""),
    ]
    out = tmp_path / "inventory.csv"
    monkeypatch.setattr(dmod.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    mod._export_inventory()
    text = out.read_text(encoding="utf-8")
    assert "A" in text and "B" in text
    assert "PCI\\VEN_1" in text
    assert "True" in text  # whql_certified for A


def test_export_inventory_html_produces_a_table(tmp_path, monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    out = tmp_path / "inventory.html"
    monkeypatch.setattr(dmod.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), "HTML (*.html)"))
    mod._export_inventory()
    text = out.read_text(encoding="utf-8")
    assert "<table" in text and "A" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k export_inventory -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Add `_INVENTORY_COLUMNS` near the existing `COLUMNS` constant:
```python
_INVENTORY_COLUMNS = [
    "Device Name", "Class", "Version", "Date", "Publisher", "Signed",
    "WHQL Certified", "Error Code", "Hardware ID", "INF Name", "Flags",
]


def _inventory_row(d: DriverInfo) -> list:
    return [d.device_name, d.driver_class, d.version, d.date, d.publisher,
            d.signed, d.whql_certified, d.error_code, d.hardware_id,
            d.inf_name, d.flags]
```

New method on `DriverModule`, and a new toolbar button wired in
`create_widget` right after the existing `self._export_btn`:
```python
        self._export_inventory_btn = QPushButton("Export Inventory")
        toolbar.addWidget(self._export_inventory_btn)
```
(add `self._export_inventory_btn: Optional[QPushButton] = None` to
`__init__` alongside the other button fields, and
`self._export_inventory_btn.clicked.connect(self._export_inventory)`
alongside the other `.clicked.connect` calls)

```python
    def _export_inventory(self) -> None:
        if self._widget is None:
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self._widget, "Export Driver Inventory", "driver_inventory.csv",
            "CSV (*.csv);;HTML (*.html)")
        if not path:
            return
        drivers = self._drivers_ref[0]
        if path.lower().endswith(".html") or "HTML" in selected_filter:
            self._write_inventory_html(path, drivers)
        else:
            self._write_inventory_csv(path, drivers)
        if self._status_lbl:
            self._status_lbl.setText(f"Exported inventory to {os.path.basename(path)}")

    def _write_inventory_csv(self, path: str, drivers: List[DriverInfo]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_INVENTORY_COLUMNS)
            for d in drivers:
                writer.writerow(_inventory_row(d))

    def _write_inventory_html(self, path: str, drivers: List[DriverInfo]) -> None:
        rows_html = []
        for d in drivers:
            cells = "".join(f"<td>{_html_escape(str(v))}</td>" for v in _inventory_row(d))
            rows_html.append(f"<tr>{cells}</tr>")
        header_html = "".join(f"<th>{_html_escape(c)}</th>" for c in _INVENTORY_COLUMNS)
        html = (
            "<html><head><meta charset='utf-8'><title>Driver Inventory</title></head>"
            "<body><table border='1' cellspacing='0' cellpadding='4'>"
            f"<tr>{header_html}</tr>{''.join(rows_html)}</table></body></html>"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
```

Add a small escape helper near the top of the file (no new dependency —
`html.escape` from the stdlib):
```python
from html import escape as _html_escape
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k export_inventory -v`
Expected: PASS.

- [ ] **Step 5: Run the full driver_module suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -v`

```bash
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): export the full driver inventory as CSV or HTML

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: "Snapshots" menu — save baseline / diff against one

**Files:**
- Modify: `src/modules/driver_manager/driver_module.py`
- Test: `tests/test_driver_module.py`

**Interfaces:**
- Consumes: `driver_baselines.save_baseline`, `.list_baselines`, `.load_baseline`, `.diff_against_baseline` (Task 5).

- [ ] **Step 1: Write the failing tests**

```python
def test_save_baseline_action_calls_save_baseline_with_current_drivers(monkeypatch, tmp_path):
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="A", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    monkeypatch.setattr(dmod, "_ask_baseline_name", lambda parent: "session-1")
    mod._save_baseline_action()
    assert [m.name for m in db.list_baselines()] == ["session-1"]


def test_diff_against_baseline_action_shows_a_summary(monkeypatch, tmp_path):
    from modules.driver_manager import driver_baselines as db
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    db.save_baseline("old", [
        DriverInfo(device_name="Removed", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ])
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="Added", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
    ]
    shown = []
    monkeypatch.setattr(dmod, "_choose_baseline", lambda parent, metas: "old")
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a))
    mod._diff_against_baseline_action()
    assert shown
    assert "Added" in shown[0][2] and "Removed" in shown[0][2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "save_baseline_action or diff_against_baseline_action" -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Add near the top of `driver_module.py` (module-level, so tests can
monkeypatch them without touching Qt dialog internals):
```python
def _ask_baseline_name(parent) -> Optional[str]:
    from PyQt6.QtWidgets import QInputDialog
    name, ok = QInputDialog.getText(parent, "Save Baseline", "Baseline name:")
    return name.strip() if ok and name.strip() else None


def _choose_baseline(parent, metas: list) -> Optional[str]:
    from PyQt6.QtWidgets import QInputDialog
    if not metas:
        return None
    names = [m.name for m in metas]
    choice, ok = QInputDialog.getItem(
        parent, "Diff Against Baseline", "Baseline:", names, 0, False)
    return choice if ok else None
```

In `create_widget`, add a "Snapshots" menu button after the existing
`self._export_inventory_btn` (Task 8):
```python
        snapshots_btn = QPushButton("Snapshots ▾")
        snapshots_menu = QMenu(snapshots_btn)
        snapshots_menu.addAction("Save Baseline...", self._save_baseline_action)
        snapshots_menu.addAction("Diff Against...", self._diff_against_baseline_action)
        snapshots_btn.setMenu(snapshots_menu)
        toolbar.addWidget(snapshots_btn)
```

New methods:
```python
    def _save_baseline_action(self) -> None:
        name = _ask_baseline_name(self._widget)
        if not name:
            return
        from modules.driver_manager import driver_baselines as db
        db.save_baseline(name, self._drivers_ref[0])
        if self._status_lbl:
            self._status_lbl.setText(f"Saved baseline '{name}'.")

    def _diff_against_baseline_action(self) -> None:
        from modules.driver_manager import driver_baselines as db
        from PyQt6.QtWidgets import QMessageBox
        metas = db.list_baselines()
        name = _choose_baseline(self._widget, metas)
        if not name:
            return
        baseline = db.load_baseline(name)
        diff = db.diff_against_baseline(baseline, self._drivers_ref[0])
        lines = [f"Added ({len(diff.added)}):"] + [f"  + {d.device_name}" for d in diff.added]
        lines += [f"Removed ({len(diff.removed)}):"] + [f"  - {d.device_name}" for d in diff.removed]
        lines += [f"Changed ({len(diff.changed)}):"] + [
            f"  ~ {old.device_name}: {old.version} -> {new.version}"
            for old, new in diff.changed]
        QMessageBox.information(self._widget, f"Diff against '{name}'", "\n".join(lines))
```

Add `from PyQt6.QtWidgets import QMessageBox` to the top-level import
block (Task 7's `_show_flag_explanation` did an inline import inside the
method since it was the only user at the time — now that this task adds a
second call site, promote it to the top-level import list and delete
Task 7's now-redundant inline `from PyQt6.QtWidgets import QMessageBox`
line inside `_show_flag_explanation`, since both would otherwise resolve
to the same class with one now dead).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "save_baseline_action or diff_against_baseline_action" -v`
Expected: PASS.

- [ ] **Step 5: Run the full driver_module suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -v`

```bash
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): save and diff named driver baselines from the toolbar

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: `DriverDetailDialog` — per-device detail view

**Files:**
- Create: `src/modules/driver_manager/driver_detail_dialog.py`
- Test: `tests/test_driver_detail_dialog.py`
- Modify: `src/modules/driver_manager/driver_module.py` (wire double-click + a "Details..." context-menu action)

**Interfaces:**
- Consumes: `DriverInfo` (all fields), `driver_diagnostics.crashes_for`/`suggested_action` (Task 4), `driver_reader.driver_store_size` (Task 3, called lazily — only when this dialog opens).
- Does NOT consume `list_restore_points` (Task 6) — restore points are a system-wide, not a per-device, concept; they get their own toolbar action and dialog in Task 12, not a slot in this per-device view.
- Reuses `ProcessPropertiesDialog`'s `_row(label, value) -> QWidget` pattern (a `QHBoxLayout` with a bold fixed-width label and a `TextSelectableByMouse` value label) — read `src/modules/process_explorer/properties_dialog.py`'s real `_row` method first and match it exactly, not a reinvention.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_driver_detail_dialog.py
import pytest

from modules.driver_manager.driver_detail_dialog import DriverDetailDialog
from modules.driver_manager.driver_reader import DriverInfo


def _driver():
    return DriverInfo(
        device_name="AMD Radeon RX 7900 XTX", driver_class="Display",
        version="31.0.1", date="2026-06-01", publisher="Advanced Micro Devices",
        signed=True, error_code=0, flags="", inf_name="oem12.inf",
        device_id="PCI\\VEN_1002&DEV_744C\\4&abc", hardware_id="PCI\\VEN_1002&DEV_744C",
        whql_certified=True,
    )


def test_dialog_shows_the_devices_own_fields(qapp, monkeypatch):
    monkeypatch.setattr(
        "modules.driver_manager.driver_detail_dialog.driver_store_size",
        lambda d: 1234567)
    monkeypatch.setattr(
        "modules.driver_manager.driver_detail_dialog.crashes_for",
        lambda name, records: [])
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    text = dlg.findChild(type(dlg)).__class__  # placeholder to force a read below
    all_text = " ".join(
        w.text() for w in dlg.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)
        if hasattr(w, "text"))
    assert "AMD Radeon RX 7900 XTX" in all_text
    assert "PCI\\VEN_1002&DEV_744C" in all_text
    assert "oem12.inf" in all_text


def test_dialog_shows_whql_yes_or_no(qapp):
    dlg = DriverDetailDialog(_driver(), reliability_records=[])
    labels = [w.text() for w in dlg.findChildren(
        __import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert any("WHQL" in t for t in labels)
```

(The `qapp` fixture is this test suite's existing shared `QApplication`
fixture — confirm its real name in `tests/conftest.py` before using it;
substitute the real fixture name if different.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_detail_dialog.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/modules/driver_manager/driver_detail_dialog.py
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QDialogButtonBox,
    QTextEdit,
)

from core.types import LogEntry
from modules.driver_manager.driver_diagnostics import crashes_for, suggested_action
from modules.driver_manager.driver_reader import DriverInfo, driver_store_size


def _row(label: str, value: str) -> QWidget:
    # Matches process_explorer/properties_dialog.py's _row helper exactly --
    # a bold fixed-width label plus a selectable value, the established
    # convention for a details view in this codebase.
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(f"<b>{label}:</b>")
    lbl.setFixedWidth(140)
    val = QLabel(value)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    val.setWordWrap(True)
    h.addWidget(lbl)
    h.addWidget(val, 1)
    return w


class DriverDetailDialog(QDialog):
    def __init__(self, driver: DriverInfo,
                reliability_records: Optional[List[LogEntry]] = None,
                parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Driver Details — {driver.device_name}")
        self.resize(560, 480)
        layout = QVBoxLayout(self)

        size = driver_store_size(driver)
        size_text = f"{size:,} bytes" if size is not None else "Unknown"

        for label, value in [
            ("Device Name", driver.device_name),
            ("Class", driver.driver_class),
            ("Version", driver.version or "Unknown"),
            ("Date", driver.date or "Unknown"),
            ("Publisher", driver.publisher or "Unknown"),
            ("Signed", "Yes" if driver.signed else "No"),
            ("WHQL Certified", "Yes" if driver.whql_certified else "No"),
            ("Error Code", str(driver.error_code) if driver.error_code else "None"),
            ("Hardware ID", driver.hardware_id or "Unknown"),
            ("INF Name", driver.inf_name or "Unknown"),
            ("Driver Store Size", size_text),
            ("Flags", driver.flags or "None"),
        ]:
            layout.addWidget(_row(label, value))

        layout.addWidget(QLabel("<b>Recent Reliability Monitor entries "
                                "(approximate match by device name):</b>"))
        crashes = crashes_for(driver.device_name, reliability_records or [])
        crashes_view = QTextEdit()
        crashes_view.setReadOnly(True)
        if crashes:
            crashes_view.setPlainText("\n".join(
                f"{getattr(c, 'timestamp', '')}: {getattr(c, 'message', '')}"
                for c in crashes))
        else:
            crashes_view.setPlainText("No matching entries found.")
        crashes_view.setMaximumHeight(100)
        layout.addWidget(crashes_view)

        if driver.error_code:
            layout.addWidget(_row("Suggested action", suggested_action(driver.error_code)))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
```

In `driver_module.py`, add a "Details..." context-menu action (in
`_on_context_menu`, right after `act_copy`):
```python
        act_details = menu.addAction("Details...")
        act_details.setEnabled(bool(driver))
        act_details.triggered.connect(lambda: self._show_driver_details(driver))
```

New method:
```python
    def _show_driver_details(self, driver: Optional[DriverInfo]) -> None:
        if driver is None:
            return
        from modules.driver_manager.driver_detail_dialog import DriverDetailDialog
        dlg = DriverDetailDialog(driver, reliability_records=[], parent=self._widget)
        dlg.exec()
```

(Note: this task deliberately does NOT fetch real Reliability records
synchronously on the UI thread when the dialog opens — that is a real WMI
call. Wiring an actual background fetch is a reasonable follow-up
improvement to make during testing per the "keep adding improvements...
as you see fit" instruction, using the same `Worker`/`COMWorker` pattern
`_do_refresh` already establishes; this task ships the dialog with an
empty reliability list as a correct, honest baseline rather than blocking
the UI, and the empty case is explicitly tested in Step 1.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_detail_dialog.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full driver_manager test suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py tests/test_driver_module.py tests/test_driver_diagnostics.py tests/test_driver_baselines.py tests/test_driver_detail_dialog.py -v`

```bash
git add src/modules/driver_manager/driver_detail_dialog.py src/modules/driver_manager/driver_module.py tests/test_driver_detail_dialog.py
git commit -m "feat(driver manager): per-device detail dialog

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Dashboard "N driver problems" tile

**Files:**
- Modify: `src/modules/dashboard/dashboard_module.py`
- Test: `tests/test_dashboard_module.py` (check whether this file already exists — if so, add to it; if not, create it following whatever test file already covers `OverviewModule`, e.g. check for `tests/test_dashboard*.py` first)

**Interfaces:**
- Consumes: `self.app.module_registry.modules` (existing `ModuleRegistry.modules` property — confirmed it returns `List[BaseModule]`, no `get_module(name)` helper exists) to find the live `DriverModule` instance by `m.name == "Driver Manager"`, then reads its `_drivers_ref[0]` the same live-handle way `DriverSearchProvider` already does.

- [ ] **Step 1: Locate the real test file and read `_DashboardWidget`'s current card layout in full**

Run: `grep -rn "class _DashboardWidget" -A 5 src/modules/dashboard/dashboard_module.py` and read the WHOLE class body (it was only partially shown during planning) before writing the new card — confirm the exact grid row/column the last existing card occupies, so the new one doesn't overlap.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_dashboard_module.py (add to existing file, or create if none exists)
from modules.dashboard.dashboard_module import _driver_problem_count
from modules.driver_manager.driver_reader import DriverInfo


class _FakeDriverModule:
    name = "Driver Manager"
    def __init__(self, drivers):
        self._drivers_ref = [drivers]


class _FakeRegistry:
    def __init__(self, modules):
        self.modules = modules


class _FakeApp:
    def __init__(self, modules):
        self.module_registry = _FakeRegistry(modules)


def test_driver_problem_count_counts_unsigned_and_errored_devices():
    drivers = [
        DriverInfo(device_name="OK", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=0, flags=""),
        DriverInfo(device_name="Unsigned", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=False, error_code=0, flags=""),
        DriverInfo(device_name="Errored", driver_class="Net", version="1.0",
                   date="", publisher="V", signed=True, error_code=28, flags=""),
    ]
    app = _FakeApp([_FakeDriverModule(drivers)])
    assert _driver_problem_count(app) == 2


def test_driver_problem_count_is_zero_when_driver_manager_not_found():
    app = _FakeApp([])
    assert _driver_problem_count(app) == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_module.py -k driver_problem_count -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 4: Implement**

Add near the top of `dashboard_module.py` (module level, easily
unit-tested without building a real `_DashboardWidget`):
```python
def _driver_problem_count(app) -> int:
    """Reads Driver Manager's live, already-fetched driver list (the same
    _drivers_ref cell DriverSearchProvider already holds a live handle
    to -- see driver_module.py's get_search_provider) without triggering
    a new scan. 0 if Driver Manager hasn't run yet, or isn't registered
    (e.g. in a test harness) -- not an error, just nothing to report."""
    if app is None or getattr(app, "module_registry", None) is None:
        return 0
    driver_module = next(
        (m for m in app.module_registry.modules if m.name == "Driver Manager"),
        None)
    if driver_module is None:
        return 0
    drivers = driver_module._drivers_ref[0]
    return sum(1 for d in drivers if d.error_code != 0 or not d.signed)
```

In `_DashboardWidget._setup_ui`, after whichever card is currently last
(confirmed in Step 1 — do not guess the row/column), add:
```python
        self._driver_card = _Card("Driver Health")
        self._driver_problems_lbl = QLabel("—")
        self._driver_card.body().addWidget(self._driver_problems_lbl)
        grid.addWidget(self._driver_card, <NEXT_ROW>, <NEXT_COL>)
```
(Fill in `<NEXT_ROW>`/`<NEXT_COL>` from the real layout read in Step 1 —
this plan does not guess the grid's current occupancy.)

In `_DashboardWidget._refresh` (find its real body — read it before
editing), add one line updating the new label:
```python
        count = _driver_problem_count(self.app)
        self._driver_problems_lbl.setText(
            f"{count} driver(s) need attention" if count else "No driver problems detected")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_module.py -k driver_problem_count -v`
Expected: PASS.

- [ ] **Step 6: Run the full dashboard suite, then the full project suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_dashboard_module.py -v`
Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: only the two pre-existing, documented `test_procengine_gpuinfo.py` failures.

```bash
git add src/modules/dashboard/dashboard_module.py tests/test_dashboard_module.py
git commit -m "feat(dashboard): show a driver-problem count tile

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Surface System Restore points in the tab

**Files:**
- Modify: `src/modules/driver_manager/driver_module.py`
- Test: `tests/test_driver_module.py`

**Interfaces:**
- Consumes: `driver_reader.list_restore_points() -> Optional[List[dict]]` (Task 6).

Restore points are system-wide, not tied to one device, so this is a
toolbar action opening a small standalone dialog (a plain list, not a full
`QDialog` subclass file of its own — this is small enough to build inline
in `driver_module.py`, unlike `DriverDetailDialog`'s richer per-device
view), rather than a slot in Task 10's per-device dialog.

- [ ] **Step 1: Write the failing tests**

```python
def test_show_restore_points_lists_them_by_creation_time_descending(monkeypatch):
    mod = _module()
    monkeypatch.setattr(
        dmod, "list_restore_points",
        lambda: [
            {"SequenceNumber": 1, "Description": "Older", "CreationTime": "20260101000000.000000-000"},
            {"SequenceNumber": 2, "Description": "Newer", "CreationTime": "20260201000000.000000-000"},
        ])
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._show_restore_points()
    assert shown
    assert shown[0].index("Newer") < shown[0].index("Older")


def test_show_restore_points_explains_a_failed_read(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dmod, "list_restore_points", lambda: None)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._show_restore_points()
    assert "could not" in shown[0].lower()


def test_show_restore_points_explains_an_empty_list(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dmod, "list_restore_points", lambda: [])
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._show_restore_points()
    assert "no restore points" in shown[0].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k restore_points -v`
Expected: FAIL — `AttributeError: 'DriverModule' object has no attribute '_show_restore_points'`.

- [ ] **Step 3: Implement**

Add to the existing `from modules.driver_manager.driver_reader import
(...)` block in `driver_module.py`: `list_restore_points`.

Add a toolbar button in `create_widget`, after the existing
`snapshots_btn` (Task 9):
```python
        restore_points_btn = QPushButton("System Restore Points")
        toolbar.addWidget(restore_points_btn)
```
and wire it: `restore_points_btn.clicked.connect(self._show_restore_points)`

New method:
```python
    def _show_restore_points(self) -> None:
        points = list_restore_points()
        if points is None:
            QMessageBox.information(
                self._widget, "System Restore Points",
                "Could not read System Restore points -- this may need "
                "administrator rights, or System Restore may be off.")
            return
        if not points:
            QMessageBox.information(
                self._widget, "System Restore Points",
                "No restore points found.")
            return
        # Newest first -- CreationTime is a WMI datetime string
        # ("20260201000000.000000-000"), which sorts correctly as plain
        # text since it's already zero-padded, fixed-width, and
        # year-first, without needing to parse it into a real datetime.
        ordered = sorted(points, key=lambda p: p.get("CreationTime", ""), reverse=True)
        lines = [
            f"{p.get('CreationTime', 'Unknown time')}: {p.get('Description', '')}"
            for p in ordered
        ]
        QMessageBox.information(self._widget, "System Restore Points", "\n".join(lines))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k restore_points -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full driver_module suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -v`

```bash
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): surface System Restore points in the tab

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Final Verification

After Task 12, run the full suite once more and confirm the driver-manager
and dashboard test files all pass together (not just individually — a
shared-state bug between them would only show up running the whole set):

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: same two pre-existing GPU-hardware failures, nothing else.

Then follow this plan's standard close-out: a final whole-branch review
(per `superpowers:subagent-driven-development`'s Final Review section),
fix any Critical/Important findings, and hand off via
`superpowers:finishing-a-development-branch` — merge `feat/driver-manager-phase0`
back to master once green.
