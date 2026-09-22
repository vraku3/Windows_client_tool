"""CMTrace / ConfigMgr log parsing.

One record is `<![LOG[message]LOG]!>` followed by an attribute blob:

    <![LOG[Starting]LOG]!><time="13:45:12.345+000" date="08-20-2026"
      component="UpdatesHandler" context="" type="1" thread="1234"
      file="updateshandler.cpp:112">

`type` is what the colouring hangs off: 1 Info, 2 Warning, 3 Error.

Anything that is not CMTrace falls back to a best-effort line parse. A viewer
that shows nothing for an ordinary `.log` is worse than one that shows
uncoloured lines.

No Qt here, so the parsing rules are testable without a display -- the same
split the CBS and DISM parsers use.
"""
import logging
import re
from datetime import datetime

from core.types import LogEntry

logger = logging.getLogger(__name__)

#: A record whose date cannot be read still has its message. Losing the line
#: is the one outcome a log viewer must never produce, so a bad timestamp
#: costs the ordering and nothing else.
UNKNOWN_TIME = datetime.min

#: Only the head of a file is sniffed. Reading 300 MB to answer a yes/no
#: question is not a detector.
SNIFF_BYTES = 8192

#: The attrs group matches the KNOWN attribute structure (a run of
#: key="value" pairs) rather than "anything but angle brackets" -- a real
#: attribute value containing a literal `<` or `>` (e.g. `context="a<b"`)
#: is legal CMTrace-adjacent content and used to make [^<>]* refuse to
#: match, silently dropping the ENTIRE record. Only a literal `"` inside a
#: value can still break this, which the format itself has no answer for
#: either (there is no escape sequence CMTrace defines for it).
_RECORD = re.compile(
    r'<!\[LOG\[(?P<message>.*?)\]LOG\]!>\s*<(?P<attrs>(?:\s*\w+="[^"]*")*)\s*>',
    re.DOTALL)
_ATTR = re.compile(r'(\w+)="([^"]*)"')

#: ConfigMgr writes 0/1 for informational, 2 warning, 3 error, and some
#: components use 4/5 for verbose. Anything else is shown rather than dropped.
_LEVELS = {"0": "Info", "1": "Info", "2": "Warning", "3": "Error",
           "4": "Debug", "5": "Debug"}

#: NOT anchored. `ReportingEvents.log` puts a 38-character GUID in front of
#: its date, so matching from column 0 left all 1,692 of its records with a
#: blank Time column -- on the log the Windows Update pane sends people to.
#: The sub-second group is optional and accepts a colon as its separator,
#: which is what that file writes (`14:39:05:086+0300`).
_PLAIN_TIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,:](\d{1,6}))?")

#: How far into a line a timestamp is looked for. Bounded, so a date quoted
#: deep inside a message does not become the row's time.
_TIME_WINDOW = 64
_PLAIN_ERROR = re.compile(r"\b(error|fail(ed|ure)?|fatal|exception)\b", re.I)
_PLAIN_WARN = re.compile(r"\b(warn(ing)?|caution)\b", re.I)

#: CBS, DISM, setupact and setuperr share one fixed-column layout. Measured
#: across 62,208 head lines of this machine's own files: the severity token
#: starts at column 21 on 100% of them, and only three tokens exist -- Info
#: (61,790), Warning (359), Error (59).
_SERVICE_HEAD = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2}), (\S+)")

#: The component sits in [43:50) and the message starts at 50. The field
#: overflows column 49 on NONE of the 62,208 lines measured, and it is
#: genuinely blank on many of them (7,059 in CBS.log) with the message still
#: starting at 50 -- which is why splitting on whitespace, as the first probe
#: did, invents components out of message text.
_COMPONENT_AT = 43
_MESSAGE_AT = 50

