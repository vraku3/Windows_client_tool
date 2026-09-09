import os

from modules.driver_manager import driver_baselines as db
from modules.driver_manager.driver_reader import DriverInfo


def _driver(name, version="1.0", publisher="V"):
    return DriverInfo(device_name=name, driver_class="Net", version=version,
                      date="2020-01-01", publisher=publisher, signed=True,
                      error_code=0, flags="")


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
