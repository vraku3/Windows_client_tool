# Per-package activity implementation plan

**Goal:** Implement the user-supplied per-package ledger design in the Updates module.

**Architecture:** A Qt-free JSONL engine owns persistence and outcome normalization. Update workers write completed attempts immediately. A shared activity widget renders package history and recent activity.

**Constraints:** Preserve unknown versions as null; append and flush; trim to 2000 only above 2100 lines; skip corrupt records; use semantic colors; preserve cancellation safeguards. Work in the supplied checkout and leave unrelated untracked files untouched. No build, deployment, commit or machine update is requested.

- [x] Add failing persistence tests covering filtering, corruption, retention, dates and concurrency; implement the ledger and normalization helpers.
- [x] Add failing integration tests; retain winget read-back versions, persist manual and shared-stage outcomes, pass unattended context explicitly, and record WU results as they arrive.
- [x] Add failing widget tests; implement package history below the app table and searchable/filterable recent activity in Run All; refresh when viewed and after completion.
- [x] Run focused Updates tests, the project suite, import checks and diff review. Report verification limits.

Ruling: Store scan triggers do not identify packages or confirm installs. Record actual msstore package attempts from the winget loop with source=store; do not fabricate per-package successes from an MDM trigger.
Ruling: WU does not expose comparable package versions through the current model. All three version fields remain null for WU, with restart-required results marked UPDATED_UNVERIFIED.
Ruling: The requested forwarding agent is unavailable; execute directly as Codex without substituting another subagent.

Verification: 181 focused Updates tests and 110 module-inventory tests passed. Main import, Ruff checks and diff whitespace checks passed. Both activity layouts were rendered and inspected. Full-suite execution encountered a native access violation in `test_cleanup_final_review_ui.py::test_cancelling_a_category_group_clean_reaches_the_engine`; it did not complete. Re-running the preceding test modules produced 965 passes, 17 failures and one skip. The failures concern machine-dependent App Buster/ASR reads and refused audit/backup registry operations; their exact names and traces are retained in `activity-prefix.log`.

Review fixes: Preserve the next record after a truncated JSONL tail. Refresh selected package history when a scan replaces a table row. Persist unexpected winget exceptions before forwarding the error. Regression tests cover each case.
