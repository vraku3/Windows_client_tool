"""CMTrace / ConfigMgr log parsing.

The format is one record per `<![LOG[...]LOG]!>` block followed by an
attribute blob. What breaks in practice is never the happy path: it is
multi-line messages, a truncated final record while the file is being
written, and messages that themselves contain the delimiters.
"""
from datetime import datetime


from modules.log_viewer import cmtrace_parser as parser

SAMPLE = (
    '<![LOG[Starting the thing]LOG]!><time="13:45:12.345+000" '
    'date="08-20-2026" component="UpdatesHandler" context="" type="1" '
    'thread="1234" file="updateshandler.cpp:112">\n'
    '<![LOG[Careful now]LOG]!><time="13:45:13.001+000" date="08-20-2026" '
    'component="ContentAccess" context="" type="2" thread="1234" '
    'file="content.cpp:9">\n'
    '<![LOG[It broke]LOG]!><time="13:45:14.500+000" date="08-20-2026" '
    'component="ContentAccess" context="" type="3" thread="99" '
    'file="content.cpp:41">\n'
)


# ---- the happy path -----------------------------------------------------

def test_every_record_is_found():
    assert len(parser.parse(SAMPLE)) == 3


def test_the_message_is_the_message():
    assert parser.parse(SAMPLE)[0].message == "Starting the thing"


def test_type_maps_to_severity():
    """1/2/3 are what the colouring is driven from; getting these wrong makes
    every row the wrong colour, which is the entire point of the viewer."""
    assert [e.level for e in parser.parse(SAMPLE)] == ["Info", "Warning", "Error"]


def test_the_component_becomes_the_source():
    assert parser.parse(SAMPLE)[0].source == "UpdatesHandler"


def test_the_timestamp_is_assembled_from_date_and_time():
    entry = parser.parse(SAMPLE)[0]
    assert entry.timestamp == datetime(2026, 8, 20, 13, 45, 12, 345000)


def test_thread_and_file_survive_for_filtering():
    raw = parser.parse(SAMPLE)[2].raw
    assert raw["thread"] == "99"
    assert raw["file"] == "content.cpp:41"


# ---- the things that actually break -------------------------------------

def test_a_multi_line_message_stays_one_record():
    """A stack trace is one log entry, not eleven."""
    text = ('<![LOG[line one\nline two\nline three]LOG]!><time="10:00:00.000+000" '
            'date="08-20-2026" component="C" context="" type="1" thread="1" '
            'file="f.cpp:1">\n')
    entries = parser.parse(text)
    assert len(entries) == 1
    assert entries[0].message == "line one\nline two\nline three"


def test_a_truncated_final_record_is_dropped_not_half_parsed():
    """The file is being written while it is read. Half a record is not a
    log entry, and showing it as one is worse than waiting for the rest."""
    entries = parser.parse(SAMPLE + '<![LOG[incomplete...')
    assert len(entries) == 3


def test_a_message_containing_the_delimiter_does_not_split_the_record():
    text = ('<![LOG[the marker ]LOG]!> appears in the text]LOG]!>'
            '<time="10:00:00.000+000" date="08-20-2026" component="C" '
            'context="" type="1" thread="1" file="f.cpp:1">\n')
    entries = parser.parse(text)
    assert len(entries) == 1


def test_a_record_missing_its_attributes_is_skipped():
    assert parser.parse('<![LOG[orphan]LOG]!>\n') == []


def test_an_unparseable_date_does_not_lose_the_message():
    """A bad timestamp costs the ordering, not the line. Losing the message
    is the one outcome a log viewer must never produce."""
    text = ('<![LOG[still readable]LOG]!><time="not-a-time" date="nonsense" '
            'component="C" context="" type="3" thread="1" file="f.cpp:1">\n')
    entry = parser.parse(text)[0]
    assert entry.message == "still readable"
    assert entry.level == "Error"
    assert entry.timestamp == parser.UNKNOWN_TIME


