# Driver Manager Phase 0 — Read-Only Enrichments

**Status:** Approved design, pending spec review
**Author:** Claude (design session with user, 2026-09-09)
**Scope:** Phase 0 of a 6-phase Driver Manager improvement program (0, 0b, 1, 2, 3 follow separately, each with its own spec)

## Context

Driver Manager (`src/modules/driver_manager/`) currently reads, exports, and
uninstalls drivers. This is the first of six planned phases adding a driver
auto-update feature plus general tab improvements. Phase 0 covers everything
that is **read-only or near-read-only, has zero dependency on the update
feature's architecture (Phases 1-3), and touches no driver install/removal
state**. It is intentionally the lowest-risk phase, chosen to ship first.

Later phases (not this spec):
- **0b** — unused/orphaned driver cleanup (a real scanner + a write action)
- **1** — update foundation, INF-minimal install, rollback, bulk driver
  restore, per-device power management toggle (the first phase with real
  writes, building shared "restore point → write → verify" infrastructure)
- **2** — full-installer update path, vendor adapters, bulk "Update All",
  update pinning, Updates-module Run-All stage integration
- **3** — generic search chain (paid API → scrape → driver-DB fallback),
  background scanning, Dashboard "N updates available"

## Goals

1. Reliability Monitor cross-reference per device, with a suggested next
   action per `ConfigManagerErrorCode`.
2. A per-device detail dialog (hardware IDs, driver files, associated
   services, install date, driver-store package size).
3. Distinguish WHQL-certified from merely-signed, if reliably determinable
   (see Open Question below — may ship as "not determinable" for now).
4. Detect driver packages sharing a hardware ID (a real, if uncommon, source
   of device instability).
5. "Why does this matter?" explainer for each flag (Old / Unsigned / Error).
6. "Copy Hardware ID" context-menu action, as a manual-lookup escape hatch.
7. Full inventory export (CSV/HTML), distinct from the existing per-driver
   CSV export.
8. Named baseline snapshots + diff against one (added/removed/changed).
9. Surface relevant System Restore points directly in the tab.
10. A small "N driver problems" tile on the Dashboard module, based on
    existing flag data (unsigned/error/old counts) — no new detection.
11. Driver-store package size, reusing Cleanup's existing sizing code.

## Non-goals (explicitly out of scope for Phase 0)

- Anything that installs, updates, uninstalls, or otherwise writes driver
  state (that starts at Phase 0b/1).
