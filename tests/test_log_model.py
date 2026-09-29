"""The log viewer's table model: colouring, filtering, the cap, and find."""
from datetime import datetime

import pytest
from PyQt6.QtCore import Qt

from core.types import LogEntry
from modules.log_viewer.cmtrace_parser import UNKNOWN_TIME
from modules.log_viewer.log_model import (
    COMPONENT, LogModel, MESSAGE, SEVERITY, THREAD, TIME,
)


def _entry(message, level="Info", source="C", thread="1", when=None):
    return LogEntry(timestamp=when or datetime(2026, 8, 20, 13, 45, 12),
                    source=source, level=level, message=message,
                    raw={"thread": thread})


def _model(entries=(), cap=None):
    model = LogModel(cap=cap) if cap else LogModel()
    model.append(list(entries))
    return model


# ---- content ------------------------------------------------------------

def test_rows_and_columns(qapp):
    model = _model([_entry("one"), _entry("two")])
    assert model.rowCount() == 2
    # Time, Source, Severity, Component, Package, Thread, Message. Source
    # and Package are always DEFINED and hidden by the pane when they would
    # be columns of blanks -- letting the column count vary at runtime would
    # shift MESSAGE's index under the delegate.
    assert model.columnCount() == 7


def test_each_column_shows_its_field(qapp):
    model = _model([_entry("hello", level="Error", source="Comp", thread="77")])
    assert model.data(model.index(0, SEVERITY)) == "Error"
    assert model.data(model.index(0, COMPONENT)) == "Comp"
    assert model.data(model.index(0, THREAD)) == "77"
    assert model.data(model.index(0, MESSAGE)) == "hello"
    assert "2026-08-20 13:45:12" in model.data(model.index(0, TIME))


def test_a_whole_second_timestamp_does_not_claim_milliseconds(qapp):
    """CBS writes `22:08:03`. Every row of it showed `.000`, which reads as a
    measured millisecond rather than as a field the log never had."""
    model = _model([_entry("x")])
    assert model.data(model.index(0, TIME)) == "2026-08-20 13:45:12"


def test_a_sub_second_timestamp_keeps_its_milliseconds(qapp):
    entry = LogEntry(timestamp=datetime(2026, 8, 20, 13, 45, 12, 345000),
                     source="C", level="Info", message="x",
                     raw={"subsecond": "1"})
    model = _model([entry])
    assert model.data(model.index(0, TIME)) == "2026-08-20 13:45:12.345"


def test_an_unknown_time_shows_blank_not_year_one(qapp):
    """"0001-01-01" reads as a real timestamp and is not one."""
    model = _model([_entry("x", when=UNKNOWN_TIME)])
    assert model.data(model.index(0, TIME)) == ""


def test_a_multi_line_message_stays_on_one_row(qapp):
    """Letting the newlines through breaks the row height for the whole
    table; the full text is still on the tooltip."""
    model = _model([_entry("first\nsecond")])
    shown = model.data(model.index(0, MESSAGE))
    assert "\n" not in shown
    assert model.data(model.index(0, MESSAGE), Qt.ItemDataRole.ToolTipRole) \
        == "first\nsecond"


# ---- colouring ----------------------------------------------------------

def test_errors_and_warnings_are_coloured(qapp):
    model = _model([_entry("e", level="Error"), _entry("w", level="Warning"),
                    _entry("i", level="Info")])
    background = Qt.ItemDataRole.BackgroundRole
    assert model.data(model.index(0, 0), background) is not None
    assert model.data(model.index(1, 0), background) is not None
    assert model.data(model.index(2, 0), background) is None


def test_error_and_warning_do_not_share_a_colour(qapp):
    model = _model([_entry("e", level="Error"), _entry("w", level="Warning")])
    background = Qt.ItemDataRole.BackgroundRole
    assert (model.data(model.index(0, 0), background)
            != model.data(model.index(1, 0), background))


def test_coloured_rows_have_readable_text(qapp):
    """A background with no matching foreground is how dark themes end up
    with red-on-red."""
    model = _model([_entry("e", level="Error")])
    assert model.data(model.index(0, 0),
                      Qt.ItemDataRole.ForegroundRole) is not None


