# Updates batches 8-10 - final waves

**Goal:** Close the remaining operator gaps after batch 7: safer install order,
exportable history, a download-state filter, a real Driver Manager deep link,
and an honest Store verification path.

## Batch 8 - install order, history export, download filter

1. `windows_updater.order_for_install()` is a stable partition that puts offers
   needing a restart last (an unreadable reboot field is treated as "may need
   a restart"). Manual installs and `run_wu_stage` both use it.
2. `history_model.entries_to_csv()` writes the filtered history with `csv`
   quoting; the History dialog gains **Export CSV...** and writes UTF-8 with a
   BOM so Excel reads non-ASCII titles. Decoded errors are included.
3. The WU filter row gains a download-state selector (Any / Downloaded / Not
   downloaded); Download Visible and Install Visible keep acting on exactly
   the visible rows.

## Batch 9 - Driver Manager deep link

4. `NavRequestData.payload` carries module-specific context; MainWindow passes
   it to a module's `receive_navigation(payload)`. Existing `open_path(path)`
   behaviour is unchanged.
5. `DriverModule.receive_navigation()` applies a filter (and stores it when the
   widget does not exist yet). Driver Manager's filter now also matches the
   publisher. Updates sends the selected driver's manufacturer (or class), so
   the destination opens on the relevant devices instead of the whole
   inventory.

## Batch 10 - Store verification

6. New Qt-free `store_verify.py`: snapshot installed AppX versions, trigger the
   Store scan, then re-read versions for a bounded 90 s window. A moved version
   stops polling early and is reported by name/old/new; a quiet window says the
   Store may still be updating; a refused read says nothing is verified. The
   Store tab runs it in a cancellable COMWorker.

## Constraints and review focus

- Ordering never drops or mutates an offer; it is a stable sort only.
- CSV is plain data, no execution; the export honours the active filters.
- Navigation payloads are optional; modules without `receive_navigation` are
  unaffected. The Driver Manager filter still matches name/class as before.
- Store verification never equates "no change in 90 s" with "up to date".
- No widget work happens on a worker thread.

## Verification

- New suites: `test_history_export.py`, `test_driver_navigation.py`,
  `test_store_verify.py`; `test_windows_updater.py`,
  `test_update_driver_download_ui.py` extended.
- Targeted updates regressions plus module inventory pass; `ruff` and
  `py_compile` clean on touched files.
