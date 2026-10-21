"""History tab v2 (WP9): additive record_report fields, the Qt-free readers,
the path tree (D8), filters, per-category history, trend data and Export HTML."""
import json
import types

import pytest

from modules.cleanup import cleanup_history as ch
from modules.cleanup.cleanup_scanner.deleter import DeleteReport


# -- Qt-free --------------------------------------------------------------------

def _log(tmp_path, monkeypatch, entries):
    path = tmp_path / "log.json"
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(path))
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_record_report_stores_the_new_fields_only_when_given(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    ch.record_report(DeleteReport(freed=10, files=1), [])
    ch.record_report(DeleteReport(freed=20, files=2), [], categories={"system": 15, "dev": 5},
                     preset="light", source="Clean Now")
    old, new = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert not {"categories", "preset", "source"} & set(old)
    assert new["categories"] == {"system": 15, "dev": 5}
    assert new["preset"] == "light" and new["source"] == "Clean Now"
    assert new["bytes"] == 20                                  # the measured total is untouched


def test_malformed_categories_are_dropped_never_a_failed_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "_deleted_path", lambda: str(tmp_path / "log.json"))
    ch.record_report(DeleteReport(freed=5, files=1), [], categories={"system": "lots"})
    (entry,) = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert "categories" not in entry and entry["bytes"] == 5


def test_filters_and_category_matching():
    plain = {"at": "2026-10-01T10:00:00", "bytes": 1}
    cancelled = {"at": "2026-10-02T10:00:00", "bytes": 2, "cancelled": True}
    refused = {"at": "2026-10-03T10:00:00", "bytes": 3, "refused": [["C:\\x", "sign-in"]]}
    in_use = {"at": "2026-10-04T10:00:00", "bytes": 4, "locked_count": 2,
              "categories": {"system": 4}}
    entries = [plain, cancelled, refused, in_use]
    assert [e for e in entries if ch.matches(e, "cancelled")] == [cancelled]
    assert [e for e in entries if ch.matches(e, "refused")] == [refused]
    assert [e for e in entries if ch.matches(e, "in_use")] == [in_use]
    assert [e for e in entries if ch.matches(e, "all", "system")] == [in_use]
    assert ch.freed_of(in_use, "system") == 4 and ch.freed_of(plain, "system") == 0


def test_category_totals_and_order():
    entries = [{"at": "2026-10-01T10:00:00", "categories": {"dev": 5, "system": 1}},
               {"at": "2026-10-05T10:00:00", "categories": {"system": 9}},
               {"at": "2026-10-06T10:00:00"}]
    totals = ch.category_totals(entries)
    assert totals["system"] == {"bytes": 10, "cleans": 2, "last_at": "2026-10-05T10:00:00"}
    assert ch.categories_seen(entries) == ["system", "dev"]


def test_trend_and_scan_points_are_oldest_first_and_skip_unreadable_times():
    entries = [{"at": "2026-10-05T10:00:00", "bytes": 50},
               {"at": "not a time", "bytes": 99},
               {"at": "2026-10-01T10:00:00", "bytes": 10, "categories": {"dev": 7}}]
    pts = ch.trend_points(entries)
    assert [v for _w, v in pts] == [10, 50] and pts[0][0] < pts[1][0]
    assert [v for _w, v in ch.trend_points(entries, "dev")] == [7, 0]
    scans = [{"at": "2026-10-03T00:00:00", "total_size": 300, "categories": {"dev": 30}},
             {"at": "2026-10-02T00:00:00", "total_size": 200}, "junk"]
    assert [v for _w, v in ch.scan_points(scans)] == [200, 300]
    assert [v for _w, v in ch.scan_points(scans, "dev")] == [0, 30]


def test_path_rows_cope_with_old_and_malformed_entries():
    entry = {"largest": [["C:\\a", 5], ["C:\\b"], [7, 1], "bad"],
             "locked": ["C:\\l", 3], "denied": ["C:\\d"],
             "refused": [["C:\\r", "holds an app's sign-in"], "C:\\old"]}
    rows = ch.path_rows(entry)
    assert rows == [("removed", "C:\\a", 5, ""), ("removed", "C:\\b", -1, ""),
                    ("locked", "C:\\l", -1, ""), ("denied", "C:\\d", -1, ""),
                    ("refused", "C:\\r", -1, "holds an app's sign-in"),
                    ("refused", "C:\\old", -1, "refused")]
    assert ch.kind_count({"locked": ["a", "b"]}, "locked") == 2          # old shape
    assert ch.kind_count({"locked": ["a"], "locked_count": 70}, "locked") == 70