def test_severity_colours_follow_the_theme(qapp):
    """The old ROW_COLOURS were dark-sheet values by their own comment, and
    this pane is not in THEME_EXEMPT."""
    from core import semantic_colors
    from PyQt6.QtCore import Qt

    model = _model([_entry("boom", level="Error")])
    index = model.index(0, MESSAGE)
    semantic_colors.set_theme("dark")
    dark = model.data(index, Qt.ItemDataRole.BackgroundRole)
    semantic_colors.set_theme("light")
    try:
        light = model.data(index, Qt.ItemDataRole.BackgroundRole)
    finally:
        semantic_colors.set_theme("dark")
    assert dark != light


def test_the_component_column_keeps_its_tint_on_an_error_row(qapp):
    """Territory, not priority: if severity won the whole row, every Error
    row would lose its component colour."""
    from PyQt6.QtCore import Qt
    from modules.log_viewer import palette

    model = _model([_entry("boom", level="Error", source="CBS")])
    cell = model.data(model.index(0, COMPONENT), Qt.ItemDataRole.BackgroundRole)
    expected, _foreground = palette.component_colour("CBS")
    assert cell.name() == expected


def test_a_blank_component_gets_no_tint(qapp):
    """Regression: with the old default level="Info", severity_row_colour()
    is already None for every row, so the assertion passed whether or not
    the `and entry.source` guard was doing anything -- weakening the guard
    to ignore entry.source (so it fires for ANY column) would still leave
    this passing, because component_colour("")'s tint and "no colour" are
    both just "not the component tint" from that assertion's point of view.

    level="Error" makes the row genuinely coloured, by severity, so the
    guard is the only thing standing between this cell and
    component_colour("")'s tint instead of the row's real severity colour --
    weakening it now changes what colour comes back, not just whether one
    does.
    """
    from PyQt6.QtCore import Qt
    from modules.log_viewer import palette

    model = _model([_entry("x", level="Error", source="")])
    cell = model.data(model.index(0, COMPONENT), Qt.ItemDataRole.BackgroundRole)
    expected, _foreground = palette.severity_row_colour("Error")
    assert cell.name() == expected


def test_a_highlight_rule_still_colours_the_component_cell_when_blank(qapp):
    """The flip side of the guard above: no component means no TINT to
    protect, so a highlight rule the user typed is free to colour that cell
    same as any other -- this is correct behaviour, not a gap."""
    from PyQt6.QtCore import Qt
    from modules.log_viewer.highlight import HighlightRule

    model = _model([_entry("boom", level="Error", source="")])
    model.set_highlight_rules([HighlightRule("boom", "#00ff00")])
    cell = model.data(model.index(0, COMPONENT), Qt.ItemDataRole.BackgroundRole)
    assert cell.name() == "#00ff00"


def test_a_malformed_highlight_rule_colour_does_not_crash_the_model(qapp):
    """readable_text_on used to raise ValueError out of this exact call --
    a reimplemented Qt virtual, where PyQt cannot catch it. Palette now
    answers with a safe ink instead, so this must not raise."""
    from PyQt6.QtCore import Qt
    from modules.log_viewer.highlight import HighlightRule

    model = _model([_entry("boom", level="Error")])
    model.set_highlight_rules([HighlightRule("boom", "red")])
    foreground = model.data(model.index(0, MESSAGE),
                            Qt.ItemDataRole.ForegroundRole)
    assert foreground is not None


# ---- filtering ----------------------------------------------------------

def test_filtering_by_level(qapp):
    model = _model([_entry("e", level="Error"), _entry("i", level="Info")])
    model.set_filter(levels={"Error"})
    assert model.rowCount() == 1
    assert model.data(model.index(0, MESSAGE)) == "e"


def test_an_empty_level_filter_shows_everything(qapp):
    model = _model([_entry("e", level="Error"), _entry("i", level="Info")])
    model.set_filter(levels=set())
    assert model.rowCount() == 2


def test_filtering_by_text_is_case_insensitive(qapp):
    model = _model([_entry("Disk On Fire"), _entry("all well")])
    model.set_filter(needle="disk")
    assert model.rowCount() == 1


def test_filtering_by_component(qapp):
    model = _model([_entry("a", source="One"), _entry("b", source="Two")])
    model.set_filter(component="Two")
    assert model.rowCount() == 1
    assert model.data(model.index(0, MESSAGE)) == "b"


def test_filters_combine(qapp):
    model = _model([_entry("boom", level="Error", source="One"),
                    _entry("boom", level="Error", source="Two"),
                    _entry("quiet", level="Error", source="One")])
    model.set_filter(levels={"Error"}, needle="boom", component="One")
    assert model.rowCount() == 1


