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


@pytest.mark.parametrize("filename", _PRESETS)
def test_preset_tweak_ids_resolve_against_some_known_catalog(filename, catalog):
    """A preset's `tweaks` block names category -> [tweak ids]. Every id it
    names (other than the "*" wildcard) must exist SOMEWHERE this app
    actually loads a tweak from -- but not necessarily the same catalog
    for every preset file.

    debloat_light.json / debloat_full.json address `debloat.json`'s own
    app catalog through "tweaks" (an alternate, redundant pointer to the
    same removals their own "apps" block already names by package name).
    debloat_privacy.json / debloat_custom.json address the six general
    tweak-definition files instead. Both are real; this test accepts
    either rather than assuming one fixed catalog, which is what made it
    wrongly look like light/full's data was broken when it never was.
    """
    with open(os.path.join(_BUILTINS, filename), encoding="utf-8") as f:
        preset = json.load(f)

    debloat_app_ids = {e["id"] for e in catalog}

    all_tweak_ids = set()
    for fname in ("privacy.json", "telemetry.json", "services.json",
                  "network.json", "ai_features.json", "navigation.json"):
        path = os.path.join(_BASE, "tweaks", "definitions", fname)
        with open(path, encoding="utf-8") as f:
            all_tweak_ids.update(t["id"] for t in json.load(f))

    known_ids = debloat_app_ids | all_tweak_ids

    for category, ids in preset.get("tweaks", {}).items():
        for tid in ids:
            if tid == "*":
                continue
            assert tid in known_ids, (
                f"{filename}: tweak id {tid!r} (category {category!r}) "
                f"does not exist in debloat.json's app catalog OR any "
                f"loaded tweak definition file")


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
