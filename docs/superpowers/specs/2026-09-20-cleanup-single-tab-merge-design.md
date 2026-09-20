# Cleanup: merge 8 tabs into one page

## Goal

Replace `CleanupModule`'s 8-tab `QTabWidget` (Quick Cleanup, System Junk,
Browser Caches, App & Game Caches, Windows Update, Logs & Reports, Large
Items, Dev Tools) with a single scrollable page: a pinned header carrying
Quick Cleanup's pie chart, total freeable space, and Scan All / Clean All
Safe, followed by seven collapsible sections — one per remaining current
tab — each wrapping that tab's existing widget unchanged.

## Why

The 8-tab layout requires clicking through tabs to see what's actually
there; Quick Cleanup's dashboard already answers "how much junk, in which
categories" at a glance, but seeing or acting on a specific category still
means switching tabs. One page with an always-visible summary and
expand-on-demand detail removes that switching, at the cost of a page
that's initially all-collapsed rather than showing a table immediately.

## What already exists and will be reused, not reimplemented

Every tab widget already exists as an eagerly-constructed `QWidget`
attribute on `CleanupModule` (`self._quick`, `self._sys_tab`, `self._browser`,
`self._app_tab`, `self._wu_tab`, `self._logs_tab`, `self._large`,
`self._dev_tab` — all built unconditionally in `create_widget()`, confirmed
by reading the current file). `QTabWidget` only controlled which one was
*visible*; none of this is lazy at the Python-object level. That means the
merge does not need to solve a lazy-construction problem — it only needs to
reparent these same widgets from tab pages into collapsible sections, and
retarget the "auto-scan on first view" trigger from "tab became current"
to "section was expanded".

Nothing in `_ScanTab`, `_BrowserCleanupTab`, `_LargeItemsTab`, or
`QuickCleanupTab` changes internally: `auto_scan()` is already idempotent
(`if not self._scanned`), `_cancel_all()` already exists on each, and the
freed-bytes signal, watchdog, and worker-tracking are all per-tab-instance
already and stay exactly as they are — only what *contains* each widget
changes.

## Design

### `_CollapsibleSection` (new, `src/modules/cleanup/collapsible_section.py`)

A small `QWidget`: a clickable header bar (title + freed/item-count summary
label + a ▸/▾ disclosure arrow) above a body that hosts one child widget.
Collapsed by default. `QWidget` visibility toggling (`body.setVisible()`),
not a `QPropertyAnimation` — this app has no precedent for animated
disclosure and one is not worth introducing here.

```python
class _CollapsibleSection(QWidget):
    expanded = pyqtSignal()  # fires only on collapsed -> expanded transitions

    def __init__(self, title: str, body: QWidget, parent=None): ...
    def set_summary(self, text: str) -> None: ...  # header's right-aligned label
    def is_expanded(self) -> bool: ...
    def set_expanded(self, expanded: bool) -> None: ...  # used by "expand and scroll to" (was _on_category_clicked)
```

Emitting `expanded` only on the collapsed→expanded transition (not on
every click, including collapsing) is what lets `CleanupModule` wire
`section.expanded.connect(tab.auto_scan)` directly — `auto_scan()`'s own
idempotency means a second expand is already a safe no-op, but firing on
collapse too would be pointless work every time someone closes a section.

### `CleanupModule.create_widget()` restructuring

Replace the `QTabWidget` with a `QScrollArea` containing a `QVBoxLayout`:

1. Header row (unchanged from today's module-level toolbar: freed-session
   label) followed immediately by `self._quick`'s widget, un-tabbed — it
   already IS the dashboard (pie chart, per-category totals, Scan All,
   Clean All Safe, advanced-panel toggle), so it becomes the page header
   verbatim, not rebuilt.
2. Seven `_CollapsibleSection`s, one per remaining tab, each built exactly
   as `create_widget()` builds that tab today (same scanner dicts, same
   `_with_catalog(...)` calls, same constructor arguments — copied
   unchanged from the current tab-construction code), then wrapped:
   `section = _CollapsibleSection("System Junk", self._sys_tab)`.
3. `section.expanded.connect(self._sys_tab.auto_scan)` for each of the
   seven (Quick Cleanup keeps its own existing `on_activate`-driven
   auto-scan, since it's no longer tab-switched to at all — it's just
   always on screen).

