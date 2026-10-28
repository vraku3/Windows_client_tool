# Unified timeline implementation plan

**Goal:** Correlate maintenance and recovery actions in a persistent Dashboard timeline.

**Architecture:** A Qt-free JSONL engine owns append, retention, filtering and reboot detection. Existing shared action boundaries emit small events through lazy imports. A Dashboard child module browses the log.

**Spec:** User feature request, 2026-10-28.

**Constraints:** Informational failures never propagate; retain 5000 events with a 500-entry trim buffer; skip corrupt records; preserve existing actions and history.

- [x] Test and implement `core/timeline.py`: persistence, concurrent appends, corrupt lines, filtering, trimming, failures and reboot detection.
- [x] Connect shared maintenance stages, Updates UI, Game Mode, Optimise OS, Cleanup, Quick Fix, Security and app lifecycle; test representative action boundaries.
- [x] Add Dashboard Timeline child with source/date/search filters and selectable JSON details; verify rendering and composite lifecycle.
- [x] Review diff, run focused regression tests and syntax checks.

Review focus: partial outcomes must remain visible; no duplicate Cleanup totals; no UI work from workers; shutdown emitted once; no false reboot after a crash.

Ruling: Work directly in the supplied workspace because the requested rescue runtime is unavailable; no commit/build/deploy is requested.

Verification: combined relevant regressions passed (488 passed, 1 skipped); added Game Mode/Optimise event assertions passed; Dashboard rendered and inspected at 1100x650; source import and syntax checks passed. One existing scapy cryptography deprecation warning.

Review lessons: compare timezone-aware timestamps by instant; label a Store scan as a request rather than installed updates; isolate stage summary failures from the action result. Kept here because the global review lessons path is outside the writable workspace.
