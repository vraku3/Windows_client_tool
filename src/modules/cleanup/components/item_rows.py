"""Qt-free, read-only presentation facts shared by Cleanup's result views.

Roles are plain integers so a Qt host can use them without importing Qt here.
Selection policy belongs to the host; presenting an item never mutates it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
from importlib import import_module
import logging
from typing import Any, Iterable

from modules.cleanup.cleanup_scanner._common import ScanItem, format_size

logger = logging.getLogger(__name__)

SIZE_ROLE = 257
SAFETY_ROLE = 258
SHARE_ROLE = 259
FACTS_ROLE = 260
COLLAPSE_THRESHOLD = 20
BADGES = {"safe": "✓ Safe", "caution": "! Review", "danger": "✕ Risky"}
_RANK = {"safe": 0, "caution": 1, "danger": 2}


def knowledge_api():
    """Resolve WP1 lazily so importing this presenter needs no KB files."""
    name = "modules.cleanup.cleanup_scanner.knowledge"
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        logger.debug("Cleanup research loader is not installed yet")
        return None


def _safety(value: str) -> str:
    # Unknown safety must never acquire a reassuring badge.
    return value if value in _RANK else "danger"


@dataclass(frozen=True)
class RowFacts:
    label: str
    path: str = ""
    size_bytes: int = 0
    safety: str = "safe"
    catalog_safety: str = "safe"
    catalog_id: str = ""
    category: str = ""
    description: str = ""
    selected: bool = False
    share_percent: float = 0.0
    subtitle: str = ""
    verdict: str = "Not yet researched"
    chips: tuple[str, ...] = ()
    tooltip: str = ""
    is_dir: bool = False
    keep_root: bool = True
    keep_newer_than_hours: int = 0
    unselected_reason: str = ""
    entry: Any = None
    kind: str = "item"
    children: tuple[RowFacts, ...] = ()

    @property
    def badge_text(self) -> str:
        return BADGES[_safety(self.safety)]

    @property
    def size_text(self) -> str:
        return format_size(self.size_bytes)

    @property
    def size_sort_value(self) -> int:
        return self.size_bytes

    @property
    def research_more_cautious(self) -> bool:
        return _RANK[_safety(self.safety)] > _RANK[_safety(self.catalog_safety)]

    @classmethod
    def from_item(
        cls,
        item: ScanItem,
        label: str = "",
        *,
        catalog_id: str = "",
        category: str = "",
        description: str = "",
        total_bytes: int = 0,
        catalog_safety: str | None = None,
        in_use_by: Iterable[str] = (),
        also_on: Iterable[str] = (),
        cloud_skipped: bool = False,
        unselected_reason: str = "",
    ) -> RowFacts:
        """Present scanner facts plus optional catalog/context facts, without I/O."""
        api = knowledge_api()
        entry = api.entry_for(catalog_id) if api and catalog_id else None
        catalog = max((_safety(item.safety), _safety(catalog_safety or item.safety)), key=_RANK.get)
        researched = api.effective_safety(catalog, entry) if api else catalog
        # Defence in depth: even an erroneous loader cannot relax catalog safety.
        safety = max((catalog, _safety(researched)), key=_RANK.get)
        chips = [f"In use by {name}" for name in in_use_by]
        chips.extend(f"Also on {tab}" for tab in also_on)
        if cloud_skipped:
            chips.append("Cloud files skipped")
        if item.keep_newer_than_hours:
            chips.append(f"Kept: newer than {item.keep_newer_than_hours} h")
        verdict = "Not yet researched"
        if entry is not None:
            if entry.regenerates is True:
                verdict = "Rebuilt automatically"
            elif entry.regenerates is False:
                verdict = "Deleted data does not come back"
            else:
                verdict = entry.used_for or "Not yet researched"
        title = label or item.path
        size = max(0, int(item.size))
        tooltip = (
            api.tooltip_html(entry, title, safety, format_size(size))
            if api
            else f"<b>{escape(title)}</b><p>{escape(description)}</p><p>Not yet researched</p>"
        )
        if api and entry is None and description:
            tooltip += f"<p>{escape(description)}</p>"
        return cls(
            label=title,
            path=item.path,
            size_bytes=size,
            safety=safety,
            catalog_safety=catalog,
            catalog_id=catalog_id,
            category=category,
            description=description,
            selected=bool(item.selected),
            share_percent=min(100.0, 100.0 * size / total_bytes) if total_bytes > 0 else 0.0,
            subtitle=entry.what if entry is not None else description,
            verdict=verdict,
            chips=tuple(chips),
            tooltip=tooltip,
            is_dir=item.is_dir,
            keep_root=item.keep_root,
            keep_newer_than_hours=item.keep_newer_than_hours,
            unselected_reason=unselected_reason,
            entry=entry,
        )

    def item_facts(self) -> dict[str, Any]:
        """Plain facts for WP1's detail_html(entry, item_facts) contract."""
        return {
            "label": self.label,
            "path": self.path,
            "paths": [self.path] if self.path else [],
            "size": self.size_bytes,
            "size_text": self.size_text,
            "share_percent": self.share_percent,
            "share": f"{self.share_percent:.1f}%",
            "safety": self.safety,
            "safety_badge": self.badge_text,
            "catalog_safety": self.catalog_safety,
            "catalog_id": self.catalog_id,
            "id": self.catalog_id,
            "category": self.category,
            "description": self.description,
            "is_dir": self.is_dir,
            "keep_root": self.keep_root,
            "keep_newer_than_hours": self.keep_newer_than_hours,
            "selected": self.selected,
            "unselected_reason": self.unselected_reason,
        }


def collapse_rows(
    rows: Iterable[RowFacts], *, expanded: bool = False, threshold: int = COLLAPSE_THRESHOLD
) -> list[RowFacts]:
    """Fold a long file-only group; retained children remain available to the host.

    Synthetic rows cannot be selected or offered to the deleter. The host expands
    the show_all row by calling again with expanded=True; no scan is necessary.
    """
    rows = list(rows)
    if expanded or len(rows) <= threshold or any(row.is_dir or row.kind != "item" for row in rows):
        return rows
    total = sum(row.size_bytes for row in rows)
    safety = max((_safety(row.safety) for row in rows), key=_RANK.get)
    summary = RowFacts(
        label=f"{len(rows)} files, {format_size(total)}",
        size_bytes=total,
        safety=safety,
        catalog_safety=safety,
        kind="summary",
        share_percent=min(100.0, sum(row.share_percent for row in rows)),
        children=tuple(rows),
        verdict="Expand to review individual files",
    )
    return [
        summary,
        replace(
            summary,
            label=f"Show all {len(rows)} files",
            kind="show_all",
            size_bytes=0,
            share_percent=0.0,
        ),
    ]
