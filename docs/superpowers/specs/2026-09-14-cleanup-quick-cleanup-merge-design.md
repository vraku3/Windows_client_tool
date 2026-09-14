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
| No config/search coupling to the module name | Confirmed: neither module registers a search provider, and nothing outside `src/modules/cleanup/` imports `QuickCleanupModule`/`QuickCleanupTab`. The merge is isolated. |

### 1.2 Recorded decisions

Settled during brainstorming; not open questions.

| Decision | Choice |
|---|---|
| Merge depth | Quick Cleanup's tab replaces Overview outright, not a combined layout |
| Auto-refresh | Kept (60s), but scoped to fire only while the Quick tab is the visible one |
| One-click actions | Promoted out of the "Advanced" collapsed section, always visible |
| `Clean All Safe` duplication | Consolidated into one shared helper, used by the merged tab and (where practical) `_ScanTab` |
| Missing watchdog | Added to the merged tab's scan loop, matching `_OverviewTab`/`_ScanTab`'s existing one |

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
  where it's attached.

### 2.3 Deleted

- `quick_cleanup_module.py` — the whole file.
- `_OverviewTab` (`tabs/_overview_tab.py`) and its export from
  `tabs/__init__.py`.
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
