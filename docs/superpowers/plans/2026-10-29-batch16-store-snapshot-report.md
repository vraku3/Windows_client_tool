# Updates batch 16 - Store changes since the previous run

**Goal:** Give the Store stage evidence without waiting on the Store: record
installed versions each run and report what moved since the previous one.

## Tasks

1. `store_verify` gains atomic snapshot persistence (`updates/store_snapshot.json`):
   `load_snapshot`, `save_snapshot`, and the existing `snapshot`/`changed_apps`.
   Missing or corrupt files are ignored, never fatal.
2. `run_store_stage` reads the previous snapshot, snapshots the current
   installed versions, triggers the Store scan, then reports versions changed
   since the previous run and saves the new snapshot. A refused enumeration is
   "changes unknown", not "no changes".
3. `normalize_stage_data` forwards `store_changed`, `store_previous_at` and
   `store_read_refused`; the maintenance report shows the changed app list or
   says versions were unreadable.

## Constraints and review focus

- The trigger stays fire-and-forget; this adds no 90-second wait to Run All.
- Snapshots store names/versions only, never package paths or user data.
- A first run records a baseline and says so; it does not claim "no changes".
- A refused read is never rendered as an unchanged Store.

## Verification

- `test_store_verify.py` covers persistence and corrupt files;
  `test_stage_runners.py` covers normalization; `test_report_generator.py`
  covers the changed-list and unreadable wording.
- Targeted updates regressions plus module inventory pass; ruff clean.
