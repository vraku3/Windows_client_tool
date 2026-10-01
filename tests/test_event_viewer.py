from datetime import datetime
from modules.event_viewer.event_search_provider import EventViewerSearchProvider
from core.search_provider import SearchQuery
from core.types import LogEntry


def _make_entries():
    return [
        LogEntry(timestamp=datetime(2026, 3, 25, 10, 0), source="DCOM", level="Error", message="DCOM got error 10016"),
        LogEntry(timestamp=datetime(2026, 3, 25, 11, 0), source="Service Control Manager", level="Warning", message="service timeout"),
        LogEntry(timestamp=datetime(2026, 3, 25, 12, 0), source="Kernel-General", level="Info", message="system time changed"),
    ]


def test_search_provider_text_filter():
    sp = EventViewerSearchProvider()
    sp.set_entries(_make_entries())
    results = sp.search(SearchQuery(text="DCOM"))
    assert len(results) == 1
    assert results[0].source == "DCOM"


def test_search_provider_type_filter():
    sp = EventViewerSearchProvider()
    sp.set_entries(_make_entries())
    results = sp.search(SearchQuery(text="", types=["Error"]))
    assert len(results) == 1
    assert results[0].type == "Error"


def test_search_provider_regex():
    sp = EventViewerSearchProvider()
    sp.set_entries(_make_entries())
    results = sp.search(SearchQuery(text="DCOM.*10016", regex_enabled=True))
    assert len(results) == 1


def test_search_provider_empty_query():
    sp = EventViewerSearchProvider()
    sp.set_entries(_make_entries())
    results = sp.search(SearchQuery(text=""))
    assert len(results) == 3  # All entries match empty query


def test_filterable_fields():
    sp = EventViewerSearchProvider()
    sp.set_entries(_make_entries())
    fields = sp.get_filterable_fields()
    assert len(fields) == 2
    source_field = next(f for f in fields if f.name == "source")
    assert "DCOM" in source_field.values


def test_event_viewer_module_creates_widget(qapp):
    from modules.event_viewer.event_viewer_module import EventViewerModule
    from ui.log_pane import LogPane

    mod = EventViewerModule()
    widget = mod.create_widget()

    # The table used to be the module's own `_table`; it now lives inside the
    # shared LogPane, along with the time-range combo this module adds.
    assert isinstance(widget, LogPane)
    assert widget.row_count() == 0
    assert widget.extra["hours_combo"].currentText() == "24 hours"


def test_event_viewer_wires_up_a_ticket_ready_copy_formatter(qapp):
    # entry_as_text() (log name, computer, record number, the event's own
    # structured data) existed but was never called from anywhere -- "Copy
    # row" fell back to LogTableWidget's generic one-liner, which drops all
    # of that. Event Viewer's pane_options() must hand it to the pane.
    from modules.event_viewer import event_analysis as ea
    from modules.event_viewer.event_viewer_module import EventViewerModule

    mod = EventViewerModule()
    options = mod.pane_options()
    assert options["copy_formatter"] is ea.entry_as_text


def test_copy_row_on_a_real_event_pastes_more_than_the_message(qapp):
    # Real-machine: pull one real System/Application event through the same
    # reader the module uses, and confirm the ticket-ready text actually adds
    # real fields (log name, record number) a plain "time [level] source:
    # message" line does not carry.
    from modules.event_viewer import event_analysis as ea
    from modules.event_viewer.event_reader import read_logs

    entries, _notes = read_logs(hours_back=24, max_events_per_log=50, include_security=False)
    if not entries:
        import pytest
        pytest.skip("no events in the last 24 hours on this machine")
    entry = entries[0]
    generic = f"{entry.timestamp} [{entry.level}] {entry.source}: {entry.message}"
    ticket_text = ea.entry_as_text(entry)
    assert ticket_text != generic
    assert entry.raw.get("log_name", "") in ticket_text
    assert str(entry.raw.get("record_number", "")) in ticket_text
