"""Classify Windows Update offers, and decide what a batch may install (Qt-free).

A batch that silently installs a feature upgrade, or a preview, is not the same
operation as installing monthly security fixes. This module names the category
and the automated-run rules explicitly; the manual tab still installs what the
user selected, after an explicit confirmation for feature upgrades.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

SECURITY = "security"
QUALITY = "quality"
FEATURE = "feature"
DRIVER = "driver"
PREVIEW = "preview"
DOTNET = "dotnet"
DEFENDER = "defender"
OTHER = "other"

LABELS = {
    SECURITY: "Security",
    QUALITY: "Quality",
    FEATURE: "Feature upgrade",
    DRIVER: "Driver",
    PREVIEW: "Preview",
    DOTNET: ".NET / runtime",
    DEFENDER: "Defender definitions",
    OTHER: "Other",
}

#: Categories an automatic run (Run All / unattended) must not install unless
#: the matching setting is on.
OPTIONAL = (DRIVER, PREVIEW, DOTNET, FEATURE)

_FEATURE_TITLE = re.compile(r"^(Windows 1[01],?\s+version|Windows 1[01]\b.*\bversion|"
                            r"Feature update|Upgrade to Windows)", re.I)


def classify(classification: str, title: str) -> str:
    """One category from WUA's classification and the title."""
    kind = (classification or "").strip().casefold()
    name = (title or "").strip()
    text = f"{kind} {name}".casefold()
    if "defender" in text or "security intelligence" in text or "antimalware" in text:
        return DEFENDER
    if kind in ("upgrades", "feature packs") or _FEATURE_TITLE.search(name):
        return FEATURE
    if kind == "drivers" or "driver" in kind:
        return DRIVER
    if "preview" in text:
        return PREVIEW
    if ".net" in text or "visual c++" in text or "runtime" in text:
        return DOTNET
    if kind in ("security updates", "critical updates") or "cumulative" in text:
        return SECURITY
    return QUALITY


def category_of(update) -> str:
    # `IUpdate.Type == utDriver` is certain where a title may not be: a driver
    # with a generic title must not be mistaken for a quality update.
    if getattr(update, "driver", False):
        return DRIVER
    return classify(getattr(update, "classification", ""), getattr(update, "title", ""))


def counts(updates: Iterable) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for update in updates:
        cat = category_of(update)
        result[cat] = result.get(cat, 0) + 1
    return result


def summary_line(updates: Iterable) -> str:
    counted = counts(updates)
    order = [SECURITY, QUALITY, FEATURE, DRIVER, PREVIEW, DOTNET, DEFENDER, OTHER]
    parts = [f"{LABELS[cat]} {counted[cat]}" for cat in order if counted.get(cat)]
    return " · ".join(parts)


def split_for_automatic(updates: List, allow_feature: bool = False,
                        allow_optional: bool = False,
                        allow_drivers: bool = False) -> Tuple[List, Dict[str, List]]:
    """(installable, skipped-by-category) for Run All / unattended.

    Drivers have their own gate, `allow_drivers`, and are skipped by default
    even when `allow_optional` is on: a driver is approved per offer (or in
    Driver Manager), not swept in with preview/.NET packages.
    """
    run, skipped = [], {}
    for update in updates:
        cat = category_of(update)
        blocked = ((cat == FEATURE and not allow_feature)
                   or (cat == DRIVER and not allow_drivers)
                   or (cat in (PREVIEW, DOTNET) and not allow_optional))
        if blocked:
            skipped.setdefault(cat, []).append(update)
        else:
            run.append(update)
    return run, skipped


def skipped_text(skipped: Dict[str, List]) -> str:
    if not skipped:
        return ""
    parts = []
    for cat in (FEATURE, DRIVER, PREVIEW, DOTNET):
        rows = skipped.get(cat) or []
        if rows:
            if cat == FEATURE:
                suffix = " (needs explicit approval)"
            elif cat == DRIVER:
                suffix = " (review and approve in Driver Manager)"
            else:
                suffix = " (optional)"
            parts.append(f"{len(rows)} {LABELS[cat].lower()}" + suffix)
    return "; ".join(parts)
