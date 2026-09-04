r"""The DDC/CI engine, tested without touching a real monitor's settings.

Everything here is either pure parsing or driven through a fake Dxva2 layer.
Three things this file exists to pin, all of them lessons DDC/CI hands you
the hard way:

* **A monitor that does not answer must SAY so.** DDC/CI is off by default in
  a lot of OSD menus, and `GetVCPFeatureAndVCPFeatureReply` failing is not
  "brightness is 0" -- it is "we could not ask". A control offered on top of a
  monitor that never replies is a slider that silently does nothing.
* **Input sources past 0x0F/0x11/0x12 are vendor-specific.** The only honest
  list is the one the capabilities string claims for VCP 0x60. A fixed list
  offers inputs the panel does not have and hides the ones it does.
* **Physical monitor handles leak** unless `DestroyPhysicalMonitors` runs, so
  the context manager must destroy them even when the body raises.

The single live test reads -- never writes -- and skips when nothing on this
machine answers DDC/CI, which is a normal state, not a failure.
"""
from __future__ import annotations

import pytest

from modules.monitor_control import ddc


# -- a fake Dxva2, so none of this needs a display ----------------------

class FakeApi:
    """Stands in for `ddc.Dxva2Api`, recording what was asked of it."""

    def __init__(self, screens=None, caps=None, vcp=None,
                 read_error=None, caps_error=None, write_error=None,
                 devices=None, device_error=None):
        # screens: {hmonitor: [(handle, description), ...]}
        self.screens = screens if screens is not None else {
            1001: [(0xAA, "Generic PnP Monitor")],
        }
        # devices: {hmonitor: r"\\.\DISPLAY1"} -- what GetMonitorInfoW says.
        self.devices = devices if devices is not None else {}
        self.device_error = device_error
        self.caps = caps or {}
        self.vcp = vcp or {}
        self.read_error = read_error
        self.caps_error = caps_error
        self.write_error = write_error
        self.destroyed = []
        self.writes = []
        self.reads = []

    def enum_hmonitors(self):
        return list(self.screens)

    def open_physical_monitors(self, hmonitor):
        return ddc.MonitorArray(handles=list(self.screens[hmonitor]),
                                payload=("array", hmonitor))

    def monitor_device_name(self, hmonitor):
        if self.device_error:
            raise ddc.DdcError(self.device_error)
        try:
            return self.devices[hmonitor]
        except KeyError:
            raise ddc.DdcError("GetMonitorInfoW failed: "
                               "ERROR_INVALID_MONITOR_HANDLE")

    def destroy(self, array):
        self.destroyed.append(array.payload)

    def get_vcp(self, handle, code):
        self.reads.append((handle, code))
        if self.read_error:
            raise ddc.DdcError(self.read_error)
        try:
            return self.vcp[handle][code]
        except KeyError:
            raise ddc.DdcError(
                "GetVCPFeatureAndVCPFeatureReply(0x%02X) failed: "
                "ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_COMMAND" % code)

    def set_vcp(self, handle, code, value):
        if self.write_error:
            raise ddc.DdcError(self.write_error)
        self.writes.append((handle, code, value))
        vcp_type, _current, maximum = self.vcp[handle][code]
        self.vcp[handle][code] = (vcp_type, value, maximum)

    def capabilities(self, handle):
        if self.caps_error:
            raise ddc.DdcError(self.caps_error)
        try:
            return self.caps[handle]
        except KeyError:
            raise ddc.DdcError("CapabilitiesRequestAndCapabilitiesReply "
                               "failed: ERROR_GRAPHICS_DDCCI_INVALID_DEVICE")


#: Shaped like a real MCCS 2.1 reply: nested value lists inside vcp(), tags
#: in no particular order, and a 0x60 that claims exactly three inputs.
DELL_CAPS = (
    "(prot(monitor)type(LCD)model(S2719DGF)cmds(01 02 03 07 0C E3 F3)"
    "vcp(02 04 05 08 10 12 14(01 05 08 0B) 16 18 1A 52 60(0F 11 12) "
    "AC AE B2 B6 C6 C8 C9 D6(01 04 05) DF)"
    "mswhql(1)asset_eep(40)mccs_ver(2.1))"
)

