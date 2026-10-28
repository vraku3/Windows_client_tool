"""Search/filters, persistent sorting and "update visible results" (#09/#10)."""
from types import SimpleNamespace

from modules.updates.updates_module import _AppUpdatesTab, _WinUpdatesTab
from modules.updates.winget_updater import AppUpdate
from modules.updates.windows_updater import WindowsUpdate


class Config:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        return True


def make_app(tmp_path):
    return SimpleNamespace(config=Config(), app_data_dir=str(tmp_path))


def app_tab(qapp, tmp_path):
    tab = _AppUpdatesTab()
    tab.set_app(make_app(tmp_path))
    tab._on_updates([AppUpdate("Zeta", "Zeta.App", "1", "2", "winget"),
                     AppUpdate("Alpha", "Alpha.App", "1", "2", "winget"),
                     AppUpdate("Store", "Store.App", "1", "2", "msstore")])
    return tab


def test_source_filter_hides_rows_and_visible_updates_follow_it(qapp, tmp_path):
    tab = app_tab(qapp, tmp_path)
    tab._source_filter.setCurrentIndex(1)          # winget
    visible = [tab._updates[r].winget_id for r in tab._visible_rows()]
    assert visible == ["Zeta.App", "Alpha.App"]
    assert tab._visible_update_ids() == visible
    tab._source_filter.setCurrentIndex(2)          # msstore
    assert tab._visible_update_ids() == ["Store.App"]


def test_search_and_result_filters_combine(qapp, tmp_path):
    tab = app_tab(qapp, tmp_path)
    tab._filter_edit.setText("alpha")
    assert tab._visible_update_ids() == ["Alpha.App"]
    tab._filter_edit.setText("")
    tab._result_filter.setCurrentIndex(1)          # confirmed
    assert tab._visible_update_ids() == []          # no result text yet
    tab._table.item(0, 5).setText("CONFIRMED")
    tab._result_filter.setCurrentIndex(0)
    tab._result_filter.setCurrentIndex(1)
    assert tab._visible_update_ids() == [tab._updates[0].winget_id]


def test_update_visible_only_runs_the_filtered_rows(qapp, tmp_path):
    tab = app_tab(qapp, tmp_path)
    captured = []
    tab._run_updates = captured.append
    tab._source_filter.setCurrentIndex(2)          # msstore
    tab._do_update_visible()
    assert captured == [["Store.App"]]


def test_sorting_is_persistent_and_toggles(qapp, tmp_path):
    tab = app_tab(qapp, tmp_path)
    tab._sort_by(0)
    assert [u.name for u in tab._updates] == ["Alpha", "Store", "Zeta"]
    assert tab.app.config.get("updates.sort_apps") == {"section": 0, "asc": True}
    tab._sort_by(0)
    assert [u.name for u in tab._updates] == ["Zeta", "Store", "Alpha"]
    assert tab.app.config.get("updates.sort_apps") == {"section": 0, "asc": False}
    # A new instance restores the saved order when the next scan arrives.
    other = _AppUpdatesTab()
    other.set_app(tab.app)
    other._on_updates([AppUpdate("Zeta", "Zeta.App", "1", "2", "winget"),
                       AppUpdate("Alpha", "Alpha.App", "1", "2", "winget")])
    assert [u.name for u in other._updates] == ["Zeta", "Alpha"]


def test_windows_filters_and_sorting(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.updates.updates_module.is_reboot_pending", lambda: False)
    tab = _WinUpdatesTab()
    tab.set_app(make_app(tmp_path))
    tab._on_updates({"updates": [
        WindowsUpdate("KB2", "Beta fix", "Security Updates", 100, "2026-10-01",
                      severity="Important", reboot="1"),
        WindowsUpdate("KB1", "Alpha fix", "Drivers", 10, "2026-10-02",
                      severity="Critical", reboot="0"),
    ], "held": [], "excluded": []})
    tab._wu_search.setText("alpha")
    assert [tab._updates[r].kb for r in tab._visible_wu_rows()] == ["KB1"]
    tab._wu_search.setText("")
    tab._wu_severity.setCurrentIndex(tab._wu_severity.findData("critical"))
    assert [tab._updates[r].kb for r in tab._visible_wu_rows()] == ["KB1"]
    tab._wu_severity.setCurrentIndex(0)
    tab._sort_wu_by(4)                              # size, ascending
    assert [u.kb for u in tab._updates] == ["KB1", "KB2"]
    assert [u.kb for u in tab._visible_wu_updates()] == ["KB1", "KB2"]
