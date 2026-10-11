# Cleanup — Stability, Truthful Results and Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Cleanup run do what it says: delete safely (never a running installer's files, never a sign-in, never through a link), report what was REALLY freed and what was left and why, and scan faster — then show that to the person in a History tab and a drive summary bar.

**Architecture:** One new Qt-free deletion engine (`cleanup_scanner/deleter.py`) replaces `shutil.rmtree(ignore_errors=True)` inside `delete_items`, and returns a `DeleteReport` with measured bytes. `delete_items` keeps its `(deleted, errors)` signature for its three callers and writes the report into the existing deletion log. A Restart Manager helper names the processes holding locked files. The UI reads the deletion log; it never re-derives numbers.

**Tech Stack:** Python 3.12, PyQt6, ctypes (Restart Manager `rstrtmgr.dll`), pytest. Windows 10/11 x64 only.

**Spec:** No separate spec. The requirements are the measurements below, taken on this machine on 2026-10-11 with `cleanup_timing.py` (read-only) and the deletion log `%APPDATA%\WindowsTweaker\cleanup_deleted.json`.

### Background — what was measured (2026-10-11)

| # | Finding | Evidence |
|---|---|---|
| F1 | A clean CLAIMS bytes it never freed. `delete_items` runs `shutil.rmtree(item.path, ignore_errors=True)` and then counts the item's whole scanned size as removed. Files in use (Teams, WhatsApp, an MSI mid-install) survive silently and are still reported freed. | `scanners_system.py` `_do_delete`: `rmtree(..., ignore_errors=True)`, then `deleted += 1; removed.append((item.path, item.size))` |
| F2 | `%TEMP%` (2.29 GB here) is effectively never cleaned. It is offered as ONE item; the Outlook/sign-in guard walks it, meets 25 unreadable subfolders, fails closed and refuses the whole 2.3 GB. | `held_user_data` returns `(True, True)` on any unreadable subfolder; sweep showed `%TEMP%` refused |
| F3 | Deleting a directory item removes the ROOT too (`rmtree`). For `%TEMP%`, `C:\Windows\Temp`, `INetCache` this deletes a folder Windows and apps expect to exist and whose ACL Windows set; recreating it gets default ACLs. | `rmtree(item.path)` |
| F4 | Nothing protects files an installer is using right now. MSI/Burn/Squirrel extract to `%TEMP%` and read back minutes later; Windows' own Disk Cleanup and Storage Sense only take Temp files older than 7 days / 1 day for exactly this reason. | Windows Disk Cleanup "Temporary files" rule; Storage Sense temp rule |
| F5 | `user_crash_dumps` targets the whole `%LOCALAPPDATA%\Temp` (that IS `%TEMP%`) as "caution", so Temp is offered twice under two safety levels. `dedupe_items` keeps whichever came first. | `definitions/system.json` `user_crash_dumps.paths` |
| F6 | Sizes are LOGICAL bytes via `os.walk` + `os.path.getsize` (one extra `stat` per file). OneDrive placeholders (Files On-Demand) count their full cloud size though they use no disk; walking follows nothing but costs two syscalls per file. | `_common.get_dir_size` |
| F7 | The 200-entry deletion log exists and is never shown; a person cannot see what was deleted, refused or left. | `cleanup_history.recent_deleted()` has no UI caller |
| F8 | Full sweep is 5.9 s for 125 present scanners (OK); the slowest are `update_cleanup` 1.23 s, `winSxS_temp` 1.08 s, `temp_files` 0.95 s. Performance work is the size walk (F6) and the delete, not scanner orchestration. | `cleanup_timing.py` output |

## Global Constraints

- Windows 10/11 x64, Python 3.12+ (`os.DirEntry.is_junction()` is 3.12+ — used).
- No PyQt6 import in `src/modules/cleanup/cleanup_scanner/` (CLAUDE.md: Qt-free engines, testable headless).
- `delete_items(items, on_progress=None, stop_wuauserv=False) -> Tuple[int, int]` keeps its signature: callers are `clean_safe_runner.py:91`, `components/category_group.py:303`, `modules/updates/stage_runners.py:149`.
- Every refusal guard in `_refusal_reason` (app folder, protected system dirs, sign-in stores, Outlook, sign-in markers) stays and runs BEFORE any byte is deleted.
- A refusal or an unreadable path is never reported as success, and never silently dropped: log at WARNING, record it in the deletion log (CLAUDE.md: "a refusal is not an answer"; "Silent exception swallowing is forbidden").
- English-only UI strings, no `tr()` (CLAUDE.md "This application is English-only").
- Colours come from the theme palette (`ui.theme_colors` / existing semantic colours) — the repo's ratchet tests forbid frozen colours and inline stylesheets.
- Functions stay under the repo's length ratchet (`tests/test_function_lengths.py`: none new over 120 lines).
- Run pytest with `-p no:cacheprovider` and the default basetemp (memory: custom `--basetemp` is never cleaned).
- After the last task: build the portable exe and deploy it to `C:/Users/iorda/OneDrive/1 Personal/Aplicații/` (CLAUDE.md hard requirement), then push.

## Review Focus

