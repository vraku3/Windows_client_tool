# Game Mode upgrade plan (2026-10-29)

**Scope:** The Game Mode module only. No Updates changes in this plan.

**Current state:** ON journals every change before applying it, OFF restores only
the recorded BEFORE state, History keeps the last 100 sessions, and the module
already handles the dual-CCD X3D power-plan rule, protected apps, game
processes, service re-stops while the app is open, and a preview/confirmation
before ON. The remaining gaps are session control, user-owned protection, saved
profiles, and portable history.

## Non-goals

- No background enforcement after the app closes (that needs a service/task and
  is a separate reliability project).
- No automatic game detection or per-game auto-ON (heuristics are unreliable
  and can close work by surprise).
- No network, HDR, refresh-rate or overclock changes; Monitor Control and the
  hardware tools own those.

## Wave 1 - active session controls and an optional restore point

1. Header shows an active-session banner: elapsed time, preset, and the last
   enforcement result. `session.session_age`/`age_text` already exist.
2. **Auto-OFF after N minutes/hours** (setting, default off). The existing
   once-a-minute enforce tick checks the age; when exceeded it runs the normal
   OFF path and reports it, never silently. A long-session warning is shown
   before the limit.
3. **Optional System Restore point before ON** (setting, default off), created
   in the worker before the first change. If it cannot be created, nothing is
   applied (same rule as the Updates WU path). Recorded in the session history.
4. Tests: fake clock/session for the auto-OFF decision; fake restore-point
   factory for success/refusal; no real service or process changes.

## Wave 2 - user-managed protected apps

1. New persisted list `game_mode.protected_patterns` (exe names or title
   substrings) merged with `catalog.NEVER_CLOSE` for close/reopen decisions.
2. "What Game Mode changes" gains an editor: add/remove entries, with the same
   fnmatch/substring semantics and a "matches now" preview. Protected entries
   are never closed, ended or reopened.
3. ON confirmation names the protected apps it is keeping (so it is clear why a
   program stays open). Tests use fake process trees and never touch the real
   machine.

## Wave 3 - saved profiles

1. A profile is a named `Choices` set (services, apps, flags). Save/load/delete
   from a preset row; stored under `game_mode.profiles` with a version.
2. Last-used profile is restored on module start; Recommended/Maximum remain
   one-click presets that populate the same controls.
3. Validation: unknown services/apps/settings are ignored with a warning, never
   applied blindly; a corrupt profile list does not block ON.

## Wave 4 - portable history

1. History tab filters (date range, preset, restore status) and a summary card
   for sessions with restore issues.
2. Export the filtered sessions to CSV and JSON, including per-action labels and
   restore issues; UTF-8 with BOM for Excel.
3. Tests for filtering/export with synthetic history; no real sessions needed.

## Order and verification

- Waves are independent; each lands with its own tests and a build only after
  the behavior is verified against a fake machine.
- Every wave must preserve the existing journal-first rule: OFF never guesses,
  and a crash/kill still leaves a recoverable session.
- A real round trip remains `tools/game_mode_roundtrip.py`, run only when the
  user asks for it on this machine.

## Wave 5 - stop when the game stops (opt-in)

1. The user picks the game executable (or an already-running game process) in
   the Game Mode panel; the choice is stored per user, never inferred from a
   title heuristic.
2. ON records the chosen game's current PIDs and descendants. The once-a-minute
   enforce tick checks whether any are still alive; after a configurable grace
   period (default 5 minutes) with none, it runs the normal OFF path and says
   which game exited. If the game never starts, nothing is auto-restored.
3. Default off. A manual OFF always wins and cancels the watcher.

## Wave 6 - quick controls without the window

1. Tray-menu actions: Game Mode ON (last profile/preset), OFF, and
   Pause enforcement for 15/30/60 minutes.
2. A global hotkey (default unset; user assigns one) sends the same actions
   through the existing singleton, never a second ON/OFF path.
