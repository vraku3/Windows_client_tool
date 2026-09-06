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
import logging
import os
from typing import Dict, Iterable, List, Set

_logger = logging.getLogger(__name__)

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


def _load_all_tweaks() -> Dict[str, str]:
    """Load all tweaks and return {id: category} mapping."""
    id_to_category = {}
    definitions_dir = os.path.join(os.path.dirname(__file__), "..", "tweaks",
                                   "definitions")

    for filename in os.listdir(definitions_dir):
        if filename.endswith(".json") and not filename.startswith("app_"):
            filepath = os.path.join(definitions_dir, filename)
            try:
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)
                    # Handle both list and dict formats
                    tweaks = data if isinstance(data, list) else data.get("tweaks", [])
                    for tweak in tweaks:
                        if isinstance(tweak, dict):
                            tweak_id = tweak.get("id")
                            category = tweak.get("category")
                            if tweak_id and category:
                                id_to_category[tweak_id] = category
            except (json.JSONDecodeError, IOError) as e:
                _logger.warning(f"Could not load tweaks from {filename}: {e}")

    return id_to_category


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
    # Load tweaks to map ids to categories
    id_to_category = _load_all_tweaks()

    # Group tweak ids by their category
    tweaks_by_category: Dict[str, List[str]] = {}
    for tweak_id in tweak_ids:
        category = id_to_category.get(tweak_id, "Custom")
        tweaks_by_category.setdefault(category, []).append(tweak_id)

    # Sort each category's tweaks and the final dict
    data["tweaks"] = {cat: sorted(ids) for cat, ids in tweaks_by_category.items()}

    with open(_custom_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
