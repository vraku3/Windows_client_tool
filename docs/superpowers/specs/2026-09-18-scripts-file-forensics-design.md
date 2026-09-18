# Scripts Tab + File Forensics Tool — Design

## Goal

Add a new sidebar entry, **Scripts**, as a home for hand-rolled utility
tools going forward. The first tool hosted there is **File Forensics**: a
professional-grade redesign of the user's own `Find-FileCreator.ps1`
script — find a file, see who has it open, and figure out which process
most likely created it — extended into a real live-monitoring tool with
color-coded results, filtering, persistent history, reputation lookups,
and remediation actions, built to the standard the rest of this app holds
its diagnostic tools to.

## Why a new composite hub

`Scripts` is a `CompositeModule` (the sixth, alongside `Diagnose`,
`Debloat`, `Startup & Boot`, `Network Diagnostics` and `System
Management`) with exactly one child today, `FileForensicsModule`. Growing
it later — a second script, a third — is adding another child, the same
way `Startup & Boot` grew from two children to three. `ModuleGroup.TOOLS`,
matching where the app's other hand-built utilities (Registry Explorer,
Environment Variables, Network Extras) live.

## What already exists and will be reused, not reimplemented

This app already has a Qt-free process-forensics engine
(`core/procengine/`) built for Process Explorer, and this tool is built
on top of it rather than duplicating any of it:

| Need | Reused from |
|---|---|
| "What has this file open" | `core.procengine.findref.find()` — the same native NT handle-enumeration Process Explorer's own Ctrl+F uses. Far more capable than the original script's Restart Manager call: it also catches DLLs/modules, and it already reports its own refusals honestly (`FindReport.summary()`). |
| Process start times, for the creator heuristic | `core.procengine.ntquery.system_processes()` — the same bulk syscall the whole process engine is built on (2.6ms for every process on the machine). |
| A creator candidate's exe path / user / elevation | `core.procengine.details.resolve(pid)` — never raises, reports refusals per-field. |
| "Is the likely creator signed" | `core.procengine.signatures.verify_signature(path)` — cached Authenticode check, already distinguishes `NOT_SIGNED` from `COULD_NOT_VERIFY`. |
| SHA256 + VirusTotal lookup | `core.virustotal_client.compute_sha256()` / `VTClient(api_key).check()` — the exact same client `process_explorer_module.py` already uses, gated on the same `virustotal.api_key` config value (no new setup for the user; if it's already configured for Process Explorer, it works here too). |
| Kill the locking process | `core.procengine.actions.end_process(pid)` — the same action Dashboard's own process menu uses. |
| Persistent run history | Same shape as `quick_fix_history.py`: a capped JSON array under the app data dir, `record()` / `recent()`. |

New engine pieces this tool actually needs, because nothing in the app
does these yet:

- **File metadata + owner** — size/times/read-only from `os.stat`, owner
  via `win32security` (`GetFileSecurity` + `LookupAccountSid`), the direct
  equivalent of the script's `Get-Acl`.
- **Creator-time correlation** — the ±N-second heuristic itself, converting
  `ntquery`'s raw FILETIME `create_time` to a comparable `datetime` (same
  formula `procengine/columns.py` already uses for display, reused for math
  here) and scoring every process within tolerance of the file's creation
  time.
- **NT device-path translation** — `findref.find()` matches against handle
  names in NT device-path form (`\Device\HarddiskVolume3\Users\...`), not
  drive-letter paths. A small `QueryDosDeviceW`-based helper converts the
  search target (`C:\Users\...`) to its device-path form once, so it can be
  passed straight into `findref.find()`'s existing substring match — no
  reverse-translation of every result needed.
- **The live folder watcher** — the one piece with no analog anywhere else
  in the app: a native `ReadDirectoryChangesW` watcher (pywin32, overlapped
  I/O) run on a background `Worker`, cancellable via a stop event rather
  than polling or diffing directory listings (which can miss fast-churning
  temp-file creates/deletes).

## Module + file layout

```
src/modules/scripts_hub/
    __init__.py
    scripts_hub_module.py       # ScriptsModule(CompositeModule)

src/modules/file_forensics/
    __init__.py
    file_forensics_module.py    # FileForensicsModule(BaseModule) -- the UI
    history_log.py              # persistent run history, quick_fix_history.py's shape
    engine/
        __init__.py
        file_metadata.py        # size/times/owner/read-only
        device_paths.py         # QueryDosDeviceW translation
        locking_processes.py    # wraps findref.find(), returns who has it open
        creator_heuristic.py    # FILETIME correlation -> ranked candidates
        reputation.py           # SHA256 + VirusTotal, wraps virustotal_client
        analysis.py             # orchestrates the above into one FileAnalysis
        folder_watcher.py       # native ReadDirectoryChangesW, cancellable
```