def test_filtering_does_not_lose_the_records(qapp):
    """A filter hides rows; it must not throw entries away, or clearing it
    would come back to fewer lines than the file has."""
    model = _model([_entry("e", level="Error"), _entry("i", level="Info")])
    model.set_filter(levels={"Error"})
    model.set_filter(levels=set())
    assert model.rowCount() == 2
    assert model.total == 2


def test_components_are_offered_for_the_filter_box(qapp):
    model = _model([_entry("a", source="Beta"), _entry("b", source="Alpha"),
                    _entry("c", source="Beta")])
    assert model.components() == ["Alpha", "Beta"]


# ---- the cap ------------------------------------------------------------

def test_the_oldest_records_age_out(qapp):
    """A 300 MB log cannot all live in the model. The newest is what matters."""
    model = _model([_entry(f"line {i}") for i in range(50)], cap=10)
    assert model.total == 10
    assert model.data(model.index(9, MESSAGE)) == "line 49"


def test_what_was_dropped_is_counted_so_the_ui_can_say_so(qapp):
    """Silently showing 10 of 50 lines is the kind of quiet wrongness that
    makes someone conclude the log is fine."""
    model = _model([_entry(f"line {i}") for i in range(50)], cap=10)
    assert model.dropped == 40


def test_appending_in_batches_still_caps(qapp):
    model = LogModel(cap=5)
    for i in range(4):
        model.append([_entry(f"batch {i} line {j}") for j in range(3)])
    assert model.total == 5
    assert model.data(model.index(4, MESSAGE)) == "batch 3 line 2"


# ---- find ---------------------------------------------------------------

def test_find_returns_the_next_match(qapp):
    model = _model([_entry("alpha"), _entry("beta"), _entry("gamma")])
    assert model.find("beta", start_row=0) == 1


def test_find_wraps_around(qapp):
    """Stopping at the end makes the user scroll back to the top to carry on."""
    model = _model([_entry("alpha"), _entry("beta")])
    assert model.find("alpha", start_row=1) == 0


def test_find_goes_backwards_too(qapp):
    model = _model([_entry("alpha"), _entry("beta"), _entry("alpha")])
    assert model.find("alpha", start_row=2, forwards=False) == 0


def test_find_is_case_insensitive(qapp):
    assert _model([_entry("Alpha")]).find("alpha") == 0


def test_find_with_no_match_returns_minus_one(qapp):
    assert _model([_entry("alpha")]).find("nothing") == -1


def test_find_on_an_empty_model_is_not_an_error(qapp):
    assert LogModel().find("anything") == -1


def test_find_only_searches_what_is_visible(qapp):
    """Jumping to a row hidden by the filter would scroll to nothing."""
    model = _model([_entry("target", level="Info"),
                    _entry("other", level="Error")])
    model.set_filter(levels={"Error"})
    assert model.find("target") == -1


# ---- find_issue -----------------------------------------------------------

def test_find_issue_lands_on_the_next_error_or_warning(qapp):
    model = _model([_entry("ok", level="Info"),
                    _entry("careful", level="Warning"),
                    _entry("broke", level="Error")])
    assert model.find_issue(start_row=0) == 1
    assert model.find_issue(start_row=1) == 2


def test_find_issue_skips_info_and_debug(qapp):
    model = _model([_entry("a", level="Debug"), _entry("b", level="Info"),
                    _entry("c", level="Error")])
    assert model.find_issue(start_row=0) == 2


def test_find_issue_wraps_around(qapp):
    model = _model([_entry("bad", level="Error"), _entry("ok", level="Info")])
    assert model.find_issue(start_row=0) == 0


def test_find_issue_goes_backwards_too(qapp):
    model = _model([_entry("bad", level="Error"), _entry("ok", level="Info"),
                    _entry("worse", level="Error")])
    assert model.find_issue(start_row=2, forwards=False) == 0


def test_find_issue_with_no_problems_returns_minus_one(qapp):
    model = _model([_entry("a"), _entry("b"), _entry("c")])
    assert model.find_issue(start_row=0) == -1


def test_find_issue_on_an_empty_model_is_not_an_error(qapp):
    assert LogModel().find_issue(start_row=0) == -1


