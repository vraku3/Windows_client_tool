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
