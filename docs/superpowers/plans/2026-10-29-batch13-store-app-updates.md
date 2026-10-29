# Updates batch 13 - per-app Store updates

**Goal:** Make the Microsoft Store tab actionable instead of trigger-only.

## Tasks

1. New Qt-free `store_update.update_apps()` upgrades each selected Store offer
   with `winget upgrade --id ... --exact` through the existing
   `install_update` path (EULA flags, verification, portable handling), records
   each outcome in the activity ledger with `context="store"`, and returns
   `[(AppUpdate, UpdateOutcome)]`. Cancellation is checked between packages; a
   running installer is never killed.
2. `_StoreUpdatesTab` gains `set_app`, `set_finish_callback` and
   `set_maintenance_busy`, an **Update Selected** button gated on a selection
   and the machine-wide operation lock, a confirmation listing the packages,
   and a worker that keeps the UI responsive. Completion re-scans the shared
   App Updates source via the module callback, which refreshes this tab too.
3. `UpdatesModule` passes the app/finish callback and includes the Store tab in
   its operation-owner polling, so another holder disables its buttons.

## Constraints and review focus

- Store offers are only the msstore subset of the shared winget scan; no second
  scan.
- No rollback is claimed; the confirmation says so.
- A failed package does not discard earlier results.
- No widget work from workers.

## Verification

- `test_store_update.py` covers success, exception-to-FAILED, cancellation and
  the msstore-only selection gate.
- Targeted winget/updates regressions plus module inventory pass; ruff clean.
