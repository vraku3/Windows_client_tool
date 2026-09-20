# Cleanup: merge 8 tabs into one page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `CleanupModule`'s 8-tab `QTabWidget` with one scrollable
page: Quick Cleanup's dashboard as an always-visible header, followed by
seven collapsible sections wrapping the other seven tabs' existing widgets
unchanged.

**Architecture:** A new generic `_CollapsibleSection` widget (header bar +
disclosure arrow + a body that shows/hides one child widget, mirroring the
▼/▶ toggle style already used by `components/category_group.py`).
`CleanupModule.create_widget()` is restructured to build a `QScrollArea`
of these sections instead of a `QTabWidget`, reparenting the exact same
`_ScanTab`/`_BrowserCleanupTab`/`_LargeItemsTab` instances it builds today.
Nothing inside those tab widgets changes.

**Tech Stack:** PyQt6, pytest with `qapp` fixture (existing conventions).

**Spec:** docs/superpowers/specs/2026-09-20-cleanup-single-tab-merge-design.md

## Global Constraints

- No change to what any scanner does, what safety tier it reports, or what
  categories exist — this is a container/layout change only.
- `self._quick`, `self._sys_tab`, `self._browser`, `self._app_tab`,
  `self._wu_tab`, `self._logs_tab`, `self._large`, `self._dev_tab` remain
  the exact attribute names on `CleanupModule`, holding the exact same
  widget instances built the exact same way — `_cancel_all_tabs()` (used by
  `on_stop()`/`on_deactivate()`) must work with zero changes.
