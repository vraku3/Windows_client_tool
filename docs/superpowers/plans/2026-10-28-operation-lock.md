# Shared operation lock implementation plan

**Goal:** Serialize Game Mode, Updates, unattended maintenance and Optimise OS across processes.

**Architecture:** A Qt-free named Windows mutex plus a process-local gate prevents recursive Windows mutex acquisition and concurrent threads. Each operation acquires/releases on its worker thread. Maintenance checks the persistent Game Mode journal under the mutex, and shared stages reuse only their own thread's acquisition.

**Tech stack:** Python, ctypes Win32 mutex APIs, pytest.

**Spec:** User's shared cross-process operation lock request, 2026-10-28.

## Constraints and review focus

- `Global\WinClientTool_OperationLock`, Unicode APIs, pointer-sized handles.
- Best-effort atomic APPDATA owner sidecar; stale metadata never grants or denies ownership.
- Abandoned mutex grants ownership; acquisition errors fail closed.
- Release on the acquiring thread, including exceptions and cancellation.
- Game Mode remains ON between workers; maintenance must check its journal under exclusion.
- Preserve current Optimise OS per-item Game Mode holds.
- Run focused tests only; no real maintenance or Game Mode round trip.

## Tasks

1. Write failing tests for exclusion between instances/threads/processes, owner metadata, stale metadata and crash recovery. Implement `src/core/operation_lock.py` and verify them.
2. Add maintenance guards and integration regressions. Protect Game Mode ON/OFF/enforce and Optimise OS Apply/Revert while keeping existing thread locks.
3. Protect app/WU/Store workers and the entire Run All/unattended run. Stages reuse a current-thread lock or acquire their own. Refusals surface the holder through existing error signals; unattended contention exits zero.
4. Run the new tests and affected Game Mode/Optimise/Updates suites. Review the final diff and check syntax/whitespace. No build or deployment requested.

## Verification and rulings

- Complete: all four tasks; 404 focused regressions passed, including 27 operation-lock tests. Syntax checks passed for the nine production files, and `git diff --check` passed.
- Native tests cover a second process holding the mutex, abrupt holder exit and WAIT_ABANDONED recovery. No real maintenance actions were used for verification.
- Test isolation: pytest uses a unique Local mutex per session; otherwise existing fake Game Mode tests compete with the running app and concurrent test processes on the production Global mutex.
- Cross-process journal freshness: ON/OFF/enforce reload the Game Mode journal after acquiring the mutex, so a second GUI cannot use stale recovery state. The long-session test now persists its simulated timestamp before calling enforce.
- Existing Optimise OS behavior permits nonoverlapping preferences during Game Mode; its per-item holds remain intact.
- Work performed in place because Git metadata is read-only. Concurrent activity/history/timeline changes were preserved. No commit, build or deployment performed.
- Automatic approval review blocked removal of temporary test folders with "blocked by policy"; the folders remain.
