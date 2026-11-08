import re
from typing import List

from core.search_provider import FilterField, SearchProvider, SearchQuery, SearchResult
from modules.quick_fix.fix_actions import ALL_ACTIONS, FixAction
from modules.quick_fix.browser_links_card import BrowserLinksCard

_ALL = [BrowserLinksCard.ACTION] + list(ALL_ACTIONS)


class QuickFixSearchProvider(SearchProvider):
    """Makes repair actions discoverable via the global search bar."""

    module_name = "Quick Fix"

    def search(self, query: SearchQuery) -> List[SearchResult]:
        results: List[SearchResult] = []
        for action in _ALL:
            if not self._matches(query, action):
                continue
            results.append(SearchResult(
                timestamp="",
                source="Quick Fix",
                type=action.category,
                summary=action.title,
                detail=action.description,
                relevance=1.0,
            ))
        return results

    @staticmethod
    def _matches(query: SearchQuery, action: FixAction) -> bool:
        if not query.text:
            return True
        haystack = f"{action.title} {action.description} {action.category}"
        if query.regex_enabled:
            try:
                return bool(re.search(query.text, haystack, re.IGNORECASE))
            except re.error:
                return False
        return query.text.lower() in haystack.lower()

    def get_filterable_fields(self) -> List[FilterField]:
        categories = sorted({a.category for a in _ALL})
        return [
            FilterField(name="category", label="Category", values=categories),
        ]