- No `self.tr()` calls anywhere (this app is deliberately English-only —
  see CLAUDE.md's Important Gotchas).
- Every new/changed test uses the existing `qapp` fixture and the
  `_settle()` / `_stub_quick_cleanup_scanners()` / `_stub_scan_tab_scanners()`
  helper pattern already established in `tests/test_cleanup_module_quick_tab.py`
  — do not invent a second stubbing convention.

---

### Task 1: `_CollapsibleSection` widget

**Files:**
- Create: `src/modules/cleanup/collapsible_section.py`
- Test: `tests/test_collapsible_section.py`

**Interfaces:**
- Produces: `_CollapsibleSection(title: str, body: QWidget, parent=None)`
  with `expanded = pyqtSignal()` (fires only on a collapsed→expanded
  transition, never on collapse, never on a redundant expand-while-already-expanded
  call), `set_summary(text: str) -> None`, `is_expanded() -> bool`,
  `set_expanded(expanded: bool) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
"""_CollapsibleSection: a header bar with a disclosure arrow over a body
that shows one child widget. Used by CleanupModule to replace its old
8-tab QTabWidget with one page of expand-on-demand sections (see
docs/superpowers/specs/2026-09-20-cleanup-single-tab-merge-design.md).
"""
from PyQt6.QtWidgets import QLabel


def _section(qapp):
    from modules.cleanup.collapsible_section import _CollapsibleSection
    body = QLabel("body content")
    section = _CollapsibleSection("System Junk", body)
    # isVisible() reflects the real on-screen state (ancestor chain
    # included), not just the explicit setVisible() flag -- show() the
    # top-level section itself so the body's own visibility is meaningful.
    section.show()
    return section, body


def test_starts_collapsed(qapp):
    section, body = _section(qapp)
    assert section.is_expanded() is False
    assert body.isVisible() is False


def test_set_expanded_true_shows_the_body_and_fires_expanded_once(qapp):
    section, body = _section(qapp)
    fired = []
    section.expanded.connect(lambda: fired.append(1))

    section.set_expanded(True)

    assert section.is_expanded() is True
    assert body.isVisible() is True
    assert fired == [1]


def test_expanding_an_already_expanded_section_does_not_refire(qapp):
    section, _body = _section(qapp)
    section.set_expanded(True)
    fired = []
    section.expanded.connect(lambda: fired.append(1))

    section.set_expanded(True)  # already expanded -- must be a no-op

    assert fired == []


def test_collapsing_does_not_fire_expanded(qapp):
    section, body = _section(qapp)
    section.set_expanded(True)
    fired = []
    section.expanded.connect(lambda: fired.append(1))

    section.set_expanded(False)

    assert section.is_expanded() is False
    assert body.isVisible() is False
    assert fired == []


def test_clicking_the_header_toggles_expansion(qapp):
    section, body = _section(qapp)
    section._header_btn.click()
    assert section.is_expanded() is True
    assert body.isVisible() is True
    section._header_btn.click()
    assert section.is_expanded() is False
    assert body.isVisible() is False


def test_set_summary_updates_the_header_label(qapp):
    section, _body = _section(qapp)
    section.set_summary("3 items, 42.1 MB")
    assert section._summary_lbl.text() == "3 items, 42.1 MB"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_collapsible_section.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.cleanup.collapsible_section'`

- [ ] **Step 3: Write `collapsible_section.py`**

```python
"""A header bar with a disclosure arrow over a body that shows one child
widget. Visual style mirrors components/category_group.py's own ▼/▶
toggle (same arrow characters, same "click header to expand" gesture),
but this is a generic container for an arbitrary pre-built widget rather
than something that owns its own scanner -- CleanupModule uses it to wrap
its seven existing tab widgets unchanged (see docs/superpowers/specs/
2026-09-20-cleanup-single-tab-merge-design.md).

QWidget.setVisible() toggling, not a QPropertyAnimation -- this app has no
existing animated-disclosure precedent and one is not worth introducing
for this.
"""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class _CollapsibleSection(QWidget):
    #: Fires ONLY on a collapsed -> expanded transition. CleanupModule
    #: connects this directly to a tab's own auto_scan(), whose own
    #: idempotency (`if not self._scanned`) makes a second connect-through
    #: safe -- but firing on every click (including collapsing, or
    #: re-clicking while already expanded) would be pointless busywork
    #: every time someone closes a section they already scanned.
    expanded = pyqtSignal()

    def __init__(self, title: str, body: QWidget, parent=None):
        super().__init__(parent)
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QHBoxLayout()
        self._header_btn = QPushButton(f"▸ {title}")
        self._header_btn.setFlat(True)
        self._header_btn.setStyleSheet("text-align: left; font-weight: bold;")
        self._header_btn.clicked.connect(self._toggle)
        header.addWidget(self._header_btn, 1)
        self._summary_lbl = QLabel("")
        header.addWidget(self._summary_lbl)
        outer.addLayout(header)

        self._title = title
        self._body = body
        body.setVisible(False)
        outer.addWidget(body)

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        was_expanded = self._expanded
        self._expanded = expanded
        self._body.setVisible(expanded)
        arrow = "▾" if expanded else "▸"
        self._header_btn.setText(f"{arrow} {self._title}")
        if expanded and not was_expanded:
            self.expanded.emit()

    def set_summary(self, text: str) -> None:
        self._summary_lbl.setText(text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_collapsible_section.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/modules/cleanup/collapsible_section.py tests/test_collapsible_section.py
git commit -m "feat(cleanup): add _CollapsibleSection widget"
```

---

### Task 2: Restructure `CleanupModule` onto the single-page layout

**Files:**
- Modify: `src/modules/cleanup/cleanup_module.py`
- Modify: `tests/test_cleanup_module_quick_tab.py`

**Interfaces:**
- Consumes: `_CollapsibleSection(title, body)` from Task 1
  (`from modules.cleanup.collapsible_section import _CollapsibleSection`).
- Produces: `CleanupModule._sections: dict[str, _CollapsibleSection]`,
  keyed by the same display names the old tabs used ("System Junk",
  "Browser Caches", "App & Game Caches", "Windows Update", "Logs & Reports",
  "Large Items", "Dev Tools") — this is what `_on_category_clicked` and
  any later code look sections up by. `CleanupModule._scroll_area:
  QScrollArea`. `self._quick`, `self._sys_tab`, `self._browser`,
  `self._app_tab`, `self._wu_tab`, `self._logs_tab`, `self._large`,
  `self._dev_tab` are unchanged (same names, same instances, same
  construction calls, copied verbatim from the current `create_widget()`).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_cleanup_module_quick_tab.py` in full with the version
below. It keeps the same fixture/stubbing helpers and the same five
behaviors under test, adapted to the new section-based structure instead
of `QTabWidget` tab-switching:

```python
"""CleanupModule's dashboard (Quick Cleanup) is now an always-visible
header, not a tab -- the other seven former tabs are collapsible sections
on the same page (see docs/superpowers/specs/
2026-09-20-cleanup-single-tab-merge-design.md). Its 60s auto-refresh
always rescans the header now, since it's no longer possible to navigate
away from it the way a QTabWidget tab could be.
"""
import tempfile
import time

from PyQt6.QtCore import QThreadPool

from modules.cleanup.cleanup_module import _CATEGORY_TAB_NAMES
from modules.cleanup.cleanup_scanner import ScanResult


def _fast_scan(min_age_days: int = 0) -> ScanResult:
    return ScanResult()


def _settle(qapp, timeout_ms: int = 10_000) -> None:
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    deadline = time.time() + 1
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _stub_quick_cleanup_scanners(quick_tab) -> None:
    """Replace every scanner QuickCleanupTab's own scan()/auto_scan() would
    actually run with an instant fake, so activating the module in a test
    starts no real ~101-category background sweep against the live
    filesystem. Matches tests/test_quick_cleanup_watchdog.py's own pattern
    of overriding `_scanner_map` entries.

    Without this, a real sweep's stragglers (workers QThreadPool.waitForDone
    didn't finish inside its window) can outlive the test and later deliver
    their signals into a freed widget during a DIFFERENT test's event pump
    -- a reproducible native access violation that kills the whole pytest
    process (Finding C1)."""
    for cid, (_fn, label, color) in list(quick_tab._scanner_map.items()):
        quick_tab._scanner_map[cid] = (_fast_scan, label, color)
    for cid, (_fn, label, color) in list(quick_tab._adv_scanner_map.items()):
        quick_tab._adv_scanner_map[cid] = (_fast_scan, label, color)
    quick_tab._browser_scanner = lambda: []


def _stub_scan_tab_scanners(scan_tab) -> None:
    """Same idea as _stub_quick_cleanup_scanners, for a real _ScanTab (e.g.
    System Junk) -- its own auto_scan() runs every scanner in its
    `_scanners` dict (75+ for System Junk) against the live filesystem."""
    scan_tab._scanners = {_fast_scan: ("Fake", "safe")}


def _module(qapp):
    from app import App
    from modules.cleanup.cleanup_module import CleanupModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = CleanupModule()
    module.on_start(app)
    widget = module.create_widget()
    module._keep_alive = widget
    _stub_quick_cleanup_scanners(module._quick)
    # Every real _ScanTab in this module -- App & Game Caches in particular
    # merges six catalog categories (apps/games/media/comms/cloud/browsers)
    # into one dict, on the same order of scale as System Junk's own
    # sweep. This module's tests expand every section (see
    # test_every_category_tab_name_mapping_resolves_to_a_real_section), so
    # all of them need stubbing, not just the one section any single test
    # happens to name.
    for scan_tab in (
        module._sys_tab, module._app_tab, module._wu_tab,
        module._logs_tab, module._large._scan_tab, module._dev_tab,
    ):
        _stub_scan_tab_scanners(scan_tab)
    return module, app


def test_quick_cleanup_is_always_visible_above_the_sections(qapp):
    module, app = _module(qapp)
    try:
        assert module._quick.isVisible() or module._quick.parent() is not None
        assert set(module._sections.keys()) == {
            "System Junk", "Browser Caches", "App & Game Caches",
            "Windows Update", "Logs & Reports", "Large Items", "Dev Tools",
        }
    finally:
        app.shutdown()


def test_clicking_a_category_card_expands_its_section(qapp):
    module, app = _module(qapp)
    try:
        assert module._sections["Browser Caches"].is_expanded() is False
        module._quick._handle_category_clicked("browser")
        assert module._sections["Browser Caches"].is_expanded() is True
        _settle(qapp)
    finally:
        app.shutdown()


def test_expanding_a_section_triggers_its_auto_scan_exactly_once(qapp):
    module, app = _module(qapp)
    try:
        calls = []
        module._sys_tab.auto_scan = lambda: calls.append(1)
        module._sections["System Junk"].set_expanded(True)
        assert calls == [1]
        module._sections["System Junk"].set_expanded(False)
        module._sections["System Junk"].set_expanded(True)
        assert calls == [1], "re-expanding an already-scanned section re-scanned it"
    finally:
        app.shutdown()


def test_refresh_data_always_rescans_the_always_visible_header(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        calls = []
        monkeypatch.setattr(module._quick, "scan", lambda: calls.append(1))
        module.refresh_data()
        assert calls == [1]
    finally:
        app.shutdown()


def test_get_refresh_interval_is_60_seconds(qapp):
    module, app = _module(qapp)
    try:
        assert module.get_refresh_interval() == 60_000
    finally:
        app.shutdown()


def test_every_category_tab_name_mapping_resolves_to_a_real_section(qapp):
    """_CATEGORY_TAB_NAMES maps 10 Quick Cleanup category ids to section
    title strings, matched by exact string equality against
    self._sections keys in _on_category_clicked. A typo in either this
    dict or quick_cleanup_tab.py's CLEANUP_CATEGORIES would silently no-op
    a category click for that category -- only "browser" had coverage
    before this test (see test_clicking_a_category_card_expands_its_section
    above); this exercises all 10."""
    module, app = _module(qapp)
    try:
        for category_id, expected_section_name in _CATEGORY_TAB_NAMES.items():
            module._quick._handle_category_clicked(category_id)
            assert module._sections[expected_section_name].is_expanded() is True, (
                f"category {category_id!r} did not expand "
                f"{expected_section_name!r}")
        _settle(qapp)
    finally:
        app.shutdown()


def test_cancel_all_tabs_still_reaches_every_section_widget(qapp):
    """_cancel_all_tabs() iterates fixed attribute names
    (_quick/_sys_tab/_browser/... ), not self._sections -- this pins that
    those attributes still exist with working _cancel_all()/cancel() after
    the QTabWidget removal, since on_stop()/on_deactivate() depend on it
    and nothing else in this file re-tests it."""
    module, app = _module(qapp)
    try:
        module.on_deactivate()  # must not raise
        module.on_stop()        # must not raise
    finally:
        app.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cleanup_module_quick_tab.py -v`
Expected: FAIL — `AttributeError: 'CleanupModule' object has no attribute '_sections'`
(and/or the old `_tabs`-based tests being replaced no longer apply)

- [ ] **Step 3: Rewrite `create_widget()` and the tab-switch/refresh methods**

In `src/modules/cleanup/cleanup_module.py`:

Replace the import block's `QTabWidget` import and add the new ones:

```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from modules.cleanup import cleanup_scanner as cs
from modules.cleanup.collapsible_section import _CollapsibleSection
from modules.cleanup.tabs import (
    _ScanTab,
    _BrowserCleanupTab,
    _LargeItemsTab,
)
from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab, ADVANCED_CATEGORIES
```

Replace the whole `create_widget()` method:

```python
    def create_widget(self) -> QWidget:
        outer = QWidget()
        main_lay = QVBoxLayout(outer)
        main_lay.setContentsMargins(4, 4, 4, 4)
        main_lay.setSpacing(4)

        # ── Module-level toolbar ──
        header = QHBoxLayout()
        self._freed_lbl = QLabel("Freed this session: 0 B")
        self._freed_lbl.setStyleSheet("color: #4caf50; font-weight: bold; padding: 2px 6px;")
        self._freed_bytes = 0
        header.addStretch()
        header.addWidget(self._freed_lbl)
        main_lay.addLayout(header)

        # ── Scrollable page: Quick Cleanup header + collapsible sections ──
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        self._scroll_area.setWidget(page)
        main_lay.addWidget(self._scroll_area, 1)

        # Quick Cleanup's dashboard is the page header, always visible --
        # it already scans everything and shows the pie chart + totals, so
        # it is not wrapped in a section (see docs/superpowers/specs/
        # 2026-09-20-cleanup-single-tab-merge-design.md).
        self._quick = QuickCleanupTab(on_category_clicked=self._on_category_clicked)
        self._quick.build(advanced_categories=ADVANCED_CATEGORIES)
        page_lay.addWidget(self._quick)

        self._sections: dict = {}

        # System Junk
        sys_scanners = {
            cs.scan_temp_files:       ("Temp Files",       "safe"),
            cs.scan_prefetch:         ("Prefetch",          "caution"),
            cs.scan_thumbnail_cache:  ("Thumbnail Cache",   "safe"),
            cs.scan_user_crash_dumps: ("User Crash Dumps",  "caution"),
        }
        self._sys_tab = _ScanTab(
            _with_catalog({**sys_scanners, **SYSTEM_EXTRA}, "system"))
        self._add_section(page_lay, "System Junk", self._sys_tab)

        # Browser Caches
        self._browser = _BrowserCleanupTab()
        self._add_section(page_lay, "Browser Caches", self._browser)

        # App & Game Caches
        app_scanners = {
            cs.scan_app_caches:           ("App Caches",             "safe"),
            cs.scan_store_app_caches:     ("Store / UWP Caches",     "safe"),
            cs.scan_d3d_shader_cache:     ("GPU Shader Cache",        "safe"),
            cs.scan_appdata_autodiscover: ("Auto-discovered Caches",  "caution"),
            cs.scan_steam_cache:          ("Steam Cache",             "safe"),
            cs.scan_stremio_cache:        ("Stremio Cache",           "safe"),
            cs.scan_outlook_cache:        ("Outlook Cache",           "safe"),
            cs.scan_winget_packages:      ("WinGet Packages",         "safe"),
        }
        # "browsers" is here rather than on the Browser Caches section: that
        # section runs EnhancedBrowserScanner, which enumerates live profiles
        # and takes no scanner dict at all, so the catalog's per-browser
        # path scanners had nowhere to appear. Overlap between the two is
        # handled by dedupe_items, which counts a path once.
        self._app_tab = _ScanTab(_with_catalog(
            app_scanners, "apps", "games", "media", "comms", "cloud",
            "browsers"))
        self._add_section(page_lay, "App & Game Caches", self._app_tab)

        # Windows Update
        wu_scanners = {
            cs.scan_wu_cache:              ("WU Download Cache",   "caution"),
            cs.scan_delivery_optimization: ("Delivery Opt. Cache", "safe"),
        }
        self._wu_tab = _ScanTab(wu_scanners, wu_cache=True)
        self._add_section(page_lay, "Windows Update", self._wu_tab)

        # Logs & Reports
        log_scanners = {
            cs.scan_windows_logs:     ("Windows Logs",      "caution"),
            cs.scan_event_logs:       ("Event Log Files",   "caution"),
            cs.scan_wer_reports:      ("WER Crash Reports", "caution"),
            cs.scan_memory_dumps:     ("Memory Dumps",      "caution"),
            cs.scan_panther_logs:     ("Panther Logs",       "caution"),
            cs.scan_dmf_logs:         ("DMF Logs",           "caution"),
            cs.scan_onedrive_logs:    ("OneDrive Logs",      "safe"),
            cs.scan_defender_history: ("Defender History",   "safe"),
        }
        self._logs_tab = _ScanTab({**log_scanners, **LOGS_EXTRA})
        self._add_section(page_lay, "Logs & Reports", self._logs_tab)

        # Large Items + DISM
        self._large = _LargeItemsTab()
        self._add_section(page_lay, "Large Items", self._large)

        # Dev Tools
        dev_scanners = {
            cs.scan_dev_tool_caches: ("Dev Tool Caches", "safe"),
        }
        self._dev_tab = _ScanTab(_with_catalog(dev_scanners, "dev"))
        self._add_section(page_lay, "Dev Tools", self._dev_tab)

        page_lay.addStretch()

        # ── Wire signals ──
        for tab in (
            self._quick, self._sys_tab, self._browser, self._app_tab,
            self._wu_tab, self._logs_tab, self._large, self._dev_tab,
        ):
            tab.freed_bytes.connect(self._on_freed)

        return outer

    def _add_section(self, page_lay, title: str, body: QWidget) -> None:
        section = _CollapsibleSection(title, body)
        if hasattr(body, "auto_scan"):
            section.expanded.connect(body.auto_scan)
        self._sections[title] = section
        page_lay.addWidget(section)
```

Replace `_on_tab_changed`/`_on_category_clicked` (remove `_on_tab_changed`
entirely — nothing calls it anymore) with:

```python
    def _on_category_clicked(self, category_id: str) -> None:
        section_name = _CATEGORY_TAB_NAMES.get(category_id)
        if section_name is None:
            return
        section = self._sections.get(section_name)
        if section is None:
            return
        section.set_expanded(True)
        self._scroll_area.ensureWidgetVisible(section)
```

Replace `refresh_data()`:

```python
    def refresh_data(self) -> None:
        """Quick Cleanup's dashboard is always on screen now (the page
        header, not a tab that can be navigated away from), so its 60s
        auto-refresh is unconditional. The seven collapsible sections are
        unaffected -- nothing about them was ever on this timer."""
        if getattr(self, "_quick", None) is None:
            return
        self._quick.scan()
```

`on_start`, `on_stop`, `on_activate`, `get_refresh_interval`,
`on_deactivate`, `_cancel_all_tabs`, `_on_freed`, and `get_status_info` are
all unchanged — leave them exactly as they are.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cleanup_module_quick_tab.py tests/test_collapsible_section.py -v`
Expected: all passed

- [ ] **Step 5: Run the full Cleanup test suite**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_cleanup_*.py tests/test_quick_cleanup_*.py tests/test_collapsible_section.py tests/test_module_inventory.py -k "not procengine"`
Expected: all passed (no other file asserts on `CleanupModule._tabs`, confirmed
during plan authoring — `test_cleanup_catalog.py` and
`tests/test_quick_cleanup_dedupe.py` only call `create_widget()`, they
don't touch `_tabs`).

- [ ] **Step 6: Commit**

```bash
git add src/modules/cleanup/cleanup_module.py tests/test_cleanup_module_quick_tab.py
git commit -m "feat(cleanup): merge 8 tabs into one page of collapsible sections"
```

---

### Task 3: Full suite + real-machine verification

**Files:**
- Test: full suite + a real run against this machine's Cleanup module

- [ ] **Step 1: Full suite**

```bash
rm -rf .pytest-tmp
.venv/Scripts/python.exe -m pytest -q --timeout=300
```
Expected: exit 0, no FAILED/ERROR beyond the two documented pre-existing
issues (`test_no_frozen_colours`'s budget drift, `test_procengine_findref.py`'s
real-machine-state flake — both predate this plan and are unrelated to it).

- [ ] **Step 2: Real-machine verification**

Run the app from source (`python src/main.py`), open Cleanup, and confirm:
the pie chart + freeable-space header renders and is always visible; all
seven section headers are present, collapsed by default; expanding a
section (e.g. System Junk) triggers exactly one scan and shows real
results; collapsing and re-expanding that same section does NOT re-scan
(the tree stays as it was, no new "Scanning..." flash); clicking a Quick
Cleanup category card (e.g. "Browser") expands the matching section and
scrolls it into view. This is the check that proves the merge works
against the real widget tree, not just mocks; do not skip it or claim done
without it.

- [ ] **Step 3: Commit (if any fixups were needed)**

Only if Step 1 or 2 required changes — otherwise this task has nothing to
commit.
