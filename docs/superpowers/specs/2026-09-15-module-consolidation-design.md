# Module Consolidation: Performance Tuner Retirement + Quick Fix/Quick Cleanup/System Health Merge

## §0. Context and rulings

This is a continuation of the tab-merging pattern established by the
Cleanup+QuickCleanup merge (`cleanup-quick-cleanup-merge` memory) and the
4-part Cleanup rework. The user asked to keep merging overlapping tabs,
named Performance Tuner as an example, and explicitly delegated candidate
selection ("I'll leave the suggestions up to you"). A brainstorming
session (this session, same conversation) surveyed all 33 registered
modules for genuine duplication (not just thematic similarity) and
proposed two concrete, evidence-backed candidates, both approved by the
user before this spec was written:

1. **Performance Tuner retirement** — its entire 27-item checklist reads
   registry values already covered by Tweaks' own JSON definitions.
2. **Quick Fix / Quick Cleanup / System Health three-way consolidation** —
   7 exact-or-near-duplicate one-click commands exist across Quick Fix and
   Quick Cleanup's one-click panel.

Two design decisions were made explicit during brainstorming and are
recorded here as rulings rather than re-litigated per-task:

- **Ruling 1**: Quick Fix's card-based UI (`_FixCard`: categorized grid,
  live output console, per-card cancel, reboot-pending banner) is the
  surviving host for one-click repair actions, not Quick Cleanup's flat
  button-row panel — the latter has already grown to 14 rows and scales
  worse. This reverses the assistant's own first-pass suggestion in this
  conversation, made before the two UIs were compared directly.
- **Ruling 2**: for each of the 7 overlapping actions, the underlying
  command implementation that survives is picked per-action on safety
  merit (which one was more recently reviewed / has a documented fix),
  not automatically "whichever module hosts the surviving UI." Quick
  Cleanup's `_clear_print_queue`, for instance, has an unconditional
  spooler-restart fix (`&` not `&&`) that Quick Fix's independent
  `clear_print_queue`/`restart_print_spooler` functions never received —
  Quick Cleanup's command wins even though Quick Fix's card UI is the
  host.

A third finding surfaced only while drafting this spec, not during the
original brainstorm, and is folded in as part of Part B rather than
spun out separately: **`_FixCard` has no confirmation-dialog mechanism at
all** — every action runs immediately on "Run" click, including
`reboot_required` ones. Quick Cleanup's equivalent actions (Network
Repair, Clear Print Queue, Reset TCP/IP, Reset Search, Clear Font Cache,
Flush WU Store) all show a `QMessageBox` confirm first. Migrating them
into `_FixCard` as-is would silently drop that confirmation — a real
regression, not a refactor. Part B's Task 1 fixes this at the mechanism
level before any actions move.

## §1. Goals