def test_top_owner_counts_cleans_not_files():
    entries = [{"owners": {"steam.exe": 100}}, {"owners": {"chrome.exe": 1, "Steam.exe": 1}},
               {"owners": {"chrome.exe": 3}}, {}]
    assert ch.top_owner(entries) == ("chrome.exe", 2)
    assert ch.top_owner([{}]) is None


# -- the tab --------------------------------------------------------------------

class _StubTrend:
    """Stands in for WP2's TrendChart: records what the tab gives it."""

    def __init__(self, *a, **k):
        from PyQt6.QtWidgets import QWidget
        self.widget = QWidget()
        self.calls = []

    def __getattr__(self, name):        # every QWidget method the tab calls
        return getattr(self.widget, name)

    def set_data(self, cleans=(), scans=()):
        self.calls.append((list(cleans), list(scans)))

    def accessible_summary(self):
        return "stub"


def _stub_charts():
    from PyQt6.QtWidgets import QFrame, QVBoxLayout
    made = []

    def trend(*a, **k):
        t = _StubTrend()
        made.append(t)
        return t

    class Card(QFrame):
        def __init__(self, title, chart_widget, caption="", parent=None):
            super().__init__(parent)
            QVBoxLayout(self).addWidget(chart_widget.widget)

    mod = types.SimpleNamespace(TrendChart=trend, ChartCard=Card,
                                TrendPoint=lambda when, value: (when, value))
    return mod, made


@pytest.fixture
def stub_charts(monkeypatch):
    from modules.cleanup.tabs import _history_tab as ht
    mod, made = _stub_charts()
    monkeypatch.setattr(ht, "_charts", lambda: mod)
    monkeypatch.setattr(ht, "_read_summaries",
                        lambda: [{"at": "2026-10-01T09:00:00", "total_size": 900}])
    return made


ENTRIES = [  # newest first is how the log is READ; the file is oldest first
    {"at": "2026-10-01T10:00:00", "count": 3, "bytes": 3000, "locked_count": 0,
     "largest": [["C:\\Temp\\big.tmp", 2000]], "categories": {"system": 3000},
     "source": "Clean Now", "preset": "light"},
    {"at": "2026-10-02T10:00:00", "count": 1, "bytes": 10, "locked_count": 1,
     "locked": ["C:\\T\\held.log"], "owners": {"Teams.exe": 1},
     "refused": [["C:\\P\\LocalCache", "holds an app's sign-in"]], "categories": {"dev": 10}},
    {"at": "2026-10-03T10:00:00", "count": 0, "bytes": 0, "cancelled": True},
]


def _tab(qapp, tmp_path, monkeypatch, entries=ENTRIES):
    _log(tmp_path, monkeypatch, entries)
    from modules.cleanup.tabs._history_tab import _HistoryTab
    tab = _HistoryTab()
    tab.refresh()
    return tab


def _child_with(tab, path):
    root = tab.paths.invisibleRootItem()
    for g in range(root.childCount()):
        group = root.child(g)
        for c in range(group.childCount()):
            if group.child(c).text(0) == path:
                return group, group.child(c)
    raise AssertionError(path)


def test_buttons_act_only_on_a_selected_path_row(qapp, tmp_path, monkeypatch, stub_charts):
    tab = _tab(qapp, tmp_path, monkeypatch)
    tab.table.selectRow(1)                                   # the 2026-10-02 clean
    assert not tab.open_btn.isEnabled() and not tab.copy_btn.isEnabled()
    assert not tab.path_hint.isHidden()
    group, child = _child_with(tab, "C:\\T\\held.log")
    tab.paths.setCurrentItem(group)                          # a group row is not a path
    assert not tab.copy_btn.isEnabled()
    tab.paths.setCurrentItem(child)
    assert tab.open_btn.isEnabled() and tab.copy_btn.isEnabled()
    tab._copy_path()
    from PyQt6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == "C:\\T\\held.log"
    assert "Held by Teams.exe (1)" in group.text(3)


def test_the_tree_groups_paths_by_what_happened_with_exact_counts(qapp, tmp_path, monkeypatch, stub_charts):
    entry = {"at": "2026-10-02T10:00:00", "count": 900, "bytes": 1,
             "largest": [["C:\\a", 1]], "locked": ["C:\\l"], "locked_count": 70,
             "denied": ["C:\\d"], "refused": ["C:\\r"]}
    tab = _tab(qapp, tmp_path, monkeypatch, [entry])
    tab.table.selectRow(0)
    groups = [tab.paths.topLevelItem(i).text(0) for i in range(tab.paths.topLevelItemCount())]
    assert groups == ["Removed (900, largest 1 listed)", "In use (70, first 1 listed)",
                      "Access denied (1)", "Refused (1)"]
    _g, child = _child_with(tab, "C:\\r")
    assert child.text(3) == "refused" and "on purpose" in child.toolTip(0)


