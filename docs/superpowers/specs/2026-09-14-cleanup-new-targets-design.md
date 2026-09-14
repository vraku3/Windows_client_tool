# New Cleanup Targets (Sub-project 2) — Design

## 0. How this spec was produced

This is Sub-project 2 of the 4-part sequence approved in
`docs/superpowers/specs/2026-09-14-cleanup-quick-cleanup-merge-design.md`
§7. Normally this codebase's brainstorming process asks the user
clarifying questions one at a time and gets explicit sign-off before
writing a spec. **That did not happen here**: the user explicitly said
"keep going with all the tasks left ... when all of them are done save
the progress and shut down this computer" mid-session, i.e. gave
blanket authorization to proceed through Sub-projects 2-4 without further
check-ins. Every design decision below that would normally be a question
is instead a **ruling**, made using this codebase's own established
patterns and CLAUDE.md's documented conventions as the tie-breaker,
recorded explicitly so the user can course-correct anything on review.

## 1. Overview

The original spec's §7.2 named seven candidate targets: Windows.old,
hibernation-file right-sizing, print spooler stuck-job cleanup, NGEN
native image cache, Recycle Bin per-drive reservation audit, orphaned
user profiles, OneDrive "always keep on this device" pin audit.

**Investigating the real current state of each (reading the actual
source, not assuming the original brainstorm's premises still held)
found that three of the seven were mis-scoped for this sub-project**,
either because they already exist, or because they don't fit this
module's ScanItem/delete contract at all:

| Target | Real finding | Disposition |
|---|---|---|
| Windows.old | **Already exists** (`scan_windows_old`) but has 2 real bugs: hardcodes `C:\Windows.old` (violates this codebase's own "never a drive letter" catalog rule — a Windows install on D: is invisible to it) and is marked `safety="safe"` (auto-selected by "Clean All Safe"), which is more confident than Microsoft's own guidance (Windows.old contains hardlinked files sharing space with the live OS; manual deletion is unsupported, Disk Cleanup / Storage Sense is the documented path) | **Fix the two bugs in this sub-project.** Building the full "official DISM/cleanmgr sagerun automation" path was considered and set aside — it needs registry `VolumeCaches` handler GUIDs and a `cleanmgr /sagerun:N` profile number verified against a REAL machine before shipping an automated version, which this environment cannot do destructively-safely; recorded as a real follow-on, not silently dropped. |
| Print spooler | **Partially exists** (`scan_print_spooler`) but only measures/cleans the queue when the spooler service is ALREADY stopped — it has no ACTIVE "clear stuck jobs" action | **Build the missing piece**: a one-click action that stops the service, clears the queue, restarts it — the exact shape `_flush_wu_store` already uses for Windows Update, so this is a proven pattern, not a new one. |
| NGEN cache | **Already covered, correctly, and the apparent "gap" should NOT be filled.** `dev.json`'s two catalog entries cover the NGEN *download/assembly binary* cache (safe-ish). The actual native image STORE (`%windir%\assembly\NativeImages_v4.0.30319_64` etc.) is deliberately not scanned: deleting those files without going through `ngen.exe uninstall`/`executeQueuedItems` first can leave stale native-image registrations that break .NET app startup until a repair runs. This is the same class of restraint this codebase already shows for the driver store (`DriverStore\FileRepository` folders never enter the checkbox tree directly). | **No action — already correctly scoped.** Documented here so nobody re-discovers this and "fixes" it into a real bug later. |
| Hibernation right-sizing | Does not exist. Different from Tweaks' `power.json` (`powercfg /h off`, fully disables hibernation) — this KEEPS hibernation on, shrinks `hiberfil.sys` | **Build**: new one-click action, `powercfg /hibernate /size 50` (Windows' own reduced-size default), gated on hibernation actually being enabled. |
| Orphaned user profiles | Does not exist, no prior art in this codebase | **Build**: new scanner, `safety="danger"` (a home folder is the highest-consequence thing this module could ever touch — never auto-selected, mirroring how virtual disks and the driver store are handled). |
| Recycle Bin reservation audit | Does not exist. On inspection this is a **configuration finding** (what size cap Explorer has set aside per drive), not a deletable item — nothing to put in a `ScanItem`/checkbox tree | **Deferred to Sub-project 4** (System Health), whose spec already has a first-class "read-only findings" category built for exactly this shape. Building it here would force it into a UI contract it doesn't fit. |
| OneDrive pin audit | Does not exist. Same shape problem: reporting how many bytes are pinned "Always keep on this device" is a finding, and un-pinning is an attribute change (Shell API / cloud-file attribute manipulation), not a path delete | **Deferred to Sub-project 4**, same reasoning. |

Net: **4 real, buildable items** in this sub-project — 2 bug fixes to an
existing scanner, 2 new one-click actions, 1 new scanner. Smaller than
the original 7-item brainstorm, and every cut is justified above rather
than silently dropped.

## 2. Component changes

### 2.1 Fix `scan_windows_old` (`cleanup_scanner/__init__.py` or wherever it
now lives after Sub-project 1's relocation — verify at implementation
time)

