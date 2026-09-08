"""Store Apps' global-search provider.

Same reasoning as driver_search_provider.py: there is no static catalog to
fall back on here, only whatever the last scan found, so this needs a LIVE
handle into the module's runtime state rather than a snapshot taken when the
provider itself was built (contrast DebloatSearchProvider, which re-reads
static catalog JSON per search and documents why it doesn't need one). It is
constructed with the module instance itself and reads `module._apps` fresh
on every search, so a provider built once at startup still sees every
refresh from then on.
"""
from __future__ import annotations

import datetime
from typing import List

from core.search_provider import FilterField, SearchProvider, SearchQuery, SearchResult


class StoreAppsSearchProvider(SearchProvider):
    """Currently-loaded Store apps, searchable by name/publisher."""

    module_name = "Store Apps"

    def __init__(self, module):
        self._module = module  # StoreAppsModule -- read module._apps live

    def search(self, query: SearchQuery) -> List[SearchResult]:
        text = (query.text or "").strip().lower()
        if not text:
            return []
        results: List[SearchResult] = []
        now = datetime.datetime.now()
        for app in self._module._apps:
            name = app.get("Name", "")
            publisher = app.get("Publisher", "")
            if text in name.lower() or text in publisher.lower():
                results.append(SearchResult(
                    timestamp=now, source="Store Apps",
                    type="app", summary=f"{name} ({publisher})" if publisher else name,
                    detail={"version": app.get("Version", ""),
                           "publisher": publisher,
                           "install_location": app.get("InstallLocation", "")},
                    relevance=2.0 if text in name.lower() else 1.0))
        return results

    def get_filterable_fields(self) -> List[FilterField]:
        return []