1. **A file in use** (Teams running, an installer extracting into `%TEMP%`) — expected: left alone, counted under "in use", named by process, never counted as freed. → Task 1 test `test_a_locked_file_is_reported_not_freed`, Task 4 test.
2. **A junction or symlink inside a cache** (`npm-cache` → another drive, a dev's `node_modules` link) — expected: the LINK is removed, the target's files are untouched and not counted. → Task 1 test `test_a_junction_is_unlinked_never_followed`.
3. **A path longer than 260 characters** (deep `node_modules`, Electron caches) — expected: deleted like any other. → Task 1 test `test_a_path_over_260_chars_is_deleted`.
4. **A read-only file** (`attrib +r`, common in extracted installers and git objects) — expected: deleted. → Task 1 test `test_a_read_only_file_is_deleted`.
5. **An unreadable subfolder inside an otherwise deletable item** (`%TEMP%` with another account's folder) — expected: that subfolder is skipped and listed; the rest of the item is cleaned. → Task 2 test `test_an_unreadable_subfolder_is_skipped_not_the_whole_item`.

---

## File Structure

| File | New? | Responsibility |
|---|---|---|
| `src/modules/cleanup/cleanup_scanner/deleter.py` | new | Qt-free deletion engine: `DeleteReport`, `delete_path()`. Contents-only by default, age floor, link-safe, long-path, read-only, measured bytes, cancellable. |
| `src/modules/cleanup/cleanup_scanner/lock_owners.py` | new | Restart Manager: which processes hold a list of files. Qt-free, ctypes. |
| `src/modules/cleanup/cleanup_scanner/_common.py` | modify | `ScanItem` gains `keep_root` and `keep_newer_than_hours`; `get_dir_size` uses `os.scandir` and skips cloud placeholders and links. |
| `src/modules/cleanup/cleanup_scanner/scanners_system.py` | modify | `delete_items` uses `deleter.delete_path`; `held_user_data` no longer treats an unreadable subfolder as "refuse the whole item"; scanners that remove a whole folder set `keep_root=False`. |
| `src/modules/cleanup/cleanup_scanner/catalog.py` | modify | `ScannerSpec` gains `keep_newer_than_hours` (JSON key), passed to items. |
| `src/modules/cleanup/cleanup_scanner/definitions/system.json` | modify | `temp_files` gets `"keep_newer_than_hours": 24`; `user_crash_dumps` loses `%LOCALAPPDATA%\Temp`. |
| `src/modules/cleanup/cleanup_history.py` | modify | `record_report()` stores measured bytes, in-use files with owners, skipped and refused; `summarise()` for the UI. |
| `src/modules/cleanup/tabs/_history_tab.py` | new | Qt: History tab over the deletion log. |
| `src/modules/cleanup/components/drive_summary_bar.py` | new | Qt: per-fixed-drive free space + last clean, above the tabs. |
| `src/modules/cleanup/cleanup_module.py` | modify | Adds the History tab and the summary bar. |
| `pyinstaller_common.py` | modify | `HIDDEN_IMPORTS` += the four new modules. |
| `CLAUDE.md` | modify | Cleanup Module rules for the deleter. |
| tests | new | `tests/test_cleanup_deleter.py`, `tests/test_cleanup_lock_owners.py`, `tests/test_cleanup_history_report.py`, `tests/test_cleanup_history_tab.py`. |

---

### Task 1: The deletion engine — measured, link-safe, long-path, contents-only

**Files:**
- Create: `src/modules/cleanup/cleanup_scanner/deleter.py`
- Test: `tests/test_cleanup_deleter.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class DeleteReport` (dataclass): `freed: int`, `files: int`, `locked: List[str]`, `denied: List[str]`, `kept_recent: int`, `cancelled: bool`; method `merge(other: DeleteReport) -> None`.
  - `delete_path(path: str, is_dir: bool, *, keep_root: bool = True, keep_newer_than_s: float = 0, cancelled: Callable[[], bool] = lambda: False) -> DeleteReport`
  - `long_path(path: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cleanup_deleter.py
import os
import stat
import subprocess
import time

import pytest

from modules.cleanup.cleanup_scanner import deleter as d


def _file(path, size=10, age_s=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def test_contents_are_deleted_and_the_root_is_kept(tmp_path):
    _file(tmp_path / "a.tmp", 100)
    _file(tmp_path / "sub" / "b.tmp", 50)
    report = d.delete_path(str(tmp_path), True)
    assert tmp_path.exists() and list(tmp_path.iterdir()) == []
    assert (report.freed, report.files) == (150, 2)


def test_keep_root_false_removes_the_folder_itself(tmp_path):
    target = tmp_path / "app-1.2.3"
    _file(target / "x.dll", 7)
    report = d.delete_path(str(target), True, keep_root=False)
    assert not target.exists() and report.freed == 7


def test_a_locked_file_is_reported_not_freed(tmp_path):
    locked = _file(tmp_path / "in-use.log", 30)
    _file(tmp_path / "free.log", 20)
    # CRT open() shares read/write but not DELETE, so this handle blocks os.remove
    # with ERROR_SHARING_VIOLATION -- exactly what a running app's open log does.
    fd = os.open(str(locked), os.O_RDONLY)
    try:
        report = d.delete_path(str(tmp_path), True)
    finally:
        os.close(fd)
    assert report.freed == 20 and report.files == 1
    assert report.locked == [str(locked)]
    assert locked.exists()


def test_a_junction_is_unlinked_never_followed(tmp_path):
    outside = tmp_path / "outside"
    keep = _file(outside / "precious.txt", 99)
    cache = tmp_path / "cache"
    cache.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(cache / "link"), str(outside)],
                   check=True, capture_output=True)
    report = d.delete_path(str(cache), True)
    assert keep.exists()                          # the target is untouched
    assert not (cache / "link").exists()          # the junction itself is gone
    assert report.freed == 0


def test_a_path_over_260_chars_is_deleted(tmp_path):
    deep = tmp_path
    while len(str(deep)) < 300:
        deep = deep / ("d" * 40)
    os.makedirs(d.long_path(str(deep)))
    with open(d.long_path(str(deep / "f.bin")), "wb") as f:
        f.write(b"y" * 5)
    report = d.delete_path(str(tmp_path), True)
    assert report.freed == 5 and list(tmp_path.iterdir()) == []


def test_a_read_only_file_is_deleted(tmp_path):
    ro = _file(tmp_path / "ro.bin", 8)
    os.chmod(ro, stat.S_IREAD)
    report = d.delete_path(str(tmp_path), True)
    assert not ro.exists() and report.freed == 8


def test_files_newer_than_the_floor_are_kept(tmp_path):
    _file(tmp_path / "old.tmp", 10, age_s=3 * 86400)
    fresh = _file(tmp_path / "msi-extract" / "fresh.cab", 40)
    report = d.delete_path(str(tmp_path), True, keep_newer_than_s=24 * 3600)
    assert fresh.exists() and report.freed == 10 and report.kept_recent == 1


def test_cancel_stops_between_files(tmp_path):
    for i in range(5):
        _file(tmp_path / f"{i}.tmp", 1)
    calls = []
    report = d.delete_path(str(tmp_path), True, cancelled=lambda: calls.append(1) or len(calls) > 2)
    assert report.cancelled and report.files < 5


def test_a_single_file_item(tmp_path):
    f = _file(tmp_path / "memory.dmp", 64)
    report = d.delete_path(str(f), False)
    assert not f.exists() and report.freed == 64


def test_a_missing_path_is_nothing_not_an_error(tmp_path):
    report = d.delete_path(str(tmp_path / "gone"), True)
    assert (report.freed, report.files, report.locked, report.denied) == (0, 0, [], [])
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_deleter.py -v`
Expected: FAIL — `ImportError: cannot import name 'deleter'`.

- [ ] **Step 3: Implement**

```python
# src/modules/cleanup/cleanup_scanner/deleter.py
r"""Delete what a ScanItem names -- and report what REALLY went.

`shutil.rmtree(ignore_errors=True)` was the old engine: it removed the
folder root along with the contents, followed nothing but reported
nothing, and every file it could not delete (in use, access denied) was
still counted as freed by the caller (2026-10-11). This walks the tree
itself so each outcome is measured:

* a directory item's CONTENTS go and the root stays (`keep_root`) --
  `%TEMP%`, `C:\Windows\Temp` and app cache roots are folders Windows and
  the apps expect to exist, with ACLs Windows set;
* a junction or symlink is unlinked and never followed: its target is
  someone else's data and is neither deleted nor counted;
* a file in use (ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION) is left
  and listed in `locked`; a refusal is listed in `denied`;
* read-only files are cleared and deleted; paths over MAX_PATH go through
  the `\\?\` prefix;
* files newer than `keep_newer_than_s` are kept -- an installer
  extracting into %TEMP% reads its files back minutes later.

Qt-free.
"""
import logging
import os
import stat
from dataclasses import dataclass, field
from typing import Callable, List

logger = logging.getLogger(__name__)

ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
_IN_USE = (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION)


@dataclass
class DeleteReport:
    freed: int = 0
    files: int = 0
    locked: List[str] = field(default_factory=list)
    denied: List[str] = field(default_factory=list)
    kept_recent: int = 0
    cancelled: bool = False

    def merge(self, other: "DeleteReport") -> None:
        self.freed += other.freed
        self.files += other.files
        self.locked.extend(other.locked)
        self.denied.extend(other.denied)
        self.kept_recent += other.kept_recent
        self.cancelled = self.cancelled or other.cancelled


def long_path(path: str) -> str:
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _short(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def _remove_file(path: str, size: int, report: DeleteReport) -> None:
    for attempt in (1, 2):
        try:
            os.remove(path)
            report.freed += size
            report.files += 1
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) in _IN_USE:
                report.locked.append(_short(path))
                return
            if attempt == 1:
                try:
                    os.chmod(path, stat.S_IWRITE)      # read-only attribute
                    continue
                except OSError:
                    logger.debug("cannot clear read-only on %s", path, exc_info=True)
            logger.warning("cannot delete %s: %s", _short(path), exc)
            report.denied.append(_short(path))
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) in _IN_USE:
                report.locked.append(_short(path))
            else:
                logger.warning("cannot delete %s: %s", _short(path), exc)
                report.denied.append(_short(path))
            return


def _unlink_link(entry: os.DirEntry, report: DeleteReport) -> None:
    try:
        if entry.is_junction() or entry.is_dir(follow_symlinks=False):
            os.rmdir(entry.path)                     # removes the link, not the target
        else:
            os.remove(entry.path)
    except OSError as exc:
        logger.warning("cannot remove link %s: %s", _short(entry.path), exc)
        report.denied.append(_short(entry.path))


def _clear(folder: str, floor: float, now: float, cancelled, report: DeleteReport) -> None:
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("cannot list %s: %s", _short(folder), exc)
        report.denied.append(_short(folder))
        return
    for entry in entries:
        if cancelled():
            report.cancelled = True
            return
        if entry.is_symlink() or entry.is_junction():
            _unlink_link(entry, report)
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            logger.warning("cannot read %s: %s", _short(entry.path), exc)
            report.denied.append(_short(entry.path))
            continue
        if entry.is_dir(follow_symlinks=False):
            _clear(entry.path, floor, now, cancelled, report)
            if report.cancelled:
                return
            try:
                os.rmdir(entry.path)                 # only succeeds once empty
            except OSError:
                pass  # not empty: something inside was kept, in use or denied -- already recorded
            continue
        if floor and now - st.st_mtime < floor:
            report.kept_recent += 1
            continue
        _remove_file(entry.path, st.st_size, report)


def delete_path(path: str, is_dir: bool, *, keep_root: bool = True,
                keep_newer_than_s: float = 0,
                cancelled: Callable[[], bool] = lambda: False) -> DeleteReport:
    import time
    report = DeleteReport()
    target = long_path(path)
    if not os.path.lexists(target):
        return report
    if not is_dir or os.path.islink(target):
        try:
            size = os.lstat(target).st_size
        except OSError:
            size = 0
        _remove_file(target, size, report)
        return report
    _clear(target, keep_newer_than_s, time.time(), cancelled, report)
    if not keep_root and not report.cancelled:
        try:
            os.rmdir(target)
        except OSError:
            pass  # not empty: what stayed is already in locked / denied / kept_recent
    return report
```

> Note on the two bare `pass` lines: they are not swallowed failures — `os.rmdir` on a non-empty folder is the expected outcome whenever something inside was kept, and every kept file is already recorded in the report. Keep the comment, or the silent-swallow ratchet test flags them; if it does flag them, replace `pass` with `logger.debug("left %s: not empty", ...)`.

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_deleter.py -v`
Expected: 10 passed. (The junction test needs `mklink /J`, which needs no elevation.)

- [ ] **Step 5: Run the ratchets**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_function_lengths.py tests/test_no_silent_swallow.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/modules/cleanup/cleanup_scanner/deleter.py tests/test_cleanup_deleter.py
git commit -m "feat(cleanup): a deletion engine that measures what it freed, keeps roots and never follows links"
```

---

### Task 2: `delete_items` uses the engine; Temp is cleanable again; installers are safe

**Files:**
- Modify: `src/modules/cleanup/cleanup_scanner/_common.py` (`ScanItem`)
- Modify: `src/modules/cleanup/cleanup_scanner/scanners_system.py` (`held_user_data`, `delete_items._do_delete`, `scan_superseded_app_versions`, `scan_empty_folders`, `scan_orphaned_installer_packages`)
- Modify: `src/modules/cleanup/cleanup_scanner/scanners_profiles.py` (`scan_orphaned_user_profiles`)
- Modify: `src/modules/cleanup/cleanup_scanner/catalog.py` (`ScannerSpec`, `run_spec`)
- Modify: `src/modules/cleanup/cleanup_scanner/definitions/system.json` (`temp_files`, `user_crash_dumps`)
- Test: `tests/test_cleanup_deleter.py` (append), `tests/test_cleanup_catalog_audit.py` (update one test)

**Interfaces:**
- Consumes: `deleter.delete_path`, `deleter.DeleteReport` (Task 1).
- Produces:
  - `ScanItem.keep_root: bool = True`, `ScanItem.keep_newer_than_hours: int = 0`
  - `ScannerSpec.keep_newer_than_hours: int` (JSON key `keep_newer_than_hours`, default 0)
  - `delete_items_report(items, on_progress=None, stop_wuauserv=False, cancelled=lambda: False) -> Tuple[DeleteReport, List[Tuple[str, str]]]` — the report and `(path, reason)` refusals. `delete_items` becomes a wrapper returning `(items_fully_cleaned, items_with_errors)`.

**Why `held_user_data` changes:** today an unreadable subfolder makes the guard refuse the WHOLE item (F2). With the new engine nothing is deleted that was not enumerated: an unreadable folder cannot be listed, so its contents cannot be deleted either, and it lands in `report.denied`. The guard therefore only needs to refuse on what it SAW (Outlook files, sign-in markers). The existing test `test_review_unreadable_outlook_tree_is_refused` pinned the old fail-closed rule and is updated to pin the new one: the item is cleaned, the unreadable part is untouched and listed.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cleanup_deleter.py`)

```python
from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss


@pytest.fixture
def no_system_guards(monkeypatch):
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])


