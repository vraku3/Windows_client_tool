"""The Tweaks tab's status column — the part the user actually reads.

The old column could only say Applied / Not Applied / Unknown, matched its own
filters by searching the label text (so "Applied" matched "Not Applied"), and
never showed why. These tests pin the replacement.
"""
import pytest
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QMessageBox, QWidget

from modules.tweaks.tweak_engine import (
    APPLIED, NOT_APPLIED, NOT_APPLICABLE, PARTIAL, UNKNOWN,
)
from modules.tweaks.tweaks_module import _FILTER_TO_STATUS, _STATUS_DISPLAY, TweakRow, TweakTab, TweaksModule


def _tweak(tid, name="A Tweak", risk="low"):
    return {
        "id": tid, "name": name, "description": "does a thing",
        "category": "Test", "risk": risk,
        "steps": [{"type": "registry", "key": r"HKCU\Software\T",
                   "value": "V", "data": 1, "kind": "DWORD"}],
    }


@pytest.fixture
def row(qapp):
    return TweakRow(_tweak("t1"))


# -- labels ---------------------------------------------------------------

@pytest.mark.parametrize("status", [APPLIED, NOT_APPLIED, PARTIAL,
                                    NOT_APPLICABLE, UNKNOWN])
def test_every_status_has_a_label(status):
    assert status in _STATUS_DISPLAY
    text, _ = _STATUS_DISPLAY[status]
    assert text.strip()


def test_partial_and_not_applicable_are_distinguishable(row):
    row.set_status(PARTIAL, "1 of 2 steps are in place")
    partial_text = row.status_label.text()
    row.set_status(NOT_APPLICABLE, "service missing")
    assert row.status_label.text() != partial_text


def test_row_remembers_its_status_code(row):
    row.set_status(PARTIAL, "half done")
    assert row.status == PARTIAL
    assert row.status_reason == "half done"


# -- the reason reaches the user ------------------------------------------

def test_reason_lands_in_the_tooltip(row):
    row.set_status(NOT_APPLIED, "HKCU\\...\\V is 0, this tweak wants 1")
    assert "wants 1" in row.status_label.toolTip()


def test_tooltip_still_names_the_status_with_no_reason(row):
    row.set_status(APPLIED, "")
    assert "Applied" in row.status_label.toolTip()


# -- not-applicable rows are inert ----------------------------------------

def test_not_applicable_row_cannot_be_selected_or_applied(row):
    row.set_checked(True)
    row.set_status(NOT_APPLICABLE, "the 'Fax' service is not installed")
    assert not row.checkbox.isEnabled()
    assert not row.is_checked, "a tweak that cannot run must not stay ticked"
    assert not row.apply_btn.isEnabled()
    assert "not installed" in row.apply_btn.toolTip()


def test_row_becomes_usable_again_when_status_changes(row):
    row.set_status(NOT_APPLICABLE, "nope")
    row.set_status(NOT_APPLIED, "not set")
    assert row.checkbox.isEnabled() and row.apply_btn.isEnabled()


def test_disable_button_shows_for_applied_and_partial(row):
    row.set_status(APPLIED, "")
    assert row.disable_btn.isVisible() or row.disable_btn.isVisibleTo(row)
    row.set_status(NOT_APPLIED, "")
    assert not row.disable_btn.isVisibleTo(row)


# -- selection by status --------------------------------------------------

@pytest.fixture
def tab(qapp):
    t = TweakTab([_tweak("applied_one"), _tweak("not_applied_one"),
                  _tweak("na_one")])
    t.set_status("applied_one", APPLIED, "")
    t.set_status("not_applied_one", NOT_APPLIED, "")
    t.set_status("na_one", NOT_APPLICABLE, "service missing")
    return t


def test_select_applied_does_not_also_select_not_applied(tab):
    """The old filter tested `"Applied" in label_text`, and "Not Applied"
    contains "Applied" — so asking for applied rows selected everything."""
    tab.select_by_status(APPLIED)
    assert tab._rows["applied_one"].is_checked
    assert not tab._rows["not_applied_one"].is_checked


def test_select_by_status_skips_not_applicable_rows(tab):
    tab.select_by_status("all")
    assert not tab._rows["na_one"].is_checked
    assert tab._rows["applied_one"].is_checked


def test_status_counts_reports_each_bucket(tab):
    counts = tab.status_counts()
    assert counts[APPLIED] == 1
    assert counts[NOT_APPLIED] == 1
    assert counts[NOT_APPLICABLE] == 1


# -- filter dropdown ------------------------------------------------------

def test_filter_names_map_onto_real_status_codes():
    for label, status in _FILTER_TO_STATUS.items():
        assert status in _STATUS_DISPLAY, f"{label} maps to unknown status {status}"


def test_filter_offers_every_status():
    assert set(_FILTER_TO_STATUS.values()) == {
        APPLIED, NOT_APPLIED, PARTIAL, NOT_APPLICABLE, UNKNOWN}


def test_bulk_apply_high_risk_needs_confirmation_before_restore_point(qapp, monkeypatch):
    module = TweaksModule()
    module._engine = MagicMock()
    module._engine.safety_reason.return_value = ""
    module._widget = QWidget()
    module._tab_widgets = {"Test": MagicMock(selected_tweaks=lambda: [
        _tweak("danger", name="Disable a Windows protection", risk="high")])}
    module.app = MagicMock()
    calls = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: calls.append(args) or QMessageBox.StandardButton.No)

    module._on_apply()

    assert calls and "Disable a Windows protection" in calls[0][2]
    module.app.backup.create_restore_point.assert_not_called()