#: A panel whose 0x60 claims a value outside the standard set. Real HDMI-2
#: (0x12) is absent; 0x1B is the vendor's own. Offering a fixed list would
#: get this monitor exactly backwards in both directions.
VENDOR_CAPS = "(type(lcd)model(MO27Q28G)vcp(10 12 60(0F 11 1B))mccs_ver(2.2))"


def _monitor(handle=0xAA, hmonitor=1001, description="Generic PnP Monitor"):
    return ddc.PhysicalMonitor(handle=handle, description=description,
                               hmonitor=hmonitor)


# -- the capabilities parser -------------------------------------------

def test_parser_reads_the_identity_tags():
    caps = ddc.parse_capabilities(DELL_CAPS)
    assert caps["model"] == "S2719DGF"
    assert caps["type"] == "LCD"
    assert caps["mccs_ver"] == "2.1"


def test_parser_lists_every_vcp_code_the_string_claims():
    caps = ddc.parse_capabilities(DELL_CAPS)
    assert 0x10 in caps["vcp"]      # brightness
    assert 0x12 in caps["vcp"]      # contrast
    assert 0x60 in caps["vcp"]      # input source
    assert 0xDF in caps["vcp"]      # VCP version, last in the list


def test_a_continuous_code_has_no_discrete_values():
    caps = ddc.parse_capabilities(DELL_CAPS)
    assert caps["vcp"][0x10] == ()


def test_nested_value_lists_do_not_swallow_the_codes_after_them():
    caps = ddc.parse_capabilities(DELL_CAPS)
    assert caps["vcp"][0x14] == (0x01, 0x05, 0x08, 0x0B)
    assert 0x16 in caps["vcp"] and 0x52 in caps["vcp"]


def test_the_input_values_are_exactly_what_the_string_claims():
    assert ddc.parse_capabilities(DELL_CAPS)["vcp"][0x60] == (0x0F, 0x11, 0x12)
    assert ddc.parse_capabilities(VENDOR_CAPS)["vcp"][0x60] == (0x0F, 0x11, 0x1B)


def test_cmds_are_parsed_too():
    assert ddc.parse_capabilities(DELL_CAPS)["cmds"] == (
        0x01, 0x02, 0x03, 0x07, 0x0C, 0xE3, 0xF3)


def test_a_well_formed_string_is_not_flagged_malformed():
    caps = ddc.parse_capabilities(DELL_CAPS)
    assert caps["malformed"] is False
    assert caps["vcp_present"] is True


def test_an_unbalanced_string_is_flagged_not_raised():
    caps = ddc.parse_capabilities("(type(lcd)model(X1)vcp(10 12 60(0F 11")
    assert caps["malformed"] is True
    assert caps["model"] == "X1"          # what survived is still reported


def test_garbage_parses_to_nothing_rather_than_exploding():
    caps = ddc.parse_capabilities("\x00\x00 not a capabilities string \xff")
    assert caps["malformed"] is True
    assert caps["vcp"] == {}
    assert caps["vcp_present"] is False


def test_an_empty_reply_is_malformed_and_claims_no_codes():
    caps = ddc.parse_capabilities("")
    assert caps["malformed"] is True
    assert caps["vcp_present"] is False


def test_a_non_hex_vcp_token_is_reported_not_silently_dropped():
    caps = ddc.parse_capabilities("(vcp(10 ZZ 60(0F)))")
    assert caps["vcp"] == {0x10: (), 0x60: (0x0F,)}
    assert "ZZ" in caps["unparsed"]


def test_a_string_with_no_vcp_tag_says_so_instead_of_claiming_zero_codes():
    caps = ddc.parse_capabilities("(prot(monitor)type(lcd)model(X))")
    assert caps["vcp_present"] is False
    assert caps["vcp"] == {}


# -- input source naming -----------------------------------------------

def test_standard_input_values_are_named():
    assert ddc.input_source_name(0x0F) == "DisplayPort-1"
    assert ddc.input_source_name(0x11) == "HDMI-1"
    assert ddc.input_source_name(0x12) == "HDMI-2"


def test_a_vendor_value_is_not_given_an_invented_name():
    name = ddc.input_source_name(0x1B)
    assert "0x1B" in name
    assert "vendor" in name.lower()


# -- enumeration -------------------------------------------------------