#: A narrower variant exists: 206 lines of setupact.log put their text at
#: column 32 (`Info       [0x090008] PANTHR ...`). Cutting those at 50 would
#: slice the front off the message, so the layout is decided PER LINE.
_TID = re.compile(r"\bTID=(\d+)")

#: Continuation lines indent 0, 2 or 4 spaces to show structure (CSI's
#: numbered operation lists) or 35 to align under the message column
#: (setupact). Measured: nothing lands in between, so anything this deep is
#: padding to be dropped rather than nesting to be kept.
_CONTINUATION_ALIGNMENT = 8

#: How many of a sniffed file's lines must look like this format. A tail
#: slice can open inside a 1,260-line continuation block, so this is a
#: fraction rather than "the first line".
_SERVICE_SHARE = 0.25


def _unpadded(line: str) -> str:
    r"""A line with any NUL padding taken off the front.

    Windows Update leaves a 928-character run of NULs inside
    `ReportingEvents.log`, and it is not a line of its own -- it sits in
    front of a real record, pushing its timestamp out of reach and rendering
    as an empty band the width of the pane.

    LEADING NULs only. A NUL beside every character is what a UTF-16 file
    read as UTF-8 looks like, and that has to stay visible rather than being
    quietly tidied away -- it is the defect this parser was fixed for.
    """
    return line.lstrip("\x00") if line[:1] == "\x00" else line


def looks_like_cmtrace(text: str) -> bool:
    return "<![LOG[" in text[:SNIFF_BYTES]


def looks_like_service_log(text: str) -> bool:
    """Does this look like the CBS/DISM/Panther fixed-column format?"""
    lines = [line for line in text[:SNIFF_BYTES].splitlines() if line.strip()]
    if not lines:
        return False
    heads = sum(1 for line in lines if _SERVICE_HEAD.match(line))
    return bool(heads) and heads >= _SERVICE_SHARE * len(lines)