```python
def scan_windows_old(min_age_days: int = 0) -> ScanResult:
    """Windows.old folder left after an in-place upgrade (often 10-30 GB).

    Marked 'caution', not 'safe': Microsoft does not support deleting this
    manually (it shares hardlinked files with the live OS); the supported
    removal path is Disk Cleanup / Storage Sense. Reported and offered
    here, but never auto-selected by "Clean All Safe".
    """
    result = ScanResult()
    system_drive = os.environ.get("SystemDrive", "C:")
    item = _make_item(os.path.join(system_drive + "\\", "Windows.old"),
                      safety="caution", min_age_days=min_age_days)
    if item:
        result.items.append(item)
        result.total_size = item.size
    return result
```

Ruling: `safety="caution"` (not "danger") — it's real reclaimable junk
most users genuinely want gone eventually, just not blindly
auto-selected. "Caution" already means "shown, selectable, not
auto-included in Clean All Safe" per this module's existing 3-level
convention.

### 2.2 New one-click action: Right-size Hibernation File

In `quick_cleanup_tab.py`, alongside the existing one-click actions
(added to `_build_one_click_panel`'s per-action list from the Cleanup
merge, action id `"resize_hibernation"`):

```python
def _resize_hibernation(self):
    # Gate on hibernation actually being enabled -- powercfg refuses
    # /hibernate /size on a machine where it's off, and the refusal
    # message is not obviously "hibernation is off" to a fresh user.
    check = subprocess.run(
        ["powercfg", "/a"], capture_output=True, text=True,
        creationflags=CREATE_NO_WINDOW, timeout=10)
    if "Hibernation has not been enabled" in (check.stdout or ""):
        self._action_status["resize_hibernation"].setText(
            "Hibernation is off on this machine — nothing to resize")
        return
    mb = QMessageBox(self)
    mb.setWindowTitle("Right-size Hibernation File")
    mb.setIcon(QMessageBox.Icon.Information)
    mb.setText(
        "This shrinks hiberfil.sys to 50% of RAM (Windows' own default "
        "since Windows 10) without disabling hibernation. Continue?"
    )
    mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
    if mb.exec() != QMessageBox.StandardButton.Ok:
        return
    self._run_action_command(
        "resize_hibernation", "powercfg /hibernate /size 50",
        "Hibernation file resized", need_confirm=False)
```

Ruling: 50%, matching Windows' own shipped default (not a user-editable
slider) — a senior-engineer "custom size" control is real scope creep
for a one-click panel; if wanted later it's a small follow-on, not part
of this pass.

### 2.3 New one-click action: Clear Stuck Print Jobs

Same file, action id `"clear_print_queue"`, mirroring `_flush_wu_store`'s
exact stop-service/clear-folder/restart-service shape:

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

### 2.4 New scanner: Orphaned user profiles

New file `cleanup_scanner/scanners_profiles.py` (a genuinely distinct
concern from the existing 8 submodules — user-account data, not app
caches — gets its own file rather than being wedged into
`scanners_system.py`, which is already 2000+ lines):

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


