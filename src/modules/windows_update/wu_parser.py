import logging
import re
from datetime import datetime
from typing import Optional

from core.log_parser_base import LogParserBase
from core.types import LogEntry

logger = logging.getLogger(__name__)

# Keywords used to classify log level from content
_ERROR_KEYWORDS = ("error", "fail", "failed", "failure", "critical")
_WARNING_KEYWORDS = ("warn", "warning", "retry", "timeout", "notfound")

_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)

# Matches timezone suffix like +0200 or -0500 at end of string
_TZ_RE = re.compile(r"[+-]\d{4}$")
# Matches colon-separated milliseconds after HH:MM:SS, e.g. "19:52:03:825" -> "19:52:03"
_MS_COLON_RE = re.compile(r"(\d{2}:\d{2}:\d{2}):\d{1,4}$")


def _parse_timestamp(value: str) -> Optional[datetime]:
    value = value.strip()
    # Strip timezone offset (+0200, -0500, etc.)
    value = _TZ_RE.sub("", value).strip()
    # Strip colon-separated milliseconds (WU log format: "HH:MM:SS:mmm")
    value = _MS_COLON_RE.sub(r"\1", value).strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            logger.debug("_parse_timestamp: skipping an item that could not be read", exc_info=True)
            continue
    # Fallback: strip dot-fractional seconds
    if "." in value:
        base = value.split(".")[0]
        for fmt in _TIMESTAMP_FORMATS:
            try:
                return datetime.strptime(base, fmt)
            except ValueError:
                logger.debug("_parse_timestamp: skipping an item that could not be read", exc_info=True)
                continue
    return None


def _classify_level(fields: list) -> str:
    combined = " ".join(fields).lower()
    for kw in _ERROR_KEYWORDS:
        if kw in combined:
            return "Error"
    for kw in _WARNING_KEYWORDS:
        if kw in combined:
            return "Warning"
    return "Info"


_GUID = re.compile(r"^\s*\{[0-9A-Fa-f-]{36}\}\s*$")
_EVENT_NAME = re.compile(r"(\d+)\s*\[([^\]]*)\]")
_HEX_FIELD = re.compile(r"^(?:0x)?([0-9A-Fa-f]{1,8})$")
_FAIL_RESULTS = ("failure", "failed", "error")


class WUParser(LogParserBase):
    """Parser for C:\\Windows\\SoftwareDistribution\\ReportingEvents.log.

    Lines are tab-separated. The real file's records start with a GUID and are
    parsed structurally; any other tab-separated line falls back to the older
    "first field is a timestamp" reading.
    """

    def _parse_reporting_event(self, fields: list) -> Optional[LogEntry]:
        """The real ReportingEvents.log record.

        `{guid} ts 1 147 [EVENT_NAME] 101 {guid} status hresult product result
        category message id` -- twelve-plus tab-separated fields, of which the
        old parser showed the GUID as the "source" and the whole tail as one
        unreadable message. The HRESULT sits in field 7 as bare hex.
        """
        if len(fields) < 12 or not _GUID.match(fields[0]):
            return None
        timestamp = _parse_timestamp(fields[1])
        name = _EVENT_NAME.search(fields[3])
        if timestamp is None or name is None:
            return None
        event_name = name.group(2)
        product, result, category, text = (f.strip() for f in fields[8:12])
        code_match = _HEX_FIELD.match(fields[7].strip())
        code = int(code_match.group(1), 16) if code_match else 0
        failed = (result.lower() in _FAIL_RESULTS or event_name.endswith("FAILED")
                  or bool(code & 0x80000000))
        level = "Error" if failed else "Info"
        message = f"{product}: {text}" if product and text else (text or product or event_name)
        return LogEntry(
            timestamp=timestamp,
            source=category or "Windows Update",
            level=level,
            message=message,
            raw={"event_name": event_name, "event_id": int(name.group(1)), "product": product,
                 "result": result, "category": category, "hresult": code,
                 "update_id": fields[5].strip(), "fields": fields},
        )

    def parse_line(self, line: str) -> Optional[LogEntry]:
        if not line.strip():
            return None

        fields = line.split("\t")
        if len(fields) < 2:
            return None

        structured = self._parse_reporting_event(fields)
        if structured is not None:
            return structured

        # Try to extract timestamp from first field
        timestamp = _parse_timestamp(fields[0])
        if timestamp is None:
            # Try second field in case first is an index or GUID
            if len(fields) >= 2:
                timestamp = _parse_timestamp(fields[1])
            if timestamp is None:
                logger.debug("Could not parse timestamp from: %s", fields[0])
                return None

        # source is next available non-timestamp field
        source = ""
        message_parts = []
        ts_field_idx = 0
        # Determine which field held the timestamp
        if _parse_timestamp(fields[0]) is not None:
            ts_field_idx = 0
        else:
            ts_field_idx = 1

        remaining = [f.strip() for i, f in enumerate(fields) if i != ts_field_idx]
        if remaining:
            source = remaining[0]
        if len(remaining) > 1:
            message_parts = remaining[1:]

        message = " | ".join(p for p in message_parts if p)
        level = _classify_level(fields)

        return LogEntry(
            timestamp=timestamp,
            source=source if source else "WindowsUpdate",
            level=level,
            message=message if message else line.strip(),
            raw={"fields": fields},
        )
