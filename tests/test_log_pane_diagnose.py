"""LogPane/LogTableWidget behaviour the Diagnose upgrade relies on."""
from datetime import datetime

from PyQt6.QtCore import Qt

from core.types import LogEntry
from ui.log_pane import LogPane
from ui.log_table_widget import LogTableWidget


def _entry(source, minute, message="m", **raw):
    return LogEntry(timestamp=datetime(2026, 9, 26, 10, minute), source=source, level="Info",
                    message=message, raw=raw)


def test_a_click_after_sorting_selects_the_entry_that_was_clicked(qapp):
    # The old widget looked the row number up in the UNSORTED list, so after a
    # sort every click showed a different event's details.
    table = LogTableWidget()
    entries = [_entry("Charlie", 3), _entry("Alpha", 1), _entry("Bravo", 2)]
    table.set_entries(entries)
    seen = []
    table.row_selected.connect(seen.append)
    table._table.sortByColumn(1, Qt.SortOrder.AscendingOrder)   # Source
    table._table.setCurrentIndex(table._model.index(0, 0))
    assert seen[-1].source == "Alpha"
    table._table.setCurrentIndex(table._model.index(2, 0))
    assert seen[-1].source == "Charlie"


def test_extra_columns_sort_numerically_and_export(qapp, tmp_path):
    table = LogTableWidget(extra_columns=["ID"], extra_values=lambda e: [e.raw["event_id"]])
    table.set_entries([_entry("a", 1, event_id=9), _entry("b", 2, event_id=10), _entry("c", 3, event_id=100)])
    table._table.sortByColumn(3, Qt.SortOrder.AscendingOrder)
    ids = [table._model.item(r, 3).data(Qt.ItemDataRole.EditRole) for r in range(3)]
    assert ids == [9, 10, 100]
    path = tmp_path / "out.csv"
    table.export_csv(str(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Time,Source,Level,ID,Message" and lines[1].endswith(",9,m")


def test_keyboard_navigation_selects_too(qapp):
    table = LogTableWidget()
    table.set_entries([_entry("a", 1), _entry("b", 2)])
    seen = []
    table.row_selected.connect(seen.append)
    table._table.setCurrentIndex(table._model.index(1, 0))   # what an arrow key does
    # (the default order is newest first, so row 1 is the OLDER entry)
    assert [e.source for e in seen] == ["a"]


def test_view_transform_filters_without_losing_what_was_loaded(qapp):
    pane = LogPane(loader=lambda w: [])
    pane.set_entries([_entry("keep", 1), _entry("drop", 2)])
    pane.set_view_transform(lambda entries: [e for e in entries if e.source == "keep"])
    assert pane.row_count() == 1 and len(pane.source_entries()) == 2
    pane.set_view_transform(None)
    assert pane.row_count() == 2


def test_a_filter_matching_nothing_is_an_empty_table_not_the_no_data_page(qapp):
    pane = LogPane(loader=lambda w: [])
    pane.set_entries([_entry("a", 1)])
    pane.set_view_transform(lambda entries: [])
    assert pane.row_count() == 0 and not pane.is_showing_empty_state()


def test_a_failing_transform_falls_back_to_everything(qapp):
    pane = LogPane(loader=lambda w: [])
    pane.set_entries([_entry("a", 1)])
    pane.set_view_transform(lambda entries: 1 / 0)
    assert pane.row_count() == 1


def test_summariser_and_enricher_reach_the_screen(qapp):
    pane = LogPane(loader=lambda w: [], summarizer=lambda es: f"{len(es)} things",
                   detail_enricher=lambda e: "<b>ENRICHED</b>")
    pane._on_result([_entry("a", 1), _entry("b", 2)])
    assert pane.note_text() == "2 things"
    pane._on_row_selected(pane.source_entries()[0])
    assert "ENRICHED" in pane._detail._content.toPlainText()


def test_a_broken_summariser_does_not_break_the_load(qapp):
    pane = LogPane(loader=lambda w: [], summarizer=lambda es: 1 / 0)
    pane._on_result([_entry("a", 1)])
    assert pane.row_count() == 1 and pane.note_text() == ""


def test_detail_panel_escapes_log_text(qapp):
    pane = LogPane(loader=lambda w: [])
    pane.set_entries([_entry("a", 1, message="<b>bold?</b> & <img src=x>", note="<i>x</i>")])
    pane._on_row_selected(pane.source_entries()[0])
    text = pane._detail._content.toPlainText()
    assert "<b>bold?</b>" in text and "<img src=x>" in text and "<i>x</i>" in text
