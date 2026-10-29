"""Game Mode settings, protection, profiles, schedules and export/import."""
import pytest

from modules.game_mode.engine import game_config


class Config:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_settings_are_clamped_and_unknown_keys_ignored():
    cfg = Config()
    cfg.set(game_config.SETTINGS_KEY, {"auto_off_minutes": 9999, "close_grace_s": -1,
                                       "priority_boost": "realtime", "bogus": 1})
    settings = game_config.load_settings(cfg)
    assert settings.auto_off_minutes == 24 * 60
    assert settings.close_grace_s == 0.0
    assert settings.priority_boost == "off"


def test_protection_matches_wildcards_and_substrings():
    patterns = ["gamebar.exe", "NVIDIA*", "Razer"]
    assert game_config.is_protected("GameBar.exe", "", patterns)
    assert game_config.is_protected("nvidia share.exe", "", patterns)
    assert game_config.is_protected("x.exe", "Razer Synapse", patterns)
    assert not game_config.is_protected("other.exe", "", patterns)


def test_profiles_round_trip_and_validation():
    cfg = Config()
    game_config.save_profile(cfg, "Raid", {"preset": "max", "close": ["a.exe"]})
    assert game_config.load_profiles(cfg)["Raid"]["preset"] == "max"
    game_config.delete_profile(cfg, "Raid")
    assert game_config.load_profiles(cfg) == {}
    with pytest.raises(ValueError):
        game_config.save_profile(cfg, "", {})


def test_schedules_reject_bad_windows_and_keep_good_ones():
    cfg = Config()
    game_config.save_schedules(cfg, [
        {"days": [0, 1], "start": "20:00", "end": "23:00", "profile": "Raid"},
        {"days": [], "start": "20:00", "end": "23:00"},
        {"days": [0], "start": "23:00", "end": "20:00"},
    ])
    rows = game_config.load_schedules(cfg)
    assert len(rows) == 1 and rows[0]["days"] == [0, 1]


def test_export_import_round_trips_every_section():
    cfg = Config()
    game_config.save_settings(cfg, game_config.GameSettings(auto_off_minutes=90))
    game_config.save_protected(cfg, ["Razer"])
    game_config.save_profile(cfg, "Raid", {"preset": "max"})
    game_config.save_binding(cfg, r"C:\Games\Wow.exe", "Raid")
    game_config.save_schedules(cfg, [{"days": [5], "start": "19:00", "end": "22:00",
                                      "profile": "Raid"}])
    text = game_config.export_settings(cfg)
    fresh = Config()
    result = game_config.import_settings(fresh, text)
    assert result["settings"].auto_off_minutes == 90
    assert result["protected"] == ["Razer"]
    assert result["profiles"]["Raid"]["preset"] == "max"
    assert result["bindings"][r"C:\Games\Wow.exe"] == "Raid"
    assert len(result["schedules"]) == 1


@pytest.mark.parametrize("text", ["{broken", "[]", '{"version": 99}',
                                  '{"version": 1, "profiles": []}'])
def test_import_rejects_malformed_files(text):
    with pytest.raises(ValueError):
        game_config.import_settings(Config(), text)
