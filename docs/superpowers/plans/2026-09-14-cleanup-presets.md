# Cleanup Preset System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Quick Cleanup's dashboard-level bulk clean a Light/Thorough/Aggressive/Custom preset selector, replacing its hardcoded "only safety=='safe'" rule.

**Architecture:** A new, small, dependency-free `cleanup_presets.py` module holds the 3 data-driven presets (a safety-level allow-set each) plus a hard `NEVER_INCLUDED` exclusion list no preset may override. `QuickCleanupTab` gains a `QComboBox` next to its existing Scan All/Clean All Safe buttons; `_do_clean_all_safe` and the button's enabled-state logic both become preset-aware, reusing the module for light/thorough/aggressive and falling back to today's exact behavior for custom (so an untouched app with the combo left on its default is behaviorally identical to before this plan).

**Tech Stack:** Python 3.12, PyQt6, pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-cleanup-presets-design.md`

## Global Constraints

- Default preset is `"light"` — an app that never touches the new combo box behaves EXACTLY as before this plan (today's `_do_clean_all_safe` already only includes `safety=="safe"`, which is Light's own definition).
- `NEVER_INCLUDED` (orphaned user profiles, virtual disk images, orphaned virtual disks) is excluded under EVERY preset including Aggressive — this is a hard rule, not a default that Aggressive overrides.
- The bulk-clean confirm dialog stays `confirm="always"` regardless of preset (carried over from Sub-project 1's Global Constraints).
- No changes to any of the other 7 tabs' own Clean/Clean Selected buttons — this plan only touches Quick Cleanup's dashboard-level bulk clean.

---

## Task 1: `cleanup_presets.py` — the preset data and resolver

**Files:**
- Create: `src/modules/cleanup/cleanup_presets.py`
- Test: `tests/test_cleanup_presets.py` (new)

**Interfaces:**
- Produces: `PRESETS: dict` (keys `"light"`/`"thorough"`/`"aggressive"`, each `{"label": str, "description": str, "safety_levels": frozenset}`), `NEVER_INCLUDED: frozenset` (scanner function `__name__`s), `preset_names() -> list` (4 ids including `"custom"`), `items_for_preset(preset_id: str, results: dict, id_to_scanner_name: dict) -> list` (raises `ValueError` for `"custom"` or any unrecognized id).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cleanup_presets.py`:

```python
"""Cleanup's preset system: a plain safety-level allow-set per preset,
plus a hard exclusion list (orphaned profiles, virtual disks) no preset
-- not even Aggressive -- may ever override.
"""
import pytest

from modules.cleanup.cleanup_presets import (
    PRESETS, NEVER_INCLUDED, preset_names, items_for_preset,
)
from modules.cleanup.cleanup_scanner import ScanItem, ScanResult


def _result(*, safe=0, caution=0, danger=0):
    r = ScanResult()
    for n, safety in ((safe, "safe"), (caution, "caution"), (danger, "danger")):
        for i in range(n):
            r.items.append(ScanItem(path=f"C:\\{safety}_{i}", size=100, is_dir=False, safety=safety))
    return r


def test_preset_names_includes_custom():
    names = preset_names()
    assert set(names) == {"light", "thorough", "aggressive", "custom"}


def test_light_includes_only_safe_items():
    results = {"a": _result(safe=2, caution=1, danger=1)}
    items = items_for_preset("light", results, {"a": "scan_temp_files"})
    assert len(items) == 2
    assert all(i.safety == "safe" for i in items)


def test_thorough_includes_safe_and_caution():
    results = {"a": _result(safe=2, caution=3, danger=1)}
    items = items_for_preset("thorough", results, {"a": "scan_temp_files"})
    assert len(items) == 5
    assert all(i.safety in ("safe", "caution") for i in items)


def test_aggressive_includes_everything_except_never_included():
    results = {
        "a": _result(safe=1, caution=1, danger=1),
        "b": _result(danger=1),
    }
    id_to_scanner = {"a": "scan_temp_files", "b": "scan_orphaned_user_profiles"}
    items = items_for_preset("aggressive", results, id_to_scanner)
    # "a"'s 3 items included; "b"'s single danger item excluded regardless
    # of Aggressive's own safety_levels allowing "danger" in general.
    assert len(items) == 3
    assert all(i.path.startswith("C:\\") and "b" not in i.path for i in items)


def test_never_included_scanner_names_match_the_real_scanners():
    # Cross-check against the actual codebase: these three scanner names
    # must exist as real functions, or this exclusion list is silently
    # protecting nothing.
    from modules.cleanup import cleanup_scanner as cs
    for name in NEVER_INCLUDED:
        assert hasattr(cs, name), f"{name} is not a real scanner function"


def test_custom_raises_value_error():
    with pytest.raises(ValueError):
        items_for_preset("custom", {}, {})


def test_unrecognized_preset_raises_value_error():
    with pytest.raises(ValueError):
        items_for_preset("does_not_exist", {}, {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cleanup_presets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.cleanup.cleanup_presets'`.

