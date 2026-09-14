"""Cleanup's three built-in presets (Light/Thorough/Aggressive) plus
"Custom" (== whatever's currently checked, handled by the caller, not
this module). Mirrors the SPIRIT of Debloat's preset system
(debloat_presets.py) -- named, curated selections a fresh user can pick
with one click -- but Cleanup only ever needs one axis (safety level),
not two catalogs to reconcile, so this stays a plain constant rather
than a JSON-plus-resolver module.
"""

#: Scanner function names NO preset -- not even Aggressive -- may ever
#: auto-include. Matches this codebase's own established "never
#: pre-selected regardless of confirmation" convention (CLAUDE.md:
#: "Drivers and virtual disks never enter the checkbox tree"; the
#: orphaned-profiles scanner's own safety="danger" + selected=False).
#: Named by scanner function name, not by safety level -- a name-based
#: exclusion list survives a future scanner accidentally being marked
#: "caution" instead of "danger" (defense in depth, not a substitute for
#: getting the safety level right).
NEVER_INCLUDED = frozenset({
    "scan_orphaned_user_profiles",
    "scan_virtual_disk_images",
    "scan_orphaned_virtual_disks",
})

PRESETS = {
    "light": {
        "label": "Light",
        "description": "Safe items only -- what \"Clean All Safe\" has always done.",
        "safety_levels": frozenset({"safe"}),
    },
    "thorough": {
        "label": "Thorough",
        "description": "Safe and caution-level items -- more reclaimed space, still nothing destructive.",
        "safety_levels": frozenset({"safe", "caution"}),
    },
    "aggressive": {
        "label": "Aggressive",
        "description": (
            "Everything reclaimable, including danger-level items "
            "(except what NEVER_INCLUDED always excludes -- orphaned "
            "profiles and virtual disks are never swept by any preset)."
        ),
        "safety_levels": frozenset({"safe", "caution", "danger"}),
    },
}


def preset_names() -> list:
    return ["light", "thorough", "aggressive", "custom"]


def items_for_preset(preset_id: str, results: dict, id_to_scanner_name: dict) -> list:
    """Every ScanItem the given preset selects, across a QuickCleanupTab-
    shaped `results` dict ({category_id: ScanResult}), honoring
    NEVER_INCLUDED regardless of what the preset's own safety_levels say.

    `id_to_scanner_name` maps a category id (e.g. "large") to the real
    scanner function's __name__, needed because NEVER_INCLUDED excludes
    by scanner identity, not category -- a category can bundle several
    scanners (see _with_catalog), only some of which might ever be
    excluded.

    preset_id == "custom" is deliberately unresolved here -- it means
    "whatever's currently checked," which is the caller's own state, not
    a data-driven rule this function could apply. Raises ValueError for
    it (and for any other unrecognized id) so a caller cannot silently
    forget the special case.
    """
    if preset_id not in PRESETS:
        raise ValueError(
            f"items_for_preset does not resolve {preset_id!r} -- "
            f"\"custom\" is the caller's own checked-item state, not a "
            f"data-driven preset; light/thorough/aggressive are the only "
            f"valid ids here")
    allowed = PRESETS[preset_id]["safety_levels"]
    selected = []
    for cid, result in results.items():
        scanner_name = id_to_scanner_name.get(cid)
        if scanner_name in NEVER_INCLUDED:
            continue
        items = getattr(result, "items", None)
        if items is None:
            continue  # e.g. browser results are a list of BrowserResult, not a ScanResult
        for item in items:
            if item.safety in allowed:
                selected.append(item)
    return selected
