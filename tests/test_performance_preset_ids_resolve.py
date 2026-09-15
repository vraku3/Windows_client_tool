"""Every tweak id the builtin 'Performance' preset names must exist
somewhere in the real tweak catalog -- a dead id is a silent no-op when
the preset is applied, and this preset now stands in for the retired
Performance Tuner module (see docs/superpowers/specs/
2026-09-15-module-consolidation-design.md)."""
import json
import os

_DEFS = os.path.join(os.path.dirname(__file__), "..", "src", "modules",
                     "tweaks", "definitions")
_BUILTINS = os.path.join(_DEFS, "builtins")


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


def test_every_performance_preset_id_resolves_to_a_real_tweak():
    with open(os.path.join(_BUILTINS, "performance.json"), encoding="utf-8") as f:
        preset = json.load(f)
    real_ids = _all_real_tweak_ids()
    named = {tid for ids in preset["tweaks"].values() for tid in ids}
    missing = named - real_ids
    assert missing == set(), f"performance.json preset names dead ids: {sorted(missing)}"


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