- [ ] **Step 3: Create `cleanup_presets.py`**

Create `src/modules/cleanup/cleanup_presets.py`:

```python
"""Cleanup's three built-in presets (Light/Thorough/Aggressive) plus
"Custom" (== whatever's currently checked, handled by the caller, not
this module). Mirrors the SPIRIT of Debloat's preset system
(debloat_presets.py) -- named, curated selections a fresh user can pick
with one click -- but Cleanup only ever needs one axis (safety level),
not two catalogs to reconcile, so this stays a plain constant rather
than a JSON-plus-resolver module.
"""

#: Scanner function names NO preset -- not even Aggressive -- may ever
#: auto-include. Matches this codebase's own established "never
#: pre-selected regardless of confirmation" convention (CLAUDE.md:
#: "Drivers and virtual disks never enter the checkbox tree"; the
#: orphaned-profiles scanner's own safety="danger" + selected=False).
#: Named by scanner function name, not by safety level -- a name-based
#: exclusion list survives a future scanner accidentally being marked
#: "caution" instead of "danger" (defense in depth, not a substitute for
#: getting the safety level right).
NEVER_INCLUDED = frozenset({
    "scan_orphaned_user_profiles",
    "scan_virtual_disk_images",
    "scan_orphaned_virtual_disks",
})

PRESETS = {
    "light": {
        "label": "Light",
        "description": "Safe items only -- what \"Clean All Safe\" has always done.",
        "safety_levels": frozenset({"safe"}),
    },
    "thorough": {
        "label": "Thorough",
        "description": "Safe and caution-level items -- more reclaimed space, still nothing destructive.",
        "safety_levels": frozenset({"safe", "caution"}),
    },
    "aggressive": {
        "label": "Aggressive",
        "description": (
            "Everything reclaimable, including danger-level items "
            "(except what NEVER_INCLUDED always excludes -- orphaned "
            "profiles and virtual disks are never swept by any preset)."
        ),
        "safety_levels": frozenset({"safe", "caution", "danger"}),
    },
}


def preset_names() -> list:
    return ["light", "thorough", "aggressive", "custom"]


def items_for_preset(preset_id: str, results: dict, id_to_scanner_name: dict) -> list:
    """Every ScanItem the given preset selects, across a QuickCleanupTab-
    shaped `results` dict ({category_id: ScanResult}), honoring
    NEVER_INCLUDED regardless of what the preset's own safety_levels say.

    `id_to_scanner_name` maps a category id (e.g. "large") to the real
    scanner function's __name__, needed because NEVER_INCLUDED excludes
    by scanner identity, not category -- a category can bundle several
    scanners (see _with_catalog), only some of which might ever be
    excluded.

    preset_id == "custom" is deliberately unresolved here -- it means
    "whatever's currently checked," which is the caller's own state, not
    a data-driven rule this function could apply. Raises ValueError for
    it (and for any other unrecognized id) so a caller cannot silently
    forget the special case.
    """
    if preset_id not in PRESETS:
        raise ValueError(
            f"items_for_preset does not resolve {preset_id!r} -- "
            f"\"custom\" is the caller's own checked-item state, not a "
            f"data-driven preset; light/thorough/aggressive are the only "
            f"valid ids here")
    allowed = PRESETS[preset_id]["safety_levels"]
    selected = []
    for cid, result in results.items():
        scanner_name = id_to_scanner_name.get(cid)
        if scanner_name in NEVER_INCLUDED:
            continue
        items = getattr(result, "items", None)
        if items is None:
            continue  # e.g. browser results are a list of BrowserResult, not a ScanResult
        for item in items:
            if item.safety in allowed:
                selected.append(item)
    return selected
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cleanup_presets.py -v`
Expected: PASS, all 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/modules/cleanup/cleanup_presets.py tests/test_cleanup_presets.py
git commit -m "feat(cleanup): add the preset data module (Light/Thorough/Aggressive + NEVER_INCLUDED)