3. Pausing enforcement does not touch the journal or the applied changes; it
   only stops re-stops/wake requests until resumed or the timer expires, and the
   header states that enforcement is paused.

## Wave 7 - app-tree usability and bulk protection

1. Search/filter the running-program tree by name, handle count, risk level and
   protected state; keep the current estimate in sync with what is visible.
2. Bulk actions: **Protect selected** (adds to Wave 2's list), **Select all
   unknown**, **Clear selection**, and **Show only safe to close**. Unknown
   programs remain unticked by default.
3. Protected rows are visually distinct and cannot be ticked for close; the
   confirmation lists them separately.

## Wave 8 - restore verification, retry and notes

1. After OFF, verify services, power plan, tasks, registry flags and reopened
   apps; show a result dialog with a "Copy report" and "Retry failed" action.
   Retry reads the same journal/last-restore evidence, never a new ON.
2. History sessions gain an optional user note/tag, editable in the detail pane
   and included in exports. Notes are free text, capped and never executed.
3. A failed restore remains recoverable after the app closes; the existing
   journal-first rule is unchanged.

## Wave 9 - selected-game priority and power guard

1. Optional: after ON, raise the user-confirmed game process to Above Normal or
   High (user choice, default off). Other processes are not touched beyond the
   existing lower-priority setting; the option is refused for protected or
   unknown processes.
2. Laptop guard: detect AC vs battery. On battery, warn before Maximum, skip
   the maintenance pause by default, and never change the power plan without a
   confirmation unless the user opts in. Desktop behaviour is unchanged.
3. The guard is advisory; it never silently downgrades a user's chosen preset.

## Backlog, considered and deferred

- **Scheduled Game Mode window** (ON at a time, OFF at a time): useful, but a
  global schedule without a game running is surprising; revisit after Wave 5.
- **Per-game profiles**: bind a saved profile to a user-selected exe; natural
  follow-on to Waves 3 and 5, but needs a stable exe identity.
- **Background enforcement service**: would keep services stopped after the app
  closes, but adds a privileged always-on component; separate project.
- **Network/HDR/refresh/overclock changes**: owned by other modules, not Game
  Mode.
- **Heuristic auto-ON from fullscreen/launcher detection**: rejected; it can
  close work without consent.

## Risks and controls

- **Closing work**: unknown apps stay unticked; protected patterns include
  launchers, shell, AV, terminals, AI agents and this app; Wave 7 adds bulk
  protection but never auto-ticks unknown programs.
- **Restore after crash**: every change is journaled before it is made; new
  waves must not add a write path outside the journal.
- **Enforcement loops**: Wave 6 pause and Wave 5 auto-OFF are state on the
  session/journal, checked by the existing tick, not new threads.
- **Real-machine testing**: `tools/game_mode_roundtrip.py` stays the only real
  ON/OFF proof; fake-state tests cover the new decisions.

## Waves 10-29 (extended roadmap)

These are additional, independent waves beyond the first nine. They are ordered
by risk and dependency; none is required before shipping Waves 1-4.

### Wave 10 - live session dashboard
1. A Session tab shows elapsed time, preset, handles/processes before and now,
   CPU/RAM/disk totals, and the last enforcement result.
2. Samples are periodic and bounded (no continuous polling when the tab is not
   visible); GPU data is shown only when a reliable provider exists, never
   guessed.
3. Charts compare "at ON" with "now"; unavailable metrics say so.

### Wave 11 - health audit and drift repair
1. A **Check current state** action verifies services, power plan, Game Bar,
   maintenance flag, disabled tasks and the journal against the expected ON
   state.
2. Drift is listed per item with the recorded value; **Repair drift** re-applies
   only that item through the existing journal path.
3. An unreadable item is unknown, not healthy; repair never invents a value.

### Wave 12 - selective undo while ON
1. From the live session list, reopen one app, start one service, or re-enable
   one task without ending the whole session.
2. The journal entry is updated so a later OFF does not repeat work; the
   session history records the selective undo.
3. Protected items and items another actor already restored are left alone.

### Wave 13 - recovery wizard
1. If OFF leaves restore issues, a wizard lists each failed item, the recorded
   BEFORE value, the error, and Retry / Skip / Copy report.
2. Retry reads the journal only; Skipping marks the issue acknowledged in
   history without deleting evidence.
3. The journal is not deleted until the user finishes or explicitly discards.

### Wave 14 - game library and user binding
1. Scan installed games from Steam, Epic, Xbox and known game folders, with
   executable path, source and last-seen time.
2. The user binds a game to a profile or to the auto-OFF watcher; nothing is
   inferred from a window title.
3. A missing/uninstalled game is shown as unavailable, not silently dropped.

### Wave 15 - armed auto-ON for a bound game
1. For an explicitly bound game, the user can arm **enter Game Mode when this
   game starts**. Detection is by the bound executable path, never heuristics.
2. Arming is session-scoped by default; a persisted arming option is available
   and clearly labelled.
3. If another operation owns the machine, the auto-ON waits/declines with a
   notification; it never fights the operation lock.

### Wave 16 - scheduled Game Mode window
1. Optional weekly windows (days + start/end) enter the chosen profile and run
   the normal OFF at the end.
2. A window with no game running is allowed only after a clear warning; the
   history records why it ran.
3. Overlapping windows and shorter-than-minimum windows are rejected at save
   time.

### Wave 17 - CLI and launcher shortcuts
1. `--game-mode on|off --profile NAME` performs the same journaled actions as
   the UI, exits non-zero on refusal, and prints a short result.
2. Launchers can call the exe with these flags; no window is required.
3. Concurrent CLI/UI calls obey the shared operation lock and report the holder.

### Wave 18 - streaming preset
1. A third preset keeps OBS/Streamlabs, Discord, browser and capture helpers
   open, lowers their priority instead of closing them, and does not disable
   the tasks they need.
2. Encoder-related processes are identified by a curated catalog, not guessed
   from names; unknown capture tools stay unticked and are named in the
   confirmation.
3. History distinguishes Streaming from Recommended/Maximum.

### Wave 19 - low-power / handheld preset
1. A conservative preset stops fewer services, leaves visuals untouched, skips
   maintenance pausing on battery and avoids the Ultimate performance plan.
2. It recommends (never silently applies) the balanced plan on battery.
3. The preset is available only as an explicit choice; no automatic battery
   detection switches presets without consent.

### Wave 20 - notifications and tray summary
1. ON, OFF, auto-OFF and restore-issue outcomes publish a single tray
   notification with a one-line summary and a "Details" action.
2. Notifications respect the app-wide toast setting and are never sent from a
   worker thread directly.
3. Repeated enforcement messages are coalesced; a failed restore is never
   hidden behind a success toast.

### Wave 21 - Game Mode settings export/import
1. Export/import profiles, protected patterns, auto-off/auto-ON settings and
   preset defaults as versioned JSON.
2. Import validates every field, reports ignored items, and never enables an
   auto-ON binding for a path that does not exist.
3. Secrets are not part of this file; only Game Mode configuration.

### Wave 22 - service dependency awareness
1. Build the dependency graph for the services in the plan and stop dependents
   before their dependencies; restart in reverse order.
2. A service required by a protected service is refused and reported instead of
   being stopped blindly.
3. OFF restores only what was actually stopped, preserving the current rule.

### Wave 23 - task ownership and policy guard
1. Before disabling a scheduled task, read its author/principal and refuse
   tasks owned by SYSTEM policy, MDM, security products or the OS.
2. A refused task is listed with its owner; it is not journaled as disabled.
3. The check is conservative: an unreadable owner refuses rather than guesses.

### Wave 24 - maintenance and conflict guard
1. Before ON, check pending reboot, an active Windows Update install, another
   optimisation tool's documented lock, and the app's own operation lock.
2. A blocking condition offers Wait / Cancel / Continue with the unapplied items
   named; nothing is applied before the user chooses.
3. The guard is advisory and never kills another process.

### Wave 25 - session metrics snapshot and benchmark hook
1. Record a bounded metrics snapshot at ON and OFF (handles, processes, RAM,
   CPU totals when available) in history.
2. A "Compare in Benchmarks" action opens the existing Dashboard benchmarks
   with the session timestamps, not a new benchmark engine.
3. Metrics that cannot be read are null, never zero-filled.

### Wave 26 - close grace and retry policy
1. Make the polite-close grace period configurable (default 8 s) and expose a
   per-app "never force-close" flag.
2. Force-close is skipped for anything elevated, protected or without a
   resolvable identity; the report says why.
3. The grace value is stored with the session so OFF/history can explain it.

### Wave 27 - publisher-protected apps
1. The user can protect every running process signed by a chosen publisher
   (e.g., a peripheral vendor) in addition to name patterns.
2. Signature identity must be verified from the file; unsigned or unreadable
   binaries are not protected by publisher.
3. The rule is previewed against current processes before saving.

### Wave 28 - history analytics and retention
1. History gains aggregate views: sessions per preset, median duration, restore
   success rate, most-closed apps, recurring restore failures.
2. Retention is configurable (default 100); trimming is atomic and keeps the
   newest entries.
3. Aggregates say when the sample is too small to be meaningful.

### Wave 29 - resume last session
1. **Resume last** re-applies the previous session's profile, choices and bound
   game without re-running the preview selections from scratch.
2. The normal ON preview and confirmation still appear; resume never bypasses
   the preflight or restore-point setting.
3. A changed/missing app or service is listed as skipped; resume does not
   recreate uninstalled software.

## Re-ranked backlog after Waves 10-29

- **Background enforcement service** — still separate; Waves 10-29 deliberately
  keep enforcement in-process.
- **Network/HDR/refresh/overclock changes** — still owned by other modules.
- **Heuristic auto-ON** — still rejected; Waves 14-15 require an explicit bind.
- **GPU clock/power tuning** — vendor tools own it; no plan here.

## Implementation status (2026-10-29)

All 29 waves are implemented in the working tree:

- New Qt-free engines: `game_config.py` (settings/profiles/protection/schedules/
  export), `game_library.py` (Steam/Epic/user bindings), `session_control.py`
  (auto-off, pause, schedules, priority), `health_audit.py` (drift + repair),
  `history_export.py` (filters, aggregates, notes, CSV/JSON, trim),
  `metrics.py` and `preflight_guard.py`.
- `game_mode_module.py`: settings/session state, auto-OFF and armed auto-ON
  monitor, schedule windows, optional restore point, conflict/battery guard,
  pause enforcement, notifications, tray menu + Ctrl+Alt+G shortcut, new
  Streaming/Low-power presets, app-tree search/protect tools, Session tab.
- `session_tab.py`: session controls, profiles, protection, schedules, health
  audit, selective undo, game binding, export/import, recovery details, resume
  last.
- `history_tab.py`: filters, analytics summary, notes, CSV/JSON export, retry,
  Benchmarks shortcut.
- `session.py`: protected patterns/publishers, close grace/force flag, bound
  game priority boost, selective undo entries, registry `after` evidence, task
  ownership guard, LOW/STREAM preset support, journal-write retry.
- `main.py`: `--game-mode on|off [--game-profile NAME]` headless path.

Deliberate simplifications: Benchmark hooks open the Dashboard rather than
auto-comparing timestamps; recovery shows the last report and retries the whole
journal rather than per-row retry; background enforcement after app exit and
heuristic auto-ON remain out of scope.
