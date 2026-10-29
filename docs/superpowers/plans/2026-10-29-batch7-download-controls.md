# Updates batch 7 - download visibility and stop

**Goal:** Finish the download split from batch 6 with the two controls an
operator reaches for next: act on everything currently visible, and stop a
long batch without leaving the app.

**Architecture:** The WU tab owns one `_op_worker` reference for the current
install/download worker. Scans use `_scan_worker` and are deliberately NOT
cancelled by Stop. `download_updates_iter` and `install_updates_iter` both
check cancellation between items, so Stop never aborts an item in flight.

## Tasks

1. Add **Download Visible** next to Download Selected; it downloads exactly
   the rows the filters leave visible, excluding offers already waiting for a
   restart.
2. Add **Stop**, enabled only while a download or install is running. It calls
   `cancel()` on `_op_worker` and reports "Stopping before the next update...";
   the result handler clears `_op_worker` and disables Stop. `_cancel_all`
   clears it too.
3. Help text covers both controls, including that an in-flight item finishes
   and completed changes remain.

## Constraints and review focus

- Stop must never cancel a scan or a worker it does not own.
- No widget work from a worker thread; the result/error signals clear state.
- An install/download that finishes after Stop still records its real outcome.
- No build, deploy or commit in this batch.

## Verification

- `test_windows_updater.py` covers stopping between downloaded items.
- `test_update_driver_download_ui.py` covers Download Visible honouring the
  filters and Stop cancelling only the operation worker.
- Targeted updates regressions plus module inventory pass; ruff clean.