def test_every_physical_monitor_behind_every_hmonitor_is_listed():
    api = FakeApi(screens={1: [(0xA1, "Dell S2719DGF")],
                           2: [(0xB1, "Gigabyte MO27Q28G")]})
    monitors = ddc.list_physical_monitors(api=api)
    assert [m.description for m in monitors] == ["Dell S2719DGF",
                                                 "Gigabyte MO27Q28G"]
    assert [m.hmonitor for m in monitors] == [1, 2]


def test_a_monitor_with_no_hmonitor_is_simply_absent_not_an_error():
    """An inactive panel (here: the LG, off the desktop) has no HMONITOR."""
    api = FakeApi(screens={1: [(0xA1, "Dell S2719DGF")],
                           2: [(0xB1, "Gigabyte MO27Q28G")]})
    monitors = ddc.list_physical_monitors(api=api)
    assert ddc.find_monitor(monitors, "LG") is None
    assert len(monitors) == 2


def test_no_monitors_at_all_is_an_empty_list_not_an_exception():
    assert ddc.list_physical_monitors(api=FakeApi(screens={})) == []


# -- the context manager, which is the only thing that closes handles ---

def test_open_monitors_destroys_the_handles_it_opened():
    api = FakeApi(screens={1: [(0xA1, "A")], 2: [(0xB1, "B")]})
    with ddc.open_monitors(api=api) as monitors:
        assert len(monitors) == 2
        assert api.destroyed == []
    assert api.destroyed == [("array", 1), ("array", 2)]


def test_handles_are_destroyed_even_when_the_body_raises():
    api = FakeApi(screens={1: [(0xA1, "A")]})
    with pytest.raises(ZeroDivisionError):
        with ddc.open_monitors(api=api):
            raise ZeroDivisionError("something in the caller blew up")
    assert api.destroyed == [("array", 1)]


def test_close_physical_monitors_destroys_each_array_once():
    api = FakeApi(screens={1: [(0xA1, "A"), (0xA2, "B")]})
    monitors = ddc.list_physical_monitors(api=api)
    ddc.close_physical_monitors(monitors, api=api)
    assert api.destroyed == [("array", 1)]


# -- probe: what the monitor will ACTUALLY answer -----------------------

def _responding_api():
    return FakeApi(
        screens={1: [(0xAA, "Dell S2719DGF")]},
        caps={0xAA: DELL_CAPS},
        vcp={0xAA: {0x10: (0, 75, 100), 0x12: (0, 75, 100),
                    0x60: (0, 0x0F, 0x12)}},
    )


def test_a_responding_monitor_reports_what_it_supports():
    cap = ddc.probe(_monitor(), api=_responding_api())
    assert cap.responded is True
    assert cap.supports_brightness is True
    assert cap.supports_contrast is True
    assert cap.supports_input_source is True
    assert cap.brightness == ddc.VcpValue(code=0x10, current=75, maximum=100)


def test_a_silent_monitor_is_reported_as_not_responding_with_a_reason():
    api = FakeApi(read_error="ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_COMMAND",
                  caps_error="ERROR_GRAPHICS_DDCCI_INVALID_DEVICE")
    cap = ddc.probe(_monitor(), api=api)
    assert cap.responded is False
    assert cap.supports_brightness is False
    assert cap.supports_contrast is False
    assert cap.supports_input_source is False
    assert cap.input_sources == ()
    assert cap.reason
    assert "DDC/CI" in cap.reason
    assert "INVALID" in cap.reason      # the driver's own words, not ours


def test_a_refused_read_is_never_reported_as_a_value_of_zero():
    api = FakeApi(read_error="ERROR_GRAPHICS_DDCCI_INVALID_DEVICE")
    cap = ddc.probe(_monitor(), api=api)
    assert cap.brightness is None
    assert ddc.get_brightness(_monitor(), api=api) is None


def test_a_maximum_of_zero_is_not_an_answer():
    """A continuous control cannot have a maximum of 0; the reply is junk."""
    api = FakeApi(caps={0xAA: DELL_CAPS},
                  vcp={0xAA: {0x10: (0, 0, 0), 0x12: (0, 50, 100),
                              0x60: (0, 0x0F, 0x12)}})
    cap = ddc.probe(_monitor(), api=api)
    assert cap.supports_brightness is False
    assert "0x10" in cap.reason