`scan/`+`store/`-style split: everything under `engine/` is Qt-free and
independently testable with no display, matching TreeSize/Monitor
Control/procengine's own convention. Only `file_forensics_module.py`
imports Qt.

## Data flow

1. **Manual search**: user enters a folder (default `%TEMP%`) and a name
   filter, clicks Search. A `Worker` walks the folder (`os.scandir`,
   optionally recursive), and for each match calls
   `engine.analysis.analyze(path) -> FileAnalysis` — metadata, locking
   processes, creator candidates, signature check on the top creator
   candidate, and (if a VT API key is configured) reputation. Results
   populate the table as they complete, not all at once at the end, the
   same progressive-population pattern `_AppUpdatesTab` already uses.
2. **Live watch**: toggling it on starts a `FolderWatcher` on a `Worker`.
   Each detected file-created event runs through the same
   `engine.analysis.analyze()` path, is appended to the results table AND
   the persistent history log, and — if the app is not the foreground
   window — raises a desktop toast via `core.events.NOTIFY_BALLOON` (the
   same mechanism `updates_module.py` already uses for long-running-op
   notifications). An ignore-pattern list (fnmatch globs, e.g. `*.tmp;
   ~$*`) is checked before analysis even runs, so routine browser/installer
   temp churn doesn't drown out real findings or spam the history log.
3. **History tab**: reads the persistent log, independently searchable and
   filterable (by filename substring, by creator process name, by date
   range) — not just a flat scrollback.

## UI

- **Toolbar**: folder path (with a browse button), name filter, Recurse
  checkbox, Ignore-patterns field, Live Watch toggle, Search button.
- **Results table**: Name, Path, Size, Created, Modified, Owner, **Locked
  By** (blank / process name — amber highlight when non-empty), **Likely
  Creator** (process name, colored by confidence: green = single
  high-confidence match, yellow = ambiguous/multiple candidates, grey =
  none found), **Signed** (green check / red "Unsigned" / grey "Unknown"),
  **Reputation** (VT verdict icon, blank if no API key configured) —
  sortable, and the toolbar's filter box live-filters rows across all
  columns.
- **Detail panel** (selected row): full metadata, every locking process
  (not just the first), every creator candidate within the tolerance
  window with its delta-seconds, and `FindReport.summary()`'s own honest
  disclosure of what could not be inspected. Actions here: **Kill Locking
  Process** (confirmation dialog naming the exact process and PID, per this
  app's destructive-action convention), **Reveal in Explorer**, **Copy
  Path**, **Check Reputation** (if not already run / no API key was
  configured at analysis time).
- **History tab**: the persistent log, its own filter box, and an Export
  button (CSV/JSON) for both the current results table and the history.
- **Empty/loading states**: `QStackedWidget` + `EmptyState`
  ("No matches — try Search or enable Live Watch"), this app's standard
  pattern. `ErrorBanner` for real errors (folder doesn't exist, permission
  denied enumerating it).

## Safety and confirmation

- Killing the locking process is the only destructive action in this tool
  and is always behind a confirmation naming the exact process/PID, never
  a bare button.
- VirusTotal submission-for-analysis (uploading an unknown file's binary)
  reuses `process_explorer_module.py`'s own existing confirm-and-warn
  wording verbatim — this tool never uploads a file without that same
  explicit Yes/No.
- The live watcher only ever reads file metadata and enumerates handles —
  it never writes to or deletes anything in the watched folder.

## Testing

- `engine/` modules are pure-Python/ctypes-and-pywin32, no Qt — each gets
  direct unit tests with subprocess/WinAPI calls mocked, matching
  `test_procengine_findref.py`'s own style (including honest-refusal
  cases: a locked handle enumeration that partially refuses must not be
  read as "nothing has it open").
- `folder_watcher.py` gets a real-filesystem test (create a temp dir,
  start the watcher, create a file inside it via a helper thread, assert
  the callback fires) plus a cancellation test (stop mid-wait must return
  promptly, not hang).
- `file_forensics_module.py` gets the standard module-level tests every
  `BaseModule` gets (`test_module_inventory.py`'s generic composite-child
  checks once it's wired into `ScriptsModule`), plus its own test file for
  the table-population, color-coding, and history-panel behavior with the
  engine layer mocked.
- Manual real-machine verification: run Search against
  `C:\Users\iorda\AppData\Local\Temp` (the user's own request) and confirm
  real results — files, real locking processes where applicable, and a
  plausible creator guess — before calling this done.

## Out of scope for this pass

- Watching multiple folders at once (one `FolderWatcher` per module
  instance for now — a real, deliberate scope cut, not an oversight).
- Cross-process handle closing (as opposed to killing the whole locking
  process) — Windows offers no safe way to close a single handle in
  another process without risking that process's own stability; killing
  the process is the supported remediation here, matching how Process
  Explorer's own "Kill Process" (not "Close Handle") is what Dashboard
  already exposes elsewhere in this app.
