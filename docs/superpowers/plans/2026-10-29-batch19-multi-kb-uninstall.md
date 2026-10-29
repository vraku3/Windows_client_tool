# Updates batch 19 - uninstall several KBs at once

**Goal:** Let the History dialog remove multiple succeeded installations in one
confirmed batch, with one restore point and one result summary.

## Tasks

1. `windows_updater.uninstall_updates()` loops `uninstall_update()` over KBs,
   keeps every result, checks cancellation between KBs, and never interrupts
   an uninstaller in flight. One failure does not discard the others.
2. The History table uses ExtendedSelection; the uninstall button is enabled
   when any selected row is a succeeded KB installation. A batch confirmation
   lists the KBs (first ten plus a count) and returns `uninstall_kbs`.
3. `_WinUpdatesTab._do_uninstall_many()` creates ONE restore point for the
   batch (when the setting is on), records each ledger/timeline outcome, shows
   successes plus up to eight failure messages, and re-scans if any succeeded.
   `_do_uninstall(kb)` remains a one-element wrapper.

## Constraints and review focus

- Only succeeded installations with a KB are selectable for uninstall.
- One restore point before the batch, not one per KB.
- Cancellation is between KBs; a partial batch reports exactly what happened.
- No widget work from workers; the result handler marshals to the UI thread.

## Verification

- `test_windows_updater.py` covers every-outcome and cancellation behaviour;
  `test_history_multiselect.py` covers extended selection, failed-row refusal
  and the returned KB list.
- Targeted updates/history regressions plus module inventory pass; ruff clean.
