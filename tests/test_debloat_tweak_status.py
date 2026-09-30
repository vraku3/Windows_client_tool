"""Debloat's Privacy & Telemetry (and AI & Navigation) tab's status column.

Before this, `_populate_tweaks_table` mapped only three of `TweakEngine`'s
five verdicts (applied/not_applied/unknown) and used a dict `.get()` default
to fold anything else -- PARTIAL and NOT_APPLICABLE -- into "Unknown". That
is exactly the collapse `detect_status()`'s own docstring says its five-value
design exists to prevent, and it is not hypothetical: probing this real
machine's privacy.json/telemetry.json/services.json/network.json (185
tweaks total) found 4 partial, 11 not_applicable and 1 unknown verdict, all
of which used to render as an indistinguishable "Unknown".
"""
import json
import os

import pytest

from modules.debloat.debloat_module import DebloatToolsModule, _STATUS_DISPLAY
from modules.tweaks.tweak_engine import (
    APPLIED, NOT_APPLIED, NOT_APPLICABLE, PARTIAL, UNKNOWN, TweakEngine,
)

_DEFS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "src", "modules", "tweaks", "definitions"
)


def _load(fname):
    with open(os.path.join(_DEFS_DIR, fname), encoding="utf-8") as f:
        return json.load(f)


# -- the mapping itself ----------------------------------------------------

def test_every_engine_status_has_its_own_label():
    for status in (APPLIED, NOT_APPLIED, PARTIAL, NOT_APPLICABLE, UNKNOWN):
        assert status in _STATUS_DISPLAY
        text, _ = _STATUS_DISPLAY[status]
        assert text.strip()


def test_partial_and_not_applicable_no_longer_collapse_to_unknown():
    """The exact bug: both used to fall through to the same "Unknown" text."""
    partial_text, _ = _STATUS_DISPLAY[PARTIAL]
    na_text, _ = _STATUS_DISPLAY[NOT_APPLICABLE]
    unknown_text, _ = _STATUS_DISPLAY[UNKNOWN]
    assert partial_text != unknown_text
    assert na_text != unknown_text
    assert partial_text != na_text


# -- the summary label -------------------------------------------------

def test_summary_names_only_buckets_that_occurred():
    text = DebloatToolsModule._status_summary(3, {APPLIED: 2, NOT_APPLIED: 1})
    assert "2 applied" in text
    assert "1 not applied" in text
    assert "partial" not in text
    assert "not applicable" not in text
    assert "unknown" not in text
    assert text.startswith("3 tweak(s) loaded")


def test_summary_handles_nothing_detected_yet():
    text = DebloatToolsModule._status_summary(5, {})
    assert "5 tweak(s) loaded" in text
    assert "nothing detected yet" in text


# -- real machine: this file's whole point ---------------------------------

@pytest.mark.real_machine
def test_real_machine_privacy_telemetry_tweaks_produce_every_kind_of_verdict():
    """Not synthetic: run the actual engine against the actual definition
    files the Privacy & Telemetry tab loads, on this actual machine, and
    confirm the previously-collapsed statuses (partial, not_applicable)
    really occur here -- so the fix has something real to guard.
    """
    tweaks = (
        _load("privacy.json") + _load("telemetry.json")
        + _load("services.json") + _load("network.json")
    )
    engine = TweakEngine(None)
    counts = {}
    for tweak in tweaks:
        status = engine.detect_status(tweak)
        assert status in _STATUS_DISPLAY, (
            f"{tweak.get('id')} produced an un-displayable status {status!r}")
        counts[status] = counts.get(status, 0) + 1

    # Measured on this machine 2026-09-30: 152 not_applied, 17 applied,
    # 4 partial, 11 not_applicable, 1 unknown. Pin only the shape of the
    # finding (more than one non-trivial bucket present), not exact counts,
    # since Windows Update / feature state can shift them over time.
    assert counts.get(PARTIAL, 0) > 0, "expected at least one partial verdict on this machine"
    assert counts.get(NOT_APPLICABLE, 0) > 0, (
        "expected at least one not_applicable verdict on this machine")
    summary = DebloatToolsModule._status_summary(len(tweaks), counts)
    assert "partial" in summary
    assert "not applicable" in summary
