import json
import os
import tempfile
import pytest
from core.config_manager import ConfigManager

@pytest.fixture
def config_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

@pytest.fixture
def default_config():
    return {
        "version": 1,
        "app": {"theme": "dark", "window_size": [1400, 900], "log_level": "INFO"},
        "modules": {"enabled": []},
        "search": {"presets": {}},
    }

@pytest.fixture
def manager(config_dir, default_config):
    return ConfigManager(config_dir=config_dir, defaults=default_config)

def test_get_with_dot_notation(manager):
    manager.load()
    assert manager.get("app.theme") == "dark"
    assert manager.get("app.window_size") == [1400, 900]

def test_get_default_value(manager):
    manager.load()
    assert manager.get("nonexistent.key", "fallback") == "fallback"

def test_set_and_get(manager):
    manager.load()
    manager.set("app.theme", "light")
    assert manager.get("app.theme") == "light"

def test_get_module_config(manager):
    manager.load()
    manager.set("modules.process_explorer.refresh_rate", 2000)
    cfg = manager.get_module_config("process_explorer")
    assert cfg["refresh_rate"] == 2000

def test_save_creates_file(manager, config_dir):
    manager.load()
    manager.set("app.theme", "light")
    manager.save()
    config_path = os.path.join(config_dir, "config.json")
    assert os.path.exists(config_path)
    with open(config_path) as f:
        saved = json.load(f)
    assert saved["app"]["theme"] == "light"

def test_save_creates_backup(manager, config_dir):
    manager.load()
    manager.save()
    manager.set("app.theme", "light")
    manager.save()
    backup_path = os.path.join(config_dir, "config.json.bak")
    assert os.path.exists(backup_path)

def test_load_from_existing_file(config_dir, default_config):
    config_path = os.path.join(config_dir, "config.json")
    custom = {**default_config, "app": {**default_config["app"], "theme": "light"}}
    with open(config_path, "w") as f:
        json.dump(custom, f)
    mgr = ConfigManager(config_dir=config_dir, defaults=default_config)
    mgr.load()
    assert mgr.get("app.theme") == "light"

def test_load_corrupt_file_falls_back_to_defaults(config_dir, default_config):
    config_path = os.path.join(config_dir, "config.json")
    with open(config_path, "w") as f:
        f.write("{corrupt json!!!")
    mgr = ConfigManager(config_dir=config_dir, defaults=default_config)
    mgr.load()
    assert mgr.get("app.theme") == "dark"

def test_load_corrupt_file_uses_backup(config_dir, default_config):
    config_path = os.path.join(config_dir, "config.json")
    backup_path = os.path.join(config_dir, "config.json.bak")
    backup = {**default_config, "app": {**default_config["app"], "theme": "custom"}}
    with open(backup_path, "w") as f:
        json.dump(backup, f)
    with open(config_path, "w") as f:
        f.write("corrupt!")
    mgr = ConfigManager(config_dir=config_dir, defaults=default_config)
    mgr.load()
    assert mgr.get("app.theme") == "custom"

def test_reset_to_defaults(manager, default_config):
    manager.load()
    manager.set("app.theme", "light")
    manager.reset_to_defaults()
    assert manager.get("app.theme") == "dark"

def test_version_field_preserved(manager):
    manager.load()
    assert manager.get("version") == 1

def test_migration_v1_to_v2(config_dir, default_config):
    config_path = os.path.join(config_dir, "config.json")
    v1_config = {**default_config, "version": 1}
    with open(config_path, "w") as f:
        json.dump(v1_config, f)
    def migrate_v1_to_v2(data):
        data["app"]["new_field"] = "added_by_migration"
        return data
    mgr = ConfigManager(config_dir=config_dir, defaults=default_config)
    mgr.register_migration(1, migrate_v1_to_v2)
    mgr.load()
    assert mgr.get("version") == 2
    assert mgr.get("app.new_field") == "added_by_migration"


def test_concurrent_saves_never_collide(tmp_path):
    """Game Mode saved from a worker while the UI autosaved: one shared .tmp
    renamed while the other still had it open was "Access is denied"."""
    import json as _json
    import threading as _th
    mgr = ConfigManager(config_dir=str(tmp_path), defaults={"version": 1})
    mgr.load()
    errors = []

    def worker(n):
        for i in range(25):
            mgr.set(f"t.k{n}", i)
            if not mgr.save():
                errors.append(n)
    threads = [_th.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    data = _json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data["t"] == {f"k{n}": 24 for n in range(6)}
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_a_rename_that_is_always_refused_keeps_the_settings(tmp_path, monkeypatch):
    """Measured 2026-10-19: an antivirus refused creating config.json for hours.
    The written settings are kept as config.json.unsaved, load() reads them
    back, and the failure is announced -- never deleted, never silent."""
    import os as _os
    published = []

    class _Bus:
        def publish_async(self, event, data):
            published.append((event, data))

        def publish(self, event, data):
            pass
    mgr = ConfigManager(config_dir=str(tmp_path), defaults={"version": 1}, event_bus=_Bus())
    mgr.load()
    mgr._data["fans"] = 8
    real_replace = _os.replace

    def refuse(src, dst):
        if dst.endswith("config.json"):
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)
    monkeypatch.setattr("core.config_manager.os.replace", refuse)
    monkeypatch.setattr("core.config_manager.time.sleep", lambda s: None)
    assert mgr.save() is False
    assert mgr.last_save_error
    assert published and published[0][0] == "config.save_failed"
    assert (tmp_path / "config.json.unsaved").exists()
    fresh = ConfigManager(config_dir=str(tmp_path), defaults={"version": 1})
    fresh.load()
    assert fresh.get("fans") == 8
