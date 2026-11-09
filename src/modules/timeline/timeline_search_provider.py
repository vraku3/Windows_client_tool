"""Search provider for the Unified Change Timeline."""
from __future__ import annotations

import re
from typing import List

from core.search_provider import FilterField, SearchProvider, SearchQuery, SearchResult
from core.types import LogEntry


class TimelineSearchProvider(SearchProvider):
    module_name = "Timeline"

    def __init__(self) -> None:
        self._entries: List[LogEntry] = []

    def set_entries(self, entries: List[LogEntry]) -> None:
        self._entries = entries

    def get_filterable_fields(self) -> List[FilterField]:
        return [
            FilterField(name="level", label="Severity", values=["Info", "Warning", "Error", "Critical"]),
            FilterField(name="source", label="Source", values=[]),
        ]

    def search(self, query: SearchQuery) -> List[SearchResult]:
        q = (query.text or "").casefold()
        results = []
        for entry in self._entries:
            if query.severity and entry.level.casefold() != query.severity.casefold():
                continue
            if query.sources and not any(s.casefold() in entry.source.casefold() for s in query.sources):
                continue
            if q and q not in entry.message.casefold() and q not in entry.source.casefold():
                continue
            results.append(SearchResult(
                timestamp=entry.timestamp,
                source=entry.source,
                type=entry.level,
                summary=entry.message[:200],
                detail=entry.raw,
                relevance=1.0,
            ))
        return results
