# Updates batch 3 — visibility and control

**Goal:** Ship roadmap items #17 (cross-tab coordinator visibility), #12
(update details with KB links), #26 (searchable Windows Update history) and
#02 (effective deferral / restart policy view).

**Architecture:** New Qt-free models (`history_model.py`, `update_policy.py`)
own filtering and policy interpretation; the dialogs and the Settings tab only
render. Coordinator state comes from `core.operation_lock.active_owner()`,
which trusts only a live holder.

## Tasks

1. `core.operation_lock.active_owner()` — in-process holder, or another
   process whose sidecar PID is still alive. Dead holders and this process's
   stale sidecar are ignored. Updates module polls it every 1.5s while active
   and shows a banner; App Updates / Windows Updates / Run All disable their
   install buttons through `set_maintenance_busy()`.
2. `windows_updater.WindowsUpdate` carries `kb_ids`, `support_url`,
   `more_info_urls`, `description` from the COM object; a Details button opens
   `wu_details_dialog.py` (KB links to support.microsoft.com, support and
   more-info URLs, description). Only http(s) URLs are rendered as links.
3. `patch_status.read_history(operations=...)` keeps the operation type;
   `history_model.py` filters by search/date and paginates;
   `history_dialog.py` replaces the 30-line log dump with a 2000-entry table
   (date, operation, update, result, decoded HRESULT).
4. `update_policy.py` reads the local GPO, AU, PolicyManager (MDM) and UX
   settings scopes and reports each deferral/pause/deadline/active-hours value
   with provenance. Missing keys are "not configured"; refused keys are listed
   as unreadable. The Settings tab renders it in a policy table.

## Constraints and review focus

- Refusal is never collapsed into "unmanaged" or "up to date".
- The coordinator must not stay stuck on a crashed holder's sidecar.
- Link rendering escapes text and accepts only http(s).
- Every new import is added to `pyinstaller_common.py` hidden imports.
- No build, deployment, commit or real update round trip.

## Verification

- New suites: `test_update_history.py`, `test_update_policy.py`,
  `test_update_coordinator.py`; extended `test_operation_lock.py`.
- Targeted regressions: updates (review 7/fixes/winget/run-all), activity,
  operation lock, timeline, stage runners, exclusions, scan timestamps,
  blocklist, run checkpoints, reboot reconcile/preflight, patch status,
  overview health and module inventory — 437 passing.
- `ruff` clean on the touched files, `py_compile` clean.

## Open for the next batch

- #12 is now shipped for WU; #13 (CVE mapping) and #16's WU uninstall path
  remain separate items.
- #01 ownership panel, #05 driver approval routing, #14 download/install split.
