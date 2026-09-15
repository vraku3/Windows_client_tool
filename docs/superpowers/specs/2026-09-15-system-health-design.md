# System Health Module (Sub-project 4) — Design

## 0. How this spec was produced

Same disclosure as Sub-projects 2 and 3 (see their specs' own §0): built
under the user's explicit "keep going ... shut down when done"
authorization, without interactive question-and-answer. Every choice
below is a ruling, made against this codebase's own established
patterns (Driver Manager, Monitor Control, Updates' `history_writer.py`
and `stage_runners.py`).

**This is the largest and most novel of the four sub-projects — a brand
new sidebar module, not a modification of existing ones.** Given that,
and given real session constraints by this point (three prior
sub-projects' worth of fix waves, one Opus API spend-limit hit already
today), this spec deliberately narrows the original outline (from
`docs/superpowers/specs/2026-09-14-cleanup-quick-cleanup-merge-design.md`
§7.4) to what can be built with the same rigor the first three
sub-projects were held to, rather than attempting the full original list
at reduced quality. Every cut is a documented, explicit deferral, not a
silent one.

## 1. Scope: what's IN, what's OUT, and why

The original outline named 11 things. Real-code investigation plus a
deliberate risk/effort call on each:

| Item | Disposition | Why |
|---|---|---|
| Pending servicing transaction (`WinSxS\pending.xml`) detection | **IN** | A file-existence + light parse check. Read-only, cheap, safe. |
| Orphaned scheduled tasks / services findings | **IN, scoped to tasks only** | A scheduled task whose Action program path no longer exists on disk is a clean, unambiguous, read-only finding. "Orphaned services" (a service registered with no backing binary) is the same shape but touches `HKLM\SYSTEM\CurrentControlSet\Services`, a much larger and more security-sensitive registry area — deferred to keep this module's first pass to the lower-risk half of this finding. |
| Free-space / next-upgrade headroom indicator | **IN, as an approximation, honestly labeled** | Microsoft does not publish one universal "headroom for your next upgrade" number — this reports free space against a commonly-cited practical minimum (not represented as an official Microsoft figure) and says so in the UI. |
| Plain WinSxS `/StartComponentCleanup` (moved out of Large Items) | **IN** | Already built and working in `_large_items_tab.py` — this is a relocation + one new sibling action (ScanHealth), not new risk surface. |
| DISM `/ScanHealth` | **IN** | A real, documented, read-only-in-effect DISM operation (checks for corruption, does not repair) — genuinely different from the existing `/AnalyzeComponentStore` button, which only measures reclaimable space. |
| Gated `/ResetBase` | **IN, heavily gated** | Typed confirmation, forced fresh restore point, its own dedicated button — never selectable via any bulk/select-all action. This is the single highest-risk action in the whole sub-project; see §2.3. |
| Command transparency in every confirmation | **IN** | Every dialog in this module states the literal command it is about to run — a UI convention, not new engineering. |
| HTML/JSON audit log | **IN**, JSON not HTML | Mirrors `modules/updates/history_writer.py`'s exact shape (a capped JSON array), not a new HTML report generator — the existing Updates module's own history is JSON, and "HTML audit log" in the original outline meant "reuse `history_writer.py`'s *pattern*," which this does; a from-scratch HTML report generator is real, separate, un-asked-for scope. |
| New `--unattended --stages health` | **IN, read-only only** | Per the original outline's own words: a "read-only `--unattended --stages health` stage." Runs the Findings tab's 3 checks and writes them to history — never ScanHealth, Cleanup, or ResetBase unattended. |
| Dry-run toggle | **OUT, replaced with something that actually maps onto DISM** | DISM has no generic "would-do" flag for `/StartComponentCleanup`/`/ResetBase` the way some tools do. A fake "dry run" checkbox that doesn't change what actually happens would be worse than no toggle. In its place: ResetBase requires ScanHealth to have been run and shown clean (no corruption) in the CURRENT session before it can be clicked at all — a real precondition, not a cosmetic one. |
| VSS shadow-storage floor management | **OUT, deferred** | Genuinely separate, write-capable feature (`vssadmin resize shadowstorage`) touching System Restore's own storage allocation — real complexity and real risk (shrinking shadow storage below its current usage can delete existing restore points) that deserves its own design pass, not a rushed addition to an already-large sub-project. |
| Orphaned services (the other half of the deferred finding above) | **OUT, deferred**, see row 2 | |

## 2. Component changes

### 2.1 New module: `src/modules/system_health/`

```
src/modules/system_health/
    __init__.py
    system_health_module.py     # BaseModule, 2 tabs
    findings.py                 # 3 read-only check functions, no Qt
    servicing.py                # DISM command wrappers, no Qt
    history.py                  # mirrors updates/history_writer.py's shape
```

**Ruling on admin model**: `requires_admin = True`, `read_only_unelevated
= True` — matching **Monitor Control's** pattern (`monitor_module.py`),
not Driver Manager's (`requires_admin = False`). Driver Manager is
fundamentally a read tool that also offers a few admin actions; System
Health is fundamentally about privileged servicing operations (DISM,
scheduled restore points) that happen to have a genuinely useful
unelevated read-only subset — Monitor Control's own framing ("display
and DDC work needs no elevation at all, only the audio endpoint writes
do") is the closer analogy. `ModuleGroup.SYSTEM` (alongside Monitor
Control).

