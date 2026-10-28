"""The machine-wide operation coordinator disables install actions (#17)."""
from types import SimpleNamespace

from modules.updates.run_all_tab import _RunAllTab
from modules.updates.updates_module import _AppUpdatesTab, _WinUpdatesTab
from modules.updates.winget_updater import AppUpdate
from modules.updates.windows_updater import WindowsUpdate


class Config:
    def get(self, key, default=None):
        return default

    def set(self, key, value):
        pass


def owner_app(tmp_path):
    return SimpleNamespace(config=Config(), app_data_dir=str(tmp_path))


def test_app_tab_install_buttons_follow_the_holder(qapp, tmp_path):
    tab = _AppUpdatesTab()
    tab.set_app(owner_app(tmp_path))
    tab._on_updates([AppUpdate("Foo", "Foo.App", "1", "2", "winget")])
    assert tab._update_sel_btn.isEnabled() and tab._update_all_btn.isEnabled()
    tab.set_maintenance_busy("Game Mode ON")
    assert not tab._update_sel_btn.isEnabled() and not tab._update_all_btn.isEnabled()
    tab.set_maintenance_busy("")
    assert tab._update_sel_btn.isEnabled() and tab._update_all_btn.isEnabled()


def test_windows_tab_install_is_disabled_while_another_operation_runs(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.updates.updates_module.is_reboot_pending", lambda: False)
    tab = _WinUpdatesTab()
    tab.set_app(owner_app(tmp_path))
    tab._on_updates({"updates": [WindowsUpdate("KB1", "Fix", "", 0, "")], "held": [], "excluded": []})
    assert tab._install_btn.isEnabled()
    tab.set_maintenance_busy("Updates install")
    assert not tab._install_btn.isEnabled()
    tab.set_maintenance_busy("")
    assert tab._install_btn.isEnabled()


def test_run_all_button_follows_the_holder(qapp, tmp_path):
    tab = _RunAllTab()
    tab.set_app(owner_app(tmp_path))
    tab.set_maintenance_busy("Unattended maintenance")
    assert not tab._run_btn.isEnabled()
    tab.set_maintenance_busy("")
    assert tab._run_btn.isEnabled()
