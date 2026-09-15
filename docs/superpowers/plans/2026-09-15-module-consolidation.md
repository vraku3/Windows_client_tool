# Module Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the redundant `PerfTunerModule`, and consolidate the
duplicated one-click repair logic spread across Quick Fix and Quick
Cleanup's one-click panel into a single home (Quick Fix), moving the
three DISM/disk-repair actions that belong there into System Health's
Servicing tab instead — with zero loss of existing functionality, and
three small additive improvements (search bar, run history, a
discoverability nudge for the retired module).

**Architecture:** Two independent halves that touch disjoint files (Part
A: Tweaks + Performance Tuner deletion; Part B: Quick Fix + Quick Cleanup
+ System Health), executed as one sequential task list in one worktree
since neither half depends on the other finishing first, but running them
concurrently would violate this repo's "never two implementer dispatches
in the same worktree at once" rule for no real benefit.

**Tech Stack:** PyQt6, existing `Worker`/`TweakEngine`/`core.long_op_pool`
infrastructure — no new libraries.

**Spec:** `docs/superpowers/specs/2026-09-15-module-consolidation-design.md`

## Global Constraints

- No functionality may be lost. Every existing one-click action, its
  exact underlying command, and its existing confirmation text (where
  present) must survive the move to a new home.
- Every new UI element follows this repo's dark theme / `semantic()`
  color conventions already used throughout `quick_fix_module.py`,
  `quick_cleanup_tab.py`, and `system_health_module.py` — no new styling
  vocabulary.
- Full test suite (`rm -rf .pytest-tmp` first, then
  `pytest -q --timeout=300`) must be clean before this plan is considered
  done — this repo has a known stale-lock flake fixed by that `rm -rf`
  (see `cleanup-suite-flaky-crash` memory), not a real regression.

---

### Task 1: Add the two missing tweak definitions

**Files:**
- Modify: `src/modules/tweaks/definitions/performance.json`
- Modify: `src/modules/tweaks/definitions/privacy.json`
- Test: `tests/test_tweak_definitions.py` (existing, no changes needed —
  it validates every file structurally; this task just needs its
  additions to pass that existing suite)

**Interfaces:**
- Consumes: nothing new — reuses `command` step type + `file_absent`
  detect probe (already documented in CLAUDE.md's Tweak System section),
  and plain `registry` step type.
- Produces: two new tweak ids, `disable_hibernation` and
  `disable_background_apps_global`, consumed by Task 2's expanded preset.

- [ ] **Step 1: Add `disable_hibernation` to `performance.json`**

Open `src/modules/tweaks/definitions/performance.json` and add a new
entry to its top-level list (match the existing entries' exact key
ordering/style in that file):

```json
{
  "id": "disable_hibernation",
  "name": "Disable Hibernation",
  "category": "performance",
  "description": "Runs powercfg /hibernate off, removing hiberfil.sys and freeing disk space equal to installed RAM.",
  "risk": "Low",
  "steps": [
    {"type": "command", "cmd": "powercfg /hibernate off"}
  ],
  "detect": {
    "type": "file_absent",
    "path": "%SystemDrive%\\hiberfil.sys"
  }
}
```

(If `performance.json`'s existing entries carry additional required
fields such as `applies_to` or `reboot`, match whatever the file's other
`command`-type entries already declare — read 2-3 neighboring entries
first and mirror their shape exactly rather than guessing.)

- [ ] **Step 2: Add `disable_background_apps_global` to `privacy.json`**

```json
{
  "id": "disable_background_apps_global",
  "name": "Disable Background App Access (Global)",
  "category": "privacy",
  "description": "Globally prevents UWP apps from running and using resources in the background.",
  "risk": "Low",
  "steps": [
    {"type": "registry",
     "key": "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\BackgroundAccessApplications",
     "value": "GlobalUserDisabled", "data": 1, "kind": "DWORD"}
  ]
}
```

Again, mirror `privacy.json`'s existing entry shape exactly (field
ordering, whether `risk` is present on neighboring entries, etc.) rather
than inventing a new shape.

- [ ] **Step 3: Run the structural validator**

Run: `python -m pytest -q tests/test_tweak_definitions.py --timeout=60`
Expected: PASS, no new failures. This test enumerates every definition
file and checks id uniqueness, hive names, DWORD-vs-SZ types, etc. — it
will catch a malformed addition here.

- [ ] **Step 4: Commit**

```bash
git add src/modules/tweaks/definitions/performance.json src/modules/tweaks/definitions/privacy.json
git commit -m "feat(tweaks): add disable_hibernation and disable_background_apps_global tweaks"
```

---

### Task 2: Expand the builtin "Performance" preset

**Files:**
- Modify: `src/modules/tweaks/definitions/builtins/performance.json`
- Test: `tests/test_performance_preset_ids_resolve.py` (new)

**Interfaces:**
- Consumes: every tweak id named in the table below, which must exist
  somewhere across `src/modules/tweaks/definitions/*.json` (checked by
  the new test) — includes Task 1's two new ids.
- Produces: nothing new consumed elsewhere; this is a leaf change.

- [ ] **Step 1: Read the current file**

`src/modules/tweaks/definitions/builtins/performance.json` currently
looks like:

```json
{
  "name": "Performance",
  "version": 1,
  "builtin": true,
  "description": "Maximize system performance: power plan, visual effects, NTFS tweaks, CPU/memory optimizations, and background service reduction.",
  "tweaks": {
    "performance": [
      "disable_superfetch", "high_perf_power_plan", "disable_search_indexing",
      "visual_effects_best_performance", "disable_transparency",
      "disable_ntfs_8dot3", "disable_ntfs_last_access", "enable_hags",
      "boost_multimedia_scheduling", "instant_menu_response",
      "disable_startup_delay", "disable_edge_preload", "keep_kernel_in_ram",
      "boost_foreground_cpu_priority"
    ],
    "services": ["disable_delivery_optimization", "disable_xbox_services", "disable_fax", "disable_tablet_input", "disable_superfetch_service", "disable_connected_devices_platform", "disable_alljoyn_router", "disable_downloaded_maps_manager", "disable_diagnostics_hub"],
    "network": ["disable_qos_reserved_bandwidth", "optimise_dns_cache"],
    "ui_tweaks": ["disable_taskbar_news", "disable_taskbar_widgets", "disable_snap_suggestions"]
  },
  "apps": {}
}
```

- [ ] **Step 2: Add the 10 missing ids under the right category key**

Add `disable_hibernation`, `disable_prefetch`, `keep_kernel_in_ram`
(already present — do not duplicate), `disable_background_apps_global`,
`disable_error_reporting`→ use the real id found in `telemetry.json`
(`disable_wersvc`) to the `performance` list; `taskbar_start.json`'s
animation-disable id and `ui_tweaks.json`'s aero-peek id to the
`ui_tweaks` list; `power.json`'s power-throttling id to a new `power`
list; `gaming.json`/`multimedia.json`'s network-throttling id to the
`network` list; and the remote-registry/diagtrack service-disable ids to
the `services` list.

Before writing the final list, run this to get the EXACT ids (names
guessed above may not match the real id strings in each file):

```bash
grep -h '"id"' src/modules/tweaks/definitions/taskbar_start.json src/modules/tweaks/definitions/ui_tweaks.json src/modules/tweaks/definitions/power.json src/modules/tweaks/definitions/gaming.json src/modules/tweaks/definitions/multimedia.json src/modules/tweaks/definitions/remote.json src/modules/tweaks/definitions/security.json src/modules/tweaks/definitions/services.json src/modules/tweaks/definitions/telemetry.json | grep -iE "animat|aero|preview|throttl|prefetch|remote.?registry|diagtrack|error.?report|wer"
```

