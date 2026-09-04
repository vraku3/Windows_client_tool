r"""Changing a display, and refusing to when it would be a guess.

This is the dangerous half of Monitor Control. A resolution or refresh rate
a monitor cannot show leaves the user looking at "no signal", and the
control that would undo it is on the screen that just went dark. So the
write layer is built to refuse more readily than it acts:

* A mode the device does not enumerate is **refused**, not attempted. The
  driver will often accept a plausible-looking mode and then show nothing.
* `ChangeDisplaySettingsEx` returns a NAMED failure — BADMODE, BADPARAM,
  BADDUALVIEW — and every one is surfaced by name. A bare -2 in a log is
  not a reason.
* Multi-monitor changes are staged with `CDS_NORESET` and committed once,
  because applying each device as it is set produces a visible cascade and
  can transiently put two monitors at the same origin.
* Deactivating the LAST active display is refused outright. Windows will do
  it, and the result is a machine with no visible output.
"""
import pytest

from modules.monitor_control import display_config as dc
from modules.monitor_control import display_modes as dm
from modules.monitor_control import display_writes as dw


# ── naming the failures ────────────────────────────────────────────────

@pytest.mark.parametrize("code,fragment", [
    (0, "SUCCESSFUL"),
    (1, "RESTART"),
    (-1, "FAILED"),
    (-2, "BADMODE"),
    (-3, "NOTUPDATED"),
    (-4, "BADFLAGS"),
    (-5, "BADPARAM"),
    (-6, "BADDUALVIEW"),
])
def test_every_change_result_has_a_name(code, fragment):
    assert fragment in dw.change_result_name(code)


def test_an_unknown_change_result_still_says_something():
    assert "42" in dw.change_result_name(42)


def test_zero_is_the_only_success():
    assert dw.change_succeeded(0) is True
    for code in (1, -1, -2, -3, -4, -5, -6):
        assert dw.change_succeeded(code) is False, code


def test_a_restart_required_result_is_not_treated_as_plain_success():
    """DISP_CHANGE_RESTART means it did NOT take effect now."""
    assert dw.change_succeeded(1) is False
    assert "RESTART" in dw.change_result_name(1)


# ── refusing a mode the device does not offer ──────────────────────────

def _modes():
    return [
        dm.DisplayMode(2560, 1440, 60.0, 32, False),
        dm.DisplayMode(2560, 1440, 144.0, 32, False),
        dm.DisplayMode(1920, 1080, 60.0, 32, False),
    ]


def test_a_mode_the_device_offers_is_allowed():
    ok, reason = dw.mode_is_offered(_modes(), 2560, 1440, 144.0)
    assert ok is True and reason == ""


def test_a_refresh_rate_absent_at_that_resolution_is_refused():
    """144 Hz exists on the device, but not at 1920x1080."""
    ok, reason = dw.mode_is_offered(_modes(), 1920, 1080, 144.0)
    assert ok is False
    assert "1920x1080" in reason and "144" in reason


def test_a_resolution_the_device_never_offers_is_refused():
    ok, reason = dw.mode_is_offered(_modes(), 3840, 2160, 60.0)
    assert ok is False
    assert "3840x2160" in reason


def test_an_empty_mode_list_refuses_rather_than_allowing_anything():
    """No list is not permission — it means we could not find out."""
    ok, reason = dw.mode_is_offered([], 2560, 1440, 60.0)
    assert ok is False
    assert reason


def test_a_near_miss_refresh_rate_matches_within_tolerance():
    """59.94 and 60 name the same mode to a user; the list holds one."""
    modes = [dm.DisplayMode(2560, 1440, 59.94, 32, False)]
    ok, _ = dw.mode_is_offered(modes, 2560, 1440, 60.0, tolerance=0.1)
    assert ok is True


# ── activating and deactivating a display ──────────────────────────────

