# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Windows desktop optimization and diagnostics utility — a PyQt6 GUI application with 40+ plugin modules. Targets Windows 10/11 (64-bit), Python 3.12+.

## Development Commands

**Activate environment** (always needed before running or building):
```
.venv\Scripts\activate
```

**Run from source** (from project root):
```
python src/main.py
```

**Install dependencies**:
```
pip install -r requirements.txt
```

**Build — folder/onedir version** (output: `dist/WinClientTool/`):
```
pyinstaller WinClientTool.spec -y --distpath dist
```

**Build — portable onefile version** (output: `dist/WinClientTool-Portable.exe`):
```
pyinstaller WinClientTool-portable.spec -y --distpath dist
```

For a clean rebuild of the portable (e.g. after code changes), delete the cached PKG first:
```
rm -f build/WinClientTool-portable/PYZ-00.toc build/WinClientTool-portable/PKG-00.toc
pyinstaller WinClientTool-portable.spec -y --distpath dist
```
The portable spec must include `a.binaries` and `a.datas` in the EXE constructor — without these, the output is a ~3MB bootloader stub.

**After every portable build, deploy it** — this is a hard requirement, not optional cleanup:
```
cp "dist/WinClientTool-Portable.exe" "C:/Users/iorda/OneDrive/1 Personal/Aplicații/WinClientTool-Portable.exe"
```
The user runs the app day-to-day from `Aplicații/`, not from this repo's own (gitignored) `dist/`. Skipping this step leaves a stale copy running there indefinitely — do it every time, overwriting whatever's already there. Same deal for an onedir build: `cp -r "dist/WinClientTool" "C:/Users/iorda/OneDrive/1 Personal/Aplicații/WinClientTool"`. See the `build-portable` skill (`.claude/skills/build-portable/SKILL.md`), which bakes this step in.

**Syntax check** (without running):
```
python -c "import sys; sys.path.insert(0, 'src'); import main"
```

## Architecture

### App Singleton (`src/app.py`)

The `App` class owns all core services as a singleton: `event_bus`, `config`, `logger`, `backup`, `theme`, `search`, `module_registry`, `thread_pool`. Created once in `main.py`, accessed elsewhere via `App.get()`.

Two resource-path helpers handle PyInstaller's `_MEIPASS` layout:
- `_get_resource_dir()` — base directory for bundled config/ and data files; returns `sys._MEIPASS` in a onefile build, or the project root in source mode
- `_get_app_data_dir()` — `%APPDATA%/WindowsTweaker` for user-persisted config and logs

### Module Plugin System (`src/core/base_module.py`)

Every UI feature is a `BaseModule` subclass. Key lifecycle order:
1. `__init__()` — module instance created during registration
2. `module_registry.start_all()` → `on_start(app)` — called before `create_widget`; store `app` reference here only
3. `window.register_module()` → `create_widget()` — creates the QWidget; called once
4. User selects module → `on_activate()` — called every time the user navigates to the module
5. User leaves module → `on_deactivate()` — stop timers, release resources here
6. App shutdown → `on_stop()` — `cancel_all_workers()` is called automatically

**Critical**: `on_start` runs BEFORE `create_widget`. Do NOT access `_widget`, `_table`, or any UI elements in `on_start`. Only store the `app` reference.

Each module declares:
- `name`, `icon`, `description` — displayed in sidebar
- `group` — one of `ModuleGroup.OVERVIEW/DIAGNOSE/SYSTEM/MANAGE/OPTIMIZE/TOOLS/PROCESS`
- `requires_admin` — if True, module is disabled when not running elevated
- `get_search_provider()` — returns a `SearchProvider` for cross-module search
- `get_refresh_interval()` — return `Optional[int]` milliseconds (e.g. `60_000`) or `None` to disable auto-refresh

**The registry calls `get_search_providers()` (plural), not `get_search_provider()`.**
The default returns the single provider; a module speaking for several sources
overrides it. `SearchEngine.execute` filters *per provider* on
`provider.module_name`, so one provider standing in for several would have to
re-implement that filtering — hence the list.

### Composite Modules (`src/core/composite_module.py`)

A `CompositeModule` hosts other `BaseModule`s as tabs. `Diagnose`, `Debloat`,
`Startup & Boot` and `Network Diagnostics` are the four; a child stays an
ordinary module that knows nothing about being hosted, so it can be tested
alone and moved between hosts unchanged. A subclass only sets `self.children`
in `__init__` (import the children *inside* `__init__`, and add them to
`HIDDEN_IMPORTS` in `pyinstaller_common.py` — the frozen build otherwise runs
fine with the tab silently absent).

Four things about it that are easy to get wrong:

- **`on_activate`/`on_deactivate` go to the visible child only**, and a tab
  change deactivates the outgoing child before activating the incoming one.
  Children stop their refresh timers in `on_deactivate`; without this a host
  with three children polls WMI three times over for the life of the app.
- **`on_stop` reaches every started child, including ones whose tab was never
  opened** — so their widgets do not exist. A teardown path that touches UI
  must guard for that. `wifi_module._stop_scan` did not, and crashed.
- **Never `removeTab`/`insertTab` to swap in a lazily built widget.** Doing it
  on the current index fires `currentChanged` and re-enters the handler that
  asked for the build. Each tab owns a permanent page whose layout the child
  widget is added into.
- **Override `wrap(tabs)` to put chrome around the tabs.** `DiagnoseModule`
  does, to keep its unified search bar and results tree above them.
- **Auto-refresh is the host's, throttled per child.** `MainWindow` reads
  `get_refresh_interval()` off the *selected* module, so the composite answers
  with the fastest rate any child wants, and `refresh_data()` ticks the visible
  child only when that child's own interval has elapsed. A composite that does
  not answer leaves every child it hosts with no timer at all — that is how six
  modules lost auto-refresh in the first pass. It also means `refresh_data()`
  can fire for a child whose widget has never been built: guard teardown and
  refresh paths that touch UI.

A child that fails `on_start`, or needs elevation it does not have, becomes a
disabled tab carrying the reason — and those are different reasons; do not
show the admin message for a crash.

### Log Reader Modules (`src/core/log_reader_module.py`)

The six diagnostic readers (Event Viewer, CBS, DISM, Windows Update,
Reliability, Crash Dumps) are `LogReaderModule` subclasses: they set
`provider_class` and implement `load_entries(worker)`, and optionally
`build_controls(toolbar, extra)`. The UI is `ui/log_pane.py`'s `LogPane` —
table, detail panel, error banner, progress, Refresh — and there is exactly
one of it. Do not hand-roll another log pane.

### Background Workers (`src/core/worker.py`)

Use the `Worker` class for all background tasks. The worker function receives a `worker` parameter and emits results via signals:

```python
def do_work(worker):
    for item in items:
        if worker.is_cancelled:  # property, NOT a method
            return
        # ... work ...
        worker.signals.progress.emit(int(progress_pct))
    return result  # returned value goes to signals.result

w = Worker(do_work)
w.signals.result.connect(self._on_done)
w.signals.error.connect(self._on_error)
self._workers.append(w)
self.app.thread_pool.start(w)
```

`worker.is_cancelled` is a `@property` (no parentheses), NOT a method. Always track workers in `self._workers`.

**Cancellation**: Use `worker.cancel()` — do NOT directly assign `worker._cancelled = True`. The `cancel()` method uses a `Lock` to safely set the flag; bypassing it risks race conditions. This applies in `_cancel_all()` methods too:

```python
def _cancel_all(self) -> None:
    for w in self._workers:
        w.cancel()       # safe — uses Lock
        # NOT: w._cancelled = True
    self._workers.clear()
```

For WMI/COM operations use `COMWorker` (calls `pythoncom.CoInitialize()` automatically) instead of plain `Worker`.

**First-load guard pattern** — trigger data load on first `on_activate()`, not in `create_widget()`:

```python
def __init__(self):
    super().__init__()
    self._loaded = False

def on_activate(self):
    if not self._loaded:
        self._loaded = True
        self._load_data()
```

**Cross-thread widget access — CRITICAL**: Never call Qt widget methods directly from a worker thread. Marshal via a `pyqtSignal`:

```python
class _FixCard(QFrame):
    _line = pyqtSignal(str)   # class-level signal

    def _setup_ui(self):
        ...
        self._line.connect(self._output.appendPlainText)

    def _run(self):
        def append(line: str):
            self._line.emit(line)   # safe from any thread
        ...
```

### Widget Lifetime Guards

When a worker fires after its host widget has been deleted (e.g. user switched tabs), guard with `sip.isdeleted()`:

```python
try:
    import sip
    def _widget_is_valid(w):
        return not sip.isdeleted(w)
except ImportError:
    def _widget_is_valid(w):  # fallback
        return True

def set_entries(self, entries):
    if not _widget_is_valid(self._status):
        return
    # ... normal work ...
```

### DiagnoseModule — Hub Pattern (`src/modules/diagnose/diagnose_module.py`)

`DiagnoseModule` is a `CompositeModule` over the six diagnostic readers (Event
Viewer, CBS Log, DISM Log, Windows Update, Reliability, Crash Dumps). Those six
are real `LogReaderModule`s again — they are not registered as sidebar entries,
but they are imported, started, and their search providers are registered.

- Tabs are built lazily by `CompositeModule`; `LogPane.loaded` prevents re-parsing
- Diagnose adds no pane of its own. To add a diagnostic source, write a
  `LogReaderModule` and put it in `self.children` — do not build another pane
- The unified search bar lives in `wrap()`. It runs as a separate
  `_active_search: Worker`, tracked apart from `self._workers`, and `on_stop()`
  cancels it explicitly before delegating to the composite
- Crash Dumps requires admin, so unelevated it is a disabled tab (the composite
  gates it) rather than a tab that loads and shows an error banner

### TreeSize Module (`src/modules/treesize/`)

A full TreeSize Professional clone, not a simple folder-size viewer. Built from
`docs/superpowers/specs/2026-08-18-treesize-pro-design.md`, which was derived from the
installed product's bundled help and screenshots — where that spec or this code names a
ribbon tab, view, or column, the name is the product's own.

Three layers, and the boundary between them is load-bearing:

- **`scan/`** — the engine. `volume_info.py` (raw volume geometry), `ntfs_structs.py`
  (byte-level NTFS parsing), `mft_reader.py` (streams `$MFT` directly, the fast path),
  `walk_scanner.py` (`FindFirstFileExW` fallback), `filters.py`, `prune.py`,
  `scanner.py` (engine selection + orchestration).
- **`store/`** — `node_store.py` (columnar arrays; a node is an `int` index, not an
  object), `rollup.py`, `aggregates.py` (per-view sums).
- **`ui/`** — `shell.py` assembles the ribbon, tree, drive list, view tabs and status
  bar; `treemap.py` is Qt-free layout; `views/` holds Chart, Details, and the aggregate
  tables.

**No PyQt6 imports in `scan/` or `store/`.** That is what lets ~450 engine tests run
with no display and no elevation, and it is why every NTFS function takes `bytes` rather
than a handle.

**Two engines.** Elevated + whole NTFS drive → MFT reader (a full `C:` in ~20s).
Anything else → walk scanner. `get_volume_info()` returning `None` is the normal signal
to pick the walk engine, not an error.

**`size` and `alloc` are independent columns**, never derived from one another. A real
`C:` here reports 380.9 GB size against 139.9 GB allocated; the gap is sparse and
compressed files.

Gotchas that cost real debugging time, all recorded in
`.superpowers/sdd/2026-08-18-treesize-phase1-engine-store/progress.md`:

- **`$MFT` is fragmented on real volumes.** Reading `mft_offset .. +mft_valid_length` as
  one span silently loses every record past the first extent — it cost 62% of the
  volume here. Record 0's own `$DATA` run list gives the true extent map.
- **Record numbers are LOGICAL positions in the MFT data stream**, not physical offsets.
  Parent references use them, and physical offsets jump at every extent boundary.
- **Sparse and compressed attributes report their real cost in `TotalAllocated` (0x40)**,
  not `alloc_size` — NTFS emits that field for sparse too, not just compressed.
- **A torn record must skip, not raise.** The volume is read live with no snapshot, so
  records genuinely change mid-read.
- **`QModelIndex.internalId()` cannot distinguish 0 from unset**, and the scan root is
  node 0 — ids are stored as `node + 1`.
- **Sorting must never reset the model** (it collapses the tree), and the sort key lives
  on the model so folders expanded *after* a sort are ordered too.
- **Size columns sort on the number, not the formatted text**, or "9 B" lands above
  "10 GB".

`tools/treesize_scan.py` is a console harness for the engine — it prints engine, totals,
rate, bytes/node and a flag census, and **exits 2 when the scan was incomplete**.

### Log Viewer (`src/modules/log_viewer/`)

A CMTrace-style viewer for ConfigMgr and plain-text logs. Its own sidebar
module rather than a Diagnose tab: CBS and DISM point at FIXED system paths,
this opens whatever it is handed.

- **`cmtrace_parser.py`** — `<![LOG[msg]LOG]!><time= date= component= type= …>`
  records into the app's `LogEntry`. `type` 1/2/3 → Info/Warning/Error, which
  is what the colouring hangs off. Auto-detects by sniffing the head of the
  file and falls back to a best-effort plain-text parse.
- **`log_reader.py`** — byte-offset incremental reads.
- **`log_model.py`** — `QAbstractTableModel` over a capped `deque`.
- **`log_search_provider.py`** — joins the global search bar.

**No Qt in the parser or the reader**, the same split `scan/` and `store/`
keep in TreeSize, so both are testable without a display.

Gotchas, every one of which cost real debugging:

- **Every log under `C:\Windows\Logs` is UTF-8 WITH a BOM.** Decoded that is
  a leading `\ufeff`, which is invisible and is NOT matched by `\s`, so the
  leading-timestamp regex missed and the first line of every real Windows log
  came through undated. Generated test data never has a BOM; only real files
  do. `LogReader` strips it once, at the very start of the stream only.
- **A rollover is detected by the file's IDENTITY, not its size.** SCCM rolls
  the log and starts a new one under the same name; a rollover to the same
  length is invisible to a size check, and keeping the old offset means
  reading past the end forever — the view goes silent and looks like a quiet
  machine.
- **A half-written final line is held back** until its newline lands. A
  CMTrace record cut down the middle parses as nothing.
- **Undecodable trailing bytes are carried forward.** A multi-byte character
  split across two reads decodes to a replacement character that never
  repairs itself.
- **`LogModel.append` INSERTS, it does not reset.** A reset clears the view's
  selection, and while following that runs once a second — click a row to
  read it and it deselects under you. A reset is right only when the cap
  starts dropping from the front and every index shifts.
- **The 32 MB read cap is measured, not guessed.** Parsing runs ~26 MB/s, so
  that is ~1.2s for an Open where 64 MB is 2.7s; it also yields ~170k records
  against the model's 200k cap, so the two limits are matched. An 8 MB cap
  silently loaded 47,831 of a 21 MB log's 120,000 records.
- **Truncation is always stated in the status bar.** Silently showing the last
  N lines of a longer log is how someone concludes the log is clean.
- The search provider holds the model's live deque rather than copying it per
  tick, and snapshots inside `search()` instead — the global bar runs on the
  UI thread today, but `DiagnoseModule` already searches on a Worker.

`tools/logviewer_check.py` drives it against the REAL CBS/DISM/Panther logs
plus a generated ConfigMgr one, including a rollover. **The CMTrace path has
never read a genuine SCCM or Intune log** — no ConfigMgr client on the dev
machine — so that check is still owed.

### Monitor Control (`src/modules/monitor_control/`)

One sidebar pane over displays, modes, monitor audio and DDC/CI.
`requires_admin = True` with `read_only_unelevated = True`: display and DDC
work needs no elevation at all, only the audio endpoint writes do. **No PyQt6
below the UI layer** — `display_config`, `display_modes`, `monitor_identity`,
`display_audio`, `ddc`, `profiles`, `window_layout`, `window_census` and
`view_model` are all testable headless, which is why 330+ tests run with no
display attached.

**`QueryDisplayConfig` is the only source of truth for display state.** The
obvious alternatives both lie, and both were measured lying on this machine:

- **`WmiMonitorID.Active` is `True` for every monitor with a valid EDID**,
  including one that is connected and switched off — it reported 3 active
  where 2 were on the desktop.
- **`Win32_VideoController.CurrentRefreshRate` reports per ADAPTER**, not per
  display: 60 Hz for a card driving panels at two different rates.

Rules that each cost a real defect:

- **`\\.\DISPLAYn` names are NOT STABLE, and neither are mode lists.** Measured
  inside one session: DISPLAY1 and DISPLAY2 swapped (226 modes/primary became
  117/not-primary) while the CCD targets did not move at all, and the
  Gigabyte's 1440p ceiling fell from 280 Hz to 120 Hz — a link renegotiating
  to less bandwidth. So: key everything on the CCD target or the EDID, resolve
  the GDI name at the moment you need it, and never write a test that pins a
  device name, a mode count or a refresh rate.
