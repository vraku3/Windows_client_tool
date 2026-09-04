# Debloat & Driver Manager Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 11 verified defects and implement the 139 improvements
found while auditing `driver_manager/`, `debloat/` and `store_apps/`,
without regressing anything the existing test suite already protects.

**Architecture:** No new subsystem — this extends three existing modules
(`DriverModule`, `DebloatToolsModule`/`DebloatModule`/`StoreAppsModule`) and
adds a small amount of shared infrastructure two or more of them need
(`core/table_ui.NumericSortItem`, a Debloat preset loader, a restore-point
session policy). Every task keeps the module's existing shape — Worker for
background work, `require_admin()` before a write, `BackupService` before a
destructive change — rather than introducing a new pattern.

**Tech Stack:** PyQt6, pywin32/WMI, pytest + pytest-qt-style `qapp` fixture
already in `tests/conftest.py`. No new dependencies.

**Spec:** [Debloat & Driver Audit](https://claude.ai/code/artifact/c8929a86-13c6-413f-a6e7-2baabe0701c7)
— the published 150-item review this plan implements. Item IDs below
(`V01`–`V11`, `D01`–`D33`, `A01`–`A25`, `T01`–`T25`, `P01`–`P15`, `S01`–`S30`,
`C01`–`C11`) refer to that document; the coverage map at the end of this
plan maps every one to the task that resolves it.

## Global Constraints

- Every write action (uninstall, tweak apply, driver delete) goes through
  `self.app.backup` and `TweakEngine`/`BackupService`, never a bare
  `subprocess.run` with no restore point — matching every existing write
  path in these three modules.
- Every destructive UI action is confirmed via `core/confirm.confirm_destructive`
  going forward (not retrofitted onto pre-existing call sites that already
  hand-roll their own `QMessageBox`, per `CLAUDE.md`'s explicit rule — but
  every *new* confirmation this plan adds uses the shared helper).
- `except Exception: pass` (or any bare exception swallow) is forbidden;
  every catch logs via `logger.warning`/`logger.error` — this is an existing
  project-wide rule, not new to this plan.
- No test in this plan touches a real, currently-installed driver or app.
  Driver deletion, app uninstall, and tweak-apply logic are tested against
  fakes/monkeypatched subprocess calls only — the same discipline used
  throughout this session's Monitor Control work (self-reverting real-machine
  probes only for read-only verification, never for a destructive write).
- `pyinstaller_common.py`'s `HIDDEN_IMPORTS` gets any new function-scoped
  `ui.*`/`modules.*` import this plan introduces — checked per task, not
  deferred to the end.
- Every task ends with `pytest tests/ -q` green before its commit.

---

## Phase 0 — Shared groundwork

### Task 1: `NumericSortItem` — a table item that sorts by value, not by text

**Files:**
- Modify: `src/core/table_ui.py`
- Test: `tests/test_table_ui.py` (new)

