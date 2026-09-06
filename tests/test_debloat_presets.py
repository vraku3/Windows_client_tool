import json

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
    dp.save_custom_tweaks_and_apps({"Privacy": ["disable_cortana"]},
                                   {"remove": ["Microsoft.BingWeather"]})
    loaded = dp.load_preset("custom", path=str(path))
    assert loaded["tweaks"] == {"Privacy": ["disable_cortana"]}
    assert loaded["apps"] == {"remove": ["Microsoft.BingWeather"]}


def test_save_custom_apps_merges_with_the_existing_tweaks_selection(
        tmp_path, monkeypatch):
    """save_custom_apps only ever changes the apps half -- whatever tweaks
    selection is already on disk must survive the round trip. `load_preset`
    resolves "custom" via its own default path rather than `_custom_path()`,
    so both are redirected at the tmp file for this test."""
    path = tmp_path / "debloat_custom.json"
    path.write_text(json.dumps(
        {"tweaks": {"Privacy": ["disable_cortana"]}, "apps": {"remove": []}}))
    monkeypatch.setattr(dp, "_custom_path", lambda: str(path))
    monkeypatch.setattr(dp, "load_preset",
                        lambda name: json.loads(path.read_text()))

    dp.save_custom_apps(["remove_bing_weather"], _CATALOG)

    saved = json.loads(path.read_text())
    assert saved["tweaks"] == {"Privacy": ["disable_cortana"]}
    assert saved["apps"] == {"remove": ["Microsoft.BingWeather"]}


def test_load_all_tweaks_uses_only_authoritative_files(tmp_path, monkeypatch):
    """Test that _load_all_tweaks only reads files in _CATEGORY_FILES, not others."""
    # Create a fake definitions directory
    defs_dir = tmp_path / "definitions"
    defs_dir.mkdir()

    # Create a fake privacy.json with real tweaks (list format)
    privacy_file = defs_dir / "privacy.json"
    privacy_file.write_text(
        '[{"id": "disable_cortana", "category": "Privacy"}, '
        '{"id": "disable_tracking", "category": "Privacy"}]'
    )

    # Create a fake debloat.json (app removal catalog, NOT tweaks)
    # that would be picked up by the old filename heuristic
    debloat_file = defs_dir / "debloat.json"
    debloat_file.write_text(
        '[{"id": "remove_bing_weather", "category": "Bing Apps", "steps": []}]'
    )

    # Patch _CATEGORY_FILES in debloat_presets to include only privacy.json
    # (not debloat.json), proving that the real function uses _CATEGORY_FILES,
    # not filesystem scanning
    monkeypatch.setattr(dp, "_CATEGORY_FILES", {"Privacy": "privacy.json"})

    # Call the real _load_all_tweaks() with the temp directory
    result = dp._load_all_tweaks(definitions_dir=str(defs_dir))

    # Should only have tweaks from privacy.json, not debloat.json
    assert "disable_cortana" in result
    assert "disable_tracking" in result
    assert result["disable_cortana"] == "Privacy"
    # Most importantly, debloat.json's entries should NOT be in the result
    # even though it exists in the directory — because it's not in _CATEGORY_FILES
    assert "remove_bing_weather" not in result


def test_load_all_tweaks_handles_io_error(tmp_path, monkeypatch, caplog):
    """Test that IOError in _load_all_tweaks is handled gracefully with logging."""
    defs_dir = tmp_path / "definitions"
    defs_dir.mkdir()

    # Create one valid file
    privacy_file = defs_dir / "privacy.json"
    privacy_file.write_text('[{"id": "disable_cortana", "category": "Privacy"}]')

    # Create a directory with the name of a file that should be a JSON
    # (trying to open a directory will raise IsADirectoryError/IOError)
    (defs_dir / "missing.json").mkdir()

    # Patch _CATEGORY_FILES in debloat_presets to include both a valid file
    # and one that will error, so we don't have to supply all 20 files
    monkeypatch.setattr(
        dp,
        "_CATEGORY_FILES",
        {"Privacy": "privacy.json", "Telemetry": "missing.json"}
    )

    # Call the real _load_all_tweaks() with the temp directory
    # Should not raise, but should log a warning
    import logging
    with caplog.at_level(logging.WARNING):
        result = dp._load_all_tweaks(definitions_dir=str(defs_dir))

    # Should have successfully loaded privacy.json
    assert "disable_cortana" in result
    assert result["disable_cortana"] == "Privacy"
    # Should have logged a warning about missing.json (the directory)
    assert "Could not load tweaks from missing.json" in caplog.text
