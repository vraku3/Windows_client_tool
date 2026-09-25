r"""A third of a real System log showed a blank Message.

Seen 2026-09-25: events whose insert strings are all empty (`['']`, a non-empty
list) produced "" -- there was no fallback for that shape. Also the Time column
was cut to "2026-09-25 ..." by the 100px default width.
"""
from types import SimpleNamespace

from modules.event_viewer import event_reader as er


def _event(inserts):
    return SimpleNamespace(StringInserts=inserts)


def test_inserts_are_used_when_present():
    assert er.event_message(_event(["a", "b"]), "System", 7) == "a | b"


def test_empty_inserts_are_dropped_not_joined_into_separators():
    assert er.event_message(_event(["a", "", "  ", "b"]), "System", 7) == "a | b"


def test_all_empty_inserts_fall_back_to_windows_formatted_text(monkeypatch):
    import types, sys
    fake = types.ModuleType("win32evtlogutil")
    fake.FormatMessage = lambda event, log: "  The  service\r\n started.  "
    monkeypatch.setitem(sys.modules, "win32evtlogutil", fake)
    assert er.event_message(_event([""]), "System", 7) == "The service started."


def test_if_windows_cannot_format_it_the_event_id_is_shown(monkeypatch):
    import types, sys
    fake = types.ModuleType("win32evtlogutil")
    def boom(event, log):
        raise RuntimeError("no message dll")
    fake.FormatMessage = boom
    monkeypatch.setitem(sys.modules, "win32evtlogutil", fake)
    assert er.event_message(_event([""]), "System", 7) == "Event ID 7"
    assert er.event_message(_event(None), "System", 42) == "Event ID 42"


def test_the_time_column_is_wide_enough_for_a_timestamp(qapp):
    from ui.log_table_widget import LogTableWidget
    table = LogTableWidget()
    widths = {name: table._table.columnWidth(i)
              for i, name in enumerate(table._columns)}
    assert widths["Time"] >= 150
