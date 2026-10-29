# Updates batch 12 - hidden offers, restart, activity export

**Goal:** Close the last operator loops: reverse a hide, finish a pending
restart from the banner, and export the activity ledger.

## Tasks

1. `windows_updater.unhide_update()` clears `IsHidden` on the live identity
   (same COM-thread requirement as `hide_update`). The WU Hide button becomes
   **Unhide Selected** when every selected row is hidden; the confirmation and
   worker choose the right operation. Mixed selections hide.
2. The reboot banner gains **Restart now**. It confirms first, refuses while an
   install/download/scan is running, schedules `shutdown /r /t 60`, records a
   timeline event, and tells the user how to cancel (`shutdown /a`).
3. `activity_ledger.entries_to_csv()` exports timestamp, package/ID/source,
   version before/offered/after, outcome, context, elevation and detail with
   csv quoting. The Activity widget's **Export CSV** writes the filtered rows,
   including rows beyond the 20 shown in the table.

## Constraints and review focus

- Unhide never invents an identity; a stale scan refuses with a re-scan hint.
- Restart is user-initiated only, never scheduled automatically, and never
  runs during an update operation.
- Activity export reflects the current search/outcome filter; missing versions
  stay empty rather than becoming the string "None".
- No widget work from workers.

## Verification

- `test_windows_updater.py` covers unhide; `test_activity_export.py` covers
  CSV and the widget filter; `test_update_driver_download_ui.py` covers the
  hide/unhide button and the restart guard.
- Targeted updates regressions plus module inventory pass; ruff clean.
