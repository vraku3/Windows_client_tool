# New Cleanup Targets (Sub-project 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `scan_windows_old`'s two real bugs, add two one-click actions (hibernation right-sizing, clear stuck print jobs), and add one new scanner (orphaned user profiles).

**Architecture:** Four independent, non-overlapping changes across existing files (`scanners_system.py`, `quick_cleanup_tab.py`, `cleanup_module.py`) plus one new file (`scanners_profiles.py`). No shared state between tasks — they can be reviewed and merged in any order, listed here from smallest/safest to largest.

**Tech Stack:** Python 3.12, PyQt6, pytest, `winreg` (stdlib).

**Spec:** `docs/superpowers/specs/2026-09-14-cleanup-new-targets-design.md`

## Global Constraints

- No changes to `scan_cache`, the scanner catalog engine, or any scanner not named in this plan.
- New scanner (`scan_orphaned_user_profiles`) must default to `safety="danger"` — never auto-selected by any "Clean All Safe"/"Clean All" bulk action.
- A registry read that is refused or fails must report NOTHING found, never "everything is orphaned" (CLAUDE.md: a refusal is never an answer).
- Both new one-click actions follow the exact busy-guard/status/`_run_action_command` pattern already established in `quick_cleanup_tab.py` — no new UI mechanism.

---

## Task 1: Fix `scan_windows_old` — drive letter and safety level

**Files:**
- Modify: `src/modules/cleanup/cleanup_scanner/scanners_system.py:75-82`
- Test: `tests/test_cleanup_windows_old_fix.py` (new)

**Interfaces:**
- Consumes: `os.environ.get("SystemDrive", "C:")` (existing stdlib pattern, matches how other scanners in this codebase read environment variables rather than hardcoding drive letters).
- Produces: `scan_windows_old(min_age_days=0) -> ScanResult` — same signature, same call sites unaffected (`cleanup_scanner/__init__.py`'s `_OV_GROUPS`, `_large_items_tab.py`'s `LARGE_SCANNERS`, `quick_cleanup_tab.py`'s `_id_map["large"]`, `cleanup_module.py`'s `LARGE_EXTRA` don't need changes — none of them read `item.safety` directly at the call site, they read it off the returned `ScanItem`).

**Behavioral note for the implementer:** changing `safety` from `"safe"` to `"caution"` does NOT change whether this item is pre-checked in the Large Items tab's own tree (`_ScanTab` checks every item by default regardless of safety — see `_scan_tab.py`'s scan-result rendering, `child.setCheckState(0, Qt.CheckState.Checked)` unconditionally). What it DOES change: `QuickCleanupTab._do_clean_all_safe` only sweeps items where `item.safety == "safe"` into its dashboard-level "Clean All Safe" — after this fix, a fresh user clicking that one button will no longer have Windows.old silently deleted as a side effect of clicking "clean everything safe." That is the actual bug being fixed; don't describe it as "no longer pre-checked in Large Items" in the commit message, that part doesn't change.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cleanup_windows_old_fix.py`:

```python
"""scan_windows_old had two real bugs: it hardcoded C:\\Windows.old (a
Windows install on D: was invisible to it, violating this codebase's own
"never a drive letter" catalog convention), and it was marked
safety="safe" -- meaning QuickCleanupTab's dashboard-level "Clean All
Safe" button would silently delete it with zero confirmation, more
confident than Microsoft's own guidance (Windows.old shares hardlinked
files with the live OS; Disk Cleanup / Storage Sense is the supported
removal path, not a raw folder delete).
"""
import os
import tempfile

from modules.cleanup.cleanup_scanner.scanners_system import scan_windows_old


def _fake_windows_old(monkeypatch):
    """Point SystemDrive at a real temp directory with a real
    Windows.old subfolder inside it, so the scanner's own
    os.path.join(SystemDrive + "\\", "Windows.old") lands on real,
    scannable content -- no os.path monkeypatching needed."""
    tmp = tempfile.mkdtemp()
    old_dir = os.path.join(tmp, "Windows.old")
    os.makedirs(old_dir)
    with open(os.path.join(old_dir, "stub.txt"), "w") as f:
        f.write("x" * 1000)
    monkeypatch.setenv("SystemDrive", tmp)
    return old_dir


