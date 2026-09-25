"""A card is a rendering of a catalog entry, not a hand-wired widget."""

from core import semantic_colors
from modules.security_dashboard.applier import ControlResult
from modules.security_dashboard.catalog.model import (
    Category, ControlState, SecurityControl)
from modules.security_dashboard.security_module import ControlCard


def _control(**over):
    base = dict(id="llmnr", title="LLMNR", category=Category.FIREWALL_NETWORK,
                description="d", why_it_matters="w",
                reader=lambda: {"available": True, "enabled": True},
                on_steps=({"type": "registry", "key": "HKLM\\A", "value": "V",
                           "data": 1, "kind": "DWORD"},),
                off_steps=({"type": "registry", "key": "HKLM\\A", "value": "V",
                            "data": 0, "kind": "DWORD"},))
    base.update(over)
    return SecurityControl(**base)


def test_a_read_only_control_offers_no_toggle_and_shows_the_reason(qapp):
    card = ControlCard(_control(on_steps=(), off_steps=(),
                                read_only_reason="TPM presence is hardware"))
    assert not card.toggle_button.isVisible() or not card.toggle_button.isEnabled()
    assert "hardware" in card.reason_label.text()


def test_a_control_that_could_not_be_read_does_not_render_as_off(qapp):
    """A refused read is not an unset value."""
    card = ControlCard(_control())
    card.set_reading(None)
    assert "Unknown" in card.status_badge.text()
    assert "Off" not in card.status_badge.text()


def test_toggling_emits_a_staging_request_and_writes_nothing(qapp):
    card = ControlCard(_control())
    card.set_reading(True)
    seen = []
    card.staged.connect(lambda cid, value: seen.append((cid, value)))
    card.toggle_button.click()
    assert seen == [("llmnr", False)]


def test_a_staged_card_says_what_it_will_become(qapp):
    card = ControlCard(_control())
    card.set_reading(True)
    card.set_staged(False)
    assert "will be" in card.staged_label.text().lower()


def test_no_colour_is_hardcoded_in_the_card(qapp):
    """40 hardcoded hex colours is how 13 of 34 panes rendered dark under the
    light theme. The #999 description text measured ~2.8:1 on white."""
    import inspect
    source = inspect.getsource(ControlCard)
    assert "#999" not in source and "#3c3c3c" not in source


# --- the rest are not in the plan ------------------------------------------
#
# 12 of the 149 controls are multi-valued: NTLM level, cached logons, minimum
# password length, cloud block level, and the four threat actions. The plan's
# card is a toggle, and a toggle over a number is wrong twice over -- see the
# two tests below, both of which describe what this machine would have shown.


def _numeric(**over):
    base = dict(id="ntlm_level", title="NTLM level",
                category=Category.ACCOUNTS, description="d", why_it_matters="w",
                reader=lambda: {"available": True, "enabled": 3},
                read_value=lambda d: d.get("enabled"),
                desired=5,
                on_steps=({"type": "registry", "key": "HKLM\\Lsa",
                           "value": "LmCompatibilityLevel", "data": 5,
                           "kind": "DWORD"},),
                off_steps=({"type": "registry", "key": "HKLM\\Lsa",
                            "value": "LmCompatibilityLevel", "data": 3,
                            "kind": "DWORD"},))
    base.update(over)
    return SecurityControl(**base)


def test_a_numeric_control_is_never_staged_to_the_opposite_of_its_value(qapp):
    """`not 3` is False, and False resolves to off_steps.

    On this machine ntlm_level reads 3 and wants 5, and its off_steps write
    3 -- so a toggle would have staged a change that writes the value it
    already has. For defender_threat_severe the same click stages
    `-SevereThreatDefaultAction None`, turning the severe-threat action OFF.
    A numeric control's action is "set it to what the catalog recommends",
    never "the opposite of what it reads".
    """
    card = ControlCard(_numeric())
    card.set_reading(3)
    seen = []
    card.staged.connect(lambda cid, value: seen.append((cid, value)))
    card.toggle_button.click()
    assert seen == [("ntlm_level", 5)], seen


def test_a_numeric_control_is_rendered_by_value_not_by_truthiness(qapp):
    """3 is truthy, and "On" for a machine three levels below what it wants is
    the green-when-wrong rendering this project keeps finding."""
    card = ControlCard(_numeric())
    card.set_reading(3)
    assert "3" in card.status_badge.text()
    assert "On" not in card.status_badge.text()


def test_a_reading_away_from_desired_is_not_coloured_as_success(qapp):
    semantic_colors.set_theme("dark")
    card = ControlCard(_numeric())
    card.set_reading(3)
    assert semantic_colors.semantic("success") not in card.status_badge.styleSheet()
    card.set_reading(5)
    assert semantic_colors.semantic("success") in card.status_badge.styleSheet()


