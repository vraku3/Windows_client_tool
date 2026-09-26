"""Read event logs through `wevtutil` so every event carries its REAL message.

The legacy `ReadEventLog` API only hands back the raw string inserts. For a
modern provider that is `42 | 31 | 40 | 1 | 91777 | 0 | ...` or nothing at all
(measured: about a third of the rows in a 24 h System sweep had a blank or
numeric-soup message), because the message TEXT lives in the provider's
manifest and only the Windows Event Log service can render it. `wevtutil qe
/f:RenderedXml` does that rendering, returns the structured EventData as well,
and takes ~0.5 s for 2000 events.

Qt-free, so it can be tested without a display.
"""
from __future__ import annotations

import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.types import LogEntry

logger = logging.getLogger(__name__)

_LEVELS = {1: "Critical", 2: "Error", 3: "Warning", 4: "Info", 0: "Info", 5: "Info"}
_NS = re.compile(r"\sxmlns='[^']*'")
_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: wevtutil's exit code when the channel needs elevation (ERROR_ACCESS_DENIED).
ACCESS_DENIED = 5


@dataclass
class QueryResult:
    log_name: str
    entries: List[LogEntry] = field(default_factory=list)
    #: None means the query ran. A string is why it did not, and is NEVER the
    #: same thing as "the log has no events".
    error: Optional[str] = None
    access_denied: bool = False
    truncated: bool = False


def _local_time(system_time: str) -> datetime:
    """`2026-09-26T06:00:48.6272778Z` (UTC) -> naive local datetime."""
    base = system_time[:19]
    moment = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return moment.astimezone().replace(tzinfo=None)


def _text(node) -> str:
    return (node.text or "").strip() if node is not None else ""


def parse_event_xml(xml_text: str, log_name: str = "") -> List[LogEntry]:
    """`<Event>...</Event>` blocks (as wevtutil prints them) -> `LogEntry` list.

    A block that does not parse is logged and skipped; the rest still load.
    """
    cleaned = _BAD_CHARS.sub("", _NS.sub("", xml_text))
    cleaned = re.sub(r"<\?xml[^>]*\?>", "", cleaned)
    try:
        root = ET.fromstring(f"<Events>{cleaned}</Events>")
    except ET.ParseError:
        logger.warning("Event XML did not parse as a whole; trying block by block", exc_info=True)
        return _parse_blockwise(cleaned, log_name)
    return [e for e in (_entry_from(node, log_name) for node in root) if e is not None]


def _parse_blockwise(cleaned: str, log_name: str) -> List[LogEntry]:
    out: List[LogEntry] = []
    for block in re.findall(r"<Event>.*?</Event>", cleaned, flags=re.S):
        try:
            entry = _entry_from(ET.fromstring(block), log_name)
        except ET.ParseError:
            logger.warning("Skipped one unparseable event block", exc_info=True)
            continue
        if entry is not None:
            out.append(entry)
    return out


def _entry_from(ev, log_name: str) -> Optional[LogEntry]:
    system = ev.find("System")
    if system is None:
        return None
    prov_node = system.find("Provider")
    provider = prov_node.get("Name", "") if prov_node is not None else ""
    # Classic providers carry a short display name ("DCOM"); manifest ones do not.
    source = (prov_node.get("EventSourceName") if prov_node is not None else "") or provider
    id_node = system.find("EventID")
    try:
        event_id = int(_text(id_node)) & 0xFFFF
    except ValueError:
        logger.debug("Event with no numeric ID skipped")
        return None
    tc = system.find("TimeCreated")
    try:
        when = _local_time(tc.get("SystemTime", "")) if tc is not None else datetime.now()
    except ValueError:
        logger.debug("Event with an unreadable time skipped")
        return None
    try:
        level = _LEVELS.get(int(_text(system.find("Level")) or 4), "Info")
    except ValueError:
        level = "Info"
    data: Dict[str, str] = {}
    for i, d in enumerate(ev.findall("./EventData/Data")):
        data[d.get("Name") or f"Data{i + 1}"] = _text(d)
    message = ""
    rendering = ev.find("RenderingInfo")
    if rendering is not None:
        message = (rendering.findtext("Message") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    rendered = bool(message)
    if not message:
        # The provider's message resource could not be found: say so, and show
        # the structured data instead of an empty cell.
        shown = "; ".join(f"{k}={v}" for k, v in data.items() if v)
        message = f"(message text unavailable for this provider) {shown}".strip()
    security = system.find("Security")
    execution = system.find("Execution")
    return LogEntry(
        timestamp=when,
        source=source or log_name,
        level=level,
        message=message,
        raw={
            "event_id": event_id,
            "log_name": _text(system.find("Channel")) or log_name,
            "provider": provider,
            "record_number": _text(system.find("EventRecordID")),
            "computer": _text(system.find("Computer")),
            "process_id": execution.get("ProcessID", "") if execution is not None else "",
            "user_sid": security.get("UserID", "") if security is not None else "",
            "message_rendered": rendered,
            "data": data,
        },
    )


#: Critical/Error/Warning, and the informational levels (LogAlways, Info, Verbose).
PROBLEM_LEVELS = "(Level=1 or Level=2 or Level=3)"
INFO_LEVELS = "(Level=0 or Level=4 or Level=5)"


def build_query(hours_back: Optional[int], levels: Optional[str] = None) -> str:
    """XPath restricting a channel to the last `hours_back` hours (None = all).

    `levels` is one of PROBLEM_LEVELS / INFO_LEVELS to fetch only those.
    """
    clauses = []
    if levels:
        clauses.append(levels)
    if hours_back:
        ms = int(hours_back) * 3600 * 1000
        clauses.append(f"TimeCreated[timediff(@SystemTime) <= {ms}]")
    if not clauses:
        return "*"
    return f"*[System[{' and '.join(clauses)}]]"


def _decode(raw: bytes) -> str:
    """wevtutil writes the system ANSI code page when piped, not UTF-8.

    Measured: "Good newsyou can turn on HDR" arrives as byte 0x97 (cp1252's
    em dash), which decoded as UTF-8 became U+FFFD. Try UTF-8 first (a future
    Windows may switch), then the ANSI code page.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("mbcs")
        except (LookupError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace")


def query_log(log_name: str, hours_back: Optional[int] = 24, max_events: int = 5000,
              timeout: int = 90, levels: Optional[str] = None,
              xpath: Optional[str] = None) -> QueryResult:
    """Newest-first events from one channel. Never raises; reports why it could not."""
    cmd = ["wevtutil", "qe", log_name, "/rd:true", f"/c:{int(max_events)}",
           "/f:RenderedXml", "/q:" + (xpath or build_query(hours_back, levels))]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("wevtutil could not be run for %s: %s", log_name, exc)
        return QueryResult(log_name, error=f"wevtutil could not be run: {exc}")
    text = _decode(proc.stdout)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip() or f"exit code {proc.returncode}"
        return QueryResult(log_name, error=detail, access_denied=proc.returncode == ACCESS_DENIED)
    entries = parse_event_xml(text, log_name)
    return QueryResult(log_name, entries=entries, truncated=len(entries) >= max_events)
