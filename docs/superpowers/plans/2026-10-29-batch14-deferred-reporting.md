# Updates batch 14 - deferred categories in the run report

**Goal:** Stop Run All from losing the offers it skipped by policy when other
updates did install, and show them in the maintenance report.

## Tasks

1. `run_wu_stage` builds `deferred = {category: count}` right after the
   automatic-run split and returns it on every path (nothing runnable,
   disk-preflight refusal, restore-point refusal, and a completed run).
   Previously only the "nothing to install" path carried it.
2. `normalize_stage_data` forwards `wu_deferred`.
3. `report_generator` adds a "Deferred by settings" card and section naming
   each skipped category and count, and states that the category gate is off
   in Updates > Settings. An empty/zero mapping adds nothing.
4. Regression: the empty-normalization test now includes `wu_deferred: {}`.

## Constraints and review focus

- Counts only; the report never claims a deferred offer was installed.
- Unknown category keys keep their raw key rather than being dropped.
- Zero counts are omitted, so an empty report does not show a misleading card.
- No build/deploy/commit in the plan itself.

## Verification

- `test_stage_runners.py` covers deferred pass-through; `test_report_generator.py`
  covers the card/section and its absence.
- Targeted updates regressions plus module inventory pass; ruff clean.
