"""The extra HTML the Event Viewer detail panel shows above an event's own fields.

Qt-free: it builds a string. Every piece of log text is escaped, because event
messages routinely contain `<` and `&`.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import List, Optional

from core.types import LogEntry
from modules.diagnose.knowledge import lookup_event
from modules.event_viewer import event_analysis as ea


def _esc(text) -> str:
    return html.escape(str(text))


def knowledge_html(entry: LogEntry) -> str:
    eid = ea.event_id(entry)
    if eid is None:
        return ""
    info = lookup_event(ea.provider_of(entry), eid) or lookup_event(entry.source, eid)
    if info is None:
        return ""
    out = [f"<b>{_esc(info.title)}</b><br>{_esc(info.meaning)}<br>",
           f"<i>Usual cause:</i> {_esc(info.cause)}<br>"]
    if info.next_step:
        out.append(f"<i>Look at next:</i> {_esc(info.next_step)}<br>")
    return "".join(out) + "<hr>"


def frequency_html(entry: LogEntry, entries: List[LogEntry], now: Optional[datetime] = None) -> str:
    raw = entry.raw or {}
    if raw.get("group"):
        same = [e for e in entries if not e.raw.get("group")
                and (ea.provider_of(e), ea.event_id(e)) == (raw.get("provider"), raw.get("event_id"))]
        head = f"<b>{raw.get('count')} occurrences</b>, {_esc(raw.get('first'))} to {_esc(raw.get('last'))}<br>"
        if not same:
            return head + "<hr>"
        freq = ea.frequency(entries, same[0], now)
    else:
        if ea.event_id(entry) is None:
            return ""
        freq = ea.frequency(entries, entry, now)
        head = (f"<b>Frequency:</b> {freq.total} in the loaded range "
                f"({freq.last_hour} in the last hour, {freq.last_24h} in the last 24 h)<br>")
    spark = f"<span style='font-family: Consolas, monospace;'>{_esc(freq.spark)}</span><br>" if freq.spark else ""
    return head + spark + "<hr>"


def correlated_html(entry: LogEntry, entries: List[LogEntry], window_s: int = 60) -> str:
    if (entry.raw or {}).get("group"):
        return ""
    near = ea.correlate(entries, entry, window_s)
    if not near:
        return f"<b>Within {window_s} s:</b> no other warnings or errors.<hr>"
    rows = [f"<b>Also within {window_s} s (warnings and worse):</b><br>"]
    seen = {}
    for offset, other in near:
        key = (ea.provider_of(other), ea.event_id(other))
        seen[key] = seen.get(key, 0) + 1
    shown = set()
    for offset, other in near:
        key = (ea.provider_of(other), ea.event_id(other))
        if key in shown:
            continue
        if len(shown) >= 12:
            break
        shown.add(key)
        eid = ea.event_id(other)
        label = f"{_esc(other.source)}" + (f" {eid}" if eid is not None else "")
        snippet = " ".join((other.message or "").split())[:90]
        times = f" (x{seen[key]})" if seen[key] > 1 else ""
        rows.append(f"{offset:+.0f}s &nbsp;[{_esc(other.level)}] {label}{times} - {_esc(snippet)}<br>")
    return "".join(rows) + "<hr>"


def detail_html(entry: LogEntry, entries: List[LogEntry], now: Optional[datetime] = None) -> str:
    return knowledge_html(entry) + frequency_html(entry, entries, now) + correlated_html(entry, entries)
