"""The WU tab exposes Download and routes driver offers to Driver Manager."""
from types import SimpleNamespace

from core.events import NAV_REQUEST_MODULE
from modules.updates.updates_module import _WinUpdatesTab
from modules.updates.windows_updater import WindowsUpdate


class Config:
    def __init__(self):
        self.values = {
            "updates.wu_include_drivers": True,
            "updates.allow_auto_drivers": False,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        return True


class Bus:
    def __init__(self):
        self.published = []

    def publish(self, topic, data):
        self.published.append((topic, data))


def owner():
    return SimpleNamespace(config=Config(), event_bus=Bus(), app_data_dir=None)


def test_selected_driver_enables_download_and_driver_manager(qapp, monkeypatch):
    monkeypatch.setattr("modules.updates.updates_module.is_reboot_pending", lambda: False)
    tab = _WinUpdatesTab()
    tab.set_app(owner())
    driver = WindowsUpdate("KB1", "Intel - Display - 31.0", "Drivers", 10.0, "2026-10-01",
                           driver=True, driver_class="Display",
                           driver_manufacturer="Intel Corporation")
    quality = WindowsUpdate("KB2", "Security Update", "Security Updates", 10.0, "2026-10-01")
    tab._on_updates({"updates": [driver, quality], "held": [], "excluded": []})
    tab._table.selectRow(0)
    assert tab._selected_wu_updates() == [driver]
    assert tab._download_btn.isEnabled()
    assert tab._drivers_btn.isEnabled()
    tab._do_open_driver_manager()
    topic, data = tab.app.event_bus.published[-1]
    assert topic == NAV_REQUEST_MODULE and data.module_name == "Driver Manager"


def test_a_quality_only_selection_does_not_enable_driver_manager(qapp, monkeypatch):
    monkeypatch.setattr("modules.updates.updates_module.is_reboot_pending", lambda: False)
    tab = _WinUpdatesTab()
    tab.set_app(owner())
    quality = WindowsUpdate("KB2", "Security Update", "Security Updates", 10.0, "2026-10-01")
    tab._on_updates({"updates": [quality], "held": [], "excluded": []})
    tab._table.selectRow(0)
    assert tab._download_btn.isEnabled()
    assert not tab._drivers_btn.isEnabled()