def test_delete_items_counts_only_what_went(tmp_path, no_system_guards):
    _file(tmp_path / "a.tmp", 100, age_s=3 * 86400)
    held = _file(tmp_path / "held.tmp", 50, age_s=3 * 86400)
    fd = os.open(str(held), os.O_RDONLY)
    try:
        report, refused = ss.delete_items_report([ScanItem(str(tmp_path), 150, True)])
    finally:
        os.close(fd)
    assert report.freed == 100 and report.locked == [str(held)] and refused == []
    assert tmp_path.exists()


def test_an_unreadable_subfolder_is_skipped_not_the_whole_item(tmp_path, no_system_guards, monkeypatch):
    _file(tmp_path / "junk.tmp", 10, age_s=3 * 86400)
    real_scandir = os.scandir
    blocked = tmp_path / "other-account"
    _file(blocked / "x.tmp", 99, age_s=3 * 86400)

    def scandir(path):
        if os.path.normcase(d._short(os.fspath(path))) == os.path.normcase(str(blocked)):
            raise PermissionError(5, "Access is denied", str(blocked))
        return real_scandir(path)
    monkeypatch.setattr(d.os, "scandir", scandir)
    monkeypatch.setattr(ss.os, "walk", lambda p, onerror=None: iter([(p, [], ["junk.tmp"])]))
    report, refused = ss.delete_items_report([ScanItem(str(tmp_path), 109, True)])
    assert report.freed == 10 and str(blocked) in report.denied and refused == []
    assert (blocked / "x.tmp").exists()


def test_temp_keeps_files_younger_than_its_floor(tmp_path, no_system_guards):
    _file(tmp_path / "old.tmp", 10, age_s=3 * 86400)
    fresh = _file(tmp_path / "{GUID}" / "setup.msi", 40)
    item = ScanItem(str(tmp_path), 50, True, keep_newer_than_hours=24)
    report, _ = ss.delete_items_report([item])
    assert fresh.exists() and report.freed == 10 and report.kept_recent == 1


def test_a_superseded_version_folder_goes_entirely(tmp_path, no_system_guards):
    old = tmp_path / "app-1.0.0"
    _file(old / "app.exe", 5)
    report, _ = ss.delete_items_report([ScanItem(str(old), 5, True, keep_root=False)])
    assert not old.exists() and report.freed == 5


def test_the_catalog_temp_entry_carries_the_floor_and_crash_dumps_no_longer_target_temp():
    from modules.cleanup.cleanup_scanner import catalog
    specs = catalog.load_catalog()
    assert specs["temp_files"].keep_newer_than_hours == 24
    assert not any(p.lower().rstrip("\\").endswith(r"%localappdata%\temp")
                   for p in specs["user_crash_dumps"].paths)
