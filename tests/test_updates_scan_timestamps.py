import importlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def engine():
    return importlib.import_module("modules.updates.scan_timestamps")


def test_persistence_preserves_sources_and_last_success(tmp_path):
    tracker = engine()
    assert tracker.load_timestamps(tmp_path) == {}
    tracker.record_scan(tmp_path, "wu", True, 5)
    tracker.record_scan(tmp_path, "winget", True, 12)
    tracker.record_scan(tmp_path, "store", True, 0)
    previous = tracker.load_timestamps(tmp_path)
    tracker.record_scan(tmp_path, "wu", False, 0)
    assert tracker.load_timestamps(tmp_path) == previous
    assert previous["wu"]["count"] == 5
    assert previous["store"]["ok"] is True
    assert json.loads((tmp_path / "updates/scan_timestamps.json").read_text()) == previous


@pytest.mark.parametrize("age, expected", [(0, "just now"), (65, "1 minute ago"),
    (7205, "2 hours ago"), (259205, "3 days ago")])
def test_elapsed(age, expected):
    timestamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    assert engine().elapsed_text(timestamp) == expected


def test_stale_threshold_and_invalid_dates():
    tracker = engine()
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    assert tracker.is_stale(old)
    assert not tracker.is_stale(old, 6)
    assert not tracker.is_stale(datetime.now().isoformat())
    assert tracker.is_stale("broken")


def test_corrupt_file_and_atomic_write_failure(tmp_path, monkeypatch):
    tracker = engine()
    tracker.record_scan(tmp_path, "wu", True, 2)
    previous = tracker.load_timestamps(tmp_path)
    def refused(*args):
        raise OSError("replace refused")
    monkeypatch.setattr(tracker.os, "replace", refused)
    with pytest.raises(OSError):
        tracker.record_scan(tmp_path, "wu", True, 9)
    assert tracker.load_timestamps(tmp_path) == previous
    (tmp_path / "updates/scan_timestamps.json").write_text("broken")
    assert tracker.load_timestamps(tmp_path) == {}


class Config:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        return None


def test_scan_labels_and_config(qapp, tmp_path, monkeypatch):
    from modules.updates import updates_module as ui
    app = SimpleNamespace(app_data_dir=tmp_path, config=Config())
    apps = ui._AppUpdatesTab()
    apps.set_app(app)
    assert apps._scan_status.text() == "Never scanned"
    apps._on_updates([])
    assert "just now" in apps._scan_status.text()
    assert "0 updates found" in apps._scan_status.text()
    data = engine().load_timestamps(tmp_path)
    data["winget"]["ts"] = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    (tmp_path / "updates/scan_timestamps.json").write_text(json.dumps(data))
    apps._scan_status.refresh()
    assert "results may be stale" in apps._scan_status.text()
    assert ui.semantic("warning") in apps._scan_status.styleSheet()
    settings = ui._UpdateSettingsTab()
    monkeypatch.setattr(settings, "_refresh_task_health", lambda: None)
    settings.set_app(app)
    assert settings._stale_hours.value() == 4
    settings._stale_hours.setValue(6)
    assert app.config.get("updates.stale_hours") == 6
    apps._scan_status.refresh()
    assert "results may be stale" not in apps._scan_status.text()
    windows = ui._WinUpdatesTab()
    windows.set_app(app)
    monkeypatch.setattr(ui, "is_reboot_pending", lambda: False)
    windows._on_updates([])
    assert "just now" in windows._scan_status.text()
    assert engine().load_timestamps(tmp_path)["wu"]["count"] == 0


def test_shared_scan_records_store_and_reloads_after_restart(qapp, tmp_path, monkeypatch):
    from modules.updates import updates_module as ui
    from modules.updates.winget_updater import AppUpdate
    monkeypatch.setattr(ui._UpdateSettingsTab, "_refresh_task_health", lambda self: None)
    app = SimpleNamespace(app_data_dir=tmp_path, config=Config())
    module = ui.UpdatesModule()
    module.on_start(app)
    root = module.create_widget()
    updates = [AppUpdate("App", "app", "1", "2", "winget"),
               AppUpdate("Store App", "store", "1", "2", "msstore")]
    module._app_tab._on_updates(updates)
    stored = engine().load_timestamps(tmp_path)
    assert stored["winget"]["count"] == 2
    assert stored["store"]["count"] == 1
    assert "1 updates found" in module._store_scan_status.text()
    assert stored["store"]["ts"] in module._store_scan_status.toolTip()
    restarted = ui._ScanStatusLabel("store")
    restarted.set_app(app)
    assert restarted.text() == module._store_scan_status.text()
    module._settings_tab._stale_hours.setValue(8)
    assert app.config.get("updates.stale_hours") == 8
    root.deleteLater()


def test_timestamp_save_error_keeps_scan_results(qapp, tmp_path, monkeypatch):
    from modules.updates import updates_module as ui
    tab = ui._AppUpdatesTab()
    tab.set_app(SimpleNamespace(app_data_dir=tmp_path, config=Config()))
    def refused(*args):
        raise OSError("disk full")
    monkeypatch.setattr(ui, "record_scan", refused)
    tab._on_updates([])
    assert "could not save" in tab._scan_status.text()
    assert tab._refresh_btn.isEnabled()
    assert tab._updates == []


@pytest.mark.parametrize("source, tab_name, outcomes", [
    ("winget", "_AppUpdatesTab", {}), ("wu", "_WinUpdatesTab", [])])
def test_post_install_scan_refreshes_timestamp(qapp, tmp_path, source, tab_name, outcomes):
    from modules.updates import updates_module as ui
    tracker = engine()
    tracker.record_scan(tmp_path, source, True, 5)
    data = tracker.load_timestamps(tmp_path)
    data[source]["ts"] = "2020-01-01T00:00:00+00:00"
    (tmp_path / "updates/scan_timestamps.json").write_text(json.dumps(data))
    tab = getattr(ui, tab_name)()
    tab.set_app(SimpleNamespace(app_data_dir=tmp_path, config=Config()))
    tab._on_verify_done(outcomes, [])
    assert not tracker.is_stale(tracker.load_timestamps(tmp_path)[source]["ts"])
    assert "just now" in tab._scan_status.text()