Use the exact id strings that command prints. Add each to the
appropriate category list in `performance.json` (the builtin), creating
a new `"power": [...]` list if none exists yet. Do NOT add
`enable_game_mode`/`disable_game_dvr` (deliberately excluded — see spec
§2.3 item 2).

- [ ] **Step 3: Write the id-resolution test**

```python
# tests/test_performance_preset_ids_resolve.py
"""Every tweak id the builtin 'Performance' preset names must exist
somewhere in the real tweak catalog -- a dead id is a silent no-op when
the preset is applied, and this preset now stands in for the retired
Performance Tuner module (see docs/superpowers/specs/
2026-09-15-module-consolidation-design.md)."""
import json
import os

_DEFS = os.path.join(os.path.dirname(__file__), "..", "src", "modules",
                     "tweaks", "definitions")
_BUILTINS = os.path.join(_DEFS, "builtins")


def _all_real_tweak_ids():
    ids = set()
    for filename in os.listdir(_DEFS):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(_DEFS, filename)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "id" in entry:
                ids.add(entry["id"])
    return ids


def test_every_performance_preset_id_resolves_to_a_real_tweak():
    with open(os.path.join(_BUILTINS, "performance.json"), encoding="utf-8") as f:
        preset = json.load(f)
    real_ids = _all_real_tweak_ids()
    named = {tid for ids in preset["tweaks"].values() for tid in ids}
    missing = named - real_ids
    assert missing == set(), f"performance.json preset names dead ids: {sorted(missing)}"


def test_the_preset_covers_every_mapped_performance_tuner_check():
    """See the design spec's §2.2 table -- 25 of PerfTuner's 27 checks
    map to a real tweak id; this preset should now name all of them
    (except the two gaming ones, deliberately excluded)."""
    with open(os.path.join(_BUILTINS, "performance.json"), encoding="utf-8") as f:
        preset = json.load(f)
    named = {tid for ids in preset["tweaks"].values() for tid in ids}
    assert "disable_hibernation" in named
    assert "disable_background_apps_global" in named
    assert "enable_game_mode" not in named
    assert "disable_game_dvr" not in named
```

- [ ] **Step 4: Run the new test and the full tweak-definitions suite**

Run: `python -m pytest -q tests/test_performance_preset_ids_resolve.py tests/test_tweak_definitions.py --timeout=60`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/modules/tweaks/definitions/builtins/performance.json tests/test_performance_preset_ids_resolve.py
git commit -m "feat(tweaks): expand the Performance preset to cover every mapped Performance Tuner check"
```

---

### Task 3: Delete `PerfTunerModule`

**Files:**
- Delete: `src/modules/performance_tuner/` (entire directory)
- Modify: `src/main.py`
- Modify: `tests/test_delivery_optimization.py`
- Modify: `tests/test_module_inventory.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: Tasks 1-2 must be done first (this task removes the only
  code that read the 2 gaps' old ad-hoc detectors; the real replacement
  — the Tweaks catalog + expanded preset — must already exist).
- Produces: nothing (terminal step of Part A).

- [ ] **Step 1: Remove the registration in `main.py`**

In `src/main.py`, remove this import (around line 120):
```python
    from modules.performance_tuner.perf_tuner_module import PerfTunerModule
```
and this registration (around line 161, under the `# Batch C` comment):
```python
    app.module_registry.register(PerfTunerModule())
```

- [ ] **Step 2: Delete the module directory**

```bash
rm -rf src/modules/performance_tuner
```

- [ ] **Step 3: Remove the 4 Performance-Tuner-dependent tests from `test_delivery_optimization.py`**

Open `tests/test_delivery_optimization.py` and delete these 4 test
functions (they test `perf_checks.py`'s own detector, which no longer
exists): `test_the_performance_tuner_uses_the_policy_too`,
`test_the_performance_tuner_detector_reads_what_it_writes`,
`test_the_detector_calls_peering_suboptimal`,
`test_an_unset_policy_is_suboptimal_not_unknown`. Keep the other 3 tests
in that file (`test_the_tweak_stops_sharing_by_policy_not_by_the_service`,
`test_the_policy_value_actually_means_no_peering`,
`test_no_definition_still_tries_to_disable_dosvc`) — they test the real
`services.json` tweak, unaffected by this deletion. Also update the
file's module docstring (top of file) to drop its reference to "the
Performance Tuner" in the "The ids stay put: six built-in presets and
the Performance Tuner reference them" sentence — say "six built-in
presets" only, since Performance Tuner is now retired.

- [ ] **Step 4: Update the sidebar-count test**

In `tests/test_module_inventory.py`, rename
`test_the_sidebar_is_33_entries` to `test_the_sidebar_is_32_entries`,
change `assert len(registered) == 33` to `assert len(registered) == 32`,
and update the docstring to explain the new count: Performance Tuner
retired (its functionality fully absorbed into the Tweaks module's
"Performance" builtin preset, expanded in this same round — see
`docs/superpowers/specs/2026-09-15-module-consolidation-design.md`).

- [ ] **Step 5: Update CLAUDE.md**

Remove the entire `### PerfTunerModule UI Pattern
(`src/modules/performance_tuner/perf_tuner_module.py`)` section (one
paragraph). Nothing else in CLAUDE.md references this pattern by name
(verified via `grep -rn "PerfTuner" CLAUDE.md`), so no other section
needs a follow-up edit.

- [ ] **Step 6: Verify no dangling references remain**

Run: `grep -rln "performance_tuner\|PerfTunerModule\|perf_checks\|PERF_CHECKS" --include="*.py" src tests tools`
Expected: no output (empty).

- [ ] **Step 7: Run the affected tests**

Run: `python -m pytest -q tests/test_delivery_optimization.py tests/test_module_inventory.py --timeout=60`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A src/modules/performance_tuner src/main.py tests/test_delivery_optimization.py tests/test_module_inventory.py CLAUDE.md
git commit -m "feat: retire PerfTunerModule -- fully absorbed into the Tweaks Performance preset"
```

This completes Part A.

---

### Task 4: Add confirmation and long-running support to `FixAction`/`_FixCard`

**Files:**
- Modify: `src/modules/quick_fix/fix_actions.py`
- Modify: `src/modules/quick_fix/quick_fix_module.py`
- Test: `tests/test_quick_fix_module.py` (new)

**Interfaces:**
- Produces: `FixAction` gains `confirm_text: Optional[str] = None`,
  `precondition: Optional[Callable[[], Optional[str]]] = None`, and
  `long_running: bool = False` fields. `_FixCard._run()` consults all
  three before dispatching a worker. Tasks 5-7 rely on these fields
  existing.

- [ ] **Step 1: Extend `FixAction`**

In `src/modules/quick_fix/fix_actions.py`, change the dataclass:

```python
@dataclass
class FixAction:
    key: str                          # unique identifier
    title: str
    description: str
    category: str
    reboot_required: bool = False
    fn: Optional[Callable[[Callable[[str], None]], None]] = field(default=None, repr=False)
    # fn signature: fn(output_cb: Callable[[str], None]) -> None
    # output_cb is called with each line of output
    confirm_text: Optional[str] = None
    # When set, _FixCard shows an Ok/Cancel QMessageBox with this text
    # (default button Cancel) before running -- ports over Quick
    # Cleanup's per-action confirm dialogs during the Part B merge.
    precondition: Optional[Callable[[], Optional[str]]] = field(default=None, repr=False)
    # Called just before running (after any confirm). Returning None
    # means proceed; returning a string shows it in the card's status
    # label instead of running -- e.g. "hibernation is off, nothing to
    # resize" for the migrated Resize Hibernation action.
    long_running: bool = False
    # True routes the worker to core.long_op_pool.get_long_op_pool()
    # instead of the shared global QThreadPool -- for actions that can
    # run 10-30 minutes (Compact WinSxS, WU Deep Clean), matching how
    # Quick Cleanup and System Health already isolate their own
    # long-running DISM calls from everything else's worker pool.
