"""Smart Views, search, sort, grouping, the Select menu and the app-information
fields -- everything the list decides, without Qt.

O&O AppBuster's behaviour, adopted:

* Smart View chips: All, Updates available, Recommended removal, Recently
  installed, Newly discovered, Hidden, Orphaned, Defect, System, Framework.
  A chip with nothing in it is hidden (All never is).
* Search matches name and description, as you type. When the current view
  has no match but another does, the list moves to the first view with one.
* View options hide apps: "Show Microsoft apps" and "Show installable
  Windows apps". What they hide is counted, so a notice can say so.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import model as m

RECENT_DAYS = 30


def _recent(r: m.AppRecord, now: Optional[datetime] = None) -> bool:
    if r.installed is None or r.status in (m.INSTALLABLE, m.STAGED):
        return False
    return r.installed >= (now or datetime.now()) - timedelta(days=RECENT_DAYS)


#: (key, label, predicate(row, new_keys))
SMART_VIEWS: Tuple[Tuple[str, str, Callable], ...] = (
    ("all", "All", lambda r, new: True),
    ("updates", "Updates available", lambda r, new: bool(r.update)),
    ("remove", "Recommended removal", lambda r, new: r.recommendation == m.REMOVE
     and r.status == m.INSTALLED),
    ("recent", "Recently installed", lambda r, new: _recent(r)),
    ("new", "Newly discovered", lambda r, new: r.key in new),
    ("hidden", "Hidden", lambda r, new: r.hidden and r.type in (m.WINDOWS, m.DESKTOP)),
    ("orphaned", "Orphaned", lambda r, new: r.type == m.ORPHANED),
    ("defect", "Defect", lambda r, new: r.type == m.DEFECT),
    ("system", "System", lambda r, new: r.type == m.SYSTEM),
    ("framework", "Framework", lambda r, new: r.type == m.FRAMEWORK),
)

#: App-information fields: (key, label, default on). Name is always shown.
FIELDS: Tuple[Tuple[str, str, bool], ...] = (
    ("recommendation", "Recommendation", True),
    ("publisher", "Publisher", True),
    ("storage", "Storage", True),
    ("status", "Status", False),
    ("installed", "Installed", True),
    ("version", "Version", True),
    ("update", "Update", False),
    ("available", "Available", False),
    ("type", "Type", False),
)

SORTS: Tuple[Tuple[str, str], ...] = (
    ("recent", "Recently installed"), ("name", "Name"), ("storage", "Storage"),
    ("publisher", "Publisher"), ("recommendation", "Recommendation"), ("version", "Version"),
    ("status", "Status"), ("available", "Available"), ("type", "Type"),
    ("updates", "Updates available"),
)

#: The sort a Details column header maps to.
FIELD_SORT = {"name": "name", "recommendation": "recommendation", "publisher": "publisher",
              "storage": "storage", "status": "status", "installed": "recent",
              "version": "version", "update": "updates", "available": "available", "type": "type"}


class ViewOptions:
    def __init__(self, show_microsoft: bool = True, show_installable: bool = False) -> None:
        self.show_microsoft = show_microsoft
        self.show_installable = show_installable


def is_microsoft(r: m.AppRecord) -> bool:
    return r.publisher_id in ("8wekyb3d8bbwe", "cw5n1h2txyewy") or \
        r.publisher.lower().startswith("microsoft")


def passes_options(r: m.AppRecord, opts: ViewOptions) -> bool:
    if not opts.show_installable and r.status in (m.INSTALLABLE, m.STAGED):
        return False
    if not opts.show_microsoft and is_microsoft(r) and r.type in (m.WINDOWS, m.DESKTOP):
        return False
    return True


def hidden_by_options(rows: Sequence[m.AppRecord], opts: ViewOptions) -> int:
    return sum(1 for r in rows if not passes_options(r, opts))


def matches(r: m.AppRecord, text: str) -> bool:
    needle = (text or "").strip().lower()
    if not needle:
        return True
    hay = f"{r.name}\n{r.description}\n{r.publisher}\n{r.package_name}".lower()
    return all(word in hay for word in needle.split())


def _view_fn(key: str) -> Callable:
    return next((fn for k, _l, fn in SMART_VIEWS if k == key), SMART_VIEWS[0][2])


def view_counts(rows: Sequence[m.AppRecord], new_keys, opts: ViewOptions,
                text: str = "") -> Dict[str, int]:
    shown = [r for r in rows if passes_options(r, opts) and matches(r, text)]
    return {k: sum(1 for r in shown if fn(r, new_keys)) for k, _l, fn in SMART_VIEWS}


def visible_views(counts: Dict[str, int]) -> List[str]:
    """Chips worth showing: All, and every other view with something in it."""
    return [k for k, _l, _f in SMART_VIEWS if k == "all" or counts.get(k, 0) > 0]


def pick_view(current: str, counts: Dict[str, int]) -> str:
    """Stay on the current view while it has results; otherwise move to the
    first view that does (search found something elsewhere)."""
    if counts.get(current, 0) > 0:
        return current
    return next((k for k, _l, _f in SMART_VIEWS if counts.get(k, 0) > 0), current)


def _version_key(v: str) -> tuple:
    parts = []
    for p in (v or "").replace("-", ".").split("."):
        parts.append(int(p) if p.isdigit() else -1)
    return tuple(parts)


_STATUS_ORDER = {m.INSTALLED: 0, m.INSTALLABLE: 1, m.STAGED: 2, m.UNREMOVABLE: 3}


def sort_key(sort: str) -> Callable[[m.AppRecord], tuple]:
    """Each key sorts ASCENDING; `descending` reverses it. "Recently installed"
    and "Storage" read naturally largest/newest first, which the caller's
    default direction handles."""
    name = lambda r: r.name.lower()                      # noqa: E731
    keys = {
        "recent": lambda r: (r.installed or datetime.min, name(r)),
        "name": lambda r: (name(r),),
        "storage": lambda r: (r.storage if r.storage is not None else -1, name(r)),
        "publisher": lambda r: (r.publisher.lower() or "~", name(r)),
        "recommendation": lambda r: (m.RECOMMENDATION_RANK.get(r.recommendation, 9), name(r)),
        "version": lambda r: (_version_key(r.version), name(r)),
        "status": lambda r: (_STATUS_ORDER.get(r.status, 9), name(r)),
        "available": lambda r: (r.available, name(r)),
        "type": lambda r: (m.TYPES.index(r.type) if r.type in m.TYPES else 9, name(r)),
        "updates": lambda r: (0 if r.update else 1, name(r)),
    }
    return keys.get(sort, keys["name"])


#: Sorts whose natural reading is largest / newest first.
DESCENDING_BY_DEFAULT = {"recent", "storage"}


def arrange(rows: Sequence[m.AppRecord], view: str, text: str, opts: ViewOptions,
            new_keys, sort: str, descending: bool, type_tab: str = "") -> List[m.AppRecord]:
    fn = _view_fn(view)
    kept = [r for r in rows if passes_options(r, opts) and matches(r, text) and fn(r, new_keys)
            and (not type_tab or r.type == type_tab)]
    kept.sort(key=sort_key(sort), reverse=descending)
    return kept


def type_tabs(rows: Sequence[m.AppRecord]) -> List[Tuple[str, int]]:
    """(type, count) for "Group by type", in the manual's order."""
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r.type] = counts.get(r.type, 0) + 1
    return [(t, counts[t]) for t in m.TYPES if counts.get(t)]