Plain safety-level allow-sets, not a JSON-plus-resolver like Debloat's
presets -- Cleanup only ever needs one axis (safety level), not two
catalogs to reconcile. NEVER_INCLUDED is a hard exclusion by scanner
name (orphaned profiles, virtual disks) that no preset, including
Aggressive, may ever override."
```

---

## Task 2: Wire the preset combo box into `QuickCleanupTab`'s toolbar

**Files:**
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Test: `tests/test_quick_cleanup_presets_ui.py` (new)

**Interfaces:**
- Consumes: `cleanup_presets.preset_names()` (Task 1).
- Produces: `QuickCleanupTab._preset_combo` (a `QComboBox`), `QuickCleanupTab._id_to_scanner_name` (a `Dict[str, Optional[str]]`, built once in `build()`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quick_cleanup_presets_ui.py`:

```python
"""The preset combo box on Quick Cleanup's toolbar, and the mapping it
needs (category id -> real scanner function name) to let the presets
module apply NEVER_INCLUDED correctly.
"""


def test_preset_combo_exists_and_defaults_to_light(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    assert hasattr(tab, "_preset_combo")
    assert tab._preset_combo.currentData() == "light"


def test_preset_combo_offers_all_four_presets(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup.cleanup_presets import preset_names

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    offered = [tab._preset_combo.itemData(i) for i in range(tab._preset_combo.count())]
    assert set(offered) == set(preset_names())


def test_changing_preset_updates_the_clean_button_label(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    light_label = tab._clean_all_btn.text()
    idx = tab._preset_combo.findData("aggressive")
    assert idx != -1
    tab._preset_combo.setCurrentIndex(idx)

    assert tab._clean_all_btn.text() != light_label
    assert "Aggressive" in tab._clean_all_btn.text()


def test_id_to_scanner_name_maps_a_known_category(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()
    tab.build()  # defaults: real CLEANUP_CATEGORIES + ADVANCED_CATEGORIES

    assert tab._id_to_scanner_name.get("temp") == "scan_temp_files"
    # "browser" has no scanner function (handled specially) -- must map
    # to None, not raise or be silently absent.
    assert "browser" in tab._id_to_scanner_name
    assert tab._id_to_scanner_name["browser"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_quick_cleanup_presets_ui.py -v`
Expected: FAIL — `AttributeError: 'QuickCleanupTab' object has no attribute '_preset_combo'`.

- [ ] **Step 3: Add `_id_to_scanner_name` to `build()`**

In `src/modules/cleanup/components/quick_cleanup_tab.py`, `build()`, right after the existing block that builds `self._adv_scanner_map` (immediately before `self._setup_ui()` is called), add:

```python
        self._id_to_scanner_name = {
            cid: (fn.__name__ if fn else None)
            for cid, (fn, _label, _color) in
            {**self._scanner_map, **self._adv_scanner_map}.items()
        }
```

- [ ] **Step 4: Add `QComboBox` to the imports**

Near the top of the file, find the existing `from PyQt6.QtWidgets import (...)` block and add `QComboBox` to it (alongside `QWidget`, `QVBoxLayout`, etc.).

- [ ] **Step 5: Add the combo box to the toolbar**

In `_setup_ui`, replace:

