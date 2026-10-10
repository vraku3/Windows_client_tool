"""App Buster in the app's global search bar.

Reads the pane's LIVE list on every search (like Store Apps' provider), so a
provider registered once at startup sees every rescan. Before App Buster has
been opened there is no list yet, and it answers nothing rather than running
a 1-second scan inside someone's keystroke.
"""
from __future__ import annotations

import datetime
from typing import List

from core.search_provider import FilterField, SearchProvider, SearchQuery, SearchResult

from .engine import model as m
from .engine import views


class AppBusterSearchProvider(SearchProvider):
    module_name = "App Buster"

    def __init__(self, module) -> None:
        self._module = module

    def search(self, query: SearchQuery) -> List[SearchResult]:
        text = (query.text or "").strip()
        widget = getattr(self._module, "_widget", None)
        rows = list(getattr(widget, "_rows", []) or [])
        if not text or not rows:
            return []
        now = datetime.datetime.now()
        out: List[SearchResult] = []
        for r in rows:
            if not views.matches(r, text):
                continue
            notes = [r.type]
            if r.recommendation == m.REMOVE and r.status == m.INSTALLED:
                notes.append("recommended for removal")
            if r.update:
                notes.append(f"update {r.update}")
            if r.status != m.INSTALLED:
                notes.append(r.status)
            out.append(SearchResult(
                timestamp=r.installed or now, source="App Buster", type="app",
                summary=f"{r.name} — {', '.join(notes)}",
                detail={"publisher": r.publisher, "version": r.version,
                        "location": r.install_location, "key": r.key},
                relevance=2.0 if text.lower() in r.name.lower() else 1.0))
        return out

    def get_filterable_fields(self) -> List[FilterField]:
        return [FilterField("type", "Type", list(m.TYPES))]