def test_a_control_the_catalog_has_no_opinion_about_is_not_a_problem(qapp):
    """Ruling 6: 14 controls have desired=None and their readers legitimately
    colour them amber or red. Neither On nor Off is wrong for those."""
    semantic_colors.set_theme("dark")
    card = ControlCard(_control(desired=None))
    card.set_reading(False)
    style = card.status_badge.styleSheet()
    assert semantic_colors.semantic("error") not in style
    assert semantic_colors.semantic("warning") not in style


def test_the_card_follows_a_theme_change(qapp):
    """A colour resolved once at build time is a colour frozen to one theme."""
    semantic_colors.set_theme("dark")
    card = ControlCard(_control(desired=True))
    card.set_reading(True)
    assert semantic_colors.SEMANTIC_PALETTES["dark"]["success"] in \
        card.status_badge.styleSheet()
    try:
        semantic_colors.set_theme("light")
        card.set_reading(True)
        assert semantic_colors.SEMANTIC_PALETTES["light"]["success"] in \
            card.status_badge.styleSheet()
    finally:
        semantic_colors.set_theme("dark")


def test_a_refusal_shows_its_first_line_and_keeps_the_rest(qapp):
    """Measured, not guessed: a refused Set-MpPreference reports twelve lines
    of PowerShell error formatting. One of those fills a card."""
    card = ControlCard(_control())
    reason = ("Set-MpPreference : You don't have enough permissions.\n"
              "At line:1 char:1\n+ Set-MpPreference -DisableArchiveScanning\n"
              "    + CategoryInfo          : NotSpecified: (MSFT_MpPreference")
    card.set_result(ControlResult("llmnr", ControlState.REFUSED, False, True,
                                  reason))
    assert "\n" not in card.result_label.text()
    assert "enough permissions" in card.result_label.text()
    assert "CategoryInfo" in card.result_label.toolTip()


def test_an_unverified_result_shows_what_the_machine_actually_says(qapp):
    semantic_colors.set_theme("dark")
    card = ControlCard(_control())
    card.set_result(ControlResult("llmnr", ControlState.APPLIED_UNVERIFIED,
                                  False, True, "something is overriding it"))
    text = card.result_label.text()
    assert "True" in text and "False" in text
    assert semantic_colors.semantic("warning") in card.result_label.styleSheet()


def test_the_four_states_are_told_apart(qapp):
    rendered = set()
    for state in ControlState:
        card = ControlCard(_control())
        card.set_result(ControlResult("llmnr", state, False, True, "r"))
        rendered.add(card.result_label.text())
    assert len(rendered) == len(ControlState)


def test_clearing_a_staged_card_goes_back_to_the_reading(qapp):
    card = ControlCard(_control())
    card.set_reading(True)
    card.set_staged(False)
    card.clear_staged()
    assert card.staged_label.text() == ""
    assert "On" in card.status_badge.text()


def test_a_read_only_control_cannot_be_staged_at_all(qapp):
    card = ControlCard(_control(on_steps=(), off_steps=(),
                                read_only_reason="hardware"))
    card.set_reading(True)
    seen = []
    card.staged.connect(lambda cid, value: seen.append((cid, value)))
    card.toggle_button.click()
    assert seen == []


def test_a_result_updates_the_badge_it_contradicts(qapp):
    """Found by rendering the card and looking at it.

    `observed` is a reading taken after the write, so it is fresher than the
    badge, which still shows what the pane read before the batch ran. Without
    this a card that applied False sat there reading "On" next to "Applied,
    and the machine confirms it".
    """
    card = ControlCard(_control())
    card.set_reading(True)
    card.set_result(ControlResult("llmnr", ControlState.APPLIED_VERIFIED,
                                  False, False, ""))
    assert "Off" in card.status_badge.text()


def test_a_result_whose_reading_was_refused_shows_unknown_not_the_old_value(qapp):
    card = ControlCard(_control())
    card.set_reading(True)
    card.set_result(ControlResult("llmnr", ControlState.APPLIED_UNVERIFIED,
                                  False, None, "could not be read back"))
    assert "Unknown" in card.status_badge.text()


def test_a_read_only_card_says_so_in_words_not_just_by_omission(qapp):
    """It has no button either, so the sentence is the only signal there is --
    and as plain body text it reads as more description."""
    card = ControlCard(_control(on_steps=(), off_steps=(),
                                read_only_reason="TPM presence is hardware"))
    assert card.reason_label.text().startswith("Read-only")
    assert card.reason_label.font().italic()
    assert not card.description_label.font().italic()


def test_a_numeric_control_already_at_its_target_offers_no_no_op_button(qapp):
    """The Cached domain logons card read 4 and still offered "Set to 4"."""
    card = ControlCard(_numeric())
    card.set_reading(5)
    assert card.toggle_button.text() == "At recommended value"
    assert not card.toggle_button.isEnabled()
    card.set_reading(3)
    assert card.toggle_button.text() == "Set to 5"
    assert card.toggle_button.isEnabled()
