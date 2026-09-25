from modules.hardware_inventory.hardware_module import _fit_height, _make_kv_table, _fill_kv


def test_summary_table_cap_shows_every_row(qapp):
    t = _make_kv_table()
    _fill_kv(t, [("Total", "1"), ("Available", "2"), ("Used", "3")])
    _fit_height(t)
    rows = sum(t.rowHeight(r) for r in range(3))
    assert t.maximumHeight() >= t.horizontalHeader().height() + rows
