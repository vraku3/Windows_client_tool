from modules.updates.stage_runners import STAGE_LABELS, STAGE_RUNNERS, normalize_stage_data


def test_stage_runners_has_entry_for_every_label():
    assert set(STAGE_RUNNERS.keys()) == set(STAGE_LABELS.keys())
    assert set(STAGE_RUNNERS.keys()) == {"wu", "winget", "store", "cleanup", "dism", "health"}


def test_normalize_stage_data_empty_input():
    result = normalize_stage_data({})
    assert result == {
        "wu_results": [],
        "wu_installed": 0,
        "winget_results": [],
        "store_triggered": 0,
        "cleanup_freed": 0,
        "cleanup_deleted": 0,
        "dism_output": None,
    }


def test_normalize_stage_data_flattens_all_stages():
    data = {
        "wu": {"results": [{"kb": "KB1", "success": True}], "installed_count": 1},
        "winget": {"results": [{"name": "Foo", "confirmed": True}]},
        "store": {"triggered": 2},
        "cleanup": {"freed": 12345, "deleted": 7},
        "dism": {"output": "some dism text"},
    }
    result = normalize_stage_data(data)
    assert result["wu_results"] == [{"kb": "KB1", "success": True}]
    assert result["wu_installed"] == 1
    assert result["winget_results"] == [{"name": "Foo", "confirmed": True}]
    assert result["store_triggered"] == 2
    assert result["cleanup_freed"] == 12345
    assert result["cleanup_deleted"] == 7
    assert result["dism_output"] == "some dism text"


def test_normalize_stage_data_tolerates_none_stage_values():
    # A stage that raised and was recorded as {} shouldn't blow up normalization.
    data = {"wu": {}, "winget": None, "cleanup": {}}
    result = normalize_stage_data(data)
    assert result["wu_results"] == []
    assert result["winget_results"] == []
    assert result["cleanup_freed"] == 0


def test_run_health_stage_calls_findings_not_servicing(monkeypatch):
    from modules.updates.stage_runners import run_health_stage

    calls = []
    monkeypatch.setattr(
        "modules.system_health.findings.all_findings",
        lambda: calls.append("findings") or [])
    # If run_health_stage ever imports/calls anything from servicing.py,
    # this makes it fail loudly rather than silently running real DISM.
    import modules.system_health.servicing as servicing_module
    def _forbidden(*a, **k):
        raise AssertionError("run_health_stage must never call servicing.py")
    monkeypatch.setattr(servicing_module, "run_scan_health", _forbidden)
    monkeypatch.setattr(servicing_module, "run_component_cleanup", _forbidden)
    monkeypatch.setattr(servicing_module, "run_reset_base", _forbidden)

    class _FakeApp:
        app_data_dir = "."

    import tempfile
    fake_app = _FakeApp()
    fake_app.app_data_dir = tempfile.mkdtemp()

    result = run_health_stage(fake_app, lambda msg: None, lambda: False)

    assert calls == ["findings"]
    assert isinstance(result, dict)


def test_health_is_a_valid_unattended_stage():
    from modules.updates.unattended_runner import VALID_STAGES
    assert "health" in VALID_STAGES


def test_health_stage_is_registered():
    from modules.updates.stage_runners import STAGE_RUNNERS, STAGE_LABELS
    assert "health" in STAGE_RUNNERS
    assert "health" in STAGE_LABELS