```

- [ ] **Step 2: Wire the three fields into `_FixCard._run()`**

In `src/modules/quick_fix/quick_fix_module.py`, add imports:
```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QFrame, QScrollArea, QGridLayout, QMessageBox, QLineEdit,
)
```
(adds `QMessageBox` and `QLineEdit` — the latter is for Task 8, added
here to touch the import line once).

Then add `from core.long_op_pool import get_long_op_pool` near the top
imports, and rewrite `_FixCard._run`:

```python
    def _run(self):
        if self._running:
            return
        action = self._action

        if action.precondition is not None:
            msg = action.precondition()
            if msg is not None:
                self._status.setText(msg)
                return

        if action.confirm_text is not None:
            mb = QMessageBox(self)
            mb.setWindowTitle(action.title)
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setText(action.confirm_text)
            mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if mb.exec() != QMessageBox.StandardButton.Ok:
                return

        self._running = True
        self._run_btn.setEnabled(False)
        self._output.clear()
        self._output.show()
        self._status.setText("Running...")

        def append(line: str):
            self._line.emit(line)

        def do_work(_w):
            self._worker = _w
            action.fn(append)

        self._worker = Worker(do_work)
        self._worker.signals.result.connect(lambda _r: self._on_done())
        self._worker.signals.error.connect(self._on_error)
        pool = get_long_op_pool() if action.long_running else self._thread_pool
        pool.start(self._worker)
```

(The rest of `_FixCard` — `_setup_ui`, `_on_done`, `_on_error`, `cancel`
— is unchanged.)

- [ ] **Step 3: Write the regression tests**

```python
# tests/test_quick_fix_module.py
"""_FixCard's confirm/precondition/long-running mechanism, added for the
module-consolidation merge (docs/superpowers/specs/
2026-09-15-module-consolidation-design.md) -- migrating Quick Cleanup's
confirmed actions into Quick Fix's card UI must not silently drop their
confirmation dialogs."""
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QMessageBox

from modules.quick_fix.fix_actions import FixAction
from modules.quick_fix.quick_fix_module import _FixCard


@pytest.fixture
def card(qapp):
    calls = []
    action = FixAction("t", "Test Action", "desc", "Test", fn=lambda cb: calls.append(1))
    c = _FixCard(action)
    return c, calls


def test_an_action_with_no_confirm_or_precondition_runs_immediately(card, qtbot=None):
    c, calls = card
    c._run()
    c._worker.fn(c._worker)  # run synchronously in-test
    assert calls == [1]


def test_a_confirm_text_action_does_not_run_when_cancelled(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        confirm_text="Are you sure?")
    c = _FixCard(action)
    with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel):
        c._run()
    assert calls == []
    assert not c._running


def test_a_confirm_text_action_runs_when_accepted(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        confirm_text="Are you sure?")
    c = _FixCard(action)
    with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok):
        c._run()
    c._worker.fn(c._worker)
    assert calls == [1]


def test_a_precondition_returning_a_message_blocks_the_run_and_shows_it(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        precondition=lambda: "nothing to do here")
    c = _FixCard(action)
    c._run()
    assert calls == []
    assert c._status.text() == "nothing to do here"


def test_a_precondition_returning_none_lets_the_action_run(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1),
                        precondition=lambda: None)
    c = _FixCard(action)
    c._run()
    c._worker.fn(c._worker)
    assert calls == [1]


def test_a_long_running_action_uses_the_long_op_pool(qapp):
    from core.long_op_pool import get_long_op_pool
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: None, long_running=True)
    c = _FixCard(action)
    with patch.object(get_long_op_pool(), "start") as mock_start:
        c._run()
        assert mock_start.called
```

(A `qapp` fixture providing a live `QApplication` is already used
throughout this test suite — e.g. `tests/test_store_apps_module.py`;
reuse whatever conftest fixture those files import, do not redefine it
here.)

- [ ] **Step 4: Run the new tests**

Run: `python -m pytest -q tests/test_quick_fix_module.py --timeout=60`
Expected: PASS.

- [ ] **Step 5: Run the existing Quick Fix action tests to confirm no regression**

Run: `python -m pytest -q tests/test_quick_fix_actions.py --timeout=60`
Expected: PASS unchanged (this task adds fields with safe defaults; no
existing `FixAction` construction changes behavior).

- [ ] **Step 6: Commit**

```bash
git add src/modules/quick_fix/fix_actions.py src/modules/quick_fix/quick_fix_module.py tests/test_quick_fix_module.py
git commit -m "feat(quick-fix): add confirm/precondition/long-running support to FixAction and _FixCard"
```

---

### Task 5: Retarget the 7 overlapping actions

**Files:**
- Modify: `src/modules/quick_fix/fix_actions.py`
- Modify: `tests/test_quick_fix_actions.py`

**Interfaces:**
- Consumes: Task 4's `confirm_text`/`long_running` fields.
- Produces: the 7 actions listed in spec §3.1, each carrying the winning
  command and (where Quick Cleanup had one) the winning confirm text.

- [ ] **Step 1: Replace `clear_print_queue`'s body**

Quick Cleanup's version has an unconditional-restart fix Quick Fix's
never received. Replace the function in `fix_actions.py`:

```python
def clear_print_queue(output_cb: Callable[[str], None]) -> None:
    """Stop the spooler, clear queued jobs, and ALWAYS restart the
    spooler even if a spool file was locked and could not be deleted --
    an all-or-nothing chain here would leave printing broken with just a
    generic error. (Ported from Quick Cleanup's _clear_print_queue,
    which carries this fix; Quick Fix's own prior version did not.)"""
    _stop_service("Spooler", output_cb)
    spool_dir = r"C:\Windows\System32\spool\PRINTERS"
    if os.path.isdir(spool_dir):
        for f in os.listdir(spool_dir):
            fpath = os.path.join(spool_dir, f)
            try:
                os.remove(fpath)
                output_cb(f"Deleted: {f}")
            except OSError as e:
                output_cb(f"Skip {f}: {e}")
    _start_service("Spooler", output_cb)
    output_cb("Print queue cleared.")