def test_only_the_claimed_input_values_are_offered():
    api = FakeApi(caps={0xAA: VENDOR_CAPS},
                  vcp={0xAA: {0x10: (0, 50, 100), 0x12: (0, 50, 100),
                              0x60: (0, 0x0F, 0x1B)}})
    cap = ddc.probe(_monitor(), api=api)
    assert cap.input_sources == (0x0F, 0x11, 0x1B)
    assert 0x12 not in cap.input_sources      # standard, but not on THIS panel
    assert cap.input_sources_known is True


def test_reads_that_work_with_no_capabilities_string_leave_inputs_unknown():
    """0x60 answers, so the control exists -- but nothing says which values."""
    api = FakeApi(caps_error="ERROR_GRAPHICS_DDCCI_INVALID_DEVICE",
                  vcp={0xAA: {0x10: (0, 50, 100), 0x12: (0, 50, 100),
                              0x60: (0, 0x0F, 0x12)}})
    cap = ddc.probe(_monitor(), api=api)
    assert cap.responded is True
    assert cap.supports_input_source is True
    assert cap.input_sources == ()
    assert cap.input_sources_known is False
    assert "capabilities" in cap.reason.lower()


def test_a_claimed_code_that_will_not_answer_is_not_called_supported():
    """The string claims 0x12; the panel refuses to report it. Believe the
    panel -- the claim is a promise, the read is the evidence."""
    api = FakeApi(caps={0xAA: DELL_CAPS},
                  vcp={0xAA: {0x10: (0, 50, 100), 0x60: (0, 0x0F, 0x12)}})
    cap = ddc.probe(_monitor(), api=api)
    assert cap.supports_contrast is False
    assert cap.responded is True


def test_capabilities_string_returns_none_when_it_was_refused():
    api = FakeApi(caps_error="ERROR_GRAPHICS_DDCCI_INVALID_DEVICE")
    assert ddc.capabilities_string(_monitor(), api=api) is None


# -- reads --------------------------------------------------------------

def test_get_brightness_reports_current_and_max():
    value = ddc.get_brightness(_monitor(), api=_responding_api())
    assert (value.current, value.maximum) == (75, 100)


def test_get_input_source_reports_the_current_value():
    value = ddc.get_input_source(_monitor(), api=_responding_api())
    assert value.current == 0x0F
    assert value.code == ddc.VCP_INPUT_SOURCE


# -- writes: clamped, validated, verified (fakes only -- never live) ----

def test_clamp_holds_the_value_inside_the_monitors_own_range():
    assert ddc.clamp_vcp(-5, 100) == 0
    assert ddc.clamp_vcp(250, 100) == 100
    assert ddc.clamp_vcp(42, 100) == 42


def test_set_brightness_clamps_to_the_maximum_the_monitor_reported():
    api = _responding_api()
    result = ddc.set_brightness(_monitor(), 250, api=api, settle=0.0)
    assert api.writes == [(0xAA, 0x10, 100)]
    assert result.requested == 250
    assert result.applied == 100
    assert result.ok is True


def test_set_brightness_refuses_when_the_maximum_could_not_be_read():
    """Without a max there is nothing to clamp against, and 100 is a guess."""
    api = FakeApi(read_error="ERROR_GRAPHICS_DDCCI_INVALID_DEVICE")
    result = ddc.set_brightness(_monitor(), 50, api=api, settle=0.0)
    assert result.ok is False
    assert api.writes == []
    assert "could not" in result.reason.lower()


def test_a_write_is_verified_by_reading_it_back():
    api = _responding_api()
    result = ddc.set_brightness(_monitor(), 40, api=api, settle=0.0)
    assert result.verified is True


def test_a_monitor_that_ignores_the_write_is_reported_not_believed():
    """Plenty of panels answer a read and quietly drop the write."""
    api = _responding_api()

    def ignore(handle, code, value):
        pass                              # returns success, changes nothing

    api.set_vcp = ignore
    result = ddc.set_brightness(_monitor(), 40, api=api, settle=0.0)
    assert result.verified is False
    assert "40" in result.reason and "75" in result.reason


