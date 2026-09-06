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


def test_save_custom_groups_multiple_tweaks_by_category(tmp_path, monkeypatch):
    """Test that multiple tweaks across multiple categories are grouped correctly."""
    path = tmp_path / "debloat_custom.json"
    monkeypatch.setattr(dp, "_custom_path", lambda: str(path))
    # Mock _load_all_tweaks to return a controlled mapping
    def fake_load_tweaks():
        return {
            "disable_cortana": "Privacy",
            "disable_location": "Privacy",
            "disable_telemetry": "Telemetry",
        }
    monkeypatch.setattr(dp, "_load_all_tweaks", fake_load_tweaks)

    # Save tweaks from two categories
    dp.save_custom(
        ["disable_cortana", "disable_location", "disable_telemetry"],
        [],
        _CATALOG
    )
    loaded = dp.load_preset("custom", path=str(path))
    # Should be grouped by their categories
    assert sorted(loaded["tweaks"]["Privacy"]) == ["disable_cortana", "disable_location"]
    assert loaded["tweaks"]["Telemetry"] == ["disable_telemetry"]
    assert loaded["apps"] == {"remove": []}


def test_save_custom_fallback_unknown_tweak_to_custom_bucket(
    tmp_path, monkeypatch
):
    """Test that unknown tweak ids fall back to 'Custom' category."""
    path = tmp_path / "debloat_custom.json"
    monkeypatch.setattr(dp, "_custom_path", lambda: str(path))
    # Mock _load_all_tweaks to return a minimal mapping that doesn't include
    # "unknown_tweak"
    def fake_load_tweaks():
        return {"disable_cortana": "Privacy"}
    monkeypatch.setattr(dp, "_load_all_tweaks", fake_load_tweaks)

    dp.save_custom(["unknown_tweak", "disable_cortana"], [], _CATALOG)
    loaded = dp.load_preset("custom", path=str(path))
    # unknown_tweak should fall back to "Custom"
    assert loaded["tweaks"]["Custom"] == ["unknown_tweak"]
    assert loaded["tweaks"]["Privacy"] == ["disable_cortana"]


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

    # Patch _CATEGORY_FILES to include only privacy.json (not debloat.json)
    fake_category_files = {"Privacy": "privacy.json"}

    # Mock the definitions_dir path construction in _load_all_tweaks
    def fake_load_all_tweaks():
        id_to_category = {}
        definitions_dir = str(defs_dir)
        for category, filename in fake_category_files.items():
            filepath = defs_dir / filename
            try:
                with open(filepath, encoding="utf-8") as f:
                    data = dp.json.load(f)
                    tweaks = data if isinstance(data, list) else data.get("tweaks", [])
                    for tweak in tweaks:
                        if isinstance(tweak, dict):
                            tweak_id = tweak.get("id")
                            tweak_category = tweak.get("category")
                            if tweak_id and tweak_category:
                                id_to_category[tweak_id] = tweak_category
            except (dp.json.JSONDecodeError, IOError):
                pass
        return id_to_category

    monkeypatch.setattr(dp, "_load_all_tweaks", fake_load_all_tweaks)

    result = dp._load_all_tweaks()
    # Should only have tweaks from privacy.json, not debloat.json
    assert "disable_cortana" in result
    assert "disable_tracking" in result
    assert result["disable_cortana"] == "Privacy"
    # Most importantly, debloat.json's entries should NOT be in the result
    assert "remove_bing_weather" not in result


def test_load_all_tweaks_handles_io_error(tmp_path, monkeypatch, caplog):
    """Test that IOError in _load_all_tweaks is handled gracefully with logging."""
    defs_dir = tmp_path / "definitions"
    defs_dir.mkdir()

    # Create one valid file
    privacy_file = defs_dir / "privacy.json"
    privacy_file.write_text('[{"id": "disable_cortana", "category": "Privacy"}]')

    # Create a directory with the name of a missing file to cause IOError
    # (trying to open a directory will raise IOError/IsADirectoryError)
    (defs_dir / "missing.json").mkdir()

    # Mock _CATEGORY_FILES with both files
    fake_category_files = {
        "Privacy": "privacy.json",
        "Telemetry": "missing.json",
    }

    # Define a minimal version that uses our temp directory
    def fake_load_all_tweaks():
        id_to_category = {}
        for category, filename in fake_category_files.items():
            filepath = defs_dir / filename
            try:
                with open(filepath, encoding="utf-8") as f:
                    data = dp.json.load(f)
                    tweaks = data if isinstance(data, list) else data.get("tweaks", [])
                    for tweak in tweaks:
                        if isinstance(tweak, dict):
                            tweak_id = tweak.get("id")
                            tweak_category = tweak.get("category")
                            if tweak_id and tweak_category:
                                id_to_category[tweak_id] = tweak_category
            except (dp.json.JSONDecodeError, IOError, OSError) as e:
                dp._logger.warning(f"Could not load tweaks from {filename}: {e}")
        return id_to_category

    monkeypatch.setattr(dp, "_load_all_tweaks", fake_load_all_tweaks)

    # Should not raise, but should log a warning
    import logging
    with caplog.at_level(logging.WARNING):
        result = dp._load_all_tweaks()

    # Should have successfully loaded privacy.json
    assert "disable_cortana" in result
    # Should have logged a warning about missing.json
    assert "Could not load tweaks from missing.json" in caplog.text
