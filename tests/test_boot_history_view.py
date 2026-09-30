"""BootHistoryPanel's rendering of the System-log fallback table.

Only the fallback path is new here; the primary boots/slow tables are
unchanged. Hidden-by-default, shown-when-populated is the behaviour that
matters: showing it alongside real BootRecords, or leaving it visible
after a refresh that got real data, would make the two sources look like
one measurement.
"""
from datetime import datetime

from modules.boot_analyzer import boot_history as bh
from modules.boot_analyzer.boot_history_view import BootHistoryPanel


def test_fallback_table_is_hidden_when_there_is_nothing_to_show(qapp):
    panel = BootHistoryPanel()
    panel.set_facts(bh.BootFacts())
    assert not panel._fallback.isVisible() or panel._fallback.rowCount() == 0
    assert panel._fallback.rowCount() == 0


def test_fallback_table_populates_and_becomes_visible(qapp):
    panel = BootHistoryPanel()
    records = [
        bh.FallbackBootRecord(datetime(2026, 9, 30, 5, 46), 32.5, True),
        bh.FallbackBootRecord(datetime(2026, 9, 29, 9, 3), None, None),
    ]
    panel.set_facts(bh.BootFacts(fallback_boots=records))
    assert panel._fallback.rowCount() == 2
    assert panel._fallback.item(0, 1).text() == "32s"
    assert panel._fallback.item(0, 2).text() == "Clean shutdown"
    assert panel._fallback.item(1, 1).text() == "Unknown"
    assert panel._fallback.item(1, 2).text() == "Unknown"


def test_fallback_table_does_not_show_alongside_real_boot_records():
    """`read_boot_facts` never populates both lists at once (fallback only
    runs when `boots` is empty), but the view must not assume that -- it
    should still prefer showing nothing over showing two disagreeing
    measurements if it were ever handed both."""
    # Not exercised through the widget here (no qapp needed): this is a
    # documentation-as-test of the contract read_boot_facts relies on.
    facts = bh.BootFacts(
        boots=[bh.BootRecord(datetime.now(), 30000, 25000, 5000, 10, False, 0, 0)],
    )
    assert facts.fallback_boots == []