def test_an_unverifiable_write_is_unknown_not_success():
    api = _responding_api()
    calls = {"n": 0}
    real_get = api.get_vcp

    def fail_second_read(handle, code):
        calls["n"] += 1
        if calls["n"] > 1 and code == 0x10:
            raise ddc.DdcError("ERROR_GRAPHICS_DDCCI_INVALID_DEVICE")
        return real_get(handle, code)

    api.get_vcp = fail_second_read
    result = ddc.set_brightness(_monitor(), 40, api=api, settle=0.0)
    assert result.verified is None
    assert result.ok is True


def test_a_failed_write_reports_the_drivers_reason():
    api = _responding_api()
    api.write_error = "ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_COMMAND"
    result = ddc.set_brightness(_monitor(), 40, api=api, settle=0.0)
    assert result.ok is False
    assert "INVALID_MESSAGE_COMMAND" in result.reason


def test_set_input_source_refuses_a_value_the_monitor_never_claimed():
    api = FakeApi(caps={0xAA: VENDOR_CAPS},
                  vcp={0xAA: {0x10: (0, 50, 100), 0x12: (0, 50, 100),
                              0x60: (0, 0x0F, 0x1B)}})
    result = ddc.set_input_source(_monitor(), 0x12, api=api, settle=0.0)
    assert result.ok is False
    assert api.writes == []
    assert "0x12" in result.reason


def test_set_input_source_accepts_a_value_the_monitor_does_claim():
    api = FakeApi(caps={0xAA: VENDOR_CAPS},
                  vcp={0xAA: {0x10: (0, 50, 100), 0x12: (0, 50, 100),
                              0x60: (0, 0x0F, 0x1B)}})
    result = ddc.set_input_source(_monitor(), 0x1B, api=api, settle=0.0)
    assert api.writes == [(0xAA, 0x60, 0x1B)]
    assert result.ok is True


def test_set_input_source_refuses_when_the_claimed_values_are_unknown():
    """No capabilities string means no list, and a guess here switches the
    monitor away from the machine that is driving it."""
    api = FakeApi(caps_error="ERROR_GRAPHICS_DDCCI_INVALID_DEVICE",
                  vcp={0xAA: {0x10: (0, 50, 100), 0x60: (0, 0x0F, 0x12)}})
    result = ddc.set_input_source(_monitor(), 0x11, api=api, settle=0.0)
    assert result.ok is False
    assert api.writes == []
    assert "capabilities" in result.reason.lower()


def test_a_write_to_a_silent_monitor_never_reaches_the_driver():
    api = FakeApi(read_error="ERROR_GRAPHICS_DDCCI_INVALID_DEVICE",
                  caps_error="ERROR_GRAPHICS_DDCCI_INVALID_DEVICE")
    result = ddc.set_brightness(_monitor(), 50, api=api, settle=0.0)
    assert result.ok is False
    assert api.writes == []


# -- live, read-only ----------------------------------------------------

@pytest.fixture(scope="module")
def live_probes():
    """Probe the real monitors. Read-only: no SetVCPFeature is ever called."""
    try:
        with ddc.open_monitors() as monitors:
            if not monitors:
                pytest.skip("no physical monitors behind any HMONITOR")
            return [(m.description, ddc.probe(m)) for m in monitors]
    except OSError as exc:            # no Dxva2, no display, no session
        pytest.skip("DDC/CI unavailable on this machine: %s" % exc)


def test_live_every_monitor_gets_a_verdict_with_a_reason(live_probes):
    for description, cap in live_probes:
        assert cap.reason, "%s got no reason for its verdict" % description


def test_live_a_responding_monitor_reports_a_sane_brightness(live_probes):
    answering = [(d, c) for d, c in live_probes if c.supports_brightness]
    if not answering:
        pytest.skip("no monitor on this machine answers DDC/CI brightness "
                    "(DDC/CI is off in the OSD, or the panel ignores it)")
    for description, cap in answering:
        assert cap.brightness is not None
        assert cap.brightness.maximum > 0, description
        assert 0 <= cap.brightness.current <= cap.brightness.maximum


def test_live_claimed_input_values_are_only_ever_the_claimed_ones(live_probes):
    for description, cap in live_probes:
        if not cap.input_sources_known:
            assert cap.input_sources == ()
            continue
        parsed = ddc.parse_capabilities(cap.capabilities_string or "")
        assert cap.input_sources == parsed["vcp"].get(0x60, ()), description