- Anything requiring network access (search, vendor lookups — Phase 3).
- "N updates available" on the Dashboard tile (needs Phase 1+ data;
  Phase 0's tile is limited to existing flag counts).

## Architecture

No new package. Three new Qt-free data-layer files alongside the existing
`driver_reader.py`, plus additions to `driver_reader.py` itself, plus UI
additions in `driver_module.py`:

```
src/modules/driver_manager/
  driver_reader.py          (existing — gains HardWareID field, duplicate-
                              hardware-ID detection, driver-store size lookup)
  driver_diagnostics.py     (new — Reliability cross-reference,
                              suggested-action map)
  driver_baselines.py       (new — named snapshot save/list/load/diff)
  driver_detail_dialog.py   (new — the per-device detail QDialog)
  driver_module.py          (existing — new toolbar/context-menu wiring)
```

Dashboard tile lives in the Dashboard module (`src/modules/dashboard/` —
confirm exact file during implementation; read that module fully first,
this spec does not assume its internal structure).

## Components

### `driver_reader.py` additions

- `_PS_CMD` gains `HardWareID = [string]$d.HardWareID` — `Win32_PnPSignedDriver`
  exposes this field; confirm during implementation that it returns a
  semicolon/array-joined string (WMI's `HardWareID` property is a string
  array) and decide how `DriverInfo` stores it (likely `hardware_id: str = ""`
  holding the first/primary id, since `DriverInfo` is otherwise flat strings
  — a device can have several hardware IDs in specificity order; the FIRST
  is the one Windows actually matched, so use that one unless testing shows
  otherwise).
- `detect_duplicate_hardware_ids(drivers: List[DriverInfo]) -> Dict[str, List[DriverInfo]]`
  — pure function, groups by `hardware_id`, returns only groups with 2+
  distinct `device_name` values sharing one id (excludes the normal case of
  one device correctly having one entry).
- `driver_store_size(driver: DriverInfo) -> Optional[int]` — bridges to
  `cleanup/cleanup_scanner/driver_store.py`'s existing `package_size()`,
  which takes a `DriverPackage` (keyed by `published`/oem-name) rather than
  this module's own `DriverInfo`. Build a minimal `DriverPackage` from
  `driver.inf_name` via `published_name_for()` (already exists) — for a
  non-OEM (inbox) driver, return `None` (nothing in the store to size).
  Reuses the existing, tested sizing logic rather than reimplementing the
  `HKLM\SYSTEM\DriverDatabase\DriverInfFiles\<published>` `Active` value
  read.
- WHQL vs. merely-signed: **open question, see below** — if determinable,
  a `whql_certified: Optional[bool]` field (`None` = "could not determine,"
  matching this codebase's "a refused read is never collapsed into a
  value" rule); if not reliably determinable from available WMI fields,
  ship Phase 0 without this item and note it in the ledger as declined
  with reasoning, rather than fake it with a heuristic that could mislabel
  a driver's actual trust level.

### `driver_diagnostics.py` (new)

```python
def crashes_for(device_name: str, records: List[LogEntry]) -> List[LogEntry]:
    """Best-effort match: Reliability Monitor records a device by free-text
    product/message fields, not hardware ID, so this is substring matching,
    not an exact join. Callers must present this as approximate in the UI."""

_SUGGESTED_ACTIONS: Dict[int, str]  # one sentence per ConfigManagerErrorCode,
                                     # alongside driver_reader._ERROR_CODE_MEANINGS
```

Calls `reliability_reader.read_reliability_records()` (existing, real,
`wmi`-based — confirmed function signature:
`read_reliability_records(max_records=1000, progress_callback=None) -> List[LogEntry]`).
Driver Manager calls this on-demand (e.g. when the detail dialog opens for
a device), not on every refresh — it is a real WMI query with its own cost,
and Reliability Monitor data does not change fast enough to warrant
re-reading it per driver-list refresh.

### `driver_baselines.py` (new)

Same shape as `gpresult/rsop_snapshot.py`: small JSON files under
`%APPDATA%/WindowsTweaker/driver_baselines/`, one per named baseline, with
a `.meta.json` sidecar (name, saved-at timestamp, driver count) so listing
never deserializes full payloads.

```python
def save_baseline(name: str, drivers: List[DriverInfo]) -> None
def list_baselines() -> List[BaselineMeta]
def load_baseline(name: str) -> List[DriverInfo]
def diff_against_baseline(baseline: List[DriverInfo], current: List[DriverInfo]) -> DriverDiff

@dataclass
class DriverDiff:
    added: List[DriverInfo]     # device_name in current, not in baseline
    removed: List[DriverInfo]   # device_name in baseline, not in current
    changed: List[Tuple[DriverInfo, DriverInfo]]  # (old, new) where version or publisher differs
```

Matching key for diff is `device_name` (baselines are a same-machine,
same-hardware-set comparison over time, not a cross-machine one — a
cross-machine "known-good profile" comparison, if ever wanted, is a
different, harder feature and explicitly out of scope here).

### `driver_detail_dialog.py` (new)

`DriverDetailDialog(QDialog)` — read the codebase's existing Properties
dialog (referenced in `tests/test_properties_dialog.py`) for the real
structural pattern before writing this; do not invent a new dialog style.
Shows: device name, class, all hardware IDs (not just the primary one used
for dedup), driver version/date/publisher, signed/WHQL status, INF name,
driver-store package size, associated services (if determinable — check
whether `Win32_PnPSignedDriver`/a related WMI class exposes this; if not,
omit rather than guess), and an embedded "Recent crashes" section calling
`driver_diagnostics.crashes_for()`.

### `driver_module.py` additions

- Toolbar: "Export Inventory" button (CSV/HTML, full table incl. columns
  not shown by default — the export is not required to match on-screen
  filtering, unlike the existing filtered CSV export from Task 36), a
  "Snapshots ▾" menu (Save Baseline as..., Diff Against...).
- Context menu: "Copy Hardware ID" (copies the primary hardware ID to
  clipboard, matching the existing "Copy device name" action's shape),
  "Why does this matter?" (enabled only when the row has non-empty
  `flags`; opens a small info box explaining each flag token present).
- A "System Restore Points" section/button surfacing
  `core/system_restore.py`'s existing restore-point listing (confirm its
  real read API during implementation — this spec does not assume it
  beyond "the Updates module already uses it for exactly this kind of
  listing").
- Double-click (or a "Details..." context-menu action) opens
  `DriverDetailDialog` for the selected row.

### Dashboard tile

A new small tile/card in the Dashboard module reading
`sum(1 for d in current_drivers if d.error_code != 0 or not d.signed)` —
this needs Driver Manager's current driver list, which means either the
Dashboard module gets a lightweight read path into Driver Manager's last
refresh (mirroring how search providers already get a live handle via
Task 38's `self._drivers_ref` pattern) or triggers its own cheap
`fetch_drivers()` call. Read the Dashboard module's actual structure
before deciding — this spec intentionally does not prescribe the wiring
mechanism, only the content (a count, with a click-through to the Driver
Manager tab).

## Data Flow

Everything reads from `self._drivers_ref[0]` (already in memory) or a
cheap, already-established adjacent read (Reliability records, restore
points). The one new WMI field (`HardWareID`) rides on the existing
per-refresh query at no extra cost. Baselines write small JSON files under
`%APPDATA%/WindowsTweaker/driver_baselines/`. Nothing in this phase touches
DriverStore, the registry (writes), or runs `pnputil` beyond what already
exists (read-only enum, if `driver_store_size` needs it — check whether
`package_size()` needs a fresh `pnputil /enum-drivers` call or works from
already-available data; if the former, this is a real subprocess call but
still read-only).

## Error Handling

Every new reader follows this codebase's established rule: a refused or
failed read is `None`/empty, distinctly, never silently collapsed into a
default value that looks like a real answer. Reliability cross-reference
is inherently best-effort (fuzzy name matching, and Reliability Monitor
may simply have nothing for a device) and the UI must say so rather than
implying an exact, complete answer. `detect_duplicate_hardware_ids` and
`DriverDiff` are pure functions over already-fetched data — no I/O, no
failure mode beyond a malformed `DriverInfo`, which should not occur since
they consume `driver_reader`'s own validated output.

## Testing

- `driver_diagnostics.py`: unit tests over fixture `LogEntry` lists,
  confirming substring matching works and doesn't over-match unrelated
  devices; `_SUGGESTED_ACTIONS` gets the same
  `test_every_error_code_meaning_is_a_real_sentence`-style validation
  Task 37 already established for `_ERROR_CODE_MEANINGS`.
- `driver_baselines.py`: real save/load/diff round-trip against a
  `tmp_path`, matching `rsop_snapshot.py`'s own test shape.
- `detect_duplicate_hardware_ids`/`driver_store_size`: direct unit tests
  with fixture `DriverInfo` lists, same style as existing
  `test_driver_reader.py`.
- `driver_module.py` additions: the established `_module()`/`_FakeApp`
  harness from `test_driver_module.py`.
- `DriverDetailDialog`: constructed against a fixture `DriverInfo`,
  asserting the fields it displays are present as text somewhere in its
  widgets (matching whatever assertion style the existing Properties
  dialog test uses).

## Open Questions (to resolve during implementation, not blocking spec approval)

1. **WHQL determinability** — `Win32_PnPSignedDriver` may not expose a
   clean WHQL signal. If a real spike shows it can't be reliably derived,
   this one goal item ships as declined-with-reasoning rather than a
   guess.
2. **Associated services per device** — may not be cleanly derivable from
   available WMI classes; omit from the detail dialog if not.
3. **`driver_store_size`'s exact WMI/registry/subprocess cost** — confirm
   whether this can be a lookup or requires a fresh `pnputil` call per
   device; if the latter, consider computing it lazily (only when the
   detail dialog opens for that device) rather than for every row on
   every refresh.
4. **Dashboard tile wiring mechanism** — read the real Dashboard module
   structure before deciding between a live-handle read and Driver
   Manager's own cheap refresh call.

## Self-Review

- **Placeholder scan**: no TBD/TODO left unresolved as a blocker — the
  four Open Questions above are explicitly framed as implementation-time
  spikes with a stated fallback (declined-with-reasoning), not gaps in
  the spec itself.
- **Internal consistency**: architecture, components, and data flow agree;
  no goal item lacks a component; no component serves a non-goal.
- **Scope check**: this spec covers Phase 0 only, as agreed; Phases
  0b/1/2/3 are named for context but not designed here.
- **Ambiguity check**: the one place two readings were possible (baseline
  diff key: device_name vs. hardware_id) is resolved explicitly above.
