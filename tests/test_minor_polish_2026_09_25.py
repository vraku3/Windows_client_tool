"""Three small honesty/layout fixes: Restore Size, empty date range, Firewall row."""


def test_restore_table_has_no_placeholder_size_column(qapp):
    from modules.restore_manager.restore_module import RestoreManagerModule
    m = RestoreManagerModule()
    m.app = type("A", (), {"thread_pool": None})()
    w = m.create_widget()
    headers = [m._table.horizontalHeaderItem(i).text() for i in range(m._table.columnCount())]
    assert "Size" not in headers                      # no placeholder column
    assert headers[:3] == ["Name", "Date", "Type"]


def test_empty_log_range_says_any_time_not_year_2000(qapp):
    from modules.log_viewer.log_viewer_module import LogViewerWidget
    w = LogViewerWidget()
    for box in (w.time_from, w.time_to):
        assert box.specialValueText() == "any time"
        assert box.dateTime() == box.minimumDateTime()


def test_firewall_filters_are_on_their_own_row(qapp):
    from modules.firewall_rules.firewall_manager_module import FirewallManagerModule
    m = FirewallManagerModule()
    m.app = type("A", (), {"thread_pool": None})()
    w = m.create_widget()
    w.resize(1100, 600)
    w.show()
    qapp.processEvents()
    assert m._dir_combo.geometry().top() > m._refresh_btn.geometry().bottom() - 2
    assert m._fit_btn.geometry().right() <= w.width()
