"""Debloat's global-search provider: the 126-app catalog and 219 tweaks.

A fresh instance is built per search (`get_search_provider()` returns a new
one), so this reads the same static JSON files DebloatToolsModule loads
rather than needing a live handle into the module's runtime state —
catalog search doesn't need to know what's actually installed to be
useful; it needs to help someone find "is there a tweak for X" before
they've even opened the tab.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import List

from core.search_provider import FilterField, SearchProvider, SearchQuery, SearchResult

logger = logging.getLogger(__name__)

_DEFS_DIR = os.path.join(os.path.dirname(__file__), "..", "tweaks", "definitions")
_TWEAK_FILES = ("privacy.json", "telemetry.json", "services.json",
                "network.json", "ai_features.json", "navigation.json")


def _load_json(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load %s: %s", path, exc)
        return []


def _relevance(query: str, name: str) -> float:
    """Exact match ranks highest, then "starts with", then "contains"."""
    lowered = name.lower()
    if lowered == query:
        return 3.0
    if lowered.startswith(query):
        return 2.0
    if query in lowered:
        return 1.0
    return 0.0


class DebloatSearchProvider(SearchProvider):
    """Every catalogued app and tweak, searchable by name/category."""

    module_name = "Debloat"

    def search(self, query: SearchQuery) -> List[SearchResult]:
        text = (query.text or "").strip().lower()
        if not text:
            return []

        results: List[SearchResult] = []
        now = datetime.now()

        apps = _load_json(os.path.join(_DEFS_DIR, "debloat.json"))
        for entry in apps:
            score = max(_relevance(text, entry.get("name", "")),
                       _relevance(text, entry.get("category", "")) * 0.5)
            if score <= 0:
                continue
            results.append(SearchResult(
                timestamp=now, source="Debloat", type="app",
                summary=f"{entry.get('name', '')} ({entry.get('category', '')})",
                detail={"entry_id": entry.get("id", ""), "kind": "app",
                       "package": entry.get("package", ""),
                       "category": entry.get("category", "")},
                relevance=score))

        for fname in _TWEAK_FILES:
            for tweak in _load_json(os.path.join(_DEFS_DIR, fname)):
                score = max(_relevance(text, tweak.get("name", "")),
                           _relevance(text, tweak.get("category", "")) * 0.5)
                if score <= 0:
                    continue
                results.append(SearchResult(
                    timestamp=now, source="Debloat", type="tweak",
                    summary=f"{tweak.get('name', '')} ({tweak.get('category', '')})",
                    detail={"entry_id": tweak.get("id", ""), "kind": "tweak",
                           "category": tweak.get("category", ""),
                           "risk": tweak.get("risk", "")},
                    relevance=score))

        results.sort(key=lambda r: -r.relevance)
        return results

    def get_filterable_fields(self) -> List[FilterField]:
        return [FilterField(name="type", label="Kind",
                            values=["app", "tweak"])]
