"""Real defect (2026-09-23): 6 catalog entries pointed a whole creative/
professional tool's %APPDATA% folder at "safe" tier while their own label
admitted covering real saved work, connection credentials, or licensing
state:

  cinema4d_cache, logic_pro_cache: both admitted "project backups" in
  their own label (logic_pro_cache also described a Windows product of a
  Mac-only DAW that does not exist -- "(if installed)" was doing a lot of
  work).
  dbeaver_cache: admitted "SQL scripts" -- real saved work -- and can also
  hold saved database connection credentials.
  qgis_cache, sas_cache: both reached a whole profile root that can hold
  real user projects/programs, not just the cache their label named.
  kontakt_cache: pointed at Native Instruments' own license/activation
  management folder, where losing state can cost a real activation on
  paid sample libraries.

Same shape as the credential-vault, save-game and document sweeps -- a
whole-catalog check, not a fixed id list. perforce_cache was reviewed and
deliberately left alone: real but lower-severity (a workflow confusion
recoverable via `p4 reconcile`, not permanent loss), so it is not swept
here.
"""
import re

from modules.cleanup.cleanup_scanner.catalog import load_catalog

#: Word stems admitting real saved work, connection credentials, or
#: licensing/activation state -- never disposable cache, regardless of
#: how an entry's own label frames it.
_WORK_WORDS = re.compile(
    r"\bproject\s*backups?\b|\bsql\s*scripts?\b|\blicense\b|\bactivation\b|"
    r"\bconnection\s*credentials?\b",
    re.I,
)

_ALLOWLIST = set()


def test_no_safe_tier_scanner_admits_covering_real_work_or_licensing():
    catalog = load_catalog()
    offenders = []
    for spec_id, spec in catalog.items():
        if spec_id in _ALLOWLIST:
            continue
        text = f"{spec.label} {spec.description}"
        if spec.safety == "safe" and _WORK_WORDS.search(text):
            offenders.append((spec_id, spec.label))
    assert offenders == [], (
        "these scanners are 'safe' tier but their own label/description "
        "admits covering real saved work, connection credentials, or "
        "licensing/activation state, not disposable cache:\n  "
        + "\n  ".join(f"{sid}: {label!r}" for sid, label in offenders))