```

(Same as before — the "always restart" behavior already exists here
because `_start_service` is called unconditionally after the `for` loop
regardless of individual file-delete failures; this is equivalent to
Quick Cleanup's `& (del ... & net start)` shell chain, just expressed in
Python control flow instead of a shell `&`.)

- [ ] **Step 2: Replace `reset_windows_update`'s body with the more thorough version (already what Quick Fix has) and update its confirm text**

Quick Fix's existing `reset_windows_update` (stops
wuauserv+cryptsvc+bits+msiserver, clears SoftwareDistribution AND
catroot2) is already the more thorough of the two — no code change
needed here, only its `FixAction` entry gains `confirm_text` in Step 5.

- [ ] **Step 3: Add `confirm_text` to the `winsock`, `tcpip`, and `network_reset` entries**

These 3 actions currently run with zero confirmation. Quick Cleanup's
equivalents (`_reset_tcpip`, `_network_repair`) had confirms — port the
wording over.

- [ ] **Step 4: Add `confirm_text` to `wu_reset`**

Quick Cleanup's `_flush_wu_store` had a confirm; port its wording,
updated to also mention `catroot2` since this surviving implementation
clears that too.

- [ ] **Step 5: Update the `ALL_ACTIONS` entries**

```python
    FixAction("winsock", "Reset Winsock", "Reset network socket catalog (reboot required)",
              "Network", reboot_required=True, fn=reset_winsock,
              confirm_text="This will reset the Winsock network socket catalog. "
                           "A reboot is required to complete. Continue?"),
    FixAction("tcpip", "Reset TCP/IP", "Reset TCP/IP stack (reboot required)",
              "Network", reboot_required=True, fn=reset_tcpip,
              confirm_text="This will reset all network adapter TCP/IP configurations. "
                           "Network adapters may briefly disconnect. This cannot be undone. Continue?"),
    FixAction("ip_renew", "IP Release/Renew", "Release and renew IP address",
              "Network", fn=ip_release_renew),
    FixAction("network_reset", "Network Reset", "Reset all network adapters to default (reboot required)",
              "Network", reboot_required=True, fn=network_reset,
              confirm_text="This will reset Winsock and the TCP/IP stack. "
                           "Your network connection will briefly drop. This cannot be undone. Continue?"),
    # Windows Update
    FixAction("wu_reset", "Reset Windows Update",
              "Stop WU services, clear caches, restart",
              "Windows Update", fn=reset_windows_update,
              confirm_text="This will reset the Windows Update client, clear the "
                           "SoftwareDistribution and catroot2 caches, and restart the "
                           "affected services. This cannot be undone. Continue?"),
```

Leave `ip_renew`, `wu_dlls`, `wu_scan` as-is (no confirm in either
source module for these).

- [ ] **Step 6: Update `test_quick_fix_actions.py`'s print-queue test if it asserts old behavior**

Read the existing test file first — if no test currently pins
`clear_print_queue`'s exact restart-on-failure behavior, add one:

```python
def test_print_queue_restarts_the_spooler_even_if_a_delete_fails(monkeypatch, output):
    """Ported from Quick Cleanup's own fix -- a locked spool file must
    not leave the spooler stopped."""
    lines, cb = output
    calls = []

    def fake_stop(name, output_cb):
        calls.append(("stop", name))

    def fake_start(name, output_cb):
        calls.append(("start", name))

    monkeypatch.setattr(fix_actions, "_stop_service", fake_stop)
    monkeypatch.setattr(fix_actions, "_start_service", fake_start)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(os, "listdir", lambda p: ["locked.spl"])
    monkeypatch.setattr(os, "remove", lambda p: (_ for _ in ()).throw(OSError("locked")))

    fix_actions.clear_print_queue(cb)

    assert ("start", "Spooler") in calls, "spooler was not restarted after a failed delete"
```

(Add `import os` at the top of the test file if not already present.)

- [ ] **Step 7: Run the tests**

Run: `python -m pytest -q tests/test_quick_fix_actions.py --timeout=60`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/modules/quick_fix/fix_actions.py tests/test_quick_fix_actions.py
git commit -m "fix(quick-fix): retarget 7 overlapping actions to their safer command + carry over confirmations"
```

---

### Task 6: Migrate Quick Cleanup's unique actions into Quick Fix; delete Quick Cleanup's one-click panel

**Files:**
- Modify: `src/modules/quick_fix/fix_actions.py`
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Modify: `tests/test_quick_fix_actions.py`
- Modify: `tests/test_quick_cleanup_one_click_actions.py`

**Interfaces:**
- Consumes: Task 4's `confirm_text`/`precondition`/`long_running` fields.
- Produces: a new `"Cleanup"` category in `fix_actions.ALL_ACTIONS`
  holding the 7 migrated actions; `quick_cleanup_tab.py` loses its
  one-click panel entirely.

- [ ] **Step 1: Add the 7 migrated functions to `fix_actions.py`**

```python
def clear_event_logs(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _clear_event_logs."""
    for log in ("System", "Application", "Security"):
        rc = _run_cmd(["wevtutil", "cl", log], output_cb)
        output_cb(f"Cleared {log} log" if rc == 0 else f"Could not clear {log} log (exit {rc})")


def compact_winsxs(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _compact_winsxs -- plain
    /StartComponentCleanup only, deliberately NOT /ResetBase (that flag
    lives solely in System Health's gated Reset Base, per the Sub-project
    4 safety fix -- do not reintroduce it here)."""
    _run_cmd(["Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], output_cb)


def wu_deep_clean(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _wu_deep_clean. Distinct from
    System Health's plain Component Cleanup: this adds
    /SuppressDefaultActions, and distinct from wu_reset above (that
    clears the WU cache/services; this reclaims WinSxS space)."""
    _run_cmd(["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup",
              "/SuppressDefaultActions"], output_cb)


def clear_clipboard(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _clear_clipboard."""
    _run_cmd(["cmd", "/c", "echo off | clip"], output_cb)
    output_cb("Clipboard cleared.")


def reset_search(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _reset_search."""
    _stop_service("WSearch", output_cb)
    _start_service("WSearch", output_cb)
    output_cb("Windows Search reset.")


def clear_font_cache(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _clear_font_cache."""
    _stop_service("FontCache", output_cb)
    _start_service("FontCache", output_cb)
    output_cb("Font cache cleared.")


def resize_hibernation(output_cb: Callable[[str], None]) -> None:
    """Ported from Quick Cleanup's _resize_hibernation. The hiberfil.sys
    existence gate moves to a `precondition` on the FixAction entry
    rather than living in this function, matching how every other
    precondition in this file's catalog works."""
    _run_cmd(["powercfg", "/hibernate", "/size", "50"], output_cb)
    output_cb("Hibernation file resized to 50% of RAM.")


def _hibernation_precondition() -> Optional[str]:
    system_drive = os.environ.get("SystemDrive", "C:")
    hiberfil = os.path.join(system_drive + "\\", "hiberfil.sys")
    if not os.path.exists(hiberfil):
        return "Hibernation is off on this machine — nothing to resize"
    return None
```

- [ ] **Step 2: Add the 7 entries to `ALL_ACTIONS` under a new "Cleanup" category**

```python
    # Cleanup
    FixAction("clear_event_logs", "Clear Event Logs",
              "Clear System, Application, and Security event logs",
              "Cleanup", fn=clear_event_logs,
              confirm_text="This will clear System, Application, and Security event logs. "
                           "They cannot be recovered. Continue?"),
    FixAction("compact_winsxs", "Compact WinSxS",
              "Run DISM component cleanup to reclaim WinSxS space",
              "Cleanup", fn=compact_winsxs, long_running=True,
              confirm_text="This runs DISM /StartComponentCleanup which can take "
                           "10–30 minutes. The system will remain usable. Continue?"),
    FixAction("wu_deep_clean", "WU Deep Clean",
              "Deep Windows Update component-store cleanup",
              "Cleanup", fn=wu_deep_clean, long_running=True,
              confirm_text="This runs a deep Windows Update cleanup which may take "
                           "10–20 minutes. Continue?"),
    FixAction("clear_clipboard", "Clear Clipboard", "Clear the Windows clipboard content",
              "Cleanup", fn=clear_clipboard),
    FixAction("reset_search", "Reset Windows Search",
              "Restart the Windows Search service and clear its database",
              "Cleanup", fn=reset_search,
              confirm_text="This will restart the Windows Search service and clear its "
                           "database. Search may be briefly unavailable. Continue?"),
    FixAction("clear_font_cache", "Clear Font Cache",
              "Flush the Windows Font Cache service",
              "Cleanup", fn=clear_font_cache,
              confirm_text="This will flush the Windows Font Cache by stopping the "
                           "FontCache service. Applications may briefly re-render text. Continue?"),
    FixAction("resize_hibernation", "Right-size Hibernation File",
              "Shrink hiberfil.sys to 50% of RAM without disabling hibernation",
              "Cleanup", fn=resize_hibernation, precondition=_hibernation_precondition,
              confirm_text="This shrinks hiberfil.sys to 50% of RAM (Windows' own default "
                           "since Windows 10) without disabling hibernation. Continue?"),
```

