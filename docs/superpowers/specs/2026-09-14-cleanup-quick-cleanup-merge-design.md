# Cleanup + Quick Cleanup Merge — Design Spec

**Project:** Windows_client_tool
**Sub-project:** Unite the Cleanup and Quick Cleanup sidebar entries
**Date:** 2026-09-14
**Status:** Approved design, not yet implemented.

---

## 1. Overview

Two sidebar entries do overlapping jobs: `CleanupModule` (8 tabs — Overview,
System Junk, Browser Caches, App & Game Caches, Windows Update, Logs &
Reports, Large Items, Dev Tools) and `QuickCleanupModule` (a single
dashboard: pie chart, category cards, 12 one-click actions, a 60-second
auto-refresh). Both already read through the same `scan_cache` (60s TTL),
so the actual disk-scanning work isn't doubled today — the duplication is
in the UI (two "here's your junk" landing screens) and in three
near-identical "clean everything safe" implementations.

This merges them into one sidebar entry: `QuickCleanupTab`'s content
becomes the new **first tab** of `CleanupModule`, replacing the existing
Overview tab outright. `QuickCleanupModule` and its sidebar registration
are deleted.

**Why replace Overview rather than combine the two layouts:** Overview is
a read-only table (Category / Total / Safe / Count / Status) with no
row-click navigation and no per-item drill-down — confirmed by reading
`_overview_tab.py`, not assumed. Everything it shows is a plainer subset
of what Quick Cleanup's pie chart + legend + expandable per-category
`CategoryGroup` widgets already show. Combining two layouts into one tab
would be more UI work for less capability than just keeping the richer
one.

### 1.1 What's actually wrong today (confirmed in the code)

| Finding | Evidence |
|---|---|
| Two sidebar entries for overlapping jobs | `main.py:144-145` registers `CleanupModule` and `QuickCleanupModule` back to back, both in `ModuleGroup.OPTIMIZE`. |
| Three near-duplicate "clean all safe" implementations | `_overview_tab.py:_do_clean_safe` (re-scans live, skips browser, size-gated confirm), `quick_cleanup_tab.py:_do_clean_all_safe` (uses cached `_results`, includes browser, always confirms), `_scan_tab.py:_do_clean` (per-tab scanner dict, size-gated confirm). All three end by calling the same `cs.delete_items(...)`, then hand-roll the same confirm/disable-buttons/progress/status/`freed_bytes`/re-scan sequence around it. |
| Quick Cleanup has no scan-watchdog backstop | `_overview_tab.py` and `_scan_tab.py` both carry a `SCAN_WATCHDOG_MS` `QTimer` backstop (a cancelled `Worker` emits `cancelled`, never `result`/`error` — CLAUDE.md records this as a real, previously-shipped bug). `quick_cleanup_tab.py` has no such timer at all. |
| One-click actions are hidden by default | `_build_one_click_panel` is called from inside `_adv_widget`, which starts `setVisible(False)` (`_setup_ui`, `quick_cleanup_tab.py:613`) — Flush DNS, Compact WinSxS, Reset TCP/IP etc. are all one click away only after "Show Advanced ▼". |
| One-click actions share ONE status label with no per-action busy-guard | `_run_action_command` (quick_cleanup_tab.py:887) neither disables its own triggering button nor tracks a per-action "already running" state, and every one of the 12 actions writes to the same `self._action_status_lbl`. Two actions clicked in quick succession clobber each other's result with no way to tell which is which. Promoting these to always-visible (this spec) makes this more likely to actually happen, so it's fixed here, not deferred. |
| No config/search coupling to the module name | Confirmed: neither module registers a search provider, and nothing outside `src/modules/cleanup/` imports `QuickCleanupModule`/`QuickCleanupTab`. The merge is isolated. |
| `_OV_GROUPS` has a hidden external consumer | `stage_runners.py:run_cleanup_safe_stage` — the engine behind this app's existing `--unattended --stages cleanup` and the scheduled `WinClientTool_UnattendedMaintenance` task — imports `_OV_GROUPS` directly from `_overview_tab.py`. Deleting that file without relocating `_OV_GROUPS` first breaks unattended cleanup silently; it would keep "working" (no import error until the next `--unattended` run) and only surface as a scheduled task quietly failing. |

### 1.2 Recorded decisions

Settled during brainstorming; not open questions.