def _known_profile_paths() -> set:
    """Every ProfileImagePath ProfileList currently knows about, lower-
    cased for a case-insensitive match against real folder names."""
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
                    with winreg.OpenKey(root, sid_name) as sid_key:
                        path, _ = winreg.QueryValueEx(sid_key, "ProfileImagePath")
                        known.add(os.path.normcase(os.path.normpath(path)))
                except OSError:
                    continue
    except OSError as e:
        # Access denied or the key genuinely isn't there -- either way,
        # we cannot tell orphaned from real, so report nothing rather
        # than guess. A None sentinel distinguishes this from "checked,
        # found none" for the caller.
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

Wired into `LARGE_EXTRA` in `cleanup_module.py` (large, user-owned,
exactly the tab's existing pattern for `scan_backup_files`/
`scan_duplicate_files`/etc.) as
`cs.scan_orphaned_user_profiles: ('Orphaned User Profiles (no matching Windows account)', 'danger')`.

## 3. Data flow

No new UI surface — all four items plug into structures the Cleanup
merge (Sub-project 1) already finished: two are one-click actions
(`_run_action_command`'s existing busy-guard/status/button machinery,
built in that merge), one is a scanner-dict fix (existing `_ScanTab`
plumbing on Large Items), one is a new scanner wired into `LARGE_EXTRA`
the same way every other Large-Items scanner already is.

## 4. Error handling

- `_known_profile_paths()` returning `None` (registry read refused) means
  `scan_orphaned_user_profiles` reports **nothing**, never "everything is
  orphaned" — the same refusal-is-not-an-answer discipline this
  codebase's Security Dashboard and GPResult modules already document
  extensively.
- Both new one-click actions reuse `_run_action_command`'s existing
  error path (already fixed to have per-action status + busy-guard +
  `on_error` in the Cleanup merge) — no new error-handling code needed.
- `_resize_hibernation`'s `powercfg /a` pre-check avoids a confusing
  raw `powercfg` error message reaching the user when hibernation is
  simply off.

## 5. Testing

- `tests/test_cleanup_windows_old_fix.py` (new): asserts `scan_windows_old`
  builds its path from `%SystemDrive%` (mock `os.environ` to a non-C:
  value and confirm the scanned path follows), and that its `safety`
  field is `"caution"`, not `"safe"`.
- `tests/test_cleanup_scanners_profiles.py` (new): real unit tests for
  `_known_profile_paths()`/`scan_orphaned_user_profiles` against a fake
  registry (monkeypatch `winreg.OpenKey`/`EnumKey`/`QueryValueEx`) and a
  temp directory standing in for `%SystemDrive%\Users` — covering: a
  real profile folder matching ProfileList (not flagged), a folder with
  no ProfileList entry (flagged, `danger`), `Public`/`Default` never
  flagged regardless of ProfileList content, and a refused registry read
  producing an EMPTY result, not a false-positive "everything orphaned"
  result.
- `tests/test_quick_cleanup_new_actions.py` (new): one-click busy-guard/
  status coverage for both `_resize_hibernation` (including the "hibernation
  is off" early-exit path) and `_clear_print_queue`, following the exact
  pattern already established in `tests/test_quick_cleanup_one_click_actions.py`.

## 6. Non-goals

- No "official DISM/cleanmgr sagerun" automation for Windows.old — real
  follow-on, needs live-machine registry verification this environment
  can't safely do; the two bug fixes (drive letter, safety level) are
  this sub-project's actual Windows.old scope.
- No custom hibernation-size slider — fixed at Windows' own 50% default.
- No NGEN native-image-store scanning — deliberately, permanently out of
  scope (see §1's table); this is not a "not yet," it's a "should not."
- No Recycle Bin reservation audit or OneDrive pin audit in this
  sub-project — both deferred to Sub-project 4 (System Health), whose
  read-only-findings UI is the right shape for them.
- No changes to the existing `scan_print_spooler` passive scanner itself
  — the new one-click action is additive, not a replacement.

## 7. Deviation from the brainstorming process (recorded per the skill's
own requirement to flag when this happens)

This spec skipped the normal "ask clarifying questions one at a time,
present the design in chat, get approval" flow the brainstorming skill
otherwise requires, because the user explicitly authorized proceeding
through Sub-projects 2-4 without further check-ins. Every choice that
would normally be a question is instead a **ruling**, above, made
against this codebase's own established conventions. The user should
treat this spec as a proposal to review after the fact, same as the
implementation itself, not as something they already approved
interactively.