```

And replace the body of `test_review_unreadable_outlook_tree_is_refused` in `tests/test_cleanup_catalog_audit.py` with the new rule (rename it):

```python
def test_review_an_unreadable_tree_is_not_refused_but_cannot_be_deleted(tmp_path, monkeypatch, caplog):
    from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss
    folder = tmp_path / "cache"
    folder.mkdir()
    def unreadable(path, onerror):
        onerror(PermissionError(13, "denied", str(folder)))
        return iter(())
    monkeypatch.setattr(ss.os, "walk", unreadable)
    assert ss.held_user_data(str(folder), True) == (False, False)
    assert str(folder) in caplog.text and caplog.records[-1].levelname == "WARNING"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_deleter.py tests/test_cleanup_catalog_audit.py -q`
Expected: FAIL — `delete_items_report` and `keep_newer_than_hours` do not exist.

- [ ] **Step 3: `ScanItem` fields** (`_common.py`)

```python
@dataclass
class ScanItem:
    path: str
    size: int        # bytes
    is_dir: bool
    selected: bool = True
    safety: str = "safe"   # "safe" | "caution" | "danger"
    #: A directory item's CONTENTS are deleted and the folder itself kept
    #: (Temp, cache roots). False only where the folder itself is the junk:
    #: a superseded version folder, an empty folder, an orphaned profile.
    keep_root: bool = True
    #: Files modified more recently than this are left alone -- an installer
    #: extracting into %TEMP% reads its files back minutes later.
    keep_newer_than_hours: int = 0
```

- [ ] **Step 4: Catalog carries the floor** (`catalog.py`)

In the `ScannerSpec` dataclass add `keep_newer_than_hours: int = 0`; where specs are built from JSON add `keep_newer_than_hours=int(raw.get("keep_newer_than_hours", 0))`; in `run_spec` after `item = _make_item(...)`:

```python
        if item:
            item.keep_newer_than_hours = spec.keep_newer_than_hours
            result.items.append(item)
```

In `definitions/system.json`, `temp_files` gains `"keep_newer_than_hours": 24,` and `user_crash_dumps.paths` becomes:

```json
      "paths": [
        "%LOCALAPPDATA%\\CrashDumps",
        "%TEMP%\\*.dmp"
      ],
```

- [ ] **Step 5: `held_user_data` stops failing closed on unreadable** (`scanners_system.py`)

Replace the last line `return outlook or unreadable, sign_in or unreadable` with `return outlook, sign_in`, keep the `onerror` WARNING, and change the docstring's first line to: `"""(holds Outlook data, holds sign-in data) -- what one walk SAW. An unreadable folder is logged; it cannot be deleted either (deleter.py lists it as denied)."""`. Also drop `or unreadable` from the `first_only` break condition.

- [ ] **Step 6: `delete_items_report` and the wrapper** (`scanners_system.py`, replace `_do_delete` inside `delete_items`)

```python
def delete_items_report(items: List[ScanItem],
                        on_progress: Optional[Callable[[int, int], None]] = None,
                        stop_wuauserv: bool = False,
                        cancelled: Callable[[], bool] = lambda: False):
    """Delete the selected items. Returns (DeleteReport, [(path, refusal reason)])."""
    from modules.cleanup.cleanup_scanner.deleter import DeleteReport, delete_path

    def _do_delete():
        report, refused = DeleteReport(), []
        selected = [i for i in items if i.selected]
        for idx, item in enumerate(selected):
            if on_progress:
                on_progress(idx + 1, len(selected))
            if cancelled():
                report.cancelled = True
                break
            if not os.path.lexists(item.path):
                continue
            reason = _refusal_reason(item)
            if reason:
                logger.warning("Refusing to delete %s: %s", item.path, reason)
                refused.append((item.path, reason))
                continue
            report.merge(delete_path(item.path, item.is_dir, keep_root=item.keep_root,
                                     keep_newer_than_s=item.keep_newer_than_hours * 3600,
                                     cancelled=cancelled))
        from modules.cleanup import cleanup_history
        cleanup_history.record_report(report, refused)
        from modules.cleanup.cleanup_scanner import scan_cache
        scan_cache.invalidate()
        return report, refused

    if stop_wuauserv:
        with _ServiceStopped("wuauserv"):
            return _do_delete()
    return _do_delete()


def delete_items(items: List[ScanItem],
                 on_progress: Optional[Callable[[int, int], None]] = None,
                 stop_wuauserv: bool = False) -> Tuple[int, int]:
    """Back-compatible: (items cleaned, items refused or left with errors)."""
    report, refused = delete_items_report(items, on_progress, stop_wuauserv)
    problems = len(refused) + (1 if report.locked or report.denied else 0)
    cleaned = max(0, sum(1 for i in items if i.selected) - problems)
    return cleaned, problems
```

Move `_ServiceStopped` from inside the old `delete_items` to module level unchanged (it is a nested class today), and keep the existing `scan_cache.invalidate()` call only once (if the old body already calls it, delete the old call). `record_report` is added in Task 3 — until then add a temporary forwarding function at the bottom of `cleanup_history.py` so this task's tests pass on their own:

```python
def record_report(report, refused) -> None:
    record_deleted([(f"{report.files} files", report.freed)] if report.files else [],
                   [path for path, _reason in refused])
```

- [ ] **Step 7: Scanners whose folder itself is the junk set `keep_root=False`**

In `scan_superseded_app_versions`, `scan_empty_folders`, `scan_orphaned_installer_packages` (`scanners_system.py`) and `scan_orphaned_user_profiles` (`scanners_profiles.py`), every `ScanItem(...)` / `_make_item(...)` result that is a directory gets `keep_root=False` — for `ScanItem(...)` add the keyword; for `_make_item(...)` set `item.keep_root = False` on the returned item before appending. Then confirm no other directory item expects the root gone:

Run: `git grep -n "ScanItem(" -- src/modules/cleanup src/modules/updates`
Expected: each remaining hit is a cache/temp folder (root kept is right) or a file item.

- [ ] **Step 8: Run the tests**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_deleter.py tests/test_cleanup_catalog_audit.py tests/test_cleanup_no_servicing_lock.py -q` then the whole cleanup set: `.venv/Scripts/python -m pytest -p no:cacheprovider -q -k "cleanup or quick_cleanup or stage_runner"`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add -A src/modules/cleanup tests/test_cleanup_deleter.py tests/test_cleanup_catalog_audit.py
git commit -m "fix(cleanup): delete_items reports measured bytes; %TEMP% cleanable again; installer files under 24h kept"
```

---

### Task 3: The deletion log records the truth — and the Run All report uses it

**Files:**
- Modify: `src/modules/cleanup/cleanup_history.py`
- Modify: `src/modules/updates/stage_runners.py:140-160` (`run_cleanup_safe_stage`)
- Test: `tests/test_cleanup_history_report.py`

**Interfaces:**
- Consumes: `DeleteReport` (Task 1), `delete_items_report` (Task 2).
- Produces:
  - `record_report(report: DeleteReport, refused: Sequence[Tuple[str, str]], owners: Optional[Dict[str, List[str]]] = None) -> None` — entry keys: `at`, `count`, `bytes`, `locked` (first 50 paths), `locked_count`, `owners` (`{process name: count}`), `denied` (first 50), `denied_count`, `kept_recent`, `refused` (`[[path, reason], ...]`), `cancelled`.
  - `summarise(entry: dict) -> str` — one line for the UI, e.g. `"Freed 2.1 GB (1,204 files). 312 in use by Teams.exe, WhatsApp.exe. 3 refused."`
  - Old entries (Task-2-and-earlier shape: `largest`, `refused` as plain paths) still load and summarise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cleanup_history_report.py
import json

from modules.cleanup import cleanup_history as ch
from modules.cleanup.cleanup_scanner.deleter import DeleteReport


def test_record_report_keeps_measured_bytes_and_every_kind_of_leftover(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    report = DeleteReport(freed=2_000, files=3, locked=[r"C:\T\a.log"], denied=[r"C:\T\x"], kept_recent=4)
    ch.record_report(report, [(r"C:\Pkg\LocalCache", "holds an app's sign-in")],
                     owners={"Teams.exe": 1})
    entry = json.loads((tmp_path / "log.json").read_text())[-1]
    assert (entry["bytes"], entry["count"], entry["locked_count"], entry["kept_recent"]) == (2000, 3, 1, 4)
    assert entry["owners"] == {"Teams.exe": 1}
    assert entry["refused"] == [[r"C:\Pkg\LocalCache", "holds an app's sign-in"]]


def test_summarise_names_what_was_left_and_why():
    entry = {"bytes": 2_254_857_830, "count": 1204, "locked_count": 312,
             "owners": {"Teams.exe": 300, "WhatsApp.exe": 12}, "denied_count": 0,
             "kept_recent": 9, "refused": [["p", "r"]] * 3, "cancelled": False}
    text = ch.summarise(entry)
    assert text.startswith("Freed 2.1 GB (1,204 files).")
    assert "312 in use by Teams.exe, WhatsApp.exe" in text
    assert "9 newer than a day kept" in text and "3 refused" in text


def test_an_old_entry_still_summarises():
    old = {"at": "2026-10-02T18:26:40", "count": 834, "bytes": 4278726303,
           "largest": [["C:\\x", 1]], "refused": ["C:\\y"]}
    assert ch.summarise(old) == "Freed 4.0 GB (834 items). 1 refused."


def test_nothing_to_record_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    ch.record_report(DeleteReport(), [])
    assert not (tmp_path / "log.json").exists()
```

