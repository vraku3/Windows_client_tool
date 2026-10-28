# Updates batch 4 — sources, history of scans, filters

**Goal:** Ship #01 (update-management ownership), #09 (search, structured
filters, persistent sorting), #10 ("update visible results") and #11 (scan
history / changes since last).

**Architecture:** Two new Qt-free engines (`update_ownership.py`,
`scan_history.py`); the update tables keep row index == model index by sorting
the model itself, never by enabling `QTableWidget` sorting, so selection and
the per-row binding stay correct.

## Tasks

1. `update_ownership.py` reads the local policy, MDM and enrollment evidence:
   WSUS server/status/target group, per-class source (WUfB vs WSUS), MDM
   providers, internet-location policy. Missing keys are definite; refused
   keys are listed as unreadable. Rendered in Settings above the policy table.
2. `scan_history.py` stores the last 50 successful snapshots per source and
   diffs by id (new / changed version / gone). First scan reports no changes;
   failed scans are never recorded. Both update tabs show a collapsible
   "Changes since last scan" panel.
3. App tab: source and result filters next to the search box; WU tab: search,
   severity, classification and restart filters. Sorting by header click sorts
   the underlying model and persists per tab (`updates.sort_apps`,
   `updates.sort_wu`).
4. "Update Visible" (winget) and "Install Visible" (WU) act only on the rows
   the current filters leave visible; they are disabled when nothing visible
   is installable.

## Constraints and review focus

- Row index must remain the model index for install/selection mapping.
- A failed scan must not be recorded, and must not turn previous offers into
  "gone".
- Sorting is stable for equal keys and never runs during an install.
- Filters must not reorder or mutate the stored offers.
- No build/commit is part of this plan; a build follows separately.

## Verification

- New suites: `test_update_ownership.py`, `test_scan_history.py`,
  `test_update_filters.py`.
- Targeted regressions: module inventory, updates suites (review 7/fixes/
  winget/run-all), exclusions, coordinator, history, operation lock, activity.
  86 + 257 passing across the two runs (plus the new modules).
- `ruff` and `py_compile` clean on the touched files.