# ---- Select menu ---------------------------------------------------------------------------

SELECTIONS: Tuple[Tuple[str, str, Callable], ...] = (
    ("remove", "Select all apps recommended for removal",
     lambda r: r.recommendation == m.REMOVE),
    ("optional", "Select all optionally recommended apps", lambda r: r.recommendation == m.OPTIONAL),
    ("windows", "Select all Windows apps", lambda r: r.type == m.WINDOWS),
    ("desktop", "Select all desktop apps", lambda r: r.type == m.DESKTOP),
    ("all", "Select all", lambda r: True),
)


def selectable(r: m.AppRecord) -> bool:
    """Only rows an action can take: never system / framework / unremovable."""
    return r.can_uninstall or r.can_install or bool(r.update)


def select(rows: Sequence[m.AppRecord], which: str) -> List[str]:
    """Keys a Select-menu entry picks among `rows` (the visible list). "Select
    all" takes anything an action applies to; the others pick apps to remove."""
    fn = next((f for k, _l, f in SELECTIONS if k == which), None)
    if fn is None:
        return []
    if which == "all":
        return [r.key for r in rows if selectable(r)]
    return [r.key for r in rows if r.can_uninstall and r.status == m.INSTALLED and fn(r)]


# ---- formatting ----------------------------------------------------------------------------

