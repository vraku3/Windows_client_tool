from types import SimpleNamespace

from modules.updates.activity_ledger import record_activity


def test_recent_activity_filters_before_twenty_row_limit_and_reloads(tmp_path, qapp):
    from modules.updates.activity_widget import ActivityWidget
    for i in range(25):
        record_activity(tmp_path, {"id": str(i), "name": "Older App" if i == 0 else "Other",
                                   "outcome": "FAILED" if i == 0 else "UPDATED"})
    widget = ActivityWidget()
    widget.set_app(SimpleNamespace(app_data_dir=tmp_path))
    assert widget._table.rowCount() == 20
    widget._search.setText("Older")
    assert widget._table.rowCount() == 1
    widget._outcome.setCurrentText("Success")
    assert widget._table.rowCount() == 0
    widget._outcome.setCurrentText("Failure")
    assert widget._table.rowCount() == 1
    widget.deleteLater()


def test_package_selection_shows_ten_entries_and_unknown_versions(tmp_path, qapp):
    from modules.updates.updates_module import _AppUpdatesTab
    from modules.updates.winget_updater import AppUpdate
    for i in range(12):
        record_activity(tmp_path, {"id": "Vendor.App", "name": "App", "outcome": "UPDATED_UNVERIFIED"})
    tab = _AppUpdatesTab()
    tab.set_app(SimpleNamespace(app_data_dir=tmp_path))
    tab._updates = [AppUpdate("App", "Vendor.App", "1", "2", "winget")]
    tab._render_table()
    tab._table.selectRow(0)
    assert tab._package_activity._table.rowCount() == 10
    assert tab._package_activity._table.item(0, 1).text() == "Unknown → Unknown"
    tab._table.clearSelection()
    assert tab._package_activity._table.rowCount() == 0
    tab.deleteLater()


def test_package_history_follows_replaced_offer_at_selected_row(tmp_path, qapp):
    from modules.updates.updates_module import _AppUpdatesTab
    from modules.updates.winget_updater import AppUpdate
    for package in ("A", "B"):
        record_activity(tmp_path, {"id": package, "name": package, "outcome": "UPDATED", "version_after": package})
    tab = _AppUpdatesTab()
    tab.set_app(SimpleNamespace(app_data_dir=tmp_path))
    tab._updates = [AppUpdate("A", "A", "1", "2", "winget")]
    tab._render_table()
    tab._table.selectRow(0)
    tab._updates = [AppUpdate("B", "B", "1", "2", "winget")]
    tab._render_table()
    assert tab._package_activity._table.item(0, 1).text() == "Unknown → B"
    tab.deleteLater()
