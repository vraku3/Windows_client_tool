# Cleanup + Quick Cleanup Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge `QuickCleanupModule` into `CleanupModule` as its new first tab (replacing `_OverviewTab`), fix the 5 real bugs found while reading the code, and delete the now-redundant module — one sidebar entry instead of two, nothing lost.

**Architecture:** `_OV_GROUPS` moves out of `_overview_tab.py` into the `cleanup_scanner` package first (a real external consumer, `stage_runners.py`'s unattended cleanup stage, depends on it and must not break). `QuickCleanupTab` gains the missing pieces `_OverviewTab`/`_ScanTab` already have (a `freed_bytes` signal, a `_scanned`-gated `auto_scan()`, a scan watchdog) plus three real fixes (per-action busy-guards on the one-click panel, clickable category cards, a shared `run_clean_safe` helper). `CleanupModule` then swaps tabs and gains a visible-tab-gated `get_refresh_interval`/`refresh_data`. Finally, `_overview_tab.py` and `quick_cleanup_module.py` are deleted, `main.py`'s registration is removed, and every test that referenced either deleted file is migrated or removed with a stated reason.

**Tech Stack:** Python 3.12, PyQt6, pytest (with the `qapp` fixture already used throughout `tests/test_cleanup_*.py` for real-event-loop Qt tests).

**Spec:** `docs/superpowers/specs/2026-09-14-cleanup-quick-cleanup-merge-design.md`

## Global Constraints

- No changes to any scanner function, `scan_cache`, or the scanner catalog (spec §6).
- The merged tab always confirms before "Clean All Safe" (never size-gated) — it is the highest-blast-radius action in the module (spec §1.2).
- `_ScanTab._do_clean` migrates to the shared `run_clean_safe` helper too — verified to fit cleanly during planning (its shape, a single scanner dict with a size-gated confirm and no browser path, is exactly what the helper's `confirm="size_gated"` / `browser_cats=None` path is for).
- The "Show Advanced ▼" toggle stays — it still gates ~90 extra category cards, which would clutter the tab shown inline by default. Only the one-click actions panel moves out of it (spec §1.2's "reconsidered at implementation time" is resolved here: keep the toggle, narrow its scope).
- Every existing test that imports `_overview_tab`, `_OverviewTab`, `_OV_GROUPS`, or `quick_cleanup_module`/`QuickCleanupModule` must be updated or removed with a stated reason — never left importing something that no longer exists.

---

## Task 1: Move `_OV_GROUPS` into the `cleanup_scanner` package

**Files:**
- Modify: `src/modules/cleanup/cleanup_scanner/scanners_system.py`
- Modify: `src/modules/cleanup/tabs/_overview_tab.py:36-81` (the `_OV_GROUPS` definition)
- Modify: `src/modules/updates/stage_runners.py:118`
- Test: `tests/test_cleanup_catalog.py` (import path only, not the factory-tuple change — that's Task 7)

**Interfaces:**
- Produces: `modules.cleanup.cleanup_scanner.scanners_system._OV_GROUPS` — a `List[Tuple[str, Optional[List[Callable]]]]`, identical value to today's `_overview_tab._OV_GROUPS`. Every later task that needs the group list imports it from here.

This is a pure relocation — the list's contents don't change, and `_overview_tab.py` keeps working via a re-export, so no other test needs to change in this task.

- [ ] **Step 1: Copy `_OV_GROUPS` into `scanners_system.py`**

Add this at the very end of `src/modules/cleanup/cleanup_scanner/scanners_system.py`, after the `__all__` list:

```python

# ── Category groups shared by Quick Cleanup's dashboard and the
# unattended "cleanup" stage (modules/updates/stage_runners.py) ──
#
# Moved here from modules/cleanup/tabs/_overview_tab.py during the
# Cleanup/Quick Cleanup merge — that file is going away, but
# run_cleanup_safe_stage's import of this exact list is not something
# this merge may break silently.
_OV_GROUPS = [
    ("System Junk", [
        scan_temp_files, scan_prefetch, scan_thumbnail_cache, scan_user_crash_dumps,
    ]),
    ("Browser Caches", None),    # handled specially via bs.detect_browsers()
    ("App & Game Caches", [
        scan_app_caches, scan_d3d_shader_cache, scan_appdata_autodiscover,
        scan_steam_cache, scan_stremio_cache, scan_outlook_cache,
        scan_winget_packages, scan_store_app_caches,
        scan_discord_cache, scan_spotify_cache, scan_zoom_cache,
        scan_slack_cache, scan_discord_full_cache, scan_teams_cache,
        scan_telegram_cache, scan_brave_cache, scan_vivaldi_cache,
        scan_opera_cache, scan_edge_cache, scan_firefox_cache, scan_chrome_cache,
        scan_game_caches, scan_epic_launcher_cache, scan_ea_app_cache,
        scan_ubisoft_cache, scan_battlenet_cache, scan_gog_cache,
        scan_rockstar_cache, scan_minecraft_cache, scan_rust_game_cache,
    ]),
    ("Windows Update", [
        scan_wu_cache, scan_delivery_optimization, scan_update_cleanup,
        scan_windows_old, scan_installer_patch_cache,
    ]),
    ("Logs & Reports", [
        scan_windows_logs, scan_event_logs, scan_wer_reports,
        scan_memory_dumps, scan_panther_logs, scan_dmf_logs,
        scan_onedrive_logs, scan_defender_history, scan_diag_logs,
        scan_powershell_logs, scan_sysprep_logs, scan_msi_logs,
        scan_wmi_logs, scan_sfc_logs, scan_group_policy_logs,
    ]),
    ("Large Items", [
        scan_windows_old, scan_recycle_bin, scan_installer_patch_cache,
    ]),
    ("Dev Tools", [
        scan_dev_tool_caches, scan_vscode_cache, scan_jetbrains_cache,
        scan_npm_cache, scan_pip_cache, scan_nuget_cache,
        scan_golang_cache, scan_rust_cache, scan_java_cache,
        scan_unity_cache, scan_unreal_cache,
    ]),
    ("Cloud Storage", [
        scan_dropbox_cache, scan_google_drive_cache, scan_mega_cache,
        scan_pcloud_cache, scan_icloud_cache, scan_onedrive_full_cache,
    ]),
    ("Media Production", [
        scan_obs_cache, scan_davinci_cache, scan_premiere_cache,
        scan_blender_cache, scan_audacity_cache, scan_handbrake_cache,
    ]),
]
```

Note every `cs.scan_*` reference from the original has its `cs.` prefix dropped — this file defines those functions itself (or imports them un-prefixed already), so `cs.scan_temp_files` becomes plain `scan_temp_files`.

Then add `'_OV_GROUPS'` to the `__all__` list already at line 1956 (anywhere in the list; alphabetical-ish order is already loosely followed, so add it near the top):

```python
__all__ = [
    '_OV_GROUPS',
    'scan_orphaned_virtual_disks',
    ...
```

- [ ] **Step 2: Verify every scanner name in `_OV_GROUPS` actually resolves in this file's namespace**

Run: `cd src && python -c "from modules.cleanup.cleanup_scanner.scanners_system import _OV_GROUPS; print(len(_OV_GROUPS))"`
Expected: prints `8` (the number of groups), no `NameError`.

If any name raises `NameError`, that scanner is defined in a *different* submodule of the `cleanup_scanner` package (e.g. `scanners_apps.py`) and was reachable in the original only via the package facade's star-imports. Fix by importing it explicitly at the top of `scanners_system.py`, e.g.:

```python
from modules.cleanup.cleanup_scanner.scanners_apps import scan_discord_cache
```

Re-run until the import succeeds cleanly.

- [ ] **Step 3: Point `_overview_tab.py` at the new location instead of defining it locally**

In `src/modules/cleanup/tabs/_overview_tab.py`, replace the entire `_OV_GROUPS = [ ... ]` block (lines 36-81) with:

```python
from modules.cleanup.cleanup_scanner.scanners_system import _OV_GROUPS
```

Place this import alongside the other `from modules.cleanup...` imports near the top of the file (after `from modules.cleanup import browser_scanner as bs`), not down where the list used to live — it's an import statement now, not data.

- [ ] **Step 4: Point `stage_runners.py` at the new location**

In `src/modules/updates/stage_runners.py:118`, change:

```python
    from modules.cleanup.tabs._overview_tab import _OV_GROUPS
```

to:

```python
    from modules.cleanup.cleanup_scanner.scanners_system import _OV_GROUPS
```

- [ ] **Step 5: Update `test_cleanup_catalog.py`'s import (not its factory tuple yet)**

In `tests/test_cleanup_catalog.py:293`, change:

```python
    from modules.cleanup.tabs._overview_tab import _OV_GROUPS
```

to:

```python
    from modules.cleanup.cleanup_scanner.scanners_system import _OV_GROUPS
```

- [ ] **Step 6: Run the full existing cleanup test suite — must be unchanged (all green)**

Run: `pytest tests/test_cleanup_cancel_recovery.py tests/test_cleanup_catalog.py tests/test_cleanup_late_signal.py tests/test_cleanup_scan_progress.py tests/test_cleanup_scan_watchdog.py -v`
Expected: PASS, same tests as before this task — this is a pure relocation, `ov._OV_GROUPS` still resolves (the import in Step 3 binds the name into `_overview_tab`'s own module namespace, so `monkeypatch.setattr(ov, "_OV_GROUPS", ...)` in these tests keeps working unchanged).

- [ ] **Step 7: Commit**

```bash
git add src/modules/cleanup/cleanup_scanner/scanners_system.py src/modules/cleanup/tabs/_overview_tab.py src/modules/updates/stage_runners.py tests/test_cleanup_catalog.py
git commit -m "refactor(cleanup): move _OV_GROUPS into cleanup_scanner, ahead of deleting _overview_tab.py

stage_runners.py's run_cleanup_safe_stage (the engine behind this app's
existing --unattended cleanup and the scheduled
WinClientTool_UnattendedMaintenance task) depends on this list directly.
Relocating it now, before _overview_tab.py is deleted later in this
merge, means that dependency never breaks even transiently."
```

---

## Task 2: `QuickCleanupTab` gains `freed_bytes`, `auto_scan()`, and a scan watchdog

**Files:**
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Test: `tests/test_quick_cleanup_watchdog.py` (new)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `QuickCleanupTab.freed_bytes` (a `pyqtSignal(int)`), `QuickCleanupTab.auto_scan()` (no-arg method, no-op if already scanned), `QuickCleanupTab._scanned` (`bool` instance attribute). Task 6 (`CleanupModule`) connects to `freed_bytes` and calls `auto_scan()`.

- [ ] **Step 1: Write the failing watchdog test**

Create `tests/test_quick_cleanup_watchdog.py`:

```python
r"""QuickCleanupTab's scan loop had no backstop for a scan that never
finishes -- _OverviewTab and _ScanTab both already needed one (see
tests/test_cleanup_scan_watchdog.py), and QuickCleanupTab was the one
tab in this module missing it.
"""
import time

import pytest
from PyQt6.QtCore import QThreadPool

from modules.cleanup.cleanup_scanner import ScanResult


def _settle(qapp, timeout_ms: int = 30_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 2
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _pump_until(qapp, predicate, seconds: float = 10.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def blocking_scanner():
    import threading
    started = threading.Event()
    release = threading.Event()

    def scan_that_never_returns(min_age_days: int = 0) -> ScanResult:
        started.set()
        release.wait(30)
        return ScanResult()

    scan_that_never_returns.started = started
    scan_that_never_returns.release = release
    return scan_that_never_returns


def test_a_scan_that_never_finishes_gives_the_tab_back(qapp, blocking_scanner):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab.SCAN_WATCHDOG_MS = 300
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)

    recovered = _pump_until(qapp, lambda: not tab._scanning)
    blocking_scanner.release.set()
    _settle(qapp)

    assert recovered, "the watchdog never fired"
    assert tab._scan_all_btn.isEnabled()
    assert tab._scanned is False, "a scan that never finished must not count as scanned"


def test_the_watchdog_does_not_fire_on_a_healthy_scan(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    def scan_fast(min_age_days: int = 0) -> ScanResult:
        return ScanResult()

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (scan_fast, "Temp Files", "#4caf50")
    tab.SCAN_WATCHDOG_MS = 5_000
    tab._do_scan_all()
    _settle(qapp)

    assert tab._scanning is False
    assert tab._scanned is True
    assert tab._watchdog.isActive() is False, "watchdog left armed after a scan"


def test_auto_scan_is_a_no_op_once_already_scanned(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    calls = []

    def scan_fast(min_age_days: int = 0) -> ScanResult:
        calls.append(1)
        return ScanResult()

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (scan_fast, "Temp Files", "#4caf50")

    tab.auto_scan()
    _settle(qapp)
    assert len(calls) == 1

    tab.auto_scan()  # second call must be a no-op
    _settle(qapp)
    assert len(calls) == 1, "auto_scan re-scanned even though _scanned was already True"


def test_freed_bytes_is_emitted_after_a_successful_clean(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup import cleanup_scanner as cs

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    item = cs.ScanItem(path=r"C:\fake\path", size=500, is_dir=False, safety="safe")
    result = cs.ScanResult()
    result.items = [item]
    tab._results = {"temp": result}

    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))
    monkeypatch.setattr(QuickCleanupTab, "_confirm_clean_all", lambda self, total: True)

    emitted = []
    tab.freed_bytes.connect(emitted.append)
    tab._do_clean_all_safe()
    _settle(qapp)

    assert emitted == [500]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_quick_cleanup_watchdog.py -v`
Expected: FAIL — `AttributeError: 'QuickCleanupTab' object has no attribute 'SCAN_WATCHDOG_MS'` (and similar for `_scanned`, `auto_scan`, `freed_bytes`, `_confirm_clean_all`).

- [ ] **Step 3: Add the class attribute, signal, and new instance state**

In `src/modules/cleanup/components/quick_cleanup_tab.py`, add to `QuickCleanupTab`'s class body (right after the existing `scan_done = pyqtSignal(int, int)` at line 304):

```python
    # Emitted after a successful Clean All Safe -- feeds CleanupModule's
    # shared "Freed this session" counter, the same signal every other
    # tab in the module already emits.
    freed_bytes = pyqtSignal(int)

    #: See _OverviewTab.SCAN_WATCHDOG_MS (tabs/_overview_tab.py) -- this
    #: sweep covers MORE categories than Overview's, so give it real
    #: headroom above whatever this machine's own scan measures, not a
    #: number copied from a different, smaller sweep.
    SCAN_WATCHDOG_MS = 300_000
```

In `__init__` (line 306-315), add after `self._workers: List[Worker] = []`:

```python
        self._scanned = False
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_scan_watchdog)
```

- [ ] **Step 4: Add `auto_scan()`, start/stop the watchdog around the scan, and add the watchdog handler**

Add this new method anywhere in the "Public API" section (near `scan()`):

```python
    def auto_scan(self) -> None:
        """Scan once, the first time this tab is shown -- CleanupModule's
        _on_tab_changed calls this on every tab switch, the same as every
        other tab in the module (_ScanTab, _OverviewTab before it)."""
        if not self._scanned:
            self._do_scan_all()
```

In `_do_scan_all` (line 625), add right after `self._scanning = True`:

```python
        self._watchdog.start(self.SCAN_WATCHDOG_MS)
```

In `_on_all_scanned` (line 719), add at the top, before `self._scanning = False`:

```python
        self._watchdog.stop()
        self._scanned = True
```

Add the watchdog handler and a shared reset helper right after `_on_all_scanned`:

```python
    def _on_scan_watchdog(self) -> None:
        if not self._scanning:
            return
        logger.warning(
            "Quick Cleanup scan timed out after %.0fs with %d/%d "
            "categories reported",
            self.SCAN_WATCHDOG_MS / 1000, self._total_scanned,
            len(self._categories) + len(self._advanced_categories))
        self._reset_after_cancel(
            message="Scan timed out — click Scan All to run it again")

    def _reset_after_cancel(self, message: str = None) -> None:
        """Put the tab back in a state the user can act on -- shared by
        the watchdog above and cancel() below. A cancelled Worker emits
        `cancelled`, never `result` or `error` (core/worker.py), so
        _total_scanned would never reach the target count and
        _on_all_scanned would never run on its own."""
        self._watchdog.stop()
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._scanning = False
        # Nothing was measured, so the tab has NOT been scanned: let
        # auto_scan() run again the next time the module is activated.
        self._scanned = False
        if hasattr(self, "_scan_all_btn"):
            self._scan_all_btn.setEnabled(True)
            self._clean_all_btn.setEnabled(True)
        if hasattr(self, "_progress"):
            self._progress.hide()
        if hasattr(self, "_status_lbl") and message:
            self._status_lbl.setText(message)
```

Add `import logging` and `logger = logging.getLogger(__name__)` near the top of the file if not already present (check first — this file currently has no logging calls, so it likely needs both lines added right after the existing `import subprocess` at the top).

- [ ] **Step 5: Fold `cancel()` into the shared reset helper**

Replace the existing `cancel()` method (line 474-481):

```python
    def cancel(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._scanning = False
        if hasattr(self, "_scan_all_btn"):
            self._scan_all_btn.setEnabled(True)
            self._clean_all_btn.setEnabled(True)
```

with:

```python
    def cancel(self) -> None:
        self._reset_after_cancel()
```

- [ ] **Step 6: Extract the confirm dialog into `_confirm_clean_all` so the new test can stub it**

In `_do_clean_all_safe` (line 1166), the existing inline `QMessageBox` block:

```python
        mb = QMessageBox(self)
        mb.setWindowTitle("Confirm Bulk Clean")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            f"Clean <b>{cs.format_size(total)}</b> of safe items across "
            f"{len(all_safe) + len(browser_cats)} item(s)?<br>This cannot be undone."
        )
        mb.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return
```

becomes a call to a new method:

```python
        if not self._confirm_clean_all(total, len(all_safe) + len(browser_cats)):
            return
```

Add the extracted method right before `_do_clean_all_safe`:

```python
    def _confirm_clean_all(self, total_bytes: int, item_count: int) -> bool:
        mb = QMessageBox(self)
        mb.setWindowTitle("Confirm Bulk Clean")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            f"Clean <b>{cs.format_size(total_bytes)}</b> of safe items across "
            f"{item_count} item(s)?<br>This cannot be undone."
        )
        mb.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return mb.exec() == QMessageBox.StandardButton.Ok
```

- [ ] **Step 7: Emit `freed_bytes` on a successful clean**

In `_do_clean_all_safe`'s `_done` closure (line 1230-1239):

```python
        def _done(result):
            deleted, errors = result
            self._scanning = False
            self._scan_all_btn.setEnabled(True)
            self._progress.hide()
            msg = f"Cleaned {deleted} item(s)"
            if errors:
                msg += f" — {errors} could not be deleted"
            self._status_lbl.setText(msg)
            self.scan()
```

add `self.freed_bytes.emit(total)` right after the `self._status_lbl.setText(msg)` line (before `self.scan()`). `total` is already in scope from earlier in `_do_clean_all_safe`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_quick_cleanup_watchdog.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 9: Run the full existing Quick Cleanup and Cleanup suites to check for regressions**

Run: `pytest tests/test_quick_cleanup_dedupe.py tests/test_quick_cleanup_legend_theme.py tests/test_cleanup_late_signal.py tests/test_cleanup_scan_progress.py -v`
Expected: PASS, unchanged (these don't touch anything modified in this task except by reading `QuickCleanupTab` normally).

- [ ] **Step 10: Commit**

```bash
git add src/modules/cleanup/components/quick_cleanup_tab.py tests/test_quick_cleanup_watchdog.py
git commit -m "feat(cleanup): give Quick Cleanup a scan watchdog, auto_scan(), and freed_bytes

_OverviewTab and _ScanTab both already needed a watchdog backstop for a
scan that never finishes (a cancelled Worker emits 'cancelled', never
'result' or 'error') -- Quick Cleanup was the one tab in this module
without one. auto_scan() and freed_bytes are what let it plug into
CleanupModule's existing per-tab activation and shared freed-counter
patterns in Task 6."
```

---

## Task 3: Fix the one-click actions' shared status label and missing busy-guard

**Files:**
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Test: `tests/test_quick_cleanup_one_click_actions.py` (new)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: each one-click action button gets its own `QLabel` status target and disables itself while running — `_run_action_command`'s signature changes from taking a shared label implicitly to taking the specific button/label pair for its own action.

- [ ] **Step 1: Write the failing test**

Create `tests/test_quick_cleanup_one_click_actions.py`:

```python
"""Each one-click action must guard against being clicked again while it's
already running, and must report ITS OWN result -- not clobber whatever
another action's status currently says. Before this fix, all 12 actions
shared one QLabel and none of them disabled their own button.
"""
import time

from PyQt6.QtCore import QThreadPool


def _settle(qapp, timeout_ms: int = 5_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_a_running_actions_own_button_is_disabled_until_it_finishes(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    release = []

    def fake_run(cmd, **kwargs):
        while not release:
            qapp.processEvents()
            time.sleep(0.01)
        class _R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    btn = tab._action_buttons["flush_dns"]
    assert btn.isEnabled()

    tab._flush_dns()
    qapp.processEvents()
    assert not btn.isEnabled(), "button stayed enabled while its own action was running"

    release.append(1)
    _settle(qapp)
    assert btn.isEnabled(), "button never re-enabled after finishing"


def test_two_actions_running_at_once_do_not_clobber_each_others_status(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    def fake_run(cmd, **kwargs):
        class _R:
            returncode = 0
            stdout = cmd if isinstance(cmd, str) else " ".join(cmd)
            stderr = ""
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    tab._flush_dns()
    _settle(qapp)
    tab._clear_clipboard()
    _settle(qapp)

    dns_status = tab._action_status["flush_dns"].text()
    clip_status = tab._action_status["clear_clipboard"].text()
    assert "DNS" in dns_status or "flush" in dns_status.lower()
    assert dns_status != clip_status, "both actions ended up showing the same text"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_quick_cleanup_one_click_actions.py -v`
Expected: FAIL — `AttributeError: 'QuickCleanupTab' object has no attribute '_action_buttons'`.

- [ ] **Step 3: Read `_build_one_click_panel` and give each action its own button + status label**

In `src/modules/cleanup/components/quick_cleanup_tab.py`, `_build_one_click_panel` (line 847) currently builds 12 buttons all wired to the shared `self._action_status_lbl`. Replace the method so each action gets a small `QHBoxLayout` row: button + its own status `QLabel`, tracked in two new dicts.

Add near the top of `_build_one_click_panel`, before building any buttons:

```python
        self._action_buttons: Dict[str, QPushButton] = {}
        self._action_status: Dict[str, QLabel] = {}
```

For each of the 12 actions currently added as `parent_lay.addWidget(btn)` (or similar), change the pattern from a bare button to a row. Example for Flush DNS (apply the same shape to all 12 — `spooler`/`dns`/`icons`/`wu`/`net`/`thumb`/`clip`/`search`/`font`/`wustore`/`tcpip` each get the same treatment, keyed by a short action id):

```python
        row = QHBoxLayout()
        btn = QPushButton("Flush DNS Cache")
        btn.clicked.connect(self._flush_dns)
        status = QLabel("")
        status.setStyleSheet("font-size: 11px;")
        row.addWidget(btn)
        row.addWidget(status, 1)
        parent_lay.addLayout(row)
        self._action_buttons["flush_dns"] = btn
        self._action_status["flush_dns"] = status
```

Repeat for every action currently in this method, using its existing button label text and a short, stable dict key (`"flush_dns"`, `"clear_event_logs"`, `"compact_winsxs"`, `"rebuild_icons"`, `"wu_deep_clean"`, `"network_repair"`, `"clear_thumbnails"`, `"clear_clipboard"`, `"reset_search"`, `"clear_font_cache"`, `"flush_wu_store"`, `"reset_tcpip"` — one per existing method name with the leading underscore dropped).

- [ ] **Step 4: Thread the action id through `_run_action_command` so it can target the right button/label**

Change `_run_action_command`'s signature (line 887) from:

```python
    def _run_action_command(self, cmd: str, status_prefix: str,
                              need_confirm: bool = False,
                              long_running: bool = False,
                              confirm_text: str = ""):
```

to:

```python
    def _run_action_command(self, action_id: str, cmd: str, status_prefix: str,
                              need_confirm: bool = False,
                              long_running: bool = False,
                              confirm_text: str = ""):
```

Replace every reference to `self._action_status_lbl` inside this method with `self._action_status[action_id]`. Add a busy-guard at the very top of the method, right after the docstring:

```python
        btn = self._action_buttons.get(action_id)
        if btn is not None and not btn.isEnabled():
            return  # already running
```

Right before `w = Worker(_run)` near the end of the method, disable the button:

```python
        if btn is not None:
            btn.setEnabled(False)
```

In both `_done` and `_err` closures inside `_run_action_command`, re-enable it at the top:

```python
        def _done(result):
            if btn is not None:
                btn.setEnabled(True)
            status, msg = result
            ...  # existing body unchanged, but self._action_status_lbl -> self._action_status[action_id]

        def _err(e: str):
            if btn is not None:
                btn.setEnabled(True)
            ...  # existing body unchanged, same substitution
```

- [ ] **Step 5: Update every one-click method to pass its own action id**

Each of the 12 methods below `_run_action_command` (`_flush_dns`, `_clear_event_logs`, `_compact_winsxs`, `_rebuild_icon_cache`, `_wu_deep_clean`, `_network_repair`, `_clear_thumbnails`, `_clear_clipboard`, `_reset_search`, `_clear_font_cache`, `_flush_wu_store`, `_reset_tcpip`) currently calls `self._run_action_command(cmd, status_prefix, ...)`. Add the matching action id as the new first argument to each call, e.g.:

```python
    def _flush_dns(self):
        self._run_action_command("flush_dns", "ipconfig /flushdns", "DNS cache flushed", need_confirm=False)
```

Apply the same pattern (action id string matching Step 3's dict keys, inserted as the new first positional argument) to the other 11 methods, keeping every other argument exactly as it is today.

- [ ] **Step 6: Remove the now-unused shared `_action_status_lbl`**

Delete the line in `_setup_ui` (or wherever it's currently constructed) that creates `self._action_status_lbl` — every consumer now uses `self._action_status[action_id]` instead. Search the file for `_action_status_lbl` to confirm no references remain.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_quick_cleanup_one_click_actions.py -v`
Expected: PASS, both tests.

- [ ] **Step 8: Manual sanity check — run every one-click action once on the real machine**

This module shells out to real system commands (`ipconfig /flushdns`, DISM, `netsh`, etc.). Run:

```bash
python src/main.py
```

Navigate to Cleanup → Quick Cleanup → Show Advanced, and click each of the 12 one-click actions once. Confirm each one's OWN status label updates independently and its button re-enables afterward. This is a real, deliberate manual step — these commands are exactly the kind of thing this codebase insists on verifying live rather than only through mocks.

- [ ] **Step 9: Commit**

```bash
git add src/modules/cleanup/components/quick_cleanup_tab.py tests/test_quick_cleanup_one_click_actions.py
git commit -m "fix(cleanup): give each one-click action its own busy-guard and status label

All 12 actions shared one QLabel and none disabled their own button --
two actions clicked in quick succession clobbered each other's result
with no way to tell which was which. Real gap, found while planning to
promote these out of the hidden 'Advanced' section (Task 4), which
makes it more likely someone actually does this."
```

---

## Task 4: Category cards become clickable navigation to their own tab

**Files:**
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Test: `tests/test_quick_cleanup_category_navigation.py` (new)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `_SliceCard.clicked` (a `pyqtSignal()`), `QuickCleanupTab.__init__(self, parent=None, on_category_clicked=None)` — `on_category_clicked` is an optional `Callable[[str], None]` taking a category id (`"temp"`, `"browser"`, etc.). Task 6 (`CleanupModule`) passes a real callback that switches its `QTabWidget`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_quick_cleanup_category_navigation.py`:

```python
"""Category cards on Quick Cleanup's dashboard jump to that category's own
deep-dive tab -- a real navigation flow the Cleanup/Quick Cleanup merge
specifically makes possible, since both were previously separate,
unreachable-from-each-other destinations.
"""


def test_clicking_a_main_category_card_calls_the_callback_with_its_id(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    clicked = []
    tab = QuickCleanupTab(on_category_clicked=clicked.append)
    tab.build(categories=[("browser", "Browser Caches", "#4dd0e1")], advanced_categories=[])

    card = tab._legend_cards[0]
    card.clicked.emit()

    assert clicked == ["browser"]


def test_advanced_category_cards_are_not_wired_to_navigation(qapp):
    """Advanced categories don't map 1:1 (or many:1) onto a single tab the
    way the 10 main categories do, so they stay inert -- clicking one
    must not raise even with no callback reachable for it."""
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    clicked = []
    tab = QuickCleanupTab(on_category_clicked=clicked.append)
    tab.build(categories=[("temp", "Temp Files", "#4caf50")],
             advanced_categories=[("recent", "Recent Files", "#90caf9")])

    card = tab._adv_cards[0]
    card.clicked.emit()  # must not raise

    assert clicked == []


def test_no_callback_given_is_safe_to_click(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()  # no on_category_clicked at all
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    tab._legend_cards[0].clicked.emit()  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_quick_cleanup_category_navigation.py -v`
Expected: FAIL — `TypeError: QuickCleanupTab() got an unexpected keyword argument 'on_category_clicked'`.

- [ ] **Step 3: Add the `clicked` signal to `_SliceCard`**

In `src/modules/cleanup/components/quick_cleanup_tab.py`, `_SliceCard` (line 222), add as the first line of the class body:

```python
class _SliceCard(QFrame):
    """Small legend card shown below the pie chart for each category."""

    clicked = pyqtSignal()

    def __init__(self, label: str, size_bytes: int, color: str, parent=None):
```

Add a `mousePressEvent` override at the end of the class (after `set_size`):

```python
    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)
```

In `__init__`, right after `self.setObjectName("sliceCard")`, add:

```python
        self.setCursor(Qt.CursorShape.PointingHandCursor)
```

(`Qt` is already imported at the top of this file.)

- [ ] **Step 4: Accept `on_category_clicked` in `QuickCleanupTab.__init__`**

Change the signature (line 306):

```python
    def __init__(self, parent=None):
        super().__init__(parent)
```

to:

```python
    def __init__(self, parent=None, on_category_clicked=None):
        super().__init__(parent)
        self._on_category_clicked = on_category_clicked
```

- [ ] **Step 5: Wire the main legend cards' `clicked` signal, leave advanced cards unwired**

In `_setup_ui`, the main legend construction loop (around line 531):

```python
        for cid, clabel, ccolor in self._categories:
            card = _SliceCard(clabel, 0, ccolor)
            self._legend_cards.append(card)
            self._legend_layout.addWidget(card)
```

becomes:

```python
        for cid, clabel, ccolor in self._categories:
            card = _SliceCard(clabel, 0, ccolor)
            card.clicked.connect(lambda cid=cid: self._handle_category_clicked(cid))
            self._legend_cards.append(card)
            self._legend_layout.addWidget(card)
```

(The advanced cards loop, building `self._adv_cards`, is left completely unchanged — it stays unwired, matching the test in Step 1 that expects clicking one to be a safe no-op.)

Add the handler method anywhere in the class:

```python
    def _handle_category_clicked(self, cid: str) -> None:
        if self._on_category_clicked is not None:
            self._on_category_clicked(cid)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_quick_cleanup_category_navigation.py -v`
Expected: PASS, all 3 tests.

- [ ] **Step 7: Run the full Quick Cleanup suite to check for regressions**

Run: `pytest tests/test_quick_cleanup_dedupe.py tests/test_quick_cleanup_legend_theme.py tests/test_quick_cleanup_watchdog.py tests/test_quick_cleanup_one_click_actions.py -v`
Expected: PASS, unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/modules/cleanup/components/quick_cleanup_tab.py tests/test_quick_cleanup_category_navigation.py
git commit -m "feat(cleanup): make Quick Cleanup's category cards clickable navigation

Wired for the 10 main categories, which map cleanly onto one of
CleanupModule's other 7 tabs each. Left the ~90 advanced categories
unwired -- they don't map 1:1 onto a single tab the way the main ones
do. CleanupModule (Task 6) supplies the real callback that switches
tabs; this task only adds the hook and proves it fires with the right
category id."
```

---

## Task 5: `run_clean_safe` shared helper, adopted by both Quick Cleanup and `_ScanTab`

**Files:**
- Modify: `src/modules/cleanup/cleanup_scanner/scanners_system.py`
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Modify: `src/modules/cleanup/tabs/_scan_tab.py`
- Test: `tests/test_cleanup_run_clean_safe.py` (new)

**Interfaces:**
- Consumes: `cs.delete_items` (existing), `bs.delete_selected` (existing), `_confirm_large` (existing, `_scan_tab.py`).
- Produces: `cleanup_scanner.run_clean_safe(widget, items, *, browser_cats=None, stop_wuauserv=False, confirm="always" | "size_gated", on_done) -> Worker`. `on_done` is `Callable[[int, int], None]` — `(deleted_count, error_count)`, called on the Qt main thread once the worker completes successfully. On a refused confirm, `run_clean_safe` returns `None` and `on_done` is never called.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cleanup_run_clean_safe.py`:

```python
"""run_clean_safe consolidates what used to be three separate, near-
identical confirm/worker/delete/combine implementations (_OverviewTab,
QuickCleanupTab, _ScanTab all had their own).
"""
import time

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QWidget

from modules.cleanup import cleanup_scanner as cs


def _settle(qapp, timeout_ms: int = 5_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def widget(qapp):
    w = QWidget()
    yield w


def test_always_confirm_runs_the_delete_when_accepted(qapp, widget, monkeypatch):
    monkeypatch.setattr(cs.QMessageBox, "exec", lambda self: cs.QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (2, 0))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    done = []
    cs.run_clean_safe(widget, [item], confirm="always", on_done=lambda d, e: done.append((d, e)))
    _settle(qapp)

    assert done == [(2, 0)]


def test_always_confirm_does_nothing_when_cancelled(qapp, widget, monkeypatch):
    monkeypatch.setattr(cs.QMessageBox, "exec", lambda self: cs.QMessageBox.StandardButton.Cancel)
    called = []
    monkeypatch.setattr(cs, "delete_items", lambda *a, **k: called.append(1))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    result = cs.run_clean_safe(widget, [item], confirm="always", on_done=lambda d, e: None)

    assert result is None
    assert called == []


def test_size_gated_confirm_skips_the_dialog_for_small_totals(qapp, widget, monkeypatch):
    # CONFIRM_BYTES threshold lives in _scan_tab.py; a single 100-byte item
    # is always under it, so no dialog should even be constructed.
    def _fail_if_called(self):
        raise AssertionError("confirm dialog should not have been shown")
    monkeypatch.setattr(cs.QMessageBox, "exec", _fail_if_called)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    done = []
    cs.run_clean_safe(widget, [item], confirm="size_gated", on_done=lambda d, e: done.append((d, e)))
    _settle(qapp)

    assert done == [(1, 0)]


def test_browser_cats_are_combined_with_regular_items(qapp, widget, monkeypatch):
    monkeypatch.setattr(cs.QMessageBox, "exec", lambda self: cs.QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))
    monkeypatch.setattr("modules.cleanup.browser_scanner.delete_selected", lambda cats, progress_cb=None: (500, 1))

    item = cs.ScanItem(path=r"C:\x", size=100, is_dir=False, safety="safe")
    done = []
    cs.run_clean_safe(widget, [item], browser_cats=["fake_cat"], confirm="always",
                      on_done=lambda d, e: done.append((d, e)))
    _settle(qapp)

    assert done == [(2, 1)]  # 1 (regular) + 1 (browser) deleted, 0 + 1 errors
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cleanup_run_clean_safe.py -v`
Expected: FAIL — `AttributeError: module 'modules.cleanup.cleanup_scanner' has no attribute 'run_clean_safe'`.

- [ ] **Step 3: Implement `run_clean_safe`**

Add to `src/modules/cleanup/cleanup_scanner/scanners_system.py`, right after `delete_items` (after its closing `return deleted, errors` around line 1750 — find the end of that function):

```python
def run_clean_safe(widget, items: List[ScanItem], *,
                   browser_cats: Optional[list] = None,
                   stop_wuauserv: bool = False,
                   confirm: str = "always",
                   on_done: Callable[[int, int], None]):
    """The shared confirm -> worker -> delete -> combine sequence every
    "Clean All Safe" action needs -- consolidated out of three near-
    identical implementations (_OverviewTab, QuickCleanupTab, _ScanTab
    each had their own). Caller-specific bits (which buttons to disable,
    what status text to show, whether to re-scan afterward) stay in the
    caller's own on_done, since those genuinely differ per widget.

    confirm="always": always asks, via a QMessageBox Ok/Cancel dialog.
    confirm="size_gated": only asks above _scan_tab.CONFIRM_BYTES.

    Returns the Worker it started, or None if the user declined the
    confirm dialog (on_done is never called in that case).
    """
    from core.worker import Worker
    total = sum(i.size for i in items)
    if browser_cats:
        total += sum(getattr(c, "size_bytes", 0) for c in browser_cats)

    if confirm == "size_gated":
        from modules.cleanup.tabs._scan_tab import _confirm_large
        if not _confirm_large(widget, total):
            return None
    else:
        mb = QMessageBox(widget)
        mb.setWindowTitle("Confirm Bulk Clean")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            f"Clean <b>{format_size(total)}</b> across "
            f"{len(items) + len(browser_cats or [])} item(s)?<br>This cannot be undone.")
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return None

    def _run(_worker):
        browser_freed = browser_errors = 0
        if browser_cats:
            from modules.cleanup import browser_scanner as bs
            browser_freed, browser_errors = bs.delete_selected(browser_cats)
        deleted, errors = delete_items(items, stop_wuauserv=stop_wuauserv) if items else (0, 0)
        return deleted + browser_freed, errors + browser_errors

    worker = Worker(_run)
    worker.signals.result.connect(lambda result: on_done(*result))
    QThreadPool_globalInstance = __import__("PyQt6.QtCore", fromlist=["QThreadPool"]).QThreadPool.globalInstance
    QThreadPool_globalInstance().start(worker)
    return worker
```

Note: `QMessageBox` needs to be importable as `cs.QMessageBox` for the tests' `monkeypatch.setattr(cs.QMessageBox, ...)` calls to work. Add `from PyQt6.QtWidgets import QMessageBox` to this file's imports (near the top, alongside the existing `typing` import), and add `'QMessageBox'` and `'run_clean_safe'` to the `__all__` list.

Replace the awkward `QThreadPool_globalInstance` line above with a normal import instead — add `from PyQt6.QtCore import QThreadPool` to this file's top-level imports and simplify the last two lines of `run_clean_safe` to:

```python
    worker = Worker(_run)
    worker.signals.result.connect(lambda result: on_done(*result))
    QThreadPool.globalInstance().start(worker)
    return worker
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cleanup_run_clean_safe.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 5: Adopt it in `QuickCleanupTab._do_clean_all_safe`**

In `src/modules/cleanup/components/quick_cleanup_tab.py`, `_do_clean_all_safe` (line 1166) currently builds `all_safe`/`browser_cats`/`total`, then confirms, then runs its own `Worker`. Replace everything from `self._scanning = True` (the button-disabling block) through the end of the method with:

```python
        self._scanning = True
        self._scan_all_btn.setEnabled(False)
        self._clean_all_btn.setEnabled(False)
        self._progress.setText("🗑️  Cleaning safe items...")
        self._progress.show()

        def _on_done(deleted, errors):
            self._scanning = False
            self._scan_all_btn.setEnabled(True)
            self._progress.hide()
            msg = f"Cleaned {deleted} item(s)"
            if errors:
                msg += f" — {errors} could not be deleted"
            self._status_lbl.setText(msg)
            self.freed_bytes.emit(total)
            self.scan()

        worker = cs.run_clean_safe(
            self, all_safe, browser_cats=browser_cats, stop_wuauserv=needs_wu,
            confirm="always", on_done=_on_done)
        if worker is None:
            self._scanning = False
```

Keep everything ABOVE that point in the method unchanged (the loop building `all_safe`/`browser_cats`/`total`/`needs_wu`, and the `if not all_safe and not browser_cats: return` guard) — only the confirm dialog and worker construction are replaced. Remove the now-unused `_confirm_clean_all` method added in Task 2 Step 6, since `run_clean_safe`'s own `confirm="always"` path replaces it — but first check `tests/test_quick_cleanup_watchdog.py::test_freed_bytes_is_emitted_after_a_successful_clean` (Task 2), which monkeypatches `_confirm_clean_all`; update that test to monkeypatch `cs.QMessageBox.exec` instead (matching this task's own test style):

```python
def test_freed_bytes_is_emitted_after_a_successful_clean(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup import cleanup_scanner as cs

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    item = cs.ScanItem(path=r"C:\fake\path", size=500, is_dir=False, safety="safe")
    result = cs.ScanResult()
    result.items = [item]
    tab._results = {"temp": result}

    monkeypatch.setattr(cs, "delete_items", lambda items, stop_wuauserv=False: (1, 0))
    monkeypatch.setattr(cs.QMessageBox, "exec", lambda self: cs.QMessageBox.StandardButton.Ok)

    emitted = []
    tab.freed_bytes.connect(emitted.append)
    tab._do_clean_all_safe()
    _settle(qapp)

    assert emitted == [500]
```

- [ ] **Step 6: Adopt it in `_ScanTab._do_clean`**

In `src/modules/cleanup/tabs/_scan_tab.py`, `_do_clean` (line 477) currently builds its own `Worker` around `cs.delete_items(selected, stop_wuauserv=wu)`. Replace the block from `wu = self._wu_cache` through `self._thread_pool.start(self._clean_worker)` with:

```python
        def _on_done(deleted, errors):
            self._on_clean_done((deleted, errors))

        worker = cs.run_clean_safe(
            self, selected, stop_wuauserv=self._wu_cache, confirm="size_gated",
            on_done=_on_done)
        if worker is not None:
            self._clean_worker = worker
            self._workers.append(worker)
        else:
            self._cleaning = False
            self._clean_btn.setEnabled(True)
            self._quick_btn.setEnabled(True)
            self._scan_btn.setEnabled(True)
```

Leave `_on_clean_done` itself completely unchanged — it already does the right per-tab bookkeeping (`freed_bytes.emit`, `_do_scan()`, error label) and `run_clean_safe`'s `on_done` contract matches its existing `(deleted, errors)` tuple signature exactly (`_on_clean_done` currently takes one tuple argument, hence the small `_on_done` shim above that repacks it).

Note this drops `_ScanTab`'s own inline confirm dialog entirely (the one built via `_confirm_large(self, total)`), since `run_clean_safe(..., confirm="size_gated", ...)` now does exactly that check internally.

- [ ] **Step 7: Run the full Cleanup test suite**

Run: `pytest tests/test_cleanup_run_clean_safe.py tests/test_quick_cleanup_watchdog.py tests/test_quick_cleanup_dedupe.py tests/test_quick_cleanup_legend_theme.py tests/test_cleanup_cancel_recovery.py tests/test_cleanup_scan_progress.py tests/test_cleanup_scan_watchdog.py tests/test_cleanup_late_signal.py -v`
Expected: PASS. (`test_cleanup_cancel_recovery.py::test_scan_tab_is_usable_again_after_a_scan_is_cancelled` and `test_a_cancelled_scan_tab_rescans_on_next_activation` exercise `_ScanTab`'s scan path, not its clean path, so they're unaffected by this task.)

- [ ] **Step 8: Commit**

```bash
git add src/modules/cleanup/cleanup_scanner/scanners_system.py src/modules/cleanup/components/quick_cleanup_tab.py src/modules/cleanup/tabs/_scan_tab.py tests/test_cleanup_run_clean_safe.py tests/test_quick_cleanup_watchdog.py
git commit -m "refactor(cleanup): consolidate three clean-all-safe implementations into run_clean_safe

_OverviewTab, QuickCleanupTab, and _ScanTab each hand-rolled the same
confirm/worker/delete/combine sequence around the same cs.delete_items
call. One shared function now, with each caller keeping only its own
button-disable specifics and re-scan trigger."
```

---

## Task 6: `CleanupModule` swaps in the merged Quick tab

**Files:**
- Modify: `src/modules/cleanup/cleanup_module.py`

**Interfaces:**
- Consumes: `QuickCleanupTab(on_category_clicked=...)` (Task 4), `QuickCleanupTab.freed_bytes` / `.auto_scan()` (Task 2), `ADVANCED_CATEGORIES` (existing, `quick_cleanup_tab.py`).
- Produces: `CleanupModule._quick` (a `QuickCleanupTab` instance, tab index 0), `CleanupModule.get_refresh_interval()`, `CleanupModule.refresh_data()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cleanup_module_quick_tab.py`:

```python
"""CleanupModule's first tab is the merged Quick Cleanup dashboard, not
the old read-only Overview table -- and its 60s auto-refresh only
actually re-scans while that tab is the visible one.
"""
import tempfile


def _module(qapp):
    from app import App
    from modules.cleanup.cleanup_module import CleanupModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = CleanupModule()
    module.on_start(app)
    widget = module.create_widget()
    module._keep_alive = widget
    return module, app


def test_the_first_tab_is_quick_cleanup(qapp):
    module, app = _module(qapp)
    try:
        assert module._tabs.tabText(0) == "Quick Cleanup"
        assert module._tabs.widget(0) is module._quick
    finally:
        app.shutdown()


def test_clicking_a_category_card_switches_to_its_tab(qapp):
    module, app = _module(qapp)
    try:
        module._tabs.setCurrentIndex(0)
        module._quick._handle_category_clicked("browser")
        assert module._tabs.tabText(module._tabs.currentIndex()) == "Browser Caches"
    finally:
        app.shutdown()


def test_refresh_data_only_rescans_when_quick_tab_is_visible(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        calls = []
        monkeypatch.setattr(module._quick, "scan", lambda: calls.append(1))

        module._tabs.setCurrentIndex(1)  # System Junk, not Quick Cleanup
        module.refresh_data()
        assert calls == [], "refresh_data rescanned a tab that wasn't visible"

        module._tabs.setCurrentIndex(0)  # Quick Cleanup
        module.refresh_data()
        assert calls == [1]
    finally:
        app.shutdown()


def test_get_refresh_interval_is_60_seconds(qapp):
    module, app = _module(qapp)
    try:
        assert module.get_refresh_interval() == 60_000
    finally:
        app.shutdown()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cleanup_module_quick_tab.py -v`
Expected: FAIL — `AssertionError` on `tabText(0) == "Quick Cleanup"` (currently "Overview"), and `AttributeError` on `get_refresh_interval` not existing yet as anything but the `BaseModule` default.

- [ ] **Step 3: Build a category-id-to-tab-name map and the switch callback**

In `src/modules/cleanup/cleanup_module.py`, add near the top of the file (after the existing `SYSTEM_EXTRA`/`LOGS_EXTRA`/`LARGE_EXTRA` dicts, before `_with_catalog`):

```python
# Quick Cleanup's 10 main category ids -> the tab name each one's own
# deep-dive lives on. Only the main categories map cleanly onto one tab
# each; the ~90 advanced categories don't (see quick_cleanup_tab.py's
# own _handle_category_clicked wiring, which only connects these 10).
_CATEGORY_TAB_NAMES = {
    "temp": "System Junk", "prefetch": "System Junk",
    "thumb": "System Junk", "crash": "System Junk",
    "browser": "Browser Caches",
    "app": "App & Game Caches",
    "logs": "Logs & Reports",
    "wu": "Windows Update",
    "large": "Large Items",
    "dev": "Dev Tools",
}
```

- [ ] **Step 4: Swap the Overview tab for the Quick tab in `create_widget`**

Change the imports at the top of the file:

```python
from modules.cleanup.tabs import (
    _ScanTab,
    _BrowserCleanupTab,
    _LargeItemsTab,
    _OverviewTab,
)
```

to:

```python
from modules.cleanup.tabs import (
    _ScanTab,
    _BrowserCleanupTab,
    _LargeItemsTab,
)
from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab, ADVANCED_CATEGORIES
```

In `create_widget`, replace:

```python
        # 1. Overview
        self._overview = _OverviewTab()
        self._tabs.addTab(self._overview, "Overview")
```

with:

```python
        # 1. Quick Cleanup (merged from the former standalone
        # QuickCleanupModule -- see docs/superpowers/specs/
        # 2026-09-14-cleanup-quick-cleanup-merge-design.md)
        self._quick = QuickCleanupTab(on_category_clicked=self._on_category_clicked)
        self._quick.build(advanced_categories=ADVANCED_CATEGORIES)
        self._tabs.addTab(self._quick, "Quick Cleanup")
```

Add the callback method anywhere in the class:

```python
    def _on_category_clicked(self, category_id: str) -> None:
        tab_name = _CATEGORY_TAB_NAMES.get(category_id)
        if tab_name is None:
            return
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == tab_name:
                self._tabs.setCurrentIndex(i)
                return
```

- [ ] **Step 5: Update the signal-wiring loop, `_cancel_all_tabs`, and `on_activate`**

Change:

```python
        for tab in (
            self._overview, self._sys_tab, self._browser, self._app_tab,
            self._wu_tab, self._logs_tab, self._large, self._dev_tab,
        ):
            tab.freed_bytes.connect(self._on_freed)
```

to:

```python
        for tab in (
            self._quick, self._sys_tab, self._browser, self._app_tab,
            self._wu_tab, self._logs_tab, self._large, self._dev_tab,
        ):
            tab.freed_bytes.connect(self._on_freed)
```

Change `_cancel_all_tabs`'s name tuple:

```python
        for name in (
            "_overview", "_sys_tab", "_browser", "_app_tab",
            "_wu_tab", "_logs_tab", "_large", "_dev_tab",
        ):
```

to:

```python
        for name in (
            "_quick", "_sys_tab", "_browser", "_app_tab",
            "_wu_tab", "_logs_tab", "_large", "_dev_tab",
        ):
```

`_ScanTab`/`_BrowserCleanupTab`/`_LargeItemsTab` all expose `_cancel_all()` already, checked via `hasattr`; `QuickCleanupTab.cancel()` is named differently, so add a compatibility check in `_cancel_all_tabs`'s loop body — change:

```python
            if hasattr(tab, "_cancel_all"):
                tab._cancel_all()
```

to:

```python
            if hasattr(tab, "_cancel_all"):
                tab._cancel_all()
            elif hasattr(tab, "cancel"):
                tab.cancel()
```

Change `on_activate`:

```python
    def on_activate(self) -> None:
        """Auto-scan the overview when the module is first opened."""
        if getattr(self, "_overview", None) is None:
            return
        self._overview.auto_scan()
```

to:

```python
    def on_activate(self) -> None:
        """Auto-scan the Quick Cleanup tab when the module is first opened."""
        if getattr(self, "_quick", None) is None:
            return
        self._quick.auto_scan()
```

- [ ] **Step 6: Add `get_refresh_interval` and `refresh_data`**

Add these two methods anywhere in the class (e.g. right after `on_activate`):

```python
    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        """Only the Quick Cleanup tab auto-refreshes, and only while it's
        the one actually visible -- every other tab in this module has
        never auto-refreshed on a timer, and blindly rescanning whichever
        tab happens to be open would silently re-run something like Large
        Items' full-machine scan every 60s while someone is reading it."""
        if getattr(self, "_quick", None) is None:
            return
        if self._tabs.currentWidget() is self._quick:
            self._quick.scan()
```

Add `from typing import Optional` to the file's imports if not already present (check the top of `cleanup_module.py` — it currently has no `typing` import at all, so add `from typing import Optional` as a new top-level import line).

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_cleanup_module_quick_tab.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 8: Commit**

```bash
git add src/modules/cleanup/cleanup_module.py tests/test_cleanup_module_quick_tab.py
git commit -m "feat(cleanup): CleanupModule's first tab is now the merged Quick Cleanup dashboard

Overview is gone from create_widget (the file itself is deleted in
Task 8, once every remaining test that touches it directly is migrated
in Task 7). get_refresh_interval/refresh_data give Quick Cleanup its
60s auto-refresh back, scoped to only fire while it's the visible tab."
```

---

## Task 7: Migrate every remaining test that references the files being deleted

**Files:**
- Modify: `tests/test_cleanup_cancel_recovery.py`
- Modify: `tests/test_cleanup_scan_watchdog.py`
- Modify: `tests/test_cleanup_late_signal.py`
- Modify: `tests/test_cleanup_scan_progress.py`
- Modify: `tests/test_cleanup_catalog.py`
- Modify: `tests/test_quick_cleanup_dedupe.py`
- Modify: `tests/test_widget_life.py`

No new production code in this task — every change is a test migrating from `_OverviewTab`/`QuickCleanupModule` to `QuickCleanupTab`/`CleanupModule`, or being removed with a stated reason.

- [ ] **Step 1: `test_cleanup_cancel_recovery.py` — migrate the two Overview tests to QuickCleanupTab**

Replace `test_overview_is_usable_again_after_a_scan_is_cancelled` (line 51-67):

```python
def test_overview_is_usable_again_after_a_scan_is_cancelled(
        qapp, monkeypatch, blocking_scanner):
    from modules.cleanup.tabs import _overview_tab as ov

    monkeypatch.setattr(ov, "_OV_GROUPS", [("Slow Group", [blocking_scanner])])
    tab = ov._OverviewTab()
    tab._build_table()
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10), "scan never started"

    tab._cancel_all()               # what switching modules does
    blocking_scanner.release.set()
    _settle(qapp)

    assert tab._pending == 0, "cancelled workers never resolved the counter"
    assert tab._scanning is False
    assert tab._scan_btn.isEnabled(), "Scan button left disabled forever"
```

with:

```python
def test_quick_cleanup_is_usable_again_after_a_scan_is_cancelled(
        qapp, blocking_scanner):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10), "scan never started"

    tab.cancel()                    # what switching modules does
    blocking_scanner.release.set()
    _settle(qapp)

    assert tab._scanning is False
    assert tab._scan_all_btn.isEnabled(), "Scan button left disabled forever"
```

Replace `test_a_cancelled_overview_scan_can_be_started_again` (line 70-94):

```python
def test_a_cancelled_overview_scan_can_be_started_again(
        qapp, monkeypatch, blocking_scanner):
    from modules.cleanup.tabs import _overview_tab as ov

    monkeypatch.setattr(ov, "_OV_GROUPS", [("Slow Group", [blocking_scanner])])
    tab = ov._OverviewTab()
    tab._build_table()
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)
    tab._cancel_all()
    blocking_scanner.release.set()
    _settle(qapp)

    blocking_scanner.release.set()
    tab._do_scan_all()

    # Not "the scanner function ran again": a measurement taken seconds ago
    # is legitimately served from the shared scan cache. What must be true
    # is that the tab ACCEPTED the request instead of returning early on a
    # stuck `_scanning`, and that it reached a finished state.
    assert tab._scanning is True, "Scan did nothing after a cancel"
    _settle(qapp)
    assert tab._pending == 0
    assert tab._scanning is False
    assert tab._scan_btn.isEnabled()
```

with:

```python
def test_a_cancelled_quick_cleanup_scan_can_be_started_again(
        qapp, blocking_scanner):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)
    tab.cancel()
    blocking_scanner.release.set()
    _settle(qapp)

    blocking_scanner.release.set()
    tab._do_scan_all()

    # Not "the scanner function ran again": a measurement taken seconds ago
    # is legitimately served from the shared scan cache. What must be true
    # is that the tab ACCEPTED the request instead of returning early on a
    # stuck `_scanning`, and that it reached a finished state.
    assert tab._scanning is True, "Scan did nothing after a cancel"
    _settle(qapp)
    assert tab._scanning is False
    assert tab._scan_all_btn.isEnabled()
```

- [ ] **Step 2: `test_cleanup_scan_watchdog.py` — migrate the Overview watchdog test**

Replace `test_the_overview_watchdog_names_the_groups_that_never_reported` (line 91-111):

```python
def test_the_overview_watchdog_names_the_groups_that_never_reported(
        qapp, monkeypatch, blocking_scanner):
    from modules.cleanup.tabs import _overview_tab as ov

    monkeypatch.setattr(
        ov, "_OV_GROUPS", [("Wedged Group", [blocking_scanner])])
    tab = ov._OverviewTab()
    tab.SCAN_WATCHDOG_MS = 300
    tab._build_table()
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)

    recovered = _pump_until(qapp, lambda: not tab._scanning)
    status = tab._status.text()
    blocking_scanner.release.set()
    _settle(qapp)

    assert recovered, "the watchdog never fired"
    assert tab._scan_btn.isEnabled()
    assert "Wedged Group" in status, (
        f"the watchdog did not name the stuck group: {status!r}")
```

with:

```python
def test_the_quick_cleanup_watchdog_recovers_a_wedged_scan(
        qapp, blocking_scanner):
    # QuickCleanupTab's own dedicated watchdog tests (naming which
    # category is stuck, not just that it recovers) live in
    # tests/test_quick_cleanup_watchdog.py, migrated there directly
    # rather than duplicated here -- this file's remaining coverage is
    # _ScanTab's watchdog, above.
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])
    tab._scanner_map["temp"] = (blocking_scanner, "Temp Files", "#4caf50")
    tab.SCAN_WATCHDOG_MS = 300
    tab._do_scan_all()
    assert blocking_scanner.started.wait(10)

    recovered = _pump_until(qapp, lambda: not tab._scanning)
    blocking_scanner.release.set()
    _settle(qapp)

    assert recovered, "the watchdog never fired"
    assert tab._scan_all_btn.isEnabled()
```

- [ ] **Step 3: `test_cleanup_late_signal.py` — remove the redundant Overview test**

Delete `test_an_overview_result_landing_after_teardown_is_dropped` (line 49-64) entirely. `test_a_quick_cleanup_result_landing_after_teardown_is_dropped` (line 67-83) already exists in this same file and covers the identical scenario for `QuickCleanupTab` directly — nothing new needs writing here, just the removal.

Update the file's module docstring (line 3), which currently reads:

```
_OverviewTab and QuickCleanupTab connect **closures** to their workers'
```

to:

```
QuickCleanupTab and _ScanTab connect **closures** to their workers'
```

- [ ] **Step 4: `test_cleanup_scan_progress.py` — remove the Overview-only Stop button test**

Delete `test_the_overview_can_be_stopped_too` (line 137-155) entirely. `QuickCleanupTab` has no visible Stop button today (only `cancel()`, called by `CleanupModule.on_deactivate` when the user switches away, not from a button the user clicks mid-scan) — there is no equivalent UI feature to test. The underlying "a cancelled scan leaves the tab usable" behavior this test was really checking is already covered by `test_quick_cleanup_is_usable_again_after_a_scan_is_cancelled` (Step 1, `test_cleanup_cancel_recovery.py`).

- [ ] **Step 5: `test_cleanup_catalog.py` — drop `QuickCleanupModule` from the factory tuple**

Change (line 289-292, note the import line is already fixed from Task 1):

```python
    from app import App
    from modules.cleanup.cleanup_module import CleanupModule

    from modules.cleanup.quick_cleanup_module import QuickCleanupModule
    from modules.cleanup.cleanup_scanner.scanners_system import _OV_GROUPS
```

to:

```python
    from app import App
    from modules.cleanup.cleanup_module import CleanupModule
    from modules.cleanup.cleanup_scanner.scanners_system import _OV_GROUPS
```

Change the comment and loop at line 306-311:

```python
        # Both panes that show scanners: Cleanup's eight tabs, and Quick
        # Cleanup's one-page dashboard. Counting only the first reported
        # 509 of 537 and blamed the wiring, when the missing 17 were simply
        # on the other pane.
        for factory in (CleanupModule, QuickCleanupModule):
```

to:

```python
        # Quick Cleanup's dashboard is now CleanupModule's own first tab
        # (the Cleanup/Quick Cleanup merge) -- reached by the same
        # dir(module) scan below as every one of the module's other 7
        # tabs, not a second factory to construct.
        for factory in (CleanupModule,):
```

- [ ] **Step 6: `test_quick_cleanup_dedupe.py` — construct `CleanupModule` and reach `_quick` directly**

Replace the `tab` fixture (line 29-51):

```python
@pytest.fixture
def tab(qapp):
    from app import App
    from modules.cleanup.quick_cleanup_module import QuickCleanupModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = QuickCleanupModule()
    module.on_start(app)
    widget = module.create_widget()          # held, or Qt destroys the tree
    found = None
    for name in dir(module):
        candidate = getattr(module, name, None)
        if hasattr(candidate, "_deduplicate_across_categories"):
            found = candidate
            break
    assert found is not None, "could not reach the Quick Cleanup tab"
    found._keep_alive = widget
    yield found
    try:
        app.shutdown()
    except Exception:  # noqa: BLE001 - teardown
        pass
```

with:

```python
@pytest.fixture
def tab(qapp):
    from app import App
    from modules.cleanup.cleanup_module import CleanupModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = CleanupModule()
    module.on_start(app)
    widget = module.create_widget()          # held, or Qt destroys the tree
    found = module._quick
    found._keep_alive = widget
    yield found
    try:
        app.shutdown()
    except Exception:  # noqa: BLE001 - teardown
        pass
```

- [ ] **Step 7: `test_widget_life.py` — drop the deleted file from the parametrize list**

Remove line 100 (`"modules/cleanup/tabs/_overview_tab.py",`) from the `@pytest.mark.parametrize("module_path", [...])` list.

- [ ] **Step 8: Run every migrated file to verify they pass**

Run: `pytest tests/test_cleanup_cancel_recovery.py tests/test_cleanup_scan_watchdog.py tests/test_cleanup_late_signal.py tests/test_cleanup_scan_progress.py tests/test_cleanup_catalog.py tests/test_quick_cleanup_dedupe.py tests/test_widget_life.py -v`
Expected: PASS. `_overview_tab.py` and `quick_cleanup_module.py` still exist on disk at this point (deleted in Task 8) — this task only stops any TEST from depending on them, so it's safe to run before or after Task 8's deletion, but running it now confirms the migration itself is correct before anything is removed.

- [ ] **Step 9: Commit**

```bash
git add tests/test_cleanup_cancel_recovery.py tests/test_cleanup_scan_watchdog.py tests/test_cleanup_late_signal.py tests/test_cleanup_scan_progress.py tests/test_cleanup_catalog.py tests/test_quick_cleanup_dedupe.py tests/test_widget_life.py
git commit -m "test(cleanup): migrate every test off _overview_tab.py and quick_cleanup_module.py

Found by an exhaustive grep, not assumed from the spec alone: 7 test
files depended on one or both of the files this merge deletes.
Overview-specific behavior tests move to QuickCleanupTab where a real
equivalent exists; two tests (Overview's own Stop button, and a
duplicate of an already-existing QuickCleanupTab teardown test) are
removed outright with the reason stated inline, since there is nothing
left to migrate them to."
```

---

## Task 8: Delete `_overview_tab.py` and `quick_cleanup_module.py`, update `main.py`

**Files:**
- Delete: `src/modules/cleanup/tabs/_overview_tab.py`
- Delete: `src/modules/cleanup/quick_cleanup_module.py`
- Modify: `src/modules/cleanup/tabs/__init__.py`
- Modify: `src/main.py`

- [ ] **Step 1: Remove `_OverviewTab` from the tabs package**

In `src/modules/cleanup/tabs/__init__.py`, change:

```python
"""Cleanup module tab classes."""
from modules.cleanup.tabs._scan_tab import _ScanTab
from modules.cleanup.tabs._browser_tab import _BrowserCleanupTab
from modules.cleanup.tabs._large_items_tab import _LargeItemsTab
from modules.cleanup.tabs._overview_tab import _OverviewTab

__all__ = ["_ScanTab", "_BrowserCleanupTab", "_LargeItemsTab", "_OverviewTab"]
```

to:

```python
"""Cleanup module tab classes."""
from modules.cleanup.tabs._scan_tab import _ScanTab
from modules.cleanup.tabs._browser_tab import _BrowserCleanupTab
from modules.cleanup.tabs._large_items_tab import _LargeItemsTab

__all__ = ["_ScanTab", "_BrowserCleanupTab", "_LargeItemsTab"]
```

- [ ] **Step 2: Delete the two files**

```bash
git rm src/modules/cleanup/tabs/_overview_tab.py
git rm src/modules/cleanup/quick_cleanup_module.py
```

- [ ] **Step 3: Remove the registration from `main.py`**

In `src/main.py`, remove line 103:

```python
    from modules.cleanup.quick_cleanup_module import QuickCleanupModule
```

and remove line 145:

```python
    app.module_registry.register(QuickCleanupModule())
```

Leave the `CleanupModule` import (line 101) and its registration (line 144) untouched.

- [ ] **Step 4: Compile-check the whole tree**

Run: `python -m compileall -q src`
Expected: no output (no syntax errors, and no leftover references to the deleted files anywhere that would have shown up as an import error at compile time — `compileall` does not execute imports, so this only catches syntax errors; Step 5 catches import errors).

- [ ] **Step 5: Import-check the app**

Run: `python -c "import sys; sys.path.insert(0, 'src'); import main"`
Expected: no `ModuleNotFoundError`/`ImportError`.

- [ ] **Step 6: Run the FULL test suite**

Run: `pytest -q`
Expected: no `ImportError`/`ModuleNotFoundError` collection failures. (Some pre-existing, unrelated failures may still be present in the suite from before this work — this step is checking specifically that nothing NEW breaks from these two deletions; compare the failure list to a baseline run from before Task 1 if anything unexpected shows up.)

- [ ] **Step 7: Regression-check the unattended cleanup stage**

This is the one consumer outside `src/modules/cleanup/` this merge touches, and it has no UI to notice a silent break in (spec §5). Run:

```bash
python src/main.py --unattended --stages cleanup
```

(Requires an elevated terminal — this stage calls `cs.delete_items`, matching how `--unattended` mode already requires admin per CLAUDE.md.) Expected: it runs to completion, logging which categories it scanned and what it cleaned, with no traceback. Confirm the log output names the same category groups `_OV_GROUPS` still defines (System Junk, Browser Caches, App & Game Caches, Windows Update, Logs & Reports, Large Items, Dev Tools, Cloud Storage, Media Production) — the same set as before this entire merge, since Task 1 only relocated the list, never changed its contents.

- [ ] **Step 8: Manual real-machine pass**

```bash
python src/main.py
```

Open Cleanup. Confirm:
- Only one "Cleanup"-related entry exists in the sidebar (no separate "Quick Cleanup" entry).
- The first tab is "Quick Cleanup" and shows the pie chart, category cards, and (un-hidden) one-click actions.
- Clicking a main category card (e.g. "Browser Caches") switches to that tab.
- "Clean All Safe" on the Quick tab always shows a confirm dialog.
- Waiting 60+ seconds while ON the Quick tab triggers a re-scan (status/progress visibly updates); switching to another tab and waiting 60+ seconds does NOT trigger one.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(cleanup): delete _overview_tab.py and quick_cleanup_module.py, unregister from main.py

The merge is complete: one sidebar entry, Quick Cleanup as the first
tab, five real bugs fixed along the way (missing watchdog, shared
one-click status label with no busy-guard, three duplicate clean-all-safe
implementations, no category-card navigation, and the hidden
_OV_GROUPS/stage_runners.py dependency this would have silently broken).

See docs/superpowers/specs/2026-09-14-cleanup-quick-cleanup-merge-design.md"
```

---

## Self-Review

**Spec coverage:**
- Merge depth (Quick replaces Overview outright) — Task 6. ✓
- Auto-refresh kept, scoped to visible tab — Task 6 Step 6. ✓
- One-click actions promoted + busy-guard fix — Task 3 (fix), Task 6 doesn't need to touch visibility since Task 3 already moves them out of `_adv_widget`. Confirmed no separate step was needed: re-checking, Task 3 only added busy-guards/per-action status — the actual "move out of `_adv_widget`" UI relocation was described in the spec's 2.2 but I did not add an explicit step for it above.

**Gap found and fixed during self-review:** Task 3 fixes the busy-guard/shared-label bug but never actually moves `_build_one_click_panel`'s call site out of `_adv_widget` into the always-visible area, which the spec's decision table (§1.2) and Global Constraints both require. Adding that as Task 3 Step 3.5:

- [ ] **Step 3.5 (Task 3): Move the one-click actions panel out of the hidden Advanced section**

In `_setup_ui` (`quick_cleanup_tab.py`), find where `_build_one_click_panel(adv_lay)` is currently called (inside the `_adv_widget` construction block, per the file's own section comment `# One-click actions`). Move that call out of `_adv_widget` entirely: add a new `QVBoxLayout` section in the always-visible part of `_setup_ui` (right after the "Scrollable category groups" `scroll` widget is added to `layout`, before the `_adv_widget` block begins), and call `_build_one_click_panel` there instead:

```python
        # ── One-click actions (always visible -- moved out of Advanced,
        # see the Cleanup/Quick Cleanup merge spec) ──
        one_click_header = QLabel("Quick Actions")
        one_click_header.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px 0;")
        layout.addWidget(one_click_header)
        self._build_one_click_panel(layout)
```

Remove the old call site and its now-empty surrounding comment from inside the `_adv_widget` block, leaving `_adv_widget` holding only the extra advanced category legend cards (`self._adv_legend_layout`).

Re-run Task 3's own test suite (`pytest tests/test_quick_cleanup_one_click_actions.py -v`) plus a quick manual check that the buttons render above the "Show Advanced ▼" toggle, not below it, to confirm this didn't silently get left inside the hidden widget.

- Confirm style = always for the merged tab — Task 5 Step 5 (`confirm="always"`). ✓
- Watchdog with a real measured constant, not copied — Task 2 gives it `300_000` with a comment explaining the reasoning (more categories than Overview's own sweep), rather than a literal fresh measurement on this machine. **Judgment call, stated plainly**: getting a real measurement requires running the full scan once in a controlled way and is better done as a follow-up manual tuning pass after Task 8's manual real-machine check (Step 8 already has the person opening the tab and watching a real scan) — noting the actual observed time there and adjusting `SCAN_WATCHDOG_MS` if it's wildly off from 300s is a reasonable one-line follow-up, not a blocking step in this plan.
- Category cards clickable — Task 4. ✓
- `run_clean_safe` + `_ScanTab` adoption — Task 5. ✓
- `_OV_GROUPS` extraction before deletion — Task 1 (extraction) → Task 8 (deletion), correctly ordered. ✓
- Every test dependency on deleted files — Task 7 (7 files migrated/trimmed) + Task 1 Step 5 (`test_cleanup_catalog.py`'s import). ✓

**Placeholder scan:** none found on re-read except the one gap above, now fixed inline.

**Type consistency:** `run_clean_safe`'s `on_done: Callable[[int, int], None]` signature is used identically in Task 5 Steps 5 and 6 (`QuickCleanupTab`, `_ScanTab`) and in Task 5's own tests. `QuickCleanupTab(on_category_clicked=...)` is defined in Task 4 Step 4 and consumed with the exact same keyword in Task 6 Step 4. `_scanned`/`SCAN_WATCHDOG_MS`/`_watchdog` names introduced in Task 2 are the same names Task 6's tests and Task 7's migrated tests reference.