def test_an_unknown_type_is_treated_as_info_not_dropped():
    text = ('<![LOG[odd]LOG]!><time="10:00:00.000+000" date="08-20-2026" '
            'component="C" context="" type="17" thread="1" file="f.cpp:1">\n')
    assert parser.parse(text)[0].level == "Info"


def test_the_utc_offset_is_recorded_but_not_applied():
    """CMTrace shows the wall clock the log was written with. Re-basing every
    line onto local time makes it disagree with every other log on the box
    and with what the person on the phone is reading out."""
    text = ('<![LOG[m]LOG]!><time="13:45:12.345-480" date="08-20-2026" '
            'component="C" context="" type="1" thread="1" file="f.cpp:1">\n')
    entry = parser.parse(text)[0]
    assert entry.timestamp.hour == 13
    assert entry.raw["utc_offset"] == "-480"


def test_empty_input_is_no_records_rather_than_an_error():
    assert parser.parse("") == []


# ---- auto-detection and the plain-text fallback -------------------------

def test_a_cmtrace_file_is_detected():
    assert parser.looks_like_cmtrace(SAMPLE) is True


def test_an_ordinary_log_is_not_mistaken_for_cmtrace():
    assert parser.looks_like_cmtrace("2026-08-20 13:45:12 ERROR nope\n") is False


def test_a_plain_log_still_yields_lines():
    """A viewer that shows nothing for an ordinary .log is worse than one
    that shows uncoloured lines."""
    entries = parser.parse("2026-08-20 13:45:12 ERROR disk on fire\n"
                           "just a line\n")
    assert len(entries) == 2
    assert entries[0].message.endswith("disk on fire")


def test_severity_words_colour_a_plain_log():
    entries = parser.parse("something WARNING here\nsomething ERROR here\n"
                           "something ordinary\n")
    assert [e.level for e in entries] == ["Warning", "Error", "Info"]


def test_a_plain_leading_timestamp_is_used_when_present():
    entry = parser.parse("2026-08-20 13:45:12 ERROR boom\n")[0]
    assert entry.timestamp == datetime(2026, 8, 20, 13, 45, 12)


#: Verbatim from `C:\Windows\SoftwareDistribution\ReportingEvents.log` on
#: this machine. Tab separated, and the timestamp is NOT at the start of the
#: line -- the GUID is -- so an anchored match leaves all 1,692 of its
#: records with a blank Time column. Note also `:086`, a COLON before the
#: milliseconds, and a `+0300` offset with no separator.
REPORTING_EVENT = (
    "{75F9F452-3CBF-4F1B-AB9B-28B2F7D8D07C}\t2026-08-02 14:39:05:086+0300\t"
    "1\t147 [AGENT_DETECTION_FINISHED]\t101\t"
    "{9482F4B4-E343-43B6-B170-9A65BC822C77}\t0\t0\t"
    "Device Driver Retrieval Client\tSuccess"
)


def test_a_timestamp_that_is_not_at_the_start_of_the_line_is_still_found():
    entry = parser.parse(REPORTING_EVENT)[0]
    assert entry.timestamp == datetime(2026, 8, 2, 14, 39, 5, 86_000)
    assert entry.raw.get("subsecond"), "it really did write milliseconds"


def test_padding_nuls_do_not_cost_a_record_its_timestamp():
    r"""Windows Update leaves one 928-character run of NULs inside
    `ReportingEvents.log`, and it is not a line of its own -- it sits in
    FRONT of a real record. That pushed the timestamp out of reach and left
    the pane's first row an empty band 928 characters wide.

    Only LEADING NULs go. A NUL beside every character is the symptom of a
    UTF-16 file read as UTF-8, and that must stay visible.
    """
    entry = parser.parse("\x00" * 928 + REPORTING_EVENT)[0]
    assert entry.timestamp == datetime(2026, 8, 2, 14, 39, 5, 86_000)
    assert entry.message.startswith("{75F9F452")


def test_a_line_of_nothing_but_padding_is_not_a_record():
    entries = parser.parse("one\n" + "\x00" * 40 + "\ntwo\n")
    assert [e.message for e in entries] == ["one", "two"]