| Decision | Choice |
|---|---|
| Merge depth | Quick Cleanup's tab replaces Overview outright, not a combined layout |
| Auto-refresh | Kept (60s), but scoped to fire only while the Quick tab is the visible one |
| One-click actions | Promoted out of the "Advanced" collapsed section, always visible; gain a per-action busy-guard and per-action status text |
| `Clean All Safe` duplication | Consolidated into one shared helper, used by the merged tab and (where practical) `_ScanTab`; the merged tab always confirms (matching Quick Cleanup's current behavior) rather than size-gating, since it's the highest-blast-radius action in the module |
| Missing watchdog | Added to the merged tab's scan loop, matching `_OverviewTab`/`_ScanTab`'s existing one; the exact timeout constant is set from a real measured scan time on this machine, not copied from Overview's own 300,000ms figure |
| Category cards | Each becomes clickable, jumping to that category's own deep-dive tab (e.g. clicking the "Browser Caches" card switches to the Browser Caches tab) — a real navigation flow the merge specifically makes possible, since both were previously separate, unreachable-from-each-other destinations |
| "Show Advanced ▼" toggle | Reconsidered at implementation time: once one-click actions move out, only a handful of extra category cards remain behind it — may not be worth keeping as a separate collapsed section at all (candidate for removal, showing all categories by default) |
| `_OV_GROUPS` | Extracted out of `_overview_tab.py` into `cleanup_scanner.py` (alongside `delete_items`, `run_clean_safe`) BEFORE `_overview_tab.py` is deleted, so `run_cleanup_safe_stage` (and anything else importing it) keeps working unchanged |

---

## 2. Component changes

### 2.1 `CleanupModule` (`cleanup_module.py`)

- Import `QuickCleanupTab`/`ADVANCED_CATEGORIES` in place of `_OverviewTab`;
  drop the `_OverviewTab` import entirely.
- `create_widget()`: build the Quick tab the same way `QuickCleanupModule`
  did (`QuickCleanupTab(); .build(advanced_categories=ADVANCED_CATEGORIES)`)
  and `addTab(..., "Quick Cleanup")` as tab index 0, in place of the old
  `_OverviewTab` line. The other seven `addTab` calls are unchanged.
- The signal-wiring loop (`for tab in (...): tab.freed_bytes.connect(...)`)
  gains the Quick tab and drops `self._overview`.
- `_cancel_all_tabs()`'s name tuple gains `_quick` and drops `_overview`.
- `on_activate()` calls `self._quick.auto_scan()` in place of
  `self._overview.auto_scan()`.
- New: `get_refresh_interval() -> Optional[int]` returns `60_000`.
- New: `refresh_data()` — only calls `self._quick.scan()` when
  `self._tabs.currentWidget() is self._quick`; otherwise a no-op. This is
  the "auto-refresh is throttled per visible child" principle
  `CompositeModule` already documents, applied here even though
  `CleanupModule` isn't built on `CompositeModule` — every other tab in
  this module has never auto-refreshed on a timer, and a blind
  `refresh_data()` that rescanned whichever tab happened to be open would
  be a real, new regression (e.g. silently re-running Large Items' 42GB
  scan every 60s while someone reads it).

### 2.2 `QuickCleanupTab` (`components/quick_cleanup_tab.py`)

- New `freed_bytes = pyqtSignal(int)` class attribute, emitted from
  `_do_clean_all_safe`'s completion handler with the same `total` value
  already computed there — this is what feeds the module's shared "Freed
  this session" counter. The tab's own `_status_lbl` message stays as
  additional, immediate, per-action feedback (matching what `_ScanTab`
  already does: both a local status line and the shared counter).
- New `auto_scan()` method: `if not self._scanned: self.scan()` —
  `_on_tab_changed`'s existing `if hasattr(tab, "auto_scan")` check picks
  this up with no change to `cleanup_module.py`'s dispatch logic. Needs a
  `self._scanned` flag set on scan completion if one doesn't already
  exist under another name (`_scanning` exists; check for a completion
  flag before adding a new one).
- New scan-watchdog: a `QTimer(self)`, single-shot, matching
  `_OverviewTab.SCAN_WATCHDOG_MS = 300_000` in shape — started when a scan
  begins, stopped when it completes normally, and its timeout handler
  resets `_scanning`/button state and clears `_scanned` the same way
  `_OverviewTab._on_scan_watchdog` does. Real measured scan time on this
  machine should set the constant, the way `_OverviewTab`'s own comment
  ("this sweep measures 6.1s... margin here is larger still") already
  records for its own budget.
- `_setup_ui()`: the one-click actions panel moves out of `_adv_widget`
  (which stays for the additional per-category advanced legend cards) and
  into the always-visible dashboard area, so it's not gated behind "Show
  Advanced ▼" any more. `_build_one_click_panel` itself is unchanged, only
  where it's attached. At implementation time, reconsider whether
  `_adv_widget`'s toggle is worth keeping at all once one-click actions no
  longer live inside it (see 1.2) — if not, the remaining advanced cards
  can just render inline with the regular ones and the toggle goes away.
- `_run_action_command` gains a per-action busy-guard (each of the 12
  one-click buttons disables itself for the duration of its own command,
  not the whole panel) and a per-action status target instead of the one
  shared `_action_status_lbl` — each button gets its own small status
  text next to it, so two actions run back to back never clobber each
  other's result.
- Category cards (`_SliceCard` / the legend list) gain a click handler
  that switches `CleanupModule`'s `QTabWidget` to that category's tab.
  Since `QuickCleanupTab` itself doesn't own a reference to the parent
  `QTabWidget`, this is a small callback `CleanupModule` passes in at
  construction time (e.g. `QuickCleanupTab(on_category_clicked=...)`),
  not `QuickCleanupTab` reaching upward into its parent.
- `_OV_GROUPS` (currently defined in `_overview_tab.py`) moves to
  `cleanup_scanner.py`. `QuickCleanupTab`'s own category list and
  `run_cleanup_safe_stage` both import it from there afterward — one
  definition, two consumers, instead of one definition and a consumer
  that would otherwise go missing.

### 2.3 Deleted

Order matters here: `_OV_GROUPS` must be extracted to `cleanup_scanner.py`
(2.2) and `run_cleanup_safe_stage` repointed at the new location BEFORE
`_overview_tab.py` is deleted, or the unattended stage breaks.

- `quick_cleanup_module.py` — the whole file.
- `_OverviewTab` (`tabs/_overview_tab.py`) and its export from
  `tabs/__init__.py` — only after `_OV_GROUPS` no longer lives there.
- `main.py`: the `QuickCleanupModule` import and
  `app.module_registry.register(QuickCleanupModule())` line.

### 2.4 The shared "clean all safe" helper

New function `run_clean_safe` in `cleanup_scanner.py`, alongside
`delete_items` — every current call site already imports that module, so
this adds no new import for any of them. Contract:

```
run_clean_safe(
    widget,             # for QMessageBox parenting
    items,              # list[ScanItem] already filtered to safety=="safe"
    *, browser_cats=None,      # list[CacheCategory], optional
    stop_wuauserv=False,
    confirm="always" | "size_gated",
    on_start, on_status, on_done,   # UI hooks, thin closures per caller
) -> Worker
```

Runs the existing confirm dialog (`_confirm_large` for `"size_gated"`, the
`QMessageBox` Quick Cleanup already builds for `"always"`), then a `Worker`
that calls `bs.delete_selected(browser_cats)` (if given) and
`cs.delete_items(items, stop_wuauserv=...)`, combining both results the
way `quick_cleanup_tab.py:1219-1228` already does. `on_done(deleted,
errors)` is the caller's status-line + `freed_bytes.emit(...)` +
re-scan-trigger — each of the three current call sites keeps its own
button-disable specifics and re-scan call, since those differ per widget,
but the confirm/worker/delete/combine logic stops being written three
times.

`_ScanTab._do_clean` adopts this too if it fits cleanly; if its shape
(single scanner dict, no browser path) makes the shared helper awkward
rather than simpler, it's fine to leave as-is — the goal is removing
duplication that exists, not forcing a third caller into a shape that
doesn't serve it. This is the implementation plan's call, not fixed here.

---

## 3. Data flow

- **Scan data**: unchanged. Quick Cleanup's `CategoryGroup` widgets and
  its own `_do_scan_all` still read through `scan_cache.cached_scan`,
  same as every other tab.
- **Freed-bytes counter**: Quick tab now participates in the same
  `freed_bytes` signal → `CleanupModule._on_freed` → header label pattern
  every other tab already uses. No separate "freed" display survives
  inside the tab itself beyond its existing per-action status line.
- **Auto-refresh**: `MainWindow`'s existing per-module timer mechanism
  (`_start_module_refresh_timer`, reads `get_refresh_interval()`) now
  fires for `CleanupModule` every 60s. `refresh_data()` checks the visible
  tab before doing anything, per 2.1.

## 4. Error handling

No new failure modes: `run_clean_safe` composes existing, already-tested
`bs.delete_selected`/`cs.delete_items` calls, whose error handling
(partial failures, `stop_wuauserv` service-stop-and-restore, "in use or
access denied" reporting) is untouched. The new watchdog's failure mode
(a `cancelled` Worker with nothing connected to it) is exactly what
`_OverviewTab`/`_ScanTab`'s existing watchdogs already guard against —
same fix, applied to the one tab that was missing it.

## 5. Testing

- `tests/test_cleanup_module.py` (or wherever `CleanupModule`'s tab
  wiring is currently tested): update tab-index/tab-name assertions for
  the Quick-tab-replaces-Overview swap; add coverage for
  `get_refresh_interval`/`refresh_data`'s visible-tab gating (asserted via
  a fake `QTabWidget.currentWidget()`, matching this codebase's existing
  Worker/`_SyncPool`-style test doubles rather than a real running scan).
- `tests/test_quick_cleanup_tab.py` (new or extended): the new
  `freed_bytes` emission, the new `auto_scan()`'s no-op-when-already-scanned
  behavior, and the watchdog firing when a `Worker` reports `cancelled`
  with nothing else connected — mirroring whatever test already exists
  for `_OverviewTab`'s own watchdog, since this is the same bug class
  being closed the same way.
- `run_clean_safe`: unit-testable in isolation (fake `items`/
  `browser_cats`, a `_SyncPool`-style worker, assert `on_done` receives
  the right combined counts) — the same pattern `test_cleanup_*` files
  already use for `cs.delete_items` today.
- Manual/real-machine: after implementation, `tools/cleanup_reader_sweep.py`
  is unaffected (it audits scanners, not UI tabs) — no change needed
  there. Worth a manual pass opening the merged tab once to confirm the
  pie chart, one-click actions, and `Clean All Safe` all still work
  end-to-end on this real machine, the same way prior cleanup work in
  this codebase was verified live rather than only through mocks.
- **`run_cleanup_safe_stage` regression check**: after moving
  `_OV_GROUPS`, run `python src/main.py --unattended --stages cleanup`
  (or the equivalent existing test coverage for `stage_runners.py`, if
  any) and confirm it still finds and cleans the same categories as
  before — this is the one consumer outside `src/modules/cleanup/` this
  merge touches, and it has no UI to notice a silent break in.

## 6. Non-goals

- No changes to the scanner catalog, `scan_cache`, or any individual
  `_ScanTab`'s own scanner dict.
- No redesign of the pie chart, category cards, or one-click actions'
  own visuals beyond un-hiding the latter.
- `_ScanTab._do_clean` is only migrated to the shared helper if it's a
  clean fit (see 2.4) — not a forced requirement of this work.
- No change to `Debloat`, `TreeSize`, or any other module's own
  Clean-All-style action, even though some share a similar shape —
  out of scope for this merge.

## 7. Related work (separate specs, not this one)

A broader "slim the OS down, keep it fully stable" pass grew out of this
merge and was deliberately split into its own sequence rather than folded
in here, per brainstorming's own guidance for a project too large for one
spec:

1. **This spec** — the merge itself. Build first: smallest, blocks
   nothing else, but the `_OV_GROUPS` extraction it requires is a real
   prerequisite for anything that touches `CleanupModule` afterward.
2. **New cleanup targets** — Windows.old (via the official DISM path
   only), hibernation-file right-sizing, print spooler stuck-job
   cleanup, NGEN native image cache, Recycle Bin per-drive reservation
   audit, orphaned user profiles, OneDrive "always keep on this device"
   pin audit. Mostly additive to the existing Large Items/System Junk
   tabs.
3. **A preset system** (Light / Thorough / Aggressive / Custom),
   mirroring Debloat's already-shipped pattern, governing what the whole
   module's "Clean" action includes across all 8 tabs at once — the
   single highest-leverage piece for serving both a fresh user (one
   button) and a senior engineer (full per-category control) with the
   same UI.
4. **A new, separate "System Health" sidebar module** — DISM
   `/ScanHealth` (run after aggressive cleanup, not just before), VSS
   shadow-storage floor management, pending servicing-transaction
   (`WinSxS\pending.xml`) detection, plain WinSxS
   `/StartComponentCleanup` (moved out of Large Items' "Analyze WinSxS"
   — it's a servicing operation, not a file to delete), a heavily-gated
   `/ResetBase` action (typed confirmation, forced fresh restore point,
   never bundled into "select all"), a free-space/next-upgrade-headroom
   indicator, read-only findings (orphaned services/scheduled tasks,
   pending-vs-archived WER reports), command transparency in every
   confirmation dialog, a dry-run toggle, an HTML audit log reusing the
   Updates module's own `history_writer.py` pattern, and a new read-only
   `--unattended --stages health` stage scheduled the same way
   `WinClientTool_UnattendedMaintenance` already is. Admin model matches
   Driver Manager's (`requires_admin` + `read_only_unelevated` — findings
   readable unelevated, servicing writes gated). Explicitly does NOT
   include: pagefile deletion/disable, Windows Search service disable,
   Defender quarantine handling, or telemetry *service* toggling — see
   that spec's own non-goals when it's written.

Each of 2-4 gets its own design spec, written and approved separately,
before its own implementation plan.