**No Qt in `findings.py`/`servicing.py`/`history.py`** — the same
`scan/`+`store/` split TreeSize, Monitor Control, and GPResult already
keep, and for the same reason: these are the parts worth testing
headless.

### 2.2 `findings.py` — the 3 read-only checks

```python
"""Read-only System Health findings. No Qt, no writes -- every function
here is safe to call unelevated and safe to call from --unattended
--stages health.
"""
import glob
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Finding:
    id: str
    title: str
    detail: str
    severity: str  # "info" | "warning" -- no "danger": nothing here deletes anything


def check_pending_servicing() -> Optional[Finding]:
    """WinSxS\\pending.xml existing means a servicing transaction is
    mid-flight (usually resolved by the next reboot). Detecting it is
    exactly what it sounds like -- a file-existence check -- but it
    answers a real question ("why won't DISM let me run ResetBase right
    now") that this module's own ResetBase gate (2.3) needs to ask too."""
    windir = os.environ.get("windir", r"C:\Windows")
    path = os.path.join(windir, "WinSxS", "pending.xml")
    if not os.path.exists(path):
        return None
    return Finding(
        id="pending_servicing",
        title="A servicing transaction is pending",
        detail=(
            f"{path} exists, meaning Windows has an in-progress "
            "component update. This usually clears on the next reboot. "
            "DISM component-store operations may refuse to run until it does."
        ),
        severity="warning",
    )


def check_orphaned_scheduled_tasks() -> List[Finding]:
    """A scheduled task whose Action names a program path that no longer
    exists on disk. Uses schtasks /query /xml (per-task), which -- like
    every other schtasks call in this codebase -- can be read
    unelevated; a refused read is reported as a Finding of its own
    rather than silently producing zero results (a refusal is never
    reported as "nothing found" -- CLAUDE.md's own recurring rule)."""
    findings: List[Finding] = []
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=30,
            creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [Finding(
            id="orphaned_tasks_refused",
            title="Could not enumerate scheduled tasks",
            detail=str(e), severity="warning")]
    if result.returncode != 0:
        return [Finding(
            id="orphaned_tasks_refused",
            title="Could not enumerate scheduled tasks",
            detail=(result.stderr or result.stdout or "schtasks refused").strip(),
            severity="warning")]

    import csv
    import io
    task_names = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if row:
            task_names.append(row[0])

    for name in task_names:
        try:
            xml_result = subprocess.run(
                ["schtasks", "/query", "/tn", name, "/xml"],
                capture_output=True, text=True, timeout=15,
                creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if xml_result.returncode != 0:
            continue
        program = _extract_command_path(xml_result.stdout)
        if program and not _program_exists(program):
            findings.append(Finding(
                id=f"orphaned_task:{name}",
                title=f"Scheduled task points at a missing program",
                detail=f"Task {name!r} runs {program!r}, which does not exist.",
                severity="info",
            ))
    return findings


def _extract_command_path(task_xml: str) -> Optional[str]:
    import re
    match = re.search(r"<Command>(.*?)</Command>", task_xml, re.IGNORECASE)
    if not match:
        return None
    return os.path.expandvars(match.group(1).strip().strip('"'))


def _program_exists(path: str) -> bool:
    if os.path.isabs(path) and os.path.exists(path):
        return True
    # A bare exe name (e.g. "notepad.exe") resolves via PATH -- shutil.which
    # is the correct check for that shape, os.path.exists alone would
    # always say "missing" for anything not given as a full path.
    import shutil
    return shutil.which(path) is not None


def check_upgrade_headroom() -> Finding:
    """Free space on the system drive against a practical minimum for a
    feature update -- NOT an official Microsoft figure (Microsoft
    publishes a 64 GB *install* minimum for Windows 11 itself, not a
    per-upgrade free-space number), stated as an approximation in the
    UI text itself so nobody mistakes this for an authoritative number."""
    import shutil as _shutil
    system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    total, used, free = _shutil.disk_usage(system_drive)
    free_gb = free / (1024 ** 3)
    threshold_gb = 20  # a commonly-cited practical minimum, not an official one
    if free_gb < threshold_gb:
        return Finding(
            id="upgrade_headroom",
            title=f"Only {free_gb:.1f} GB free on {system_drive}",
            detail=(
                f"Feature updates commonly need roughly {threshold_gb} GB "
                "of free space to install (a practical rule of thumb, not "
                "an official Microsoft minimum) -- you're below that."
            ),
            severity="warning",
        )
    return Finding(
        id="upgrade_headroom",
        title=f"{free_gb:.1f} GB free on {system_drive}",
        detail=f"Above the ~{threshold_gb} GB practical minimum for a feature update.",
        severity="info",
    )


def all_findings() -> List[Finding]:
    findings = []
    pending = check_pending_servicing()
    if pending:
        findings.append(pending)
    findings.extend(check_orphaned_scheduled_tasks())
    findings.append(check_upgrade_headroom())
    return findings
```