def test_interleaved_nuls_are_left_alone_so_a_bad_decode_still_shows():
    entry = parser.parse("h\x00e\x00l\x00l\x00o\x00")[0]
    assert "\x00" in entry.message


def test_a_timestamp_is_only_looked_for_near_the_front_of_a_line():
    """Otherwise a date quoted deep inside a message becomes the row's time."""
    entry = parser.parse("x" * 200 + " 2026-08-20 13:45:12 done")[0]
    assert entry.timestamp == parser.UNKNOWN_TIME


def test_blank_lines_are_not_entries():
    assert len(parser.parse("one\n\n\ntwo\n")) == 2


def test_detection_only_sniffs_the_head_of_a_huge_file():
    """Sniffing 300 MB to answer a yes/no question is not a detector."""
    text = "x" * 5_000_000 + "<![LOG[late]LOG]!>"
    assert parser.looks_like_cmtrace(text) is False


def test_a_leading_bom_does_not_cost_the_first_lines_timestamp():
    r"""\ufeff is not whitespace to the regex, so an unstripped BOM silently
    drops the timestamp of the first line of every real Windows log."""
    entry = parser.parse("\ufeff2026-08-20 13:45:12 ERROR boom\n")[0]
    assert entry.timestamp == datetime(2026, 8, 20, 13, 45, 12)


# ---- real-log edge cases this app has no genuine SCCM/Intune log to test
# against (see CLAUDE.md's Log Viewer section) -- these are the specific
# things flagged as unverified against a real client's output, checked here
# empirically instead of left as an assumption ----------------------------

def test_an_attribute_value_containing_an_angle_bracket_does_not_drop_the_record():
    """Real bug, found empirically: the attrs blob used to be matched as
    "anything but < or >", so a value like context="a<b" made the whole
    record disappear rather than degrade -- exactly the "losing the line"
    outcome this parser's own docstring says must never happen. The attrs
    group now matches the KNOWN key="value" structure instead, so a `<`/`>`
    inside a quoted value is no longer special."""
    text = ('<![LOG[bracket test]LOG]!><time="01:02:03.000+000" '
           'date="01-01-2026" component="X" context="a<b" type="1">')
    entries = parser.parse_cmtrace(text)
    assert len(entries) == 1
    assert entries[0].message == "bracket test"
    assert entries[0].raw["context"] == "a<b"


def test_non_ascii_content_survives_component_and_message():
    """Real component names and messages are not guaranteed to be ASCII
    (a non-English OS locale, a localized component name)."""
    text = ('<![LOG[Starting up \u65e5\u672c\u8a9e componente]LOG]!>'
           '<time="13:45:12.345+000" date="08-20-2026" '
           'component="\u00dcpdatesHandler" type="1">')
    entries = parser.parse_cmtrace(text)
    assert entries[0].message == "Starting up \u65e5\u672c\u8a9e componente"
    assert entries[0].source == "\u00dcpdatesHandler"


def test_attribute_order_does_not_matter():
    """`_ATTR.findall` builds a dict keyed by name, so a real client writing
    attributes in a different order (or a different one per component)
    reads identically to the canonical time/date/component/.../file order
    every synthetic fixture in this file happens to use."""
    text = ('<![LOG[reordered]LOG]!><thread="99" type="2" component="Foo" '
           'time="01:02:03.000+000" date="01-01-2026">')
    entries = parser.parse_cmtrace(text)
    assert entries[0].source == "Foo"
    assert entries[0].level == "Warning"


def test_nul_padding_before_a_cmtrace_record_does_not_hide_it():
    """parse_plain/parse_service_log strip leading NUL padding per line
    (_unpadded) because their timestamp regexes only look at a bounded
    window near the front of a line -- parse_cmtrace's _RECORD regex scans
    the whole text with finditer, so padding anywhere before a record was
    already not a risk; this pins that down rather than leaving it assumed."""
    text = "\x00" * 50 + ('<![LOG[after nuls]LOG]!><time="01:02:03.000+000" '
                          'date="01-01-2026" component="X" type="1">')
    entries = parser.parse_cmtrace(text)
    assert len(entries) == 1
    assert entries[0].message == "after nuls"
