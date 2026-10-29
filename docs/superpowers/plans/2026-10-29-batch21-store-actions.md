# Updates batch 21 - Store tab batch actions

**Goal:** Make the Microsoft Store tab useful for more than one selected app.

## Tasks

1. **Update All** upgrades every msstore offer through the same
   `store_update.update_apps()` path, with one confirmation and the operation
   lock/restore-point handling already used by Update Selected.
2. **Export CSV** writes the listed Store offers with the shared app-offer
   format; **Open in Store** opens Store search for the selected app name.
3. Buttons are gated by the operation lock, an in-flight Store update and the
   current selection/list; help text covers the new actions.

## Constraints and review focus

- Update All still requires an explicit confirmation; it is not an automatic
  run.
- No second winget scan: the list comes from the shared App Updates scan.
- Open in Store uses only the app name, not a guessed product id.
- No widget work from workers.

## Verification

- `test_store_update.py` covers list/selection gating, CSV export and the
  Store-search URL; existing Store/updates regressions and module inventory
  pass; ruff clean.