def test_filters_and_category_narrow_the_table(qapp, tmp_path, monkeypatch, stub_charts):
    tab = _tab(qapp, tmp_path, monkeypatch)
    assert tab.table.rowCount() == 3
    for key, rows in (("cancelled", 1), ("refused", 1), ("in_use", 1), ("all", 3)):
        tab.filter.setCurrentIndex(tab.filter.findData(key))
        assert tab.table.rowCount() == rows, key
    assert not tab.category.isHidden()
    assert [tab.category.itemData(i) for i in range(tab.category.count())] == [None, "system", "dev"]
    tab.category.setCurrentIndex(tab.category.findData("dev"))
    assert tab.table.rowCount() == 1 and tab.table.item(0, 1).text() == "10 B"
    assert "1 of" not in tab.status.text() and "showing 1" in tab.status.text()
    tab.filter.setCurrentIndex(tab.filter.findData("cancelled"))
    assert tab.table.rowCount() == 0 and tab.stack.currentIndex() == 1
    assert "No clean matches" in tab.empty.text()


def test_sorting_keeps_each_row_on_its_own_clean(qapp, tmp_path, monkeypatch, stub_charts):
    from PyQt6.QtCore import Qt
    tab = _tab(qapp, tmp_path, monkeypatch)
    tab.table.sortItems(1, Qt.SortOrder.AscendingOrder)       # Freed: 0 B, 10 B, 2.9 KB
    assert [tab.table.item(r, 1).text() for r in range(3)][0] == "0 B"
    assert tab.table.item(2, 0).text().startswith("2026-10-01")
    tab.table.selectRow(2)
    assert "C:\\Temp\\big.tmp" in tab.detail.toPlainText()
    assert "Run from: Clean Now (Light)" in tab.detail.toPlainText()


def test_trend_gets_cleans_and_scans_and_follows_the_category(qapp, tmp_path, monkeypatch, stub_charts):
    tab = _tab(qapp, tmp_path, monkeypatch)
    trend = stub_charts[0]
    cleans, scans = trend.calls[-1]
    assert [v for _w, v in cleans] == [3000, 10, 0] and [v for _w, v in scans] == [900]
    tab.category.setCurrentIndex(tab.category.findData("system"))
    cleans, scans = trend.calls[-1]
    assert [v for _w, v in cleans] == [3000] and [v for _w, v in scans] == [0]


def test_without_the_charts_module_the_tab_still_works(qapp, tmp_path, monkeypatch):
    from modules.cleanup.tabs import _history_tab as ht
    monkeypatch.setattr(ht, "_charts", lambda: None)
    monkeypatch.setattr(ht, "_read_summaries", lambda: [])
    tab = _tab(qapp, tmp_path, monkeypatch)
    assert tab.trend is None and tab.table.rowCount() == 3


def test_export_html_writes_oldest_first_and_opens_it(qapp, tmp_path, monkeypatch, stub_charts):
    import os
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    tab = _tab(qapp, tmp_path, monkeypatch)
    tab._export_html()
    assert len(opened) == 1 and opened[0].endswith(".html")
    html = open(opened[0], encoding="utf-8").read()
    assert html.index("2026-10-01") < html.index("2026-10-03")
    assert "<script" not in html.lower()
    assert tab.status.objectName() == "statusSuccess"


def test_an_export_that_cannot_write_says_so(qapp, tmp_path, monkeypatch, stub_charts):
    from modules.cleanup import cleanup_reports

    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(cleanup_reports, "export_history", boom)
    tab = _tab(qapp, tmp_path, monkeypatch)
    tab._export_html()
    assert "Could not write the report" in tab.status.text()
    assert tab.status.objectName() == "statusError"


def test_old_shape_entries_render(qapp, tmp_path, monkeypatch, stub_charts):
    old = [{"at": "2026-09-01T08:00:00", "count": 4, "bytes": 4096,
            "largest": [["C:\\old\\x", 4096]], "refused": ["C:\\old\\r"]},
           {"freed_bytes": 7}]                                  # no time at all
    tab = _tab(qapp, tmp_path, monkeypatch, old)
    assert tab.table.rowCount() == 2
    assert tab.category.isHidden()                              # no category ever recorded
    texts = {tab.table.item(r, 0).text() for r in range(2)}
    assert "Unknown time" in texts


def test_every_control_has_a_tooltip(qapp, tmp_path, monkeypatch, stub_charts):
    tab = _tab(qapp, tmp_path, monkeypatch)
    for w in (tab.filter, tab.category, tab.export_btn, tab.open_btn, tab.copy_btn,
              tab.paths, tab.detail, tab.status, tab.tile_cleans, tab.tile_holder):
        assert w.toolTip(), w
    for col in range(tab.table.columnCount()):
        assert tab.table.horizontalHeaderItem(col).toolTip()