`self._quick`, `self._sys_tab`, `self._browser`, `self._app_tab`,
`self._wu_tab`, `self._logs_tab`, `self._large`, `self._dev_tab` remain the
exact same attribute names holding the exact same widget instances —
`_cancel_all_tabs()` (used by `on_stop()`/`on_deactivate()`) needs no
changes at all.

### `_on_category_clicked` (Quick Cleanup's category-card click)

Today this calls `self._tabs.setCurrentIndex(i)` to switch to the matching
tab. Becomes: find the matching `_CollapsibleSection`, call
`section.set_expanded(True)`, then `self._scroll_area.ensureWidgetVisible(section)`
— expand-and-scroll-to instead of switch-to.

### `refresh_data()` (60s auto-refresh)

Today: `if self._tabs.currentWidget() is self._quick: self._quick.scan()`
— it only rescans Quick Cleanup while that tab happens to be the visible
one, specifically to avoid blindly re-triggering some other tab's (e.g.
Large Items') expensive full-machine scan every 60 seconds while someone
is reading it. In the merged page Quick Cleanup's widget is *always* on
screen (it's the header, not a tab), so the "only if visible" condition
simplifies to unconditional: `self._quick.scan()`. The seven collapsible
sections are unaffected — nothing about them was ever on this timer, and
nothing here changes that.

### Cross-cutting behavior that does NOT change

- Safety colour-coding, per-tab age filters, running-process guard, >500 MB
  confirmation, error panel, freed-session counter, DISM button on Large
  Items — all internal to the tab widgets, none of it moves.
- `_cancel_all_tabs()`, `on_stop()`, `on_deactivate()`, `get_refresh_interval()`
  (still `60_000`) — unchanged.
- The `SCAN_WATCHDOG_MS` per-`_ScanTab` backstop — unchanged, still per
  widget instance.

## Testing

- `tests/test_collapsible_section.py` (new): starts collapsed; `set_expanded(True)`
  shows the body and fires `expanded` exactly once; a second
  `set_expanded(True)` while already expanded does not re-fire `expanded`;
  collapsing does not fire `expanded`.
- `tests/test_cleanup_module.py` (existing — read it first for current
  coverage and helper fixtures before adding to it): update any test that
  asserts on `self._tabs`/`QTabWidget` structure to instead assert against
  the new section list; add a test that `_on_category_clicked` expands the
  right section and does not raise when the category has no matching
  section; add a test that `refresh_data()` now calls `self._quick.scan()`
  unconditionally (no tab-visibility gate to construct in the test).
- Full existing Cleanup test suite (`test_cleanup_*.py`, `test_debloat_*`
  untouched) must stay green — this is a container change around widgets
  whose own internals and own tests do not change.
- Real-machine spot check: run from source, open Cleanup, confirm the pie
  chart header renders, expand two or three sections and confirm each
  triggers its scan exactly once (not on every expand), collapse and
  re-expand a section and confirm it does NOT re-scan (idempotent
  `auto_scan()` still holds).

## Out of scope for this pass

- Any change to what a scanner does, what safety tier it reports, or what
  categories exist — this is a container/layout change only. New scanners
  and safety-classification work belongs to the follow-on improvements
  pass (see below), against the post-merge code.
- Remembering expand/collapse state across sessions (e.g. in config) — the
  page starts fully collapsed every time; not requested, and adds a
  persistence surface for a page that scans fast enough this doesn't cost
  much.
- Reordering or renaming sections — same seven names, same order as today's
  tabs.

## Follow-on: Sub-project 2 (separate spec, after this merge lands)

A second pass, scoped and speced separately once this page exists for
real: at least 100 additional safe improvements to Cleanup + Debloat,
explicitly steered toward more scanning power per the user's own
follow-up — deeper DISM component-store cleanup coverage, Disk Cleanup's
native `/sageset` + `/sagerun` preset automation, broader browser
cache/junk detection, and more AppData junk categories — plus whatever
else a close read of the merged code and both scanner catalogs turns up
(safety-tier corrections, missed refusal disclosure, dead code, small UX
gaps), the same audit-then-SDD shape as the earlier 40-improvement pass
on this codebase.