- Remove three sources of duplicated/redundant logic (Performance Tuner
  vs Tweaks; Quick Fix vs Quick Cleanup's one-click panel) without losing
  any user-facing capability.
- Reduce the sidebar from 33 to 32 entries (Performance Tuner retires;
  no new modules are created).
- Leave every migrated action provably equivalent or safer than before
  (same command, same or better confirmation, same or better safety
  gating).

## §2. Part A — Performance Tuner retirement

### §2.1 Current state

`PerfTunerModule` (`src/modules/performance_tuner/`, `ModuleGroup.OPTIMIZE`,
`requires_admin=True`) is a 230-line module plus a 27-item hand-rolled
checklist (`perf_checks.py`) with its own inline `detect`/`apply`
functions. Each check uses the same step vocabulary TweakEngine already
understands (`registry`, `service`, `command`), but detection is a crude
3-value scheme (`optimal`/`suboptimal`/`unknown`) instead of TweakEngine's
proper 5-value `DetectionResult` (`applied`/`not_applied`/`partial`/
`not_applicable`/`unknown` — see CLAUDE.md's Tweak System section). This
is the same "shim instead of the real thing" anti-pattern already fixed
once for Debloat's own tables.

### §2.2 Verified overlap

All 27 `PERF_CHECKS` registry values/service names were checked against
every file in `src/modules/tweaks/definitions/` (both the 20 category
files and `definitions/builtins/`):

| PERF_CHECKS id | Exists in Tweaks catalog as | In builtin "Performance" preset already? |
|---|---|---|
| visual_effects_best_perf | `performance.json` | yes (`visual_effects_best_performance`) |
| disable_transparency | `performance.json` | yes |
| disable_animations | `taskbar_start.json` | no |
| menu_show_delay | `ui_tweaks.json` (`DisablePreviewDesktop`... see note) | yes (`instant_menu_response`) |
| disable_aero_peek | `ui_tweaks.json` (`DisablePreviewDesktop`) | no |
| high_perf_power_plan | `performance.json` | yes |
| disable_power_throttling | `gaming.json`, `performance.json`, `power.json` | no |
| disable_hibernate | `power.json` (`power_disable_hibernate_file` — runs the identical `powercfg /hibernate off`; missed by the original audit because it was searched for by registry value name, and this is a `command` step with no registry value to grep for) | no |
| hardware_gpu_scheduling | `gaming.json`, `multimedia.json`, `performance.json` (`enable_hags`) | yes |
| boost_cpu_priority | `performance.json` (`boost_foreground_cpu_priority`) | yes |
| disable_superfetch | `performance.json` | yes |
| disable_prefetch | `performance.json` | no |
| keep_kernel_in_ram | `performance.json` | yes |
| disable_search_indexing | `performance.json` | yes |
| ntfs_disable_last_access | `performance.json`, `storage.json` | yes |
| ntfs_disable_8dot3 | `performance.json` | yes |
| remove_network_throttling | `gaming.json`, `multimedia.json` | no |
| disable_delivery_optimization | `privacy.json`, `services.json`, `updates.json` | yes (under `services`) |
| enable_game_mode | `gaming.json` | no |
| disable_game_dvr | `gaming.json` | no |
| disable_remote_registry | `remote.json`, `security.json`, `services.json` | no |
| disable_diagtrack | `telemetry.json` | no |
| disable_fax | (present in the builtin `services` list) | yes |
| disable_startup_delay | `performance.json` | yes |
| disable_edge_preload | `performance.json` | yes |
| disable_error_reporting | `telemetry.json` (`disable_wersvc` — disables the WER service/scheduled task rather than setting the same `Disabled` registry policy value PERF_CHECKS uses, but reaches the same practical outcome) | no |
| disable_background_apps | `privacy.json` (`disable_background_apps`, name "Disable Background App Access (Global)") — but this is an `HKLM` Group Policy override (`requires_admin: true`), a genuinely different mechanism from PERF_CHECKS' own `HKCU` per-user toggle, not a true duplicate | no |

**Correction, found during Task 1's review**: this table's original audit
searched only by registry value name, which missed `power.json`'s
`power_disable_hibernate_file` (a `command` step with no registry value
to grep for) — a TRUE duplicate of PERF_CHECKS' "Disable Hibernation",
same command, already in the catalog. Only ONE genuine gap remains:
"Disable Background App Access (Global)" needs a new `HKCU`-scoped tweak
(the existing `disable_background_apps` id is `HKLM` Group Policy, a
different mechanism — keeping both is correct, but naming clash needs
avoiding, see below).

26 of 27 have a real, existing tweak id somewhere in the catalog; the
builtin `definitions/builtins/performance.json` preset already bundles 14
of them behind one click via the Tweaks module's existing preset toolbar
(`_preset_combo`, load/apply/export — already built, already in the UI).

**One genuine gap**: "Disable Background App Access (Global)" — the
`HKCU` per-user toggle PERF_CHECKS reads — has no existing tweak; the
`HKCU` mechanism is distinct from the existing `disable_background_apps`
id's `HKLM` Group Policy override. This must be added as a new tweak
definition before Performance Tuner can be retired without losing
capability. "Disable Hibernation" needs no new tweak at all — reference
the existing `power_disable_hibernate_file` id directly.

### §2.3 Changes

1. **Add one new tweak definition**: `disable_background_apps_global` in
   `privacy.json`, a `registry` step
   (`HKCU\Software\Microsoft\Windows\CurrentVersion\
   BackgroundAccessApplications`, value `GlobalUserDisabled`, DWORD 1).
   Its `name` field must NOT read "Disable Background App Access
   (Global)" verbatim — that string is already the existing
   `disable_background_apps` entry's name, and both would otherwise show
   identically in the same Privacy tab with no way to tell them apart.
   Use a name that discloses the real distinction, e.g. "Disable
   Background App Access (Per-User)". Goes through
   `tests/test_tweak_definitions.py`'s existing structural validation —
   no new test infrastructure needed.

2. **Expand `definitions/builtins/performance.json`** to include every
   mapped id from the table above marked "no" except the two gaming ones
   (8 ids: `disable_animations`, `disable_aero_peek`,
   `disable_power_throttling`, `disable_prefetch`,
   `remove_network_throttling`, `disable_remote_registry`,
   `disable_diagtrack`, `disable_error_reporting`), plus
   `power_disable_hibernate_file` (the existing id, standing in for
   PERF_CHECKS' "Disable Hibernation") and the 1 new one from §2.3 item 1
   — 10 additions total, joining the `performance`/relevant sub-list.
   `enable_game_mode`/`disable_game_dvr` are
   deliberately left OUT of the general "Performance" preset (gaming
   tweaks belong to gaming-specific choices, not a blanket performance
   pass) — these remain individually discoverable in the Gaming tab,
   which is not a regression since Performance Tuner's own "Apply All"
   already ran everything indiscriminately including gaming tweaks, and
   folding them into a general preset would be a worse default for a
   non-gaming machine. This is a deliberate, disclosed scope narrowing:
   Performance Tuner's "one giant button" behavior is not preserved
   byte-for-byte, but every individual setting remains one click away in
   Tweaks, which is a strictly more honest UI (a labeled category instead
   of an undifferentiated pile).

3. **Delete `PerfTunerModule`** entirely: remove the
   `src/modules/performance_tuner/` directory, its registration in
   `main.py`, and update `tests/test_module_inventory.py`'s sidebar-count
   test (33 → 32).

### §2.4 Testing

- `tests/test_tweak_definitions.py` (existing) validates the 1 new tweak
  entry structurally.
- New test: the expanded `performance.json` preset's tweak ids all
  resolve to real entries across the category files (a preset naming a
  dead id is a silent no-op when applied — this is exactly the kind of
  defect the tweak-definitions test suite exists to catch, and the
  builtins directory does not currently have equivalent coverage).
- `tests/test_module_inventory.py` sidebar-count assertion updated.
- Full suite run to confirm no import of the deleted module survives
  anywhere (search provider registration, `HIDDEN_IMPORTS`, etc.).

## §3. Part B — Quick Fix / Quick Cleanup / System Health consolidation

### §3.1 Verified overlap

| Action | Quick Fix (`fix_actions.py`) | Quick Cleanup (`quick_cleanup_tab.py`) | Which command survives |
|---|---|---|---|
| Flush DNS | `flush_dns` (`ipconfig /flushdns`) | `_flush_dns` (same) | identical — either |
| Rebuild Icon Cache | `rebuild_icon_cache` (Python: kill explorer, delete cache dir, restart) | `_rebuild_icon_cache` (shell one-liner, same steps) | Quick Cleanup's (already shell-string form `_FixCard` can host via a thin wrapper) |
| Clear Print Queue | `clear_print_queue` (`_stop_service`/`_start_service`, no unconditional-restart fix) | `_clear_print_queue` (`net stop && (del & net start)` — unconditional restart even if delete fails, fixed in a prior review) | **Quick Cleanup's** — Quick Fix's version regresses the documented fix |
| Restart Print Spooler | `restart_print_spooler` (standalone) | *(not present — subsumed into clear_print_queue)* | Quick Fix's — kept as a distinct, lower-impact action |
| Reset TCP/IP | `reset_tcpip` (`netsh int ip reset`, no confirm) | `_reset_tcpip` (same command, has confirm) | same command; **Quick Cleanup's confirm text carries over** |
| Network Reset/Repair | `network_reset` (`netsh winsock reset` + `netsh int ip reset`, no confirm) | `_network_repair` (same two commands, has confirm) | same command; **Quick Cleanup's confirm text carries over** |
| Clear Thumbnail Cache | `clear_thumbnail_cache` (Python glob+remove) | `_clear_thumbnails` (shell del one-liner) | either — functionally identical; keep Quick Fix's Python version (already output-per-file, better console feedback) |
| Windows Update cache reset | `reset_windows_update` (stops wuauserv+cryptsvc+bits+msiserver, clears SoftwareDistribution AND catroot2) | `_flush_wu_store` (stops only wuauserv, clears SoftwareDistribution\Download only) | **Quick Fix's** — strictly more thorough, and matches what its confirm text should say |

`_wu_deep_clean` (DISM `/StartComponentCleanup /SuppressDefaultActions`)
is NOT a duplicate of anything in Quick Fix — it's WU-adjacent but a
different operation (component-store cleanup vs WU cache reset) and
migrates as its own distinct action.

### §3.2 Non-duplicate actions and their destinations

**Stay in Quick Fix, unchanged**: Reset Performance Counters, Restart
Explorer, Clear Prefetch, Clear Recent Files, IP Release/Renew,
Re-register WU DLLs, Check for Updates, Disk Cleanup (cleanmgr).

**Move from Quick Fix to System Health's Servicing tab**: SFC Scan, DISM
RestoreHealth, CHKDSK Schedule. System Health already runs DISM
ScanHealth and Component Cleanup on that tab and explicitly deferred
RestoreHealth in its own spec (`2026-09-15-system-health-design.md` §1
table) — these three belong with the other DISM/servicing operations
under one set of conventions (the same worker/output pattern System
Health's Servicing tab already uses), not scattered across an unrelated
"Quick Fix" grab-bag.

**Move from Quick Cleanup's one-click panel into Quick Fix** (new
"Cleanup" category in `fix_actions.py`, unchanged commands): Clear Event
Logs, Compact WinSxS, WU Deep Clean, Clear Clipboard, Reset Search, Clear
Font Cache, Resize Hibernation. `_resize_hibernation`'s hiberfil.sys
existence gate migrates as a `FixAction`-level precondition (see §3.3
Task 3).

**Stays in Quick Cleanup**: nothing from the one-click panel — the panel
itself is deleted (`_build_one_click_panel` and all `_action_*` methods
removed). Quick Cleanup's tab keeps its scan/clean/preset functionality
untouched.

### §3.3 Changes

1. **Add confirmation support to `_FixCard`/`FixAction`** (fixes the gap
   found while drafting this spec, §0). `FixAction` gains an optional
   `confirm_text: Optional[str] = None` field and an optional
   `precondition: Optional[Callable[[], Optional[str]]] = None` field (a
   callable returning `None` to proceed or a message to show instead of
   running — this is what carries over `_resize_hibernation`'s
   hiberfil.sys existence gate). `_FixCard._run()` shows a
   `QMessageBox.question`-style confirm (Ok/Cancel, default Cancel) before
   dispatching the worker when `confirm_text` is set, and checks
   `precondition()` first when set, showing its message in the status
   label instead of running if it returns non-`None`. This is additive —
   every existing `FixAction` with neither field keeps running exactly as
   it does today.

2. **Retarget the 7 overlapping actions** in `fix_actions.py` per the
   "which command survives" column in §3.1 — replace `clear_print_queue`'s
   body with Quick Cleanup's stop/delete/unconditional-restart logic
   (translated from its shell-string form into the `output_cb`-driven
   Python form the rest of `fix_actions.py` uses, calling `_stop_service`/
   subprocess `del`/`_start_service` directly rather than shelling out to
   `cmd /c` — keeps the actual command semantics, including the
   unconditional restart, without introducing a second shell-string
   convention into this file), replace `reset_windows_update`'s call
   sites' confirm text to name what it actually now also clears
   (`catroot2`), add `confirm_text` to `winsock`/`tcpip`/`network_reset`/
   `wu_reset` matching Quick Cleanup's existing wording.

3. **Add the 7 migrated-from-Quick-Cleanup actions** to `fix_actions.py`
   under a new `"Cleanup"` category, each a thin function wrapping the
   exact same command Quick Cleanup ran (shell string via `_run_cmd`,
   consistent with how `run_sfc`/`run_dism`/`run_chkdsk` already shell out
   in this file), each carrying over its existing confirm text via the
   new `confirm_text` field, and `_resize_hibernation`'s gate via
   `precondition`.

4. **Delete Quick Cleanup's one-click panel**: remove
   `_build_one_click_panel`, `_run_action_command`, every `_action_*`
   method, `_action_buttons`/`_action_status`, and the "One-Click
   Maintenance" section header from `quick_cleanup_tab.py`.

5. **Move SFC/DISM RestoreHealth/CHKDSK into System Health's Servicing
   tab**: add three buttons alongside the existing ScanHealth/Component
   Cleanup/Reset Base row in `system_health_module.py`, reusing
   `servicing.py`'s `DismResult`-returning-worker pattern (add
   `run_sfc_scan()`/`run_restore_health()`/`run_chkdsk_schedule()`
   wrapping the same commands Quick Fix's `run_sfc`/`run_dism`/
   `run_chkdsk` used) rather than copying Quick Fix's own
   `output_cb`-per-line console style — System Health's Servicing tab
   already has its own output-display convention (see
   `system_health_module.py`'s existing Servicing tab code) and these
   three should look and behave like its existing buttons, not like an
   implant from Quick Fix.

6. **Delete `run_sfc`/`run_dism`/`run_chkdsk`** from
   `src/modules/quick_fix/fix_actions.py` and their `FixAction` entries,
   now that System Health owns them.

### §3.4 Testing

- New tests for `_FixCard`'s confirm/precondition mechanism: a
  `confirm_text` action does not run when the dialog is cancelled (mock
  `QMessageBox.exec`), does run when accepted; a `precondition` returning
  a message shows it and never dispatches a worker; an action with
  neither field runs immediately exactly as before (regression guard for
  the additive claim in Task 1).
- Existing `tests/test_quick_fix_module.py` (if present) / new tests for
  each retargeted action's command string (Clear Print Queue's
  unconditional-restart behavior specifically — a test asserting the
  restart step runs even when the delete step is mocked to fail, mirroring
  whatever test already pins this for Quick Cleanup's version, so the
  guarantee survives the migration).
- `tests/test_quick_cleanup_one_click_actions.py` (existing, per
  CLAUDE.md's System Health section) — the one-click-panel tests it
  covers are deleted along with the panel; any assertions about
  `_compact_winsxs`'s safety (no `/ResetBase`) that still apply after the
  action moves to Quick Fix are re-homed to a Quick Fix test file, not
  silently dropped.
- `tests/test_system_health_module.py` extended for the 3 new Servicing
  buttons, following the existing ScanHealth/Component-Cleanup test
  pattern.
- `tests/test_module_inventory.py` — sidebar count unaffected by Part B
  (Quick Fix and System Health both already exist as sidebar entries;
  only Part A changes the count).
- Full suite + a manual grep sweep (`grep -rn "_build_one_click_panel\|_action_buttons"`)
  confirming no dangling references survive the panel deletion.

### §3.5 Additional improvements (approved alongside Part B)

Three follow-up suggestions, raised after the design above was approved,
are folded into this same round since they touch the same files:

1. **Search/filter bar for Quick Fix.** Post-merge it holds ~23 actions
   across 5 categories (System, Network, Windows Update, Print, Cleanup)
   — too many to scan visually without a filter, the same reasoning that
   already gave Debloat, Tweaks, and Store Apps a search box. Add a
   `QLineEdit` above the scroll area in `QuickFixModule.create_widget()`;
   `textChanged` filters on `action.title`/`action.description`
   substring match (case-insensitive) by hiding non-matching `_FixCard`
   widgets, and hides a category's header label too when every card
   under it is hidden (never removes/rebuilds the grid — same "hide,
   don't rebuild" principle Store Apps' `_apply_apps_filter` already
   uses for its table rows).

2. **Run-history log for Quick Fix.** Every other "runs things" module
   (Debloat, Cleanup, Updates, System Health) logs what ran and when to
   its own capped JSON file; Quick Fix is the one that doesn't. Add
   `src/modules/quick_fix/quick_fix_history.py`, mirroring
   `debloat_history.py`'s exact shape: `record(action: str, outcome:
   str) -> None` (appends `{"at": isoformat, "action": title, "outcome":
   "ok"|"error"|"cancelled"}`, caps at 200, writes to
   `%APPDATA%/WindowsTweaker/quick_fix_history.json`) and `recent(limit:
   int = 20) -> List[dict]`. `_FixCard._on_done`/`_on_error`/`cancel()`
   each call `record()` with the right outcome. Add a "View History"
   toolbar button next to the existing reboot-pending banner, opening a
   `QuickFixHistoryDialog` that lists `recent(20)` — a direct copy of
   `DebloatHistoryDialog`'s structure (`QListWidget` + Close button).

3. **Discoverability nudge for the retired Performance Tuner.** Someone
   who used to click "Performance Tuner" in the sidebar has no obvious
   replacement once it's gone. Add one `QLabel` under the Tweaks module's
   existing preset toolbar (`_build_preset_toolbar()`), muted styling,
   shown only when the preset combo's current selection is not already
   "Performance": `"Looking for what used to be Performance Tuner? Try
   the Performance preset above."` No first-run/dismissal state to
   track — it is just always there when a different preset (or none) is
   selected, and disappears once the user has the Performance preset
   loaded, which reads as confirmation rather than nagging.

### §3.6 Testing (additional improvements)

- Quick Fix search: a test that typing a substring hides non-matching
  cards and their category header stays/hides correctly when partially
  vs fully filtered out.
- Quick Fix history: `record`/`recent` round-trip test (mirrors
  `test_debloat_history.py` if one exists, else a fresh small test file);
  a test that running a card (mocked success/error/cancel) calls
  `record()` with the right outcome string.
- Tweaks nudge label: a test that the label is visible when the preset
  combo is on anything other than "Performance" and hidden when it is on
  "Performance".

## §4. Out of scope (deferred, not part of this spec)

Two weaker candidates surfaced during brainstorming but were explicitly
not selected for this round:

- Scheduled Tasks + Services Manager + Windows Features (thematic
  similarity only, no code duplication — a UX tidiness win at best).
- Shared Resources + Remote Tools folding into the existing Network
  Diagnostics composite (no duplication either, just an unclaimed
  natural home).

Neither is touched by this spec. Revisit separately if there's appetite
after this round lands.