Add `Optional` to this file's `typing` import if not already present
(check the existing `from typing import Callable, List, Optional` line —
it already has `Optional`).

- [ ] **Step 3: Delete Quick Cleanup's one-click panel**

In `src/modules/cleanup/components/quick_cleanup_tab.py`, delete:
`_build_one_click_panel`, `_run_action_command`, and every `_action_*`
method (`_flush_dns`, `_clear_event_logs`, `_compact_winsxs`,
`_rebuild_icon_cache`, `_wu_deep_clean`, `_network_repair`,
`_clear_thumbnails`, `_clear_clipboard`, `_reset_search`,
`_clear_font_cache`, `_flush_wu_store`, `_reset_tcpip`,
`_resize_hibernation`, `_clear_print_queue`), the `_action_buttons`/
`_action_status` instance attributes, and the call site
`self._build_one_click_panel(layout)` in `build()`. Run
`grep -n "_action_buttons\|_action_status\|_build_one_click_panel\|_run_action_command" src/modules/cleanup/components/quick_cleanup_tab.py`
after editing — expect no output.

- [ ] **Step 4: Update `tests/test_quick_cleanup_one_click_actions.py`**

This file's 4 tests (`test_a_running_actions_own_button_is_disabled_
until_it_finishes`, `test_two_actions_running_at_once_do_not_clobber_
each_others_status`, `test_a_cancelled_one_click_action_re_enables_its_
button`, `test_compact_winsxs_also_guards_against_a_second_click`) all
test the now-deleted `_run_action_command`/button-guard mechanism.
Delete this test file entirely — Task 4's
`tests/test_quick_fix_module.py` already covers the equivalent
busy-guard behavior for `_FixCard` (a card's own `if self._running:
return` is exercised implicitly by any test that calls `_run()` twice;
add one explicit test there if none already covers it: calling `_run()`
twice while the first is "running" must not create a second worker).

- [ ] **Step 5: Add the busy-guard regression test to `test_quick_fix_module.py`**

```python
def test_running_a_card_twice_while_busy_does_not_start_a_second_worker(qapp):
    calls = []
    action = FixAction("t", "Test", "desc", "Test", fn=lambda cb: calls.append(1))
    c = _FixCard(action)
    c._run()
    first_worker = c._worker
    c._run()  # second call while "running" -- must be a no-op
    assert c._worker is first_worker
```

- [ ] **Step 6: Update `test_quick_fix_actions.py` for the 7 new functions**

Add coverage mirroring the existing
`test_the_repair_runs_the_command_it_says_it_does` parametrization for
at least `clear_event_logs`, `compact_winsxs`, and `resize_hibernation`
(the ones with the most command-shape risk):

```python
@pytest.mark.parametrize("action,expected_contains", [
    (fix_actions.compact_winsxs, "StartComponentCleanup"),
    (fix_actions.resize_hibernation, "hibernate"),
])
def test_the_migrated_cleanup_action_runs_the_expected_command(action, expected_contains, ran, output):
    lines, cb = output
    action(cb)
    flat = " ".join(" ".join(c) for c in ran)
    assert expected_contains in flat


def test_compact_winsxs_never_passes_resetbase():
    """The one remaining door to /ResetBase must stay System Health's
    gated Reset Base -- see the Sub-project 4 safety fix this must not
    reintroduce."""
    calls = []
    original = fix_actions._run_cmd
    fix_actions._run_cmd = lambda cmd, cb, input_bytes=None: (calls.append(list(cmd)), 0)[1]
    try:
        fix_actions.compact_winsxs(lambda _line: None)
    finally:
        fix_actions._run_cmd = original
    assert "/ResetBase" not in calls[0]


def test_hibernation_precondition_blocks_when_hiberfil_is_absent(monkeypatch):
    monkeypatch.setattr(fix_actions.os.path, "exists", lambda p: False)
    assert fix_actions._hibernation_precondition() is not None


def test_hibernation_precondition_allows_when_hiberfil_is_present(monkeypatch):
    monkeypatch.setattr(fix_actions.os.path, "exists", lambda p: True)
    assert fix_actions._hibernation_precondition() is None
```

- [ ] **Step 7: Run all affected tests**

Run: `python -m pytest -q tests/test_quick_fix_actions.py tests/test_quick_fix_module.py --timeout=60`
Expected: PASS. Also run
`python -m pytest -q tests/test_quick_cleanup*.py --timeout=60` to
confirm deleting the one-click-panel test file didn't leave anything
else in that directory broken.

- [ ] **Step 8: grep sweep for dangling references**

Run: `grep -rn "_build_one_click_panel\|_action_buttons\|_run_action_command" src/ tests/`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add src/modules/quick_fix/fix_actions.py src/modules/cleanup/components/quick_cleanup_tab.py tests/test_quick_fix_actions.py tests/test_quick_fix_module.py
git rm tests/test_quick_cleanup_one_click_actions.py
git commit -m "feat: migrate Quick Cleanup's one-click panel into Quick Fix, delete the panel"
```

---

### Task 7: Move SFC/RestoreHealth/CHKDSK to System Health's Servicing tab

