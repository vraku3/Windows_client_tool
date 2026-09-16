"""Every tweak id a builtin preset names must exist somewhere in the
real tweak catalog, filed under the category key that actually owns it
-- a dead id, or one filed under the wrong key, is a silent no-op when
the preset is applied. The "Performance" preset gets its own dedicated
tests below since it stands in for the retired Performance Tuner module
(see docs/superpowers/specs/2026-09-15-module-consolidation-design.md);
the placement/dead-id checks that follow run over every OTHER
tweak-category builtin preset too -- the same defect Performance's own
audit missed (ids filed under the wrong key, invisible to the tab that
owns them) turned out to also exist, independently, in balanced.json,
corporate_hardened.json and privacy.json, plus a dead `disable_utcsvc`
id (removed from the catalog when disable_diagtrack replaced it, but
never dropped from 5 presets that still named it)."""
import json
import os

import pytest

_DEFS = os.path.join(os.path.dirname(__file__), "..", "src", "modules",
                     "tweaks", "definitions")
_BUILTINS = os.path.join(_DEFS, "builtins")

#: debloat_* presets key on app categories, not tweak categories --
#: _preset_key_to_defining_file() has nothing to check them against.
_TWEAK_CATEGORY_PRESETS = [
    f for f in os.listdir(_BUILTINS)
    if f.endswith(".json") and not f.startswith("debloat_")
]


def _all_real_tweak_ids():
    ids = set()
    for filename in os.listdir(_DEFS):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(_DEFS, filename)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "id" in entry:
                ids.add(entry["id"])
    return ids


def _tweak_id_to_defining_file():
    """Same scan as _all_real_tweak_ids(), but also records which
    definitions file each id actually came from."""
    id_to_file = {}
    for filename in os.listdir(_DEFS):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(_DEFS, filename)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "id" in entry:
                id_to_file[entry["id"]] = filename
    return id_to_file


def _preset_key_to_defining_file():
    """The reverse of tweak_categories.CATEGORY_FILES, keyed the same way
    TweaksModule._on_load_preset derives a preset key from a tab's category
    name: category.lower().replace(" ", "_"), with the literal category
    name itself as a fallback key (this is how "Taskbar & Start" round-trips
    as "taskbar_&_start" while still tolerating a preset author writing the
    exact category name)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from modules.tweaks.tweak_categories import CATEGORY_FILES

    key_to_file = {}
    for category, filename in CATEGORY_FILES.items():
        key_to_file[category.lower().replace(" ", "_")] = filename
        key_to_file[category] = filename
    return key_to_file


@pytest.mark.parametrize("preset_file", _TWEAK_CATEGORY_PRESETS)
def test_every_builtin_preset_id_resolves_to_a_real_tweak(preset_file):
    with open(os.path.join(_BUILTINS, preset_file), encoding="utf-8") as f:
        preset = json.load(f)
    real_ids = _all_real_tweak_ids()
    named = {tid for ids in preset["tweaks"].values() for tid in ids}
    missing = named - real_ids
    assert missing == set(), f"{preset_file} names dead ids: {sorted(missing)}"


def test_the_preset_covers_every_mapped_performance_tuner_check():
    """See the design spec's §2.2 table -- 25 of PerfTuner's 27 checks
    map to a real tweak id; this preset should now name all of them
    (except the two gaming ones, deliberately excluded)."""
    with open(os.path.join(_BUILTINS, "performance.json"), encoding="utf-8") as f:
        preset = json.load(f)
    named = {tid for ids in preset["tweaks"].values() for tid in ids}
    assert "power_disable_hibernate_file" in named
    assert "disable_background_apps_global" in named
    assert "enable_game_mode" not in named
    assert "disable_game_dvr" not in named


@pytest.mark.parametrize("preset_file", _TWEAK_CATEGORY_PRESETS)
def test_every_builtin_preset_id_is_filed_under_its_own_defining_categorys_key(preset_file):
    """It is not enough for an id to exist SOMEWHERE in the catalog and be
    named SOMEWHERE in the preset -- TweaksModule._on_load_preset only
    hands each tab the ids listed under THAT TAB'S OWN key. An id filed
    under the wrong key is silently never applied when the preset loads.
    This pins that every id in every builtin preset lives under the key
    that maps back to the definitions file it is actually defined in --
    the "Performance" preset's own audit missed exactly this for 6 ids
    (see the module docstring), and the same defect turned out to exist
    independently in three other presets, caught only once this check
    ran over all of them rather than just the one it was written for."""
    with open(os.path.join(_BUILTINS, preset_file), encoding="utf-8") as f:
        preset = json.load(f)
    id_to_file = _tweak_id_to_defining_file()
    key_to_file = _preset_key_to_defining_file()

    misfiled = []
    for key, ids in preset["tweaks"].items():
        expected_file = key_to_file.get(key)
        assert expected_file is not None, (
            f"{preset_file}: preset key {key!r} does not map back to any "
            f"known tweak-category definitions file"
        )
        for tid in ids:
            actual_file = id_to_file.get(tid)
            if actual_file is None:
                continue  # covered by test_every_builtin_preset_id_resolves_to_a_real_tweak
            if actual_file != expected_file:
                misfiled.append((tid, key, actual_file))

    assert misfiled == [], (
        f"{preset_file}: preset ids filed under the wrong category key "
        f"(id, wrong_key, actually_defined_in): {misfiled}"
    )
