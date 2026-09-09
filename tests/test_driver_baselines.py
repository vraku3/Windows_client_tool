import os

from modules.driver_manager import driver_baselines as db
from modules.driver_manager.driver_reader import DriverInfo


def _driver(name, version="1.0", publisher="V", device_id=""):
    return DriverInfo(device_name=name, driver_class="Net", version=version,
                      date="2020-01-01", publisher=publisher, signed=True,
                      error_code=0, flags="", device_id=device_id)


def test_save_list_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    drivers = [_driver("A"), _driver("B")]
    db.save_baseline("my-baseline", drivers)

    metas = db.list_baselines()
    assert len(metas) == 1
    assert metas[0].name == "my-baseline"
    assert metas[0].driver_count == 2

    loaded = db.load_baseline("my-baseline")
    assert len(loaded) == 2
    assert {d.device_name for d in loaded} == {"A", "B"}


def test_list_baselines_survives_a_corrupt_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    os.makedirs(tmp_path, exist_ok=True)
    with open(os.path.join(str(tmp_path), "broken.meta.json"), "w") as f:
        f.write("{not valid json")
    metas = db.list_baselines()
    assert len(metas) == 1
    assert metas[0].error != ""


def test_diff_against_baseline_finds_added_removed_and_changed():
    baseline = [_driver("Kept Same"), _driver("Removed Device"),
                _driver("Changed Device", version="1.0")]
    current = [_driver("Kept Same"), _driver("Added Device"),
               _driver("Changed Device", version="2.0")]
    diff = db.diff_against_baseline(baseline, current)
    assert [d.device_name for d in diff.added] == ["Added Device"]
    assert [d.device_name for d in diff.removed] == ["Removed Device"]
    assert len(diff.changed) == 1
    old, new = diff.changed[0]
    assert old.version == "1.0" and new.version == "2.0"


def test_load_baseline_returns_none_for_missing_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    assert db.load_baseline("does-not-exist") is None


def test_load_baseline_returns_none_for_a_top_level_dict_instead_of_a_list(tmp_path, monkeypatch):
    """Final-review finding I3 (site B): well-formed JSON of the wrong
    shape raises TypeError out of `[DriverInfo(**d) for d in raw]` --
    that IS "otherwise unreadable" per this function's own docstring, and
    must return None rather than crash."""
    import json
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    os.makedirs(tmp_path, exist_ok=True)
    with open(os.path.join(str(tmp_path), "wrong-shape.json"), "w") as f:
        json.dump({"not": "a list"}, f)
    assert db.load_baseline("wrong-shape") is None


def test_load_baseline_returns_none_for_a_list_of_dicts_with_wrong_keys(tmp_path, monkeypatch):
    """Same TypeError path, a different way to trigger it: a list of dicts
    whose keys don't match DriverInfo's fields."""
    import json
    monkeypatch.setattr(db, "default_baseline_dir", lambda: str(tmp_path))
    os.makedirs(tmp_path, exist_ok=True)
    with open(os.path.join(str(tmp_path), "wrong-keys.json"), "w") as f:
        json.dump([{"wrong": "keys"}], f)
    assert db.load_baseline("wrong-keys") is None


def test_diff_distinguishes_same_named_devices_by_device_id():
    # Two distinct physical devices sharing a generic name, both present
    # unchanged in baseline and current -- must NOT collapse into one
    # entry, and must NOT be paired against each other as "changed".
    baseline = [
        _driver("USB Root Hub", device_id="USB\\ROOT_HUB\\1", version="1.0"),
        _driver("USB Root Hub", device_id="USB\\ROOT_HUB\\2", version="2.0"),
    ]
    current = [
        _driver("USB Root Hub", device_id="USB\\ROOT_HUB\\1", version="1.0"),
        _driver("USB Root Hub", device_id="USB\\ROOT_HUB\\2", version="2.0"),
    ]
    diff = db.diff_against_baseline(baseline, current)
    assert diff.added == []
    assert diff.removed == []
    assert diff.changed == []


def test_diff_reports_only_the_actually_removed_same_named_device():
    baseline = [
        _driver("USB Root Hub", device_id="USB\\ROOT_HUB\\1"),
        _driver("USB Root Hub", device_id="USB\\ROOT_HUB\\2"),
    ]
    # Only the second device is removed; the first, same-named device stays.
    current = [
        _driver("USB Root Hub", device_id="USB\\ROOT_HUB\\1"),
    ]
    diff = db.diff_against_baseline(baseline, current)
    assert len(diff.removed) == 1
    assert diff.removed[0].device_id == "USB\\ROOT_HUB\\2"
    assert diff.added == []
    assert diff.changed == []