def test_find_issue_only_searches_what_is_visible(qapp):
    """The severity filter hiding an Error is not the same question as
    whether the whole log has one -- jumping to a hidden row would scroll
    to nothing."""
    model = _model([_entry("ok", level="Info"), _entry("bad", level="Error")])
    model.set_filter(levels={"Info"})
    assert model.find_issue(start_row=0) == -1


def test_find_issue_reaches_a_continuation_that_folding_was_hiding(qapp):
    """The model does not assume a continuation's level always matches its
    parent's -- only `parse_service_log` happens to set it that way today --
    so find_issue unfolds to reach an Error that folding was hiding, the
    same as `find` does for a text match."""
    parent = _entry("Performing 1 operation:", level="Info", source="CSI")
    cont = LogEntry(timestamp=UNKNOWN_TIME, source="CSI", level="Error",
                    message="  (0)  broke", raw={"continuation": "1"})
    model = _model([_entry("ok", level="Info"), parent, cont])
    row = model.find_issue(start_row=1)
    assert row >= 0
    assert model.entry(row).message == "  (0)  broke"
    assert model.is_folding() is False


def test_find_issue_that_finds_nothing_leaves_folding_alone(qapp):
    model = _model([_entry("ok", level="Info")])
    assert model.find_issue(start_row=0) == -1
    assert model.is_folding() is True


def test_find_issue_reaches_the_real_cbs_logs_first_warning_or_error(qapp):
    """Against the real machine, not a synthetic fixture: CBS.log measured
    359 Warning and 59 Error lines out of 62,208 (see cmtrace_parser's own
    module docstring). find_issue must land on one of them, unfolded or not,
    starting from row 0 -- the exact "jump to the first problem" an admin
    opening this file would reach for."""
    import os

    from modules.log_viewer.cmtrace_parser import parse

    path = r"C:\Windows\Logs\CBS\CBS.log"
    if not os.path.exists(path):
        pytest.skip("no CBS.log on this machine")
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    entries = parse(text)
    if not any(e.level in ("Warning", "Error") for e in entries):
        pytest.skip("this machine's CBS.log currently has no Warning/Error")
    model = _model(entries)
    row = model.find_issue(start_row=0)
    assert row >= 0
    assert model.entry(row).level in ("Warning", "Error")


# ---- clearing -----------------------------------------------------------

def test_clearing_empties_everything(qapp):
    model = _model([_entry("a"), _entry("b")])
    model.clear()
    assert model.rowCount() == 0 and model.total == 0 and model.dropped == 0


# ---- appending must not reset the world ---------------------------------

def test_appending_inserts_rather_than_resetting(qapp):
    """A model reset clears the view's selection. While following, that
    happens every second -- click a row to read it and it deselects under
    you. Only an append that actually SHIFTS indices justifies a reset."""
    model = _model([_entry("one")])
    events = []
    model.modelAboutToBeReset.connect(lambda: events.append("reset"))
    model.rowsInserted.connect(lambda *_a: events.append("insert"))

    model.append([_entry("two")])

    assert "insert" in events
    assert "reset" not in events


def test_an_append_that_overflows_the_cap_does_reset(qapp):
    """Dropping from the front renumbers every row, so a reset is the honest
    signal -- an insert would leave the view pointing at the wrong records."""
    model = LogModel(cap=2)
    model.append([_entry("a"), _entry("b")])
    events = []
    model.modelAboutToBeReset.connect(lambda: events.append("reset"))
    model.append([_entry("c")])
    assert "reset" in events


def test_an_append_hidden_by_the_filter_inserts_no_rows(qapp):
    model = _model([_entry("visible", level="Error")])
    model.set_filter(levels={"Error"})
    events = []
    model.rowsInserted.connect(lambda *_a: events.append("insert"))
    model.append([_entry("hidden", level="Info")])
    assert events == []
    assert model.rowCount() == 1
    assert model.total == 2


def test_a_filtered_append_still_shows_what_matches(qapp):
    model = _model([_entry("first", level="Error")])
    model.set_filter(levels={"Error"})
    model.append([_entry("second", level="Error"), _entry("no", level="Info")])
    assert model.rowCount() == 2
    assert model.data(model.index(1, MESSAGE)) == "second"


def test_appending_keeps_the_rows_in_order(qapp):
    model = _model([_entry("one")])
    model.append([_entry("two"), _entry("three")])
    assert [model.data(model.index(r, MESSAGE)) for r in range(3)] == [
        "one", "two", "three"]


