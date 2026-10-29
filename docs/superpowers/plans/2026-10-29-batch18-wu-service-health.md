# Updates batch 18 - Windows Update service health

**Goal:** Show why a scan/install might fail, without changing a service.

## Tasks

1. `wu_health.py` (Qt-free) reads state/start type for wuauserv, BITS,
   cryptsvc, msiserver, dosvc and TrustedInstaller through pywin32. States and
   start types are named; a refused service keeps its error text and is never
   shown as Running.
2. Settings gains a read-only Windows Update services table with a Refresh
   button. Non-running/unreadable rows are visually marked as problems.
3. A failed Windows Update scan appends the service summary to the tab log so
   the cause is visible next to the error.
4. Help text documents the read-only table.

## Constraints and review focus

- No service is started, stopped or reconfigured by this wave.
- A refused read is a reason, not a clean state; WU health never collapses an
  unknown into "running".
- Reading is synchronous and tiny (six service queries); no workers needed.

## Verification

- `test_wu_health.py` covers named states, stopped/unreadable rows, a refused
  start-type read that preserves a running state, and summary wording.
- Targeted updates regressions plus module inventory pass; ruff clean.