- **`modeInfoIdx` is only meaningful on an ACTIVE path.** 103 inactive paths
  carry the `0xFFFFFFFF` marker and **20 carry a valid-looking index pointing
  at another display's mode**. Resolving it blindly gives a switched-off
  monitor someone else's resolution.
- **Active/inactive is a property of a TARGET, not a path.** Each target
  appears on up to five source paths and can have active and inactive ones at
  once. Aggregate per target.
- **`DISPLAYCONFIG_MODE_INFO_TYPE` is SOURCE=1, TARGET=2.** Inverted, a
  target's 64-bit `pixelRate` reads as a source's width and you get a
  "resolution" of 241500000x0 rather than an error.
- **`edidManufactureId` must be BYTE-SWAPPED before decoding** — raw `0xAC10`
  → `0x10AC` → "DEL"; unswapped it decodes to `'K@P'`. Manufacturer and product
  are `None` when the driver clears `edidIdsValid`, never a plausible string.
- **"Best mode" means the panel's NATIVE resolution at its highest refresh**,
  not the largest mode enumerated. AMD VSR offers 3840x2160 on a 1440p panel,
  so largest-first recommends something both softer AND slower than the glass
  can do. Native comes from the EDID preferred timing; pass it to `best_mode`.
- **Audio `DeviceState` carries undocumented high bits** — `0x10000001` is
  ACTIVE with `0x10000000` set — so MASK the documented bits, never compare
  equal. Endpoint→monitor matching is by name and **duplicate names are real
  here** ("2 - MO27Q28G" ACTIVE alongside "4 - MO27Q28G" NOTPRESENT), so
  matching narrows by state and reports genuine ambiguity rather than guessing.
- **`IPolicyConfig::SetEndpointVisibility` is at vtable index 14, not 12.**
  Index 12 is `SetPropertyValue(PCWSTR, const PROPERTYKEY&, PROPVARIANT*)`.
  The write path is implemented and **has never been executed** — it needs a
  supervised disable → re-read → re-enable → re-read round-trip.
- **DDC/CI: not every monitor answers**, so `probe()` records `responded` with
  a reason and a mute monitor gets no control rather than a dead slider. Input
  source (0x60) is an ENUMERATION — offer only values the capabilities string
  claims, and verify a write against the **low byte only**, because panels
  mirror the value into the high byte (the Dell reports DisplayPort-1 as
  `0x0F0F`) and a whole-word comparison calls a successful switch "ignored".
  Physical monitor handles must go back through `DestroyPhysicalMonitors`.
- **Profiles key on EDID identity**, never on a device name or a path index,
  and refuse (naming what is missing) rather than partially applying. Window
  restore uses `SetWindowPlacement`, not `MoveWindow`, or a maximised window
  comes back the wrong size; cloaked UWP windows are excluded from per-monitor
  counts via `DWMWA_CLOAKED`.
- **Every display change goes through `_apply_guard`**: snapshot, apply, then a
  15s countdown that reverts unless confirmed. The confirm must land on a
  screen that still exists AFTER the change, and a change whose snapshot could
  not be taken is refused outright — no undo, no change.

`tools/monitor_control_check.py` is the read-only real-machine harness (the
sibling of `treesize_scan.py` and `cleanup_reader_sweep.py`); it exits non-zero
when anything came back unreadable.

### Cleanup Module (`src/modules/cleanup/`)

**Scanner** (`cleanup_scanner.py`): functions take `min_age_days: int = 0` and return `ScanResult`. `ScanItem` fields: `path`, `size`, `is_dir`, `selected`, `safety` ("safe"/"caution"/"danger") — **no `name` field** (passing `name=` raises `TypeError`).

**8-tab structure** in `cleanup_module.py`:
- `_ScanTab(QWidget)` — reusable tab wrapping a `{fn: label}` scanners dict; exposes `freed_bytes = pyqtSignal(int)` and `auto_scan()` (no-op if already scanned). Tracks **all** workers (scan + clean) in a single `self._workers: list`.
- `_BrowserCleanupTab(QWidget)` — uses `EnhancedBrowserScanner` from `browser_scanner.py`; same signal and worker pattern.
- `_LargeItemsTab(QWidget)` — wraps `_ScanTab` + DISM "Component Cleanup" button in background. Its `_cancel_all()` calls `self._scan_tab._cancel_all()` then `self._dism_worker.cancel()`.
- `_OverviewTab(QWidget)` — table of all groups; "Scan All" parallelises workers; "Clean All Safe" deletes safe items. `_cancel_all()` iterates `self._scan_workers` list calling `w.cancel()` on each.
- `CleanupModule.on_activate()` triggers `_overview.auto_scan()`; `QTabWidget.currentChanged` wires each tab's `auto_scan()` for lazy first-load

**Rules here, every one of which cost a measured defect:**

- **A cancelled Worker emits `cancelled` — never `result` or `error`.** Nothing
  was connected to it, so `on_deactivate()` (any module switch) left `_scanning`
  True and the completion counter unreachable: progress bar up, status stuck on
  "Scanning...", Scan button disabled, `_do_scan*` returning early, for the life
  of the process. `_cancel_all()` on both tabs now resets that state and clears
  `_scanned`, and a `SCAN_WATCHDOG_MS` timer (5 min, against a 35.6s worst
  measured sweep) is the backstop for any other lost completion. **Cancel also
  has to be READ**: `Worker.cancel()` only sets a flag, so the scan loop checks
  `worker.is_cancelled` between scanners or a cancelled sweep runs to completion
  anyway.
- **These tabs connect CLOSURES to worker signals, not bound methods.** Qt
  auto-disconnects a bound method when its receiver is destroyed and cannot do
  that for a closure, so a scan landing after teardown crashed the app (exit
  -1073740791, not an exception). Guard every such callback with `_alive(self)`.
  It is **`from PyQt6 import sip`, not `import sip`** — the bare form does not
  exist under PyQt6, so eight other modules in this repo have widget-lifetime
  guards that silently fall back to "always valid" and protect nothing.
- **A scan never shells out to an image-servicing tool.** `scan_winsxs_cleanup`
  ran `Dism /Online /Cleanup-Image /AnalyzeComponentStore`: 0.03s unelevated
  (DISM refuses — which is exactly how it hid), **25.03s elevated**, holding the
  Windows servicing lock, returning 0 items. That was 70% of the System Junk
  tab. `tests/test_cleanup_no_servicing_lock.py` runs every scanner with
  subprocess patched and fails if one does it again.
- **`min_age_default` tests the DIRECTORY's own mtime**, so an age floor on a
  folder hides everything inside it after any write. `package_cache` reported
  0.03 GB as 0 items until its floor came off.
- **Never build a user-data path from `expanduser("~")`.** Documents, Desktop and
  Pictures are redirected once OneDrive Backup is on: `~\Desktop` and
  `~\Pictures` DO NOT EXIST here. Use `known_folders.known_folder()`
  (`SHGetKnownFolderPath`). Fixing that took `scan_old_files` from 8 items to
  11,626 and `scan_large_files` from 56.7 GB to 178.4 GB. The trap that comes
  with it: anything that OPENS a file in those folders hydrates a Files
  On-Demand placeholder — `known_folders.is_cloud_placeholder()` answers from
  the file attributes without opening.
- **`%FIXED_DRIVES%` in a catalog path** expands to one path per fixed volume
  (`drives.py`). Only two of 537 scanners had ever looked past C:, and they were
  duplicates of each other; `E:\temp` held 20.36 GB nothing could find. Fixed
  volumes only — never removable or network.
- **`scanners_for(category, present_only=True)`** is what the tabs use: 74 of
  460 specs match anything on a given machine (comms 0/35, dev 8/121). The
  catalog still defines all of them; filtering is a display and scan decision,
  never a deletion.
- **`scan_cache.cached_scan(fn, min_age)`**, not `fn(min_age_days=...)`, from any
  tab. Overview, System Junk and Quick Cleanup all re-walk the same trees;
  %TEMP% here is 47.36 GB / 100k entries / ~3s per walk. 60s TTL, keyed on
  `__name__` (the catalog builds a fresh callable per tab, so identity never
  hits), copies its ScanItems (the tabs mutate `selected`), and `delete_items()`
  invalidates it.
- **Drivers and virtual disks never enter the checkbox tree.** `delete_items`
  deletes PATHS. Removing a `DriverStore\FileRepository` folder directly is how
  a machine loses a driver it still believes it has, so superseded packages get
  their own panel (`tabs/_driver_panel.py`) and `pnputil /delete-driver` behind
  a confirmation. Virtual disks stay `selected=False`, split by whether a
  hypervisor that could open them is installed at all — `E:\VMs\Ubuntu` is
  11.05 GB of orphan here, because VirtualBox was uninstalled and left it.
  Check for the PRODUCT, never for a file Windows ships anyway: `wsl.exe`
  exists on every Windows 11 install, so WSL counts only when `wsl --list`
  names a distribution.
