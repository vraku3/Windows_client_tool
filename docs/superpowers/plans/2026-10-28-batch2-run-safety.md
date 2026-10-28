# Updates batch 2 — run safety and compliance

**Goal:** Ship the next five High items of the 2026-10-28 Updates & Game Mode
roadmap: #08 patch compliance, #15 reboot preflight, #16 post-reboot
reconciliation, #18 durable checkpoints, #22 exclusion pattern preview.

**Architecture:** Qt-free engines own persistence and judgement; the Update
tabs only render and ask. All new files live under `src/modules/updates/`,
and the Dashboard's findings engine stays Qt-free.

**Spec:** Roadmap artifact supplied 2026-10-28 (batch 1 = items 07, 21, 25,
50, 52, 64, 72, 81).

## Tasks

1. `run_checkpoint.py` — atomic run intent plus per-item transitions; a
   dead-owner run is reported, never guessed. Wire begin/finish into Run All,
   `--unattended`, the App Updates batch and the manual WU batch; per-item
   transitions in the shared WU/winget stages.
2. `reboot_preflight.py` — pending-restart reasons plus an unfinished
   checkpoint, shown before Run All and a manual WU install. Unreadable
   evidence is said out loud, never treated as "no restart".
3. `reboot_reconcile.py` — persist WU items that asked for a restart, then
   reconcile on the next session. Winget/Store only confirmed by a version
   that moved forward or disappearance from a successful scan; WU is
   explicitly unverifiable.
4. `overview_health.judge_patch()` plus a COM-reading default; the Dashboard
   findings worker becomes a `COMWorker`. Refused history is UNKNOWN.
5. `blocklist.matching_offers()` and a live preview in the exclusion dialog,
   fed by the offers of the last scans.

## Constraints and review focus

- No version is invented: missing is null/unknown, never "updated".
- A refusal to read is visible; empty never means fine.
- Checkpoint/ledger/reconcile failures are logged and never abort an install.
- No duplicate timeline totals; no UI work from a worker thread.
- No real maintenance round trip, build, deployment or commit.

## Verification

- New suites: `test_run_checkpoint.py`, `test_reboot_reconcile.py`,
  `test_reboot_preflight.py`; extended `test_overview_health.py`,
  `test_update_exclusions_ui.py`.
- Targeted regressions: updates (review 7/fixes/winget/run-all), activity,
  operation lock, timeline, stage runners, scan timestamps, blocklist,
  module inventory, Game Mode and Game Mode history/preflight — all passing.
- `py_compile` on every touched production file; `git diff --check`.

## Open for the next batch

- #17 cross-tab coordinator visibility (the lock already serializes; tabs do
  not yet disable their install buttons or show the current holder).
- #12 details pane, #26 searchable WU history, #02 policy/deferral view.
