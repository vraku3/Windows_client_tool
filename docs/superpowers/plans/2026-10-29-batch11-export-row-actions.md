# Updates batch 11 - offer export and row actions

**Goal:** Make the scanned state portable and every row actionable without
hunting through the toolbar.

## Tasks

1. `export_csv.py` is Qt-free: `app_offers_to_csv()` and `wu_offers_to_csv()`
   include installed/available versions, category, severity, restart, size
   bounds, downloaded state, driver flag and published security identifiers.
   `csv` owns quoting; unknown sizes are empty, never zero. The App tab gains
   **Export CSV**; the WU context menu exports the visible offers (held rows
   included when visible).
2. Both update tables gain a row context menu. App rows: Update, Package
   Details, Add to Blocklist, Copy ID, Export. WU rows: Download, Install,
   Details, Add to Blocklist, Driver Manager (driver offers only), Copy KB,
   Export. Actions that change the machine are disabled while a scan or
   another operation is running, and for rows awaiting a restart.
3. Help text documents both exports. CSV is UTF-8 with BOM for Excel.

## Constraints and review focus

- Export reads the last scan; it never contacts winget or Windows Update.
- Row actions call the same guarded methods as the toolbar, never a second
  install/download path.
- Held AMD rows offer Copy/Export only; they remain non-selectable for install.
- No widget work from workers; no build/deploy/commit in the plan itself.

## Verification

- New `test_offers_export.py`; `test_update_driver_download_ui.py` covers the
  filtered WU export.
- Targeted updates regressions plus module inventory pass; ruff clean.