# -- real captures, and the two traps they revealed ---------------------
#
# Both strings below are verbatim replies from this machine
# (AMD RX 7900 XTX; Dell on DisplayPort, Gigabyte on HDMI). Neither was
# invented, and each carries something a hand-written fixture would not:
# the Dell's `60(0F 11 12 )` has a trailing space inside the value list, and
# the Gigabyte identifies itself only in the capabilities string -- Windows
# calls it "Generic PnP Monitor".

DELL_REAL_CAPS = (
    "(prot(monitor)type(LCD)model(S2719DGF)cmds(01 02 03 07 0C E3 F3)"
    "vcp(02 04 05 08 10 12 14(05 08 0B 0C) 16 18 1A 52 60(0F 11 12 ) 62 "
    "AA(01 02 04 ) AC AE B2 B6 C6 C8 C9 CC(02 0A 03 04 08 09 0D 06 ) "
    "D6(01 04 05) DC(00 05 ) DF E0 E1 E2(00 22 20 21 04 1E 1F 1D 0E 12 14 ) "
    "F0(0D 0E 0C 0F 10 11 ) F1 F2 FD)mswhql(1)asset_eep(40)mccs_ver(2.1))"
)

GIGABYTE_REAL_CAPS = (
    "(prot(monitor)type(LCD)model(GIGABYTE)cmds(01 02 03 07 0C E3 F3)"
    "vcp(02 04 05 06 08 0B 0C 10 12 14(04 05 07 08 0B) 16 18 1A 52 "
    "60(0F 10 11 12) 62 87 8D AC AE B2 B6 C6 C8 CA CC(01 02 03 04 06 0A 0D) "
    "D6(01 04 05) DF FD FF)mswhql(1)asset_eep(40)mccs_ver(2.2))"
)


def test_the_real_dell_string_parses_whole():
    caps = ddc.parse_capabilities(DELL_REAL_CAPS)
    assert caps["malformed"] is False
    assert caps["unparsed"] == ()
    assert caps["model"] == "S2719DGF"
    assert caps["mccs_ver"] == "2.1"
    assert len(caps["vcp"]) == 32
    assert caps["vcp"][0x60] == (0x0F, 0x11, 0x12)   # trailing space and all


def test_the_real_gigabyte_string_parses_whole():
    caps = ddc.parse_capabilities(GIGABYTE_REAL_CAPS)
    assert caps["malformed"] is False
    assert caps["unparsed"] == ()
    assert caps["model"] == "GIGABYTE"
    assert caps["vcp"][0x60] == (0x0F, 0x10, 0x11, 0x12)
    assert caps["vcp"][0x14] == (0x04, 0x05, 0x07, 0x08, 0x0B)


def test_a_handle_of_zero_is_a_real_handle():
    """Measured: `GetPhysicalMonitorsFromHMONITOR` hands out 0 for the first
    monitor, and ctypes renders that NULL HANDLE as None. It reads fine --
    so nothing may treat a falsy handle as a missing one."""
    api = FakeApi(screens={7: [(0, "Dell S2719DGF(Displayport)")]},
                  caps={0: DELL_REAL_CAPS},
                  vcp={0: {0x10: (1, 79, 100), 0x12: (1, 75, 100),
                           0x60: (1, 0x0F0F, 0x1212)}})
    monitors = ddc.list_physical_monitors(api=api)
    assert [m.handle for m in monitors] == [0]
    assert ddc.probe(monitors[0], api=api).responded is True


def test_an_input_reply_mirrored_into_the_high_byte_is_read_low_byte_first():
    """The Dell answers 0x60 with 0x0F0F for DisplayPort-1. Read raw, that
    is 3855 and matches no input at all."""
    api = FakeApi(caps={0xAA: DELL_REAL_CAPS},
                  vcp={0xAA: {0x10: (1, 79, 100), 0x12: (1, 75, 100),
                              0x60: (1, 0x0F0F, 0x1212)}})
    cap = ddc.probe(_monitor(), api=api)
    assert cap.input_source.current == 0x0F0F      # kept as reported
    assert cap.input_source.low_byte == 0x0F       # what it means
    assert cap.current_input == 0x0F
    assert cap.current_input_name == "DisplayPort-1"