- **`pnputil /enum-drivers` prints its HELP TEXT and exits 0 when unelevated**,
  and its output is UTF-16. `driver_store.parse_enum_drivers` returns `None`
  for the refusal, never `[]`, detecting it by the absence of `Published Name:`
  records. Sizing is separate and needs no elevation: the `Active` value under
  `HKLM\SYSTEM\DriverDatabase\DriverInfFiles\<published>`. Five of eleven
  superseded packages here have no such key, so `package_size` returns `None`
  and the report lists them apart — an unmeasurable size counted as 0 quietly
  understates the total. Worth knowing before chasing it again: the 7.13 GB
  store yields **2.2 MB** of superseded packages; the gigabytes are DISM's.
- **`tools/cleanup_reader_sweep.py`** is the cleanup sibling of
  `security_refusal_sweep.py`. "Found nothing" is the CORRECT answer for most of
  this catalog, so it only reports a scanner whose target exists AND has content
  AND which returned nothing. That found three scanners that had looked
  plausible for as long as they existed — one measuring the Win+X folder while
  claiming to measure restore points, one looking where the search index has not
  lived since Windows 8, one offering the whole 7.13 GB DriverStore as a single
  delete. Run it unelevated; it exits non-zero on any finding.

### QuickCleanupModule (`src/modules/cleanup/quick_cleanup_module.py`)

Single-page dashboard with pie chart and auto-refresh. Uses `QuickCleanupTab` from `modules/ui/components/quick_cleanup_tab.py`.

- `_id_map` — maps category IDs to `(scanner_fn, safety)` tuples
- `_adv_scanner_map` — maps advanced category IDs
- `_do_scan_all()` — runs both main and advanced scanners in parallel via Workers
- `_toggle_advanced()` — reveals/hides the advanced panel
- `_build_one_click_panel()` — one-click maintenance actions (Flush DNS, Clear Event Logs, Compact WinSxS, Rebuild Icons, WU Deep Clean, Network Repair)
- `get_refresh_interval()` returns `60_000` (60s auto-refresh)
- `on_deactivate()` calls `stop_auto_refresh()` and `cancel()` to stop timers and workers

### DebloatModule (`src/modules/debloat/debloat_module.py`)

`DebloatModule` is a `CompositeModule` with two children: `DebloatToolsModule`
(the three tabs below, `requires_admin = True`) and Store Apps.

`DebloatToolsModule` is a 3-tab module in `ModuleGroup.OPTIMIZE`:
- **Apps tab** — scans installed UWP apps via `Get-AppxPackage` (using `debloat_scanner.py`), shows table with checkboxes, Apply Selected / Apply All Safe. Protected apps (Store, Terminal, Get Help, Calculator, Notepad, Alarms) highlighted orange and require confirmation before removal.
- **Privacy & Telemetry tab** — loads tweak definitions from `privacy.json`, `telemetry.json`, `services.json`, `network.json`; shows status (Applied/Not Applied) per tweak; preset filters (Light, Full, Privacy-Focused, Custom)
- **AI & Navigation tab** — loads `ai_features.json` and `navigation.json`; same UI pattern

Restore points created via `BackupService` before any apply operation. TweakEngine detects status for every step type, and for `command`/`script` tweaks that carry a `detect` block — see **Tweak System** below for the five-value status vocabulary.

### PerfTunerModule UI Pattern (`src/modules/performance_tuner/perf_tuner_module.py`)

Checklist-style table with 5 columns: ☑ Select | Name | Category | Risk | Status. Per-row Apply button. Preset buttons (Light, Aggressive, Custom) at top. This is the reference UI pattern for modules that present a list of togglable items.

### QuickFixModule (`src/modules/quick_fix/quick_fix_module.py`)

Uses `_FixCard` widget subclasses for each fix. Cards run in background Workers. `QuickFixModule._workers` (plural, on the module) tracks all workers. Individual cards track `self._worker` (singular) for cancellation. `_FixCard` does NOT have a `_workers` list.

### Tweak System (`src/modules/tweaks/`)

JSON definition files in `src/modules/tweaks/definitions/` define registry/script tweaks. Each entry has `steps[]` with one of these types:

| Step type | Fields | What it does |
|-----------|--------|--------------|
| `registry` | `key`, `value`, `data`, `kind` | Sets a registry value via `winreg` |
| `registry_delete` | `key`, `value` | Removes a value (some behaviour is only off when the value is *absent*, not 0) |
| `service` | `name`, `start_type` | Changes service startup type via win32service |
| `command` | `cmd` | Runs a shell command via `subprocess.run` with `CREATE_NO_WINDOW` |
| `script` | `command`, `revert_command` | Like `command`, with an explicit revert string |
| `appx` | `package` | Removes a UWP app via `Get-AppxPackage \| Remove-AppxPackage` |
| `scheduled_task` | `task_name` | Disables a scheduled task (`task_name` MUST start with `\`) |

`tweak_engine.py` applies tweaks via `TweakEngine.apply_tweak()` and detects state via
`TweakEngine.detect(tweak) -> DetectionResult(status, reason, steps)`.
`detect_status()` still returns just the string, for the Qt signal.

**Status vocabulary — five values, and "unknown" now means what it says:**

| Status | When |
|--------|------|
| `applied` | every applicable step is in place |
| `not_applied` | none are — *including when the key or value does not exist*, which is Windows sitting at its default |
| `partial` | some steps in place, some not |
| `not_applicable` | nothing to do here: the `applies_to` gate fails, or the targeted service / scheduled task / appx package is not on this machine |
| `unknown` | we genuinely could not find out (access denied, or a `command` step with no `detect` block) |

Every result carries a `reason` — the sentence the row's tooltip and the details
panel show. A bare "Unknown" with nothing behind it is the bug this design exists
to prevent; `test_command_unknown_still_explains_itself` pins that.

**Two things are easy to get wrong here:**

- **A missing registry key is `not_applied`, not `unknown`.** It was the latter for a
  long time, which made most of the Network tab read "? Unknown" on a clean install.
  Only `ERROR_ACCESS_DENIED` (5) produces `unknown`; `ERROR_FILE_NOT_FOUND` (2) is a
  definite answer. Set `"absent_means": "applied"` on a step when Windows' default
  already *is* the desired state.
- **`service_exists()` / `scheduled_task_exists()` return `Optional[bool]`.** `False`
  means not installed (→ `not_applicable`); `None` means the query itself failed
  (→ `unknown`). Collapsing the two tells the user a service is absent when it is
  merely unreadable.
- **A revert undoes the KEY as well as the value.** `StepRecord.key_created`
  records, at apply time, that `CreateKeyEx` made the key — `BackupOutcome.ok`
  with `exported` False means there was no key to export. `revert_step` then
  removes it, but only when it deleted the value, only when the key is empty
  *right now*, and never a parent. It is not inferable at revert time: a key's
  mere existence can be the whole point (`pol_parser` meets key-only policy
  records on this machine), so deleting an empty key the app did not create
  would be a change dressed up as an undo. Without this, an elevated LLMNR
  round-trip reported "back to exactly what it was" while leaving
  `HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient` behind, empty.
- **`backup_registry_key` returns a `BackupOutcome`, and `ok` is not
  `exported`.** `reg export` answers rc=1 both for a key that is not there and
  for one it was refused (measured: a missing key and `HKLM\SECURITY` are
  indistinguishable by return code), so the key's existence is checked
  separately. "Nothing to back up" is fine; "no way back" is logged at ERROR
  naming the key about to change.

**`applies_to`** (optional, per tweak) gates on the machine, evaluated by
`os_context.py`: `min_build` / `max_build` (a build number or an alias like `"23H2"`,
`"WIN11_24H2"`), `os`, `editions`, `not_editions`, `arch`, `requires_gpedit`
(→ not applicable on Home/S, where policy values are written but ignored),
`client_only`, `server_only`.

**`detect`** (optional, per tweak) overrides step-derived detection — this is how a
`command`/`script` tweak becomes checkable at all, and
`test_command_tweaks_declare_how_they_are_detected` requires one on every such tweak.
It takes a probe object or a list of them, using any step type plus:
`registry_key_exists`, `registry_key_absent`, `powershell` (`script` +
`applied_when`, must be read-only — it runs on every sweep), `file_exists`,
`file_absent`, and `none` (+ `reason`, for repair actions with no state to read).

**`os_context.py`** caches the build, edition, architecture, per-service existence,
per-task status and the whole `Get-AppxPackage` list for the process lifetime.
`get_os_context()` is the singleton; `reset_os_context()` exists for tests.

**`TweakEngine.detect_many()`** runs a sweep across a thread pool. The cost is
dominated by process launches (schtasks, powercfg, PowerShell), which block with the
GIL released — this took the 696-tweak sweep from ~17 s to ~6 s.

Definition files (20 categories, ~700 tweaks; each file's `category` field must match
the tab name in `_CATEGORY_FILES`, enforced by `test_tweak_definitions.py`):
`privacy`, `performance`, `telemetry`, `ui_tweaks`, `services`, `gaming`, `security`,
`network`, `ai_features`, `navigation`, `explorer`, `taskbar_start`, `power`, `input`,
`updates`, `defender`, `browser`, `storage`, `multimedia`, `remote`.
Plus `debloat.json` (120+ `appx` removals) and `definitions/builtins/*.json` (presets).

`tests/test_tweak_definitions.py` validates every file structurally — unique ids, hive
names, DWORD-vs-SZ data types, absolute scheduled-task paths, known `applies_to` keys.
Run it after touching any definition file; it is the only thing that reads them.

`tools/tweak_refusal_sweep.py` is the behavioural counterpart, and the sibling
of `tools/security_refusal_sweep.py`: it detects all ~700 tweaks against the
real machine and asks which of them state a verdict on top of a read that was
refused. Run it **unelevated** — elevated there is nothing to refuse. Audited
2026-08-29: 696 tweaks, none. `_aggregate` may conclude "not in place" while
one step is unreadable (a step that is definitely missing settles it), and it
appends "(N step(s) could not be checked)" when it does — disclosed
uncertainty is fine, a swallowed refusal is the bug.

**Apps tab** (`AppManagerTab` + `app_catalog.py`) — three lists: installed AppX
packages, installed programs (Win32/winget), and the catalog of installable
apps. The two removal queues are separate (`_remove_queue` vs
`_remove_winget_queue`) because they are removed by different commands, and
`queued_changes()` reports them under `remove` and `remove_winget`.

- **A removal is verified, never assumed.** `Get-AppxPackage 'X' |
  Remove-AppxPackage` and `winget uninstall` both exit 0 while removing
  nothing, so `remove_appx`/`remove_app_winget` require positive evidence both
  ways — listed before, not listed after — and a list that could not be *read*
  is never read as "it is gone".
- **"Still installed" does not mean the removal failed.**
  `Microsoft-Windows-AppXDeploymentServer/Operational` records every deployment
  attempt and is readable unelevated. `JAMSoftware.TreeSizeContextMenu` is
  removed successfully and then re-added ~10s later from TreeSize's own
  install directory, so `remove_appx` consults that log and reports the path
  that put it back. Generalise it: when an API's answer is unsatisfying, read
  the log or file Windows itself writes.
- **`winget list` must be parsed by COLUMN, from the header offsets.** 24 of
  139 rows here carry an id with spaces (`ARP\Machine\X64\AMD Catalyst Install
  Manager`), and names carry their own dots ("7-Zip 26.02 (x64 edition)"). The
  old "first whitespace token containing a dot" heuristic returned 66 junk ids
  out of 126 — `.NET`, `Drv_3.00.0045`, bare version numbers — and those ids
  are what marks a catalog entry already installed.
- **`MSIX\…` rows are AppX packages under another name** and are excluded from
  the desktop list, or the tab offers two different removals of one thing.
  `ARP\…` and `MSIX\…` are both winget's internal handles, never real ids.
- **Uninstall by `--id … --exact`**, never a bare positional query: a query
  matches id, name *or* moniker, so it can hit several apps and refuse — and an
  ARP id contains spaces.
- Plenty of apps are installed under an ARP id rather than the id the catalog
  knows (Chrome is `ARP\Machine\X86\Google Chrome`, with no `Google.Chrome` row
  anywhere), so the catalog's "Installed" marker also matches on an **exact**
  name. Prefixes would mark Notepad++ installed because Notepad is.
- `populate_installed*` sets a check state on every row and each one fires
  `itemChanged`; `_populating` is what stops a refresh queueing every app on
  the machine for removal.

### Security Dashboard (`src/modules/security_dashboard/`)

149 actionable controls over 165 `check_*` readers, in seven category tabs.
`security_reader.py` reads, `catalog/` describes each control (steps, risk,
`read_value`), `staging.py` collects pending changes, `applier.py` writes via
`TweakEngine`, `reverting.py` undoes, `profile.py`/baselines compare.

**The rules here are all one rule: a refusal is not an answer.**

- **`SecurityControl.read()` returns `None` for "we could not look", and it is
  never collapsed into `False`.** A reader signals that with
  `available: False`; without it, `read()` falls through to `read_value`, whose
  tests are things like `"Protected" in status` — and "Requires administrator"
  does not contain "Protected", so a refused BitLocker read answered `False`,
  i.e. "your system drive is NOT encrypted". `read()` is what staging,
  baselines and profiles compare, so this is not only a card label.
  `tools/security_refusal_sweep.py` asks all 149 controls whether any of them
  answers with a value after being refused — **run it unelevated after
  touching a reader**; elevated there is nothing to refuse.
- **Never treat `rc == 0` as success.** `snapshots._looks_refused(rc, out, err)`
  exists because `Get-BitLockerVolume`, `Get-Tpm`, `dism` and `netsh` all
  refuse while exiting 0, some writing the reason to *stdout*.
- **Being refused costs real time, so do not buy a refusal twice — or once, if
  it is certain.** The `MicrosoftVolumeEncryption` and `MicrosoftTpm`
  namespaces take a fixed ~5s to deny an ordinary user;
  `_wmi_namespace` caches a denial per namespace AND skips those two outright
  when unelevated, since a process cannot gain elevation while it runs.
  `root\Microsoft\Windows\Defender` answers unelevated and is deliberately not
  in that set. This took Device & Boot from 16.79s to 1.26s.
- **A write is verified by reading the control back, after dropping the
  snapshot caches once per batch** — `BackupService` reporting success is not
  evidence the machine moved.

Tools (all real-machine, none of them press a button unless asked):
`security_catalog_check.py` (+`--apply <id>` for one full round-trip,
`--compare a.json b.json` to diff an elevated run against an unelevated one),
`security_refusal_sweep.py`, `security_pane_timing.py`, `security_apply_dryrun.py`,
and the `*_render.py` trio. `Start-Process -Verb RunAs` cannot redirect output,
so anything elevated goes through a `.ps1` wrapper that writes its own log.

### UpdatesModule (`src/modules/updates/`)

5-tab module in `ModuleGroup.TOOLS`, `requires_admin = True`. This installs updates — it is a different thing from `DiagnoseModule`'s embedded Windows Update *log* viewer.

- **App Updates tab** (`_AppUpdatesTab` in `updates_module.py`) — winget-based via `winget_updater.py`. Live filter box, "Package Details" (`winget show`), "Add to Blocklist", post-update verification. "Update All" is routed through the same per-item loop as "Update Selected" rather than one opaque `winget upgrade --all` call, so progress/pass-fail is per-package.
- **Windows Updates tab** (`_WinUpdatesTab`) — WUA COM via `windows_updater.py`'s `fetch_pending_updates()` / `install_updates_iter()` (per-item downloader/installer calls, real progress). Hide/show-hidden support, an opt-in restore point before install (`core/system_restore.py` — a real `Checkpoint-Computer`, NOT `BackupService.create_restore_point`, which is this app's own separate revert-log mechanism), post-install verification, WU HRESULT decoding (`core/wu_error_codes.py`).
- **Microsoft Store tab** (`_StoreUpdatesTab`, `store_updates_tab.py`) — renders the `source == "msstore"` subset of the *same* winget scan the App Updates tab already ran, dispatched via `UpdatesModule._on_app_updates()` → `_store_tab.set_updates()`. Does **not** run its own winget scan. "Verify Store Updates" triggers `core/mdm_store_trigger.py` (MDM CIM class — needs `COMWorker`).
- **Run All tab** (`_RunAllTab`, `run_all_tab.py`) — runs checked stages (wu/winget/store/cleanup/dism) sequentially in one worker, then writes run history and an HTML report.
- **Settings tab** (`_UpdateSettingsTab`) — blocklist editor, verify/restore-point/driver toggles, and the Task Scheduler wiring for `--unattended` mode.

**Shared stage logic** (`stage_runners.py`): `run_wu_stage` / `run_winget_stage` / `run_store_stage` / `run_cleanup_safe_stage` / `run_dism_stage`, each `(app, log_fn, is_cancelled_fn) -> dict`. Used by *both* `_RunAllTab` (threaded, `is_cancelled=lambda: worker.is_cancelled`) and `unattended_runner.py` (headless, `is_cancelled=lambda: False`) — extend this file rather than duplicating stage logic in either caller. `normalize_stage_data()` flattens the per-stage result dicts into the shape `report_generator.py` / `history_writer.py` expect.

**Blocklist** (`core/blocklist.py`): `is_blocked(name, id, patterns)` is fnmatch-based — a pattern with no `*` is auto-wrapped as `*pattern*`. Stored at config key `updates.blocklist_patterns` (`list[str]`), not a separate file. `add_pattern(app, pattern)` backs the "Add to Blocklist" button on both the winget and WU tabs.

**Unattended mode**: `python src/main.py --unattended --stages wu,winget,cleanup` — headless, no `QApplication`/`MainWindow`; calls `pythoncom.CoInitialize()` explicitly since there's no `COMWorker`/Qt event loop available. Requires admin — exits 1 immediately if not elevated. The Settings tab's "Create/Update Task" button wires this into Task Scheduler as `WinClientTool_UnattendedMaintenance` (`/rl HIGHEST`, `/sc DAILY /mo <N>`). The older winget-only `WinClientTool_UpdateCheck` task (`_save_legacy`) is kept as-is for existing users — don't merge it into the new one.

**Worker tracking**: every tab here is a `QWidget` (not `BaseModule`) with its own `self._workers: list` and `_cancel_all()`, per the general widget-subclass rule below. `UpdatesModule.on_deactivate()` / `on_stop()` call `_cancel_all()` on all four stateful tabs explicitly — `BaseModule.cancel_all_workers()` only covers workers created directly on the module itself, not on child tab widgets.

### Monitor Control (`src/modules/monitor_control/`)

One tab (`ModuleGroup.SYSTEM`, `requires_admin` with `read_only_unelevated`)
over the displays: topology, refresh rates, the four Win+P arrangements,
connect/disconnect, DDC/CI brightness/contrast/input, the audio a monitor
carries, and saved display profiles. Only `monitor_module.py`,
`_arrangement_canvas.py` and `_screen_overlay.py` import Qt — the same split
`scan/`+`store/` keep in TreeSize, which is why the engines test headless.

**Every change goes through `_apply_guard`**: snapshot, apply, then a
15-second countdown that puts it back unless someone confirms. The failure
being designed around is a mode the monitor cannot show — the screen goes
dark and the control that would undo it is on that screen — so doing nothing
has to be the safe answer, and doing nothing reverts.

Rules here, each one measured:

- **A `MonitorView` is paired to a DDC handle by GDI device name, never by
  description.** `GetMonitorInfoW`'s `szDevice` matches `view.device_name`
  exactly and one-to-one. `find_monitor(monitors, view.name)` cannot work:
  Windows calls the Gigabyte here "Generic PnP Monitor" while its view is
  named "MO27Q28G". Use `find_monitor_for_device` / `probe_device` /
  `set_*_for_device`.
- **A `PhysicalMonitor` handle must not outlive its `open_monitors()`
  block.** `probe_device` returns a capability with `monitor=None` for that
  reason; a tab refreshing on a timer would otherwise call into memory
  `DestroyPhysicalMonitors` already took back. Every write re-finds its
  monitor.
- **A DDC write costs ~0.28s and a re-probe ~1.5s**, and `build_views()` is
  ~3.4s for three monitors. So writes run on a worker, fire on
  `sliderReleased` (never `valueChanged`), and the card is NEVER re-probed
  after a write — `WriteResult` already carries the applied value and
  whether the read-back agreed. Report `verified` as three outcomes: False
  means the monitor took the call and ignored it, None means the read-back
  itself was refused.
- **Sliders run over the monitor's OWN maximum**, never an assumed 0..100,
  and a maximum of 0 is refused upstream rather than shown as a range.
- **Input source needs the countdown and brightness/contrast do not** — the
  slider that undoes those stays on screen. The input confirm must land on a
  screen OTHER than the one being switched
  (`choose_confirm_screen_avoiding`): the GPU is still driving that panel so
  Qt still lists it, while it is showing another machine. **Qt names screens
  by MODEL** (`S2719DGF`), not by device path, so match a view to a QScreen
  by name with a geometry fallback.
- **`0x10000000` in an audio endpoint's `DeviceState` is `DEVICE_STATE_HIDDEN`**,
  the flag `IPolicyConfig::SetEndpointVisibility` toggles — measured, six
  round trips. It leaves the endpoint ACTIVE and merely invisible, so
  `state` cannot answer "is this on?" and `is_hidden()` is a separate
  question. `IPolicyConfig` needs the full `{0.0.0.00000000}.{guid}` id; the
  registry enumerates by the trailing guid alone.
- **A profile's identity is the EDID, never `\\.\DISPLAYn`** — that is a
  position in a list, and the CCD target id and the device-path UID are both
  the adapter output, not the panel. The manufacturer id at EDID bytes 8-9 is
  **big**-endian and the product code at 10-11 is **little**-endian.
- **EDID descriptor strings are not always newline-terminated.** The
  Gigabyte here fills all 13 bytes and ends with `\x00`, where the spec says
  0x0A padded with 0x20. `str.strip()` does not remove NUL, so the serial
  carried one into the identity key, into profile JSON and into every
  comparison. Terminate on both, and drop control characters.
- **Window layout replays `SetWindowPlacement`, never `MoveWindow`.** A
  maximised window has two rectangles — the screen one and the one it
  returns to — and only `WINDOWPLACEMENT` carries both plus `showCmd`.
  `MoveWindow` takes one rect and no state, so there is no rect that makes
  it correct. Snapped windows are the same problem: Windows records no
  "snapped" flag, and the only signal is `rcNormalPosition` disagreeing with
  the on-screen rect.
- **A capture costs ~4ms, but a capture taken AFTER a topology change is
  worthless** — Windows has already piled the windows onto the surviving
  display. `monitor_module._topology_changed` is what keeps the screen
  signals' refresh from overwriting the only good record.
- **Connect/disconnect skip the revert countdown entirely.** `_do_toggle_monitor`
  calls `dw.set_target_active` directly, not through `_guarded`. Both directions
  are safe without one: connecting only adds a display, and
  `can_set_target_active` already refuses the one unsafe disconnect (turning
  off the last active display) before the write runs — Windows reassigns
  primary to the survivor on its own when the one turned off was primary
  (measured). The countdown is friction with nothing behind it for a change
  that can never leave a screen with no way back — same reasoning as
  brightness/contrast.
- **`SetDisplayConfig(0, NULL, ..., SDC_TOPOLOGY_EXTEND)` can get stuck
  refusing ERROR_INVALID_PARAMETER, and `DisplaySwitch.exe` — what Win+P
  itself invokes — refuses identically.** Measured 2026-09-04: heavy CCD
  churn (repeated mode/topology changes, DDC probes) left one target cloned
  onto the same VidPn source as another rather than extended, and neither
  the auto-topology flag nor the OS's own Win+P recovery could resolve it.
  `set_target_active`'s `active=True` path is partly why — it flips
  `PATH_ACTIVE` on the FIRST matching path entry for a target id, which can
  be the same source another target already occupies, silently re-cloning
  rather than extending. GDI (`EnumDisplayMonitors`) is the ground truth for
  what is actually on screen when CCD's story stops matching it — a target
  CCD reports `active=True` can still be invisible. No further blind CCD
  writes fixed it once wedged; it needed the hardware itself power-cycled.
  Worth remembering before trusting `active: True` from `dc.query()` alone.
- **A monitor's CCD target id is not stable across the session.** The
  Gigabyte here has been seen as `520` and later `67109384` (`0x04000208`)
  for the same physical panel, with no user action beyond ordinary topology
  changes. Never cache a target id across a refresh; `view_model.build_views()`
  already re-reads it every time for exactly this reason.
- **A refused read is never an answer.** `MonitorIdentity.identified` False,
  `DdcCapability.responded` False, `audio_hidden` None and
  `WriteResult.verified` None all mean "we could not find out", and none of
  them is collapsed into a value. `can_apply` refuses **by name**.

Harnesses, all read-only unless told otherwise: `tools/monitor_control_check.py`
(what the hardware says) and `tools/monitor_revert_check.py` (the countdown
against a real display).

### Driver Manager (`src/modules/driver_manager/`)

Read, export, and (as of this pass) per-package uninstall for installed
drivers. `requires_admin = False` — reading and exporting need no
elevation; only `pnputil /delete-driver` and a backup to a
permission-restricted folder do.

- **`Win32_PnPSignedDriver` only lists devices that HAVE a driver.** A
  device Windows found none for at all — the yellow-bang case — needed a
  second `Win32_PnPEntity` query merged in, or "Driver Manager" never
  showed the machine's actual problem devices.
- **Only an `oem##.inf`-published package can be `pnputil
  /delete-driver`'d.** An inbox driver (`usb.inf`, `hidclass.inf`, ...) has
  no OEM number and nothing to remove this way — offer the action only
  when `published_name_for()` resolves one, never guess.
- **Roll-back-to-previous-version is routed to Device Manager, not
  implemented here.** It needs the previous driver package still cached,
  which this app does not track; Windows itself can check.
- **A driver's signed/unsigned flag is what Windows' own catalog metadata
  claims, not an independent Authenticode check.** Driver signing is
  catalog-based (`.cat` files against a whole package), which is a
  different question than `core/procengine/signatures.py`'s
  single-PE-file check answers — labeled "(as reported by Windows)"
  rather than built as something it is not.
- **One 90-second WMI call for every driver dies with zero partial
  results on a timeout.** Chunked by device class instead, each with its
  own shorter timeout, so a slow class costs that class, not the whole
  refresh.
- **Deduping the chunked merge by `device_name` alone silently collapsed
  distinct devices sharing a generic name.** Windows commonly reports
  several physical devices under an identical string (multiple "USB Root
  Hub" entries, several "Generic PnP Monitor"s) — keys on `device_id` (the
  PNP device instance id, a real per-device unique string) instead,
  falling back to name only for the rare device that reports none.

### Debloat (`src/modules/debloat/`)

126 catalogued apps plus 219 tweaks across two tabs, and four builtin
presets (`tweaks/definitions/builtins/debloat_*.json`) that name and
curate exactly what "Light/Full/Privacy/Custom" mean.

- **Two lists of "what apps exist" must be checked against each other,
  or entries silently vanish.** `debloat_scanner.KNOWN_PACKAGES` (what
  can be detected as installed) and `debloat.json` (the catalog with
  names/categories/removal steps) drifted — 12 real packages including
  Recall and Copilot were catalogued but undetectable.
  `tests/test_debloat_definitions.py` checks this now; keep it green
  after every catalog edit.
- **A preset file with a real name is not the same as a preset that
  runs.** Four fully-curated JSON files sat unreferenced by any Python
  for long enough that the UI's own Light/Full/Privacy/Custom buttons
  reimplemented a cruder version from scratch. If a preset needs
  changing, edit the JSON — `debloat_presets.py` is the only thing that
  should read it.
- **`TweakEngine.detect_status()` is the three-value back-compatible
  shim; `detect()` is the real five-value answer with a `reason`.**
  Debloat's tables used the shim and folded two of the five values into
  "Unknown" — exactly what the engine's own docstring says its design
  exists to prevent.
- **One restore point per apply-click, across three tabs used in one
  sitting, risks the second and third being silent no-ops** against
  Windows' own checkpoint frequency floor. `debloat_session.py` gives
  one Debloat session one restore point, reused across Apps/Tweaks/AI
  within a window, rather than creating a fresh one per click.

### Group Policy Module (`src/modules/gpresult/`)

One sidebar pane (`ModuleGroup.MANAGE`, `requires_admin = False`) over ten
modules. It is a *report*, not an editor — `gpedit.msc` and `rsop.msc` are
buttons on its toolbar. The tree has three roots: **Computer Configuration**,
**User Configuration**, and **Policy Audit** for findings that are this tool
comparing two sources rather than anything Windows said.

| File | Qt? | What it owns |
|---|---|---|
| `rsop_runner.py` | no | runs `gpresult /x`, per scope; also `export_html_report`, `mmc_console_path` |
| `rsop_parser.py` | no | RSOP XML → `RsopResult` / `RsopScope` / `GpoInfo` / `PolicySetting` / `ExtensionStatus` |
| `pol_parser.py` | no | decodes `Registry.pol` (PReg) → `PolFile` / `PolicyValue`; `local_policy_files()` |
| `policy_drift.py` | no | `.pol` vs the live registry → `applied` / `different` / `missing` / `unreadable` |
| `tattooed.py` | no | values in the four policy branches that no `.pol` accounts for |
| `tweak_conflicts.py` | no | this app's own tweaks that write into policy-managed keys |
| `admx_catalog.py` | no | offline (key, value) → friendly name / explain text / gpedit path index over `C:\Windows\PolicyDefinitions` |
| `rsop_snapshot.py` | no | `save_snapshot` / `list_snapshots` / `load_snapshot` / `diff_rsop` |
| `gpupdate.py` | no | runs `gpupdate` streaming, `parse_output` decides the verdict |
| `gpresult_module.py`, `snapshot_dialog.py`, `gpupdate_dialog.py` | yes | the pane and its two dialogs |

**Only the three UI files import Qt** — the same split `scan/`+`store/` keep in
TreeSize, and it is why every rule above is tested headless.

The recurring theme, and the reason most of these files exist:

- **`gpresult /x` unelevated exits 0 and returns half the report.** The XML is
  well-formed and contains only `<UserResults>`; the computer half is refused
  with no error anywhere. Asking for it explicitly (`/scope computer`) is what
  makes the refusal visible — it exits 1 saying "Access Denied". So a missing
  `<ComputerResults>` is reported as **not collected, with the reason**, never
  as "no computer policy". `RsopScope.available` carries that, set from
  whether the element was *present*, not from whether its lists were empty.
- **`gpupdate` does the same thing**: it prints "Computer policy could not be
  updated successfully" in the middle of otherwise normal output and still
  exits 0. The verdict comes from `parse_output` (`STATUS_SUCCESS` /
  `STATUS_PARTIAL` / `STATUS_FAILURE` / `STATUS_CANCELLED`), and `returncode`
  is one signal among several. `/logoff` and `/boot` are **never** passed —
  they perform the logoff or reboot rather than suppressing the prompt; stdin
  is `DEVNULL` so the prompt reads EOF, and the requirement is detected from
  the output wording instead.
- **`Registry.pol` is world-readable even when `gpresult` refuses.** On a
  machine that is not domain-joined, local policy is the only policy there is,
  so `pol_parser` fills in unelevated what the RSOP call dropped — with no UAC
  prompt at all.
- **A refused read is never reported as an absent value.** `policy_drift`
  keeps `missing` and `unreadable` apart, the same distinction `rsop_parser`
  draws between an empty scope and a refused one and `tweak_engine` draws
  between `ERROR_FILE_NOT_FOUND` and `ERROR_ACCESS_DENIED`. Collapsing them
  invents drift and sends someone hunting for it.
- **A scope becoming visible is not the machine changing.** Snapshot the
  report unelevated, then elevated, and a naive diff announces hundreds of new
  settings. `rsop_snapshot` reports that as a *visibility* change
  (`VISIBILITY_GAINED`/`_LOST`) and does not walk the scope's contents.

Smaller traps, each of which cost real debugging:

- **`Registry.pol`'s type and size fields are exactly four bytes, never scanned
  for the `;` delimiter.** A 59-byte record encodes its size as `3B 00 00 00`,
  and `0x003B` *is* the UTF-16 code unit for `;` — a delimiter scan terminates
  the field early and desynchronises everything after it. It parses this
  machine's six records perfectly and corrupts the first real domain policy it
  meets.
- **A key-only `.pol` record is not drift.** This machine carries
  `Software\Policies\Microsoft\Windows\Safer` as REG_NONE with an empty value
  name and zero bytes — it exists to create the key. There is nothing to
  `QueryValueEx`; key existence is the whole question.
- **Most ADMX values are not on the `<policy>` element.** On this machine
  1,365 of the 3,502 policies have no `valueName` attribute — their values come
  from `<elements>` children (2,297 carry one) and from `<item key= valueName=>`
  rows (4,379 of those). Indexing
  only the policy attribute misses most settings anyone actually sets. `<list>`
  has no value name at all, so it lands in a separate key-only index consulted
  only after an exact miss.
- **Tattooed findings are grouped by branch, not listed flat.** Windows' own
  shipped UAC defaults under `CurrentVersion\Policies\System` are technically
  tattooed too, and a flat list reads as far more alarming than it is.
- **The tweak-conflict wording is "can be reverted without warning", not "is
  reverted every 90 minutes".** With default settings the Registry CSE skips
  processing when no GPO version changed, so a hand-written policy value can
  sit untouched for weeks. A tweak writing the *same* data as policy is
  duplicating it (harmless); one writing *different* data is fighting it and
  will lose — reported separately, never collapsed.
- **`rsop_snapshot` listing must not deserialise the payloads.** An elevated
  report is megabytes of JSON. Each snapshot writes a small `.meta.json`
  sidecar and listing reads only those; a snapshot missing its sidecar still
  lists, by falling back to its payload for that one file.
- Nothing in `tweak_conflicts` loads at import time — ~800 tweak definitions is
  not a cost to pay for `import`.

`rsop_snapshot`, `snapshot_dialog`, `gpupdate` and `gpupdate_dialog` are
imported inside the pane's button handlers, so they are listed in
`HIDDEN_IMPORTS` (`pyinstaller_common.py`) — miss one and the frozen build runs
fine until someone clicks Snapshot, Compare or Refresh Policy.

## UI Patterns

**Dark theme** — all modules use `#2d2d2d` backgrounds, `#3c3c3c` cards, `#e0e0e0` text. QSS styles in `src/ui/styles/dark.qss`.

**Error handling** — show errors in-module via `ErrorBanner` widget (`src/ui/error_banner.py`) or `QMessageBox`, not just logs.

**Loading/empty states** — wrap content in `QStackedWidget`; page 0 = content, page 1 = centered "No data — click Refresh" label.

**Confirmation dialogs** — always confirm destructive actions (delete, stop service, disable startup item, toggle Windows features).

**Admin-gated modules** — `requires_admin = True` on the module class. `ModuleRegistry.start_all()` checks `is_admin()` and disables the module if not elevated.

## Auto-Refresh System (`MainWindow`)

`MainWindow._start_module_refresh_timer()` starts a `QTimer` for modules that return a non-None interval from `get_refresh_interval()`. The timer calls `refresh_data()` if available, otherwise `on_activate()`. All timers are stopped in `closeEvent()`. The toolbar has a "Pause/Resume Refresh" toggle.

## Search System (`src/core/search_engine.py`, `src/core/search_provider.py`)

Modules return a `SearchProvider` subclass from `get_search_provider()`. The engine aggregates results from all providers and sorts by relevance. `SearchProvider` is an ABC — subclasses must implement `search(query: SearchQuery) -> List[SearchResult]` and `get_filterable_fields() -> List[FilterField]`. DiagnoseModule additionally has its own unified search bar that iterates its children's providers directly — that one does NOT use `app.search`. Its children's providers ARE registered with `app.search` as well, via `get_search_providers()`; they answer only after a tab has been opened once, because a provider is fed by `LogPane`'s `entries_loaded`.

## Event Bus (`src/core/event_bus.py`)

Pub/sub for loose coupling between modules. Use `app.event_bus.publish("topic", data)` and `subscribe("topic", handler)`. Available topics include module selection, theme changes, cleanup completions.

## Adding New Modules

In `main.py`, import and register:
```python
from modules.<name>.<module_name>_module import MyModule
app.module_registry.register(MyModule())
```

Note: EventViewer, CBS, DISM, WU, Reliability, and CrashDumps are embedded in DiagnoseModule — do NOT register them as standalone modules.

## Important Gotchas

- **This application is English-only, deliberately.** There are zero
  `self.tr()` calls in 92,000 lines and that is a decision, not an
  oversight: it is a single-machine Windows utility with one user, and
  wrapping every string would cost more than it returns. Do NOT half-start
  it — a handful of `tr()` calls scattered through new panes is worse than
  none, because it looks like a translation path that works and does not.
  If that changes, the whole surface goes at once, with a .ts file and a
  lupdate step in the build; until then, write plain strings.

- `sys.stdout` is `None` in onefile windowed mode — guard with `hasattr(sys.stdout, 'isatty')`
- `tempfile` module must be explicitly imported — PyInstaller may miss it
- The walrus operator `:=` inside PyQt `addRow()` calls causes Python 3.12 parser failures — use separate assignment lines
- `win32serviceutil` (pywin32) requires `pythoncom.CoInitialize()` before use in worker threads; use `COMWorker` instead of `Worker` for WMI/COM operations
- Do NOT call UI-creating methods (`_load_data()`, `_setup_table()`) from `on_start` — use `on_activate` instead since `on_start` runs before `create_widget`
- **Silent exception swallowing is forbidden** — `except Exception: pass` and bare `except: pass` silently hide errors from users who see only empty results. Always log with `logger.warning()` or `logger.error()`.
- `QTableWidget.sortOrder()` does not exist in PyQt6 — use `self._table.horizontalHeader().sortIndicatorOrder()` to get the current sort direction
- **Windows 11 quirks**: CBS.log may not exist as a text file — Windows 11 stores CBS data in `CbsPersist_*.cab` files. The CBS tab uses 7z to extract from the most recent cab if the text file is absent. DISM.log similarly may not exist; DISM tab falls back to `Get-HotFix`.
- **`get_refresh_interval()` return type** must be `Optional[int]` — some modules incorrectly declare `-> int:` which breaks type checking
- **Widget subclasses** (`_ScanTab`, `_FixCard`, `_ToolCard`, `_DiskCard`) are `QWidget`, NOT `BaseModule`. They need their own `self._workers: list` and must expose a `cancel()` or `_cancel_all()` method for `on_deactivate()` to call.
- **DiagnoseModule worker tracking**: `self._workers` covers per-tab loader workers; `_active_search` (a standalone `Worker`) must be cancelled separately in `on_stop()`.
- **Timers in card helpers** — if a `_ToolCard` or card helper creates a `QTimer`, store it on the card (`card._auto_timer = timer`) so `_cancel_all_cards()` can stop it on deactivation.
- **`subprocess` with `shell=True` discipline** — every `shell=True` call in this codebase (`tweak_engine.py`'s `command`/`script` steps, `backup_service.py`'s revert commands, `software_module.py`'s uninstall strings, `quick_cleanup_tab.py`'s one-click actions) runs a command string that traces back to a trusted, app-owned source: a bundled JSON tweak definition, a hardcoded string, or a registry-derived value the app doesn't let the user free-type into — never raw user input interpolated into the string. Keep it that way; if a new `shell=True` call needs to include user-typed text, don't string-format it in — pass args as a list with `shell=False`, or shell-quote properly (see `core/system_restore.py`'s `description.replace("'", "''")` for the one existing case that takes user text, going through PowerShell single-quote escaping rather than `shell=True`).
- **Destructive-action confirmations** — `core/confirm.py`'s `confirm_destructive()` is scaffolding for *new* destructive actions (Yes/No, defaults to No, standard wording). Existing modules mostly hand-roll their own `QMessageBox.question()`/`.warning()` calls for this and have NOT been retrofitted — that's intentional, not an oversight; don't "clean up" the existing call sites to use the helper as a drive-by change.
- **`QTabWidget.addTab()` fires `currentChanged` synchronously for the first tab added** — if a hub-style module (see DiagnoseModule) connects `currentChanged` before populating tabs, adding the first tab immediately triggers a real (potentially slow) data load during `create_widget()`, defeating the documented lazy-load-on-`on_activate` pattern. Connect `currentChanged` *after* the `addTab()` loop.
- **Building a real `App()` at pytest collection time can return a `None` `QThreadPool`** — `QThreadPool.globalInstance()` called from module-level test code (executed during collection, before fixtures run) has been observed to come back `None` in this codebase's import graph, while the identical call inside a fixture or test function body (execution time) does not. Collection-time code should only do cheap, non-Qt-threading work (e.g. building a module class list); construct the real `App` in a fixture — see `tests/test_module_smoke.py`.

### Network Diagnostics `_ToolCard` Pattern (`src/modules/network_diagnostics/`)

Each card builder function must assign the return value to a local `card` variable BEFORE any closures that capture `nonlocal card` run:

```python
def _build_foo_card() -> _ToolCard:
    card: Optional[_ToolCard] = None  # pre-declare so closures capture it
    # ... build UI ...
    def _run_foo():
        nonlocal card
        # ... uses card._worker ...
    btn.clicked.connect(_run_foo)
    card = _ToolCard("Title", content)  # MUST be assigned before return
    return card
```

Also add `card is not None` guard in `_cancel_all_cards()`.

### PerfMon Custom Charts (`src/modules/perfmon/perfmon_charts.py`)

Charts are drawn with pure `QPainter` — no pyqtgraph or matplotlib. **PyQt6 coordinate types are strict**: `drawText`, `drawLine`, `fillRect`, and `drawEllipse` require `int` coordinates. Use `int()` casts on all computed positions.

### Cleanup Browser Scanner (`src/modules/cleanup/browser_scanner.py`)

`pathlib.Path.is_file()` does NOT accept `follow_symlinks` keyword argument. Use `entry.is_file()` without arguments.
