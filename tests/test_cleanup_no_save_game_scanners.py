"""Real defect (2026-09-23): 7 catalog entries pointed a whole game's
%APPDATA%/%LOCALAPPDATA% folder at "safe" tier while their own label
admitted covering real save data ("save game", "savegame backup", "ladder
save data") -- or, for factorio_cache/stardew_cache/godot_cache/
gta_v_cache, pointed at the exact real, well-documented location that
game/engine stores actual save files, regardless of label wording:

  scrivener_cache, factorio_cache, stardew_cache, godot_cache,
  gta_v_cache, snowrunner_cache, warcraft_3_cache

Losing a save file can mean losing hundreds of hours of real progress.
This is the same shape as test_cleanup_no_credential_vault_scanners.py,
for save data instead of credentials -- a whole-catalog sweep, not a
fixed id list, so a future game entry trips it too.
"""
import re

from modules.cleanup.cleanup_scanner.catalog import load_catalog

#: Word stems admitting real save/progress data, not disposable cache.
_SAVE_WORDS = re.compile(
    r"\bsave\s*game|\bsavegame|\bsave\s*data|\bsave\s*file|\bsave\s*backup|"
    r"\bladder\s*save",
    re.I,
)

#: Real, well-documented per-game/engine save locations -- checked
#: regardless of label wording, since a bulk-generated catalog entry can
#: describe a folder as "cache" while still pointing at the one place a
#: game keeps its actual saves.
_KNOWN_REAL_SAVE_ROOTS = {
    # (path tail, why) -- path tail is the final 1-2 segments, case-insensitive.
    "factorio": "Factorio saves directly under %APPDATA%\\Factorio\\saves",
    "stardewvalley": "Stardew Valley saves directly under "
                     "%APPDATA%\\StardewValley\\Saves",
    "godot": "Godot's own app_userdata convention is where countless "
            "indie games built with it keep save files",
}

#: Already correctly scoped to a logs-only subfolder (2026-09-23) -- the
#: match is each one's own reassuring disclaimer text explaining what it
#: does NOT cover, the same false-positive shape rdp_cache/ansible_cache
#: hit in test_cleanup_no_credential_vault_scanners.py.
_ALLOWLIST = {
    "cities_skylines_cache", "ck3_cache", "eu4_cache", "beamng_cache",
}


def test_no_safe_tier_scanner_admits_covering_save_data():
    catalog = load_catalog()
    offenders = []
    for spec_id, spec in catalog.items():
        if spec_id in _ALLOWLIST:
            continue
        text = f"{spec.label} {spec.description}"
        if spec.safety == "safe" and _SAVE_WORDS.search(text):
            offenders.append((spec_id, spec.label))
    assert offenders == [], (
        "these scanners are 'safe' tier (auto-included in Clean All Safe) "
        "but their own label/description admits covering real save/"
        "progress data -- narrow the path to a verified cache-only "
        "subfolder (see cities_skylines_cache/ck3_cache/eu4_cache/"
        "beamng_cache for the pattern: keep only \\logs, drop the whole "
        "game-root path) or remove entirely:\n  "
        + "\n  ".join(f"{sid}: {label!r}" for sid, label in offenders))


def test_no_scanner_targets_a_known_real_save_location():
    catalog = load_catalog()
    offenders = []
    for spec_id, spec in catalog.items():
        for path in spec.paths:
            tail = path.rstrip("\\").split("\\")[-1].lower()
            if tail in _KNOWN_REAL_SAVE_ROOTS:
                offenders.append((spec_id, path, _KNOWN_REAL_SAVE_ROOTS[tail]))
                break
    assert offenders == [], (
        "these scanners target a folder known to hold real save data "
        "directly, regardless of what their own label claims:\n  "
        + "\n  ".join(f"{sid}: {path} ({why})" for sid, path, why in offenders))