def _timestamp(attrs: dict) -> datetime:
    """`date="08-20-2026"` plus `time="13:45:12.345+000"`.

    The trailing UTC offset is KEPT but never applied. CMTrace shows the wall
    clock the log was written with; re-basing every line onto local time makes
    it disagree with every other log on the box, and with whatever the person
    on the phone is reading out.
    """
    date_text = attrs.get("date", "")
    time_text = attrs.get("time", "")
    clock = re.split(r"[+-]", time_text, maxsplit=1)[0]
    for date_format in ("%m-%d-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        for time_format in ("%H:%M:%S.%f", "%H:%M:%S"):
            try:
                return datetime.strptime(f"{date_text} {clock}",
                                         f"{date_format} {time_format}")
            except ValueError:
                logger.debug("_timestamp: skipping an item that could not be read", exc_info=True)
                continue
    return UNKNOWN_TIME


def _offset(time_text: str) -> str:
    match = re.search(r"([+-]\d+)$", time_text or "")
    return match.group(1) if match else ""


def parse_cmtrace(text: str) -> list:
    entries = []
    for match in _RECORD.finditer(text):
        attrs = dict(_ATTR.findall(match.group("attrs")))
        if not attrs:
            # A `<...>` that carries no key="value" pair is not an attribute
            # blob; the record is malformed and half a record is not an entry.
            continue
        raw = dict(attrs)
        raw["utc_offset"] = _offset(attrs.get("time", ""))
        if re.search(r"\d:\d{2}:\d{2}[.,]\d", attrs.get("time", "")):
            raw["subsecond"] = "1"
        entries.append(LogEntry(
            timestamp=_timestamp(attrs),
            source=attrs.get("component", ""),
            level=_LEVELS.get(attrs.get("type", ""), "Info"),
            message=match.group("message"),
            raw=raw,
        ))
    return entries


def parse_plain(text: str) -> list:
    """Best effort for anything that is not CMTrace."""
    entries = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = _unpadded(line)
        if not line.strip():
            continue
        stamp = UNKNOWN_TIME
        fraction = ""
        match = _PLAIN_TIME.search(line[:_TIME_WINDOW])
        if match:
            try:
                stamp = datetime(*(int(part)
                                   for part in match.groups()[:6]))
                fraction = match.group(7) or ""
                if fraction:
                    stamp = stamp.replace(
                        microsecond=int(fraction.ljust(6, "0")[:6]))
            except ValueError:
                stamp, fraction = UNKNOWN_TIME, ""
        if _PLAIN_ERROR.search(line):
            level = "Error"
        elif _PLAIN_WARN.search(line):
            level = "Warning"
        else:
            level = "Info"
        raw = {"line": str(number)}
        if fraction:
            raw["subsecond"] = "1"
        entries.append(LogEntry(timestamp=stamp, source="", level=level,
                                message=line.rstrip(), raw=raw))
    return entries


def _service_level(token: str) -> str:
    """The severity the line STATES, not one guessed from its words.

    `parse_plain` greps the whole line for `error|fail|fatal|exception`, and
    on these files that is wrong 1,156 times over -- `Info ...
    InternalOpenPackage failed for Package_for_KB3025096` is an informational
    line about a package that is not installed, and it rendered red.
    """
    level = token.strip().rstrip(":").title()
    return level or "Info"


def parse_service_log(text: str) -> list:
    """CBS / DISM / setupact / setuperr: fixed columns, stated severity.

    Continuation lines -- 9,185 of them in the sampled files, wrapped output
    with no timestamp of their own -- inherit the record above rather than
    becoming timeless rows of their own.
    """
    entries = []
    previous = None
    for number, line in enumerate(text.splitlines(), start=1):
        line = _unpadded(line)
        if not line.strip():
            continue
        raw = {"line": str(number)}
        match = _SERVICE_HEAD.match(line)
        if not match:
            indent = len(line) - len(line.lstrip())
            message = (line.strip() if indent >= _CONTINUATION_ALIGNMENT
                       else line.rstrip())
            raw["continuation"] = "1"
            if previous is not None:
                raw["thread"] = previous.raw.get("thread", "")
                entries.append(LogEntry(
                    timestamp=previous.timestamp, source=previous.source,
                    level=previous.level, message=message, raw=raw))
            else:
                # A tail slice can open inside a continuation block. The line
                # is still the log's content; only its heading is missing.
                entries.append(LogEntry(timestamp=UNKNOWN_TIME, source="",
                                        level="Info", message=message,
                                        raw=raw))
            continue

        try:
            stamp = datetime(*(int(part) for part in match.groups()[:6]))
        except ValueError:
            stamp = UNKNOWN_TIME
        rest = line[match.end():]
        starts = match.end() + (len(rest) - len(rest.lstrip()))
        # The wide layout only if the text really begins at the message
        # column and nothing straddles the component field's right edge.
        wide = (starts >= _COMPONENT_AT
                and not (len(line) > _MESSAGE_AT
                         and line[_MESSAGE_AT - 1] != " "
                         and line[_MESSAGE_AT] != " "))
        if wide:
            component = line[_COMPONENT_AT:_MESSAGE_AT].strip()
            message = line[_MESSAGE_AT:].strip()
        else:
            component = ""
            message = line[starts:].rstrip()
        thread = _TID.search(message)
        if thread:
            raw["thread"] = thread.group(1)
        previous = LogEntry(timestamp=stamp, source=component,
                            level=_service_level(match.group(7)),
                            message=message, raw=raw)
        entries.append(previous)
    return entries


def parse(text: str) -> list:
    """Records from `text`, picking the parser by sniffing it."""
    if not text:
        return []
    # Belt and braces: LogReader strips this, but `parse` is also called
    # directly, and U+FEFF is not whitespace to the timestamp regex.
    text = text.lstrip("\ufeff")
    if looks_like_cmtrace(text):
        return parse_cmtrace(text)
    if looks_like_service_log(text):
        return parse_service_log(text)
    return parse_plain(text)