### 2.3 `servicing.py` — the DISM actions

```python
"""DISM component-store servicing actions. No Qt -- callers (the module's
UI, and the --unattended stage) both drive this the same way."""
import subprocess
from dataclasses import dataclass
from typing import Optional


CREATE_NO_WINDOW = 0x08000000


@dataclass
class DismResult:
    command: str
    returncode: int
    output: str


def run_scan_health(timeout: int = 600) -> DismResult:
    """DISM /Online /Cleanup-Image /ScanHealth -- checks the component
    store for corruption. Does not repair anything (that's /RestoreHealth,
    not offered here -- repairing corruption this module merely detects
    is real scope beyond "system health findings")."""
    cmd = ["dism", "/Online", "/Cleanup-Image", "/ScanHealth"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_component_cleanup(timeout: int = 1800) -> DismResult:
    """Moved verbatim from _large_items_tab.py's _run_dism -- same
    command, same shape, just relocated (a servicing operation, not a
    file to select-and-delete)."""
    cmd = ["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_reset_base(timeout: int = 1800) -> DismResult:
    """DISM /Online /Cleanup-Image /StartComponentCleanup /ResetBase --
    removes the ABILITY to uninstall superseded Windows updates. This is
    the single most irreversible action in this module. The UI layer
    (system_health_module.py) is responsible for every gate before this
    is ever called: a fresh ScanHealth showing no corruption, a typed
    "RESETBASE" confirmation, and a forced restore point -- this function
    itself does none of that gating, it only runs the command, so the
    gating logic is testable independent of ever actually invoking DISM."""
    cmd = ["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup", "/ResetBase"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))
```

### 2.4 `history.py` — mirrors `updates/history_writer.py`

Same shape (capped JSON array at `{app_data_dir}/system_health/history.json`),
new file rather than extending the Updates module's own history file —
this module's entries (`{ts, action, command, returncode, findings_count}`)
don't fit Update Center's `{ts, freed, updates, wg}` shape, and mixing
unrelated history streams into one file is worse than two small,
separately-typed ones.

### 2.5 UI: `system_health_module.py`, 2 tabs

**"Findings" tab** — read-only, works unelevated (`read_only_unelevated`):
a "Refresh Findings" button running `findings.all_findings()` on a
`Worker`, rendered as a simple list (icon by severity, title, detail),
matching the visual weight of a warning/info row elsewhere in this app
(reuse `core.semantic.semantic()` color tokens, not new colors).