def _paths(active_targets, all_targets=(256, 264, 265)):
    return [
        dc.DisplayPath(
            source_id=i, target_id=t, adapter=(1, 0),
            active=t in active_targets, available=True,
            output_technology=5, refresh_hz=60.0 if t in active_targets else 0.0,
            source_mode_idx=i if t in active_targets else dc.MODE_IDX_INVALID,
            target_mode_idx=i, rotation=0)
        for i, t in enumerate(all_targets)
    ]


def test_deactivating_one_of_two_is_allowed():
    ok, reason = dw.can_set_target_active(_paths({256, 264}), 264, False)
    assert ok is True and reason == ""


def test_deactivating_the_last_active_display_is_refused():
    """Windows permits it. The result is a machine showing nothing."""
    ok, reason = dw.can_set_target_active(_paths({256}), 256, False)
    assert ok is False
    assert "last" in reason.lower() or "only" in reason.lower()


def test_activating_an_available_target_is_allowed():
    ok, reason = dw.can_set_target_active(_paths({256}), 265, True)
    assert ok is True and reason == ""


def test_activating_a_target_that_is_not_available_is_refused():
    paths = _paths({256})
    paths = [p if p.target_id != 265 else
             dc.DisplayPath(**{**p.__dict__, "available": False})
             for p in paths]
    ok, reason = dw.can_set_target_active(paths, 265, False if False else True)
    assert ok is False
    assert "not available" in reason.lower() or "not connected" in reason.lower()


def test_activating_something_already_active_is_a_no_op_not_an_error():
    ok, reason = dw.can_set_target_active(_paths({256, 264}), 256, True)
    assert ok is True
    assert "already" in reason.lower() or reason == ""


def test_an_unknown_target_is_refused():
    ok, reason = dw.can_set_target_active(_paths({256}), 9999, True)
    assert ok is False
    assert "9999" in reason


# ── the flags used for the write ───────────────────────────────────────

def test_applying_a_topology_validates_before_it_applies():
    """SDC_VALIDATE first: an impossible arrangement is refused before it
    is attempted, not after half of it has happened."""
    assert dc.SDC_VALIDATE & dc.APPLY_FLAGS == 0, (
        "validate must be a separate call, not folded into the apply flags")
    assert dc.SDC_APPLY & dc.APPLY_FLAGS
    assert dc.SDC_USE_SUPPLIED_DISPLAY_CONFIG & dc.APPLY_FLAGS
    assert dc.SDC_ALLOW_CHANGES & dc.APPLY_FLAGS


def test_a_staged_mode_change_does_not_reset_on_each_device():
    """CDS_NORESET while staging, so the commit applies them together."""
    assert dw.CDS_NORESET & dw.STAGE_FLAGS
    assert dw.CDS_UPDATEREGISTRY & dw.STAGE_FLAGS
    assert dw.CDS_NORESET & dw.COMMIT_FLAGS == 0


# ── arrangement (the Win+P options) ────────────────────────────────────

def test_every_arrangement_maps_to_one_topology_flag():
    for name in ("extend", "clone", "internal", "external"):
        flag = dw.ARRANGEMENTS[name]
        assert flag, f"{name} has no flag"
        # Exactly one topology bit, never a combination: Windows treats
        # these as alternatives and OR-ing two is not "either".
        assert bin(flag).count("1") == 1, name


def test_an_unknown_arrangement_is_refused_not_guessed():
    ok, reason = dw.can_set_arrangement("sideways")
    assert ok is False
    assert "sideways" in reason


def test_a_known_arrangement_is_allowed():
    ok, reason = dw.can_set_arrangement("extend")
    assert ok is True and reason == ""


def test_the_topology_flags_are_not_mixed_with_the_supplied_config_flags():
    """SDC_TOPOLOGY_* and SDC_USE_SUPPLIED_DISPLAY_CONFIG are mutually
    exclusive ways of describing what to apply; combining them is
    ERROR_INVALID_PARAMETER."""
    for flag in dw.ARRANGEMENTS.values():
        assert flag & dc.SDC_USE_SUPPLIED_DISPLAY_CONFIG == 0