**Files:**
- Modify: `src/modules/quick_fix/fix_actions.py`
- Modify: `src/modules/quick_fix/quick_fix_module.py` (no code change
  expected — `ALL_ACTIONS` losing 3 entries needs no other adjustment,
  confirm by re-reading `create_widget()`'s category-grouping loop)
- Modify: `tests/test_quick_fix_actions.py`
- Modify: `src/modules/system_health/servicing.py`
- Modify: `src/modules/system_health/system_health_module.py`
- Test: `tests/test_system_health_servicing.py`
- Test: `tests/test_system_health_module.py`

**Interfaces:**
- Consumes: `servicing.py`'s existing `DismResult` dataclass and
  `run_scan_health`/`run_component_cleanup` pattern.
- Produces: `servicing.run_sfc_scan()`, `servicing.run_restore_health()`,
  `servicing.run_chkdsk_schedule()`, each returning `DismResult`; three
  new buttons on System Health's Servicing tab.

- [ ] **Step 1: Add the three functions to `servicing.py`**

```python
def run_sfc_scan(timeout: int = 900) -> DismResult:
    """sfc /scannow -- scans and repairs protected Windows system files.
    Moved from Quick Fix (fix_actions.run_sfc) -- same command, same
    shape, relocated alongside the other servicing/repair operations."""
    cmd = ["sfc", "/scannow"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_restore_health(timeout: int = 1800) -> DismResult:
    """DISM /Online /Cleanup-Image /RestoreHealth -- repairs component
    store corruption ScanHealth detects. Moved from Quick Fix
    (fix_actions.run_dism); this module's ScanHealth previously
    explicitly deferred offering a repair action -- this is that
    deferred action, now added."""
    cmd = ["dism", "/online", "/cleanup-image", "/restorehealth"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))


def run_chkdsk_schedule(timeout: int = 60) -> DismResult:
    """Schedules CHKDSK C: /f /r /x for next reboot by answering the
    Y/N prompt. Moved from Quick Fix (fix_actions.run_chkdsk) -- same
    command and same answered-prompt trick, relocated."""
    cmd = ["chkdsk", "C:", "/f", "/r", "/x"]
    proc = subprocess.run(cmd, input="Y\n", capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return DismResult(" ".join(cmd), proc.returncode,
                      (proc.stdout or "") + (proc.stderr or ""))
```

- [ ] **Step 2: Update `servicing.py`'s ScanHealth docstring**

`run_scan_health`'s docstring currently says "Does not repair anything
(that's /RestoreHealth, not offered here...)". Update it to drop the
"not offered here" clause since Step 1 now offers it as its own function
— e.g. "Does not repair anything itself; see `run_restore_health` for
the repair action."

- [ ] **Step 3: Add three buttons to the Servicing tab**

In `system_health_module.py`'s `_build_servicing_tab`, add after the
existing `self._reset_base_btn` block (before `lay.addWidget(self._servicing_out)`):

```python
        self._sfc_btn = QPushButton("🩹  SFC Scan")
        self._sfc_btn.setToolTip(
            "Runs: sfc /scannow\n"
            "Scans and repairs protected Windows system files. "
            "Can take 10-15 minutes."
        )
        self._sfc_btn.clicked.connect(self._run_sfc_scan)
        lay.addWidget(self._sfc_btn)

        self._restore_health_btn = QPushButton("🩹  DISM RestoreHealth")
        self._restore_health_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /RestoreHealth\n"
            "Repairs component-store corruption ScanHealth detects. "
            "Can take 10-30 minutes."
        )
        self._restore_health_btn.clicked.connect(self._run_restore_health)
        lay.addWidget(self._restore_health_btn)

        self._chkdsk_btn = QPushButton("🩹  Schedule CHKDSK")
        self._chkdsk_btn.setToolTip(
            "Runs: chkdsk C: /f /r /x\n"
            "Schedules a full disk check and repair for the next reboot."
        )
        self._chkdsk_btn.clicked.connect(self._run_chkdsk_schedule)
        lay.addWidget(self._chkdsk_btn)
```

- [ ] **Step 4: Add the three handler methods**

Following the exact pattern `_run_component_cleanup` already uses
(confirm dialog, disable button, worker on `self._servicing_pool`,
write to `history.append_run`):

```python
    def _run_sfc_scan(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        mb = QMessageBox(self._tabs)
        mb.setWindowTitle("SFC Scan")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText("Runs: sfc /scannow\n\nScans and repairs protected Windows "
                   "system files. Can take 10-15 minutes. Continue?")
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return

        self._sfc_btn.setEnabled(False)
        self._servicing_out.setText("Running SFC scan (can take 10-15 minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_sfc_scan()

        def _done(result):
            self._sfc_btn.setEnabled(True)
            self._servicing_out.setText(
                f"SFC scan finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="sfc_scan",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._sfc_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)

    def _run_restore_health(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        mb = QMessageBox(self._tabs)
        mb.setWindowTitle("DISM RestoreHealth")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText("Runs: dism /Online /Cleanup-Image /RestoreHealth\n\n"
                   "Repairs component-store corruption. Can take 10-30 minutes. Continue?")
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return

        self._restore_health_btn.setEnabled(False)
        self._servicing_out.setText("Running DISM RestoreHealth (can take 10-30 minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_restore_health()

        def _done(result):
            self._restore_health_btn.setEnabled(True)
            self._servicing_out.setText(
                f"RestoreHealth finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="restore_health",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._restore_health_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)

    def _run_chkdsk_schedule(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        mb = QMessageBox(self._tabs)
        mb.setWindowTitle("Schedule CHKDSK")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText("Runs: chkdsk C: /f /r /x\n\n"
                   "Schedules a full disk check and repair for the NEXT REBOOT. "
                   "Continue?")
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return

        self._chkdsk_btn.setEnabled(False)
        self._servicing_out.setText("Scheduling CHKDSK for next reboot...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_chkdsk_schedule()

        def _done(result):
            self._chkdsk_btn.setEnabled(True)
            self._servicing_out.setText(
                f"CHKDSK scheduled (exit {result.returncode}). Reboot to run.\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="chkdsk_schedule",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._chkdsk_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)
```

- [ ] **Step 5: Remove the 3 functions and their `ALL_ACTIONS` entries from Quick Fix**

Delete `run_sfc`, `run_dism`, `run_chkdsk` from
`src/modules/quick_fix/fix_actions.py`, and their 3 `FixAction` entries
(`"sfc"`, `"dism"`, `"chkdsk"`) from `ALL_ACTIONS`. The "System Repairs"
category keeps its remaining 2 entries (`cleanmgr`, `perf_reset`).

- [ ] **Step 6: Update `test_quick_fix_actions.py`**

Remove `test_dism_asks_for_restorehealth_not_just_a_scan` and
`test_chkdsk_is_scheduled_not_run_now` (they test functions that no
longer exist here), and remove the `(fix_actions.run_sfc, ["sfc",
"/scannow"])` parametrize case from
`test_the_repair_runs_the_command_it_says_it_does`.

- [ ] **Step 7: Add the moved tests to System Health's test files**

```python
# addition to tests/test_system_health_servicing.py
def test_run_sfc_scan_runs_the_right_command(monkeypatch):
    from modules.system_health import servicing
    captured = {}
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr(servicing.subprocess, "run", fake_run)
    servicing.run_sfc_scan()
    assert captured["cmd"] == ["sfc", "/scannow"]


def test_run_restore_health_asks_for_restorehealth_not_just_a_scan(monkeypatch):
    """CheckHealth/ScanHealth only report; RestoreHealth is the one that
    repairs -- ported from the Quick Fix test this replaces."""
    from modules.system_health import servicing
    captured = {}
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr(servicing.subprocess, "run", fake_run)
    servicing.run_restore_health()
    assert "/restorehealth" in " ".join(captured["cmd"]).lower()


def test_run_chkdsk_schedule_answers_the_yn_prompt(monkeypatch):
    """chkdsk on the system volume cannot run live; it must be sent the
    Y answer or it hangs on a prompt nobody can see -- ported from the
    Quick Fix test this replaces."""
    from modules.system_health import servicing
    captured = {}
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr(servicing.subprocess, "run", fake_run)
    servicing.run_chkdsk_schedule()
    assert "chkdsk" in captured["cmd"]
    assert captured["input"], "no answer sent to chkdsk's Y/N prompt"
```

(Read `tests/test_system_health_servicing.py`'s existing tests first to
match its exact `subprocess.run` mocking convention — it may already
mock at a different granularity than shown above; match what's there
rather than introducing a second style.)

Add to `tests/test_system_health_module.py`, following whatever pattern
its existing `_run_component_cleanup`-equivalent test already uses, one
test per new button confirming: the confirm dialog gates the call, the
button disables while running, and `history.append_run` is called with
the right `action=` string.

- [ ] **Step 8: Run all affected tests**

Run:
```
python -m pytest -q tests/test_quick_fix_actions.py tests/test_system_health_servicing.py tests/test_system_health_module.py --timeout=60
```
Expected: PASS.

- [ ] **Step 9: grep sweep**

Run: `grep -rn "run_sfc\b\|run_dism\b\|run_chkdsk\b" src/ tests/`
Expected: only matches inside `system_health/` and its tests — none
left in `quick_fix/`.

- [ ] **Step 10: Commit**

```bash
git add src/modules/quick_fix/fix_actions.py src/modules/system_health/servicing.py src/modules/system_health/system_health_module.py tests/test_quick_fix_actions.py tests/test_system_health_servicing.py tests/test_system_health_module.py
git commit -m "feat(system-health): move SFC/RestoreHealth/CHKDSK from Quick Fix onto the Servicing tab"
```

---

### Task 8: Add a search/filter bar to Quick Fix

**Files:**
- Modify: `src/modules/quick_fix/quick_fix_module.py`
- Test: `tests/test_quick_fix_module.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `QuickFixModule._apply_filter(text: str) -> None`, wired to a
  new `QLineEdit`'s `textChanged` signal.

- [ ] **Step 1: Track category headers alongside cards**

In `create_widget()`, the loop currently builds `hdr` (a `QLabel` per
category) without keeping a reference. Add `self._category_headers:
Dict[str, QLabel] = {}` in `__init__`, and in the loop:
```python
            self._category_headers[cat_name] = hdr
```
right after `content_layout.addWidget(hdr)`.

Also track each card's category on the card itself for the filter to
use: after `card = _FixCard(action)`, add `card._category = cat_name`
(a plain attribute, not a Qt property — matches how this codebase
already stashes ad-hoc attributes on widgets elsewhere, e.g.
`card._auto_timer` in CLAUDE.md's documented pattern for card helpers).

- [ ] **Step 2: Add the search box**

In `create_widget()`, right after the reboot banner and before the
scroll area:
```python
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search repair actions…")
        self._search.textChanged.connect(self._apply_filter)
        outer_layout.addWidget(self._search)
```
(`QLineEdit` already added to imports in Task 4 Step 2.)

- [ ] **Step 3: Implement `_apply_filter`**

```python
    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        visible_by_category: Dict[str, bool] = {name: False for name in self._category_headers}
        for card in self._cards:
            action = card._action
            matches = (not query
                      or query in action.title.lower()
                      or query in action.description.lower())
            card.setVisible(matches)
            if matches:
                visible_by_category[card._category] = True
        for cat_name, hdr in self._category_headers.items():
            hdr.setVisible(visible_by_category[cat_name])
```

- [ ] **Step 4: Reset filter state on rebuild**

At the top of `create_widget()`, alongside `self._cards.clear()`, add
`self._category_headers.clear()` (both must be cleared together since
`create_widget()` can in principle run more than once in tests).

- [ ] **Step 5: Write the tests**

```python
def test_search_hides_non_matching_cards_and_their_category_header(qapp):
    from modules.quick_fix.quick_fix_module import QuickFixModule

    class FakeApp:
        pass
    mod = QuickFixModule()
    mod.on_start(FakeApp())
    mod.create_widget()

    mod._apply_filter("winsock")
    matched = [c for c in mod._cards if c.isVisible()]
    assert matched, "expected at least one card to match 'winsock'"
    assert all("winsock" in c._action.title.lower()
              or "winsock" in c._action.description.lower() for c in matched)

    non_matching_categories = {c._category for c in mod._cards if not c.isVisible()}
    fully_hidden = non_matching_categories - {c._category for c in matched}
    for cat in fully_hidden:
        assert not mod._category_headers[cat].isVisible()


def test_clearing_the_search_shows_everything_again(qapp):
    from modules.quick_fix.quick_fix_module import QuickFixModule

    class FakeApp:
        pass
    mod = QuickFixModule()
    mod.on_start(FakeApp())
    mod.create_widget()

    mod._apply_filter("winsock")
    mod._apply_filter("")
    assert all(c.isVisible() for c in mod._cards)
    assert all(hdr.isVisible() for hdr in mod._category_headers.values())
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest -q tests/test_quick_fix_module.py --timeout=60`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/modules/quick_fix/quick_fix_module.py tests/test_quick_fix_module.py
git commit -m "feat(quick-fix): add a search/filter bar"
```

---

### Task 9: Add a run-history log to Quick Fix

**Files:**
- Create: `src/modules/quick_fix/quick_fix_history.py`
- Modify: `src/modules/quick_fix/quick_fix_module.py`
- Test: `tests/test_quick_fix_history.py`

**Interfaces:**
- Produces: `record(action: str, outcome: str) -> None`,
  `recent(limit: int = 20) -> List[dict]` — same shape as
  `debloat_history.py`.

- [ ] **Step 1: Write `quick_fix_history.py`**

```python
"""A local, append-only log of Quick Fix action runs -- what ran, when,
and whether it succeeded. Mirrors debloat_history.py's exact shape; a
"View History" dialog reads this directly. Added alongside the
module-consolidation merge (docs/superpowers/specs/
2026-09-15-module-consolidation-design.md) since Quick Fix became the
one "runs things" module in this app without any history of its own.
"""
import json
import os
from datetime import datetime
from typing import List


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "quick_fix_history.json")


def record(action: str, outcome: str) -> None:
    path = _history_path()
    entries = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            entries = []
    entries.append({"at": datetime.now().isoformat(timespec="seconds"),
                    "action": action, "outcome": outcome})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries[-200:], f, indent=2)


def recent(limit: int = 20) -> List[dict]:
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return list(reversed(entries))[:limit]
```

- [ ] **Step 2: Wire `record()` into `_FixCard`'s outcome handlers**

`_FixCard` currently has no reference to the action's own title at the
point `_on_done`/`_on_error`/`cancel` run — it does, via
`self._action.title`. Update all three:

```python
    def _on_done(self):
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._status.setText("")
        from modules.quick_fix import quick_fix_history
        quick_fix_history.record(self._action.title, "ok")

    def _on_error(self, error_str: str):
        self._running = False
        self._worker = None
        self._run_btn.setEnabled(True)
        self._status.setText("")
        self._output.appendPlainText(f"ERROR: {error_str}")
        from modules.quick_fix import quick_fix_history
        quick_fix_history.record(self._action.title, "error")

    def cancel(self) -> None:
        """Cancel the running worker if any."""
        if self._worker is not None and self._running:
            self._worker.cancel()
            self._running = False
            self._worker = None
            self._run_btn.setEnabled(True)
            self._output.appendPlainText("Cancelled.")
            from modules.quick_fix import quick_fix_history
            quick_fix_history.record(self._action.title, "cancelled")
```

- [ ] **Step 3: Add a "View History" button and dialog**

In `create_widget()`, add a toolbar row above the reboot banner (or
alongside the search box from Task 8 — same row is fine):

```python
        history_row = QHBoxLayout()
        history_row.addStretch()
        history_btn = QPushButton("View History")
        history_btn.clicked.connect(self._show_history)
        history_row.addWidget(history_btn)
        outer_layout.addLayout(history_row)
```

Add the dialog class (module-level, alongside `_FixCard`) and the
handler method:

```python
class QuickFixHistoryDialog(QDialog):
    """Read-only browser over the local Quick Fix run history."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Fix History")
        self.resize(520, 420)
        root = QVBoxLayout(self)

        self._list = QListWidget()
        from modules.quick_fix import quick_fix_history
        entries = quick_fix_history.recent(limit=20)
        if entries:
            for entry in entries:
                self._list.addItem(
                    f"{entry['at']} — {entry['action']}: {entry['outcome']}")
        else:
            self._list.addItem("No actions run yet.")
        root.addWidget(self._list)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)
```

```python
    def _show_history(self) -> None:
        QuickFixHistoryDialog(self._widget).exec()
```

Add `QDialog`, `QListWidget` to the `PyQt6.QtWidgets` import line.

- [ ] **Step 4: Write the tests**

```python
# tests/test_quick_fix_history.py
"""Mirrors tests/test_debloat_history.py's structure for the identically
-shaped quick_fix_history module."""
import json

from modules.quick_fix import quick_fix_history as qfh


def test_record_then_recent_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "h.json"))
    qfh.record("Flush DNS", "ok")
    qfh.record("Reset Winsock", "error")
    entries = qfh.recent(limit=10)
    assert len(entries) == 2
    assert entries[0]["action"] == "Reset Winsock"  # most recent first
    assert entries[0]["outcome"] == "error"
    assert entries[1]["action"] == "Flush DNS"


def test_recent_on_no_history_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(qfh, "_history_path", lambda: str(tmp_path / "nope.json"))
    assert qfh.recent() == []


def test_record_caps_at_200_entries(tmp_path, monkeypatch):
    path = tmp_path / "h.json"
    monkeypatch.setattr(qfh, "_history_path", lambda: str(path))
    for i in range(210):
        qfh.record(f"Action {i}", "ok")
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    assert len(entries) == 200
    assert entries[-1]["action"] == "Action 209"
```

Add to `tests/test_quick_fix_module.py`: a test that `_on_done`,
`_on_error`, and `cancel` each call `quick_fix_history.record` with the
right outcome string (monkeypatch `quick_fix_history.record` and assert
the call).

- [ ] **Step 5: Run the tests**

Run:
```
python -m pytest -q tests/test_quick_fix_history.py tests/test_quick_fix_module.py --timeout=60
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/modules/quick_fix/quick_fix_history.py src/modules/quick_fix/quick_fix_module.py tests/test_quick_fix_history.py tests/test_quick_fix_module.py
git commit -m "feat(quick-fix): add a run-history log and View History dialog"
```

---

### Task 10: Add the Performance Tuner discoverability nudge to Tweaks

**Files:**
- Modify: `src/modules/tweaks/tweaks_module.py`
- Test: `tests/test_tweaks_module.py` (or wherever this module's existing
  UI tests live — check first with
  `grep -rl "_build_preset_toolbar\|_preset_combo" tests/`)

**Interfaces:**
- Consumes: `self._preset_combo` (already exists).
- Produces: `self._perf_tuner_nudge_lbl`, visibility bound to
  `self._preset_combo`'s current text.

- [ ] **Step 1: Add the label in `_build_preset_toolbar`**

After the existing `return bar` in `_build_preset_toolbar` would be too
late (the label needs to be added to `layout` before returning). Insert
right after the `import_btn` block and before `layout.addStretch()`:

```python
        self._perf_tuner_nudge_lbl = QLabel(
            "Looking for what used to be Performance Tuner? Try the Performance preset above.")
        self._perf_tuner_nudge_lbl.setObjectName("muted")
        self._perf_tuner_nudge_lbl.setStyleSheet("font-size: 11px; font-style: italic;")
        layout.addWidget(self._perf_tuner_nudge_lbl)
        self._preset_combo.currentTextChanged.connect(self._update_perf_tuner_nudge)
        self._update_perf_tuner_nudge(self._preset_combo.currentText())
```

- [ ] **Step 2: Add the visibility handler**

```python
    def _update_perf_tuner_nudge(self, current_preset_name: str) -> None:
        self._perf_tuner_nudge_lbl.setVisible(current_preset_name != "Performance")
```

- [ ] **Step 3: Write the test**

First check where Tweaks module UI tests already live:
```bash
grep -rl "TweaksModule\b" tests/ | head -5
```
Add to that file (creating `tests/test_tweaks_perf_tuner_nudge.py` only
if no suitable existing file is found):

```python
def test_the_nudge_is_hidden_when_the_performance_preset_is_selected(qapp):
    mod = _tweaks_module()  # use whatever module-construction helper this test file already has
    mod._preset_combo.setCurrentText("Performance")
    assert not mod._perf_tuner_nudge_lbl.isVisible()


def test_the_nudge_is_visible_for_any_other_preset(qapp):
    mod = _tweaks_module()
    mod._preset_combo.setCurrentText("Balanced")
    assert mod._perf_tuner_nudge_lbl.isVisible()
```

(Match whatever fixture/helper the surrounding test file already uses to
construct a `TweaksModule` with a real widget — do not invent a second
one; read 20 lines of context first.)

- [ ] **Step 4: Run the test**

Run whatever command matches the file this was added to, e.g.:
```
python -m pytest -q tests/test_tweaks_module.py -k nudge --timeout=60
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/modules/tweaks/tweaks_module.py tests/
git commit -m "feat(tweaks): add a discoverability nudge for the retired Performance Tuner"
```

---

### Task 11: Final whole-branch verification

**Files:** none (verification only)

- [ ] **Step 1: Full grep sweep for every deleted symbol**

```bash
grep -rln "performance_tuner\|PerfTunerModule\|perf_checks\|PERF_CHECKS\|_build_one_click_panel\|_action_buttons\|_run_action_command" --include="*.py" src tests tools
```
Expected: no output.

- [ ] **Step 2: Full test suite**

```bash
rm -rf .pytest-tmp
python -m pytest -q --timeout=300
```
Expected: exit 0, no FAILED/ERROR (a stale `.pytest-tmp` cascade or the
documented ~1-in-3 access-violation flake are pre-existing, not
regressions — re-run once if either appears, per
`cleanup-suite-flaky-crash` memory).

- [ ] **Step 3: Manual sidebar-count sanity check**

```bash
python -c "import sys; sys.path.insert(0, 'src'); from app import App; from main import register_all_modules; app = App(); register_all_modules(app); print(len(app.module_registry.modules))"
```
Expected: `32`.

- [ ] **Step 4: Syntax check the frozen-build entry point**

```bash
python -c "import sys; sys.path.insert(0, 'src'); import main"
```
Expected: no errors.

- [ ] **Step 5: Update CLAUDE.md's `QuickFixModule` section**

Its current text (`_FixCard`/`_workers`/`_worker` tracking) stays
accurate and needs no correction, but add one sentence noting the
module now also absorbed Quick Cleanup's one-click panel (7 actions
under a "Cleanup" category), has a search box, and logs a run history —
so a future session reading this section doesn't need to rediscover
that from git history:

```
Uses `_FixCard` widget subclasses for each fix. Cards run in background Workers. `QuickFixModule._workers` (plural, on the module) tracks all workers. Individual cards track `self._worker` (singular) for cancellation. `_FixCard` does NOT have a `_workers` list. `FixAction` also carries optional `confirm_text` (shows an Ok/Cancel dialog before running), `precondition` (a callable that can block the run with a status message instead), and `long_running` (routes to `core.long_op_pool` instead of the shared global pool) — added when this module absorbed Quick Cleanup's one-click panel and gained a search box and a `quick_fix_history.py` run log (2026-09-15 module consolidation).
```

- [ ] **Step 6: Update memory**

Write a session memory entry (following this repo's established pattern
— see `cleanup-quick-cleanup-merge`, `system-health-module` in
`C:\Users\iorda\.claude\projects\c--Users-iorda-Windows-client-tool\memory\`)
summarizing: what merged where, the 7-action overlap table, the
`_FixCard` confirm-mechanism gap found and fixed, and the sidebar count
change (33 → 32).