def test_follows_systemdrive_rather_than_a_hardcoded_c(monkeypatch):
    old_dir = _fake_windows_old(monkeypatch)
    result = scan_windows_old()
    assert len(result.items) == 1
    assert os.path.normcase(result.items[0].path) == os.path.normcase(old_dir)


def test_safety_is_caution_not_safe(monkeypatch):
    _fake_windows_old(monkeypatch)
    result = scan_windows_old()
    assert result.items[0].safety == "caution"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cleanup_windows_old_fix.py -v`
Expected: FAIL — the current implementation hardcodes `C:\Windows.old`, so a `SystemDrive`-pointed temp dir's `Windows.old` is never found (0 items), and even if it were found, `safety` would be `"safe"`.

- [ ] **Step 3: Fix `scan_windows_old`**

In `src/modules/cleanup/cleanup_scanner/scanners_system.py`, replace lines 75-82:

```python
def scan_windows_old(min_age_days: int = 0) -> ScanResult:
    """Windows.old folder left after an in-place upgrade (often 10-30 GB)."""
    result = ScanResult()
    item = _make_item(r"C:\Windows.old", safety="safe", min_age_days=min_age_days)
    if item:
        result.items.append(item)
        result.total_size = item.size
    return result
```

with:

```python
def scan_windows_old(min_age_days: int = 0) -> ScanResult:
    """Windows.old folder left after an in-place upgrade (often 10-30 GB).

    safety="caution", not "safe": Microsoft does not support deleting
    this manually (it shares hardlinked files with the live OS); the
    documented removal path is Disk Cleanup / Storage Sense. Reported
    and selectable here, but QuickCleanupTab's dashboard-level "Clean
    All Safe" only sweeps safety=="safe" items, so this is never
    silently deleted by that one button anymore.
    """
    result = ScanResult()
    system_drive = os.environ.get("SystemDrive", "C:")
    path = os.path.join(system_drive + "\\", "Windows.old")
    item = _make_item(path, safety="caution", min_age_days=min_age_days)
    if item:
        result.items.append(item)
        result.total_size = item.size
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cleanup_windows_old_fix.py -v`
Expected: PASS, both tests.

- [ ] **Step 5: Run the broader cleanup suite to check for regressions**

Run: `pytest tests/test_cleanup_catalog.py tests/test_cleanup_no_servicing_lock.py -k "windows_old or large" -v` and also the full `tests/test_cleanup_catalog.py` (no `-k`, since it asserts every scanner is reachable and safety-leveled — a changed safety string must not break its own structural assertions):

Run: `pytest tests/test_cleanup_catalog.py -v`
Expected: PASS, unchanged (the catalog test asserts every scanner HAS a safety level from `{"safe","caution","danger"}`, not a SPECIFIC one for `scan_windows_old`).

- [ ] **Step 6: Commit**

```bash
git add src/modules/cleanup/cleanup_scanner/scanners_system.py tests/test_cleanup_windows_old_fix.py
git commit -m "fix(cleanup): scan_windows_old follows SystemDrive and is no longer auto-swept

Hardcoded C:\\Windows.old was invisible on a non-C: Windows install,
violating this codebase's own catalog convention (never a drive
letter). safety=\"safe\" meant QuickCleanupTab's dashboard-level Clean
All Safe button would delete it with zero confirmation -- more
confident than Microsoft's own guidance, which treats manual deletion
as unsupported (Windows.old shares hardlinked files with the live OS).
Now safety=\"caution\": still reported and selectable, never swept by
that one button."
```

---

## Task 2: New one-click action — Right-size Hibernation File

**Files:**
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Test: `tests/test_quick_cleanup_new_actions.py` (new — shared with Task 3)

**Interfaces:**
- Consumes: `self._run_action_command` (existing, `action_id, cmd, status_prefix, need_confirm=False, long_running=False, confirm_text=""`), `self._action_buttons`/`self._action_status` (existing dicts, populated by `_build_one_click_panel`).
- Produces: a 13th one-click action, id `"resize_hibernation"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_quick_cleanup_new_actions.py`:

```python
"""New one-click actions added in the cleanup-new-targets sub-project:
hibernation right-sizing and clearing stuck print jobs. Same busy-guard/
status pattern as every other one-click action -- see
tests/test_quick_cleanup_one_click_actions.py for the pattern this
follows.
"""
import time

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QMessageBox