# ---- highlight rules --------------------------------------------------------

def test_a_highlight_rule_beats_severity_on_the_row(qapp):
    from modules.log_viewer.highlight import HighlightRule

    model = _model([_entry("boom", level="Error")])
    model.set_highlight_rules([HighlightRule("boom", "#00ff00")])
    colour = model.data(model.index(0, MESSAGE),
                        Qt.ItemDataRole.BackgroundRole)
    assert colour.name() == "#00ff00"


def test_a_highlight_rule_does_not_take_the_component_column(qapp):
    from modules.log_viewer import palette
    from modules.log_viewer.highlight import HighlightRule

    model = _model([_entry("boom", source="CBS")])
    model.set_highlight_rules([HighlightRule("boom", "#00ff00")])
    cell = model.data(model.index(0, COMPONENT),
                      Qt.ItemDataRole.BackgroundRole)
    assert cell.name() == palette.component_colour("CBS")[0]


def test_setting_rules_repaints_without_losing_the_selection(qapp):
    """A reset clears the view's selection, and this pane already learned
    that lesson once with append()."""
    model = _model([_entry("one"), _entry("two")])
    seen = []
    model.dataChanged.connect(lambda *a: seen.append(a))
    model.set_highlight_rules([])
    assert seen, "the view was never told to repaint"


def test_a_highlight_rule_foreground_contrasts_with_its_background(qapp):
    """A bright green rule (#00ff00) with an Error level must not return the
    severity's Error foreground (#ff9999), which is chosen for legibility on
    Error's dark red (#5c1a1a). Pink on green has poor contrast. The
    foreground must instead be chosen to contrast with green."""
    from modules.log_viewer.highlight import HighlightRule

    model = _model([_entry("boom", level="Error")])
    model.set_highlight_rules([HighlightRule("boom", "#00ff00")])

    background = model.data(model.index(0, MESSAGE),
                           Qt.ItemDataRole.BackgroundRole)
    foreground = model.data(model.index(0, MESSAGE),
                           Qt.ItemDataRole.ForegroundRole)

    # Compute contrast using the same luminance formula as test_log_palette.py
    def _luminance(hex_colour):
        value = hex_colour.lstrip("#")
        parts = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        def channel(v):
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        red, green, blue = (channel(p) for p in parts)
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    def _contrast(a, b):
        first, second = _luminance(a), _luminance(b)
        lighter, darker = max(first, second), min(first, second)
        return (lighter + 0.05) / (darker + 0.05)

    # The rule's background is green (#00ff00), and foreground must contrast
    ratio = _contrast(foreground.name(), background.name())
    assert ratio >= 4.5, (
        f"A rule with background {background.name()} must have foreground "
        f"with 4.5:1 contrast; got {foreground.name()} at {ratio:.2f}:1")


# ---- thread / time-range / regex filtering -------------------------------

def _at(when, message="x", thread="1"):
    return LogEntry(timestamp=when, source="CBS", level="Info",
                    message=message, raw={"thread": thread})


def test_filtering_by_thread(qapp):
    model = _model([_at(datetime(2026, 8, 30, 12, 0), thread="100"),
                    _at(datetime(2026, 8, 30, 12, 1), thread="200")])
    model.set_filter(thread="200")
    assert model.rowCount() == 1


def test_threads_are_offered_by_how_common_they_are(qapp):
    """DISM has 329 distinct thread ids, so the combo has to be ordered by
    something useful -- an alphabetical list of 329 numbers is not."""
    model = _model([_at(datetime(2026, 8, 30, 12, 0), thread="100"),
                    _at(datetime(2026, 8, 30, 12, 1), thread="200"),
                    _at(datetime(2026, 8, 30, 12, 2), thread="200")])
    assert model.threads() == [("200", 2), ("100", 1)]


def test_filtering_by_a_time_range_includes_both_ends(qapp):
    model = _model([_at(datetime(2026, 8, 30, 12, 0)),
                    _at(datetime(2026, 8, 30, 12, 5)),
                    _at(datetime(2026, 8, 30, 12, 9))])
    model.set_filter(time_from=datetime(2026, 8, 30, 12, 0),
                     time_to=datetime(2026, 8, 30, 12, 5))
    assert model.rowCount() == 2