def test_the_maximum_of_an_enumerated_code_is_not_treated_as_a_range():
    """The Gigabyte claims four inputs and reports a maximum of 3 for 0x60;
    the Dell reports 0x1212. The maximum is junk on both, so it decides
    nothing here."""
    api = FakeApi(caps={0xAA: GIGABYTE_REAL_CAPS},
                  vcp={0xAA: {0x10: (1, 46, 100), 0x12: (1, 60, 100),
                              0x60: (1, 0x11, 3)}})
    cap = ddc.probe(_monitor(), api=api)
    assert cap.supports_input_source is True
    assert cap.current_input == 0x11
    assert cap.input_sources == (0x0F, 0x10, 0x11, 0x12)


def test_input_source_is_still_readable_with_a_maximum_of_zero():
    """A range of 0 damns a continuous control, not an enumerated one."""
    api = FakeApi(caps={0xAA: GIGABYTE_REAL_CAPS},
                  vcp={0xAA: {0x10: (1, 46, 100), 0x12: (1, 60, 100),
                              0x60: (1, 0x11, 0)}})
    cap = ddc.probe(_monitor(), api=api)
    assert cap.supports_input_source is True
    assert cap.current_input == 0x11


def test_the_windows_description_is_not_an_identity():
    """Windows calls the Gigabyte "Generic PnP Monitor"; only the
    capabilities string knows what it is."""
    api = FakeApi(screens={2: [(0xB1, "Generic PnP Monitor")]},
                  caps={0xB1: GIGABYTE_REAL_CAPS},
                  vcp={0xB1: {0x10: (1, 46, 100), 0x12: (1, 60, 100),
                              0x60: (1, 0x11, 3)}})
    monitors = ddc.list_physical_monitors(api=api)
    assert ddc.find_monitor(monitors, "Gigabyte") is None
    assert ddc.probe(monitors[0], api=api).model == "GIGABYTE"


def test_an_input_switch_is_verified_against_the_low_byte_too():
    """A panel that mirrors the value back would fail an exact comparison,
    and a correct switch would be reported as ignored."""
    api = FakeApi(caps={0xAA: DELL_REAL_CAPS},
                  vcp={0xAA: {0x10: (1, 79, 100), 0x12: (1, 75, 100),
                              0x60: (1, 0x0F0F, 0x1212)}})

    def mirror(handle, code, value):
        api.writes.append((handle, code, value))
        vcp_type, _current, maximum = api.vcp[handle][code]
        api.vcp[handle][code] = (vcp_type, (value << 8) | value, maximum)

    api.set_vcp = mirror
    result = ddc.set_input_source(_monitor(), 0x11, api=api, settle=0.0)
    assert api.writes == [(0xAA, 0x60, 0x11)]
    assert result.verified is True


# -- tying a physical monitor to a GDI device ---------------------------
#
# This is the seam the tab needs: a `MonitorView` knows `\\.\DISPLAY2`, and
# a `PhysicalMonitor` is behind an HMONITOR. `GetMonitorInfoW` joins them,
# and it is the ONLY thing that does -- measured on the real machine, where
# the Gigabyte's DDC description is "Generic PnP Monitor" and its view's
# name is "MO27Q28G", so nothing about the two strings matches.

def _paired_api():
    """Two monitors, named as this machine really names them."""
    return FakeApi(
        screens={1: [(0xA1, "Dell S2719DGF(Displayport)")],
                 2: [(0xB1, "Generic PnP Monitor")]},
        devices={1: r"\\.\DISPLAY1", 2: r"\\.\DISPLAY2"},
        caps={0xA1: DELL_REAL_CAPS, 0xB1: GIGABYTE_REAL_CAPS},
        vcp={0xA1: {0x10: (1, 79, 100), 0x12: (1, 75, 100),
                    0x60: (1, 0x0F, 0x1212)},
             0xB1: {0x10: (1, 46, 100), 0x12: (1, 60, 100),
                    0x60: (1, 0x11, 3)}})


def test_a_physical_monitor_carries_the_gdi_device_it_draws_to():
    monitors = ddc.list_physical_monitors(api=_paired_api())
    assert [m.device for m in monitors] == [r"\\.\DISPLAY1", r"\\.\DISPLAY2"]