**"Servicing" tab** — admin-gated actions:
- "Check for Corruption" (ScanHealth) — command-transparent confirm
  ("Runs: dism /Online /Cleanup-Image /ScanHealth — checks the component
  store for corruption. Read-only, can take several minutes. Continue?"),
  sets `self._last_scan_health_clean = (returncode == 0)` on completion
  (DISM's own documented convention: 0 = no corruption found).
- "Component Cleanup" (moved from Large Items) — same command-transparent
  confirm pattern, same DISM call as today's Large Items button.
- "Reset Base" — gated behind ALL of:
  1. `self._last_scan_health_clean is True` (button disabled otherwise,
     tooltip explains why: "Run Check for Corruption first, with a clean
     result, before Reset Base becomes available").
  2. A typed-confirmation dialog: the user must type the literal word
     `RESETBASE` into a text field before the Ok button enables (not a
     checkbox — a checkbox is too easy to click through without reading).
  3. A forced restore point via `core.system_restore` (the SAME real
     `Checkpoint-Computer` mechanism the Updates module's Windows Updates
     tab already uses for its own opt-in pre-install restore point —
     re-used here, not reimplemented) — created automatically, not
     optional, immediately before the DISM call.
  4. Never appears in any "select all"/bulk list — it's a dedicated
     button with its own row, not a checkbox in a tree.

### 2.6 `--unattended --stages health`

New `run_health_stage(app, log, is_cancelled) -> dict` in
`modules/updates/stage_runners.py`, added to `STAGE_RUNNERS`. Calls
`findings.all_findings()` only — never `servicing.py`'s DISM functions.
Writes the findings to `system_health.history.append_run(...)`. Matches
the existing stage-runner shape exactly (same `(app, log, is_cancelled)
-> dict` signature every other stage already uses).

## 3. Data flow

Findings tab: `Worker` runs `findings.all_findings()` → renders a list →
optionally logs to `history.py` on explicit "Save to History" (not
automatic on every refresh, to avoid a 200-entry cap filling up from
someone just browsing the tab).

Servicing tab: each button is its own confirm → `Worker` (using
`get_long_op_pool()`, matching the existing Large Items DISM pattern,
since these can run for minutes) → `DismResult` → logged to `history.py`
unconditionally (a servicing action's outcome is always worth a
permanent record, unlike a findings-browse).

## 4. Error handling

- `check_orphaned_scheduled_tasks`'s `schtasks` refusal produces a
  `Finding` naming the refusal, never an empty list read as "no orphaned
  tasks" (this module's own version of "a refusal is never an answer").
- Every DISM call's `DismResult.returncode` is shown verbatim in the
  UI — never collapsed into a boolean "worked"/"failed" the way
  `snapshots._looks_refused` warns against elsewhere in this codebase
  (DISM's own exit codes carry real meaning: 0 = success, 3010 = reboot
  required, both distinct from a genuine failure).

## 5. Testing

- `tests/test_system_health_findings.py` (new): each of the 3 check
  functions against realistic fake inputs — a real `pending.xml`-shaped
  temp file present/absent, a fake `schtasks` CSV+XML output pair (one
  task pointing at a real temp exe, one at a path that doesn't exist,
  one bare command resolved via `shutil.which`), a monkeypatched
  `shutil.disk_usage` above and below the threshold. A refused
  `schtasks` call (non-zero exit, or `OSError`) must produce a Finding
  naming the refusal, never an empty list.
- `tests/test_system_health_servicing.py` (new): `run_scan_health`/
  `run_component_cleanup`/`run_reset_base` each build the exact
  documented DISM command line (mock `subprocess.run`, assert on the
  `cmd` list passed) and return a `DismResult` carrying the real
  `returncode`/output — never invoked against the real system in tests.
- `tests/test_system_health_module.py` (new): the ResetBase button's
  gating — disabled until `_last_scan_health_clean is True`; the typed
  "RESETBASE" confirmation dialog's Ok button stays disabled until the
  exact string is typed; a forced restore point is created (mock
  `core.system_restore`) before the DISM call, not after or skipped.
- `tests/test_stage_runners.py` (existing file — add to it): `run_health_stage`
  calls `findings.all_findings()` and never imports/calls anything from
  `servicing.py`.

## 6. Non-goals

- Pagefile deletion/disable, Windows Search service disable, Defender
  quarantine handling, telemetry *service* toggling — explicitly never
  in scope for this module (carried over verbatim from the original
  outline).
- Orphaned SERVICES (only orphaned scheduled TASKS are in this pass —
  §1).
- VSS shadow-storage floor management (§1).
- DISM `/RestoreHealth` (repairing corruption ScanHealth finds) — this
  module DETECTS corruption, it doesn't fix it; repairing it needs a
  source (Windows Update or install media) this module has no opinion
  about, and is real, separate scope.
- A generic "dry run" toggle (§1 explains why this doesn't map onto
  real DISM capabilities) — ResetBase's own precondition (a clean,
  same-session ScanHealth) is this sub-project's substitute.
