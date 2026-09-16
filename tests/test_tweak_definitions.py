"""Structural checks over every JSON tweak definition.

These files are data, so nothing else catches a typo in them — a bad hive name
or a duplicated id ships silently and shows up as a broken row in the Tweaks
tab. Everything here is cheap and offline: no registry, no services.
"""
import json
import os

import pytest

from modules.tweaks.tweak_engine import _HIVE_MAP, _KIND_MAP, _START_TYPE_MAP
from modules.tweaks.os_context import BUILD_ALIASES

DEFS_DIR = os.path.join("src", "modules", "tweaks", "definitions")

#: app_catalog.json is a different schema (winget app list), not tweaks.
_NOT_TWEAK_FILES = {"app_catalog.json"}

VALID_STEP_TYPES = {
    "registry", "registry_delete", "service", "command", "script", "appx",
    "scheduled_task",
}
VALID_DETECT_TYPES = VALID_STEP_TYPES | {
    "registry_key_exists", "registry_key_absent", "powershell",
    "file_exists", "file_absent", "none",
}
VALID_RISKS = {"low", "medium", "high"}
VALID_APPLIES_KEYS = {
    "min_build", "max_build", "os", "editions", "not_editions", "arch",
    "requires_gpedit", "client_only", "server_only",
}


def _definition_files():
    return sorted(
        f for f in os.listdir(DEFS_DIR)
        if f.endswith(".json") and f not in _NOT_TWEAK_FILES
    )


