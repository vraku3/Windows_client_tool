# Updates batch 5 — categories, progress honesty, uninstall

**Goal:** Ship #03 (feature vs quality separation), #04 (optional update
categories), #19 (progress phases with stall detection) and #20 (update
recovery / uninstall).

**Architecture:** One Qt-free classifier (`wu_categories.py`) decides category
and the automated-run rules. Phase markers are ordinary log lines
(`Phase: installing`) intercepted by Run All; no new worker signals. Uninstall
goes through WUA `IUpdateInstaller.Uninstall()` behind a confirmation and the
existing restore-point setting.

## Tasks

1. `wu_categories.py` classifies Defender / Feature / Driver / Preview / .NET /
   Security / Quality, summarises a scan, and splits offers for Run All and
   unattended. Feature upgrades and optional categories are skipped unless
   `updates.allow_feature_updates` / `updates.allow_optional_updates` are on.
2. The WU tab shows a category summary in its status line, gains a category
   filter, and asks an explicit confirmation before a manual batch that
   contains a feature upgrade. Settings exposes both opt-in toggles.
3. `run_wu_stage` / `run_winget_stage` / cleanup / DISM emit `Phase:` markers;
   Run All hides them from the log, shows "Installing… 4m 12s" and warns once
   there has been no output for 3 minutes.
4. `windows_updater.uninstall_update(kb)` searches installed software updates,
   refuses when `UninstallationBehavior.CanUninstall` is False, and reports
   the WUA result. The History dialog offers "Uninstall update…" for succeeded
   KB installations only; the tab confirms again, creates a restore point
   according to the setting, records the result in the ledger and timeline,
   and re-scans. `history_model.kb_of_title`/`uninstallable` gate the button.

## Constraints and review focus

- Manual selection stays manual: only feature upgrades get an extra confirm.
- Automated runs never silently install feature/optional updates.
- No new worker signal; phase lines must still be readable in the session log.
- Stall detection reports, never cancels.
- Uninstall never runs on a guessed KB, without confirmation, or without the
  restore-point setting being honoured.

## Verification

- New suites: `test_wu_categories.py`, `test_run_progress.py`; history tests
  extended for KB extraction/eligibility.
- Targeted regressions: module inventory, updates (review 7/fixes/winget/
  run-all), stage runners, windows updater, activity, coordinator, scan
  history, ownership, filters, policy, operation lock, timeline — 316 passing.
- `ruff` clean on touched files; `py_compile` clean.