```python
        # ── Top toolbar ──
        toolbar = QHBoxLayout()
        self._scan_all_btn = QPushButton("🔍  Scan All")
        self._scan_all_btn.clicked.connect(self.scan)
        self._clean_all_btn = QPushButton("🗑️  Clean All Safe")
        self._clean_all_btn.setEnabled(False)
        self._clean_all_btn.clicked.connect(self._do_clean_all_safe)
        self._status_lbl = QLabel("Click Scan All to analyze your system")
        self._status_lbl.setObjectName("muted")
        self._show_adv_btn = QPushButton("Show Advanced ▼")
        self._show_adv_btn.setStyleSheet("font-size: 12px; padding: 4px 10px;")
        self._show_adv_btn.clicked.connect(self._toggle_advanced)
        self._adv_shown = False
        toolbar.addWidget(self._scan_all_btn)
        toolbar.addWidget(self._clean_all_btn)
        toolbar.addWidget(self._show_adv_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)
```

with:

```python
        # ── Top toolbar ──
        toolbar = QHBoxLayout()
        self._scan_all_btn = QPushButton("🔍  Scan All")
        self._scan_all_btn.clicked.connect(self.scan)

        from modules.cleanup.cleanup_presets import PRESETS, preset_names
        self._preset_combo = QComboBox()
        self._preset_combo.setToolTip(
            "Which items \"Clean\" includes when you click it -- Light is "
            "the original behavior, Aggressive includes danger-level items "
            "(except orphaned profiles/virtual disks, never included by any preset)."
        )
        for pid in preset_names():
            label = PRESETS[pid]["label"] if pid in PRESETS else "Custom"
            self._preset_combo.addItem(label, pid)
        self._preset_combo.setCurrentIndex(self._preset_combo.findData("light"))
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        self._clean_all_btn = QPushButton("🗑️  Clean (Light)")
        self._clean_all_btn.setEnabled(False)
        self._clean_all_btn.clicked.connect(self._do_clean_all_safe)
        self._status_lbl = QLabel("Click Scan All to analyze your system")
        self._status_lbl.setObjectName("muted")
        self._show_adv_btn = QPushButton("Show Advanced ▼")
        self._show_adv_btn.setStyleSheet("font-size: 12px; padding: 4px 10px;")
        self._show_adv_btn.clicked.connect(self._toggle_advanced)
        self._adv_shown = False
        toolbar.addWidget(self._scan_all_btn)
        toolbar.addWidget(self._preset_combo)
        toolbar.addWidget(self._clean_all_btn)
        toolbar.addWidget(self._show_adv_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._status_lbl)
        layout.addLayout(toolbar)
```

- [ ] **Step 6: Add `_on_preset_changed`**

Add this method anywhere in the class (e.g. right after `_setup_ui`):

```python
    def _on_preset_changed(self, _index: int) -> None:
        from modules.cleanup.cleanup_presets import PRESETS
        pid = self._preset_combo.currentData()
        label = PRESETS[pid]["label"] if pid in PRESETS else "Custom"
        self._clean_all_btn.setText(f"🗑️  Clean ({label})")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_quick_cleanup_presets_ui.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 8: Run the broader Quick Cleanup suite to check for regressions**

Run: `pytest tests/test_quick_cleanup_dedupe.py tests/test_quick_cleanup_legend_theme.py tests/test_quick_cleanup_watchdog.py tests/test_quick_cleanup_one_click_actions.py tests/test_quick_cleanup_category_navigation.py tests/test_quick_cleanup_new_actions.py -v`
Expected: PASS, unchanged — this task only adds a new widget and a new dict, doesn't change any existing behavior yet (Task 3 does).

- [ ] **Step 9: Commit**

```bash
git add src/modules/cleanup/components/quick_cleanup_tab.py tests/test_quick_cleanup_presets_ui.py
git commit -m "feat(cleanup): add the preset selector to Quick Cleanup's toolbar