- [ ] **Step 2: Run to fail**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_history_report.py -v`
Expected: FAIL (`summarise` missing, `record_report` is the Task-2 stub).

- [ ] **Step 3: Implement** (replace the Task-2 stub in `cleanup_history.py`)

```python
LEFTOVER_PATHS = 50


def record_report(report, refused, owners=None) -> None:
    """Append one clean's MEASURED outcome to the deletion log. Never raises."""
    if not (report.files or report.locked or report.denied or report.kept_recent or refused):
        return
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "count": report.files,
        "bytes": int(report.freed),
        "locked": report.locked[:LEFTOVER_PATHS],
        "locked_count": len(report.locked),
        "owners": dict(owners or {}),
        "denied": report.denied[:LEFTOVER_PATHS],
        "denied_count": len(report.denied),
        "kept_recent": report.kept_recent,
        "refused": [[path, reason] for path, reason in refused][:PATHS_PER_ENTRY],
        "cancelled": bool(report.cancelled),
    }
    _append_entry(entry)


def _append_entry(entry: dict) -> None:
    path = _deleted_path()
    try:
        entries = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    entries = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("cleanup deletion log unreadable, starting over: %s", e)
        entries.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries[-200:], f, indent=2)
    except OSError as e:
        logger.warning("could not write the cleanup deletion log %s: %s", path, e)


def summarise(entry: dict) -> str:
    from core.formatting import format_size
    noun = "files" if "locked_count" in entry else "items"
    parts = [f"Freed {format_size(entry.get('bytes', 0))} ({entry.get('count', 0):,} {noun})."]
    if entry.get("locked_count"):
        who = ", ".join(sorted(entry.get("owners") or {}, key=lambda k: -entry["owners"][k]))
        parts.append(f"{entry['locked_count']:,} in use" + (f" by {who}." if who else "."))
    if entry.get("denied_count"):
        parts.append(f"{entry['denied_count']:,} access denied.")
    if entry.get("kept_recent"):
        parts.append(f"{entry['kept_recent']:,} newer than a day kept.")
    if entry.get("refused"):
        parts.append(f"{len(entry['refused'])} refused.")
    if entry.get("cancelled"):
        parts.append("Cancelled.")
    return " ".join(parts)
```

Make `record_deleted` call `_append_entry(entry)` instead of its inline file code (same behaviour, no duplicate writer). Confirm `format_size(4278726303)` prints `4.0 GB` and `format_size(2254857830)` prints `2.1 GB`; if `core.formatting.format_size` uses another precision, change the two expected strings to its output, not the function.

In `stage_runners.run_cleanup_safe_stage`, replace `deleted, errors = cs.delete_items(all_items, stop_wuauserv=needs_wu)` with:

```python
    report, refused = cs.delete_items_report(all_items, stop_wuauserv=needs_wu)
    deleted, errors = report.files, len(refused) + len(report.locked) + len(report.denied)
```

and wherever that function reports freed bytes, use `report.freed` (measured) instead of the scanned total. Export `delete_items_report` from `cleanup_scanner/__init__.py` next to `delete_items`.

- [ ] **Step 4: Run**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_history_report.py -q -k "cleanup_history or stage_runner"` and `-k "cleanup or stage"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/modules/cleanup/cleanup_history.py src/modules/cleanup/cleanup_scanner/__init__.py src/modules/updates/stage_runners.py tests/test_cleanup_history_report.py
git commit -m "feat(cleanup): the deletion log records measured bytes, files in use, refusals with reasons"
```

---

### Task 4: Name the process holding locked files (Restart Manager)

**Files:**
- Create: `src/modules/cleanup/cleanup_scanner/lock_owners.py`
- Modify: `src/modules/cleanup/cleanup_scanner/scanners_system.py` (`delete_items_report` passes owners)
- Test: `tests/test_cleanup_lock_owners.py`

**Interfaces:**
- Consumes: `DeleteReport.locked` (Task 1), `record_report(..., owners=)` (Task 3).
- Produces: `owners_of(paths: Sequence[str], limit: int = 64) -> Dict[str, int]` — `{process image name: number of the given files it holds}`; `{}` when Restart Manager refuses, with a WARNING. Never raises.

**Why Restart Manager:** it is the API Windows Installer and Explorer's "file in use" dialog use (`RmStartSession` → `RmRegisterResources` → `RmGetList`), needs no elevation for the caller's own processes, and returns `strAppName` and the PID. Cap at 64 files per call: `RmGetList` cost grows with resources, and the person needs the top owners, not a census.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cleanup_lock_owners.py
import os
import subprocess
import sys
import time

from modules.cleanup.cleanup_scanner import lock_owners


def test_a_file_held_by_another_process_is_attributed_to_it(tmp_path):
    held = tmp_path / "held.log"
    held.write_text("x")
    code = f"import time; f = open(r'{held}', 'rb'); time.sleep(30)"
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        for _ in range(50):
            owners = lock_owners.owners_of([str(held)])
            if owners:
                break
            time.sleep(0.1)
        assert any(name.lower().startswith("python") for name in owners), owners
    finally:
        proc.kill()


def test_nothing_held_is_an_empty_answer(tmp_path):
    free = tmp_path / "free.log"
    free.write_text("x")
    assert lock_owners.owners_of([str(free)]) == {}


def test_no_paths_costs_nothing():
    assert lock_owners.owners_of([]) == {}
```

- [ ] **Step 2: Run to fail**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_lock_owners.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/modules/cleanup/cleanup_scanner/lock_owners.py
"""Which processes hold these files -- Restart Manager, the API Windows
Installer and Explorer's "file in use" dialog use. Qt-free; never raises."""
import ctypes
import logging
import os
from collections import Counter
from ctypes import wintypes
from typing import Dict, Sequence

logger = logging.getLogger(__name__)

CCH_RM_SESSION_KEY = 33
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63
ERROR_MORE_DATA = 234


class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", _RM_UNIQUE_PROCESS),
                ("strAppName", wintypes.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
                ("strServiceShortName", wintypes.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG),
                ("TSSessionId", wintypes.DWORD),
                ("bRestartable", wintypes.BOOL)]


def _image_name(pid: int, fallback: str) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception as exc:  # gone, or another user's: the RM display name will do
        logger.debug("no image name for pid %s: %s", pid, exc)
        return fallback or f"PID {pid}"


def _owners_of_one(rm, path: str) -> list:
    """Restart Manager answers per SESSION, not per file -- so one session per path
    is what makes "how many of these files does Teams hold" countable."""
    handle = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY)
    if rm.RmStartSession(ctypes.byref(handle), 0, key) != 0:
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if rm.RmRegisterResources(handle, 1, files, 0, None, 0, None) != 0:
            return []
        needed, count, reasons = wintypes.UINT(), wintypes.UINT(0), wintypes.DWORD()
        rc = rm.RmGetList(handle, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if rc not in (0, ERROR_MORE_DATA) or needed.value == 0:
            return []
        infos = (_RM_PROCESS_INFO * needed.value)()
        count.value = needed.value
        if rm.RmGetList(handle, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons)) != 0:
            return []
        return [_image_name(i.Process.dwProcessId, i.strAppName) for i in infos[:count.value]]
    finally:
        rm.RmEndSession(handle)


def owners_of(paths: Sequence[str], limit: int = 64) -> Dict[str, int]:
    if not paths:
        return {}
    try:
        rm = ctypes.WinDLL("rstrtmgr")
    except OSError as exc:
        logger.warning("Restart Manager unavailable: %s", exc)
        return {}
    counts: Counter = Counter()
    for path in list(paths)[:limit]:
        try:
            for name in set(_owners_of_one(rm, os.path.abspath(path))):
                counts[name] += 1
        except OSError as exc:
            logger.warning("Restart Manager could not check %s: %s", path, exc)
    return dict(counts)
```