def test_a_device_name_windows_will_not_give_is_none_not_a_guess():
    """One unreadable name must not cost the other monitors."""
    api = FakeApi(screens={1: [(0xA1, "A")], 2: [(0xB1, "B")]},
                  devices={1: r"\\.\DISPLAY1"})
    monitors = ddc.list_physical_monitors(api=api)
    assert [m.device for m in monitors] == [r"\\.\DISPLAY1", None]
    assert len(monitors) == 2


def test_the_monitor_for_a_device_is_found_by_that_device():
    monitors = ddc.list_physical_monitors(api=_paired_api())
    found = ddc.find_monitor_for_device(monitors, r"\\.\DISPLAY2")
    assert found is not None and found.handle == 0xB1


def test_a_device_nothing_draws_to_is_none_not_the_first_monitor():
    """The LG here is connected and switched off: no HMONITOR at all."""
    monitors = ddc.list_physical_monitors(api=_paired_api())
    assert ddc.find_monitor_for_device(monitors, r"\\.\DISPLAY3") is None
    assert ddc.find_monitor_for_device(monitors, None) is None
    assert ddc.find_monitor_for_device(monitors, "") is None


def test_the_device_match_is_never_a_description_match():
    """`find_monitor` on the name would miss the Gigabyte entirely, which is
    exactly why the pairing goes through the device name."""
    monitors = ddc.list_physical_monitors(api=_paired_api())
    assert ddc.find_monitor(monitors, "MO27Q28G") is None
    assert ddc.find_monitor_for_device(
        monitors, r"\\.\DISPLAY2").description == "Generic PnP Monitor"


# -- probing by device: the handle never outlives the call --------------

def test_probing_by_device_answers_and_gives_the_handle_back():
    api = _paired_api()
    cap = ddc.probe_device(r"\\.\DISPLAY1", api=api)
    assert cap.responded is True
    assert cap.supports_brightness and cap.supports_input_source
    assert cap.brightness.current == 79
    assert api.destroyed == [("array", 1), ("array", 2)]


def test_a_probed_capability_carries_no_handle_to_use_later():
    """`open_monitors` destroyed it on the way out, so keeping it would hand
    the UI a dangling handle to call across the next refresh."""
    cap = ddc.probe_device(r"\\.\DISPLAY1", api=_paired_api())
    assert cap.monitor is None


def test_probing_a_device_that_is_not_on_the_desktop_says_so():
    cap = ddc.probe_device(r"\\.\DISPLAY9", api=_paired_api())
    assert cap.responded is False
    assert cap.reason
    assert r"\\.\DISPLAY9" in cap.reason
    assert cap.supports_brightness is False


# -- writing by device --------------------------------------------------

def test_setting_brightness_by_device_reaches_that_monitor_only():
    api = _paired_api()
    result = ddc.set_brightness_for_device(r"\\.\DISPLAY2", 70, api=api,
                                           settle=0.0)
    assert result.ok is True and result.verified is True
    assert api.writes == [(0xB1, 0x10, 70)]


def test_setting_contrast_by_device_reaches_that_monitor_only():
    api = _paired_api()
    result = ddc.set_contrast_for_device(r"\\.\DISPLAY1", 50, api=api,
                                         settle=0.0)
    assert result.ok is True
    assert api.writes == [(0xA1, 0x12, 50)]


def test_switching_input_by_device_still_validates_the_claimed_list():
    api = _paired_api()
    result = ddc.set_input_source_for_device(r"\\.\DISPLAY1", 0x99, api=api,
                                             settle=0.0)
    assert result.ok is False
    assert api.writes == []
    assert "does not claim" in result.reason


def test_a_write_to_a_device_that_is_not_there_writes_nothing():
    api = _paired_api()
    result = ddc.set_brightness_for_device(r"\\.\DISPLAY9", 70, api=api,
                                           settle=0.0)
    assert result.ok is False
    assert result.verified is None
    assert api.writes == []
    assert r"\\.\DISPLAY9" in result.reason


def test_a_write_by_device_gives_its_handles_back_too():
    api = _paired_api()
    ddc.set_brightness_for_device(r"\\.\DISPLAY1", 70, api=api, settle=0.0)
    assert api.destroyed == [("array", 1), ("array", 2)]