def human_bytes(n: Optional[int]) -> str:
    if n is None:
        return ""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def field_text(r: m.AppRecord, key: str) -> str:
    if key == "name":
        return r.name
    if key == "storage":
        if r.storage is not None:
            return human_bytes(r.storage)
        return "…" if r.status == m.INSTALLED and not r.extra.get("measured") else "—"
    if key == "installed":
        return f"{r.installed:%Y-%m-%d}" if r.installed else ""
    if key == "update":
        return f"→ {r.update}" if r.update else ""
    return str(getattr(r, key, "") or "")


def subline(r: m.AppRecord, fields: Sequence[str]) -> str:
    """The card's second line: the chosen facts, in order, empty ones skipped."""
    parts = []
    for key in fields:
        if key == "recommendation":
            continue                    # the card draws it as a badge
        text = field_text(r, key)
        if text:
            parts.append(f"Installed {text}" if key == "installed" else
                         f"Update {r.update}" if key == "update" else text)
    return "  ·  ".join(parts)


def properties(r: m.AppRecord) -> List[Tuple[str, str]]:
    """The Properties dialog, as (label, value) rows. Copied as shown."""
    rows = [("Name", r.name), ("Type", r.type), ("Status", r.status),
            ("Installed", f"{r.installed:%Y-%m-%d %H:%M}" if r.installed else "not recorded"),
            ("Version", r.version or "not recorded"), ("Platform", r.architecture or "not recorded"),
            ("Publisher", r.publisher or "not recorded"),
            ("App files", human_bytes(r.files_bytes) or "not measured"),
            ("App data", human_bytes(r.data_bytes) or ("none" if r.data_bytes == 0 else "not measured")),
            ("Total", human_bytes(r.storage) or "not measured"),
            ("Install location", r.install_location or "not recorded"),
            ("Available to", r.available or "nobody"),
            ("Recommendation", f"{r.recommendation} -- {r.extra.get('why', '')}".strip(" -"))]
    if r.description:
        rows.insert(1, ("Description", r.description))
    if r.update:
        rows.append(("Update", f"{r.update} via winget ({r.winget_id})"))
    if r.full_name:
        rows.append(("Package", r.full_name))
    if r.family:
        rows.append(("Package family", r.family))
    if r.signature:
        rows.append(("Signature", r.signature))
    if r.registry_key:
        rows.append(("Registry", r.registry_key))
    if r.product_code:
        rows.append(("MSI product code", r.product_code))
    if r.uninstall_string:
        rows.append(("Uninstall command", r.uninstall_string))
    if r.type in (m.ORPHANED, m.DEFECT):
        rows += [("Why", r.reason), ("Readable name", r.name),
                 ("Original identifier", r.extra.get("original identifier", r.family or r.product_code
                                                      or r.registry_key)),
                 ("Presumed vendor", r.vendor or "unknown"), ("Likely purpose", r.purpose or "unknown"),
                 ("Confidence", r.confidence or "Unknown")]
        rows += [("Removes", p) for p in r.leftover_paths]
    return rows


def properties_text(r: m.AppRecord) -> str:
    return "\n".join(f"{label}: {value}" for label, value in properties(r))
