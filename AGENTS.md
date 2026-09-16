# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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

**After every portable build, deploy it** — copy `dist/WinClientTool-Portable.exe` over `C:/Users/iorda/OneDrive/1 Personal/Aplicații/WinClientTool-Portable.exe`, the copy the user actually runs day to day. Not optional cleanup — skipping it leaves a stale exe running there. See the `build-portable` skill if present.

**Code-signing** — the unsigned exe is blocked by SmartScreen/WDAC on many machines. After building, sign with `tools/sign_build.ps1` (a `.pfx` + password, or a SHA-1 thumbprint of a cert already in the user's store):
```
powershell -ExecutionPolicy Bypass -File tools\sign_build.ps1 -Pfx C:\certs\wct.pfx -Password "secret"
```

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

### Composite Modules (`src/core/composite_module.py`)

A `CompositeModule` hosts other `BaseModule`s as tabs, so a child module knows nothing about being hosted and can be tested alone. Five composites: `Diagnose`, `Debloat`, `Startup & Boot`, `Network Diagnostics`, `System Management`.

- `on_activate`/`on_deactivate` reach the VISIBLE child only — children stop their own refresh timers in `on_deactivate`
- `on_stop` reaches every started child, including ones whose tab was never opened (so their widgets may not exist — guard teardown code for that)
- Never `removeTab`/`insertTab` on the current index to swap in a lazily-built widget — it re-enters `currentChanged`. Each tab owns a permanent page the child widget is added into.
- The host answers `get_refresh_interval()` with the fastest rate any child wants; `refresh_data()` only ticks the visible child
- **A child re-hosted into a composite can have a latent crash that was never reachable standalone** — the host's auto-refresh timer can tick a tab that was never opened, and `on_start()` can run against a bare fake app in tests. Check with `test_module_inventory.py`'s generic `test_every_composite_child_survives_a_tick_it_was_not_built_for` before assuming a moved module needs no code changes.

`DiagnoseModule` hosts 6 diagnostic viewers (Event Viewer, CBS Log, DISM Log, Windows Update, Reliability, Crash Dumps) as `LogReaderModule` subclasses with a unified search bar in `wrap()`; they are NOT standalone sidebar entries. `DebloatModule` hosts `DebloatToolsModule` (Apps / Privacy & Telemetry / AI & Navigation tabs) plus a separate Store Apps module. `SystemManagementModule` (added 2026-09-16) hosts Scheduled Tasks, Services, and Windows Features. `NetworkDiagnosticsModule` also hosts Shared Resources and Remote Tools alongside its original 4 children.

### Log Reader Modules (`src/core/log_reader_module.py`)

The six diagnostic readers set `provider_class` and implement `load_entries(worker)`. The shared UI is `ui/log_pane.py`'s `LogPane` — table, detail panel, error banner, progress, Refresh. Don't hand-roll another log pane.

### TreeSize Module (`src/modules/treesize/`)

A full TreeSize Professional clone, not a simple folder-size viewer, split into three layers:
- **`scan/`** — the engine: `volume_info.py`, `ntfs_structs.py`, `mft_reader.py` (streams `$MFT` directly — the fast path), `walk_scanner.py` (`FindFirstFileExW` fallback), `filters.py`, `prune.py`, `scanner.py` (engine selection)
- **`store/`** — `node_store.py` (columnar arrays; a node is an `int` index, not an object), `rollup.py`, `aggregates.py`, `duplicates.py`, `search.py`, `snapshots.py`, `compare.py`
- **`ui/`** — ribbon, tree, drive list, view tabs, status bar; `treemap.py` is Qt-free layout

**No PyQt6 in `scan/` or `store/`** — that split is what lets the engine tests run with no display and no elevation. Two engines: elevated + whole NTFS drive uses the MFT reader; everything else uses the walk scanner. `size` and `alloc` are independent columns, never derived from one another.

### Log Viewer Module (`src/modules/log_viewer/`)

A CMTrace-style viewer for ConfigMgr/plain-text logs, its own sidebar module (not a Diagnose tab) since it opens whatever file it's handed rather than a fixed system path. No Qt in `cmtrace_parser.py`/`log_reader.py`/`log_model.py`.

### Monitor Control (`src/modules/monitor_control/`)

Displays, modes, monitor audio, DDC/CI. `requires_admin=True`, `read_only_unelevated=True` — display/DDC reads need no elevation, only audio endpoint writes do. `QueryDisplayConfig` (CCD) is the only trustworthy source of display state; `WmiMonitorID.Active` and `Win32_VideoController.CurrentRefreshRate` both measured lying. Every display change goes through `_apply_guard`: snapshot, apply, 15s revert countdown unless confirmed.

### Security Dashboard (`src/modules/security_dashboard/`)

149 controls over `check_*` readers in seven category tabs. `SecurityControl.read()` returns `None` for "could not check" and this is never collapsed into `False` — a refused BitLocker read must never read as "not encrypted". `_looks_refused()` catches tools (`Get-BitLockerVolume`, `Get-Tpm`, `dism`, `netsh`) that exit 0 while refusing.

### Driver Manager (`src/modules/driver_manager/`)

Read/export/uninstall for installed drivers plus vendor update checking (AMD/NVIDIA/Realtek providers, and a vendor-agnostic Windows-Update-driver-channel path for others like Intel). `requires_admin=False` for read/export; only `pnputil /delete-driver` and restricted-folder backups need elevation. `Win32_PnPSignedDriver` only lists devices that HAVE a driver — a second `Win32_PnPEntity` query catches the yellow-bang case.

### System Health Module (`src/modules/system_health/`)

Findings tab (pending servicing transaction, orphaned scheduled tasks, upgrade headroom — all Qt-free) plus a Servicing tab (DISM ScanHealth, Component Cleanup, and a heavily-gated Reset Base: disabled until a same-session clean ScanHealth, a typed `RESETBASE` confirmation, and a forced restore point before it ever runs). `/ResetBase` must never be reachable from anywhere else in the app.

### Group Policy Module (`src/modules/gpresult/`)

A report, not an editor, over `gpresult /x` + local `Registry.pol` parsing. `gpresult /x` unelevated silently drops the computer half of the report (exits 0) — `RsopScope.available` tracks whether it was present at all, never inferred from empty content. `Registry.pol` is readable unelevated even when `gpresult` is refused, so `pol_parser` fills in what RSOP dropped.

### Cleanup Module (`src/modules/cleanup/`)

**Scanner** (`cleanup_scanner.py`): functions take `min_age_days: int = 0` and return `ScanResult`. `ScanItem` fields: `path`, `size`, `is_dir`, `selected`, `safety` ("safe"/"caution"/"danger") — **no `name` field** (passing `name=` raises `TypeError`).

**8-tab structure** in `cleanup_module.py`:
- `_ScanTab(QWidget)` — reusable tab wrapping a `{fn: label}` scanners dict; exposes `freed_bytes = pyqtSignal(int)` and `auto_scan()` (no-op if already scanned). Tracks **all** workers (scan + clean) in a single `self._workers: list`.
- `_BrowserCleanupTab(QWidget)` — uses `EnhancedBrowserScanner` from `browser_scanner.py`; same signal and worker pattern.
- `_LargeItemsTab(QWidget)` — wraps `_ScanTab` + DISM "Component Cleanup" button in background. Its `_cancel_all()` calls `self._scan_tab._cancel_all()` then `self._dism_worker.cancel()`.
- `_OverviewTab(QWidget)` — table of all groups; "Scan All" parallelises workers; "Clean All Safe" deletes safe items. `_cancel_all()` iterates `self._scan_workers` list calling `w.cancel()` on each.
- `CleanupModule.on_activate()` triggers `_overview.auto_scan()`; `QTabWidget.currentChanged` wires each tab's `auto_scan()` for lazy first-load

### QuickCleanupModule (`src/modules/cleanup/quick_cleanup_module.py`)

Single-page dashboard with pie chart and auto-refresh, now Cleanup's own first tab (not a separate sidebar entry — merged in). Uses `QuickCleanupTab` from `modules/cleanup/components/quick_cleanup_tab.py`.

- `_id_map` — maps category IDs to `(scanner_fn, safety)` tuples
- A preset selector (Light/Thorough/Aggressive/Custom, `cleanup_presets.py`) drives `_do_clean_all_safe`
- `get_refresh_interval()` returns `60_000` (60s auto-refresh)
- `on_deactivate()` calls `stop_auto_refresh()` and `cancel()` to stop timers and workers
- Its one-click maintenance panel was removed — those actions moved into `QuickFixModule` (below), the single home for one-click repairs now

### DebloatModule (`src/modules/debloat/debloat_module.py`)

A `CompositeModule` with two children: `DebloatToolsModule` (3 tabs, `requires_admin=True`) and a separate Store Apps module.

- **Apps tab** — scans installed UWP apps via `Get-AppxPackage` (`debloat_scanner.py`, backed by the shared `core/appx_service.py`), checkbox table, Apply Selected / Apply All Safe. Protected apps highlighted and require confirmation.
- **Privacy & Telemetry** / **AI & Navigation tabs** — tweak-status tables with preset filters, sourced from `debloat_presets.py` reading the real builtin preset JSON files (not a hand-rolled reimplementation)

Restore points created via `BackupService`/`debloat_session.py` (one restore point per session across all three tabs, not one per click) before any apply operation.

### QuickFixModule (`src/modules/quick_fix/quick_fix_module.py`)

The single home for one-click repair/maintenance actions (SFC/DISM moved OUT to System Health; Quick Cleanup's old one-click panel moved IN here). Uses `_FixCard` widget subclasses for each fix, defined in `fix_actions.py`'s `ALL_ACTIONS` list.

- `FixAction` fields: `confirm_text` (shows an Ok/Cancel dialog before running), `precondition` (a callable that can block the run with a status message instead), `long_running` (routes to `core.long_op_pool` instead of the shared global pool)
- A search box filters cards by title/description; a `quick_fix_history.py` run log (mirrors `debloat_history.py`'s shape) backs a "View History" dialog
- `QuickFixModule._workers` (plural) tracks all workers; individual `_FixCard`s track `self._worker` (singular). `_on_done`/`_on_error` compare worker IDENTITY, not just None-ness, against `self._worker` before recording an outcome — a cancelled-then-rerun card must not let the old worker's late result clobber the new run

### Tweak System (`src/modules/tweaks/`)

JSON definition files in `src/modules/tweaks/definitions/` (20 category files, ~700 tweaks) define registry/script tweaks. Each entry has `steps[]` with one of: `registry`, `registry_delete`, `service`, `command`, `script`, `appx`, `scheduled_task`.

`tweak_engine.py`'s `TweakEngine.detect()` returns a real `DetectionResult(status, reason, steps)` with FIVE values: `applied`, `not_applied` (including a missing key/value — that's Windows at its default, a definite answer), `partial`, `not_applicable` (the target service/task/package/edition gate doesn't apply here), and `unknown` only when the read itself was genuinely refused (e.g. access denied). `detect_status()` is a 3-value back-compat shim over that — prefer `detect()` in new code; collapsing to the shim is the exact anti-pattern that made Debloat's own tables read "Unknown" for things Windows was already at its default for.

`definitions/builtins/*.json` are named presets (Balanced, Performance, Privacy, Privacy Focused, Corporate Hardened, Developer Machine, Minimal, Gaming, plus 4 `debloat_*` ones) — a preset's tweak ids are grouped by CATEGORY KEY (e.g. `"power"`, `"telemetry"`), and `TweaksModule._on_load_preset` only applies the ids filed under the key matching a tab's own category. An id filed under the wrong key is silently never applied — this has been a real, repeated bug (fixed across 4 presets already); a test (`tests/test_performance_preset_ids_resolve.py`) checks every builtin preset for it.

## UI Patterns

**Dark theme** — all modules use `#2d2d2d` backgrounds, `#3c3c3c` cards, `#e0e0e0` text. QSS styles in `src/ui/styles/dark.qss`.

**Error handling** — show errors in-module via `ErrorBanner` widget (`src/ui/error_banner.py`) or `QMessageBox`, not just logs.

**Loading/empty states** — wrap content in `QStackedWidget`; page 0 = content, page 1 = centered "No data — click Refresh" label.

**Confirmation dialogs** — always confirm destructive actions (delete, stop service, disable startup item, toggle Windows features).

**Admin-gated modules** — `requires_admin = True` on the module class. `ModuleRegistry.start_all()` checks `is_admin()` and disables the module if not elevated.

## Auto-Refresh System (`MainWindow`)

`MainWindow._start_module_refresh_timer()` starts a `QTimer` for modules that return a non-None interval from `get_refresh_interval()`. The timer calls `refresh_data()` if available, otherwise `on_activate()`. All timers are stopped in `closeEvent()`. The toolbar has a "Pause/Resume Refresh" toggle.

## Search System (`src/core/search_engine.py`, `src/core/search_provider.py`)

Modules return a `SearchProvider` subclass from `get_search_provider()`. The engine aggregates results from all providers and sorts by relevance. `SearchProvider` is an ABC — subclasses must implement `search(query: SearchQuery) -> List[SearchResult]` and `get_filterable_fields() -> List[FilterField]`. DiagnoseModule has its own unified search that iterates per-tab providers — it does NOT use `app.search`.

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
