r"""The display-audio engine, against this machine's real endpoints.

Every synthetic row in this file was copied out of a read-only dump of
`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render`
on the machine this was written for (25 endpoints). Three things in that
dump would break an implementation written from the documentation alone,
and all three are pinned here:

* **`DeviceState` carries an undocumented high bit.** The two live display
  endpoints report `268435457` = `0x10000001` — ACTIVE (1) with `0x10000000`
  also set. `raw == DEVICE_STATE_ACTIVE` is False for those, so an equality
  test reports the two monitors that are actually playing audio as being in
  an unknown state. Only the documented nibble may be compared.

* **Endpoint names are not unique, and the duplicates are not hypothetical.**
  This machine carries both `2 - MO27Q28G` (active) and `4 - MO27Q28G` (a
  stale ghost, NOTPRESENT) — the same panel, remembered twice on different
  display indices. Two *identical* monitors would go further and produce two
  identically-named ACTIVE endpoints, which no amount of cleverness can tell
  apart; that case must be reported, never guessed at.

* **21 of the 25 endpoints are NOTPRESENT ghosts.** Anything that assumes the
  registry lists only real hardware picks a device that has not existed for
  years.

Nothing here writes. The two write paths are exercised only through their
safety interlock, which refuses before it reaches Windows — see the module
docstring of `display_audio` for why.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from modules.monitor_control import display_audio as da


# ── real rows, copied from the read-only registry dump ──────────────────
#
# (endpoint_id, friendly_name, device_description, raw DeviceState)

REAL_ROWS = [
    ("{ep-mo27-active}", "2 - MO27Q28G", "AMD High Definition Audio Device", 0x10000001),
    ("{ep-dell-active}", "1 - S2719DGF", "AMD High Definition Audio Device", 0x10000001),
    ("{ep-lg-ghost}", "4 - LG ULTRAWIDE", "AMD High Definition Audio Device", 0x4),
    ("{ep-mo27-ghost}", "4 - MO27Q28G", "AMD High Definition Audio Device", 0x4),
    ("{ep-hidock}", "Speakers", "HiDock H1", 0x1),
    ("{ep-arctis}", "Headphones", "Arctis Nova Pro Wireless", 0x1),
    ("{ep-arctis-ghost}", "Headphones", "Arctis Nova Pro Wireless", 0x4),
    ("{ep-edifier-ghost}", "Speakers", "EDIFIER S3000 Pro", 0x4),
    ("{ep-xmos-ghost}", "Speakers", "XMOS USB Audio", 0x4),
    ("{ep-amd-digital-out}", "Digital Output", "AMD High Definition Audio Device", 0x4),
    ("{ep-hdmi-generic}", "Digital Audio (HDMI)", "High Definition Audio Device", 0x4),
]


def _rows_to_dicts(rows):
    return [
        {
            "endpoint_id": ep,
            "friendly_name": name,
            "device_description": desc,
            "raw_state": state,
        }
        for ep, name, desc, state in rows
    ]


@pytest.fixture
def endpoints():
    return da.build_endpoints(_rows_to_dicts(REAL_ROWS))


def _by_id(endpoints, endpoint_id):
    return next(e for e in endpoints if e.endpoint_id == endpoint_id)


# ── the state mask ─────────────────────────────────────────────────────

def test_the_undocumented_high_bit_does_not_hide_an_active_endpoint():
    """0x10000001 is ACTIVE. This is the whole reason for the mask."""
    assert da.decode_state(268435457) is da.EndpointState.ACTIVE
    assert da.decode_state(0x10000001) is da.EndpointState.ACTIVE


def test_equality_against_the_raw_value_is_what_the_mask_replaces():
    """Pin the trap itself, so nobody 'simplifies' the mask away."""
    raw = 268435457
    assert raw != da.DEVICE_STATE_ACTIVE          # the naive test fails
    assert raw & da.DEVICE_STATE_MASK == da.DEVICE_STATE_ACTIVE


def test_the_undocumented_bits_are_reported_not_discarded():
    assert da.undocumented_state_bits(0x10000001) == 0x10000000
    assert da.undocumented_state_bits(0x4) == 0


# ── hidden is a separate question from state ───────────────────────────
#
# Measured 2026-09-04: SetEndpointVisibility(id, 0) sets 0x10000000 and
# (id, 1) clears it, leaving the documented nibble at ACTIVE. So an endpoint
# is routinely ACTIVE and invisible at the same time, and `state` cannot
# answer "is this on?" on its own.

def test_a_hidden_endpoint_is_still_active():
    """The exact pair the round trip produced, and the reason `is_hidden`
    exists at all."""
    raw = da.DEVICE_STATE_ACTIVE | da.DEVICE_STATE_HIDDEN
    assert da.decode_state(raw) is da.EndpointState.ACTIVE
    assert da.is_hidden(raw) is True


def test_a_visible_endpoint_says_so():
    assert da.is_hidden(da.DEVICE_STATE_ACTIVE) is False


def test_an_unreadable_state_is_not_a_claim_that_it_is_visible():
    """None, never False: False would say "you can pick this device" on the
    strength of a read that did not happen."""
    assert da.is_hidden(None) is None


def test_hiding_does_not_disturb_the_documented_nibble():
    for documented in (da.DEVICE_STATE_ACTIVE, da.DEVICE_STATE_DISABLED,
                       da.DEVICE_STATE_NOTPRESENT,
                       da.DEVICE_STATE_UNPLUGGED):
        hidden = documented | da.DEVICE_STATE_HIDDEN
        assert da.decode_state(hidden) is da.decode_state(documented)
        assert da.is_hidden(hidden) is True
        assert da.is_hidden(documented) is False


@pytest.mark.parametrize("raw,expected", [
    (0x1, da.EndpointState.ACTIVE),
    (0x2, da.EndpointState.DISABLED),
    (0x4, da.EndpointState.NOTPRESENT),
    (0x8, da.EndpointState.UNPLUGGED),
    (0x10000002, da.EndpointState.DISABLED),
    (0x10000004, da.EndpointState.NOTPRESENT),
    (0x10000008, da.EndpointState.UNPLUGGED),
])
def test_every_documented_bit_survives_a_high_bit(raw, expected):
    assert da.decode_state(raw) is expected


def test_no_documented_bit_set_is_unknown_not_a_guess():
    assert da.decode_state(0) is da.EndpointState.UNKNOWN
    assert da.decode_state(0x10000000) is da.EndpointState.UNKNOWN


def test_a_value_we_could_not_read_is_unknown():
    """`None` is 'the read did not answer', and must not become ACTIVE."""
    assert da.decode_state(None) is da.EndpointState.UNKNOWN


def test_two_documented_bits_at_once_is_unknown_not_the_first_one():
    """The four states are mutually exclusive; two set means we cannot say."""
    assert da.decode_state(0x1 | 0x2) is da.EndpointState.UNKNOWN


def test_state_is_usable_as_a_plain_string():
    assert da.decode_state(0x10000001) == "active"


# ── display-audio detection ────────────────────────────────────────────

def test_the_numbered_per_monitor_endpoints_are_display_audio(endpoints):
    for endpoint_id in ("{ep-mo27-active}", "{ep-dell-active}",
                        "{ep-lg-ghost}", "{ep-mo27-ghost}"):
        assert _by_id(endpoints, endpoint_id).is_display_audio


def test_the_generic_gpu_sinks_are_display_audio_too(endpoints):
    assert _by_id(endpoints, "{ep-amd-digital-out}").is_display_audio
    assert _by_id(endpoints, "{ep-hdmi-generic}").is_display_audio


def test_real_speakers_and_headphones_are_not_display_audio(endpoints):
    for endpoint_id in ("{ep-hidock}", "{ep-arctis}", "{ep-arctis-ghost}",
                        "{ep-edifier-ghost}", "{ep-xmos-ghost}"):
        assert not _by_id(endpoints, endpoint_id).is_display_audio


def test_a_spdif_digital_output_on_a_sound_card_is_not_display_audio():
    """'Digital Output' alone is S/PDIF. It counts only behind a display driver."""
    assert not da.looks_like_display_audio("Digital Output", "Creative Sound Blaster Z")


def test_the_monitor_name_is_parsed_off_the_numbered_form(endpoints):
    assert _by_id(endpoints, "{ep-mo27-active}").monitor_name == "MO27Q28G"
    assert _by_id(endpoints, "{ep-mo27-active}").display_index == 2
    assert _by_id(endpoints, "{ep-lg-ghost}").monitor_name == "LG ULTRAWIDE"
    assert _by_id(endpoints, "{ep-lg-ghost}").display_index == 4


def test_a_generic_sink_has_no_monitor_name(endpoints):
    """'Digital Output' names no monitor, so it must not pretend to."""
    generic = _by_id(endpoints, "{ep-amd-digital-out}")
    assert generic.is_display_audio
    assert generic.monitor_name is None
    assert generic.display_index is None


# ── mapping a monitor to an endpoint, which is FALLIBLE ────────────────

def test_a_monitor_maps_to_its_endpoint(endpoints):
    found = da.endpoint_for_monitor(endpoints, "S2719DGF")
    assert found is not None
    assert found.endpoint_id == "{ep-dell-active}"
    assert da.ambiguous_matches(endpoints, "S2719DGF") == []


def test_matching_is_case_and_whitespace_insensitive(endpoints):
    assert da.endpoint_for_monitor(endpoints, "  s2719dgf ").endpoint_id == \
        "{ep-dell-active}"


def test_a_stale_ghost_does_not_make_a_live_monitor_ambiguous(endpoints):
    """This machine really does carry '2 - MO27Q28G' and '4 - MO27Q28G'.

    Both name the same panel; only one is present. Narrowing to the live
    endpoints leaves exactly one candidate, so this is answerable.
    """
    assert len(da.matches_for_monitor(endpoints, "MO27Q28G")) == 2
    assert da.endpoint_for_monitor(endpoints, "MO27Q28G").endpoint_id == \
        "{ep-mo27-active}"
    assert da.ambiguous_matches(endpoints, "MO27Q28G") == []


def test_an_inactive_monitor_with_one_endpoint_still_maps(endpoints):
    """The LG is NOTPRESENT, but it is the only thing named LG ULTRAWIDE."""
    found = da.endpoint_for_monitor(endpoints, "LG ULTRAWIDE")
    assert found is not None
    assert found.endpoint_id == "{ep-lg-ghost}"
    assert found.state is da.EndpointState.NOTPRESENT


def test_two_identical_monitors_are_reported_as_ambiguous_never_picked():
    """The case name-based mapping genuinely cannot solve.

    Two of the same panel on the same GPU produce two live endpoints with
    the same monitor name. Nothing in the registry distinguishes them, so
    picking one is a coin flip dressed up as an answer.
    """
    twins = da.build_endpoints(_rows_to_dicts([
        ("{ep-twin-a}", "1 - MO27Q28G", "AMD High Definition Audio Device", 0x10000001),
        ("{ep-twin-b}", "2 - MO27Q28G", "AMD High Definition Audio Device", 0x10000001),
        ("{ep-hidock}", "Speakers", "HiDock H1", 0x1),
    ]))

    ambiguous = da.ambiguous_matches(twins, "MO27Q28G")
    assert len(ambiguous) == 2
    assert {e.endpoint_id for e in ambiguous} == {"{ep-twin-a}", "{ep-twin-b}"}
    assert da.endpoint_for_monitor(twins, "MO27Q28G") is None


def test_a_name_that_matches_nothing_is_none_and_not_ambiguous(endpoints):
    assert da.endpoint_for_monitor(endpoints, "ACME 9000") is None
    assert da.ambiguous_matches(endpoints, "ACME 9000") == []
    assert da.matches_for_monitor(endpoints, "ACME 9000") == []


def test_an_empty_monitor_name_matches_nothing(endpoints):
    """Otherwise a blank field silently selects the first display endpoint."""
    assert da.endpoint_for_monitor(endpoints, "") is None
    assert da.endpoint_for_monitor(endpoints, "   ") is None


def test_the_full_endpoint_name_also_matches(endpoints):
    """The UI may hand back what it displayed, not the bare monitor name."""
    assert da.endpoint_for_monitor(endpoints, "1 - S2719DGF").endpoint_id == \
        "{ep-dell-active}"


# ── reading the registry: a refused read is never an answer ────────────

def test_a_missing_registry_key_raises_rather_than_returning_empty():
    """An empty list means 'this machine has no render endpoints', which is
    a very different claim from 'the key is not there'."""
    with pytest.raises(da.AudioEndpointError) as exc:
        da.read_endpoint_rows(
            subkey=r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices"
                   r"\Audio\ThisKeyDoesNotExist")
    assert "ThisKeyDoesNotExist" in str(exc.value)


def test_an_unreadable_state_becomes_unknown_not_a_dropped_endpoint():
    built = da.build_endpoints([{
        "endpoint_id": "{ep-unreadable}",
        "friendly_name": "1 - S2719DGF",
        "device_description": "AMD High Definition Audio Device",
        "raw_state": None,
    }])
    assert len(built) == 1
    assert built[0].state is da.EndpointState.UNKNOWN
    assert built[0].raw_state is None


def test_an_endpoint_with_no_readable_name_is_still_listed():
    """A ghost with no Properties subkey is still an endpoint that exists."""
    built = da.build_endpoints([{
        "endpoint_id": "{ep-nameless}",
        "friendly_name": None,
        "device_description": None,
        "raw_state": 0x4,
    }])
    assert built[0].friendly_name == ""
    assert built[0].is_display_audio is False
    assert built[0].state is da.EndpointState.NOTPRESENT


# ── the write paths: refused, on purpose ───────────────────────────────

def test_the_com_write_path_refuses_without_the_supervised_interlock():
    with pytest.raises(da.SupervisionRequired):
        da.set_endpoint_enabled("{0.0.0.00000000}.{not-a-real-endpoint}", False)


def test_the_registry_write_path_refuses_without_the_supervised_interlock():
    with pytest.raises(da.SupervisionRequired):
        da.set_endpoint_enabled_via_registry(
            "{0.0.0.00000000}.{not-a-real-endpoint}", False)


def test_the_vtable_index_is_the_twelfth_interface_method():
    """3 IUnknown slots + 12 IPolicyConfig methods, SetEndpointVisibility last.

    Index 12 would be SetPropertyValue(PCWSTR, const PROPERTYKEY&,
    PROPVARIANT*) — three pointer-shaped arguments, called with a string and
    an int. See the module docstring for the sources.
    """
    assert da.IPOLICYCONFIG_SET_ENDPOINT_VISIBILITY_VTBL_INDEX == 14
    assert da.IPOLICYCONFIG_VISTA_SET_ENDPOINT_VISIBILITY_VTBL_INDEX == 13


def test_the_module_says_out_loud_that_the_com_write_path_is_unverified():
    assert "UNVERIFIED" in (da.__doc__ or "")


# ── engine-layer rule ──────────────────────────────────────────────────

def test_the_engine_layer_imports_no_qt():
    """Same split `scan/`+`store/` keep in TreeSize: testable with no display."""
    source = pathlib.Path(da.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+PyQt6", source, re.M)


def test_the_engine_layer_does_not_need_comtypes():
    source = pathlib.Path(da.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+comtypes", source, re.M)


# ── live, read-only, against the real machine ──────────────────────────

@pytest.fixture(scope="module")
def live_endpoints():
    try:
        return da.list_render_endpoints()
    except da.AudioEndpointError as exc:
        pytest.skip(f"MMDevices\\Audio\\Render is not readable here: {exc}")


@pytest.fixture(scope="module")
def live_display_endpoints(live_endpoints):
    found = [e for e in live_endpoints
             if e.is_display_audio and e.monitor_name is not None]
    if not found:
        pytest.skip("no per-monitor display-audio endpoints on this machine")
    return found


def test_live_the_machine_has_render_endpoints(live_endpoints):
    assert live_endpoints
    for endpoint in live_endpoints:
        assert endpoint.endpoint_id


def test_live_no_endpoint_decodes_to_unknown(live_endpoints):
    """If this fails, Windows used a state bit this module does not know."""
    unknown = [(e.friendly_name, e.raw_state) for e in live_endpoints
               if e.state is da.EndpointState.UNKNOWN]
    assert unknown == []


def test_live_the_display_endpoints_are_named_per_monitor(live_display_endpoints):
    for endpoint in live_display_endpoints:
        assert endpoint.display_index is not None
        assert endpoint.monitor_name


def test_live_undocumented_state_bits_never_change_the_decoded_state(
        live_display_endpoints):
    """The masking is the invariant; the extra bits are not.

    This asserted that every active display endpoint carries `0x10000000`,
    which was true of the machine the module was written on and is not true
    of the one it was rebuilt on. That looked like a driver difference and
    was not: `0x10000000` is `DEVICE_STATE_HIDDEN`, the flag
    `SetEndpointVisibility` toggles, so the old machine simply had those
    endpoints HIDDEN from the sound list. Either value is a legitimate
    reading of a working endpoint, which is exactly why no count of it
    belongs in an assertion.

    What must hold either way is that whatever rides above the documented
    nibble is masked off and does not move the verdict, which is what
    `decode_state` promises and what this now checks.
    """
    for endpoint in live_display_endpoints:
        raw = endpoint.raw_state
        extra = da.undocumented_state_bits(raw)
        assert extra & da.DEVICE_STATE_MASK == 0, \
            f"{endpoint.friendly_name}: 0x{extra:08X} is not above the mask"
        if raw is not None:
            assert da.decode_state(raw) is da.decode_state(raw & ~extra), \
                f"{endpoint.friendly_name}: 0x{extra:08X} changed the verdict"


def test_live_the_default_endpoint_is_one_of_the_enumerated_ones(live_endpoints):
    """Cross-checks the COM read against the registry read.

    Skips when the default genuinely cannot be determined — which is an
    answer of its own, and is not silently turned into 'there is none'.
    """
    result = da.default_render_endpoint_detail()
    if not result.determined:
        pytest.skip(f"default endpoint could not be determined: {result.reason}")
    if result.endpoint_id is None:
        pytest.skip(f"this machine has no default render endpoint: {result.reason}")

    known = {e.endpoint_id for e in live_endpoints}
    assert any(result.endpoint_id.endswith(guid) for guid in known), \
        f"{result.endpoint_id} is not among the {len(known)} enumerated endpoints"


def test_live_the_simple_default_accessor_agrees_with_the_detailed_one():
    detail = da.default_render_endpoint_detail()
    assert da.default_render_endpoint() == detail.endpoint_id


def test_live_the_com_read_survives_owning_its_own_apartment():
    r"""The COM read must run in a process that did NOT already init COM.

    This has to be a subprocess, and the reason is the bug it caught. Under
    pytest, `conftest.py` builds a `QApplication`, which initialises COM for
    the process — so `_Apartment` never owns the apartment and never calls
    `CoUninitialize`. The first version of this module released its
    interface pointers in a `finally` that ran *after* the `with` block had
    already uninitialised the apartment. In-process that is invisible; in a
    plain interpreter it is a use-after-free, and it took the process down
    with SIGSEGV (exit 139 / 0xC0000005) rather than raising anything.

    So: assert on the exit code of a clean interpreter. A crash here is not
    a failed read, it is a crashed app.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(r"""
        import sys
        sys.path.insert(0, %r)
        from modules.monitor_control import display_audio as da
        assert "PyQt6" not in sys.modules, "this must run without Qt"
        result = da.default_render_endpoint_detail()
        print(result.determined, result.endpoint_id, result.reason, sep="|")
    """) % str(pathlib.Path(da.__file__).resolve().parents[2])

    done = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (
        f"clean-interpreter COM read exited {done.returncode}\n"
        f"stdout: {done.stdout}\nstderr: {done.stderr}")
    assert "|" in done.stdout, done.stdout