def _settle(qapp, timeout_ms: int = 5_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_resize_hibernation_is_wired_into_the_action_panel(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    assert "resize_hibernation" in tab._action_buttons
    assert "resize_hibernation" in tab._action_status


def test_resize_hibernation_reports_when_hibernation_is_off(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    class _Off:
        returncode = 0
        stdout = "Hibernation has not been enabled."
        stderr = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Off())
    tab._resize_hibernation()

    assert "off" in tab._action_status["resize_hibernation"].text().lower()


def test_resize_hibernation_runs_powercfg_when_enabled_and_confirmed(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []

    class _On:
        returncode = 0
        stdout = "Hibernate is enabled."
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _On()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)

    tab._resize_hibernation()
    _settle(qapp)

    assert any("powercfg" in c and "/hibernate" in c and "/size 50" in c for c in calls if isinstance(c, str))


def test_resize_hibernation_does_nothing_when_confirm_declined(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []

    class _On:
        returncode = 0
        stdout = "Hibernate is enabled."
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _On()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    tab._resize_hibernation()
    _settle(qapp)

    # The FIRST call (the "powercfg /a" status check) is expected; a
    # SECOND call containing "/hibernate /size" must not happen.
    assert not any("/hibernate" in c and "/size" in c for c in calls if isinstance(c, str))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_quick_cleanup_new_actions.py -v`
Expected: FAIL — `AttributeError: 'QuickCleanupTab' object has no attribute '_resize_hibernation'`.

- [ ] **Step 3: Add the action to `_build_one_click_panel`'s list**

In `src/modules/cleanup/components/quick_cleanup_tab.py`, in `_build_one_click_panel` (line 987), add a new entry to the `actions` list (position doesn't matter functionally; append at the end to minimize diff noise):

```python
        actions = [
            ("flush_dns", "Flush DNS", self._flush_dns),
            ("clear_event_logs", "Clear Event Logs", self._clear_event_logs),
            ("compact_winsxs", "Compact WinSxS", self._compact_winsxs),
            ("rebuild_icon_cache", "Rebuild Icons", self._rebuild_icon_cache),
            ("wu_deep_clean", "WU Deep Clean", self._wu_deep_clean),
            ("network_repair", "Network Repair", self._network_repair),
            ("clear_thumbnails", "Clear Thumbnails", self._clear_thumbnails),
            ("clear_clipboard", "Clear Clipboard", self._clear_clipboard),
            ("reset_search", "Reset Search", self._reset_search),
            ("clear_font_cache", "Clear Font Cache", self._clear_font_cache),
            ("flush_wu_store", "Flush WinUpdate", self._flush_wu_store),
            ("reset_tcpip", "Reset TCP/IP", self._reset_tcpip),
            ("resize_hibernation", "Right-size Hibernation", self._resize_hibernation),
        ]
```

- [ ] **Step 4: Add the `_resize_hibernation` method**

Add near the other one-click methods (e.g. right after `_reset_tcpip`):

```python
    def _resize_hibernation(self):
        # Gate on hibernation actually being enabled -- powercfg refuses
        # /hibernate /size on a machine where it's off, and the raw
        # error text is not obviously "hibernation is off" to a fresh
        # user reading a one-line status label.
        check = subprocess.run(
            "powercfg /a", capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=True,
            creationflags=CREATE_NO_WINDOW, timeout=10)
        if "has not been enabled" in (check.stdout or "").lower():
            self._action_status["resize_hibernation"].setText(
                "Hibernation is off on this machine — nothing to resize")
            return

        mb = QMessageBox(self)
        mb.setWindowTitle("Right-size Hibernation File")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText(
            "This shrinks hiberfil.sys to 50% of RAM (Windows' own "
            "default since Windows 10) without disabling hibernation. "
            "Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return

        self._run_action_command(
            "resize_hibernation", "powercfg /hibernate /size 50",
            "Hibernation file resized", need_confirm=False)
```

Note: the pre-check uses a direct `subprocess.run("powercfg /a", ...)` call rather than going through `_run_action_command`, because it needs its result INSPECTED synchronously (to decide whether to even show the confirm dialog) — `_run_action_command` is fire-and-forget-to-a-worker by design and has no return-value hook. This mirrors how `_compact_winsxs` also runs its own confirm dialog before ever calling `_run_action_command` for the actual work.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_quick_cleanup_new_actions.py -v -k resize_hibernation`
Expected: PASS, all 4 `resize_hibernation` tests.

- [ ] **Step 6: Commit**

```bash
git add src/modules/cleanup/components/quick_cleanup_tab.py tests/test_quick_cleanup_new_actions.py
git commit -m "feat(cleanup): add Right-size Hibernation File one-click action

powercfg /hibernate /size 50 -- Windows' own reduced-size default since
Windows 10 -- shrinks hiberfil.sys without disabling hibernation
(different from Tweaks' existing power.json entry, which turns
hibernation off entirely). Gated on hibernation actually being enabled
first, since powercfg's own refusal message isn't obviously that to a
fresh user."
```

---

## Task 3: New one-click action — Clear Stuck Print Jobs

**Files:**
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Test: `tests/test_quick_cleanup_new_actions.py` (same file as Task 2 — add to it)

**Interfaces:**
- Same as Task 2 — a 14th one-click action, id `"clear_print_queue"`.

- [ ] **Step 1: Add tests to `tests/test_quick_cleanup_new_actions.py`**

Append to the same file created in Task 2:

```python
def test_clear_print_queue_is_wired_into_the_action_panel(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    assert "clear_print_queue" in tab._action_buttons
    assert "clear_print_queue" in tab._action_status


def test_clear_print_queue_runs_the_stop_clear_restart_sequence(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []

    class _R:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)

    tab._clear_print_queue()
    _settle(qapp)

    assert len(calls) == 1
    cmd = calls[0]
    assert "net stop spooler" in cmd
    assert "spool\\PRINTERS" in cmd or "spool\\\\PRINTERS" in cmd
    assert "net start spooler" in cmd


def test_clear_print_queue_does_nothing_when_confirm_declined(qapp, monkeypatch):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    calls = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    tab._clear_print_queue()

    assert calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_quick_cleanup_new_actions.py -v -k print_queue`
Expected: FAIL — `AttributeError: 'QuickCleanupTab' object has no attribute '_clear_print_queue'`.

- [ ] **Step 3: Add the action to `_build_one_click_panel`'s list**

In the same `actions` list Task 2 already extended, add one more entry:

```python
            ("resize_hibernation", "Right-size Hibernation", self._resize_hibernation),
            ("clear_print_queue", "Clear Stuck Print Jobs", self._clear_print_queue),
```

- [ ] **Step 4: Add the `_clear_print_queue` method**

Add near `_resize_hibernation`:

```python
    def _clear_print_queue(self):
        mb = QMessageBox(self)
        mb.setWindowTitle("Clear Stuck Print Jobs")
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setText(
            "This will <b>stop the Print Spooler</b>, clear all queued "
            "print jobs, and restart it. Any job currently printing or "
            "queued will be lost. Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return
        cmd = (
            "net stop spooler && "
            "del /q /f %SystemRoot%\\System32\\spool\\PRINTERS\\* 2>nul && "
            "net start spooler"
        )
        self._run_action_command("clear_print_queue", cmd, "Print queue cleared", need_confirm=False)
```

- [ ] **Step 5: Run the full new-actions test file**

Run: `pytest tests/test_quick_cleanup_new_actions.py -v`
Expected: PASS, all 8 tests (4 from Task 2, 4 from this task).

- [ ] **Step 6: Run the broader one-click-actions suite to check for regressions**

Run: `pytest tests/test_quick_cleanup_one_click_actions.py -v`
Expected: PASS, unchanged — this task only adds new dict entries and new methods, doesn't touch `_run_action_command` or any existing action.

- [ ] **Step 7: Commit**

```bash
git add src/modules/cleanup/components/quick_cleanup_tab.py tests/test_quick_cleanup_new_actions.py
git commit -m "feat(cleanup): add Clear Stuck Print Jobs one-click action

scan_print_spooler already existed but only measured/cleaned the queue
when the spooler service was ALREADY stopped -- there was no active
'clear stuck jobs' action. This adds one, using the exact stop-service/
clear-folder/restart-service shape _flush_wu_store already established
for Windows Update."
```

---

## Task 4: New scanner — Orphaned User Profiles

**Files:**
- Create: `src/modules/cleanup/cleanup_scanner/scanners_profiles.py`
- Modify: `src/modules/cleanup/cleanup_scanner/__init__.py` (add the star-import)
- Modify: `src/modules/cleanup/cleanup_module.py` (wire into `LARGE_EXTRA`)
- Test: `tests/test_cleanup_scanners_profiles.py` (new)

**Interfaces:**
- Produces: `cleanup_scanner.scan_orphaned_user_profiles(min_age_days=0) -> ScanResult`, reachable as `cs.scan_orphaned_user_profiles` everywhere else in the codebase already reaches scanners (via the package's existing star-import + `globals().update(all_scanners())` mechanism — this is a hand-written scanner, so only the star-import matters here, not the catalog).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cleanup_scanners_profiles.py`:

```python
"""Orphaned user profile detection: a folder under %SystemDrive%\\Users
with no matching entry in HKLM\\SOFTWARE\\Microsoft\\Windows NT\\
CurrentVersion\\ProfileList is one Windows itself no longer considers a
real account. Highest-consequence thing this module can point at -- a
home folder, not a cache -- so safety="danger" is load-bearing, not a
formality: _do_clean_all_safe only auto-selects safety=="safe" items.
"""
import os
import tempfile

import pytest

from modules.cleanup.cleanup_scanner import scanners_profiles as sp


class _FakeKey:
    def __init__(self, subkeys):
        self._subkeys = subkeys  # {sid_name: profile_image_path}
        self._names = list(subkeys.keys())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_registry(monkeypatch, sid_to_path: dict, *, refuse: bool = False):
    """Simulate HKLM\\...\\ProfileList with the given {sid: path} entries,
    or a total refusal (OSError) if refuse=True."""
    root = _FakeKey(sid_to_path)

    def fake_open_key(hive, path, *a, **k):
        if refuse:
            raise OSError("Access is denied")
        if path == sp._PROFILE_LIST_KEY:
            return root
        # Opening a per-SID subkey: return a fake key that answers
        # QueryValueEx for ProfileImagePath.
        sid = path.rsplit("\\", 1)[-1]
        return _FakeKey({"ProfileImagePath": sid_to_path[sid]})

    def fake_enum_key(key, index):
        names = key._names if key is root else []
        if index >= len(names):
            raise OSError("no more items")
        return names[index]

    def fake_query_value_ex(key, name):
        return (key._subkeys[name], 1)

    monkeypatch.setattr(sp.winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(sp.winreg, "EnumKey", fake_enum_key)
    monkeypatch.setattr(sp.winreg, "QueryValueEx", fake_query_value_ex)


@pytest.fixture
def users_dir(monkeypatch):
    tmp = tempfile.mkdtemp()
    users = os.path.join(tmp, "Users")
    os.makedirs(users)
    monkeypatch.setenv("SystemDrive", tmp)
    return users


def _mkprofile(users_dir: str, name: str) -> str:
    path = os.path.join(users_dir, name)
    os.makedirs(path)
    with open(os.path.join(path, "ntuser.dat"), "w") as f:
        f.write("x" * 500)
    return path


def test_a_profile_matching_profilelist_is_not_flagged(users_dir, monkeypatch):
    real = _mkprofile(users_dir, "alice")
    _patch_registry(monkeypatch, {"S-1-5-21-1": real})

    result = sp.scan_orphaned_user_profiles()

    assert result.items == []


def test_a_profile_with_no_profilelist_entry_is_flagged_danger(users_dir, monkeypatch):
    orphan = _mkprofile(users_dir, "ghost")
    _patch_registry(monkeypatch, {})  # ProfileList knows nothing

    result = sp.scan_orphaned_user_profiles()

    assert len(result.items) == 1
    assert os.path.normcase(result.items[0].path) == os.path.normcase(orphan)
    assert result.items[0].safety == "danger"


def test_public_and_default_are_never_flagged_regardless_of_profilelist(users_dir, monkeypatch):
    _mkprofile(users_dir, "Public")
    _mkprofile(users_dir, "Default")
    _patch_registry(monkeypatch, {})  # nothing known -- would flag both if not excluded

    result = sp.scan_orphaned_user_profiles()

    assert result.items == []


def test_a_refused_registry_read_reports_nothing_not_everything(users_dir, monkeypatch):
    _mkprofile(users_dir, "alice")
    _mkprofile(users_dir, "ghost")
    _patch_registry(monkeypatch, {}, refuse=True)

    result = sp.scan_orphaned_user_profiles()

    assert result.items == [], (
        "a refused registry read must report nothing, never flag every "
        "profile as orphaned")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cleanup_scanners_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.cleanup.cleanup_scanner.scanners_profiles'`.

- [ ] **Step 3: Create `scanners_profiles.py`**

Create `src/modules/cleanup/cleanup_scanner/scanners_profiles.py`:

```python
"""Orphaned Windows user profile detection.

A profile folder under %SystemDrive%\\Users with no matching entry in
HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList is one
Windows itself no longer considers a real account -- left behind by an
incompletely-removed domain/AAD account, a profile-creation failure that
Windows abandons under a .bak-suffixed SID key, or manual account
deletion that didn't clean up the folder.

This is the single highest-consequence thing this module can point at: a
home folder, not a cache. safety='danger' is not a formality here --
_do_clean_all_safe only auto-selects 'safe' items, so this can never be
swept by a bulk action, only deleted one folder at a time with the
explicit checkbox ticked.
"""
import logging
import os
import winreg

from modules.cleanup.cleanup_scanner._common import ScanResult, _make_item

logger = logging.getLogger(__name__)

_PROFILE_LIST_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"

#: Folders Windows itself creates under %SystemDrive%\Users that are
#: never real user profiles -- never flag these as orphaned regardless
#: of ProfileList's contents.
_NEVER_ORPHAN = {"Public", "Default", "Default User", "All Users",
                 "desktop.ini"}

__all__ = ['scan_orphaned_user_profiles']


def _known_profile_paths():
    """Every ProfileImagePath ProfileList currently knows about, lower-
    cased for a case-insensitive match against real folder names.
    Returns None if the read was refused -- distinct from an empty set,
    which means "checked, found none"."""
    known = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PROFILE_LIST_KEY) as root:
            index = 0
            while True:
                try:
                    sid_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, _PROFILE_LIST_KEY + "\\" + sid_name) as sid_key:
                        path, _ = winreg.QueryValueEx(sid_key, "ProfileImagePath")
                        known.add(os.path.normcase(os.path.normpath(path)))
                except OSError:
                    continue
    except OSError as e:
        logger.warning("Could not read %s: %s", _PROFILE_LIST_KEY, e)
        return None
    return known


def scan_orphaned_user_profiles(min_age_days: int = 0) -> ScanResult:
    result = ScanResult()
    system_drive = os.environ.get("SystemDrive", "C:")
    users_dir = os.path.join(system_drive + "\\", "Users")
    if not os.path.isdir(users_dir):
        return result

    known = _known_profile_paths()
    if known is None:
        return result  # refused read -- report nothing, not everything

    for name in os.listdir(users_dir):
        if name in _NEVER_ORPHAN:
            continue
        full = os.path.join(users_dir, name)
        if not os.path.isdir(full):
            continue
        if os.path.normcase(os.path.normpath(full)) in known:
            continue
        item = _make_item(full, safety="danger", min_age_days=min_age_days)
        if item:
            result.items.append(item)
            result.total_size += item.size
    return result
```

Note the second `winreg.OpenKey` call passes `_PROFILE_LIST_KEY + "\\" + sid_name` as a full path from `HKEY_LOCAL_MACHINE`, not `root` + relative name — `winreg.OpenKey(root, sid_name)` (opening a relative subkey off an already-open key handle) also works and is more idiomatic; either is fine as long as the test's `_FakeKey`/`fake_open_key` fixture (Step 1) matches whichever form is used. **Match this to whatever the test fixture expects** — re-check `_patch_registry`'s `fake_open_key` signature in the test file before finalizing this exact call shape, since the fake's `path` parameter handling depends on which form you pick.

- [ ] **Step 4: Add the star-import to the package**

In `src/modules/cleanup/cleanup_scanner/__init__.py`, add one line among the existing 8 submodule star-imports (alphabetical position, after `scanners_media`, before `scanners_system`):

```python
from modules.cleanup.cleanup_scanner.scanners_profiles import *  # noqa: F401,F403
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_cleanup_scanners_profiles.py -v`
Expected: PASS, all 4 tests. If Step 3's registry-call-shape note above caused a mismatch with the test fixture, fix whichever side is wrong (prefer adjusting the production code's `winreg.OpenKey` call to the more idiomatic relative-subkey form, `winreg.OpenKey(root, sid_name)`, and updating `_patch_registry`'s `fake_open_key` to match, rather than contorting the test around an awkward absolute-path production call).

- [ ] **Step 6: Wire into `LARGE_EXTRA`**

In `src/modules/cleanup/cleanup_module.py`, `LARGE_EXTRA` dict, add one entry (order doesn't matter — dict, not a list):

```python
LARGE_EXTRA = {
    # ... existing entries unchanged ...
    cs.scan_orphaned_user_profiles: (
        'Orphaned User Profiles (no matching Windows account)', 'danger'),
}
```

- [ ] **Step 7: Run the full cleanup catalog/structural test suite**

Run: `pytest tests/test_cleanup_catalog.py tests/test_module_smoke.py -v -k "Cleanup or cleanup"`
Expected: PASS — the catalog test's structural assertions (every scanner has a safety level, appears in some tab, etc.) must still hold with the new scanner added; `test_module_smoke.py` proves `CleanupModule` still constructs cleanly with the new `LARGE_EXTRA` entry.

- [ ] **Step 8: Manual sanity check — real machine, unelevated**

This module needs no elevation to read `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList` (it's world-readable). Run:

```bash
"C:/Users/iorda/Windows_client_tool/.venv/Scripts/python.exe" -c "
import sys; sys.path.insert(0, 'src')
from modules.cleanup.cleanup_scanner.scanners_profiles import scan_orphaned_user_profiles
r = scan_orphaned_user_profiles()
print(f'{len(r.items)} orphaned profile(s) found, {r.total_size} bytes total')
for i in r.items:
    print(' ', i.path, i.size, i.safety)
"
```

Expected: runs without error; on THIS real machine, report whatever it finds (likely 0 — this is a personal single-user machine, not expected to have leftover domain/AAD profile folders) as a genuine real-machine verification, not a guess. If it reports something unexpected (e.g. a real folder the user recognizes as legitimate), that's a real finding to flag, not something to silently adjust the scanner to hide.

- [ ] **Step 9: Commit**

```bash
git add src/modules/cleanup/cleanup_scanner/scanners_profiles.py src/modules/cleanup/cleanup_scanner/__init__.py src/modules/cleanup/cleanup_module.py tests/test_cleanup_scanners_profiles.py
git commit -m "feat(cleanup): detect orphaned user profile folders

A folder under %SystemDrive%\\Users with no matching entry in
ProfileList is one Windows itself no longer considers a real account.
safety=\"danger\" -- the highest-consequence thing this module can point
at -- so it can never be swept by any bulk 'Clean All Safe' action, only
deleted one folder at a time with the checkbox explicitly ticked. A
refused registry read reports nothing found, never \"everything is
orphaned\"."
```

---

## Self-Review

**Spec coverage:** all 4 buildable items from the spec (§1's table) have a task: Windows.old fix (Task 1), hibernation right-sizing (Task 2), print queue clearing (Task 3), orphaned profiles (Task 4). The 3 deferred/rejected items (NGEN, Recycle Bin audit, OneDrive pin audit) correctly have NO task — confirmed intentional, not an oversight, per spec §1's table and §6 non-goals.

**Placeholder scan:** Task 4 Step 3 contains one explicit, bounded ambiguity (the `winreg.OpenKey` relative-vs-absolute subkey path form) with concrete resolution instructions rather than a bare "TBD" — flagged because the exact right answer depends on matching test-fixture shape decided in the same task, not left unresolved across task boundaries.

**Type consistency:** `_resize_hibernation`/`_clear_print_queue`'s action ids (`"resize_hibernation"`, `"clear_print_queue"`) are used identically in the `actions` list (Tasks 2/3 Step 3) and their own test files. `scan_orphaned_user_profiles`'s signature (`min_age_days: int = 0`) matches every other scanner in this codebase.