In `delete_items_report._do_delete`, before `cleanup_history.record_report(report, refused)`:

```python
        from modules.cleanup.cleanup_scanner.lock_owners import owners_of
        owners = owners_of(report.locked) if report.locked else {}
        cleanup_history.record_report(report, refused, owners=owners)
```

- [ ] **Step 4: Run**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_lock_owners.py tests/test_cleanup_deleter.py -v`
Expected: PASS. Then measure on the real machine with Teams running (read-only): `.venv/Scripts/python -c "import sys,glob,os; sys.path.insert(0,'src'); from modules.cleanup.cleanup_scanner.lock_owners import owners_of; fs=[f for f in glob.glob(os.path.expandvars(r'%LOCALAPPDATA%\Packages\MSTeams_*\LocalCache\**\*'), recursive=True) if os.path.isfile(f)]; print(len(fs), owners_of(fs))"`
Expected: a non-empty owner such as `ms-teams.exe` while Teams runs. If it is empty with Teams running, stop and investigate before continuing — attribution that never names anyone is worse than none.

- [ ] **Step 5: Commit**

```bash
git add src/modules/cleanup/cleanup_scanner/lock_owners.py src/modules/cleanup/cleanup_scanner/scanners_system.py tests/test_cleanup_lock_owners.py
git commit -m "feat(cleanup): files left in use are attributed to the process holding them (Restart Manager)"
```

---

### Task 5: Faster, truer sizes — one `scandir` walk, no cloud placeholders, no links

**Files:**
- Modify: `src/modules/cleanup/cleanup_scanner/_common.py` (`get_dir_size`, `_make_item`)
- Test: `tests/test_cleanup_deleter.py` (append) — the size and the delete must agree

**Interfaces:**
- Consumes: nothing new.
- Produces: `get_dir_size(path: str) -> int` — same signature; now counts what `delete_path` would free from a full clean (local, non-placeholder, not-through-a-link bytes).

**Why:** `os.walk` + `os.path.getsize` is two kernel round trips per file; `os.scandir`'s `DirEntry.stat()` on Windows is served from the `FindNextFileW` data already fetched — zero extra syscalls. OneDrive Files On-Demand placeholders (`FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` 0x400000, `FILE_ATTRIBUTE_RECALL_ON_OPEN` 0x40000, `FILE_ATTRIBUTE_OFFLINE` 0x1000) report their cloud size but occupy nothing locally. CLAUDE.md already records that opening such a file hydrates it — `scandir` never opens.

- [ ] **Step 1: Write the failing tests** (append)

```python
from modules.cleanup.cleanup_scanner import _common


def test_size_skips_links_and_matches_what_a_clean_frees(tmp_path):
    outside = tmp_path / "outside"
    _file(outside / "big.bin", 1000)
    cache = tmp_path / "cache"
    _file(cache / "a.bin", 30)
    _file(cache / "sub" / "b.bin", 12)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(cache / "link"), str(outside)],
                   check=True, capture_output=True)
    measured = _common.get_dir_size(str(cache))
    assert measured == 42
    assert d.delete_path(str(cache), True).freed == measured


def test_size_skips_cloud_placeholders(tmp_path, monkeypatch):
    _file(tmp_path / "local.bin", 10)
    _file(tmp_path / "cloud.bin", 5000)
    real = _common.os.scandir

    class Entry:
        def __init__(self, e):
            self._e, self.name, self.path = e, e.name, e.path
        def is_dir(self, follow_symlinks=True):
            return self._e.is_dir(follow_symlinks=follow_symlinks)
        def is_symlink(self):
            return False
        def is_junction(self):
            return False
        def stat(self, follow_symlinks=True):
            st = self._e.stat(follow_symlinks=follow_symlinks)
            if self.name == "cloud.bin":
                return os.stat_result((st.st_mode, 0, 0, 0, 0, 0, st.st_size, 0, 0, 0)), 0x400000
            return st, 0

    def scandir(path):
        return [Entry(e) for e in real(path)]
    monkeypatch.setattr(_common, "_attrs", lambda st_and_attr: st_and_attr[1])
    monkeypatch.setattr(_common, "_stat", lambda entry: entry.stat(follow_symlinks=False))
    monkeypatch.setattr(_common.os, "scandir", scandir)
    assert _common.get_dir_size(str(tmp_path)) == 10
```

- [ ] **Step 2: Run to fail**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_deleter.py -k size -v`
Expected: FAIL (`measured == 1042`, `_attrs` missing).

- [ ] **Step 3: Implement** (`_common.py`)

```python
#: On-demand cloud files: their size is the cloud's, not this disk's.
_PLACEHOLDER = 0x400000 | 0x40000 | 0x1000  # RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE


def _stat(entry):
    st = entry.stat(follow_symlinks=False)
    return st, getattr(st, "st_file_attributes", 0)


def _attrs(st_and_attr) -> int:
    return st_and_attr[1]


def get_dir_size(path: str) -> int:
    """Local bytes a full clean of `path` would free: one scandir walk (no extra
    stat per file), never through a junction/symlink, never a cloud placeholder."""
    total = 0
    stack = [path]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError as exc:
            logger.debug("size walk cannot list %s: %s", folder, exc)
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
                st_and_attr = _stat(entry)
                if _attrs(st_and_attr) & _PLACEHOLDER:
                    continue
                total += st_and_attr[0].st_size
            except OSError as exc:
                logger.debug("size walk cannot read %s: %s", entry.path, exc)
    return total
```

(`logger.debug` here is right, not a swallow: sizing is an estimate, the deleter reports refusals for real at WARNING. If the silent-swallow ratchet disagrees, follow the ratchet.)

- [ ] **Step 4: Measure the speed-up on the real machine (read-only)**

Run before and after the change (stash to get "before"):

```bash
.venv/Scripts/python -c "import sys,time,os; sys.path.insert(0,'src'); from modules.cleanup.cleanup_scanner._common import get_dir_size as g; p=os.environ['TEMP']; t=time.perf_counter(); s=g(p); print(round(s/2**30,2),'GB', round(time.perf_counter()-t,2),'s')"
```

Expected: the same GB give or take placeholders and links, and a shorter time. Record both numbers in the commit message. If it is NOT faster, keep the change only for correctness and say so in the message.

