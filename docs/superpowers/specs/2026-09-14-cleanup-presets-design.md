# Cleanup Preset System (Sub-project 3) — Design

## 0. How this spec was produced

Same disclosure as Sub-project 2's spec (`docs/superpowers/specs/
2026-09-14-cleanup-new-targets-design.md` §0): built under the user's
explicit "keep going ... shut down when done" authorization, without
interactive question-and-answer. Every choice below that would normally
be a question is a ruling, made against this codebase's own established
Debloat preset pattern (`src/modules/debloat/debloat_presets.py`) and
CLAUDE.md's documented safety conventions.

## 1. Overview

The original spec's §7.3: "A preset system (Light/Thorough/Aggressive/
Custom), mirroring Debloat's already-shipped pattern, governing what the
whole module's 'Clean' action includes across all 8 tabs at once."

**Real-code finding that shapes the design**: `QuickCleanupTab`'s
dashboard scan (built in Sub-project 1) already sweeps ~101 categories
that mirror what all 8 individual tabs cover (10 main categories map
1:1 onto the other 7 tabs via `_CATEGORY_TAB_NAMES`, plus ~91 advanced
categories covering the same ground in more depth). Building a SEPARATE
"scan all 8 tabs, then apply a preset filter" orchestration layer would
duplicate that sweep. Instead: **the preset system governs Quick
Cleanup's own dashboard-level bulk clean**, replacing its hardcoded
"only `safety=='safe'`" rule with a per-preset rule. This is the
existing single highest-leverage button (`_do_clean_all_safe`) the
original spec was describing, not a new one.

**Ruling on "Custom"**: Debloat's Custom preset persists a saved
selection to `%APPDATA%`. Cleanup already has 8 tabs' worth of per-item
checkboxes the user can hand-select right now — building a SECOND,
parallel "which categories are in my custom preset" UI would be a
redundant selection mechanism. **Custom here means "whatever is
currently checked across the scanned items"** — i.e. Custom is not a
5th safety-level rule, it's "skip the preset's safety filter entirely
and clean exactly what's ticked," which is already `_ScanTab`'s existing
per-tab behavior. On the Quick Cleanup dashboard specifically (which has
no per-item checkboxes of its own — it works off aggregate categories),
"Custom" degrades to Light behavior with a note, since there's nothing
for it to mean there yet; documented as a known simplification, not
silently glossed over.

## 2. The three real presets (data, not a resolver)

Unlike Debloat's presets (which need to reconcile two catalogs — apps
and tweaks, each keyed differently), a Cleanup preset only ever needs
to answer ONE question per item: **should this item's safety level be
included?** No JSON files, no resolver module — a plain Python constant
is the right amount of engineering for this:

```python
# src/modules/cleanup/cleanup_presets.py

"""Cleanup's three built-in presets (Light/Thorough/Aggressive) plus
"Custom" (== whatever's currently checked, handled by the caller, not
this module). Mirrors the SPIRIT of Debloat's preset system
(debloat_presets.py) -- named, curated selections a fresh user can pick
with one click -- but Cleanup only ever needs one axis (safety level),
not two catalogs to reconcile, so this stays a plain constant rather
than a JSON-plus-resolver module.
"""
from typing import FrozenSet

#: Scanner function names NO preset -- not even Aggressive -- may ever
#: auto-include. Matches this codebase's own established "never
#: pre-selected regardless of confirmation" convention (CLAUDE.md:
#: "Drivers and virtual disks never enter the checkbox tree"; the
#: orphaned-profiles scanner's own safety="danger" + selected=False).
#: Named by scanner function name (matching cs.scan_orphaned_user_profiles
#: etc.), not by safety level -- a name-based exclusion list survives a
#: future scanner accidentally being marked "caution" instead of "danger"
#: (defense in depth, not a substitute for getting the safety level right).
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

    preset_id == "custom" returns [] -- the caller is responsible for
    using its own already-checked-item state instead of calling this at
    all in that case; this function exists for light/thorough/aggressive
    only, and raises ValueError on an unrecognized id (including
    "custom", deliberately, so a caller cannot silently forget the
    special case).
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
        for item in result.items:
            if item.safety in allowed:
                selected.append(item)
    return selected
