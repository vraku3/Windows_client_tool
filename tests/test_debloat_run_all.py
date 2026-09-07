from modules.debloat.run_all_tab import run_stage


def test_run_stage_applies_every_resolved_id(monkeypatch):
    applied = []
    fake_engine = type("E", (), {"apply_tweak": lambda self, t, rp: applied.append(t["id"]) or True})()
    result = run_stage(
        preset_name="light", tweaks=[{"id": "a", "category": "Privacy"}],
        catalog={}, engine=fake_engine, rp_id="rp-1",
        resolve_tweak_ids=lambda preset, tw: {"a"},
        resolve_app_entry_ids=lambda preset, cat: set(),
        apply_apps=lambda ids, rp: 0)
    assert applied == ["a"]
    assert result["tweaks_applied"] == 1


def test_run_stage_reports_apps_and_tweaks_separately(monkeypatch):
    result = run_stage(
        preset_name="full", tweaks=[], catalog={},
        engine=type("E", (), {"apply_tweak": lambda self, t, rp: True})(),
        rp_id="rp-1",
        resolve_tweak_ids=lambda preset, tw: set(),
        resolve_app_entry_ids=lambda preset, cat: {"e1", "e2"},
        apply_apps=lambda ids, rp: len(ids))
    assert result == {"tweaks_applied": 0, "apps_applied": 2}