- [ ] **Step 5: Run the cleanup tests and the reader sweep**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider -q -k "cleanup"` and `.venv/Scripts/python tools/cleanup_reader_sweep.py` (unelevated)
Expected: PASS; the sweep exits 0.

- [ ] **Step 6: Commit**

```bash
git add src/modules/cleanup/cleanup_scanner/_common.py tests/test_cleanup_deleter.py
git commit -m "perf(cleanup): size by one scandir walk; skip cloud placeholders and links (before X s, after Y s)"
```

---

### Task 6: History tab — what every clean really did

**Files:**
- Create: `src/modules/cleanup/tabs/_history_tab.py`
- Modify: `src/modules/cleanup/cleanup_module.py` (add the tab after "Discover"; refresh it after any clean)
- Modify: `pyinstaller_common.py` (`HIDDEN_IMPORTS` += `modules.cleanup.tabs._history_tab`, `modules.cleanup.cleanup_scanner.deleter`, `modules.cleanup.cleanup_scanner.lock_owners`)
- Test: `tests/test_cleanup_history_tab.py`

**Interfaces:**
- Consumes: `cleanup_history.recent_deleted(limit)`, `cleanup_history.summarise(entry)` (Task 3).
- Produces: `class _HistoryTab(QWidget)` with `refresh() -> None`; the table has columns `When | Freed | Files | Left in use | Refused | Summary`; selecting a row fills a detail pane listing in-use files (with owners), denied paths and refused paths with reasons; buttons `Open folder` and `Copy path` act on the selected detail line.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cleanup_history_tab.py
from modules.cleanup import cleanup_history as ch


def test_history_tab_lists_entries_newest_first_with_the_measured_summary(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    from modules.cleanup.cleanup_scanner.deleter import DeleteReport
    ch.record_report(DeleteReport(freed=1024 ** 3, files=10), [])
    ch.record_report(DeleteReport(freed=5, files=1, locked=[r"C:\T\a.log"]),
                     [(r"C:\P\LocalCache", "holds an app's sign-in")], owners={"Teams.exe": 1})
    from modules.cleanup.tabs._history_tab import _HistoryTab
    tab = _HistoryTab()
    tab.refresh()
    assert tab.table.rowCount() == 2
    assert tab.table.item(0, 3).text() == "1"            # newest first: 1 left in use
    tab.table.selectRow(0)
    detail = tab.detail.toPlainText()
    assert r"C:\T\a.log" in detail and "Teams.exe" in detail
    assert "holds an app's sign-in" in detail


def test_an_empty_log_says_so(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "none.json"))
    from modules.cleanup.tabs._history_tab import _HistoryTab
    tab = _HistoryTab()
    tab.refresh()
    assert tab.table.rowCount() == 0 and "No cleans recorded yet" in tab.status.text()
```

(`qapp` is the QApplication fixture in `tests/conftest.py`.)

- [ ] **Step 2: Run to fail**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_history_tab.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/modules/cleanup/tabs/_history_tab.py
"""History: what each clean REALLY did -- measured bytes, files left in use and
by whom, refusals with their reason. Reads cleanup_history; computes nothing."""
import os
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QHeaderView, QLabel,
                             QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from core.formatting import format_size
from modules.cleanup import cleanup_history

COLUMNS = ("When", "Freed", "Files", "Left in use", "Refused", "Summary")


def _detail_text(entry: dict) -> str:
    lines = [cleanup_history.summarise(entry), ""]
    owners = entry.get("owners") or {}
    if owners:
        lines.append("Held by: " + ", ".join(f"{n} ({c})" for n, c in owners.items()))
    for title, key in (("In use", "locked"), ("Access denied", "denied")):
        paths = entry.get(key) or []
        if paths:
            lines.append(f"{title}:")
            lines += [f"  {p}" for p in paths]
    refused = entry.get("refused") or []
    if refused:
        lines.append("Refused:")
        for row in refused:
            path, reason = (row if isinstance(row, list) else [row, "refused"])
            lines.append(f"  {path}  --  {reason}")
    return "\n".join(lines)


