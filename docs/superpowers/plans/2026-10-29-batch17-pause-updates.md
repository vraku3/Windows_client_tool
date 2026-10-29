# Updates batch 17 - pause and resume Windows Update

**Goal:** Make the policy view actionable: pause quality/feature updates for
1-35 days and resume them, using the same UX values Windows Settings writes.

## Tasks

1. `update_pause.py` (Qt-free): `pause_values(days, now)` builds the five
   REG_SZ values in UTC; `read_pause`, `pause_updates` and `resume_updates`
   use `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\UX\Settings`.
   Missing values are "not paused"; a refused read/write is returned as a
   reason, never a silent success. Days are clamped to Windows' 35-day limit.
2. Settings gains a Pause Windows Update box: current state, days spin box,
   **Pause updates**, **Resume updates**. Both require administrator rights and
   confirmation; the policy table refreshes afterwards.
3. Help text explains the reversibility and the 35-day maximum.

## Constraints and review focus

- Pause only writes the documented UX values; it never edits policy/MDM keys.
- Resume deletes only those five values; other policy stays intact.
- A non-elevated user gets a clear message, not a failed registry call.
- No widget work from workers (these are synchronous, tiny registry calls).

## Verification

- `test_update_pause.py` covers value shape/clamping, first read, write/read
  round trip, resume, and resume-without-a-pause using a fake registry.
- Targeted updates/policy regressions plus module inventory pass; ruff clean.