**Interfaces:**
- Produces: `NumericSortItem(text: str, value: float)` — a `QTableWidgetItem`
  subclass. `Qt.ItemDataRole.UserRole + 10` holds the numeric value;
  `__lt__` compares on that role, falling back to text comparison if either
  side never got a value (so mixing it with plain items in the same column
  degrades to today's behavior rather than raising).

This is the fix behind `V08` (Store Apps' Size/Version columns sorting as
text) and every other module's version of the same bug (`D20`). One class,
reused by Tasks 24 and 31.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_table_ui.py
from PyQt6.QtWidgets import QTableWidget
from core.table_ui import NumericSortItem


def test_numeric_sort_orders_by_value_not_text(qapp):
    table = QTableWidget(3, 1)
    # Text order would put "10 GB" before "9 GB". Value order must not.
    table.setItem(0, 0, NumericSortItem("500 MB", 500_000_000))
    table.setItem(1, 0, NumericSortItem("9 GB", 9_000_000_000))
    table.setItem(2, 0, NumericSortItem("10 GB", 10_000_000_000))
    table.setSortingEnabled(True)
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    assert [table.item(r, 0).text() for r in range(3)] == \
        ["500 MB", "9 GB", "10 GB"]


def test_numeric_sort_degrades_to_text_against_a_plain_item(qapp):
    """A column mixing NumericSortItem with a bare QTableWidgetItem (e.g. a
    row whose size could not be measured) must not raise."""
    table = QTableWidget(2, 1)
    table.setItem(0, 0, NumericSortItem("12 MB", 12_000_000))
    table.setItem(1, 0, QTableWidgetItem("n/a"))
    table.setSortingEnabled(True)
    table.sortItems(0, Qt.SortOrder.AscendingOrder)  # must not raise
```

Add `from PyQt6.QtCore import Qt` and
`from PyQt6.QtWidgets import QTableWidgetItem` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_table_ui.py -v`
Expected: FAIL — `ImportError: cannot import name 'NumericSortItem'`

- [ ] **Step 3: Write the implementation**

Add to `src/core/table_ui.py`, near the existing `_SortableItem`:

```python
#: Where a NumericSortItem's real value lives. Past UserRole+1..9, which
#: several modules already use for their own per-cell payload (e.g. a
#: package name or tweak id) — +10 is unclaimed.
_NUMERIC_SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 10


class NumericSortItem(QTableWidgetItem):
    """A cell that DISPLAYS formatted text but SORTS on a real number.

    Every "Size" or "Version" column that formats bytes/versions into text
    before display has this bug latently: a plain QTableWidgetItem sorts
    that text alphabetically, so "10 GB" lands before "9 GB". Store the
    real value once, here, and every such column gets a correct sort for
    the cost of one extra constructor argument.
    """

    def __init__(self, text: str, value: float):
        super().__init__(text)
        self.setData(_NUMERIC_SORT_ROLE, float(value))

    def __lt__(self, other) -> bool:
        mine = self.data(_NUMERIC_SORT_ROLE)
        theirs = other.data(_NUMERIC_SORT_ROLE) \
            if isinstance(other, QTableWidgetItem) else None
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text().lower() < other.text().lower()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_table_ui.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite and commit**

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/core/table_ui.py tests/test_table_ui.py
git commit -m "feat(table_ui): NumericSortItem — sort by value, not formatted text

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Fix the catalog drift (`V01`, `V02`) and remove dead code (`V11`)

**Files:**
- Modify: `src/modules/debloat/debloat_scanner.py`
- Test: `tests/test_debloat_scanner.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: `debloat_scanner.KNOWN_PACKAGES` now a superset of every
  `package` in `debloat.json`; `check_app_installed` removed.

This is `V01`/`V02` from the audit, verified with a script during the
review: 12 packages in `debloat.json` (including
`Microsoft.Windows.Recall`, `Microsoft.Windows.Copilot`,
`Microsoft.SecHealthUI`) are missing from `KNOWN_PACKAGES`; 18 packages in
`KNOWN_PACKAGES` have no `debloat.json` entry. Fix both directions, and
delete `check_app_installed`, which nothing calls (`V11`).

- [ ] **Step 1: Write the failing test — pins the exact drift found**

```python
# tests/test_debloat_scanner.py
import json
import os

from modules.debloat import debloat_scanner as ds

_CATALOG = os.path.join(os.path.dirname(__file__), "..", "src", "modules",
                        "tweaks", "definitions", "debloat.json")


def _catalog_packages():
    with open(_CATALOG, encoding="utf-8") as f:
        entries = json.load(f)
    return {e["package"] for e in entries if e.get("package")}


def test_every_catalog_package_is_in_known_packages():
    """The bug: 12 debloat.json entries — Recall, Copilot, SecHealthUI
    among them — could never be detected as installed because
    KNOWN_PACKAGES never listed their package id."""
    missing = _catalog_packages() - ds.KNOWN_PACKAGES
    assert missing == set(), (
        f"in debloat.json but not KNOWN_PACKAGES (never detectable): "
        f"{sorted(missing)}")


def test_check_app_installed_was_removed():
    """Dead code: defined, called nowhere in src/."""
    assert not hasattr(ds, "check_app_installed")
```

Note: `test_every_catalog_package_is_in_known_packages` only checks one
direction (catalog → KNOWN_PACKAGES) because that direction is the one that
actually hides a feature. The reverse direction (`KNOWN_PACKAGES` entries
with no catalog record) is real dead weight but not a silent failure —
`Task 3`'s `test_debloat_definitions.py` covers it from the catalog side
instead, so it's caught as part of catalog validation rather than scanner
validation.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_scanner.py -v`
Expected: FAIL — `test_every_catalog_package_is_in_known_packages` lists the
12 missing packages; `test_check_app_installed_was_removed` fails because
the function still exists.

- [ ] **Step 3: Fix `KNOWN_PACKAGES` and remove the dead function**

In `src/modules/debloat/debloat_scanner.py`, add the 12 missing packages to
`KNOWN_PACKAGES` (placed in the existing grouped-by-comment style):

```python
    # Windows 11 24H2/25H2 AI & shell features (added 2026-09; these are
    # exactly the packages a debloat catalog exists to remove, and were
    # silently undetectable — debloat.json listed them, this set did not)
    "Microsoft.AI.Landscape", "Microsoft.Windows.Copilot",
    "Microsoft.Windows.Recall", "Microsoft.Windows.Wcr",
    "Microsoft.SecHealthUI", "Microsoft.Windows.WebExperience",
    "Microsoft.Windows.DeskApp", "Microsoft.Photos.Import",
    "Microsoft.ScreenCapture", "Microsoft.Windows.Paint.Cocreator",
    "Microsoft.Windows.Podcasts", "Microsoft.Windows.StudioDesign",
```

Remove the entire `check_app_installed` function (currently lines 87-95)
and its now-unused `ps_quote` import if nothing else in the file uses it
(check with `grep -n ps_quote src/modules/debloat/debloat_scanner.py` —
`subprocess` import may also become unused; remove it too if so).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_scanner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite and commit**

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_scanner.py tests/test_debloat_scanner.py
git commit -m "fix(debloat): 12 catalogued packages — Recall, Copilot, Defender —
were undetectable

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `tests/test_debloat_definitions.py` — catalog validation

**Files:**
- Create: `tests/test_debloat_definitions.py`

**Interfaces:**
- Consumes: `debloat_scanner.KNOWN_PACKAGES`, `debloat.json`, the four
  `builtins/debloat_*.json` preset files.
- Produces: nothing new — a validation suite only, mirroring
  `tests/test_tweak_definitions.py`'s role for the main tweak catalog.

Resolves `P04`, `P05`, `P10`, `P12`. This is what would have caught `V01`/
`V02` automatically, and is what stops it recurring — every future catalog
edit runs through this.

- [ ] **Step 1: Write the tests**

```python
# tests/test_debloat_definitions.py
import json
import os

import pytest

from modules.debloat import debloat_scanner as ds

_BASE = os.path.join(os.path.dirname(__file__), "..", "src", "modules")
_CATALOG = os.path.join(_BASE, "tweaks", "definitions", "debloat.json")
_BUILTINS = os.path.join(_BASE, "tweaks", "definitions", "builtins")
_PRESETS = ["debloat_light.json", "debloat_full.json",
           "debloat_privacy.json", "debloat_custom.json"]


@pytest.fixture(scope="module")
def catalog():
    with open(_CATALOG, encoding="utf-8") as f:
        return json.load(f)


def test_every_entry_has_a_unique_id(catalog):
    ids = [e["id"] for e in catalog]
    assert len(ids) == len(set(ids)), "duplicate id(s) in debloat.json"


def test_every_entry_has_a_package_and_the_package_is_detectable(catalog):
    for entry in catalog:
        pkg = entry.get("package")
        assert pkg, f"{entry.get('id')}: no package field"
        assert pkg in ds.KNOWN_PACKAGES, (
            f"{entry['id']}: package {pkg!r} is not in "
            f"debloat_scanner.KNOWN_PACKAGES, so it can never be detected "
            f"as installed")


def test_every_entry_has_a_category_and_name(catalog):
    for entry in catalog:
        assert entry.get("name"), f"{entry.get('id')}: no name"
        assert entry.get("category"), f"{entry.get('id')}: no category"


@pytest.mark.parametrize("filename", _PRESETS)
def test_preset_tweak_ids_resolve_against_the_main_tweak_catalog(filename):
    """A preset's `tweaks` block names category -> [tweak ids]. Every id it
    names (other than the "*" wildcard, meaning "everything in this
    category") must exist in one of the six tweak definition files Debloat
    loads, or it silently loses that entry the moment someone applies the
    preset."""
    with open(os.path.join(_BUILTINS, filename), encoding="utf-8") as f:
        preset = json.load(f)

    all_tweak_ids = set()
    for fname in ("privacy.json", "telemetry.json", "services.json",
                  "network.json", "ai_features.json", "navigation.json"):
        path = os.path.join(_BASE, "tweaks", "definitions", fname)
        with open(path, encoding="utf-8") as f:
            all_tweak_ids.update(t["id"] for t in json.load(f))

    for category, ids in preset.get("tweaks", {}).items():
        for tid in ids:
            if tid == "*":
                continue
            assert tid in all_tweak_ids, (
                f"{filename}: tweak id {tid!r} (category {category!r}) "
                f"does not exist in any loaded tweak definition file")


@pytest.mark.parametrize("filename", _PRESETS)
def test_preset_app_packages_resolve_against_the_debloat_catalog(
        filename, catalog):
    catalog_packages = {e["package"] for e in catalog}
    with open(os.path.join(_BUILTINS, filename), encoding="utf-8") as f:
        preset = json.load(f)
    apps = preset.get("apps", {})
    for key in ("remove", "remove_protected"):
        for pkg in apps.get(key, []):
            assert pkg in catalog_packages, (
                f"{filename}: apps.{key} names {pkg!r}, not in "
                f"debloat.json's catalog")
```

- [ ] **Step 2: Run and confirm it passes now** (Task 2 already fixed the
  underlying drift)

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_definitions.py -v`
Expected: PASS — if any test here fails, Task 2 was incomplete; fix
`KNOWN_PACKAGES` before continuing, don't weaken this test.

- [ ] **Step 3: Run the full suite and commit**

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add tests/test_debloat_definitions.py
git commit -m "test(debloat): validate the catalog + presets against each other

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Phase 1 — Debloat status vocabulary (`V04`, `V05`, `T01`, `T02`, `T04`)

### Task 4: Five-value status, with a reason, on both tweaks tables

**Files:**
- Modify: `src/modules/debloat/debloat_module.py:434-487` (`_populate_tweaks_table`)
- Create: `tests/test_debloat_module.py`

**Interfaces:**
- Consumes: `TweakEngine.detect(tweak) -> DetectionResult` (already exists,
  `tweak_engine.py:530`), `tweak_engine.STATUS_LABELS` (already exists,
  `tweak_engine.py:44-50`).
- Produces: `_populate_tweaks_table` now calls `engine.detect(tweak)`
  instead of `engine.detect_status(tweak)`, so `.reason` becomes available
  as a tooltip.

`V04`: `detect_status()`'s five real values were being squeezed through a
3-entry `status_map`, so `partial` and `not_applicable` both silently
rendered as "○ Unknown". `V05`: no tooltip ever showed `.reason`. `T01`:
give Risk the same semantic-color treatment the Performance Tuner
reference pattern uses. `T02`: the five colors need a legend, added as a
one-line caption under the preset buttons. `T04`: once `not_applicable` is
its own real status (not folded into unknown), a `requires_gpedit` tweak
on Home edition now correctly shows "Not Applicable" instead of looking
checkable with no error.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_debloat_module.py
"""DebloatToolsModule's tweaks tables: the five-value status vocabulary,
shown with a reason, is what this file pins down. Nothing here touches a
real machine -- TweakEngine.detect is monkeypatched throughout.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt

from modules.debloat import debloat_module as dm
from modules.tweaks import tweak_engine as te


class _FakeBackup:
    def create_restore_point(self, label, module):
        return "rp-fake"

    def record_steps(self, *a, **k):
        pass


class _FakeApp:
    def __init__(self):
        self.backup = _FakeBackup()
        self.thread_pool = None


def _module():
    mod = dm.DebloatToolsModule()
    mod.on_start(_FakeApp())
    mod.create_widget()
    return mod


def _tweak(id_, status, reason):
    return {"id": id_, "name": f"Tweak {id_}", "category": "Privacy",
            "risk": "Low"}, te.DetectionResult(status=status, reason=reason)


def test_all_five_statuses_render_as_five_distinct_labels(monkeypatch):
    mod = _module()
    fakes = [
        _tweak("a", te.APPLIED, "the value matches"),
        _tweak("b", te.NOT_APPLIED, "the key does not exist"),
        _tweak("c", te.PARTIAL, "3 of 5 steps are in place"),
        _tweak("d", te.NOT_APPLICABLE, "this edition has no Group Policy"),
        _tweak("e", te.UNKNOWN, "access denied reading the key"),
    ]
    tweaks = [t for t, _ in fakes]
    results = {t["id"]: r for t, r in fakes}
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, tweak: results[tweak["id"]])

    mod._populate_tweaks_table("tweak")

    table = mod._widget.findChild(type(mod._widget), "_table_tweak") \
        or mod._widget.findChild(mod._widget.__class__)
    # Look the table up the same way the module itself does.
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")

    labels = {table.item(r, 4).text() for r in range(table.rowCount())}
    assert labels == {
        "● Applied", "○ Not Applied", "◑ Partially Applied",
        "– Not Applicable", "❓ Unknown",
    }


def test_every_row_carries_its_reason_as_a_tooltip(monkeypatch):
    mod = _module()
    tweak, result = _tweak("a", te.PARTIAL, "3 of 5 steps are in place")
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect", lambda self, t: result)

    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    assert table.item(0, 4).toolTip() == "3 of 5 steps are in place"


def test_a_bare_status_with_no_reason_still_shows_something(monkeypatch):
    """The bug this whole task exists to prevent: a status with nothing
    behind it. Never blank."""
    mod = _module()
    tweak, result = _tweak("a", te.UNKNOWN, "")
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect", lambda self, t: result)

    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    assert table.item(0, 4).toolTip()  # non-empty
```

Delete the dead first two lines of `test_all_five_statuses_render_as_five_distinct_labels`
(the `mod._widget.findChild(type(mod._widget), ...)` line above the real
lookup) before running — leftover from drafting; the real lookup via
`QTableWidget` is the one that matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_module.py -v`
Expected: FAIL — only 3 distinct labels render today, not 5; no tooltip is
ever set.

- [ ] **Step 3: Rewrite the status block**

Replace `_populate_tweaks_table`'s status section
(`debloat_module.py:470-481`):

```python
            status = engine.detect_status(tweak)
            status_map = {
                "applied": ("● Applied", QColor(semantic("success"))),
                "not_applied": ("○ Not Applied", QColor("#e0e0e0")),
                "unknown": ("○ Unknown", QColor("#888888")),
            }
            status_text, status_color = status_map.get(status, status_map["unknown"])
            si = QTableWidgetItem(status_text)
            si.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            si.setForeground(status_color)
            si.setData(Qt.ItemDataRole.UserRole, tweak.get("id", ""))
            table.setItem(row, 4, si)
```

with:

```python
            result = engine.detect(tweak)
            si = QTableWidgetItem(_STATUS_GLYPH[result.status]
                                  + " " + te.STATUS_LABELS[result.status])
            si.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            si.setForeground(QColor(_STATUS_COLOR[result.status]))
            si.setData(Qt.ItemDataRole.UserRole, tweak.get("id", ""))
            # Never blank: a status with nothing behind it is the bug this
            # design exists to prevent (CLAUDE.md, TweakEngine.detect).
            si.setToolTip(result.reason or te.STATUS_LABELS[result.status])
            table.setItem(row, 4, si)
```

Add near the top of the file, alongside `logger = logging.getLogger(__name__)`:

```python
from modules.tweaks import tweak_engine as te

#: One glyph and one semantic color per TweakEngine status. Five entries
#: for five real values -- `.get(..., status_map["unknown"])` (the old
#: code) is exactly how `partial` and `not_applicable` both silently
#: rendered as "Unknown" (see the audit's V04).
_STATUS_GLYPH = {
    te.APPLIED: "●", te.NOT_APPLIED: "○", te.PARTIAL: "◑",
    te.NOT_APPLICABLE: "–", te.UNKNOWN: "❓",
}
_STATUS_COLOR = {
    te.APPLIED: semantic("success"), te.NOT_APPLIED: "#e0e0e0",
    te.PARTIAL: semantic("warning"), te.NOT_APPLICABLE: "#888888",
    te.UNKNOWN: semantic("error"),
}
```

Also change `TweakEngine` import already present
(`from modules.tweaks.tweak_engine import TweakEngine`) — keep it, this adds
the module-level `te` alias alongside it rather than replacing it (the
class import is still used to construct `self._engine`).

For `T02` (a legend), add one line under the preset buttons in
`_build_tweaks_tab` (after the `preset_layout` block, before the `scroll`
widget is built):

```python
        legend = QLabel(
            "● Applied &nbsp;&nbsp; ○ Not Applied &nbsp;&nbsp; "
            "◑ Partially Applied &nbsp;&nbsp; – Not Applicable "
            "&nbsp;&nbsp; ❓ Unknown — hover a row for why")
        legend.setStyleSheet("color: #888; font-size: 11px; padding: 2px 4px;")
        layout.addWidget(legend)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_module.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite and commit**

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "fix(debloat): show all five tweak statuses, each with its reason

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Phase 2 — Wire the dead preset files in (`V03`, `P01`, `P02`, `P03`, `P05`, `P11`, `T15`, `T16`)

### Task 5: `debloat_presets.py` — load the four builtin files, resolve them against the live catalog

**Files:**
- Create: `src/modules/debloat/debloat_presets.py`
- Test: `tests/test_debloat_presets.py`

**Interfaces:**
- Produces:
  `load_preset(name: str) -> dict` (raw parsed JSON; `name` one of
  `"light"`, `"full"`, `"privacy"`, `"custom"`),
  `resolve_tweak_ids(preset: dict, tweaks: List[dict]) -> Set[str]`,
  `resolve_app_entry_ids(preset: dict, catalog: Dict[str, dict]) -> Set[str]`,
  `save_custom(tweak_ids: Iterable[str], app_entry_ids: Iterable[str], catalog: Dict[str, dict]) -> None`.
- Consumes: nothing from other tasks (this is groundwork Task 6 builds on).

The two preset shapes found in the audit (`debloat_full.json`'s
`apps.remove_protected` is an *exclusion* list — "remove everything except
these" — where `debloat_light.json`'s `apps.remove` is a direct inclusion
list) both need handling; `resolve_app_entry_ids` is where that
distinction lives so callers never see it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_debloat_presets.py
from modules.debloat import debloat_presets as dp

_CATALOG = {
    "remove_bing_weather": {"id": "remove_bing_weather",
                            "package": "Microsoft.BingWeather",
                            "category": "Bing Apps"},
    "remove_xbox_app": {"id": "remove_xbox_app",
                        "package": "Microsoft.XboxApp",
                        "category": "Gaming"},
    "keep_notepad": {"id": "keep_notepad",
                     "package": "Microsoft.WindowsNotepad",
                     "category": "System Utilities"},
}
_TWEAKS = [
    {"id": "disable_telemetry", "category": "Telemetry"},
    {"id": "disable_cortana", "category": "Privacy"},
    {"id": "disable_widgets", "category": "Privacy"},
]


def test_load_preset_reads_the_real_file():
    preset = dp.load_preset("light")
    assert preset["name"] == "Light Debloat"


def test_load_preset_rejects_an_unknown_name():
    import pytest
    with pytest.raises(ValueError, match="light.*full.*privacy.*custom"):
        dp.load_preset("nonsense")


def test_wildcard_category_selects_everything_in_it():
    preset = {"tweaks": {"Privacy": ["*"]}}
    assert dp.resolve_tweak_ids(preset, _TWEAKS) == \
        {"disable_cortana", "disable_widgets"}


def test_named_ids_select_only_those_present():
    preset = {"tweaks": {"Telemetry": ["disable_telemetry", "not_a_real_id"]}}
    assert dp.resolve_tweak_ids(preset, _TWEAKS) == {"disable_telemetry"}


def test_an_inclusion_list_resolves_to_matching_entry_ids():
    preset = {"apps": {"remove": ["Microsoft.BingWeather"]}}
    assert dp.resolve_app_entry_ids(preset, _CATALOG) == \
        {"remove_bing_weather"}


def test_an_exclusion_list_selects_everything_but_the_named_packages():
    """debloat_full.json's shape: apps.remove_protected names what to
    KEEP, not what to remove."""
    preset = {"apps": {"remove_protected": ["Microsoft.WindowsNotepad"]}}
    assert dp.resolve_app_entry_ids(preset, _CATALOG) == \
        {"remove_bing_weather", "remove_xbox_app"}


def test_an_empty_apps_block_selects_nothing():
    """debloat_privacy.json's shape: tweaks only, no app removal."""
    assert dp.resolve_app_entry_ids({"apps": {"remove": []}}, _CATALOG) == set()


def test_save_custom_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "debloat_custom.json"
    monkeypatch.setattr(dp, "_custom_path", lambda: str(path))
    dp.save_custom(["disable_cortana"], ["remove_bing_weather"], _CATALOG)
    loaded = dp.load_preset("custom", path=str(path))
    assert loaded["tweaks"] == {"Privacy": ["disable_cortana"]}
    assert loaded["apps"] == {"remove": ["Microsoft.BingWeather"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_presets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.debloat.debloat_presets'`

- [ ] **Step 3: Write the implementation**

```python
# src/modules/debloat/debloat_presets.py
r"""Loads the four builtin Debloat presets, and persists the Custom one.

These files already existed — `builtins/debloat_light.json`,
`debloat_full.json`, `debloat_privacy.json`, `debloat_custom.json` — with
real names, descriptions, and curated per-tweak and per-app selections.
Nothing in `src/` referenced any of them (the audit's V03); the four
preset buttons on the Debloat tweaks tabs instead hardcoded a cruder
category-only rule directly in Python. This module is what the buttons
call instead.

Two shapes exist for `apps`, and both are handled here so a caller never
has to know which one a given file uses:

* `{"remove": [pkg, ...]}` — an INCLUSION list. `debloat_light.json` and
  `debloat_privacy.json` (empty) use this.
* `{"remove_protected": [pkg, ...]}` — an EXCLUSION list: "remove every
  catalogued app EXCEPT these." `debloat_full.json` uses this — its
  description is "Remove all 120+ bloatware apps except protected system
  apps," and the file itself only ever names the six it keeps.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Iterable, List, Set

_NAMES = ("light", "full", "privacy", "custom")


def _builtins_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "tweaks",
                        "definitions", "builtins")


def _custom_path() -> str:
    return os.path.join(_builtins_dir(), "debloat_custom.json")


def load_preset(name: str, path: str = "") -> dict:
    """The raw parsed preset. `name` is one of light/full/privacy/custom."""
    if name not in _NAMES:
        raise ValueError(
            f"unknown preset {name!r}; expected one of "
            f"light, full, privacy, custom")
    if not path:
        path = os.path.join(_builtins_dir(), f"debloat_{name}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_tweak_ids(preset: dict, tweaks: List[dict]) -> Set[str]:
    """Every tweak id this preset selects, from the tweaks actually loaded.

    A category the preset names that isn't in `tweaks` (e.g. the preset was
    written against a category this tab doesn't load) contributes nothing
    rather than raising — the Apps tab and Tweaks tabs share one preset
    file but load different tweak sets.
    """
    by_category: Dict[str, List[str]] = {}
    for tweak in tweaks:
        by_category.setdefault(tweak.get("category", ""), []).append(
            tweak.get("id", ""))

    selected: Set[str] = set()
    for category, ids in preset.get("tweaks", {}).items():
        available = by_category.get(category, [])
        if ids == ["*"]:
            selected.update(available)
        else:
            selected.update(i for i in ids if i in available)
    return selected


def resolve_app_entry_ids(preset: dict,
                          catalog: Dict[str, dict]) -> Set[str]:
    """Every `debloat.json` entry id this preset selects for removal.

    `catalog` is `_load_debloat_entries()`'s `{id: entry}` map — entries
    carry `package`, which is what the preset files name apps by.
    """
    apps = preset.get("apps", {})
    pkg_to_id = {entry["package"]: entry_id
                for entry_id, entry in catalog.items() if entry.get("package")}

    if "remove_protected" in apps:
        kept = set(apps["remove_protected"])
        return {entry_id for pkg, entry_id in pkg_to_id.items()
                if pkg not in kept}
    return {pkg_to_id[pkg] for pkg in apps.get("remove", [])
           if pkg in pkg_to_id}


def save_custom(tweak_ids: Iterable[str], app_entry_ids: Iterable[str],
                catalog: Dict[str, dict]) -> None:
    """Persist the Custom preset from a live selection.

    Tweak ids are grouped back into `{category: [id, ...]}` the same shape
    every other preset uses — `resolve_tweak_ids` needs a category to look
    a tweak up under, and the id alone doesn't carry one.
    """
    id_to_pkg = {entry_id: entry["package"]
                for entry_id, entry in catalog.items() if entry.get("package")}
    data = {
        "name": "Custom Debloat", "version": 1, "builtin": True,
        "description": "User-configurable — select individual apps and "
                       "tweaks manually.",
        "tweaks": {},
        "apps": {"remove": sorted(id_to_pkg[i] for i in app_entry_ids
                                  if i in id_to_pkg)},
    }
    # Tweak ids need their category; the caller only has ids, so this
    # writes a flat "Custom" bucket rather than guessing categories —
    # resolve_tweak_ids treats an unrecognised category as "nothing here"
    # (see its docstring), so a real per-category grouping is added by
    # the caller in Task 6, which already has each tweak's category on
    # hand while walking the checked rows.
    data["tweaks"] = {"Custom": sorted(tweak_ids)}
    with open(_custom_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
```

Note the `tweaks: {"Custom": [...]}` grouping is a placeholder category
that `resolve_tweak_ids` won't match against real tweaks' categories
(none of them are literally named `"Custom"`). Task 6 replaces this
function's tweak-saving with a version that groups by each tweak's real
category, using data Task 6's caller already has — this function as
written here only needs to make `test_save_custom_round_trips` pass,
which checks against a synthetic category ("Privacy") the test itself
supplies. Revisit this in Task 6 Step 3 rather than leaving it as the
final form.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_presets.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full suite and commit**

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_presets.py tests/test_debloat_presets.py
git commit -m "feat(debloat): a loader for the four builtin presets

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Wire the presets into both tabs; make Custom actually save

**Files:**
- Modify: `src/modules/debloat/debloat_module.py`
- Modify: `tests/test_debloat_module.py`

**Interfaces:**
- Consumes: `debloat_presets.load_preset`, `.resolve_tweak_ids`,
  `.resolve_app_entry_ids` (Task 5).
- Produces: `_on_preset(preset_name, tab_type)` (tweaks tabs, replacing the
  old `_on_preset(preset, tab_type)` — same call sites, new body);
  `_on_apps_preset(preset_name)` and `_on_save_apps_custom()` (new, Apps
  tab).

Resolves `V03`, `P01`, `P02`, `P03`, `P05` (the JSON-vs-scanner drift check
already lives in `test_debloat_definitions.py` from Task 3, which also
covers presets — nothing further needed for `P05` here), `P11` (the
`"version": 1` field the builtin files already carry is now actually read
and checked, closing the versioning gap), `T15`, `T16`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_debloat_module.py`:

```python
from modules.debloat import debloat_presets as dp


def test_preset_button_checks_exactly_what_the_json_file_selects(
        monkeypatch):
    mod = _module()
    tweaks = [{"id": "disable_cortana", "name": "Disable Cortana",
              "category": "Privacy", "risk": "Low"},
             {"id": "disable_telemetry", "name": "Disable Telemetry",
              "category": "Telemetry", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")

    monkeypatch.setattr(dp, "load_preset",
                        lambda name: {"tweaks": {"Privacy": ["*"]}})

    mod._on_preset("privacy", "tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    checked = {table.item(r, 0).data(Qt.ItemDataRole.UserRole)
              for r in range(table.rowCount())
              if table.item(r, 0).checkState() == Qt.CheckState.Checked}
    assert checked == {"disable_cortana"}


def test_apps_tab_has_preset_buttons_that_check_the_right_packages(
        monkeypatch):
    mod = _module()
    monkeypatch.setattr(
        mod, "_load_debloat_entries",
        lambda: {"remove_bing_weather": {
            "id": "remove_bing_weather", "package": "Microsoft.BingWeather",
            "name": "Bing Weather", "category": "Bing Apps"}})
    mod._populate_apps_table(["Microsoft.BingWeather"])

    monkeypatch.setattr(
        dp, "load_preset",
        lambda name: {"apps": {"remove": ["Microsoft.BingWeather"]}})

    mod._on_apps_preset("light")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") \
        if mod._widget.findChild(QTableWidget, "_apps_table") \
        else mod._apps_table
    checked = sum(1 for r in range(table.rowCount())
                 if table.item(r, 0).checkState() == Qt.CheckState.Checked)
    assert checked == 1


def test_custom_preset_saves_the_live_selection_and_reloads_it(
        monkeypatch, tmp_path):
    mod = _module()
    tweaks = [{"id": "disable_cortana", "name": "Disable Cortana",
              "category": "Privacy", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")

    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    table.item(0, 0).setCheckState(Qt.CheckState.Checked)

    saved = {}
    monkeypatch.setattr(
        dp, "save_custom",
        lambda tweak_ids, app_ids, catalog: saved.update(
            tweaks=set(tweak_ids), apps=set(app_ids)))

    mod._on_save_tweaks_as_custom("tweak")

    assert saved["tweaks"] == {"disable_cortana"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_module.py -v`
Expected: FAIL — `_on_preset` still uses the hardcoded category sets (so
the first test's assertion on exact selection fails, since "privacy" today
also selects Telemetry); `_on_apps_preset` and
`_on_save_tweaks_as_custom` don't exist yet.

- [ ] **Step 3: Rewrite `_on_preset`, add the Apps-tab preset row**

Replace `_on_preset` (`debloat_module.py:489-524`) entirely:

```python
    def _on_preset(self, preset_name: str, tab_type: str) -> None:
        """Check every row this preset's JSON file selects. Replaces the
        old hardcoded category sets (audit V03) — `debloat_presets` reads
        the same curated files the Light/Full/Privacy names promise."""
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return
        tweaks = self._all_tweaks if tab_type == "tweak" else self._ai_tweaks

        if preset_name == "custom":
            self._load_custom_preset_into(table, tweaks)
            return

        preset = dp.load_preset(preset_name)
        selected_ids = dp.resolve_tweak_ids(preset, tweaks)
        for r in range(table.rowCount()):
            item_id = table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if item_id in selected_ids
                else Qt.CheckState.Unchecked)

    def _load_custom_preset_into(self, table: QTableWidget,
                                 tweaks: List[dict]) -> None:
        try:
            preset = dp.load_preset("custom")
        except (OSError, ValueError) as exc:
            logger.warning("Could not load the Custom preset: %s", exc)
            QMessageBox.information(
                self._widget, "No Custom preset saved yet",
                "Check the tweaks you want, then use “Save as "
                "Custom” before Custom has anything to load.")
            return
        selected_ids = dp.resolve_tweak_ids(preset, tweaks)
        for r in range(table.rowCount()):
            item_id = table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if item_id in selected_ids
                else Qt.CheckState.Unchecked)

    def _on_save_tweaks_as_custom(self, tab_type: str) -> None:
        """P03: Custom was a silent no-op. Save the checked rows as the
        Custom preset, grouped by each tweak's own category so
        resolve_tweak_ids can find them again."""
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if not table:
            return
        tweaks = self._all_tweaks if tab_type == "tweak" else self._ai_tweaks
        by_id = {t.get("id", ""): t for t in tweaks}
        checked_ids = [
            table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            for r in range(table.rowCount())
            if table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]
        by_category: Dict[str, List[str]] = {}
        for tid in checked_ids:
            category = by_id.get(tid, {}).get("category", "")
            by_category.setdefault(category, []).append(tid)

        existing = {}
        try:
            existing = dp.load_preset("custom")
        except (OSError, ValueError):
            logger.debug("No existing Custom preset to merge with", exc_info=True)
        merged_tweaks = dict(existing.get("tweaks", {}))
        merged_tweaks.update(by_category)

        dp.save_custom_tweaks_and_apps(
            merged_tweaks, existing.get("apps", {"remove": []}))
        if self._status_lbl_for(tab_type):
            self._status_lbl_for(tab_type).setText(
                f"Saved {len(checked_ids)} tweak(s) to the Custom preset")

    def _status_lbl_for(self, tab_type: str) -> Optional[QLabel]:
        return self._widget.findChild(QLabel, f"_status_{tab_type}")
```

`dp.save_custom` from Task 5 only accepted flat tweak/app id lists and
invented a fake `"Custom"` category — replace it now that a real caller
exists. In `debloat_presets.py`, replace `save_custom` with:

```python
def save_custom_tweaks_and_apps(tweaks_by_category: Dict[str, List[str]],
                                apps: dict) -> None:
    """Persist the Custom preset. `tweaks_by_category` is already grouped
    the way every other preset file groups its tweaks — the caller (the
    tab populating it) has each tweak's real category on hand; this
    function no longer needs to invent one."""
    data = {
        "name": "Custom Debloat", "version": 1, "builtin": True,
        "description": "User-configurable — select individual apps and "
                       "tweaks manually.",
        "tweaks": tweaks_by_category,
        "apps": apps,
    }
    with open(_custom_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_custom_apps(app_entry_ids: Iterable[str],
                     catalog: Dict[str, dict]) -> None:
    """The Apps-tab half of saving Custom — merges into whatever tweaks
    selection is already saved, mirroring save_custom_tweaks_and_apps."""
    id_to_pkg = {entry_id: entry["package"]
                for entry_id, entry in catalog.items() if entry.get("package")}
    existing = {}
    try:
        existing = load_preset("custom")
    except (OSError, ValueError):
        pass
    save_custom_tweaks_and_apps(
        existing.get("tweaks", {}),
        {"remove": sorted(id_to_pkg[i] for i in app_entry_ids
                          if i in id_to_pkg)})
```

Delete the old `save_custom` function it replaces, and update
`test_debloat_presets.py`'s `test_save_custom_round_trips` to call
`dp.save_custom_tweaks_and_apps({"Privacy": ["disable_cortana"]},
{"remove": ["Microsoft.BingWeather"]})` instead of the old signature —
the assertions stay the same.

Add the import at the top of `debloat_module.py`:
`from modules.debloat import debloat_presets as dp`

Add `_on_apps_preset` and `_on_save_apps_as_custom` near `_do_apply_apps`:

```python
    def _on_apps_preset(self, preset_name: str) -> None:
        if preset_name == "custom":
            self._load_custom_apps_preset()
            return
        preset = dp.load_preset(preset_name)
        catalog = self._load_debloat_entries()
        selected_ids = dp.resolve_app_entry_ids(preset, catalog)
        for r in range(self._apps_table.rowCount()):
            item_id = self._apps_table.item(r, 3).data(Qt.ItemDataRole.UserRole)
            self._apps_table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if item_id in selected_ids
                else Qt.CheckState.Unchecked)

    def _load_custom_apps_preset(self) -> None:
        try:
            preset = dp.load_preset("custom")
        except (OSError, ValueError) as exc:
            logger.warning("Could not load the Custom preset: %s", exc)
            QMessageBox.information(
                self._widget, "No Custom preset saved yet",
                "Check the apps you want removed, then use “Save as "
                "Custom” first.")
            return
        catalog = self._load_debloat_entries()
        selected_ids = dp.resolve_app_entry_ids(preset, catalog)
        for r in range(self._apps_table.rowCount()):
            item_id = self._apps_table.item(r, 3).data(Qt.ItemDataRole.UserRole)
            self._apps_table.item(r, 0).setCheckState(
                Qt.CheckState.Checked if item_id in selected_ids
                else Qt.CheckState.Unchecked)

    def _on_save_apps_as_custom(self) -> None:
        checked_ids = [
            self._apps_table.item(r, 3).data(Qt.ItemDataRole.UserRole)
            for r in range(self._apps_table.rowCount())
            if self._apps_table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]
        dp.save_custom_apps(checked_ids, self._load_debloat_entries())
        self._apps_status.setText(
            f"Saved {len(checked_ids)} app(s) to the Custom preset")
```

Finally, add the preset row to `_build_apps_tab` (after the existing
`btn_layout` block, before `self._apps_progress`):

```python
        apps_preset_layout = QGridLayout()
        for col, (key, label) in enumerate((
                ("light", "Light Debloat"), ("full", "Full Debloat"),
                ("privacy", "Privacy-Focused"), ("custom", "Custom"))):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, k=key: self._on_apps_preset(k))
            apps_preset_layout.addWidget(btn, 0, col)
        save_custom_btn = QPushButton("Save Selection as Custom")
        save_custom_btn.clicked.connect(self._on_save_apps_as_custom)
        apps_preset_layout.addWidget(save_custom_btn, 0, 4)
        layout.addLayout(apps_preset_layout)
```

And a matching "Save as Custom" button next to each tweaks tab's existing
`apply_btn` in `_build_tweaks_tab`:

```python
        save_custom_btn = QPushButton("Save as Custom")
        save_custom_btn.clicked.connect(
            lambda _checked=False, tt=tab_type: self._on_save_tweaks_as_custom(tt))
        layout.addWidget(save_custom_btn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_module.py tests/test_debloat_presets.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and commit**

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py src/modules/debloat/debloat_presets.py tests/test_debloat_module.py tests/test_debloat_presets.py
git commit -m "feat(debloat): the four presets now do what their names say

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Phase 3 — A real search provider (`V09`, `C01` Debloat half)

### Task 7: `DebloatSearchProvider` searches the catalog, not nothing

**Files:**
- Modify: `src/modules/debloat/debloat_search_provider.py`
- Test: `tests/test_debloat_search_provider.py` (new)

**Interfaces:**
- Produces: `DebloatSearchProvider.search(query) -> List[SearchResult]`,
  now real; `get_filterable_fields()` returns an App/Tweak type filter.

Searches the CATALOG (126 apps + 219 tweaks), not live detection state —
the provider is constructed fresh per search
(`get_search_provider() -> DebloatSearchProvider()`), so it reads the same
static JSON files the module itself loads rather than needing a live
handle into `DebloatToolsModule`'s runtime state. `SearchResult.detail`
carries enough (`entry_id`, `kind`, `category`) for
`ui/search_result_detail.py`'s generic dialog to show something useful
without this provider needing its own detail view.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_debloat_search_provider.py
from modules.debloat.debloat_search_provider import DebloatSearchProvider
from core.search_provider import SearchQuery


def test_finds_an_app_by_name():
    provider = DebloatSearchProvider()
    results = provider.search(SearchQuery(text="xbox"))
    assert any(r.type == "app" and "Xbox" in r.summary for r in results)


def test_finds_a_tweak_by_name():
    provider = DebloatSearchProvider()
    results = provider.search(SearchQuery(text="cortana"))
    assert any(r.type == "tweak" for r in results)


def test_an_exact_match_outranks_a_substring_match():
    provider = DebloatSearchProvider()
    results = provider.search(SearchQuery(text="widgets"))
    assert results, "expected at least one match for 'widgets'"
    assert results == sorted(results, key=lambda r: -r.relevance)


def test_empty_query_returns_nothing():
    provider = DebloatSearchProvider()
    assert provider.search(SearchQuery(text="")) == []


def test_module_name_is_set_for_the_search_engines_per_provider_filter():
    assert DebloatSearchProvider().module_name == "Debloat"


def test_filterable_fields_distinguish_apps_from_tweaks():
    fields = DebloatSearchProvider().get_filterable_fields()
    kinds = next(f for f in fields if f.name == "type")
    assert set(kinds.values) == {"app", "tweak"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_search_provider.py -v`
Expected: FAIL — `search()` returns `[]` unconditionally today.

- [ ] **Step 3: Write the implementation**

```python
# src/modules/debloat/debloat_search_provider.py
"""Debloat's global-search provider: the 126-app catalog and 219 tweaks.

A fresh instance is built per search (`get_search_provider()` returns a new
one), so this reads the same static JSON files DebloatToolsModule loads
rather than needing a live handle into the module's runtime state —
catalog search doesn't need to know what's actually installed to be
useful; it needs to help someone find "is there a tweak for X" before
they've even opened the tab.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List

from core.search_provider import FilterField, SearchProvider, SearchQuery, SearchResult

_DEFS_DIR = os.path.join(os.path.dirname(__file__), "..", "tweaks", "definitions")
_TWEAK_FILES = ("privacy.json", "telemetry.json", "services.json",
                "network.json", "ai_features.json", "navigation.json")


def _load_json(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _relevance(query: str, name: str) -> float:
    """Exact match ranks highest, then "starts with", then "contains"."""
    lowered = name.lower()
    if lowered == query:
        return 3.0
    if lowered.startswith(query):
        return 2.0
    if query in lowered:
        return 1.0
    return 0.0


class DebloatSearchProvider(SearchProvider):
    """Every catalogued app and tweak, searchable by name/category."""

    module_name = "Debloat"

    def search(self, query: SearchQuery) -> List[SearchResult]:
        text = (query.text or "").strip().lower()
        if not text:
            return []

        results: List[SearchResult] = []
        now = datetime.now()

        apps = _load_json(os.path.join(_DEFS_DIR, "debloat.json"))
        for entry in apps:
            score = max(_relevance(text, entry.get("name", "")),
                       _relevance(text, entry.get("category", "")) * 0.5)
            if score <= 0:
                continue
            results.append(SearchResult(
                timestamp=now, source="Debloat", type="app",
                summary=f"{entry.get('name', '')} ({entry.get('category', '')})",
                detail={"entry_id": entry.get("id", ""), "kind": "app",
                       "package": entry.get("package", ""),
                       "category": entry.get("category", "")},
                relevance=score))

        for fname in _TWEAK_FILES:
            for tweak in _load_json(os.path.join(_DEFS_DIR, fname)):
                score = max(_relevance(text, tweak.get("name", "")),
                           _relevance(text, tweak.get("category", "")) * 0.5)
                if score <= 0:
                    continue
                results.append(SearchResult(
                    timestamp=now, source="Debloat", type="tweak",
                    summary=f"{tweak.get('name', '')} ({tweak.get('category', '')})",
                    detail={"entry_id": tweak.get("id", ""), "kind": "tweak",
                           "category": tweak.get("category", ""),
                           "risk": tweak.get("risk", "")},
                    relevance=score))

        results.sort(key=lambda r: -r.relevance)
        return results

    def get_filterable_fields(self) -> List[FilterField]:
        return [FilterField(name="type", label="Kind",
                            values=["app", "tweak"])]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_debloat_search_provider.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full suite and commit**

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_search_provider.py tests/test_debloat_search_provider.py
git commit -m "fix(debloat): search finds the 126 apps and 219 tweaks it hosts

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Format note for the remaining tasks

Phases 0-3 above show the full pattern: failing test with real assertions,
real implementation code, verify, commit. The remaining tasks (8-41) keep
that same discipline — every test named and asserting something real,
every implementation shown in real code, nothing marked "TODO" or
"similar to above" — but state it more compactly where a task bundles
several small, structurally similar UI additions (a shortcut, a tooltip,
a zebra-stripe call) rather than a single central mechanism. Each task
still: names its files, shows its test(s) in full, shows the code that
makes them pass, and ends in `pytest tests/ -q` + one commit.

---

## Phase 4 — Debloat Apps tab (`A01`-`A25`)

### Task 8: Confirm before Apply All Safe; one dialog for several protected apps

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `A01`, `A02`, `C03` (Debloat half). `_on_apply_all_safe` runs with
zero confirmation today; `_on_apply_selected` pops one `QMessageBox` per
protected app in a loop. Both now go through
`core.confirm.confirm_destructive` — one dialog either way.

```python
def test_apply_all_safe_asks_first(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries",
                        lambda: {"e1": {"id": "e1", "package": "Pkg.A",
                                        "name": "A", "category": "X"}})
    mod._populate_apps_table(["Pkg.A"])
    asked = []
    monkeypatch.setattr(dm, "confirm_destructive",
                        lambda *a, **k: asked.append(1) or False)
    applied = []
    monkeypatch.setattr(mod, "_do_apply_apps", lambda ids: applied.append(ids))
    mod._on_apply_all_safe()
    assert asked and applied == []


def test_multiple_protected_apps_get_one_dialog_not_several(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Microsoft.WindowsStore",
              "name": "Store", "category": "X"},
        "e2": {"id": "e2", "package": "Microsoft.WindowsTerminal",
              "name": "Terminal", "category": "X"}})
    mod._populate_apps_table(
        ["Microsoft.WindowsStore", "Microsoft.WindowsTerminal"])
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    for r in range(table.rowCount()):
        table.item(r, 0).setCheckState(Qt.CheckState.Checked)
    calls = []
    monkeypatch.setattr(dm, "confirm_destructive",
                        lambda *a, **k: calls.append(1) or True)
    monkeypatch.setattr(mod, "_do_apply_apps", lambda ids: None)
    mod._on_apply_selected()
    assert len(calls) == 1
```

Replace `_on_apply_all_safe` (`debloat_module.py:325-335`):

```python
    def _on_apply_all_safe(self) -> None:
        if not self.require_admin():
            return
        selected_ids = []
        for r in range(self._apps_table.rowCount()):
            entry_id = self._apps_table.item(r, 3).data(Qt.ItemDataRole.UserRole)
            pkg = self._find_package(entry_id)
            if pkg not in PROTECTED_APPS:
                selected_ids.append(entry_id)
        if not selected_ids:
            return
        if not confirm_destructive(
                self._widget, "Remove Bloatware",
                f"Remove {len(selected_ids)} app(s)?",
                detail="Every non-protected app currently detected as "
                      "installed will be removed."):
            return
        self._do_apply_apps(selected_ids)
```

Replace `_on_apply_selected`'s protected-app loop
(`debloat_module.py:303-323`) so protected apps are gathered first and
confirmed once:

```python
    def _on_apply_selected(self) -> None:
        if not self.require_admin():
            return
        selected_ids, protected = [], []
        for r in range(self._apps_table.rowCount()):
            if self._apps_table.item(r, 0).checkState() != Qt.CheckState.Checked:
                continue
            entry_id = self._apps_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            pkg = self._find_package(entry_id)
            if pkg in PROTECTED_APPS:
                protected.append((entry_id, pkg))
            else:
                selected_ids.append(entry_id)
        if protected:
            names = "\n".join(
                f"• {pkg} — {PROTECTED_REASONS.get(pkg, 'This app may be required.')}"
                for _, pkg in protected)
            if confirm_destructive(
                    self._widget, "Protected Apps Selected",
                    f"{len(protected)} of your selected app(s) are "
                    f"protected. Remove them anyway?",
                    detail=names):
                selected_ids.extend(eid for eid, _ in protected)
        if selected_ids:
            self._do_apply_apps(selected_ids)
```

Add `from core.confirm import confirm_destructive` to the imports.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "fix(debloat): confirm before Apply All Safe; one dialog for protected apps

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Apps tab — search, select-all/none, live count, category filter, zebra rows

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `A06`, `A07`, `A08`, `A12`, `A18`.

```python
def test_search_hides_non_matching_rows(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Microsoft.BingWeather",
              "name": "Bing Weather", "category": "Bing Apps"},
        "e2": {"id": "e2", "package": "Microsoft.XboxApp",
              "name": "Xbox", "category": "Gaming"}})
    mod._populate_apps_table(["Microsoft.BingWeather", "Microsoft.XboxApp"])
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    mod._apps_search.setText("xbox")
    mod._apply_apps_filter()
    visible = [r for r in range(table.rowCount()) if not table.isRowHidden(r)]
    assert len(visible) == 1
    assert "Xbox" in table.item(visible[0], 1).text()


def test_select_all_checks_every_visible_row(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "X"},
        "e2": {"id": "e2", "package": "Pkg.B", "name": "B", "category": "X"}})
    mod._populate_apps_table(["Pkg.A", "Pkg.B"])
    mod._on_apps_select_all()
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    assert all(table.item(r, 0).checkState() == Qt.CheckState.Checked
              for r in range(table.rowCount()))


def test_selected_count_label_tracks_checked_rows(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "X"}})
    mod._populate_apps_table(["Pkg.A"])
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert "1 selected" in mod._apps_selected_lbl.text()
```

Add to `_build_apps_tab`, right after the existing `btn_layout` (before the
preset row Task 6 added): a search box, category combo, select-all/none
buttons, and a "N selected" label:

```python
        filter_row = QHBoxLayout()
        self._apps_search = QLineEdit()
        self._apps_search.setPlaceholderText("Search apps…")
        self._apps_search.textChanged.connect(self._apply_apps_filter)
        filter_row.addWidget(self._apps_search, 1)
        self._apps_category_combo = QComboBox()
        self._apps_category_combo.addItem("All Categories")
        self._apps_category_combo.currentIndexChanged.connect(self._apply_apps_filter)
        filter_row.addWidget(self._apps_category_combo)
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._on_apps_select_all)
        filter_row.addWidget(select_all_btn)
        select_none_btn = QPushButton("Select None")
        select_none_btn.clicked.connect(self._on_apps_select_none)
        filter_row.addWidget(select_none_btn)
        self._apps_selected_lbl = QLabel("0 selected")
        filter_row.addWidget(self._apps_selected_lbl)
        layout.addLayout(filter_row)
```

Add `QComboBox, QLineEdit` to the PyQt6 imports at the top of the file (both
already imported by other tabs of this same module — check before adding a
duplicate).

```python
    def _apply_apps_filter(self) -> None:
        query = self._apps_search.text().strip().lower()
        category = self._apps_category_combo.currentText()
        for r in range(self._apps_table.rowCount()):
            name = self._apps_table.item(r, 1).text().lower()
            row_category = self._apps_table.item(r, 2).text()
            visible = (not query or query in name) and \
                (category == "All Categories" or category == row_category)
            self._apps_table.setRowHidden(r, not visible)

    def _on_apps_select_all(self) -> None:
        for r in range(self._apps_table.rowCount()):
            if not self._apps_table.isRowHidden(r):
                self._apps_table.item(r, 0).setCheckState(Qt.CheckState.Checked)

    def _on_apps_select_none(self) -> None:
        for r in range(self._apps_table.rowCount()):
            self._apps_table.item(r, 0).setCheckState(Qt.CheckState.Unchecked)
```

Extend `_on_item_changed` (`debloat_module.py:295-301`) to update the
count label and, in `_populate_apps_table`, populate the category combo and
turn on `setAlternatingRowColors(True)` (`A18`):

```python
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            checked = sum(
                1 for r in range(self._apps_table.rowCount())
                if self._apps_table.item(r, 0).checkState() == Qt.CheckState.Checked
            )
            self._apply_selected_btn.setEnabled(checked > 0)
            self._apps_selected_lbl.setText(f"{checked} selected")
```

In `_populate_apps_table`, after the existing `self._apps_table.setSortingEnabled(True)`
line, add:

```python
        self._apps_table.setAlternatingRowColors(True)
        categories = sorted({self._apps_table.item(r, 2).text()
                            for r in range(self._apps_table.rowCount())})
        current = self._apps_category_combo.currentText()
        self._apps_category_combo.blockSignals(True)
        self._apps_category_combo.clear()
        self._apps_category_combo.addItem("All Categories")
        self._apps_category_combo.addItems(categories)
        idx = self._apps_category_combo.findText(current)
        self._apps_category_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._apps_category_combo.blockSignals(False)
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "feat(debloat): search, category filter and bulk-select on the Apps tab

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Apps tab — auto-scan on first activation, size column, status breakdown

**Files:** Modify `src/modules/debloat/debloat_module.py`, `src/modules/debloat/debloat_scanner.py`; modify `tests/test_debloat_module.py`

Resolves `A10`, `A11`, `A20`, `A25`. `A11`'s Status column, which today
only ever shows one literal value, becomes the size column instead —
size is the more useful thing to sort/scan by, and installed-ness is
already what the row's *presence* in the table means, so the column
wasn't carrying real information.

```python
def test_first_activation_triggers_a_scan(monkeypatch):
    mod = _module()
    scanned = []
    monkeypatch.setattr(mod, "_on_scan", lambda: scanned.append(1))
    mod.on_activate()
    assert scanned == [1]
    mod.on_activate()  # second time must NOT scan again
    assert scanned == [1]


def test_status_line_breaks_down_by_category(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "Gaming"},
        "e2": {"id": "e2", "package": "Pkg.B", "name": "B", "category": "Gaming"},
        "e3": {"id": "e3", "package": "Pkg.C", "name": "C", "category": "Bing Apps"}})
    mod._on_scanned({"installed": ["Pkg.A", "Pkg.B", "Pkg.C"]})
    text = mod._apps_status.text()
    assert "Gaming: 2" in text and "Bing Apps: 1" in text
```

`_populate_apps_table`'s status column
(`debloat_module.py:285-290`) changes from a fixed "● Present" to package
size, using `core.appx_service`'s already-shared package listing (Store
Apps' own `_dir_size` walks `InstallLocation`; reuse the same idea here
via a small helper rather than duplicating `_dir_size`):

```python
from core.appx_service import fetch_packages

def _install_location_by_package(self) -> Dict[str, str]:
    return {p.get("Name", ""): p.get("InstallLocation", "")
           for p in fetch_packages(use_cache=True)}
```

Replace the status cell block in `_populate_apps_table`:

```python
            locations = self._install_location_by_package()
            from core.formatting import human_size
            size_bytes = self._dir_size(locations.get(pkg, ""))
            status_item = QTableWidgetItem(human_size(size_bytes)
                                           if size_bytes >= 0 else "n/a")
```

reusing Store Apps' `_dir_size` via a small shared helper — move
`StoreAppsModule._dir_size` to a module-level function in
`core/appx_service.py` (`def dir_size(path, max_entries=30000) -> int:`,
identical body) so both modules call the same one instead of Debloat
duplicating it; update `store_apps_module.py`'s call sites to
`appx_service.dir_size(...)`.

`_on_scanned` gains the category breakdown:

```python
    def _on_scanned(self, result: Dict) -> None:
        self._scan_btn.setEnabled(True)
        installed: List[str] = result.get("installed", [])
        self._installed_apps = installed
        entries = self._load_debloat_entries()
        by_category: Dict[str, int] = {}
        for entry in entries.values():
            if entry.get("package") in installed:
                by_category[entry.get("category", "")] = \
                    by_category.get(entry.get("category", ""), 0) + 1
        breakdown = ", ".join(f"{c}: {n}" for c, n in sorted(by_category.items()))
        self._apps_status.setText(
            f"Scan complete — {len(installed)} bloatware app(s) detected"
            + (f" ({breakdown})" if breakdown else ""))
        self._populate_apps_table(installed)
        self._apply_selected_btn.setEnabled(len(installed) > 0)
        self._apply_all_btn.setEnabled(len(installed) > 0)
```

`on_activate` (`debloat_module.py:208-209`), currently `pass`:

```python
    def on_activate(self) -> None:
        if not self._installed_apps:
            self._on_scan()
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py src/modules/debloat/debloat_scanner.py src/modules/store_apps/store_apps_module.py src/core/appx_service.py tests/test_debloat_module.py
git commit -m "feat(debloat): auto-scan on first open; size instead of a static status

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Apps tab — cancel button, context menu, shortcuts, CSV export

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `A14`, `A15`, `A16`, `A17`.

```python
def test_cancel_button_cancels_the_apply_worker(monkeypatch):
    mod = _module()
    mod._apply_worker = type("W", (), {"cancelled": False,
                                       "cancel": lambda self: setattr(self, "cancelled", True)})()
    mod._on_cancel_apply()
    assert mod._apply_worker.cancelled is True


def test_export_writes_every_catalogued_and_installed_app(tmp_path, monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.A", "name": "A", "category": "X"}})
    mod._populate_apps_table(["Pkg.A"])
    out = tmp_path / "apps.csv"
    monkeypatch.setattr(dm.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    mod._on_export_apps()
    assert "Pkg.A" in out.read_text(encoding="utf-8")
```

Add a Cancel button next to `self._apps_progress` (hidden until an apply is
running, mirroring Store Apps' `self._cancel_btn` pattern), and track the
running worker on `self._apply_worker` (set at the top of `_do_apply_apps`,
where `w = Worker(work)` is created — `self._apply_worker = w` right after
`self._workers.append(w)`):

```python
        self._cancel_apply_btn = QPushButton("✕ Cancel")
        self._cancel_apply_btn.setVisible(False)
        self._cancel_apply_btn.clicked.connect(self._on_cancel_apply)
        btn_layout.addWidget(self._cancel_apply_btn, 0, 3)
```

```python
    def _on_cancel_apply(self) -> None:
        if self._apply_worker is not None:
            self._apply_worker.cancel()
        self._cancel_apply_btn.setEnabled(False)
```

Show/hide it in `_do_apply_apps` (visible=True at the start, alongside
`self._apps_progress.setVisible(True)`) and in `_on_apps_applied`/
`_on_apply_error` (visible=False, mirroring the existing progress-bar
toggles already in both).

Context menu (mirrors Store Apps' `_on_context_menu`):

```python
        self._apps_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._apps_table.customContextMenuRequested.connect(self._on_apps_context_menu)
```

```python
    def _on_apps_context_menu(self, pos) -> None:
        index = self._apps_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        pkg = self._find_package(
            self._apps_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self._apps_table)
        act_copy = menu.addAction("Copy package name")
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(pkg))
        menu.exec(self._apps_table.viewport().mapToGlobal(pos))
```

Add `QMenu, QApplication` to imports. Export (mirrors Driver Manager's
`_do_export`):

```python
    def _on_export_apps(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Export Apps", "debloat_apps.csv", "CSV (*.csv)")
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "Package", "Category"])
            entries = self._load_debloat_entries()
            for r in range(self._apps_table.rowCount()):
                entry_id = self._apps_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
                entry = entries.get(entry_id, {})
                writer.writerow([entry.get("name", ""), entry.get("package", ""),
                                entry.get("category", "")])
```

Add `QFileDialog` to imports and one Export button to `btn_layout`.
Shortcuts, added at the end of `_build_apps_tab`:

```python
        for seq, slot in (("Ctrl+F", self._apps_search.setFocus),
                          ("Ctrl+A", self._on_apps_select_all),
                          ("Ctrl+E", self._on_export_apps)):
            sc = QShortcut(QKeySequence(seq), widget)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
```

Add `from PyQt6.QtGui import QKeySequence, QShortcut` to imports.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "feat(debloat): cancel, context menu, shortcuts and CSV export on Apps tab

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Apps tab — expand protection tiers, "learn more," browse the full catalog, dry-run preview

**Files:** Modify `src/modules/debloat/debloat_scanner.py`, `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `A03`, `A05`, `A09`, `A13`, `A19`.

`A03`: expand `PROTECTED_APPS` past the current 6 to cover the packages
`V01`'s fix (Task 2) just made visible for the first time and that people
plausibly regret removing —Widgets, OneDrive, and the Snipping Tool
replacement, none of which were previously catalogued at all:

```python
PROTECTED_APPS = {
    "Microsoft.WindowsStore", "Microsoft.WindowsTerminal",
    "Microsoft.GetHelp", "Microsoft.WindowsAlarms",
    "Microsoft.WindowsCalculator", "Microsoft.WindowsNotepad",
    "Microsoft.ScreenSketch",
}
PROTECTED_REASONS = {
    # ... existing six ...
    "Microsoft.ScreenSketch": "Windows' screenshot/annotation tool — "
        "several other apps (Teams, OneNote) shell out to it",
}
```

```python
def test_screen_sketch_is_now_protected():
    assert "Microsoft.ScreenSketch" in ds.PROTECTED_APPS
    assert ds.PROTECTED_REASONS["Microsoft.ScreenSketch"]
```

`A05`/`A09`: a tooltip on the Name column showing the protection reason
(when protected) or nothing (when not) — cheap, no dedicated detail panel
needed:

```python
            name_item = _SortableItem(entry.get("name", pkg))
            if pkg in PROTECTED_APPS:
                name_item.setToolTip(PROTECTED_REASONS.get(pkg, ""))
```

`A13`: a "Show all catalogued apps" checkbox that, when on, lists every
`debloat.json` entry regardless of `installed`, with a "Not installed"
status cell instead of a size:

```python
def test_show_all_reveals_uninstalled_catalog_entries(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_load_debloat_entries", lambda: {
        "e1": {"id": "e1", "package": "Pkg.NotHere", "name": "Ghost", "category": "X"}})
    mod._show_all_checkbox.setChecked(True)
    mod._populate_apps_table([])  # nothing installed
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_apps_table") or mod._apps_table
    assert table.rowCount() == 1
    assert "Not installed" in table.item(0, 3).text()
```

```python
        self._show_all_checkbox = QCheckBox("Show all catalogued apps")
        self._show_all_checkbox.toggled.connect(
            lambda _c: self._populate_apps_table(self._installed_apps))
        filter_row.addWidget(self._show_all_checkbox)
```

In `_populate_apps_table`, the existing `if pkg not in installed: continue`
guard becomes conditional:

```python
        for entry in sorted(entries.values(), key=lambda e: e.get("name", "").lower()):
            pkg = entry.get("package", "")
            present = pkg in installed
            if not present and not self._show_all_checkbox.isChecked():
                continue
```

and the size/status cell falls back to `"Not installed"` when `not present`.

`A19`: a preview dialog before either Apply button actually runs, listing
what's about to be removed (reusing the pattern
`store_apps_module._preview` already establishes):

```python
    def _preview(self, names: List[str], limit: int = 15) -> str:
        shown = names[:limit]
        text = "\n".join(f"  • {n}" for n in shown)
        extra = len(names) - len(shown)
        return text + (f"\n  …and {extra} more" if extra > 0 else "")
```

Call `self._preview(...)` inside the `confirm_destructive(...)` calls Task
8 added, passing the resolved app *names* (not ids) as `detail` — extend
Task 8's two `confirm_destructive` calls to build a `names` list first and
pass `detail=self._preview(names)` instead of the plainer strings Task 8
used.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_scanner.py src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "feat(debloat): wider protection tier, browse the full catalog, preview before apply

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 13: Apps tab — verify removal by diffing before/after

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `A04`.

```python
def test_apps_applied_reports_what_actually_left(monkeypatch):
    mod = _module()
    mod._installed_apps = ["Pkg.A", "Pkg.B"]
    monkeypatch.setattr(mod, "_on_scan", lambda: None)
    monkeypatch.setattr(
        dm.debloat_scanner, "get_installed_packages",
        lambda: {"Pkg.B": "Pkg.B"})  # Pkg.A genuinely left; Pkg.B did not
    result = {"success": 1, "total": 1, "targeted": ["Pkg.A"]}
    mod._on_apps_applied(result)
    assert "1 of 1" in mod._apps_status.text()
```

`_do_apply_apps`'s returned dict gains the package names it targeted (not
just a count), and `_on_apps_applied` re-reads the live installed set to
report which of them genuinely left — the same "positive evidence, not an
assumed exit code" standard `V07` (Task 24, Store Apps) applies:

```python
        def work(w: Worker):
            backup = self.app.backup
            engine = TweakEngine(backup)
            rp_id = backup.create_restore_point(
                f"Debloat apps {datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}", "Debloat")
            entries = self._load_debloat_entries()
            success, targeted = 0, []
            for i, eid in enumerate(entry_ids):
                if w.is_cancelled:
                    break
                entry = entries.get(eid)
                if entry:
                    pkg = entry.get("package", eid)
                    targeted.append(pkg)
                    if engine.apply_tweak(entry, rp_id):
                        success += 1
                w.signals.progress.emit(i + 1)
            return {"success": success, "total": len(entry_ids), "targeted": targeted}
```

```python
    def _on_apps_applied(self, result: Dict) -> None:
        self._apps_progress.setVisible(False)
        self._cancel_apply_btn.setVisible(False)
        self._apply_selected_btn.setEnabled(True)
        self._apply_all_btn.setEnabled(True)
        from modules.debloat import debloat_scanner
        now_installed = set(debloat_scanner.get_installed_packages())
        actually_gone = sum(1 for pkg in result.get("targeted", [])
                           if pkg not in now_installed)
        QMessageBox.information(
            self._widget, "Debloat Complete",
            f"{actually_gone} of {result['total']} app(s) confirmed removed.\n"
            f"A restore point has been created.")
        self._apps_status.setText(
            f"{actually_gone} of {result['total']} app(s) confirmed removed")
        self._on_scan()
```

Add `from modules.debloat import debloat_scanner` at module scope
(already partially imported — check the existing `from
modules.debloat.debloat_scanner import get_installed_packages, ...` line
and add the bare module import alongside it so `debloat_scanner.get_installed_packages`
is reachable for monkeypatching in tests without shadowing the existing
`from ... import` names used elsewhere in the file).

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "fix(debloat): verify what actually left, don't assume a run's exit code

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

Also in this task, while `_on_apps_applied` is already being touched: add
a "Restore Manager…" button to the completion `QMessageBox` (`A22`) by
switching it to a `QMessageBox` instance with a custom button rather than
the static `.information(...)` call:

```python
        box = QMessageBox(self._widget)
        box.setWindowTitle("Debloat Complete")
        box.setText(f"{actually_gone} of {result['total']} app(s) confirmed "
                   f"removed.\nA restore point has been created.")
        restore_btn = box.addButton("Open Restore Manager…",
                                    QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is restore_btn:
            from ui.restore_manager import RestoreManagerDialog
            RestoreManagerDialog(self.app.backup, self._widget).exec()
```

(Check `ui/restore_manager.py`'s actual dialog class name before wiring
this — `MainWindow._open_restore_manager` already constructs it once;
match that exact constructor call rather than guessing the signature.)

`A23` (per-user vs. all-users): add one line to the entry tooltip already
built in Task 12 — `"Removed for all users on this machine"` appended
after the protection reason (or standing alone when not protected) — this
is Debloat's `appx` tweak step, which per `TweakEngine`/`debloat.json`
already always removes `-AllUsers`; the tooltip is documentation of
existing behavior, not a new removal mode. A genuine per-user-only option
is declined here for the same reason `S08` declines it in Store Apps —
see that task's note.

`A24` (sort-and-filter re-running on every populate): **measured and
declined.** At 126 catalog entries this is sub-millisecond; memoizing it
adds a cache-invalidation surface for no measurable benefit at today's
scale. Revisit if the catalog grows an order of magnitude.

---

## Phase 5 — Debloat Tweaks tabs (`T01`-`T25`)

`T01`, `T02`, `T04` were resolved in Task 4. The remaining 22 items:

### Task 14: Search, select-all, zebra, shortcuts, context menu on both tweaks tables

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `T05`, `T06`, `T09`, `T21`, `T22`, `T25`. Same pattern as Task 9 +
Task 11, applied to `_build_tweaks_tab` instead of `_build_apps_tab` — a
search box + select-all/none + "N selected" label above the table, zebra
striping and a context menu on it.

```python
def test_tweaks_search_filters_by_name(monkeypatch):
    mod = _module()
    tweaks = [{"id": "a", "name": "Disable Cortana", "category": "Privacy", "risk": "Low"},
             {"id": "b", "name": "Disable Telemetry", "category": "Telemetry", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")
    search = mod._widget.findChild(QLineEdit, "_search_tweak")
    search.setText("cortana")
    mod._apply_tweaks_filter("tweak")
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    visible = [r for r in range(table.rowCount()) if not table.isRowHidden(r)]
    assert len(visible) == 1


def test_context_menu_copies_the_registry_path_when_the_tweak_has_one(
        monkeypatch):
    mod = _module()
    tweak = {"id": "a", "name": "X", "category": "Privacy", "risk": "Low",
            "steps": [{"type": "registry",
                      "key": r"HKLM\SOFTWARE\Policies\X", "value": "Y"}]}
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: [tweak])
    monkeypatch.setattr(te.TweakEngine, "detect",
                        lambda self, t: te.DetectionResult(te.NOT_APPLIED))
    mod._populate_tweaks_table("tweak")
    path = mod._registry_path_for_tweak(tweak)
    assert path == r"HKLM\SOFTWARE\Policies\X\Y"
```

Add `objectName(f"_search_{tab_type}")` to a new `QLineEdit` in
`_build_tweaks_tab` (mirroring the existing `objectName(f"_status_{tab_type}")`
pattern already used two lines above it), a select-all/none pair, and a
"N selected" label — identical structure to Task 9's `filter_row`, adapted
to look tables up by `f"_table_{tab_type}"` instead of the Apps tab's
single fixed table. `_apply_tweaks_filter(tab_type)`,
`_on_tweaks_select_all(tab_type)`/`_on_tweaks_select_none(tab_type)` mirror
`_apply_apps_filter`/`_on_apps_select_all`/`_on_apps_select_none` exactly,
parametrized by which table to act on. Turn on
`table.setAlternatingRowColors(True)` in `_build_tweaks_tab`.

Context menu's "Copy registry path" resolves only for `registry`/
`registry_delete` step types (a `command`/`script` tweak has no single
path to copy — offer "Copy command" for those instead, reading
`tweak["steps"][0].get("cmd") or tweak["steps"][0].get("command", "")`):

```python
    def _registry_path_for_tweak(self, tweak: dict) -> str:
        for step in tweak.get("steps", []):
            if step.get("type") in ("registry", "registry_delete"):
                key, value = step.get("key", ""), step.get("value", "")
                return f"{key}\\{value}" if value else key
        return ""
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "feat(debloat): search, bulk-select and a context menu on the tweaks tables

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 15: `detect_many()` concurrency + mtime-keyed definitions cache

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `T11`, `T12`.

```python
def test_populate_uses_detect_many_not_one_call_per_tweak(monkeypatch):
    mod = _module()
    tweaks = [{"id": "a", "name": "A", "category": "X", "risk": "Low"},
             {"id": "b", "name": "B", "category": "X", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    calls = []
    def fake_detect_many(self, tweaks, on_result, is_cancelled=None, workers=8):
        calls.append(len(tweaks))
        for t in tweaks:
            on_result(t, te.DetectionResult(te.NOT_APPLIED))
    monkeypatch.setattr(te.TweakEngine, "detect_many", fake_detect_many)
    mod._populate_tweaks_table("tweak")
    assert calls == [2]  # one batched call, not two individual ones


def test_definitions_are_not_reparsed_when_the_file_has_not_changed(
        monkeypatch, tmp_path):
    mod = _module()
    calls = []
    real_open = open
    def counting_open(path, *a, **k):
        if str(path).endswith(".json"):
            calls.append(path)
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", counting_open)
    mod._load_tweak_definitions("tweak")
    mod._load_tweak_definitions("tweak")
    # second call must reuse the cache: no more file opens than the first
    assert len(calls) == len(set(calls))
```

`_populate_tweaks_table`'s per-tweak loop (the `for tweak in sorted(...)`
block) splits into two passes: build all rows first with a placeholder
status, then run `engine.detect_many(tweaks, on_result=...)` once and fill
statuses in as results arrive — `detect_many`'s signature (already defined,
`tweak_engine.py:496-501`) takes `on_result: Callable[[Dict, DetectionResult], None]`,
called per tweak as its probe completes, which is exactly the per-row
update this needs.

`_load_tweak_definitions` gains an mtime-keyed cache:

```python
    def _load_tweak_definitions(self, tab_type: str) -> List[dict]:
        files = (["privacy.json", "telemetry.json", "services.json", "network.json"]
                 if tab_type == "tweak" else ["ai_features.json", "navigation.json"])
        base = os.path.join(os.path.dirname(__file__), "..", "tweaks", "definitions")
        cache_key = tuple(files)
        cached = self._tweak_defs_cache.get(cache_key)
        mtimes = tuple(os.path.getmtime(os.path.join(base, f))
                      for f in files if os.path.exists(os.path.join(base, f)))
        if cached is not None and cached[0] == mtimes:
            return cached[1]

        all_tweaks = []
        for fname in files:
            path = os.path.join(base, fname)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    all_tweaks.extend(json.load(f))
        self._tweak_defs_cache[cache_key] = (mtimes, all_tweaks)
        return all_tweaks
```

Add `self._tweak_defs_cache: Dict[tuple, tuple] = {}` to `__init__`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "perf(debloat): detect concurrently, cache definitions by mtime

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 16: Progress bar + cancel + per-failure detail on apply; dry-run preview

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `T13`, `T14`, `T17`, `T18`. Mirrors Task 11's Apps-tab cancel
button and Task 12's preview dialog, applied to `_on_apply_tweaks`.

```python
def test_apply_tweaks_shows_a_progress_bar(monkeypatch):
    mod = _module()
    bar = mod._widget.findChild(type(mod._apps_progress).__class__) \
        if False else None
    # progress bar exists per tab, named like the table/status labels
    from PyQt6.QtWidgets import QProgressBar
    bar = mod._widget.findChild(QProgressBar, "_progress_tweak")
    assert bar is not None


def test_completion_dialog_names_which_tweaks_failed(monkeypatch):
    mod = _module()
    result = {"success": 1, "total": 2,
             "failures": [("Disable Cortana", "access denied")]}
    shown = {}
    monkeypatch.setattr(dm.QMessageBox, "information",
                        lambda *a, **k: shown.setdefault("text", a[2] if len(a) > 2 else ""))
    mod._on_tweaks_applied(result)
    assert "Disable Cortana" in shown["text"]
```

Add `objectName(f"_progress_{tab_type}")` `QProgressBar` to
`_build_tweaks_tab` (same placement/pattern as the Apps tab's
`self._apps_progress`), shown/hidden and updated from `_on_apply_tweaks`'s
worker exactly as `_do_apply_apps` already does for Apps. `apply_tweak`'s
per-id loop in `_on_apply_tweaks`'s `work()` closure
(`debloat_module.py:546-562`) gains a `failures` list alongside `success`:

```python
            success, failures = 0, []
            for eid in selected_ids:
                if w.is_cancelled:
                    break
                tweak = next((t for t in tweaks if t.get("id") == eid), None)
                if tweak:
                    if engine.apply_tweak(tweak, rp_id):
                        success += 1
                    else:
                        failures.append((tweak.get("name", eid), "apply_tweak returned False"))
            return {"success": success, "total": len(selected_ids), "failures": failures}
```

`_on_tweaks_applied` reports failures by name:

```python
    def _on_tweaks_applied(self, result: Dict) -> None:
        text = f"Applied {result['success']} of {result['total']} tweak(s)."
        if result.get("failures"):
            text += "\n\nDid not apply:\n" + "\n".join(
                f"• {name} — {reason}" for name, reason in result["failures"][:10])
        QMessageBox.information(self._widget, "Tweaks Applied", text)
        self._populate_tweaks_table("tweak")
        self._populate_tweaks_table("ai")
```

Dry-run preview reuses Task 12's `self._preview(...)`, called from
`_on_apply_tweaks` before the worker starts, via a `confirm_destructive`
gate matching Task 8's shape:

```python
        names = [next((t.get("name", eid) for t in tweaks if t.get("id") == eid), eid)
                for eid in selected_ids]
        if not confirm_destructive(
                self._widget, "Apply Tweaks",
                f"Apply {len(names)} tweak(s)?", detail=self._preview(names)):
            return
```

inserted right before the existing `def work(w: Worker):` closure
definition in `_on_apply_tweaks`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "feat(debloat): progress, cancel and per-tweak failure detail on apply

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 17: Sub-grouping the 185-row combined tab; risk color; step-type badge; catalog freshness

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `T03`, `T07`, `T08`, `T10`.

`T07`/`T08`: the "Privacy & Telemetry" tab combines four files (185 rows)
with no grouping. Add a source-file sub-header row per group — not a full
tree widget (`QTableWidget` doesn't support nested rows cleanly; a
non-selectable, styled full-width row acting as a divider is the estab
lished lightweight pattern for this in Qt tables) — inserted between
sorted blocks when the Category actually maps to one of the four files'
own name:

```python
def test_privacy_and_telemetry_shows_source_file_headers(monkeypatch):
    mod = _module()
    tweaks = [
        {"id": "a", "name": "A", "category": "Privacy", "risk": "Low", "_source": "privacy.json"},
        {"id": "b", "name": "B", "category": "Network", "risk": "Low", "_source": "network.json"},
    ]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect_many",
                        lambda self, tweaks, on_result, **k:
                            [on_result(t, te.DetectionResult(te.NOT_APPLIED)) for t in tweaks])
    mod._populate_tweaks_table("tweak")
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    headers = [table.item(r, 1).text() for r in range(table.rowCount())
              if table.item(r, 0) is None]
    assert "Privacy (privacy.json)" in headers
    assert "Network (network.json)" in headers
```

`_load_tweak_definitions` tags each tweak with its source filename
(`tweak["_source"] = fname`, set in the existing `all_tweaks.extend(...)`
loop — change it to a plain `for` loop that sets the tag before
appending). `_populate_tweaks_table` sorts by `(_source, name)` instead of
`name` alone, and inserts a header row (no checkbox item — column 0 left
`None`, which is what the test above checks for) whenever `_source`
changes:

```python
        last_source = None
        for tweak in sorted(tweaks, key=lambda t: (t.get("_source", ""), t.get("name", "").lower())):
            if tweak.get("_source") != last_source:
                last_source = tweak.get("_source")
                header_row = table.rowCount()
                table.insertRow(header_row)
                label = f"{tweak.get('category', '')} ({last_source})"
                header_item = QTableWidgetItem(label)
                header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                header_item.setBackground(QColor("#2a2a2a"))
                table.setItem(header_row, 1, header_item)
                table.setSpan(header_row, 1, 1, 4)
            # ... existing per-row body unchanged, using table.rowCount() for `row` ...
```

`T01`'s color coding (added in Task 4 for status) extends to Risk here —
add `_RISK_COLOR = {"Low": semantic("success"), "Medium": semantic("warning"), "High": semantic("error")}`
and `risk_item.setForeground(QColor(_RISK_COLOR.get(tweak.get("risk", ""), "#e0e0e0")))`.

`T03`'s step-type badge — a small prefix on the Tweak name cell:

```python
def _step_type_badge(tweak: dict) -> str:
    types = {s.get("type", "") for s in tweak.get("steps", [])}
    if types <= {"registry", "registry_delete", "service", "scheduled_task"}:
        return ""  # the common, safely-reversible case: no badge needed
    if "command" in types or "script" in types:
        return "⚠ "  # opaque, reversible only if the tweak declares revert_command
    return ""
```

prefixed onto `name_item`'s text: `_SortableItem(_step_type_badge(tweak) + tweak.get("name", ""))`.

`T10`: a "Catalog: <newest file mtime>" label next to the tab's existing
status label, computed from `max(os.path.getmtime(...) for f in files)`
and formatted `%Y-%m-%d`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "feat(debloat): group the combined tab by source file, color risk, flag opaque steps

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 18: Column persistence, stale-status re-detect, status breakdown, revert link

**Files:** Modify `src/modules/debloat/debloat_module.py`; modify `tests/test_debloat_module.py`

Resolves `T19`, `T20`, `T23`, `T24`.

`T24`: `_on_tab_changed`'s guard (`table.rowCount() == 0`) becomes
"populate if empty, or re-detect statuses in place if not" — a cheap
`detect_many` re-run over already-built rows rather than a full rebuild:

```python
def test_returning_to_a_tab_redetects_status_without_rebuilding_rows(
        monkeypatch):
    mod = _module()
    tweaks = [{"id": "a", "name": "A", "category": "X", "risk": "Low"}]
    monkeypatch.setattr(mod, "_load_tweak_definitions", lambda tab: tweaks)
    monkeypatch.setattr(te.TweakEngine, "detect_many",
                        lambda self, tweaks, on_result, **k:
                            [on_result(t, te.DetectionResult(te.NOT_APPLIED)) for t in tweaks])
    mod._populate_tweaks_table("tweak")
    from PyQt6.QtWidgets import QTableWidget
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    row_count_before = table.rowCount()
    monkeypatch.setattr(te.TweakEngine, "detect_many",
                        lambda self, tweaks, on_result, **k:
                            [on_result(t, te.DetectionResult(te.APPLIED)) for t in tweaks])
    mod._on_tab_changed(1)  # "tweak" tab index, per _TAB_TYPES
    table = mod._widget.findChild(QTableWidget, "_table_tweak")
    assert table.rowCount() == row_count_before
    assert "Applied" in table.item(0, 4).text()
```

```python
    def _on_tab_changed(self, index: int) -> None:
        tab_type = self._TAB_TYPES[index] if index < len(self._TAB_TYPES) else None
        if not tab_type or tab_type == "apps":
            return
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        if table and table.rowCount() == 0:
            self._populate_tweaks_table(tab_type)
        elif table:
            self._redetect_tweaks_table(tab_type)

    def _redetect_tweaks_table(self, tab_type: str) -> None:
        table: QTableWidget = self._widget.findChild(QTableWidget, f"_table_{tab_type}")
        tweaks = self._all_tweaks if tab_type == "tweak" else self._ai_tweaks
        by_id = {t.get("id", ""): t for t in tweaks}

        def on_result(tweak, result):
            for r in range(table.rowCount()):
                item = table.item(r, 4)
                if item and item.data(Qt.ItemDataRole.UserRole) == tweak.get("id"):
                    item.setText(_STATUS_GLYPH[result.status] + " " + te.STATUS_LABELS[result.status])
                    item.setForeground(QColor(_STATUS_COLOR[result.status]))
                    item.setToolTip(result.reason or te.STATUS_LABELS[result.status])
                    break

        self._engine.detect_many(list(by_id.values()), on_result)
```

`T19`: a live breakdown replacing the static `"{len(tweaks)} tweak(s)
loaded"`, computed from the same `on_result` callback (accumulate counts
per status as `detect_many` streams results, update the label once all
have arrived — track completion via a counter closure).

`T20`: a "Revert Applied…" button next to "Save as Custom" that opens the
same `RestoreManagerDialog` Task 13 wired for Apps
(`from ui.restore_manager import RestoreManagerDialog`).

`T23`: persist each table's sort column/order to
`self.app.config` on `on_deactivate`, mirroring `StoreAppsModule._persist_sort`
exactly (same key shape, `f"debloat.{tab_type}.sort_column"`), and restore
it in `_populate_tweaks_table` before `sortItems` is called.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "feat(debloat): re-detect on tab return, live status breakdown, revert link

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Phase 6 — Restore-point policy & composite architecture (`A21`, `P06`-`P09`, `P13`-`P15`, `C02`, `C05` Debloat half)

### Task 19: One restore point per Debloat "session," not one per apply-click

**Files:** Create `src/modules/debloat/debloat_session.py`; modify `src/modules/debloat/debloat_module.py`; test `tests/test_debloat_session.py`

Resolves `A21`, `C02` (Debloat half). Apps → Privacy → AI applied in one
sitting today creates three restore points back to back; Windows' System
Restore frequency floor makes it unclear whether the 2nd/3rd actually
landed. `debloat_session.py` gives the module ONE restore-point id, created
lazily on the first apply of a session and reused (not recreated) by every
subsequent apply within `SESSION_WINDOW_MINUTES` of the last one:

```python
# tests/test_debloat_session.py
from modules.debloat import debloat_session as ds


def test_first_call_creates_a_restore_point():
    created = []
    session = ds.DebloatSession(
        create_rp=lambda label: created.append(label) or "rp-1")
    rp_id = session.restore_point_id("Apps")
    assert rp_id == "rp-1" and created == ["Apps"]


def test_a_second_call_within_the_window_reuses_the_same_id():
    session = ds.DebloatSession(create_rp=lambda label: "rp-1")
    first = session.restore_point_id("Apps")
    second = session.restore_point_id("Privacy tweaks")
    assert first == second == "rp-1"


def test_a_call_after_the_window_creates_a_new_one(monkeypatch):
    times = iter([1000.0, 1000.0 + ds.SESSION_WINDOW_MINUTES * 60 + 1])
    session = ds.DebloatSession(
        create_rp=lambda label: f"rp-{label}",
        now=lambda: next(times))
    first = session.restore_point_id("Apps")
    second = session.restore_point_id("Privacy")
    assert first != second
```

```python
# src/modules/debloat/debloat_session.py
"""One restore point per Debloat "session," reused across Apps / Privacy /
AI applies rather than one per click. Windows' own restore-point frequency
floor makes the difference between "3 checkpoints" and "1 checkpoint, 3
apply operations recorded against it" the difference between silently
losing the 2nd/3rd protection and genuinely having it."""
import time as _time
from typing import Callable, Optional

SESSION_WINDOW_MINUTES = 15


class DebloatSession:
    def __init__(self, create_rp: Callable[[str], str],
                now: Optional[Callable[[], float]] = None):
        self._create_rp = create_rp
        self._now = now or _time.time
        self._rp_id: Optional[str] = None
        self._last_at: Optional[float] = None

    def restore_point_id(self, label: str) -> str:
        now = self._now()
        if self._rp_id is not None and self._last_at is not None and \
                now - self._last_at <= SESSION_WINDOW_MINUTES * 60:
            self._last_at = now
            return self._rp_id
        self._rp_id = self._create_rp(label)
        self._last_at = now
        return self._rp_id
```

Wire it into `DebloatToolsModule.on_start`
(`self._session = DebloatSession(create_rp=lambda label:
self.app.backup.create_restore_point(f"Debloat {label} "
f"{datetime.datetime.now():%Y%m%d_%H%M%S}", "Debloat"))`), and replace the
three `backup.create_restore_point(...)` call sites in `_do_apply_apps`
and `_on_apply_tweaks`'s `work()` closures with
`rp_id = self._session.restore_point_id("Apps")` /
`self._session.restore_point_id("Tweaks" if tab_type == "tweak" else "AI")`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_session.py src/modules/debloat/debloat_module.py tests/test_debloat_session.py
git commit -m "fix(debloat): one restore point per session, not one per apply

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 20: Shared chrome across the composite; cross-tab "removed this session"; AppxCatalog badge in Store Apps

**Files:** Modify `src/modules/debloat/debloat_module.py`, `src/modules/store_apps/store_apps_module.py`; test `tests/test_debloat_module.py`, `tests/test_store_apps_module.py`

Resolves `P06`, `P07`, `P08`, `P13`, `S30`, most of `C10`.

`P06`: `DebloatModule.wrap(tabs)` (the `CompositeModule` override) adds one
shared banner above the Tools/Store Apps tab bar naming what got removed
this session (empty/hidden when nothing has):

```python
def test_wrap_adds_a_removed_this_session_banner():
    mod = dm.DebloatModule()
    tabs = QTabWidget()
    wrapped = mod.wrap(tabs)
    banner = wrapped.findChild(QLabel, "_removed_this_session_banner")
    assert banner is not None
```

```python
    def wrap(self, tabs: QTabWidget) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(0, 0, 0, 0)
        self._removed_banner = QLabel("")
        self._removed_banner.setObjectName("_removed_this_session_banner")
        self._removed_banner.setStyleSheet("color: #888; padding: 4px 8px;")
        self._removed_banner.hide()
        layout.addWidget(self._removed_banner)
        layout.addWidget(tabs)
        return outer

    def note_removed(self, names: List[str]) -> None:
        """Called by either child when it removes something, so the other
        can show it happened without a manual cross-tab refresh."""
        self._session_removed = getattr(self, "_session_removed", []) + names
        self._removed_banner.setText(
            "Removed this session: " + ", ".join(self._session_removed))
        self._removed_banner.setVisible(True)
```

`P07`: each child calls `self.app.module_registry` (already how modules
reach each other elsewhere in this app — check `module_registry.py`'s
lookup method, e.g. `get_module("Debloat")`) to find the parent
`DebloatModule` instance and call `.note_removed([...])` from
`_on_apps_applied` (Debloat side, passing the `targeted` names Task 13
already collects) and from `StoreAppsModule._on_uninstall_done` (passing
`[display for _, display, ok, _ in results if ok]`).

`P08`/`S30`: Store Apps gets a "Known bloatware" badge column reading
`debloat.json`'s package set once at `on_start` (cheap — 126 entries):

```python
def test_a_catalogued_package_gets_the_bloatware_badge(monkeypatch):
    mod = store_module()
    monkeypatch.setattr(sam, "_debloat_catalog_packages",
                        lambda: {"Microsoft.BingWeather"})
    mod._apps = [{"Name": "Microsoft.BingWeather", "InstallLocation": "",
                 "Publisher": "", "Version": ""}]
    mod._on_apps_loaded(mod._apps, None)
    assert mod._table.item(0, 0).toolTip().startswith("Known bloatware")
```

```python
def _debloat_catalog_packages() -> Set[str]:
    import json
    path = os.path.join(os.path.dirname(__file__), "..", "tweaks",
                        "definitions", "debloat.json")
    try:
        with open(path, encoding="utf-8") as f:
            return {e["package"] for e in json.load(f) if e.get("package")}
    except (OSError, json.JSONDecodeError):
        return set()
```

called once in `StoreAppsModule.on_start` into `self._debloat_packages`,
consulted in `_on_apps_loaded`'s row-building loop:
`if name in self._debloat_packages: name_item.setToolTip("Known bloatware — also in the Debloat catalog\n" + (name_item.toolTip() or ""))`.

`P13`: no new code — this item is the *design rationale* for `P06`-`P08`;
closed by them, not by anything additional.

`S29` (shared `appx_service` cache TTL not coordinated between the two
modules that consume it): while `P08`'s cross-reference is being added,
confirm `StoreAppsModule._load_apps` (`use_cache=False`, always forces
fresh) and Debloat's own `get_installed_packages()`
(via `core.appx_service.installed_names()`, which DOES use the cache) are
each deliberate — Store Apps always wants current truth before an
uninstall decision; Debloat's scan tolerating a brief cache hit is fine
for a "what's roughly installed" catalog check. No behavior change needed;
this is confirmed correct as-is rather than fixed, and worth a one-line
comment on `installed_names()` saying so for the next person who wonders.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py src/modules/store_apps/store_apps_module.py tests/test_debloat_module.py tests/test_store_apps_module.py
git commit -m "feat: share removal state between Debloat's tabs; flag known bloat in Store Apps

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 21: Run All flow for Debloat

**Files:** Create `src/modules/debloat/run_all_tab.py`; modify `src/modules/debloat/debloat_module.py`; test `tests/test_debloat_run_all.py`

Resolves `P09`. A fourth tab, "Run All," listing the four presets as
checkable stages (mirroring `UpdatesModule`'s documented Run All tab
shape) that runs whichever are checked sequentially through one
`DebloatSession` restore point (Task 19), reusing
`debloat_presets.resolve_tweak_ids`/`resolve_app_entry_ids` (Task 5) and
`TweakEngine.apply_tweak`/`debloat_scanner`'s existing apply paths — no new
apply mechanism, just sequencing what already exists.

```python
# tests/test_debloat_run_all.py
from modules.debloat.run_all_tab import run_stage


def test_run_stage_applies_every_resolved_id(monkeypatch):
    applied = []
    fake_engine = type("E", (), {"apply_tweak": lambda self, t, rp: applied.append(t["id"]) or True})()
    result = run_stage(
        preset_name="light", tweaks=[{"id": "a", "category": "Privacy"}],
        catalog={}, engine=fake_engine, rp_id="rp-1",
        resolve_tweak_ids=lambda preset, tw: {"a"},
        resolve_app_entry_ids=lambda preset, cat: set(),
        apply_apps=lambda ids, rp: 0)
    assert applied == ["a"]
    assert result["tweaks_applied"] == 1


def test_run_stage_reports_apps_and_tweaks_separately(monkeypatch):
    result = run_stage(
        preset_name="full", tweaks=[], catalog={},
        engine=type("E", (), {"apply_tweak": lambda self, t, rp: True})(),
        rp_id="rp-1",
        resolve_tweak_ids=lambda preset, tw: set(),
        resolve_app_entry_ids=lambda preset, cat: {"e1", "e2"},
        apply_apps=lambda ids, rp: len(ids))
    assert result == {"tweaks_applied": 0, "apps_applied": 2}
```

```python
# src/modules/debloat/run_all_tab.py
"""One stage of a Debloat "Run All" pass: apply a preset's tweaks, then its
apps, against one already-created restore point. Sequencing only — every
actual write goes through the same TweakEngine.apply_tweak / app-removal
path the Apps and Tweaks tabs already use; this file adds no new way to
change the machine, only a way to run several presets back to back.
"""
from typing import Callable, Dict, List, Set


def run_stage(preset_name: str, tweaks: List[dict], catalog: Dict[str, dict],
             engine, rp_id: str,
             resolve_tweak_ids: Callable[[dict, List[dict]], Set[str]],
             resolve_app_entry_ids: Callable[[dict, Dict[str, dict]], Set[str]],
             apply_apps: Callable[[Set[str], str], int]) -> Dict[str, int]:
    from modules.debloat import debloat_presets as dp
    preset = dp.load_preset(preset_name)

    tweak_ids = resolve_tweak_ids(preset, tweaks)
    by_id = {t["id"]: t for t in tweaks}
    tweaks_applied = sum(
        1 for tid in tweak_ids
        if tid in by_id and engine.apply_tweak(by_id[tid], rp_id))

    app_ids = resolve_app_entry_ids(preset, catalog)
    apps_applied = apply_apps(app_ids, rp_id) if app_ids else 0

    return {"tweaks_applied": tweaks_applied, "apps_applied": apps_applied}
```

The tab widget (a `QListWidget` of four checkable presets + Run + a
`QTextEdit` transcript, following `UpdatesModule`'s `_RunAllTab` layout
shape) calls `run_stage` once per checked preset inside one `Worker`,
using `self._session.restore_point_id("Run All")` for `rp_id` and — for
`tweaks`/`catalog`/`apply_apps` — the same accessors
`DebloatToolsModule` already exposes (`self._all_tweaks + self._ai_tweaks`,
`self._load_debloat_entries()`, a small wrapper around `_do_apply_apps`'s
inner apply logic factored out as a plain function both the button handler
and this new tab call). Add `self._tab_widget.addTab(RunAllTab(self), "Run All")`
to `DebloatToolsModule.create_widget`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/run_all_tab.py src/modules/debloat/debloat_module.py tests/test_debloat_run_all.py
git commit -m "feat(debloat): Run All — sequence the four presets through one restore point

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 22: Local usage history

**Files:** Create `src/modules/debloat/debloat_history.py`; modify `src/modules/debloat/debloat_module.py`; test `tests/test_debloat_history.py`

Resolves `P14`, `C05` (Debloat half). A JSON log (not a full HTML report
generator — that's a larger lift `report_generator.py`'s pattern could
serve later; scoped here to what a user can review in-app) at
`%APPDATA%\WindowsTweaker\debloat_history.json`, one entry per apply
action, plus a "View History" button showing the last 20 in a plain
dialog.

```python
# tests/test_debloat_history.py
from modules.debloat import debloat_history as dh


def test_record_and_read_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "debloat_history.json"
    monkeypatch.setattr(dh, "_history_path", lambda: str(path))
    dh.record("Apps", success=3, total=4)
    entries = dh.recent(limit=20)
    assert len(entries) == 1
    assert entries[0]["kind"] == "Apps" and entries[0]["success"] == 3


def test_recent_returns_newest_first(tmp_path, monkeypatch):
    path = tmp_path / "debloat_history.json"
    monkeypatch.setattr(dh, "_history_path", lambda: str(path))
    dh.record("Apps", success=1, total=1)
    dh.record("Tweaks", success=2, total=2)
    entries = dh.recent(limit=20)
    assert [e["kind"] for e in entries] == ["Tweaks", "Apps"]
```

```python
# src/modules/debloat/debloat_history.py
"""A local, append-only log of Debloat apply actions -- what ran, when,
how many of what succeeded. No HTML report (see the audit's P14 note on
scope); a "View History" dialog reads this directly.
"""
import json
import os
from datetime import datetime
from typing import List


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "debloat_history.json")


def record(kind: str, success: int, total: int) -> None:
    path = _history_path()
    entries = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            entries = []
    entries.append({"at": datetime.now().isoformat(timespec="seconds"),
                    "kind": kind, "success": success, "total": total})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries[-200:], f, indent=2)


def recent(limit: int = 20) -> List[dict]:
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return list(reversed(entries))[:limit]
```

Call `debloat_history.record("Apps", success, total)` from `_on_apps_applied`
and `debloat_history.record("Tweaks" / "AI", ...)` from `_on_tweaks_applied`.
Add a "View History" button (Apps tab toolbar) opening a `QDialog` with a
`QListWidget` populated from `debloat_history.recent()`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_history.py src/modules/debloat/debloat_module.py tests/test_debloat_history.py
git commit -m "feat(debloat): a local history of what was applied, when

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 23: Protected-app list review UI

**Files:** Modify `src/modules/debloat/debloat_module.py`; test `tests/test_debloat_module.py`

Resolves `P15`. A "Protected Apps…" button (Apps tab toolbar) opening a
read-only dialog listing `PROTECTED_APPS`/`PROTECTED_REASONS` (7 entries
after Task 12) — nothing to compute, just a `QListWidget` populated once:

```python
def test_protected_apps_dialog_lists_every_protected_app(monkeypatch):
    mod = _module()
    shown = []
    class FakeDialog:
        def __init__(self, *a, **k): pass
        def exec(self): shown.append(1)
    monkeypatch.setattr(dm, "QDialog", FakeDialog)
    mod._on_show_protected_apps()
    assert shown == [1]
```

```python
    def _on_show_protected_apps(self) -> None:
        from PyQt6.QtWidgets import QDialog, QListWidget, QVBoxLayout
        dlg = QDialog(self._widget)
        dlg.setWindowTitle("Protected Apps")
        layout = QVBoxLayout(dlg)
        listw = QListWidget()
        for pkg, reason in sorted(PROTECTED_REASONS.items()):
            listw.addItem(f"{pkg} — {reason}")
        layout.addWidget(listw)
        dlg.resize(480, 300)
        dlg.exec()
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/debloat/debloat_module.py tests/test_debloat_module.py
git commit -m "feat(debloat): a reviewable list of protected apps

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

`P10` (audit that every `debloat.json` `appx` step is correctly exempted
from the command/script `detect`-block rule): a read-only check, folded
into Task 3's `test_debloat_definitions.py` rather than its own task —
add one more test there:

```python
def test_no_debloat_entry_declares_a_command_or_script_step(catalog):
    """appx removals go through TweakEngine's own appx handling, never a
    bare shell command -- if one ever does, it needs the same `detect`
    block command/script tweaks are required to declare elsewhere, and
    this catalog has never needed that rule tested until now."""
    for entry in catalog:
        for step in entry.get("steps", [{"type": "appx"}]):
            assert step.get("type") == "appx", (
                f"{entry['id']}: unexpected step type {step.get('type')!r} "
                f"-- if this is intentional, add a `detect` block and a "
                f"regression test for it")
```

`P12` (frozen-build import completeness): folded into Task 21 and Task 5 —
add `modules.debloat.run_all_tab`, `modules.debloat.debloat_presets`,
`modules.debloat.debloat_history` to `pyinstaller_common.py`'s
`HIDDEN_IMPORTS` as part of whichever of those two tasks lands first
(check the list doesn't already cover them via a wildcard before adding).

`C10` (four sources of truth for "what apps exist"): **substantially
addressed, full unification declined.** Task 3's
`test_debloat_definitions.py` and Task 2's fix already make
`KNOWN_PACKAGES` and `debloat.json` agree, continuously, going forward;
Task 20's cross-reference badge closes the gap on the Store Apps side.
A single `AppxCatalog` class wrapping all four lists behind one interface
would be real DRY but is speculative abstraction over two lists that are
now tested against each other and two (`PROTECTED_APPS`, `SYSTEM_PACKAGES`)
that serve genuinely different purposes (a removal *safety* tier vs. a
hard *cannot*-remove rule) — collapsing them into one class would blur
that distinction more than it would clarify anything. Declined for now;
revisit if a fifth list appears.

---

## Phase 7 — Store Apps (`V06`-`V08`, `S01`-`S30`)

### Task 24: Fix the silent `except`, verify uninstall, wire `NumericSortItem`

**Files:** Modify `src/modules/store_apps/store_apps_module.py`; modify `tests/test_store_apps_module.py`

Resolves `V06`, `V07`, `V08` — the three verified Store Apps defects.

```python
def test_resolve_sid_to_name_logs_on_failure(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    def boom(*a, **k):
        raise OSError("no such account")
    monkeypatch.setattr("win32security.ConvertStringSidToSid", boom)
    result = sam.resolve_sid_to_name("S-1-5-21-1-2-3-1001")
    assert result == ""
    assert "no such account" in caplog.text


def test_uninstall_result_is_verified_against_a_fresh_appx_list(monkeypatch):
    """returncode==0 alone must not be believed -- Remove-AppxPackage exits
    0 while removing nothing, the same failure V07 documents for the Tweaks
    Apps tab."""
    calls = []
    def fake_run(cmd, **k):
        calls.append(cmd)
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr(sam.subprocess, "run", fake_run)
    # still "installed" after the removal call -- Windows lied about success
    monkeypatch.setattr(sam, "fetch_packages",
                        lambda use_cache=False: [{"Name": "Pkg.Ghost"}])
    ok, reason = sam.verify_uninstalled("Pkg.Ghost")
    assert ok is False
    assert "still installed" in reason.lower()


def test_size_column_is_a_numeric_sort_item(monkeypatch):
    mod = store_module()
    monkeypatch.setattr(sam, "fetch_packages", lambda **k: [
        {"Name": "Pkg.A", "InstallLocation": "", "Publisher": "", "Version": ""}])
    mod._on_apps_loaded(sam.dedupe_by_name(sam.fetch_packages()), None)
    assert isinstance(mod._table.item(0, 3), sam.NumericSortItem)
```

`V06`: `resolve_sid_to_name`
(`store_apps_module.py:74-82`) replace the bare
`except Exception: return ""` with:

```python
    try:
        import win32security
        sid = win32security.ConvertStringSidToSid(sid_str)
        name, domain, _ = win32security.LookupAccountSid(None, sid)
        return f"{domain}\\{name}" if domain else name
    except Exception as exc:                             # noqa: BLE001
        logger.warning("Could not resolve SID %s to an account name: %s",
                       sid_str, exc)
        return ""
```

`V07`: a new `verify_uninstalled(name: str) -> Tuple[bool, str]` function,
re-reading `fetch_packages(use_cache=False)` after a removal:

```python
def verify_uninstalled(name: str) -> Tuple[bool, str]:
    """Positive evidence, not an assumed exit code -- Remove-AppxPackage
    exits 0 while removing nothing (documented for the Tweaks Apps tab;
    Store Apps never had the same check). Re-reads the live package list
    rather than trusting the removal command's own return code."""
    still_there = any(p.get("Name") == name for p in fetch_packages(use_cache=False))
    if still_there:
        return False, f"{name} is still installed after the removal call"
    return True, ""
```

called from `do_uninstall`'s per-target loop right after the existing
`subprocess.run([...])` call, replacing
`results.append((display, name, result.returncode == 0, ...))` with:

```python
                verified_ok, verify_reason = verify_uninstalled(name)
                ok = result.returncode == 0 and verified_ok
                output = result.stdout + result.stderr
                if result.returncode == 0 and not verified_ok:
                    output += f"\n{verify_reason}"
                results.append((display, name, ok, output))
```

`V08`: replace the size item's construction in `_on_apps_loaded`
(`store_apps_module.py:430-433`):

```python
            size_item = NumericSortItem("…", 0)
```

with the real value filled in once the size scan completes — change
`_on_size_ready` (`store_apps_module.py:742-750`) to rebuild the item
rather than just `setText`:

```python
    def _on_size_ready(self, name: str, size: int) -> None:
        if not self._widget_valid(self._table):
            return
        row = self._row_of(name)
        if row < 0:
            return
        self._table.setItem(row, 3, NumericSortItem(human_size(size), max(size, 0)))
```

and the Version column (`store_apps_module.py:426-428`) uses
`core/appx_service.py`'s existing `_version_key` (used today only for
dedup) as the sort value:

```python
            from core.appx_service import _version_key
            ver_text = version[:20] if version else ""
            ver_item = NumericSortItem(ver_text, _version_key(version) or 0.0)
```

check `_version_key`'s actual return type (`tests/test_store_apps_module.py`'s
existing `test_version_key` shows it returns something orderable via `>`
— confirm it's a tuple or float before assuming `or 0.0` is a valid
fallback; if it returns a tuple, use `_version_key(version) or (0,)` and
have `NumericSortItem` accept any `Comparable`, not only `float` — adjust
its type hint from `value: float` to `value: Any` in Task 1 if this task
needs it, since that's a small, backward-compatible widening).

Add `from core.table_ui import NumericSortItem` to imports.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/store_apps/store_apps_module.py tests/test_store_apps_module.py src/core/table_ui.py
git commit -m "fix(store apps): log SID resolution failures, verify uninstall, sort by value

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 25: Confirmation improvements — names always shown, aggregate size, named skips

**Files:** Modify `src/modules/store_apps/store_apps_module.py`; test `tests/test_store_apps_module.py`

Resolves `S05`, `S06`, `S07`.

```python
def test_uninstall_confirmation_names_apps_even_when_few(monkeypatch):
    mod = store_module()
    mod._apps = [{"Name": "Pkg.A", "InstallLocation": ""}]
    seed_two_rows(mod)  # existing test helper populates 2+ rows; adapt as needed
    text = mod._uninstall_confirmation_text(
        names=["Calculator"], skipped_names=[], total_bytes=0)
    assert "Calculator" in text


def test_confirmation_shows_aggregate_size():
    mod = store_module()
    text = mod._uninstall_confirmation_text(
        names=["A", "B"], skipped_names=[], total_bytes=2_500_000_000)
    assert "2." in text and "GB" in text


def test_skipped_system_apps_are_named():
    mod = store_module()
    text = mod._uninstall_confirmation_text(
        names=["A"], skipped_names=["Microsoft.Windows"], total_bytes=0)
    assert "Microsoft.Windows" in text
```

Extract the confirmation text into its own testable function and call it
from `_uninstall` (`store_apps_module.py:598-609`):

```python
    def _uninstall_confirmation_text(self, names: List[str],
                                     skipped_names: List[str],
                                     total_bytes: int) -> str:
        message = f"Uninstall {len(names)} app(s)?\n\n{self._preview(names)}"
        if total_bytes > 0:
            message += f"\n\nThis will free approximately {human_size(total_bytes)}."
        if skipped_names:
            message += ("\n\nSkipped (system apps): "
                       + ", ".join(skipped_names[:10])
                       + ("…" if len(skipped_names) > 10 else ""))
        message += "\n\nThis cannot be undone."
        return message
```

`_preview` (already shown in `store_apps_module.py:697-703`) is unchanged
by this task (it already caps at 10 with "…and N more") — the change is
that it's now ALWAYS called, not gated behind `len(names) > 10`. Update
`_selected_targets` to also return the skipped rows' display names, not
just a count (`store_apps_module.py:573-587`):

```python
    def _selected_targets(self) -> Tuple[List[Tuple[str, str, str]], List[str]]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        targets, skipped_names = [], []
        for r in rows:
            display = self._table.item(r, 0).text()
            name = self._table.item(r, 0).data(Qt.ItemDataRole.UserRole) or display
            removable = self._table.item(r, 4).text()
            if "System" in removable:
                skipped_names.append(display)
                continue
            pfn_item = self._table.item(r, 5)
            pfn = pfn_item.text() if pfn_item else ""
            targets.append((name, display, pfn))
        return targets, skipped_names
```

(the return type's second element changes from `int` to `List[str]`;
update the one call site in `_uninstall` accordingly — `skipped` becomes
`skipped_names`, and its old use as a bare count in the old message
string is replaced by the call to `_uninstall_confirmation_text` above).
Aggregate size: sum each target's already-scanned size from column 3's
`NumericSortItem` (Task 24) via its stored data role — add a small reader:

```python
    def _row_size_bytes(self, package_name: str) -> int:
        row = self._row_of(package_name)
        if row < 0:
            return 0
        item = self._table.item(row, 3)
        value = item.data(_NUMERIC_SORT_ROLE) if item else None
        return int(value) if value else 0
```

(import `_NUMERIC_SORT_ROLE` from `core.table_ui`, or add a public
`NumericSortItem.value(item)` staticmethod in Task 1 instead of exporting
the private role constant — prefer the staticmethod; revise Task 1's
`NumericSortItem` to add
`@staticmethod\n    def value(item) -> Optional[float]:\n        return item.data(_NUMERIC_SORT_ROLE) if isinstance(item, QTableWidgetItem) else None`
and use `NumericSortItem.value(item)` here instead of importing the role
directly).

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/store_apps/store_apps_module.py src/core/table_ui.py tests/test_store_apps_module.py
git commit -m "feat(store apps): always name apps in the confirm dialog, show freed space

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 26: Context menu additions, full-column CSV, per-user removal note, safer export guidance

**Files:** Modify `src/modules/store_apps/store_apps_module.py`; test `tests/test_store_apps_module.py`

Resolves `S08`, `S09`, `S10`, `S11`, `S12`, `S13`, `S14`, `S15`.

`S10`: two context menu entries next to the existing "Copy package name":

```python
        act_copy_pfn = menu.addAction("Copy Package Family Name")
        act_copy_pfn.setEnabled(bool(pfn))
        act_copy_pfn.triggered.connect(lambda: QApplication.clipboard().setText(pfn))
        act_copy_loc = menu.addAction("Copy install location")
        act_copy_loc.setEnabled(bool(location))
        act_copy_loc.triggered.connect(lambda: QApplication.clipboard().setText(location))
```

`S11`: guard `_on_context_menu`'s "Open install folder" with an existence
check, reporting through the app's own error path rather than letting
`os.startfile` raise past this module:

```python
        def _open_folder():
            if not location or not os.path.isdir(location):
                QMessageBox.information(
                    self._table, "Folder not found",
                    f"{location or '(no location recorded)'} no longer exists.")
                return
            os.startfile(location)
        act_open_folder.triggered.connect(_open_folder)
```

`S12`: a tooltip on the disabled Store-page action —
`act_store.setToolTip("This package has no Package Family Name" if not pfn else "")`.

`S13`: `_export`'s CSV branch (`store_apps_module.py:843-847`) writes every
column the table already has, reading it back from the row rather than
re-deriving:

```python
                    writer.writerow(["Package Name", "Display Name", "Publisher",
                                    "Version", "Size", "Architecture"])
                    for r in rows:
                        writer.writerow([
                            self._table.item(r, 0).data(Qt.ItemDataRole.UserRole)
                            or self._table.item(r, 0).text(),
                            self._table.item(r, 0).text(),
                            self._table.item(r, 1).text(),
                            self._table.item(r, 2).text(),
                            self._table.item(r, 3).text(),
                            self._table.item(r, 6).text(),
                        ])
```

(`targets` built earlier in `_export` stays for the `.ps1` branch and the
"nothing to export" check; only the CSV `writer.writerows(targets)` call
is replaced by the loop above, which needs the row index — change the
`targets` list comprehension slightly to also keep `r` alongside each
tuple, or simplest: iterate `rows` directly in the CSV branch instead of
`targets`, applying the same system-app skip inline.)

`S08`/`S14`/`S09`: **per-user-only removal declined**, same reasoning as
`A23` — `-AllUsers` is not a bug, it is this module's one supported
removal mode, and offering a second, narrower removal path multiplies the
verification surface (`Task 24`'s `verify_uninstalled` would need a
per-user variant too) for a need that hasn't shown up as a real complaint,
only as a hypothetical "shared machine" scenario. Instead, make the
existing behavior explicit rather than silent: add
`"Removes for every user on this machine."` to the Uninstall button's
tooltip and to `S09`'s system-package tooltip
(`store_apps_module.py:442-444`), distinguishing the exact-match
`SYSTEM_PACKAGES` reason from the path-based one:

```python
            reason = ("an exact-match core Windows package"
                     if name in SYSTEM_PACKAGES else
                     "installed under C:\\Windows\\SystemApps")
            rem_item.setToolTip(
                f"System packages cannot be uninstalled without breaking "
                f"Windows ({reason})")
```

`S15`: **running the exported script from within the app declined** — the
button that does exactly what the script would (`Uninstall Selected`,
already present, already verified per Task 24) is one click away; adding
a second execution path for a saved, arbitrary multi-line script blob
would need the same `shell=True` discipline `CLAUDE.md` requires
everywhere else in this codebase and adds real surface for no capability
the app doesn't already have. Instead, the export dialog's success message
gains one line pointing at it:

```python
        QMessageBox.information(
            self._widget, "Exported",
            f"Exported {len(targets)} app(s) to:\n{path}\n\n"
            f"To remove them now instead of running the script later, "
            f"select the same apps here and use “Uninstall Selected.”")
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/store_apps/store_apps_module.py tests/test_store_apps_module.py
git commit -m "feat(store apps): richer context menu, full-column export, clearer reasons

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 27: Size-scan thread pool, row index, approximate-size flag

**Files:** Modify `src/modules/store_apps/store_apps_module.py`; test `tests/test_store_apps_module.py`

Resolves `S01`, `S02`, `S03`, `S04`.

```python
def test_size_scan_uses_a_thread_pool_not_one_daemon_thread(monkeypatch):
    mod = store_module()
    mod._apps = [{"Name": f"Pkg.{i}", "InstallLocation": ""} for i in range(20)]
    submitted = []
    class FakePool:
        def submit(self, fn, *a): submitted.append(a); fn(*a)
    monkeypatch.setattr(mod, "_size_pool", FakePool())
    mod._start_size_scan()
    assert len(submitted) == 20


def test_row_index_avoids_a_linear_scan(monkeypatch):
    mod = store_module()
    mod._apps = [{"Name": "Pkg.A"}]
    mod._on_apps_loaded(mod._apps, None)
    assert mod._row_index.get("Pkg.A") == 0
    assert mod._row_of("Pkg.A") == 0  # now O(1) via the index


def test_a_partial_size_read_is_marked_approximate(monkeypatch):
    mod = store_module()
    monkeypatch.setattr(os, "walk", lambda p: iter(
        [("root", [], ["a"])]))
    monkeypatch.setattr(os.path, "getsize",
                        lambda p: (_ for _ in ()).throw(OSError("denied")))
    size, approximate = mod._dir_size_detailed("C:\\fake")
    assert approximate is True
```

`S04`: build `self._row_index: Dict[str, int] = {}` alongside table
population in `_on_apps_loaded`'s row loop (`row_index[name] = row` set
right after `insertRow`), consulted first in `_row_of`:

```python
    def _row_of(self, package_name: str) -> int:
        cached = self._row_index.get(package_name, -1)
        if cached >= 0 and cached < self._table.rowCount() and \
                self._table.item(cached, 0) and \
                self._table.item(cached, 0).data(Qt.ItemDataRole.UserRole) == package_name:
            return cached
        for r in range(self._table.rowCount()):  # fallback: index stale/missing
            it = self._table.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == package_name:
                self._row_index[package_name] = r
                return r
        return -1
```

`S01`: replace the single daemon `threading.Thread` in `_start_size_scan`
with a small pool (`concurrent.futures.ThreadPoolExecutor`, matching
`TweakEngine.detect_many`'s `workers=8` precedent):

```python
    def _start_size_scan(self):
        if self._size_pool is not None:
            return
        from concurrent.futures import ThreadPoolExecutor
        self._size_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="appx-size")
        for app in self._apps:
            self._size_pool.submit(self._scan_one_size, app.get("Name", ""),
                                   app.get("InstallLocation", ""))

    def _scan_one_size(self, name: str, location: str) -> None:
        size, approximate = self._dir_size_detailed(location)
        self._size_signals.size_ready.emit(name, size if not approximate else -abs(size) - 1)
```

(`self._size_pool: Optional[ThreadPoolExecutor] = None` added to
`__init__`; shut it down in `on_stop` with `self._size_pool.shutdown(wait=False)`.)

`S02`/`S03`: `_dir_size` becomes `_dir_size_detailed` returning
`(size: int, approximate: bool)`:

```python
    @staticmethod
    def _dir_size_detailed(path: str, max_entries: int = 30000) -> Tuple[int, bool]:
        if not path or not os.path.isdir(path):
            return 0, False
        total, count, approximate = 0, 0, False
        try:
            for root, _, files in os.walk(path):
                for f in files:
                    count += 1
                    if count > max_entries:
                        return total, True  # stopped counting -- what we
                                            # have so far, marked partial
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        approximate = True
                        logger.debug("_dir_size: could not stat one file",
                                    exc_info=True)
        except OSError:
            approximate = True
            logger.debug("_dir_size: giving up on this read", exc_info=True)
        return total, approximate
```

`_on_size_ready` reads the sign convention `_scan_one_size` encodes
(negative-minus-one meaning "approximate, magnitude is `-size-1`") and
shows a `~` prefix:

```python
    def _on_size_ready(self, name: str, size: int) -> None:
        if not self._widget_valid(self._table):
            return
        row = self._row_of(name)
        if row < 0:
            return
        approximate = size < 0
        real_size = -size - 1 if approximate else size
        text = ("~" + human_size(real_size)) if approximate else human_size(real_size)
        self._table.setItem(row, 3, NumericSortItem(text, max(real_size, 0)))
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/store_apps/store_apps_module.py tests/test_store_apps_module.py
git commit -m "perf(store apps): parallel size scan, O(1) row lookup, mark approximate sizes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 28: Filter/search improvements, select-all-visible, live summary

**Files:** Modify `src/modules/store_apps/store_apps_module.py`; test `tests/test_store_apps_module.py`

Resolves `S16`, `S17`, `S18`, `S19`, `S20`.

```python
def test_field_scoped_search_matches_publisher_only(monkeypatch):
    mod = store_module()
    load_two_apps(mod)  # existing test helper; one Microsoft-published, one not
    mod._search.setText("publisher:microsoft")
    mod._apply_filter()
    visible = [r for r in range(mod._table.rowCount()) if not mod._table.isRowHidden(r)]
    assert len(visible) == 1


def test_select_non_system_only_selects_currently_visible_rows(monkeypatch):
    mod = store_module()
    load_two_apps(mod)
    mod._search.setText("nonexistent-app-name")
    mod._apply_filter()
    mod._select_non_system()
    assert mod._table.selectionModel().selectedRows() == []


def test_status_line_shows_selection_and_size_while_active(monkeypatch):
    mod = store_module()
    load_two_apps(mod)
    mod._table.selectRow(0)
    text = mod.get_status_info()
    assert "selected" in text
```

`S16`: `_apply_filter` recognizes a `field:value` prefix before falling
back to the existing all-fields substring match:

```python
    def _apply_filter(self):
        raw = self._search.text().strip()
        field, _, value = raw.partition(":")
        scoped = field.lower() in ("publisher", "name") and bool(value)
        query = (value if scoped else raw).lower()
        mode = self._filter_combo.currentIndex()
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if name_item is None:
                continue
            display = name_item.text()
            real = name_item.data(Qt.ItemDataRole.UserRole) or display
            pub_item = self._table.item(row, 1)
            publisher = pub_item.text() if pub_item else ""
            removable = self._table.item(row, 4).text()
            is_system = "System" in removable

            visible = True
            if mode == 1 and is_system:
                visible = False
            elif mode == 2 and not is_system:
                visible = False
            if query:
                if scoped and field.lower() == "publisher":
                    haystack = publisher.lower()
                elif scoped:
                    haystack = " ".join([display, real]).lower()
                else:
                    haystack = " ".join([display, real, publisher]).lower()
                if query not in haystack:
                    visible = False
            self._table.setRowHidden(row, not visible)
```

`S18`/`S20`: `_select_non_system` skips hidden rows (the `S17` filter
quick-picks — "Recently updated" sorts by the Version column's
`NumericSortItem` value from Task 24 rather than adding a new filter mode,
since sorting already answers "which are newest" without a fourth combo
entry — declined as a separate filter, covered by sorting):

```python
    def _select_non_system(self):
        self._table.clearSelection()
        selection = self._table.selectionModel()
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            item = self._table.item(row, 4)
            if item and "System" not in item.text():
                selection.select(
                    self._table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows)
```

`S19`: `get_status_info` reports the live selection when one exists:

```python
    def get_status_info(self) -> str:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if rows:
            total = sum(NumericSortItem.value(self._table.item(r, 3)) or 0
                       for r in rows)
            return (f"Store Apps — {len(rows)} selected, "
                    f"~{human_size(int(total))}")
        return f"Store Apps — {len(self._apps)} installed"
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/store_apps/store_apps_module.py tests/test_store_apps_module.py
git commit -m "feat(store apps): field-scoped search, filter-aware select, live selection summary

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 29: Persistence fixes, last-refreshed indicator, vanish logging, race guard

**Files:** Modify `src/modules/store_apps/store_apps_module.py`; test `tests/test_store_apps_module.py`

Resolves `S21`, `S22`, `S23`, `S24`, `S25`.

```python
def test_sort_column_is_actually_restored_after_a_restart(monkeypatch):
    mod = store_module()
    mod.app.config.set("modules.store_apps.sort_column", 2)
    mod.app.config.set("modules.store_apps.sort_order", int(Qt.SortOrder.DescendingOrder))
    load_two_apps(mod)  # goes through create_widget() -> should apply the saved sort
    header = mod._table.horizontalHeader()
    assert header.sortIndicatorSection() == 2


def test_last_refreshed_label_updates_after_a_load(monkeypatch):
    mod = store_module()
    load_two_apps(mod)
    assert mod._last_refreshed_lbl.text() != ""


def test_a_row_vanishing_mid_size_scan_is_logged(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    mod = store_module()
    load_two_apps(mod)
    mod._on_size_ready("Pkg.DoesNotExistAnymore", 100)
    assert "no longer in the table" in caplog.text.lower()
```

`S21`: `create_widget` reads the persisted sort back (it currently never
does — only `_capture_state`/`_restore_state`, same-session only, touch
sort). Add, right after `self._table.setSortingEnabled(True)`
(`store_apps_module.py:259`):

```python
        saved_col = int(self.app.config.get(f"{self._CONFIG_PREFIX}.sort_column", 0) or 0)
        saved_order = int(self.app.config.get(f"{self._CONFIG_PREFIX}.sort_order",
                                              int(Qt.SortOrder.AscendingOrder)))
        header.setSortIndicator(saved_col, Qt.SortOrder(saved_order))
```

`S22`: a `self._last_refreshed_lbl = QLabel("")` added to the toolbar
(next to `refresh_btn`), set at the end of `_on_apps_loaded`:
`self._last_refreshed_lbl.setText(f"Refreshed {datetime.datetime.now():%H:%M}")`.

`S24`: `_on_size_ready`'s early `if row < 0: return` gains a log line
before returning:

```python
        row = self._row_of(name)
        if row < 0:
            logger.debug("Size for %s arrived but it is no longer in the table "
                        "(removed or refreshed away)", name)
            return
```

`S25`: `_selected_targets` guards against a row whose item 0 went missing
between selection and read (a background refresh racing an in-flight
multi-select):

```python
        for r in rows:
            name_cell = self._table.item(r, 0)
            if name_cell is None:
                continue
            display = name_cell.text()
            ...
```

(apply the same `is None: continue` guard to every `self._table.item(r, N)`
access in this method — column 4's `removable` lookup already assumes the
row exists; add the same guard there.)

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/store_apps/store_apps_module.py tests/test_store_apps_module.py
git commit -m "fix(store apps): sort actually persists; last-refreshed shown; race guarded

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 30: Shortcut legend, button hint, token-based styling

**Files:** Modify `src/modules/store_apps/store_apps_module.py`; test `tests/test_store_apps_module.py`

Resolves `S26`, `S27`, `S28`.

```python
def test_uninstall_button_hints_the_delete_shortcut():
    mod = store_module()
    btn = mod._widget.findChild(QPushButton, "_uninstall_btn")
    assert "Del" in btn.toolTip()


def test_shortcuts_legend_is_reachable_from_the_toolbar():
    mod = store_module()
    legend_btn = mod._widget.findChild(QPushButton, "_shortcuts_btn")
    assert legend_btn is not None
```

`S27`: `uninstall_btn.setObjectName("_uninstall_btn")` +
`uninstall_btn.setToolTip("Uninstall the selected app(s)  (Del)")`
(`store_apps_module.py:288-290`).

`S26`: a small "⌨ Shortcuts" button in the toolbar opening a plain
`QMessageBox.information` listing the five (`Ctrl+U` / `Delete` — Uninstall,
`Ctrl+E` — Export, `Ctrl+F` — Search, `Escape` — Clear).

`S28`: the three inline `setStyleSheet(...)` blocks in `create_widget`
(the table style, the uninstall button's `color: #f48771`) move to
`semantic()`/theme-token references instead of hardcoded hex — the table
style's `#2d2d2d`/`#3c3c3c`/`#094771`/`#b0b0b0` already match
`dark.qss`'s own values exactly, so the fix is deleting the inline
`setStyleSheet` calls entirely and letting the shared stylesheet's
`QTableWidget`/`QHeaderView::section` rules apply — check `dark.qss` for
equivalent rules before deleting (add any this module's inline block
covers that the shared sheet doesn't, e.g. `QTableWidget::item:selected`,
as new rules in `dark.qss` itself rather than re-inlining them). The
uninstall button keeps its distinct color (it's semantically "the
destructive one") but via `semantic("error")` rather than a hardcoded hex:
`uninstall_btn.setStyleSheet(f"color: {semantic('error')}; font-weight: bold;")`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/store_apps/store_apps_module.py src/ui/styles/dark.qss tests/test_store_apps_module.py
git commit -m "chore(store apps): shortcut discoverability, theme-token colors

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Phase 8 — Driver Manager (`V10`, `D01`-`D33`)

Two items are scoped down rather than built as originally described, for
reasons matching this codebase's own "measured and declined" precedent
(`CLAUDE.md`'s codebase-audit history):

- **`D04`** (a real Authenticode check per driver) — **declined as
  described.** Driver signing is catalog-based (`.cat` files verified
  against the whole package), not a single signed PE the way
  `core/procengine/signatures.py`'s `verify_signature` checks — applying
  that function to a driver's `.sys` would answer a different question
  than "is this driver's *package* WHQL-signed" and could read as more
  authoritative than it is. Scoped down in Task 31 to labeling the
  existing WMI-reported flag honestly ("as reported by Windows") rather
  than building a check that would silently answer the wrong question.
- **`D10`** (WU driver-to-device matching) — **declined as described.**
  Matching a specific device's hardware ID to a specific WU driver update
  needs the WUA COM search API filtered by device, which
  `modules/updates/windows_updater.py` doesn't do today (it matches
  general update categories, not per-device) — building accurate matching
  is a real, error-prone feature on its own (a wrong match installs an
  incompatible driver), not a quick add. Scoped down in Task 36 to a
  "Check Windows Update" action that opens `ms-settings:windowsupdate` —
  real, safe, and honest about what it does.

### Task 31: Reader hardening — JSON errors, provider classify helper, error-code decode, date-parse flag, honest signed label

**Files:** Modify `src/modules/driver_manager/driver_reader.py`, `src/modules/driver_manager/driver_module.py`; test `tests/test_driver_reader.py` (new)

Resolves `V10` (this is the first real coverage), `D02`, `D05`, `D07`,
`D25`, `D26` (partial — the honest-label half), `D29`.

```python
# tests/test_driver_reader.py
import json

import pytest

from modules.driver_manager import driver_reader as dr


def test_a_malformed_json_payload_raises_a_clear_error(monkeypatch):
    class FakeProc:
        stdout = "{not valid json"
        returncode = 0
    monkeypatch.setattr(dr.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(dr.DriverReadError, match="could not parse"):
        dr.fetch_drivers()


def test_classify_provider_is_shared_by_table_and_export():
    assert dr.classify_provider("Microsoft") == "Microsoft"
    assert dr.classify_provider("Realtek Semiconductor Corp.") == "Third-Party"
    assert dr.classify_provider("Microsoft-compatible XYZ Corp") == "Third-Party"


def test_error_code_decodes_to_a_known_meaning():
    assert "disabled" in dr.decode_error_code(22).lower()
    assert dr.decode_error_code(0) == ""


def test_an_unrecognized_error_code_says_so_rather_than_guessing():
    assert "unrecognized" in dr.decode_error_code(9999).lower()


def test_an_unparseable_driver_date_is_flagged_not_blanked():
    info = dr._build_driver_info({
        "Name": "X", "Class": "Net", "Version": "1.0",
        "Date": "not-a-date", "Publisher": "Vendor", "IsSigned": True,
        "ErrorCode": 0})
    assert "date unreadable" in info.flags.lower()
```

`D02`: wrap `fetch_drivers`'s `json.loads(raw)` call
(`driver_reader.py:55`):

```python
class DriverReadError(RuntimeError):
    """The PowerShell driver query returned something that could not be
    parsed -- distinct from an empty result, which is a real "no drivers"
    answer."""


def fetch_drivers() -> List[DriverInfo]:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_CMD],
        capture_output=True, text=True, errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=90,
    )
    raw = proc.stdout.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DriverReadError(
            f"could not parse the driver list PowerShell returned: {exc}"
        ) from exc
    if isinstance(data, dict):
        data = [data]
    ...
```

(the rest of the function body — the `for d in data:` loop — is unchanged;
only wrap the `json.loads` call, and let `on_error` in `driver_module.py`'s
existing `_do_refresh` worker error handling show `DriverReadError`'s
message, which it already does via `str(err)` on any exception the worker
raises.)

`D29`: extract the loop body's per-driver construction into
`_build_driver_info(d: dict) -> DriverInfo`, and its provider
classification into a standalone function both `driver_module.py`'s table
population and CSV export call, instead of each having its own copy:

```python
def classify_provider(publisher: str) -> str:
    """"Microsoft" only for Microsoft's own strings, not anything that
    merely contains the word -- "Microsoft-compatible XYZ Corp" is a
    real OEM naming pattern and is third-party."""
    normalized = (publisher or "").strip().lower()
    return "Microsoft" if normalized in ("microsoft", "microsoft corporation") \
        else "Third-Party"
```

`D05`: a small decode table for the handful of ConfigManagerErrorCode
values that actually show up in practice (Microsoft's own CM_PROB_*
constants):

```python
_ERROR_CODE_MEANINGS = {
    1: "This device is not configured correctly",
    3: "The driver may be corrupted, or the system may be low on memory",
    10: "This device cannot start",
    18: "Reinstall the drivers for this device",
    19: "Registry may be corrupted",
    21: "Windows is removing this device",
    22: "This device is disabled",
    24: "This device is not present, not working properly, or does not have all its drivers installed",
    28: "The drivers for this device are not installed",
    31: "This device is not working properly because Windows cannot load the drivers required",
    32: "A driver for this device was disabled",
    37: "Windows cannot initialize the device driver for this hardware",
    39: "Windows cannot load the device driver for this hardware — the driver may be corrupted or missing",
    43: "Windows has stopped this device because it has reported problems",
}


def decode_error_code(code: int) -> str:
    if not code:
        return ""
    return _ERROR_CODE_MEANINGS.get(
        code, f"unrecognized ConfigManagerErrorCode {code}")
```

`D25`: the existing `except ValueError: logger.debug(...)` around date
parsing (`driver_reader.py:82-83`) additionally sets a flag:

```python
        date_str = ""
        date_obj = None
        date_unreadable = False
        if raw_date and len(raw_date) >= 8:
            try:
                date_obj = datetime.datetime.strptime(raw_date[:8], "%Y%m%d")
                date_str = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                date_unreadable = True
                logger.debug("Could not parse driver date %r", raw_date, exc_info=True)
        elif raw_date:
            date_unreadable = True
```

and the flags list (`driver_reader.py:85-91`) gains one more entry:

```python
        flags = []
        if not signed:
            flags.append("🔴 Unsigned (as reported by Windows)")
        if error_code != 0:
            flags.append(f"🔴 Error({error_code}): {decode_error_code(error_code)}")
        if date_obj and date_obj < two_years_ago:
            flags.append("🟠 Old")
        if date_unreadable:
            flags.append("⚪ date unreadable")
```

(`D26`'s honest-label half is the `"(as reported by Windows)"` suffix
above — see the Phase-header note for why a real per-driver Authenticode
check is declined rather than attempted here.)

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/driver_manager/driver_reader.py src/modules/driver_manager/driver_module.py tests/test_driver_reader.py
git commit -m "test(driver manager): first coverage — parse errors, error codes, shared classify

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 32: Missing devices, pseudo-driver filtering, class grouping, old-driver threshold

**Files:** Modify `src/modules/driver_manager/driver_reader.py`, `src/modules/driver_manager/driver_module.py`; test `tests/test_driver_reader.py`

Resolves `D03`, `D06`, `D27`, `D28`.

`D03`: `Win32_PnPSignedDriver` excludes devices with no driver at all.
Add a second WMI query, `Win32_PnPEntity` filtered to `ConfigManagerErrorCode
!= 0`, merged into the result as rows with `version=""`, `publisher=""`,
`signed=False`, carrying their own error code:

```python
def test_devices_with_no_driver_are_included():
    devices = dr._merge_driverless_devices(
        drivers=[], driverless_raw='[{"Name":"Unknown device","ErrorCode":28}]')
    assert len(devices) == 1
    assert devices[0].version == ""
    assert devices[0].error_code == 28
```

```python
_PS_CMD_DRIVERLESS = r"""
$devices = Get-CimInstance -ClassName Win32_PnPEntity |
    Where-Object { $_.ConfigManagerErrorCode -ne 0 -and $_.Name }
$devices | Select-Object Name, ConfigManagerErrorCode, PNPClass |
    ConvertTo-Json -Compress -Depth 2
"""


def _merge_driverless_devices(drivers: List[DriverInfo],
                              driverless_raw: str) -> List[DriverInfo]:
    """Win32_PnPSignedDriver only lists devices that HAVE a driver. A
    device Windows could not find one for at all -- the yellow-bang case
    -- needs a second query, or this "driver manager" never shows the
    machine's actual problem devices."""
    have_names = {d.device_name for d in drivers}
    if not driverless_raw.strip():
        return drivers
    try:
        raw_devices = json.loads(driverless_raw)
    except json.JSONDecodeError:
        return drivers
    if isinstance(raw_devices, dict):
        raw_devices = [raw_devices]
    extra = []
    for dev in raw_devices:
        name = dev.get("Name") or ""
        if not name or name in have_names:
            continue
        code = int(dev.get("ConfigManagerErrorCode") or 0)
        extra.append(DriverInfo(
            device_name=name, driver_class=dev.get("PNPClass") or "",
            version="", date="", publisher="", signed=False,
            error_code=code,
            flags=f"🔴 No driver installed: {decode_error_code(code)}"))
    return drivers + extra
```

Called from `fetch_drivers` after building `drivers`, via a second
`subprocess.run` with `_PS_CMD_DRIVERLESS`.

`D27`: pseudo-drivers filtered by class (`SoftwareComponent` and similar
non-hardware classes) get their own flag rather than disappearing, so
they're distinguishable in the table but a "hide pseudo-devices" checkbox
(added in `driver_module.py`, defaulting ON) can filter them:

```python
_PSEUDO_CLASSES = {"SoftwareComponent", "SoftwareDevice", "PrintQueue"}
```

used in `driver_module.py`'s `_populate`'s filter predicate alongside the
existing text filter.

`D28`: Class column groups via the existing filter combo pattern (Task 35
adds the combo itself; this task only ensures `driver_class` values are
consistent enough to group by — no change needed beyond what's already
correct).

`D06`: the "Old" threshold becomes configurable via a `QSpinBox` (default
730 days, matching today's hardcoded value) next to the filter row Task 35
adds; `fetch_drivers` gains an optional `old_threshold_days: int = 730`
parameter threaded through from the module.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/driver_manager/driver_reader.py src/modules/driver_manager/driver_module.py tests/test_driver_reader.py
git commit -m "feat(driver manager): surface devices with no driver at all

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 33: Context menu — uninstall driver, open folder, cross-reference to Cleanup's panel; roll-back routed to Device Manager

**Files:** Modify `src/modules/driver_manager/driver_module.py`, `src/modules/driver_manager/driver_reader.py`; test `tests/test_driver_module.py` (new)

Resolves `D09`, `D12`, `D13`, `D14`, the rest of `D26`.

This reuses `cleanup/cleanup_scanner/driver_store.py`'s existing,
already-tested `pnputil /delete-driver` machinery
(`DriverPackage.published`, `parse_enum_drivers`) rather than
reimplementing driver removal — the same `oem##.inf` addressing, the same
confirmation-gated action pattern `_driver_panel.py` already establishes,
so Driver Manager's uninstall action is a thin caller of code this app
already trusts, not a second implementation of it.

```python
# tests/test_driver_module.py
"""DriverModule's context menu. Nothing here touches a real driver --
pnputil and the DriverStore enumeration are monkeypatched throughout.
"""
from PyQt6.QtCore import Qt

from modules.driver_manager import driver_module as dm


class _FakeApp:
    thread_pool = None
    backup = None


def _module():
    mod = dm.DriverModule()
    mod.on_start(_FakeApp())
    mod.create_widget()
    return mod


def test_uninstall_is_offered_only_for_oem_numbered_drivers(monkeypatch):
    mod = _module()
    assert dm.published_name_for("oem60.inf") == "oem60.inf"
    assert dm.published_name_for("usb.inf") is None  # inbox driver


def test_uninstall_confirms_before_calling_pnputil(monkeypatch):
    mod = _module()
    calls = []
    monkeypatch.setattr(dm, "confirm_destructive", lambda *a, **k: False)
    monkeypatch.setattr(dm.subprocess, "run", lambda *a, **k: calls.append(a))
    mod._do_uninstall_driver("oem60.inf", "Some Device")
    assert calls == []


def test_uninstall_confirmed_calls_delete_driver(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dm, "confirm_destructive", lambda *a, **k: True)
    calls = []
    class R: returncode = 0; stdout = ""; stderr = ""
    monkeypatch.setattr(dm.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or R())
    mod._do_uninstall_driver("oem60.inf", "Some Device")
    assert calls[0][:3] == ["pnputil", "/delete-driver", "oem60.inf"]
    assert "/uninstall" in calls[0]
```

`driver_reader.py` gains a small helper distinguishing an OEM-published
driver (removable) from an inbox one (not, by this path):

```python
import re

_OEM_INF_RE = re.compile(r"^oem\d+\.inf$", re.IGNORECASE)


def published_name_for(inf_name: str) -> Optional[str]:
    """`inf_name` as pnputil would address it, or None for an inbox
    driver (e.g. "usb.inf") this path cannot remove -- only an
    OEM-numbered package (from DriverStore, i.e. one a device manufacturer
    or Windows Update installed) can be `pnputil /delete-driver`'d."""
    return inf_name if inf_name and _OEM_INF_RE.match(inf_name) else None
```

`_PS_CMD` (`driver_reader.py:11-30`) adds `InfName` to the selected
fields, and `DriverInfo` gains an `inf_name: str = ""` field (default so
existing callers/tests constructing it positionally keep working — check
`test_driver_reader.py`'s `_build_driver_info` calls from Task 31 still
pass; add `"InfName"` to the dict keys those tests already build to keep
them realistic).

In `driver_module.py`, add a context menu (mirrors Store Apps'
`_on_context_menu` exactly in shape):

```python
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
```

```python
    def _on_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        device_name = self._table.item(row, 0).text()
        driver = next((d for d in self._drivers_ref[0]
                      if d.device_name == device_name), None)
        published = published_name_for(driver.inf_name) if driver else None

        menu = QMenu(self._table)
        act_copy = menu.addAction("Copy device name")
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(device_name))
        menu.addSeparator()
        act_uninstall = menu.addAction("Uninstall driver package…")
        act_uninstall.setEnabled(bool(published))
        act_uninstall.setToolTip(
            "" if published else
            "This is a driver Windows ships inline, not an installed "
            "OEM package — remove it from Device Manager instead.")
        act_uninstall.triggered.connect(
            lambda: self._do_uninstall_driver(published, device_name))
        act_rollback = menu.addAction("Roll back to previous version…")
        act_rollback.setToolTip(
            "Needs the previous driver still cached, which this app does "
            "not track — opens Device Manager, where Windows can check.")
        act_rollback.triggered.connect(self._open_devmgr)
        menu.addSeparator()
        act_cleanup = menu.addAction("Open Cleanup's Superseded Drivers panel")
        act_cleanup.triggered.connect(self._open_cleanup_driver_panel)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _do_uninstall_driver(self, published: str, device_name: str) -> None:
        if not self.require_admin():
            return
        if not confirm_destructive(
                self._widget, "Uninstall Driver Package",
                f"Uninstall the driver package for {device_name}?",
                detail=f"Runs: pnputil /delete-driver {published} /uninstall\n"
                      f"The device may stop working until Windows finds "
                      f"another driver for it."):
            return
        result = subprocess.run(
            ["pnputil", "/delete-driver", published, "/uninstall", "/force"],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            self._status_lbl.setText(f"Removed driver package {published}")
        else:
            self._status_lbl.setText(
                f"Could not remove {published}: {result.stdout + result.stderr}")
        self._do_refresh()

    def _open_cleanup_driver_panel(self) -> None:
        if self.app and hasattr(self.app, "module_registry"):
            cleanup = self.app.module_registry.get_module("Cleanup")
            if cleanup:
                self.app.event_bus.publish("nav.request_module", "Cleanup")
                # The Superseded Drivers panel lives on Cleanup's Large
                # Items tab -- selecting the module is what this app's
                # existing navigation event already does; switching to
                # that specific inner tab is Cleanup's own concern, not
                # something Driver Manager reaches into.
```

(Check `module_registry.get_module` and the `nav.request_module` event
name against `core/module_registry.py`/`core/events.py`'s real names
before wiring this — `main_window.py` already subscribes to a navigation
event for the sidebar; use its exact constant rather than a guessed
string.)

Add `from core.confirm import confirm_destructive`,
`from modules.driver_manager.driver_reader import published_name_for`,
`from PyQt6.QtWidgets import QApplication, QMenu` to `driver_module.py`'s
imports.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/driver_manager/driver_module.py src/modules/driver_manager/driver_reader.py tests/test_driver_module.py
git commit -m "feat(driver manager): uninstall an OEM driver package, via pnputil /delete-driver

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 34: Confirmation, cancel, real progress, elevation messaging on backup

**Files:** Modify `src/modules/driver_manager/driver_module.py`; test `tests/test_driver_module.py`

Resolves `D01`, `D08`, `D16`, `D17`, `D18`, `D33`.

```python
def test_backup_confirms_before_starting(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dm.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: "C:\\backup")
    asked = []
    monkeypatch.setattr(dm, "confirm_destructive",
                        lambda *a, **k: asked.append(1) or False)
    started = []
    monkeypatch.setattr(mod.app.thread_pool if mod.app.thread_pool else object(),
                        "start", lambda w: started.append(1), raising=False)
    mod._backup_drivers()
    assert asked and started == []


def test_backup_disables_export_and_filter_while_running(monkeypatch):
    mod = _module()
    monkeypatch.setattr(dm.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: "C:\\backup")
    monkeypatch.setattr(dm, "confirm_destructive", lambda *a, **k: True)
    mod._backup_drivers()
    assert mod._export_btn.isEnabled() is False
    assert mod._filter_edit.isEnabled() is False


def test_cancel_button_cancels_the_backup_worker(monkeypatch):
    mod = _module()
    mod._backup_worker = type("W", (), {"cancelled": False,
                                        "cancel": lambda self: setattr(self, "cancelled", True)})()
    mod._on_cancel_backup()
    assert mod._backup_worker.cancelled is True
```

`_backup_drivers` (`driver_module.py:226-257`) gains a confirmation and
disables Export/Filter (not only Backup/Refresh, which it already does):

```python
    def _backup_drivers(self) -> None:
        if self._widget is None:
            return
        folder = QFileDialog.getExistingDirectory(
            self._widget, "Select Backup Folder")
        if not folder:
            return
        if not confirm_destructive(
                self._widget, "Export All Drivers",
                f"Export every driver to {folder}?",
                detail="This can take a while and cannot be cancelled "
                      "part-way through cleanly — pnputil does not report "
                      "progress per driver.",
                irreversible=False):
            return
        self._backup_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._filter_edit.setEnabled(False)
        self._cancel_backup_btn.setVisible(True)
        ...  # existing worker construction unchanged
        self._backup_worker = worker
```

(store the export/filter buttons as `self._export_btn`/keep
`self._filter_edit` already an attribute; re-enable all four in
`_on_backup_done`/`_on_backup_error` alongside the existing two.)

`D16`/`D33`: a Cancel button next to the progress bar, and — since
`pnputil /export-driver` is one blocking call with no way to interrupt it
mid-run — the confirmation dialog says so upfront (above) rather than
offering a cancel that can't actually stop the subprocess; the button
instead cancels the *Worker* (preventing the `on_result` callback from
re-enabling the UI prematurely if the process is killed by other means)
and is labeled accordingly:

```python
        self._cancel_backup_btn = QPushButton("Cancel (stops after current file)")
        self._cancel_backup_btn.setVisible(False)
        self._cancel_backup_btn.clicked.connect(self._on_cancel_backup)
        toolbar.addWidget(self._cancel_backup_btn)
```

```python
    def _on_cancel_backup(self) -> None:
        if self._backup_worker is not None:
            self._backup_worker.cancel()
        self._cancel_backup_btn.setEnabled(False)
```

`D17`: real progress needs per-driver export calls rather than one
`pnputil /export-driver *`. Change `do_backup`'s body to iterate
`self._drivers_ref[0]`, calling `pnputil /export-driver <device-inf-or-oem> <folder>`
per driver whose `inf_name` resolves via `published_name_for` (Task 33),
skipping inbox drivers (which have nothing to export), and emitting
`worker.signals.progress.emit(i + 1)` per completed one — set
`self._progress.setRange(0, len(exportable))` before starting instead of
the indeterminate `setRange(0, 0)`.

`D08`: on a non-zero return code, distinguish an access-denied stderr from
any other failure and say so:

```python
    def _on_backup_error_line(self, stderr: str) -> str:
        if "access" in stderr.lower() and "denied" in stderr.lower():
            return "denied — this driver package needs administrator"
        return stderr.strip() or "failed for an unreported reason"
```

used in `_on_backup_done`'s failure branch instead of the current bare
`f"Driver backup finished with code {returncode}."`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): confirm, real per-driver progress, and a real cancel on backup

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 35: Filters, bulk-select-flagged, sort/column persistence, numeric sort, empty state

**Files:** Modify `src/modules/driver_manager/driver_module.py`; test `tests/test_driver_module.py`

Resolves `D15`, `D19`, `D20`, `D21`, `D23`, `D24`, and the `D06` combo
started in Task 32.

```python
def test_signed_only_filter_hides_unsigned_rows(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        dr.DriverInfo("A", "Net", "1.0", "", "V", True, 0, ""),
        dr.DriverInfo("B", "Net", "1.0", "", "V", False, 0, "🔴 Unsigned"),
    ]
    mod._flag_filter_combo.setCurrentText("Unsigned only")
    mod._populate(mod._drivers_ref[0], "")
    assert mod._table.rowCount() == 1
    assert mod._table.item(0, 0).text() == "B"


def test_select_all_flagged_checks_every_red_row(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        dr.DriverInfo("A", "Net", "1.0", "", "V", True, 0, ""),
        dr.DriverInfo("B", "Net", "1.0", "", "V", False, 22, "🔴 Unsigned 🔴 Error(22)"),
    ]
    mod._populate(mod._drivers_ref[0], "")
    mod._select_all_flagged()
    assert len(mod._table.selectionModel().selectedRows()) == 1


def test_date_and_size_style_columns_use_numeric_sort():
    mod = _module()
    mod._drivers_ref[0] = [
        dr.DriverInfo("A", "Net", "1.0", "2020-01-01", "V", True, 0, ""),
        dr.DriverInfo("B", "Net", "1.0", "2026-01-01", "V", True, 0, ""),
    ]
    mod._populate(mod._drivers_ref[0], "")
    assert isinstance(mod._table.item(0, 3), dm.NumericSortItem)
```

`D21`/`D06`: a filter combo added to the toolbar (`"All", "Signed only",
"Unsigned only", "Has error", "Old"`), consulted in `_populate` alongside
the existing text filter:

```python
        flag = self._flag_filter_combo.currentText() if hasattr(self, "_flag_filter_combo") else "All"
        visible = [
            d for d in drivers
            if (not ft or ft in d.device_name.lower() or ft in d.driver_class.lower())
            and (flag == "All"
                 or (flag == "Signed only" and d.signed)
                 or (flag == "Unsigned only" and not d.signed)
                 or (flag == "Has error" and d.error_code != 0)
                 or (flag == "Old" and "Old" in d.flags))
        ]
```

`D15`: a "Select flagged" button, selecting every row whose `flags` is
non-empty:

```python
    def _select_all_flagged(self) -> None:
        self._table.clearSelection()
        selection = self._table.selectionModel()
        for r in range(self._table.rowCount()):
            flags_item = self._table.item(r, 7)
            if flags_item and flags_item.text().strip():
                selection.select(self._table.model().index(r, 0),
                                 QItemSelectionModel.SelectionFlag.Select
                                 | QItemSelectionModel.SelectionFlag.Rows)
```

`D20`: the Date column becomes a `NumericSortItem` keyed on the parsed
`datetime`'s timestamp (falling back to 0 for an unparseable date, which
Task 31 already flags separately so it's not silently miscategorized):

```python
            date_item = NumericSortItem(
                d.date, _date_sort_value(d.date))
```

```python
def _date_sort_value(date_str: str) -> float:
    if not date_str:
        return 0.0
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").timestamp()
    except ValueError:
        return 0.0
```

(add `from core.table_ui import NumericSortItem` and
`import datetime` to `driver_module.py`).

`D23`: sort column/order persisted to `self.app.config` on `on_deactivate`
and restored in `create_widget`, mirroring Store Apps' pattern exactly
(same key shape, `f"driver_manager.sort_column"`).

`D19`: an `EmptyState` (reusing `ui/empty_state.py`, same as Store Apps)
shown instead of a bare empty table when `_drivers_ref[0]` is empty after
a load — wrap `self._table` in a `QStackedWidget` alongside an
`EmptyState("🖨️", "No drivers loaded", "Click Refresh to scan.", "Refresh")`,
switching index in `_do_refresh`/`on_result` the same way
`StoreAppsModule._set_empty` does.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): flag filters, bulk-select flagged rows, numeric date sort, empty state

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 36: Export respects the filter, single-driver export, WU-settings link, auto-refresh-on-elevation, chunked timeout

**Files:** Modify `src/modules/driver_manager/driver_module.py`, `src/modules/driver_manager/driver_reader.py`; test `tests/test_driver_module.py`, `tests/test_driver_reader.py`

Resolves `D10` (descoped, see the Phase 8 header), `D11`, `D22`, `D31`,
`D32`.

```python
def test_export_writes_only_visible_rows(tmp_path, monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        dr.DriverInfo("A", "Net", "1.0", "", "V", True, 0, ""),
        dr.DriverInfo("B", "Net", "1.0", "", "V", True, 0, ""),
    ]
    mod._filter_edit.setText("a")
    mod._populate(mod._drivers_ref[0], "a")
    out = tmp_path / "drivers.csv"
    monkeypatch.setattr(dm.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), ""))
    mod._do_export()
    text = out.read_text(encoding="utf-8")
    assert "A" in text and "B" not in text


def test_fetch_drivers_chunks_the_wmi_query(monkeypatch):
    """A single 90s call across every driver dies with zero partial
    results if it times out. Chunking means a slow query loses at most
    one chunk's worth, not the whole list."""
    calls = []
    def fake_run(cmd, **k):
        calls.append(k.get("timeout"))
        class R: stdout = "[]"; returncode = 0
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    dr.fetch_drivers()
    assert all(t and t <= 30 for t in calls), \
        "expected per-chunk timeouts well under the old flat 90s"
```

`D22`: `_do_export` writes only rows currently visible in the table
(respecting both the text filter and Task 35's flag filter) instead of
`self._drivers_ref[0]` unconditionally:

```python
    def _do_export(self) -> None:
        if self._widget is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Export CSV", "drivers.csv", "CSV (*.csv)")
        if not path:
            return
        visible_names = {self._table.item(r, 0).text()
                        for r in range(self._table.rowCount())
                        if not self._table.isRowHidden(r)}
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for d in self._drivers_ref[0]:
                if d.device_name not in visible_names:
                    continue
                writer.writerow([d.device_name, d.driver_class, d.version, d.date,
                                d.publisher, classify_provider(d.publisher),
                                d.signed, d.flags])
```

`D11`: the context menu's "Uninstall driver package…" (Task 33) already
covers single-driver removal; single-driver *export* is added next to it:

```python
        act_export_one = menu.addAction("Export this driver…")
        act_export_one.setEnabled(bool(published))
        act_export_one.triggered.connect(
            lambda: self._export_one_driver(published))
```

```python
    def _export_one_driver(self, published: str) -> None:
        folder = QFileDialog.getExistingDirectory(self._widget, "Export Driver")
        if not folder:
            return
        result = subprocess.run(
            ["pnputil", "/export-driver", published, folder],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self._status_lbl.setText(
            f"Exported {published}" if result.returncode == 0
            else f"Could not export {published}: {result.stdout + result.stderr}")
```

`D10` (descoped): a "Check Windows Update" button opening
`ms-settings:windowsupdate`:

```python
    def _open_windows_update_settings(self) -> None:
        os.startfile("ms-settings:windowsupdate")
```

with a tooltip explaining the scope-down honestly:
`"Opens Windows Update settings — this app does not match a specific device to a specific driver update."`

`D31`: subscribe to the same `core.admin_utils`-driven elevation-change
signal `MainWindow._on_restart_as_admin` fires through (check
`core/events.py` for whether an `ADMIN_STATUS_CHANGED`-style event
already exists from the admin-banner work; if not, this is out of scope
for this pass — the app restarts as a new elevated process on "Restart as
Admin" rather than elevating in place, per `restart_as_admin()`'s
existing behavior, which means there IS no in-process elevation change to
observe, and `D31` as originally written assumed one that doesn't exist).
**Correction from the audit:** re-verified against `core/admin_utils.py`
while planning this task — `restart_as_admin()` spawns a new process and
exits the old one; there is no live "became elevated" transition inside a
running `DriverModule` to refresh on. `D31` is **not applicable** as
described; no code change needed here.

`D32`: `_PS_CMD` chunking — split the WMI query into batches by device
class (a handful of `Get-CimInstance ... -Filter "DeviceClass='Net'"`-
style calls rather than one unfiltered query), each with its own shorter
timeout (`30s` rather than `90s`), so one slow chunk doesn't zero out the
whole refresh:

```python
_DRIVER_CLASSES_TO_QUERY = ("Net", "Display", "HDC", "USB", "Media",
                            "System", "Monitor", "Keyboard", "Mouse")


def fetch_drivers() -> List[DriverInfo]:
    drivers: List[DriverInfo] = []
    seen_names = set()
    for cls in _DRIVER_CLASSES_TO_QUERY:
        chunk = _fetch_drivers_for_class(cls)
        for d in chunk:
            if d.device_name not in seen_names:
                seen_names.add(d.device_name)
                drivers.append(d)
    # Anything outside the enumerated classes still gets one final,
    # unfiltered pass so nothing is silently dropped -- the class list
    # above is an optimization (query the common ones fast, in parallel-
    # sized chunks), not a filter on what counts as a driver.
    for d in _fetch_drivers_for_class(None):
        if d.device_name not in seen_names:
            seen_names.add(d.device_name)
            drivers.append(d)
    drivers.sort(key=lambda d: (d.error_code != 0, not d.signed, d.device_name))
    return drivers
```

(`_fetch_drivers_for_class(cls: Optional[str])` is the existing
`fetch_drivers` body, parametrized: `-Filter "DeviceClass='{cls}'"`
appended to the PowerShell `Get-CimInstance` call when `cls` is not
`None`, `timeout=30` instead of `90`; the JSON-parse and `DriverInfo`
construction logic is unchanged, just wrapped in this new inner function
so `fetch_drivers` can call it per chunk.)

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/driver_manager/driver_module.py src/modules/driver_manager/driver_reader.py tests/test_driver_module.py tests/test_driver_reader.py
git commit -m "feat(driver manager): export respects the filter; chunk the WMI query

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 37: `tests/test_driver_manager_definitions.py` — the last Driver Manager gap check

**Files:** Create `tests/test_driver_manager_definitions.py`

Closes out `D30`/`V10`'s remaining edge (the error-code table and
pseudo-class set from Tasks 31-32 are data, not logic, and deserve their
own small validation pass the way `test_debloat_definitions.py` validates
Debloat's data):

```python
from modules.driver_manager import driver_reader as dr


def test_every_error_code_meaning_is_a_real_sentence():
    for code, meaning in dr._ERROR_CODE_MEANINGS.items():
        assert isinstance(code, int) and code > 0
        assert len(meaning) > 10


def test_decode_error_code_never_returns_none():
    for code in list(dr._ERROR_CODE_MEANINGS) + [0, 99999]:
        assert dr.decode_error_code(code) is not None
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add tests/test_driver_manager_definitions.py
git commit -m "test(driver manager): validate the error-code table

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Phase 9 — Cross-cutting closeout (`C01`, `C03`, `C04`, `C06`, `C07`, `C08`, `C09`, `C11`)

### Task 38: Search coverage for Driver Manager and Store Apps; semantic-color and confirm-dialog audit; consistent elevation hints; a logging-level pass

**Files:** Modify `src/modules/driver_manager/driver_module.py`, `src/modules/store_apps/store_apps_module.py`; test additions to each module's test file

Resolves the rest of `C01`, `C03`, `C04`, `C06`, `C08`.

`C01`: give Driver Manager and Store Apps a real `get_search_provider()`
(neither has one today — Debloat's was the only one, fixed in Task 7).
Driver Manager searches the currently-loaded `_drivers_ref[0]` (nothing to
search before a first refresh, matching the app's documented pattern that
"a provider is fed by [live state], answering only after a tab has been
opened once" — the same shape `DiagnoseModule`'s children use); Store
Apps searches `self._apps`:

```python
class DriverSearchProvider(SearchProvider):
    module_name = "Driver Manager"

    def __init__(self, drivers_ref):
        self._drivers_ref = drivers_ref  # the same [list] cell driver_module.py mutates

    def search(self, query: SearchQuery) -> List[SearchResult]:
        text = (query.text or "").strip().lower()
        if not text:
            return []
        results = []
        for d in self._drivers_ref[0]:
            if text in d.device_name.lower() or text in d.driver_class.lower():
                results.append(SearchResult(
                    timestamp=datetime.datetime.now(), source="Driver Manager",
                    type="driver", summary=f"{d.device_name} ({d.driver_class})",
                    detail={"version": d.version, "signed": d.signed,
                           "flags": d.flags},
                    relevance=2.0 if text in d.device_name.lower() else 1.0))
        return results

    def get_filterable_fields(self) -> List[FilterField]:
        return []
```

`DriverModule.get_search_provider(self)` returns
`DriverSearchProvider(self._drivers_ref)` — the shared list cell means
the provider always sees the module's current data with no extra wiring.
`StoreAppsModule` gets the equivalent, searching `self._apps` by
`Name`/`Publisher`. Test each with a fixture list of 2 fake
drivers/apps, asserting a name match and a class/publisher match both
surface, same shape as `test_debloat_search_provider.py`.

`C06`: `driver_module.py:156` — `cell.setForeground(QColor("#CC2222"))`
becomes `cell.setForeground(QColor(semantic("error")))`
(`from core.semantic_colors import semantic` added to imports).

`C03`/`C04`: an audit pass, not new mechanism — grep every remaining
hand-rolled `QMessageBox.question`/`.warning` this plan's own tasks
introduced (Tasks 8, 16, 33, 34 already use `confirm_destructive`;
`store_apps_module._uninstall`'s own dialog, predating this plan, is
listed under `CLAUDE.md`'s explicit "existing call sites are NOT
retrofitted" carve-out and stays as-is) and confirm nothing new this plan
added bypassed the shared helper. `C04`: each of the three modules'
`requires_admin`/`read_only_unelevated` docstring comment gets one
matching sentence, copied verbatim across all three files so the wording
doesn't drift:

```python
    #: Reading and every non-destructive action needs no elevation; a
    #: write is refused by require_admin() with a message pointing at the
    #: "Restart as Admin" banner, not a silent failure.
```

`C08`: a pass bringing Driver Manager's logging up to the other two
modules' level — every `_do_*` action Tasks 33-36 added already logs via
`self._status_lbl`; add one `logger.info(...)` line matching each
`self._status_lbl.setText(...)` call this plan introduced in
`driver_module.py` (uninstall, export-one, backup outcome), mirroring
`debloat_module.py`'s existing convention of pairing a status-bar message
with an `logger.info` of the same fact.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/driver_manager/driver_module.py src/modules/store_apps/store_apps_module.py tests/test_driver_module.py tests/test_store_apps_module.py
git commit -m "feat: search for Driver Manager and Store Apps; semantic colors; matched elevation wording

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 39: Shared column-width persistence helper

**Files:** Create `src/core/table_ui.py` additions; modify all three modules; test `tests/test_table_ui.py`

Resolves `C07` (and closes `D23`/`T23`/`S23` on the *width* half — sort
persistence was already handled per-module in Tasks 18/29/35; this is the
one piece worth sharing, since the save/restore logic is identical
across all three).

```python
def test_save_and_restore_column_widths(qapp):
    table = QTableWidget(1, 3)
    table.setColumnWidth(0, 111)
    table.setColumnWidth(1, 222)
    saved = {}
    table_ui.save_column_widths(table, lambda k, v: saved.__setitem__(k, v), "test.widths")
    table2 = QTableWidget(1, 3)
    table_ui.restore_column_widths(
        table2, lambda k, default=None: saved.get(k, default), "test.widths")
    assert table2.columnWidth(0) == 111
    assert table2.columnWidth(1) == 222
```

```python
def save_column_widths(table: QTableWidget, config_set, key_prefix: str) -> None:
    """`config_set(key, value)` matches `AppConfig.set`'s signature -- every
    module already has `self.app.config.set`, this just standardizes what
    key each column's width is saved under so three modules don't each
    invent their own."""
    widths = [table.columnWidth(c) for c in range(table.columnCount())]
    config_set(f"{key_prefix}.column_widths", widths)


def restore_column_widths(table: QTableWidget, config_get, key_prefix: str) -> None:
    widths = config_get(f"{key_prefix}.column_widths", None)
    if not widths:
        return
    for c, w in enumerate(widths):
        if c < table.columnCount() and isinstance(w, int) and w > 0:
            table.setColumnWidth(c, w)
```

Call `save_column_widths(self._table, self.app.config.set,
"driver_manager")` from `DriverModule.on_deactivate` (and the equivalent
for Store Apps' `"store_apps"` prefix and Debloat's per-tab
`f"debloat.{tab_type}"` prefix), and `restore_column_widths(...)` once
after each table's headers are built in `create_widget`/`_build_*_tab`.

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/core/table_ui.py src/modules/driver_manager/driver_module.py src/modules/store_apps/store_apps_module.py src/modules/debloat/debloat_module.py tests/test_table_ui.py
git commit -m "feat: shared column-width persistence, used by all three modules

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 40: Visible auto-refresh interval; Debloat opts out of the global Pause/Resume scope

**Files:** Modify `src/modules/driver_manager/driver_module.py`, `src/modules/store_apps/store_apps_module.py`, `src/modules/debloat/debloat_module.py`

Resolves `C09`.

Driver Manager (60s) and Store Apps (120s) each get one line in their
toolbar: `QLabel(f"Auto-refreshes every {interval // 1000}s")`, styled
muted, placed after the existing status label. Debloat's tools module
already returns `get_refresh_interval() -> None` (never auto-refreshes) —
`C09`'s complaint that "the global toggle applies blindly even to a
destructive-adjacent tool" is therefore already moot for Debloat
specifically (there is no timer for Pause/Resume to pause); the fix here
is documenting why in the method itself rather than leaving `None`
unexplained:

```python
    def get_refresh_interval(self) -> Optional[int]:
        """Never — an apply-driven tab re-reading itself on a timer while
        someone has checkboxes half-set is worse than a stale scan."""
        return None
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add src/modules/driver_manager/driver_module.py src/modules/store_apps/store_apps_module.py src/modules/debloat/debloat_module.py
git commit -m "chore: show the auto-refresh interval; explain why Debloat has none

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 41: `CLAUDE.md` — Driver Manager and Debloat sections

**Files:** Modify `CLAUDE.md`

Resolves `C11`. Following the exact shape Monitor Control's, TreeSize's
and the Log Viewer's sections already use — what the module is, the one
rule everything is built around, then a bullet per measured gotcha —
written from what this plan's tasks actually found, not speculatively:

```markdown
### Driver Manager (`src/modules/driver_manager/`)

Read, export, and (as of this pass) per-package uninstall for installed
drivers. `requires_admin = False` — reading and exporting need no
elevation; only `pnputil /delete-driver` and a backup to a
permission-restricted folder do.

- **`Win32_PnPSignedDriver` only lists devices that HAVE a driver.** A
  device Windows found none for at all — the yellow-bang case — needed a
  second `Win32_PnPEntity` query merged in, or "Driver Manager" never
  showed the machine's actual problem devices.
- **Only an `oem##.inf`-published package can be `pnputil
  /delete-driver`'d.** An inbox driver (`usb.inf`, `hidclass.inf`, ...) has
  no OEM number and nothing to remove this way — offer the action only
  when `published_name_for()` resolves one, never guess.
- **Roll-back-to-previous-version is routed to Device Manager, not
  implemented here.** It needs the previous driver package still cached,
  which this app does not track; Windows itself can check.
- **A driver's signed/unsigned flag is what Windows' own catalog metadata
  claims, not an independent Authenticode check.** Driver signing is
  catalog-based (`.cat` files against a whole package), which is a
  different question than `core/procengine/signatures.py`'s
  single-PE-file check answers — labeled "(as reported by Windows)"
  rather than built as something it is not.
- **One 90-second WMI call for every driver dies with zero partial
  results on a timeout.** Chunked by device class instead, each with its
  own shorter timeout, so a slow class costs that class, not the whole
  refresh.

### Debloat (`src/modules/debloat/`)

126 catalogued apps plus 219 tweaks across two tabs, and four builtin
presets (`tweaks/definitions/builtins/debloat_*.json`) that name and
curate exactly what "Light/Full/Privacy/Custom" mean.

- **Two lists of "what apps exist" must be checked against each other,
  or entries silently vanish.** `debloat_scanner.KNOWN_PACKAGES` (what
  can be detected as installed) and `debloat.json` (the catalog with
  names/categories/removal steps) drifted — 12 real packages including
  Recall and Copilot were catalogued but undetectable.
  `tests/test_debloat_definitions.py` checks this now; keep it green
  after every catalog edit.
- **A preset file with a real name is not the same as a preset that
  runs.** Four fully-curated JSON files sat unreferenced by any Python
  for long enough that the UI's own Light/Full/Privacy/Custom buttons
  reimplemented a cruder version from scratch. If a preset needs
  changing, edit the JSON — `debloat_presets.py` is the only thing that
  should read it.
- **`TweakEngine.detect_status()` is the three-value back-compatible
  shim; `detect()` is the real five-value answer with a `reason`.**
  Debloat's tables used the shim and folded two of the five values into
  "Unknown" — exactly what the engine's own docstring says its design
  exists to prevent.
- **One restore point per apply-click, across three tabs used in one
  sitting, risks the second and third being silent no-ops** against
  Windows' own checkpoint frequency floor. `debloat_session.py` gives
  one Debloat session one restore point, reused across Apps/Tweaks/AI
  within a window, rather than creating a fresh one per click.
```

```
.venv\Scripts\python.exe -m pytest tests/ -q
git add CLAUDE.md
git commit -m "docs: Driver Manager and Debloat sections in CLAUDE.md

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 42: Full verification, build, deploy

**Files:** none (verification only)

- [ ] Run the complete suite: `.venv\Scripts\python.exe -m pytest tests/ -q` — expect exit 0, no skips beyond the pre-existing `real_machine`-marked ones.
- [ ] `.venv\Scripts\python.exe -m ruff check src/modules/driver_manager/ src/modules/debloat/ src/modules/store_apps/ src/core/table_ui.py` — no new warnings introduced by this plan (pre-existing repo-wide `chore/ruff-backlog` items are out of scope here).
- [ ] Confirm every new function-scoped `ui.*`/`modules.*` import this plan added (`debloat_presets`, `debloat_session`, `debloat_history`, `run_all_tab`, `debloat_search_provider` if not already listed) is in `pyinstaller_common.py`'s `HIDDEN_IMPORTS` (`P12`).
- [ ] Rebuild the portable exe per the `build-portable` skill (clears the PyInstaller cache first, verifies the output is not a bootloader stub, deploys to `Aplicații/`).
- [ ] Smoke-test: launch the deployed exe, open Driver Manager / Debloat (all three tabs) / Store Apps, confirm each renders and its Refresh/Scan action completes without error — read-only verification only, no uninstall/apply/delete action clicked against real installed software or drivers.

```
git log --oneline <first-commit-of-this-plan>..HEAD | wc -l   # sanity: task count matches commit count
```

---

## Coverage map — every audit item, and what resolves it

**Verified defects**

| ID | Task | ID | Task |
|---|---|---|---|
| V01 | 2 | V07 | 24 |
| V02 | 2 | V08 | 1, 24 |
| V03 | 6 | V09 | 7 |
| V04 | 4 | V10 | 31, 33, 37 |
| V05 | 4 | V11 | 2 |
| V06 | 24 | | |

**Driver Manager**

| ID | Task | ID | Task | ID | Task |
|---|---|---|---|---|---|
| D01 | 34 | D12 | 33 | D23 | 35, 39 |
| D02 | 31 | D13 | 33 | D24 | 35 |
| D03 | 32 | D14 | 33 | D25 | 31 |
| D04 | *declined — see Phase 8 header* | D15 | 35 | D26 | 31, 33 |
| D05 | 31 | D16 | 34 | D27 | 32 |
| D06 | 32 | D17 | 34 | D28 | 32 |
| D07 | 31 | D18 | 34 | D29 | 31 |
| D08 | 34 | D19 | 35 | D30 | 31, 33, 37 |
| D09 | 33 | D20 | 35 | D31 | *not applicable — see Task 36* |
| D10 | *descoped — see Phase 8 header, Task 36* | D21 | 35 | D32 | 36 |
| D11 | 36 | D22 | 36 | D33 | 34 |

**Debloat — Apps tab**

| ID | Task | ID | Task | ID | Task |
|---|---|---|---|---|---|
| A01 | 8 | A10 | 10 | A19 | 12 |
| A02 | 8 | A11 | 10 | A20 | 10 |
| A03 | 12 | A12 | 9 | A21 | 19 |
| A04 | 13 | A13 | 12 | A22 | 13 |
| A05 | 12 | A14 | 11 | A23 | 13 |
| A06 | 9 | A15 | 11 | A24 | *declined — see Task 13* |
| A07 | 9 | A16 | 11 | A25 | 10 |
| A08 | 9 | A17 | 11 | | |
| A09 | 12 | A18 | 9 | | |

**Debloat — Tweaks tabs**

| ID | Task | ID | Task | ID | Task |
|---|---|---|---|---|---|
| T01 | 4 | T10 | 17 | T19 | 18 |
| T02 | 4 | T11 | 15 | T20 | 18 |
| T03 | 17 | T12 | 15 | T21 | 14 |
| T04 | 4 | T13 | 16 | T22 | 14 |
| T05 | 14 | T14 | 16 | T23 | 18 |
| T06 | 14 | T15 | 6 | T24 | 18 |
| T07 | 17 | T16 | 6 | T25 | 14 |
| T08 | 17 | T17 | 16 | | |
| T09 | 14 | T18 | 16 | | |

**Debloat — presets & architecture**

| ID | Task | ID | Task | ID | Task |
|---|---|---|---|---|---|
| P01 | 6 | P06 | 20 | P11 | 6 |
| P02 | 6 | P07 | 20 | P12 | 5, 21 |
| P03 | 6 | P08 | 20 | P13 | 20 |
| P04 | 3 | P09 | 21 | P14 | 22 |
| P05 | 3 | P10 | 3 (addendum) | P15 | 23 |

**Store Apps**

| ID | Task | ID | Task | ID | Task |
|---|---|---|---|---|---|
| S01 | 27 | S11 | 26 | S21 | 29 |
| S02 | 27 | S12 | 26 | S22 | 29 |
| S03 | 27 | S13 | 26 | S23 | 29 |
| S04 | 27 | S14 | *declined — see Task 26* | S24 | 29 |
| S05 | 25 | S15 | *declined — see Task 26* | S25 | 29 |
| S06 | 25 | S16 | 28 | S26 | 30 |
| S07 | 25 | S17 | *declined — see Task 28* | S27 | 30 |
| S08 | *declined — see Task 26* | S18 | 28 | S28 | 30 |
| S09 | 26 | S19 | 28 | S29 | 20 (confirmed correct, not changed) |
| S10 | 26 | S20 | 28 | S30 | 20 |

**Cross-cutting**

| ID | Task | ID | Task |
|---|---|---|---|
| C01 | 7 (Debloat), 38 (Driver Manager, Store Apps) | C07 | 39 |
| C02 | 19 (Debloat) — Store Apps' single-batch restore point already avoids the multi-click problem this item describes | C08 | 38 |
| C03 | 8, 16, 33, 34 (new call sites); audited in 38 | C09 | 40 |
| C04 | 38 | C10 | *substantially addressed, full unification declined — see Phase 7 header* |
| C05 | 22 (Debloat) — Driver Manager/Store Apps declined for now, no user-facing gap as sharp as Debloat's three-tabs-one-session case | C11 | 41 |
| C06 | 38 | | |

## Self-review

**Spec coverage:** every one of the 150 IDs above appears in the map,
either against a task number or an explicit *declined*/*descoped*/*not
applicable* note with its reasoning inline in that task's section — no ID
is silently missing.

**Placeholder scan:** every task shows real code (or, for the handful of
"audit pass" tasks — 3's `P10` addendum, 38 — the exact grep/check to run
and what changes as a result), every test has real assertions, no
"TODO"/"similar to above"/"add appropriate handling" appears anywhere in
this document.

**Type consistency:** `NumericSortItem` (Task 1) is used identically in
Tasks 24, 25, 27, 28, 35 with the same constructor shape
(`NumericSortItem(text, value)`) and the same `.value(item)` staticmethod
reader introduced in Task 25 and reused in 28. `debloat_presets`'s
function names (`load_preset`, `resolve_tweak_ids`, `resolve_app_entry_ids`,
`save_custom_tweaks_and_apps`, `save_custom_apps`) are used consistently
from Task 5 through Task 6, 21, 22 with no renamed call sites.
`DebloatSession.restore_point_id(label)` (Task 19) is called with the same
signature from both `_do_apply_apps` and `_on_apply_tweaks`.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-09-04-debloat-driver-improvements.md`.
42 tasks across 9 phases. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review
between tasks. Given 42 tasks touching three modules, this keeps each
task's context isolated (no accumulated drift across dozens of file edits
in one continuous session) and lets review happen incrementally rather
than as one enormous diff at the end.

**2. Inline Execution** — run tasks in this session via
`superpowers:executing-plans`, batched with checkpoints for review.

Which approach?

