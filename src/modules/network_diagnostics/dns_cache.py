"""The local DNS resolver cache, browsable. Qt-free: parses `ipconfig
/displaydns`, the same command `network_fixes.dns_cache_count` already
shells out to for its before/after count -- this reads the same output
in full instead of just counting "Record Name" lines.

Confirmed live: entries repeat "Record Name" once per record when a name
has several (an A and an AAAA for the same host print as two separate
blocks, each with its own Record Name line) -- no need to group by name,
each printed record is a distinct row in its own right, same as the cache
itself treats them.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: ipconfig prints the type as a bare number; these are the ones actually
#: seen in real caches (A/AAAA) plus the other common record types so an
#: unusual one still gets a name instead of just its number.
TYPE_NAMES: Dict[int, str] = {
    1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 12: "PTR", 15: "MX",
    16: "TXT", 28: "AAAA", 33: "SRV", 65: "HTTPS", 64: "SVCB",
}

_FIELD = re.compile(r"^\s*(.+?)\s*(?:\.\s*){2,}:\s*(.*?)\s*$")
_KNOWN_LABELS = {"record name", "record type", "time to live", "data length", "section"}


@dataclass
class DnsCacheEntry:
    name: str
    record_type: Optional[int]
    type_name: str
    ttl: Optional[int]
    section: str
    data: str


def _to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_displaydns(text: str) -> List[DnsCacheEntry]:
    """Every record `ipconfig /displaydns` printed, in order.

    Each record block has a fixed set of labelled fields (Record Name,
    Record Type, Time To Live, Data Length, Section) plus exactly one more
    line whose LABEL depends on the record type ("A (Host) Record",
    "AAAA Record", "CNAME Record", ...) -- that one extra line is always
    the data, so it is captured by exclusion rather than a type-specific
    label list that would need extending for every record type Windows
    might ever print.
    """
    entries: List[DnsCacheEntry] = []
    fields: Dict[str, str] = {}

    def flush() -> None:
        if not fields.get("record name"):
            return
        rtype = _to_int(fields.get("record type"))
        entries.append(DnsCacheEntry(
            name=fields["record name"],
            record_type=rtype,
            type_name=TYPE_NAMES.get(rtype, str(rtype) if rtype is not None else "?"),
            ttl=_to_int(fields.get("time to live")),
            section=fields.get("section", ""),
            data=fields.get("_data", ""),
        ))

    for raw in text.splitlines():
        match = _FIELD.match(raw)
        if match is None:
            if not raw.strip() and fields:
                flush()
                fields = {}
            continue
        label, value = match.group(1).strip().lower(), match.group(2).strip()
        if label in _KNOWN_LABELS:
            fields[label] = value
        else:
            fields["_data"] = value
    flush()
    return entries


def read_dns_cache(timeout: int = 30) -> Optional[List[DnsCacheEntry]]:
    """None means the read failed or was refused; an empty list means the
    cache itself is genuinely empty -- the two are not the same answer."""
    try:
        result = subprocess.run(
            ["ipconfig", "/displaydns"], capture_output=True, text=True, errors="replace",
            timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ipconfig /displaydns failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.warning("ipconfig /displaydns refused: %s",
                       (result.stderr or result.stdout or "").strip()[:200])
        return None
    return parse_displaydns(result.stdout)