Defaults to Light (today's exact existing behavior). Changing it updates
the Clean button's label so it's always clear what a click will do.
Behavior itself (Task 3) is unchanged by this step -- this is UI
plumbing only."
```

---

## Task 3: Make the bulk clean and its button-enable logic preset-aware

**Files:**
- Modify: `src/modules/cleanup/components/quick_cleanup_tab.py`
- Test: `tests/test_quick_cleanup_presets_ui.py` (same file as Task 2 — extend it)

**Interfaces:**
- Consumes: `cleanup_presets.items_for_preset` (Task 1), `self._preset_combo`/`self._id_to_scanner_name` (Task 2).
- Produces: `_do_clean_all_safe` now filters by the active preset instead of a hardcoded `safety=="safe"`; `_has_safe_items` is renamed `_has_cleanable_items` and does the same; `_on_all_scanned`'s `_clean_all_btn.setEnabled(...)` line uses the new preset-aware check instead of the old `total_safe > 0`.

- [ ] **Step 1: Add tests to `tests/test_quick_cleanup_presets_ui.py`**

Append to the file created in Task 2:

```python
def _built_tab_with_result(qapp, safe=0, caution=0, danger=0):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab
    from modules.cleanup.cleanup_scanner import ScanItem, ScanResult

    tab = QuickCleanupTab()
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    result = ScanResult()
    for n, safety in ((safe, "safe"), (caution, "caution"), (danger, "danger")):
        for i in range(n):
            result.items.append(ScanItem(path=f"C:\\{safety}_{i}", size=100, is_dir=False, safety=safety))
    result.total_size = sum(i.size for i in result.items)
    tab._results = {"temp": result}
    return tab


def test_custom_preset_preserves_the_original_safe_only_behavior(qapp, monkeypatch):
    """Regression test: before this plan, _do_clean_all_safe always
    behaved like this. "Custom" must keep doing exactly this, unchanged."""
    from modules.cleanup import cleanup_scanner as cs
    from PyQt6.QtWidgets import QMessageBox

    tab = _built_tab_with_result(qapp, safe=2, caution=1, danger=1)
    idx = tab._preset_combo.findData("custom")
    tab._preset_combo.setCurrentIndex(idx)

    captured = {}
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    def fake_run_clean_safe(widget, items, **kwargs):
        captured["items"] = items
        return None
    monkeypatch.setattr("modules.cleanup.clean_safe_runner.run_clean_safe", fake_run_clean_safe)

    tab._do_clean_all_safe()

    assert len(captured["items"]) == 2
    assert all(i.safety == "safe" for i in captured["items"])


def test_thorough_preset_includes_caution_items_in_the_clean_call(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    tab = _built_tab_with_result(qapp, safe=2, caution=3, danger=1)
    idx = tab._preset_combo.findData("thorough")
    tab._preset_combo.setCurrentIndex(idx)

    captured = {}
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    def fake_run_clean_safe(widget, items, **kwargs):
        captured["items"] = items
        return None
    monkeypatch.setattr("modules.cleanup.clean_safe_runner.run_clean_safe", fake_run_clean_safe)

    tab._do_clean_all_safe()

    assert len(captured["items"]) == 5
    assert all(i.safety in ("safe", "caution") for i in captured["items"])


def test_aggressive_preset_still_excludes_never_included_scanners(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    tab = _built_tab_with_result(qapp, safe=1, danger=1)
    # Force the "temp" category's scanner name to a NEVER_INCLUDED one to
    # prove the exclusion is genuinely applied end-to-end, not just in
    # cleanup_presets.py's own unit tests.
    tab._id_to_scanner_name["temp"] = "scan_orphaned_user_profiles"
    idx = tab._preset_combo.findData("aggressive")
    tab._preset_combo.setCurrentIndex(idx)

    captured = {}
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    def fake_run_clean_safe(widget, items, **kwargs):
        captured["items"] = items
        return None
    monkeypatch.setattr("modules.cleanup.clean_safe_runner.run_clean_safe", fake_run_clean_safe)

    tab._do_clean_all_safe()

    assert captured.get("items", []) == []


def test_has_cleanable_items_is_preset_aware(qapp):
    tab = _built_tab_with_result(qapp, caution=1)  # no "safe" items at all

    idx = tab._preset_combo.findData("light")
    tab._preset_combo.setCurrentIndex(idx)
    assert tab._has_cleanable_items() is False, "Light must not see a caution-only result as cleanable"

    idx = tab._preset_combo.findData("thorough")
    tab._preset_combo.setCurrentIndex(idx)
    assert tab._has_cleanable_items() is True, "Thorough must see the caution item as cleanable"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_quick_cleanup_presets_ui.py -v -k "custom_preset or thorough_preset or aggressive_preset or has_cleanable"`
Expected: FAIL — `_do_clean_all_safe` still hardcodes `safety=="safe"` regardless of preset, and `_has_cleanable_items` doesn't exist yet (`_has_safe_items` does, under the old name).

- [ ] **Step 3: Rename `_has_safe_items` to `_has_cleanable_items` and make it preset-aware**

Replace the existing method:

```python
    def _has_safe_items(self) -> bool:
        """Is there at least one "safe" item anywhere in the last scan
        results? Mirrors the condition _on_all_scanned uses to enable
        _clean_all_btn, so a cancelled/timed-out scan and a completed one
        agree on when there is actually something to clean."""
        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs

        for cid, result in self._results.items():
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = result or []
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                return True
            elif isinstance(result, cs.ScanResult):
                if any(item.safety == "safe" for item in result.items):
                    return True
        return False
```

with:

```python
    def _has_cleanable_items(self) -> bool:
        """Is there at least one item the CURRENT preset would clean?
        Mirrors what _do_clean_all_safe actually does, so a cancelled/
        timed-out scan and a completed one agree on when there is
        genuinely something to clean under whatever preset is selected."""
        from modules.cleanup import cleanup_scanner as cs
        from modules.cleanup import browser_scanner as bs
        from modules.cleanup import cleanup_presets

        preset_id = self._preset_combo.currentData() if hasattr(self, "_preset_combo") else "light"

        for cid, result in self._results.items():
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = result or []
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                return True
            elif isinstance(result, cs.ScanResult):
                if preset_id == "custom":
                    if any(item.safety == "safe" for item in result.items):
                        return True
                else:
                    items = cleanup_presets.items_for_preset(
                        preset_id, {cid: result}, self._id_to_scanner_name)
                    if items:
                        return True
        return False
```

- [ ] **Step 4: Update the one other call site (`_reset_after_cancel`)**

Find `self._clean_all_btn.setEnabled(self._has_safe_items())` and change it to:

```python
            self._clean_all_btn.setEnabled(self._has_cleanable_items())
```

- [ ] **Step 5: Update `_on_all_scanned`'s button-enable line**

Find `self._clean_all_btn.setEnabled(total_safe > 0)` (in `_on_all_scanned`, right after the "Advanced categories" loop, alongside `self._safe_lbl.setText(...)`) and change ONLY that one line — leave `total_safe`'s own computation and the "Safe to clean" label's text completely unchanged, since that label is a factual measurement independent of the active preset:

```python
        self._clean_all_btn.setEnabled(self._has_cleanable_items())
```

- [ ] **Step 6: Make `_do_clean_all_safe` preset-aware**

In `_do_clean_all_safe`, replace the item-collection loop:

```python
        for cid in self._results:
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = self._results.get(cid, [])
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                browser_cats.append(cat)
                                total += cat.size_bytes
            else:
                result: cs.ScanResult = self._results.get(cid, cs.ScanResult())
                for item in result.items:
                    if item.safety == "safe":
                        item.selected = True
                        all_safe.append(item)
                        total += item.size
                if cid == "wu":
                    needs_wu = True
```

with:

```python
        from modules.cleanup import cleanup_presets
        preset_id = self._preset_combo.currentData()

        for cid in self._results:
            if cid == "browser":
                browser_results: List[bs.BrowserResult] = self._results.get(cid, [])
                for r in browser_results:
                    for profile in r.profiles:
                        for cat in profile.categories:
                            if cat.size_bytes > 0:
                                browser_cats.append(cat)
                                total += cat.size_bytes
            elif cid == "wu" and cid in self._results:
                needs_wu = True

        if preset_id == "custom":
            for cid, result in self._results.items():
                if cid == "browser":
                    continue
                for item in result.items:
                    if item.safety == "safe":
                        item.selected = True
                        all_safe.append(item)
                        total += item.size
        else:
            selected_items = cleanup_presets.items_for_preset(
                preset_id, {k: v for k, v in self._results.items() if k != "browser"},
                self._id_to_scanner_name)
            for item in selected_items:
                item.selected = True
                all_safe.append(item)
                total += item.size
```

Note the `needs_wu` detection was pulled out into its own small loop pass (`elif cid == "wu" and cid in self._results:`) since it's about "was the WU category even scanned," independent of which preset is active or which items got selected from it — under Custom it stays exactly as it always was; under a data-driven preset, WU's own `caution`-level items are already included/excluded by `items_for_preset` like everything else, so `needs_wu` only needs to answer "is WU part of what got cleaned," which the presence of the category key already tells us. Double-check this reasoning holds by running Step 7's tests — if `needs_wu` needs to instead reflect "did WU actually contribute a selected item," adjust to check membership of `"wu"` against `{item... }`'s originating category instead of just key presence; the existing test suite (Step 7) will show whether this simplification is behaviorally sufficient.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_quick_cleanup_presets_ui.py -v`
Expected: PASS, all tests in the file (4 from Task 2, 5 from this task).

- [ ] **Step 8: Run the full regression suite for this file and its dependents**

Run: `pytest tests/test_quick_cleanup_dedupe.py tests/test_quick_cleanup_legend_theme.py tests/test_quick_cleanup_watchdog.py tests/test_quick_cleanup_one_click_actions.py tests/test_quick_cleanup_category_navigation.py tests/test_quick_cleanup_new_actions.py tests/test_cleanup_module_quick_tab.py tests/test_cleanup_run_clean_safe.py -v`
Expected: PASS. Pay particular attention to any test that exercises `_do_clean_all_safe`'s WU-related behavior — if one fails on the `needs_wu` simplification noted in Step 6, fix `needs_wu`'s detection to check whether any of the FINAL selected items actually came from the `"wu"` category, rather than just checking category presence in `self._results`.

- [ ] **Step 9: Run the FULL test suite**

Run: `pytest -q --timeout=300` (QT_QPA_PLATFORM=offscreen). Confirm exit code 0, no FAILURES section, no crash. Given this codebase's own recent history (both prior sub-projects in this sequence found real full-suite-only issues that narrower runs missed), do not skip this step or substitute a narrower one.

- [ ] **Step 10: Commit**

```bash
git add src/modules/cleanup/components/quick_cleanup_tab.py tests/test_quick_cleanup_presets_ui.py
git commit -m "feat(cleanup): make the dashboard bulk clean and its button-enable logic preset-aware

_do_clean_all_safe now reads the active preset (Light/Thorough/
Aggressive/Custom) instead of a hardcoded safety==\"safe\" rule. Custom
preserves today's exact original behavior unchanged, pinned by a
regression test. _has_cleanable_items (renamed from _has_safe_items)
and _on_all_scanned's button-enable check are both preset-aware too, so
the Clean button correctly enables when only caution/danger items exist
under Thorough/Aggressive."
```

---

## Self-Review

**Spec coverage:** §2's data module (Task 1), §3's UI change (Task 2), §4's `_id_to_scanner_name` (Task 2 Step 3), the `_do_clean_all_safe` integration (Task 3) are all covered. §1's Custom ruling (== current checked-item behavior on this dashboard, i.e. today's exact safe-only rule) is pinned by Task 3's own regression test rather than left as an assumption.

**Placeholder scan:** Task 3 Step 6 contains one explicitly-flagged, bounded uncertainty (the `needs_wu` simplification) with a concrete verification-and-fix instruction attached (run the tests, adjust if they fail) — not a bare TBD, and resolved within the same task rather than deferred.

**Type consistency:** `items_for_preset`'s signature (`preset_id: str, results: dict, id_to_scanner_name: dict) -> list`) is used identically in Task 1's own tests and Task 3's `_do_clean_all_safe`/`_has_cleanable_items` call sites. `_preset_combo`/`_id_to_scanner_name` names introduced in Task 2 are the exact names Task 3 consumes.