class _HistoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.status = QLabel()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        top.addWidget(self.status, 1)
        top.addWidget(refresh)
        layout.addLayout(top)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        layout.addWidget(split, 1)
        buttons = QHBoxLayout()
        for text, slot in (("Open folder", self._open_folder), ("Copy path", self._copy_path)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def refresh(self) -> None:
        self._entries = cleanup_history.recent_deleted(limit=200)
        self.table.setRowCount(len(self._entries))
        for row, e in enumerate(self._entries):
            cells = (e.get("at", "").replace("T", " "), format_size(e.get("bytes", 0)),
                     f"{e.get('count', 0):,}", f"{e.get('locked_count', 0):,}",
                     str(len(e.get("refused") or [])), cleanup_history.summarise(e))
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.status.setText(f"{len(self._entries)} cleans recorded." if self._entries
                            else "No cleans recorded yet.")
        self.detail.clear()

    def _show_detail(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self._entries):
            self.detail.setPlainText(_detail_text(self._entries[rows[0].row()]))

    def _selected_path(self) -> str:
        line = self.detail.textCursor().block().text().strip()
        return line.split("  --  ")[0] if line[1:3] == ":\\" else ""

    def _open_folder(self) -> None:
        path = self._selected_path()
        if path:
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            subprocess.Popen(["explorer", folder])

    def _copy_path(self) -> None:
        path = self._selected_path()
        if path:
            QApplication.clipboard().setText(path)
```

In `cleanup_module.py`, after the Discover `addTab` line (currently `self._tabs.addTab(self._discover, "Discover")`):

```python
        from modules.cleanup.tabs._history_tab import _HistoryTab
        self._history = _HistoryTab()
        self._tabs.addTab(self._history, "History")
```

and refresh it when the tab is shown and after a clean: in the existing `currentChanged` handler call `self._history.refresh()` when the History tab becomes current, and in the module's `freed_bytes` handler (the slot every tab's `freed_bytes` signal is connected to) add `self._history.refresh()`.

- [ ] **Step 4: Run**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_history_tab.py tests/test_module_smoke.py -q` tests/test_no_inline_stylesheets.py tests/test_no_frozen_colours.py.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/modules/cleanup/tabs/_history_tab.py src/modules/cleanup/cleanup_module.py pyinstaller_common.py tests/test_cleanup_history_tab.py
git commit -m "feat(cleanup): History tab -- measured freed bytes, files left in use and by whom, refusals and why"
```

---

### Task 7: Drive summary bar — free space per fixed drive and the last clean, above the tabs

**Files:**
- Create: `src/modules/cleanup/components/drive_summary_bar.py`
- Modify: `src/modules/cleanup/cleanup_module.py` (insert above the tab widget; refresh on activate and after a clean)
- Modify: `pyinstaller_common.py` (`HIDDEN_IMPORTS` += `modules.cleanup.components.drive_summary_bar`)
- Test: `tests/test_cleanup_history_tab.py` (append)

**Interfaces:**
- Consumes: `cleanup_scanner.drives` (fixed volumes: `drives.fixed_drive_roots() -> List[str]`), `shutil.disk_usage`, `cleanup_history.recent_deleted`, `cleanup_history.summarise`.
- Produces: `class DriveSummaryBar(QWidget)` with `refresh() -> None`; `drive_lines() -> List[Tuple[str, int, int]]` Qt-free helper in the same file returning `(root, free, total)`; one `QProgressBar` per drive (value = used %, text `"C:  412.3 GB free of 1.8 TB"`) and a label `"Last clean: <summarise(entry)>  (<when>)"`, or `"No clean recorded yet."`.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_summary_bar_shows_every_fixed_drive_and_the_last_clean(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    from modules.cleanup.cleanup_scanner.deleter import DeleteReport
    ch.record_report(DeleteReport(freed=3 * 1024 ** 3, files=7), [])
    from modules.cleanup.components import drive_summary_bar as dsb
    monkeypatch.setattr(dsb, "drive_lines", lambda: [("C:\\", 400 * 1024 ** 3, 1000 * 1024 ** 3),
                                                     ("E:\\", 10 * 1024 ** 3, 100 * 1024 ** 3)])
    bar = dsb.DriveSummaryBar()
    bar.refresh()
    texts = [b.format() for b in bar.bars]
    assert texts[0].startswith("C:") and "free of" in texts[0] and len(texts) == 2
    assert bar.bars[1].value() == 90                       # E: is 90 % used
    assert bar.last.text().startswith("Last clean: Freed 3.0 GB")
```

- [ ] **Step 2: Run to fail**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_history_tab.py -k summary -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/modules/cleanup/components/drive_summary_bar.py
"""Free space on every fixed drive and what the last clean did -- the two
numbers a person checks before and after cleaning. Measured, never estimated."""
import logging
import shutil
from typing import List, Tuple

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from core.formatting import format_size
from modules.cleanup import cleanup_history
from modules.cleanup.cleanup_scanner import drives

logger = logging.getLogger(__name__)


def drive_lines() -> List[Tuple[str, int, int]]:
    out = []
    for root in drives.fixed_drive_roots():
        try:
            usage = shutil.disk_usage(root)
            out.append((root, usage.free, usage.total))
        except OSError as exc:
            logger.warning("cannot read free space on %s: %s", root, exc)
    return out


class DriveSummaryBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bars: List[QProgressBar] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._row = QHBoxLayout()
        self._layout.addLayout(self._row)
        self.last = QLabel()
        self.last.setWordWrap(True)
        self._layout.addWidget(self.last)

    def refresh(self) -> None:
        for bar in self.bars:
            bar.deleteLater()
        self.bars = []
        for root, free, total in drive_lines():
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(round(100 * (total - free) / total) if total else 0)
            bar.setFormat(f"{root.rstrip(chr(92))}  {format_size(free)} free of {format_size(total)}")
            bar.setTextVisible(True)
            self._row.addWidget(bar)
            self.bars.append(bar)
        recent = cleanup_history.recent_deleted(limit=1)
        if recent:
            when = recent[0].get("at", "").replace("T", " ")
            self.last.setText(f"Last clean: {cleanup_history.summarise(recent[0])}  ({when})")
        else:
            self.last.setText("No clean recorded yet.")
```

In `cleanup_module.py` `create_widget`, add `self._summary = DriveSummaryBar()` to the outer layout immediately before the tab widget; call `self._summary.refresh()` in `on_activate()` and in the same `freed_bytes` slot Task 6 touched.

- [ ] **Step 4: Run**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider tests/test_cleanup_history_tab.py tests/test_module_smoke.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/modules/cleanup/components/drive_summary_bar.py src/modules/cleanup/cleanup_module.py pyinstaller_common.py tests/test_cleanup_history_tab.py
git commit -m "feat(cleanup): drive free-space bar and last clean above the tabs"
```

---

### Task 8: Real-machine verification, docs, build, deploy, push

**Files:**
- Modify: `CLAUDE.md` (Cleanup Module rules)
- Create: `tools/cleanup_delete_check.py`

**Interfaces:**
- Consumes: everything above.
- Produces: a harness that runs the deleter on a THROWAWAY tree on the real disk (never a real cache) with a locked file, a junction, a read-only file, a 300-char path and a fresh file, prints the report and exits non-zero on any mismatch.

- [ ] **Step 1: Write the harness**

```python
# tools/cleanup_delete_check.py
"""Drive the Cleanup deleter on a throwaway tree under %TEMP% -- never a real
cache. Exits 1 if any outcome differs from what the deleter promises."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from modules.cleanup.cleanup_scanner import deleter as d
from modules.cleanup.cleanup_scanner.lock_owners import owners_of


def main() -> int:
    root = tempfile.mkdtemp(prefix="wct-delete-check-")
    outside = tempfile.mkdtemp(prefix="wct-delete-target-")
    try:
        old = time.time() - 3 * 86400
        def put(rel, size):
            p = os.path.join(root, rel)
            os.makedirs(d.long_path(os.path.dirname(p)), exist_ok=True)
            with open(d.long_path(p), "wb") as f:
                f.write(b"x" * size)
            os.utime(d.long_path(p), (old, old))
            return p
        put("a.tmp", 1000)
        ro = put("ro.tmp", 10)
        os.chmod(ro, 0o444)
        put(os.path.join(*(["d" * 40] * 8), "deep.bin"), 7)
        fresh = os.path.join(root, "fresh.msi")
        with open(fresh, "wb") as f:
            f.write(b"y" * 50)
        locked = put("locked.log", 30)
        with open(os.path.join(outside, "precious.txt"), "w") as f:
            f.write("keep")
        subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(root, "link"), outside],
                       check=True, capture_output=True)
        fd = os.open(locked, os.O_RDONLY)
        try:
            report = d.delete_path(root, True, keep_newer_than_s=86400)
            owners = owners_of(report.locked)
        finally:
            os.close(fd)
        print(report, owners)
        checks = {
            "freed 1017 bytes": report.freed == 1017,
            "locked file listed": report.locked == [locked],
            "locked file attributed to python": any(n.lower().startswith("python") for n in owners),
            "fresh file kept": os.path.exists(fresh) and report.kept_recent == 1,
            "junction target untouched": os.path.exists(os.path.join(outside, "precious.txt")),
            "root kept": os.path.isdir(root),
        }
        for name, ok in checks.items():
            print(("PASS " if ok else "FAIL ") + name)
        return 0 if all(checks.values()) else 1
    finally:
        shutil.rmtree(d.long_path(root), ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python tools/cleanup_delete_check.py`
Expected: six `PASS` lines, exit 0.

- [ ] **Step 3: CLAUDE.md** — add under "Cleanup Module", after the sign-in bullet:

```markdown
- **`delete_items` deletes through `cleanup_scanner/deleter.py`, never
  `rmtree`** (2026-10-11). The old `rmtree(ignore_errors=True)` reported a
  whole item freed while files in use stayed, deleted cache ROOTS, and the
  sign-in guard's fail-closed walk refused all of `%TEMP%` over 25 unreadable
  subfolders. Now: a directory item's contents go and the root stays unless
  the item sets `keep_root=False` (superseded versions, empty folders,
  orphaned profiles); links are unlinked, never followed; files in use are
  listed and named by Restart Manager (`lock_owners.py`); Temp keeps files
  younger than 24 h (`keep_newer_than_hours`), because installers read their
  extracted files back. Freed bytes are MEASURED; the History tab and the
  drive bar read `cleanup_deleted.json` and compute nothing.
  `tools/cleanup_delete_check.py` drives it on a throwaway tree.
```

- [ ] **Step 4: Full suite**

Run: `.venv/Scripts/python -m pytest -p no:cacheprovider -q`
Expected: PASS. If `tests/test_log_recursive.py::test_the_real_windows_logs_tree_is_handled` fails with "everything was ticked", that is machine state (no oversized log in `C:\Windows\Logs`), unrelated — re-run with `--deselect` for it and say so in the hand-off.

- [ ] **Step 5: Commit, build, deploy, push**

```bash
git add CLAUDE.md tools/cleanup_delete_check.py
git commit -m "docs(cleanup): the deleter's rules; a real-disk delete harness"
rm -f build/WinClientTool-portable/PYZ-00.toc build/WinClientTool-portable/PKG-00.toc
.venv/Scripts/pyinstaller WinClientTool-portable.spec -y --distpath dist
cp "dist/WinClientTool-Portable.exe" "C:/Users/iorda/OneDrive/1 Personal/Aplicații/WinClientTool-Portable.exe"
cmp "dist/WinClientTool-Portable.exe" "C:/Users/iorda/OneDrive/1 Personal/Aplicații/WinClientTool-Portable.exe" && echo DEPLOYED-IDENTICAL
git push origin master
```

Expected: `DEPLOYED-IDENTICAL`, push succeeds.

---

## Out of scope (considered and deliberately left)

- **Merging the 10 Cleanup tabs into one flow.** Real UX win, but a redesign of `quick_cleanup_tab.py` (1,305 lines) and `_scan_tab.py` (672) is its own plan; this one changes what the tabs DO, not how many there are.
- **Delete-at-reboot (`MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)`) for files in use.** Needs elevation for most paths and queues `PendingFileRenameOperations`, which this app's own pending-restart logic then has to explain. Naming the owner (Task 4) lets the person close it and clean again — the lower-risk answer.
- **Allocation size (size on disk) instead of logical size.** Needs a handle per file (`GetFileInformationByHandleEx`), which is the cost Task 5 removes. Placeholders and links were the large errors; compression is small for caches.
- **Scanner orchestration speed.** Measured 5.9 s for the full present sweep; not the bottleneck.