def test_a_record_with_no_timestamp_survives_a_time_filter(qapp):
    """Losing a record is the one outcome a log viewer must never produce,
    and a continuation line has no timestamp of its own."""
    from modules.log_viewer.cmtrace_parser import UNKNOWN_TIME

    model = _model([_at(UNKNOWN_TIME)])
    model.set_filter(time_from=datetime(2026, 8, 30, 12, 0))
    assert model.rowCount() == 1


def test_the_time_span_is_the_first_and_last_real_timestamp(qapp):
    from modules.log_viewer.cmtrace_parser import UNKNOWN_TIME

    model = _model([_at(UNKNOWN_TIME),
                    _at(datetime(2026, 8, 30, 12, 0)),
                    _at(datetime(2026, 8, 30, 12, 9))])
    assert model.time_span() == (datetime(2026, 8, 30, 12, 0),
                                 datetime(2026, 8, 30, 12, 9))


def test_the_time_span_of_an_empty_log_is_none(qapp):
    assert _model([]).time_span() is None


def test_filtering_by_a_regular_expression(qapp):
    model = _model([_at(datetime(2026, 8, 30, 12, 0), "HRESULT = 0x80070005"),
                    _at(datetime(2026, 8, 30, 12, 1), "all fine")])
    model.set_filter(needle=r"0x8007[0-9a-f]{4}", regex=True)
    assert model.rowCount() == 1


def test_an_invalid_regex_matches_nothing_and_does_not_raise(qapp):
    model = _model([_at(datetime(2026, 8, 30, 12, 0), "anything")])
    model.set_filter(needle="[unclosed", regex=True)
    assert model.rowCount() == 0


def test_the_regex_is_compiled_once_per_filter_not_once_per_row(qapp):
    import re

    calls = []
    original = re.compile

    def counting(pattern, *args, **kwargs):
        calls.append(pattern)
        return original(pattern, *args, **kwargs)

    model = _model([_at(datetime(2026, 8, 30, 12, i)) for i in range(20)])
    re.compile = counting
    try:
        model.set_filter(needle="x", regex=True)
    finally:
        re.compile = original
    assert len(calls) == 1, f"compiled {len(calls)} times for 20 rows"


def test_find_honours_the_regex_flag_too(qapp):
    """One checkbox governs Find AND Filter, so find() cannot stay
    substring-only while the filter understands patterns."""
    model = _model([_at(datetime(2026, 8, 30, 12, 0), "all fine"),
                    _at(datetime(2026, 8, 30, 12, 1), "HRESULT = 0x80070005")])
    model.set_filter(regex=True)
    assert model.find(r"0x8007[0-9a-f]{4}", start_row=0) == 1


def test_find_with_an_invalid_pattern_finds_nothing(qapp):
    model = _model([_at(datetime(2026, 8, 30, 12, 0), "anything")])
    model.set_filter(regex=True)
    assert model.find("[unclosed", start_row=0) == -1


def test_the_filter_axes_combine(qapp):
    model = _model([_at(datetime(2026, 8, 30, 12, 0), "keep", thread="100"),
                    _at(datetime(2026, 8, 30, 12, 1), "keep", thread="200"),
                    _at(datetime(2026, 8, 30, 13, 0), "keep", thread="100")])
    model.set_filter(thread="100", needle="keep",
                     time_to=datetime(2026, 8, 30, 12, 30))
    assert model.rowCount() == 1


# ---- shared formatting/matching, not two copies that can drift ----------
#
# log_export._stamp/format_stamp and this TIME branch used to implement the
# same three rules (UNKNOWN_TIME -> blank, subsecond -> milliseconds, else
# whole seconds) independently. They agreed by luck; nothing enforced it.
# Same story for the filter haystack vs highlight._haystack/haystack.

def test_the_time_column_delegates_to_log_exports_format_stamp(qapp,
                                                                monkeypatch):
    from modules.log_viewer import log_model as log_model_module

    monkeypatch.setattr(log_model_module, "format_stamp",
                        lambda entry: "STAMP-SENTINEL", raising=False)
    model = _model([_entry("x")])
    assert model.data(model.index(0, TIME)) == "STAMP-SENTINEL"