```

## 3. UI change

`QuickCleanupTab`'s toolbar (`_scan_all_btn`, `_clean_all_btn`,
`_show_adv_btn`) gains one more control: a `QComboBox` named
`_preset_combo`, populated from `cleanup_presets.preset_names()`,
defaulting to `"light"` (so an untouched app behaves EXACTLY as it does
today — no behavior change for anyone who never touches the new
control). `_clean_all_btn`'s label changes from the static "🗑️ Clean All
Safe" to a dynamic "🗑️ Clean ({preset label})", updated whenever the
combo changes.

`_do_clean_all_safe` (already the dashboard's bulk-clean entry point,
built in Sub-project 1) is extended: instead of unconditionally filtering
`item.safety == "safe"`, it reads `self._preset_combo.currentData()` and
calls `cleanup_presets.items_for_preset(preset_id, self._results,
self._id_to_scanner_name)` when the preset isn't `"custom"`; for
`"custom"` it keeps doing exactly what it does today (nothing changes —
today's behavior already IS "whatever's safe," which is the fallback
Light behavior §1 already commits to for Custom on this specific
dashboard).

**Ruling on the confirm dialog**: stays `confirm="always"` regardless of
preset (Global Constraint carried over from Sub-project 1 — the
highest-blast-radius action in the module never skips confirmation), and
the confirm dialog's text names the preset and the real item count/size,
so "Aggressive" cleaning genuinely dangerous-tier items is never
disguised as an ordinary "Clean All Safe" click.

## 4. `_id_to_scanner_name` — new, small lookup

`items_for_preset` needs to map a category id to its real scanner
function's name (to apply `NEVER_INCLUDED`). `QuickCleanupTab.build()`
already constructs `self._scanner_map`/`self._adv_scanner_map` as
`{cid: (fn, label, color)}` — `_id_to_scanner_name` is a one-line
derived dict built once in `build()`:

```python
self._id_to_scanner_name = {
    cid: (fn.__name__ if fn else None)
    for cid, (fn, _label, _color) in
    {**self._scanner_map, **self._adv_scanner_map}.items()
}
```

## 5. Data flow

No new scan mechanism, no new persistence, no new dialog beyond the
existing confirm. The preset is UI-local state (`QComboBox.currentData()`)
that changes what `_do_clean_all_safe` filters on — everything else
(the scan itself, `run_clean_safe`, the confirm dialog, the freed-bytes
signal) is unchanged from Sub-project 1.

## 6. Error handling

`items_for_preset` raising `ValueError` on an unrecognized `preset_id`
is a programmer error (the combo box only ever offers the 4 known ids),
never a user-facing path — no new error UI needed.

## 7. Testing

- `tests/test_cleanup_presets.py` (new): `items_for_preset` for each of
  light/thorough/aggressive against a small fake `results` dict spanning
  all 3 safety levels plus a `NEVER_INCLUDED` scanner, proving: Light
  gets only safe; Thorough gets safe+caution; Aggressive gets all three
  EXCEPT the `NEVER_INCLUDED` scanner's own danger item is excluded even
  under Aggressive; `items_for_preset("custom", ...)` raises `ValueError`
  (proving the caller can't accidentally call it for Custom).
- `tests/test_quick_cleanup_presets_ui.py` (new): the combo box exists
  and defaults to "light"; changing it updates `_clean_all_btn`'s label;
  running `_do_clean_all_safe` under each of Light/Thorough/Aggressive
  against a small real scan result correctly varies which items get
  passed to `run_clean_safe`; Custom preserves today's exact
  `safety=='safe'`-only behavior unchanged (a regression test pinning
  Sub-project 1's existing behavior, not just new-feature coverage).

## 8. Non-goals

- No preset system on the OTHER 7 tabs' own "Clean"/"Clean Selected"
  buttons — those already default to fully-checked-and-user-adjustable
  (per Sub-project 2's C2 fix), which IS their own "custom, all reclaimable
  space", and the original spec's own framing already treats the
  dashboard as the one preset-governed surface.
- No NEW persisted Custom-preset file (unlike Debloat) — see §1's ruling.
- No preset-specific one-click-action bundling (e.g. "Aggressive also
  runs Compact WinSxS") — a preset governs the SCAN-based bulk clean
  only, not the one-click actions panel, which stays exactly as
  Sub-projects 1-2 left it.
