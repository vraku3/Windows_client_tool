"""Real defect (2026-09-23): 2 catalog entries pointed at real, saved user
documents rather than disposable cache, marked "safe" tier:

  joplin_cache: Joplin is local-first -- the actual notes database
  (database.sqlite) lives directly in %APPDATA%\\Joplin, and cloud sync is
  optional, so this is often the ONLY copy of a user's notes.

  microsoft_templates: %APPDATA%\\Microsoft\\Templates holds the user's own
  SAVED Office templates (a custom letterhead, a .dotx someone built) --
  real content by design, never cache, despite the old label calling it
  "cached document templates".

A third, sticky_notes, was narrowed rather than removed: its
%LOCALAPPDATA%\\Packages\\...\\LocalState path held plum.sqlite, the real
database of every sticky note's actual text, alongside a genuinely
telemetry-only .sqm file that is now the only path left.

Same shape as the credential-vault and save-game sweeps -- a
whole-catalog check, not a fixed id list.
"""
import re

from modules.cleanup.cleanup_scanner.catalog import load_catalog

#: Real, saved user content -- never disposable cache, regardless of how
#: an entry's own label describes it.
_DOCUMENT_WORDS = re.compile(
    r"\bnotes?\s*database|\bnote\s*database|\bsaved\s*template|"
    r"\bown\s*template|\bpersonal\s*template",
    re.I,
)

#: Known real per-app note databases that live directly in an app's
#: profile root, regardless of label wording -- the same idea as
#: test_cleanup_no_save_game_scanners.py's _KNOWN_REAL_SAVE_ROOTS.
_KNOWN_REAL_NOTE_ROOTS = {
    "joplin": "Joplin is local-first -- database.sqlite (all notes) lives "
             "directly in this folder, sync is optional",
}

_ALLOWLIST = {
    # Narrowed 2026-09-23 to the .sqm telemetry file only -- the real
    # database path (plum.sqlite under LocalState) was removed. No match
    # expected here going forward; nothing to allowlist by keyword, but a
    # future edit that accidentally widens its scope again should still be
    # caught by test_no_scanner_targets_a_known_real_note_root below if it
    # names "joplin" specifically, or by manual review otherwise.
}


def test_no_safe_tier_scanner_targets_real_saved_documents():
    catalog = load_catalog()
    offenders = []
    for spec_id, spec in catalog.items():
        if spec_id in _ALLOWLIST:
            continue
        text = f"{spec.label} {spec.description}"
        if spec.safety == "safe" and _DOCUMENT_WORDS.search(text):
            offenders.append((spec_id, spec.label))
    assert offenders == [], (
        "these scanners are 'safe' tier but their own label/description "
        "admits covering real saved documents/notes, not cache:\n  "
        + "\n  ".join(f"{sid}: {label!r}" for sid, label in offenders))


def test_no_scanner_targets_a_known_real_note_root():
    catalog = load_catalog()
    offenders = []
    for spec_id, spec in catalog.items():
        for path in spec.paths:
            tail = path.rstrip("\\").split("\\")[-1].lower()
            if tail in _KNOWN_REAL_NOTE_ROOTS:
                offenders.append((spec_id, path, _KNOWN_REAL_NOTE_ROOTS[tail]))
                break
    assert offenders == [], (
        "these scanners target a folder known to hold a real local notes "
        "database directly:\n  "
        + "\n  ".join(f"{sid}: {path} ({why})" for sid, path, why in offenders))
