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

### DiagnoseModule — Hub Pattern (`src/modules/diagnose/diagnose_module.py`)

The `DiagnoseModule` embeds 6 diagnostic viewers as sub-tabs (Event Viewer, CBS Log, DISM Log, Windows Update, Reliability, Crash Dumps) with a unified search bar. These 6 modules are NOT registered as standalone sidebar entries — they exist only within the hub. Their standalone files exist but are not imported in `main.py`.

- Tabs are lazy-loaded on first switch via `_load_tab()`; the `loaded` flag in `_tab_state` prevents re-parsing
- `_build_tab_widget()` is a module-level factory that constructs each tab's UI — new diagnostic tabs should follow this pattern
- Unified search runs as a separate `_active_search: Worker` tracked independently from `self._workers`; `on_stop()` must cancel it explicitly
- Crash Dumps tab requires admin — `_load_tab()` checks `is_admin()` before loading and shows an error banner if not elevated

### TreeSize Module (`src/modules/treesize/`)

Three key files:
- `disk_scanner.py` — `DiskScanner` runs in a background thread, emits batches of `DiskNode` via `signals.batch_ready` every 500 nodes
- `disk_tree_model.py` — `DiskTreeModel` (a `QAbstractItemModel`) receives batches via `add_batch()` on the main thread; must override `sort()` for interactive column sorting
- `treesize_module.py` — wires scanner signals to model mutations; uses `threading.Thread(daemon=True)` for the scanner

`DiskTreeModel.sort()` is required because `QAbstractItemModel` does not implement sorting by default:

```python
def sort(self, column: int, order: Qt.SortOrder = ...) -> None:
    self.layoutAboutToBeChanged.emit()
    # sort self._roots and recursively sort children
    self.layoutChanged.emit()
```

### Cleanup Module (`src/modules/cleanup/`)

**Scanner** (`cleanup_scanner.py`): functions take `min_age_days: int = 0` and return `ScanResult`. `ScanItem` fields: `path`, `size`, `is_dir`, `selected`, `safety` ("safe"/"caution"/"danger") — **no `name` field** (passing `name=` raises `TypeError`).

**8-tab structure** in `cleanup_module.py`:
- `_ScanTab(QWidget)` — reusable tab wrapping a `{fn: label}` scanners dict; exposes `freed_bytes = pyqtSignal(int)` and `auto_scan()` (no-op if already scanned). Tracks **all** workers (scan + clean) in a single `self._workers: list`.
- `_BrowserCleanupTab(QWidget)` — uses `EnhancedBrowserScanner` from `browser_scanner.py`; same signal and worker pattern.
- `_LargeItemsTab(QWidget)` — wraps `_ScanTab` + DISM "Component Cleanup" button in background. Its `_cancel_all()` calls `self._scan_tab._cancel_all()` then `self._dism_worker.cancel()`.
- `_OverviewTab(QWidget)` — table of all groups; "Scan All" parallelises workers; "Clean All Safe" deletes safe items. `_cancel_all()` iterates `self._scan_workers` list calling `w.cancel()` on each.
- `CleanupModule.on_activate()` triggers `_overview.auto_scan()`; `QTabWidget.currentChanged` wires each tab's `auto_scan()` for lazy first-load

### QuickCleanupModule (`src/modules/cleanup/quick_cleanup_module.py`)

Single-page dashboard with pie chart and auto-refresh. Uses `QuickCleanupTab` from `modules/ui/components/quick_cleanup_tab.py`.

- `_id_map` — maps category IDs to `(scanner_fn, safety)` tuples
- `_adv_scanner_map` — maps advanced category IDs
- `_do_scan_all()` — runs both main and advanced scanners in parallel via Workers
- `_toggle_advanced()` — reveals/hides the advanced panel
- `get_refresh_interval()` returns `60_000` (60s auto-refresh)
- `on_deactivate()` calls `stop_auto_refresh()` and `cancel()` to stop timers and workers

### DebloatModule (`src/modules/debloat/debloat_module.py`)

3-tab module in `ModuleGroup.OPTIMIZE`, `requires_admin = True`:
- **Apps tab** — scans installed UWP apps via `Get-AppxPackage` (using `debloat_scanner.py`), shows table with checkboxes, Apply Selected / Apply All Safe. Protected apps (Store, Terminal, Get Help, Calculator, Notepad, Alarms) highlighted orange and require confirmation before removal.
- **Privacy & Telemetry tab** — loads tweak definitions from `privacy.json`, `telemetry.json`, `services.json`, `network.json`; shows status (Applied/Not Applied) per tweak; preset filters (Light, Full, Privacy-Focused, Custom)
- **AI & Navigation tab** — loads `ai_features.json` and `navigation.json`; same UI pattern

Restore points created via `BackupService` before any apply operation. TweakEngine detects status for registry, service, appx, and scheduled_task step types.

### QuickFixModule (`src/modules/quick_fix/quick_fix_module.py`)

Uses `_FixCard` widget subclasses for each fix. Cards run in background Workers. `QuickFixModule._workers` (plural, on the module) tracks all workers. Individual cards track `self._worker` (singular) for cancellation. `_FixCard` does NOT have a `_workers` list.

### Tweak System (`src/modules/tweaks/`)

JSON definition files in `src/modules/tweaks/definitions/` define registry/script tweaks. Each entry has `steps[]` with one of these types:

| Step type | Fields | What it does |
|-----------|--------|--------------|
| `registry` | `key`, `value`, `data`, `kind` | Sets a registry value via `winreg` |
| `service` | `name`, `start_type` | Changes service startup type via win32service |
| `command` | `cmd` | Runs a shell command via `subprocess.run` with `CREATE_NO_WINDOW` |
| `appx` | `package` | Removes a UWP app via `Get-AppxPackage \| Remove-AppxPackage` |
| `scheduled_task` | `task_name` | Disables a scheduled task via `schtasks /change /tn ... /disable` |

`tweak_engine.py` applies tweaks via `TweakEngine.apply_tweak()` and detects state via `TweakEngine.detect_status()` (returns `"applied"`, `"not_applied"`, or `"unknown"`). BackupService creates restore points automatically before applying.

Key definition files:
- `privacy.json` — privacy policy tweaks (47 entries)
- `telemetry.json` — telemetry and diagnostics tweaks (19 entries)
- `services.json` — service disable/enable tweaks (34 entries)
- `debloat.json` — 90+ UWP app removal entries with `appx` step type
- `ai_features.json` — Win11 24H2 AI feature tweaks (Click-to-Do, AI Hub, WSAIFabricSvc)
- `navigation.json` — File Explorer navigation pane tweaks (Gallery, 3D Objects, Home, duplicate drives)
- `definitions/builtins/*.json` — preset profiles (8 existing + 4 debloat presets)

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