def test_filtering_delegates_to_the_shared_haystack(qapp, monkeypatch):
    from modules.log_viewer import log_model as log_model_module

    calls = []

    def fake_haystack(entry):
        calls.append(entry)
        return "totally different text"

    monkeypatch.setattr(log_model_module, "haystack", fake_haystack,
                        raising=False)
    model = _model([_entry("anything")])
    model.set_filter(needle="different")
    assert calls, "_matches must call the shared haystack(), not build its own"
    assert model.rowCount() == 1


# ---- folding continuation lines -----------------------------------------

#: A CSI block on the real CbsPersist archive runs to 1,260 continuation
#: lines under one parent, and 9,185 of that log's 90,714 records are
#: continuations. Folding is what stops one operation list swamping the view.
def _cont(message, when=None):
    return LogEntry(timestamp=when or UNKNOWN_TIME, source="CSI",
                    level="Info", message=message,
                    raw={"continuation": "1"})


def _block(qapp=None):
    """A parent, two continuations under it, then an unrelated parent."""
    return _model([_entry("Performing 3 operations as follows:", source="CSI"),
                   _cont("  (0)  Uninstall: alpha"),
                   _cont("  (1)  Install: beta"),
                   _entry("Ending TrustedInstaller", source="CBS")])


def test_folding_is_on_by_default_and_hides_continuations(qapp):
    model = _block()
    assert model.rowCount() == 2
    assert model.total == 4, "the records are still there, only the rows went"


def test_unfolding_brings_the_continuations_back(qapp):
    model = _block()
    model.set_folding(False)
    assert model.rowCount() == 4


def test_a_folded_parent_says_how_many_lines_it_is_hiding(qapp):
    model = _block()
    assert "(+2 lines)" in model.data(model.index(0, MESSAGE))


def test_a_parent_with_no_continuations_gets_no_suffix(qapp):
    model = _block()
    assert "lines)" not in model.data(model.index(1, MESSAGE))


def test_the_suffix_is_gone_once_unfolded(qapp):
    model = _block()
    model.set_folding(False)
    assert "lines)" not in model.data(model.index(0, MESSAGE))


def test_the_suffix_never_reaches_the_record_itself(qapp):
    """Export and copy read entry.message, so the display suffix must not
    leak into the data they write."""
    model = _block()
    model.data(model.index(0, MESSAGE))
    assert model.entry(0).message == "Performing 3 operations as follows:"


def test_typing_a_filter_makes_folded_lines_eligible_again(qapp):
    """Folding is for browsing. The moment the user searches, nothing may
    hide a match from them."""
    model = _block()
    model.set_filter(needle="Uninstall")
    assert model.rowCount() == 1
    assert "Uninstall" in model.entry(0).message


def test_clearing_the_filter_folds_again(qapp):
    model = _block()
    model.set_filter(needle="Uninstall")
    model.set_filter(needle="")
    assert model.rowCount() == 2


def test_find_reaches_a_line_that_folding_was_hiding(qapp):
    """A search result outranks a view convenience: find unfolds to reach it
    rather than reporting no match."""
    model = _block()
    row = model.find("Install: beta", start_row=0)
    assert row >= 0
    assert "Install: beta" in model.entry(row).message
    assert model.is_folding() is False, "it must unfold, not silently miss"


def test_find_that_matches_nothing_leaves_folding_alone(qapp):
    model = _block()
    assert model.find("no such text anywhere", start_row=0) == -1
    assert model.is_folding() is True


def test_the_folded_count_is_reported_for_the_status_bar(qapp):
    model = _block()
    assert model.folded_count() == 2
    model.set_folding(False)
    assert model.folded_count() == 0


def test_export_rows_include_the_lines_folding_hides(qapp):
    """An exported file that silently drops a tenth of the log is the exact
    failure this module exists to prevent."""
    model = _block()
    exported = [e.message for e in model.rows_for_export()]
    assert len(exported) == 4
    assert "  (0)  Uninstall: alpha" in exported


def test_export_rows_still_respect_a_real_filter(qapp):
    """Folding is ignored by export; an actual filter is not."""
    model = _block()
    model.set_filter(component="CBS")
    assert [e.message for e in model.rows_for_export()] == [
        "Ending TrustedInstaller"]


def test_an_orphan_continuation_is_never_dropped(qapp):
    """A tail slice can open inside a 1,260-line block, so the first records
    can be continuations with no parent above them."""
    model = _model([_cont("  (0)  Uninstall: alpha"),
                    _entry("Ending TrustedInstaller")])
    assert model.rowCount() == 2, "an orphan has no parent to fold under"