def _load(filename):
    with open(os.path.join(DEFS_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def _all_tweaks():
    for filename in _definition_files():
        for tweak in _load(filename):
            yield filename, tweak


@pytest.mark.parametrize("filename", _definition_files())
def test_file_is_a_list_of_objects(filename):
    data = _load(filename)
    assert isinstance(data, list), f"{filename} must hold a JSON array"
    assert data, f"{filename} is empty"
    assert all(isinstance(t, dict) for t in data)


def test_ids_are_unique_across_every_file():
    """Two tweaks sharing an id means one row's status overwrites the other's,
    because the UI keys rows by id across all tabs."""
    seen = {}
    duplicates = []
    for filename, tweak in _all_tweaks():
        tid = tweak.get("id")
        if tid in seen:
            duplicates.append(f"{tid} in {seen[tid]} and {filename}")
        seen[tid] = filename
    assert not duplicates, "duplicate tweak ids: " + "; ".join(duplicates)


def test_names_are_unique_within_each_file():
    """Two DIFFERENT tweaks (different ids) can legitimately share a display
    name if they live in different files -- they show up in different Tweaks
    tabs, so nothing on screen is actually ambiguous (14 such cross-file
    pairs already exist in this catalog, e.g. "Show File Extensions" in both
    explorer.json and ui_tweaks.json, and that's fine).

    Two entries in the SAME file sharing a name is the real defect: they
    render in the SAME tab with no way to tell them apart except clicking
    through. This is exactly the bug a task review caught once already (see
    the module-consolidation-design spec) -- disable_background_apps_global
    was given the same name as the pre-existing disable_background_apps,
    both in privacy.json -- caught by a human reading the diff, not by any
    test, because test_ids_are_unique_across_every_file only checks ids."""
    duplicates = []
    for filename in _definition_files():
        seen_names = {}
        for tweak in _load(filename):
            name = tweak.get("name")
            tid = tweak.get("id")
            if name in seen_names:
                duplicates.append(f"{filename}: {tid!r} and {seen_names[name]!r} both named {name!r}")
            seen_names[name] = tid
    assert not duplicates, "tweaks with a duplicate name in the same file/tab:\n  " + "\n  ".join(duplicates)


def test_every_tweak_has_the_required_fields():
    missing = []
    for filename, tweak in _all_tweaks():
        for field in ("id", "name", "description", "category", "risk", "steps"):
            if not tweak.get(field):
                missing.append(f"{filename}:{tweak.get('id', '?')} missing {field}")
    assert not missing, "; ".join(missing[:20])


def test_ids_are_lowercase_snake_case():
    bad = [f"{fn}:{t.get('id')}" for fn, t in _all_tweaks()
           if not str(t.get("id", "")).replace("_", "").isalnum()
           or str(t.get("id", "")) != str(t.get("id", "")).lower()]
    assert not bad, "ids must be lowercase and underscore-separated: " + "; ".join(bad)


def test_risk_values_are_known():
    bad = [f"{fn}:{t['id']}={t.get('risk')}" for fn, t in _all_tweaks()
           if t.get("risk") not in VALID_RISKS]
    assert not bad, "; ".join(bad)


def test_step_types_are_known():
    bad = []
    for filename, tweak in _all_tweaks():
        for step in tweak.get("steps", []):
            if step.get("type") not in VALID_STEP_TYPES:
                bad.append(f"{filename}:{tweak['id']} step type {step.get('type')!r}")
    assert not bad, "; ".join(bad)


def test_registry_steps_are_well_formed():
    bad = []
    for filename, tweak in _all_tweaks():
        for step in tweak.get("steps", []):
            if step.get("type") not in ("registry", "registry_delete"):
                continue
            where = f"{filename}:{tweak['id']}"
            key = step.get("key", "")
            hive = key.split("\\", 1)[0].upper()
            if hive not in _HIVE_MAP:
                bad.append(f"{where} unknown hive {hive!r}")
            if "\\" not in key:
                bad.append(f"{where} key has no subkey path: {key!r}")
            if key.rstrip() != key or "\\\\" in key:
                bad.append(f"{where} malformed key path: {key!r}")
            if step["type"] == "registry":
                if step.get("kind", "DWORD") not in _KIND_MAP:
                    bad.append(f"{where} unknown kind {step.get('kind')!r}")
                if "data" not in step:
                    bad.append(f"{where} registry step has no data")
    assert not bad, "; ".join(bad[:20])


def test_dword_data_is_an_integer():
    """A DWORD written from a JSON string raises at apply time, on the user's
    machine, after a restore point has already been taken."""
    bad = []
    for filename, tweak in _all_tweaks():
        for step in tweak.get("steps", []):
            if step.get("type") != "registry":
                continue
            if step.get("kind", "DWORD") in ("DWORD", "QWORD") and \
                    not isinstance(step.get("data"), int):
                bad.append(f"{filename}:{tweak['id']} data={step.get('data')!r}")
    assert not bad, "; ".join(bad[:20])


def test_sz_data_is_a_string():
    bad = []
    for filename, tweak in _all_tweaks():
        for step in tweak.get("steps", []):
            if step.get("type") != "registry":
                continue
            if step.get("kind") in ("SZ", "EXPAND_SZ") and \
                    not isinstance(step.get("data"), str):
                bad.append(f"{filename}:{tweak['id']} data={step.get('data')!r}")
    assert not bad, "; ".join(bad[:20])


def test_service_steps_name_a_known_start_type():
    bad = []
    for filename, tweak in _all_tweaks():
        for step in tweak.get("steps", []):
            if step.get("type") != "service":
                continue
            if not step.get("name"):
                bad.append(f"{filename}:{tweak['id']} service step has no name")
            start = step.get("start_type")
            if not (isinstance(start, int) or
                    str(start).lower() in _START_TYPE_MAP):
                bad.append(f"{filename}:{tweak['id']} start_type={start!r}")
    assert not bad, "; ".join(bad[:20])


def test_scheduled_task_paths_are_absolute():
    """schtasks resolves a relative name against the root folder, so
    "Microsoft\\Windows\\..." silently misses the task it meant."""
    bad = [f"{fn}:{t['id']} {s.get('task_name')!r}"
           for fn, t in _all_tweaks()
           for s in t.get("steps", [])
           if s.get("type") == "scheduled_task"
           and not str(s.get("task_name", "")).startswith("\\")]
    assert not bad, "; ".join(bad[:20])


def test_detect_blocks_are_well_formed():
    bad = []
    for filename, tweak in _all_tweaks():
        detect = tweak.get("detect")
        if detect is None:
            continue
        probes = detect if isinstance(detect, list) else [detect]
        for probe in probes:
            where = f"{filename}:{tweak['id']}"
            if not isinstance(probe, dict):
                bad.append(f"{where} detect probe is not an object")
                continue
            if probe.get("type") not in VALID_DETECT_TYPES:
                bad.append(f"{where} detect type {probe.get('type')!r}")
            if probe.get("type") == "powershell" and not probe.get("script"):
                bad.append(f"{where} powershell probe has no script")
            if probe.get("type") == "none" and not probe.get("reason"):
                bad.append(f"{where} 'none' probe must explain itself")
    assert not bad, "; ".join(bad[:20])


def test_applies_to_blocks_use_known_keys():
    bad = []
    for filename, tweak in _all_tweaks():
        applies = tweak.get("applies_to")
        if applies is None:
            continue
        where = f"{filename}:{tweak['id']}"
        if not isinstance(applies, dict):
            bad.append(f"{where} applies_to is not an object")
            continue
        for key in applies:
            if key not in VALID_APPLIES_KEYS:
                bad.append(f"{where} unknown applies_to key {key!r}")
        for key in ("min_build", "max_build"):
            spec = applies.get(key)
            if spec is None:
                continue
            if not (isinstance(spec, int) or str(spec).isdigit()
                    or str(spec).upper() in BUILD_ALIASES):
                bad.append(f"{where} unresolvable {key}={spec!r}")
    assert not bad, "; ".join(bad[:20])


def test_command_tweaks_declare_how_they_are_detected():
    """Every command/script tweak needs a `detect` block, otherwise its row
    can only ever say "Unknown" — the thing this whole pass exists to remove."""
    bad = []
    for filename, tweak in _all_tweaks():
        types = {s.get("type") for s in tweak.get("steps", [])}
        if not types & {"command", "script"}:
            continue
        if tweak.get("detect") is None:
            bad.append(f"{filename}:{tweak['id']}")
    assert not bad, ("command/script tweaks with no detect block: "
                     + "; ".join(bad[:20]))


def test_category_matches_the_file_it_lives_in():
    """The Tweaks tab groups by file; a mismatched `category` field shows the
    wrong label in the details panel."""
    expected = {
        "privacy.json": "Privacy", "performance.json": "Performance",
        "telemetry.json": "Telemetry", "ui_tweaks.json": "UI Tweaks",
        "services.json": "Services", "gaming.json": "Gaming",
        "security.json": "Security", "network.json": "Network",
        "ai_features.json": "AI Features", "navigation.json": "Navigation Pane",
        "explorer.json": "Explorer", "taskbar_start.json": "Taskbar & Start",
        "power.json": "Power", "input.json": "Input",
        "updates.json": "Windows Update", "defender.json": "Defender & Firewall",
        "browser.json": "Browsers", "storage.json": "Storage",
        "multimedia.json": "Multimedia", "remote.json": "Remote Access",
    }
    bad = []
    for filename, tweak in _all_tweaks():
        want = expected.get(filename)
        if want and tweak.get("category") != want:
            bad.append(f"{filename}:{tweak['id']} category={tweak.get('category')!r}")
    assert not bad, "; ".join(bad[:20])


def test_descriptions_say_something_useful():
    """A registry tweak's description is the only thing standing between the
    user and an opaque DWORD, so it has to be a real sentence.

    debloat.json is exempt: its rows are "remove this app", the app name
    carries the meaning, and "Camera app." is genuinely all there is to say.
    """
    bad = [f"{fn}:{t['id']}" for fn, t in _all_tweaks()
           if fn != "debloat.json" and len(str(t.get("description", ""))) < 40]
    assert not bad, "descriptions under 40 chars: " + "; ".join(bad[:20])


def test_every_tweak_has_some_description():
    bad = [f"{fn}:{t['id']}" for fn, t in _all_tweaks()
           if len(str(t.get("description", ""))) < 10]
    assert not bad, "; ".join(bad[:20])
