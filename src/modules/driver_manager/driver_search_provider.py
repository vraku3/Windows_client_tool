"""Driver Manager's global-search provider.

Unlike DebloatSearchProvider (which re-reads static catalog JSON on every
search — see debloat_search_provider.py's own docstring for why that one
needs no live handle), there is no catalog of drivers to fall back on: the
only data that exists is whatever the last refresh loaded. So this provider
is constructed with the SAME single-element list cell driver_module.py
mutates on every refresh (`self._drivers_ref`, never reassigned after
__init__) rather than a snapshot taken when the provider itself was built.
Before a first refresh that cell holds `[]`, so search answers nothing --
matching the app's documented pattern that "a provider is fed by [live
state], answering only after a tab has been opened once."
"""
from __future__ import annotations

import datetime
from typing import List

from core.search_provider import FilterField, SearchProvider, SearchQuery, SearchResult


class DriverSearchProvider(SearchProvider):
    """Currently-loaded drivers, searchable by device name/class."""

    module_name = "Driver Manager"

    def __init__(self, drivers_ref):
        self._drivers_ref = drivers_ref  # the same [list] cell driver_module.py mutates

    def search(self, query: SearchQuery) -> List[SearchResult]:
        text = (query.text or "").strip().lower()
        if not text:
            return []
        results: List[SearchResult] = []
        now = datetime.datetime.now()
        for d in self._drivers_ref[0]:
            if text in d.device_name.lower() or text in d.driver_class.lower():
                results.append(SearchResult(
                    timestamp=now, source="Driver Manager",
                    type="driver", summary=f"{d.device_name} ({d.driver_class})",
                    detail={"version": d.version, "signed": d.signed,
                           "flags": d.flags},
                    relevance=2.0 if text in d.device_name.lower() else 1.0))
        return results

    def get_filterable_fields(self) -> List[FilterField]:
        return []
