# Updates batch 6 - driver routing, download split, security identifiers

**Goal:** Ship the three remaining roadmap items named as open by batches 3-5:
#05 (driver approval routing), #13 (security-bulletin/CVE mapping) and #14
(download/install split).

**Architecture:** `windows_updater.py` owns the new COM boundary work; the WU
tab only renders and asks. Driver policy is Qt-free in `wu_categories.py`, so
Run All and `--unattended` obey it without duplicating the rule.

## Tasks

1. `download_updates_iter()` downloads selected offers through
   `IUpdateDownloader` and marks `WindowsUpdate.downloaded` only when WUA
   reports success. It creates no installer and requests no restart.
   `install_updates_iter()` already reuses `IsDownloaded`; the new
   **Download Selected** button exercises the split with progress, a log and
   a timeline event. A download failure leaves the offer installable.
2. `fetch_pending_updates()` records `driver` from `IUpdate.Type == utDriver`
   plus `DriverClass`/`DriverManufacturer`. `wu_categories.category_of()`
   trusts the COM type over a title that can read like a quality update.
   `split_for_automatic(..., allow_drivers=False)` skips drivers by default
   even when optional updates are allowed; the new Settings checkbox
   `updates.allow_auto_drivers` is the only opt-in. A manual batch that
   contains a driver asks a driver-specific confirmation, and the WU tab gains
   a **Driver Manager** button that publishes `NAV_REQUEST_MODULE`.
3. `WindowsUpdate.security_bulletins` / `cve_ids` are read from
   `SecurityBulletinIDs` / `CveIds` when the build exposes them. The Details
   pane links MSRC bulletins to Microsoft's archive and CVEs to NVD, and says
   so explicitly when Windows Update published neither. Search covers both.

## Constraints and review focus

- A download changes no installed software and must not imply installation.
- A refused/absent identifier is stated, never invented, and a refused
  `Type` never promotes a driver into an automated run.
- Manual selection stays manual: only the extra driver confirmation is added.
- The automatic-run change is deliberate: `allow_optional` no longer sweeps
  drivers in; `allow_auto_drivers` does.
- No worker touches widgets; no build, deploy or commit in this batch.

## Verification

- New suites: `test_wu_details_security.py`, `test_update_driver_download_ui.py`;
  `test_windows_updater.py` and `test_wu_categories.py` extended.
- Targeted regressions: updates (review 7/fixes/winget/run-all), exclusions,
  filters, history, ownership, policy, coordinator, activity, stage runners,
  checkpoints, progress, scan timestamps, WU categories/parser/errors, driver
  check and module inventory - 361 passing.
- `ruff` clean on touched files; `py_compile` clean.
