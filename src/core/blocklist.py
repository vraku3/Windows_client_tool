"""Blocklist matcher for Windows Update / winget items.

Ported from Update Center's Test-UcBlocked: an item is blocked if its name OR
id matches any pattern (case-insensitive). Patterns support '*' as a wildcard;
a bare pattern with no '*' is auto-wrapped as '*pattern*' (substring match).
Blank lines and lines starting with '#' are treated as comments and ignored.
"""
import fnmatch
from datetime import date
from typing import List


def normalize_patterns(raw_text: str) -> List[str]:
    """Split textarea content into a clean pattern list (strip, drop blanks/#comments)."""
    patterns = []
    for line in (raw_text or "").splitlines():
        p = line.strip()
        if not p or p.startswith("#"):
            continue
        patterns.append(p)
    return patterns


def _normalize_exclusions(exclusions):
    entries = []
    seen = set()
    for raw in exclusions or []:
        entry = dict(raw) if isinstance(raw, dict) else {"pattern": raw}
        pattern = entry.get("pattern", "")
        if not isinstance(pattern, str):
            continue
        pattern = pattern.strip()
        if not pattern or pattern.startswith("#") or pattern.casefold() in seen:
            continue
        seen.add(pattern.casefold())
        entry["pattern"] = pattern
        for key, value in {"reason": "", "origin": "manual", "added": date.today().isoformat(),
                           "review_by": None, "expires": None, "added_by": ""}.items():
            entry.setdefault(key, value)
        entries.append(entry)
    return entries


def load_exclusions(app) -> list[dict]:
    raw = app.config.get("updates.blocklist_patterns", [])
    entries = _normalize_exclusions(raw)
    if entries != raw:
        save_exclusions(app, entries)
    return entries


def save_exclusions(app, exclusions) -> None:
    app.config.set("updates.blocklist_patterns", _normalize_exclusions(exclusions))


def add_exclusion(app, pattern, reason, origin="manual") -> None:
    if origin not in ("manual", "auto"):
        raise ValueError("Origin must be manual or auto")
    pattern = (pattern or "").strip()
    if not pattern or pattern.startswith("#"):
        return
    entries = load_exclusions(app)
    if any(e["pattern"].casefold() == pattern.casefold() for e in entries):
        return
    import getpass
    entries.append({"pattern": pattern, "reason": reason, "origin": origin,
                    "added_by": "WinClientTool" if origin == "auto" else getpass.getuser()})
    save_exclusions(app, entries)
    from core.timeline import record
    record(getattr(app, "app_data_dir", None), "updates", "blocklist_add", f"Added update blocklist pattern: {pattern}", {"origin": origin})


def remove_exclusion(app, pattern) -> None:
    save_exclusions(app, [e for e in load_exclusions(app)
                          if e["pattern"].casefold() != pattern.strip().casefold()])


def add_pattern(app, pattern: str) -> None:
    add_exclusion(app, pattern, "")


def expired_exclusions(exclusions) -> list[dict]:
    expired = []
    for entry in exclusions or []:
        if isinstance(entry, dict) and entry.get("expires"):
            try:
                if date.fromisoformat(entry["expires"]) < date.today():
                    expired.append(entry)
            except (ValueError, TypeError):
                continue
    return expired


def is_blocked(name: str, id_: str, patterns: List[str | dict]) -> bool:
    """Return True if `name` or `id_` matches any pattern in `patterns`."""
    return bool(_matching_pattern(name, id_, patterns))


def matching_offers(pattern, offers) -> list[tuple[str, str]]:
    """Which of the current offers a new pattern would hide, as (name, id).

    Used to preview a pattern before it is saved; expired entries are ignored
    the same way `is_blocked` ignores them.
    """
    text = (pattern or "").strip()
    if not text or text.startswith("#"):
        return []
    out = []
    for offer in offers or []:
        name = getattr(offer, "name", None)
        ident = getattr(offer, "winget_id", None)
        if name is None and isinstance(offer, dict):
            name, ident = offer.get("name"), offer.get("winget_id") or offer.get("id")
        if name is None and ident is None:
            name, ident = str(offer), ""
        if is_blocked(str(name or ""), str(ident or ""), [{"pattern": text}]):
            out.append((str(name or ident), str(ident or "")))
    return out


def _matching_pattern(name: str, id_: str, patterns: List[str | dict]) -> str:
    if not patterns:
        return ""
    name = (name or "").lower()
    id_ = (id_ or "").lower()
    for raw in patterns:
        if isinstance(raw, dict):
            if expired_exclusions([raw]):
                continue
            raw = raw.get("pattern", "")
        p = (raw or "").strip()
        if not p or p.startswith("#"):
            continue
        p = p.lower()
        if "*" not in p:
            p = f"*{p}*"
        if id_ and fnmatch.fnmatch(id_, p):
            return p
        if name and fnmatch.fnmatch(name, p):
            return p
    return ""
