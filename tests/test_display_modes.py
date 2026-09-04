r"""Enumerating what a display can do, against this machine's two panels.

`EnumDisplaySettingsExW` is a generous API: it answers for devices that are
not attached, it answers for the iGPU's five phantom devices, and it fills a
DEVMODE with zeroes rather than failing. Three of its habits produce a wrong
answer rather than an error, and all three are pinned here:

* **A mode list is per resolution, not per device.** `\\.\DISPLAY1` here
  offers 19 distinct refresh rates across 226 modes, but 280 Hz exists only
  at the lower resolutions. Offering a device's whole rate list at a
  resolution that cannot run them is how a UI proposes a mode that fails.
* **`dmDisplayFrequency` of 0 or 1 means "whatever the hardware defaults
  to".** Both are real values in the enumeration and neither is a rate; 1 Hz
  shown in a dropdown is indistinguishable from a mode that exists.
* **An unattached device has no current mode.** DISPLAY3..DISPLAY5 are real
  entries that answer ENUM_CURRENT_SETTINGS with nothing, and a zeroed
  DEVMODE read as an answer is a 0x0 display at 0 Hz.

The live tests skip cleanly when the machine is not this one.
"""
import pytest

from modules.monitor_control import display_modes as dm


def mode(width, height, refresh, bpp=32, interlaced=False):
    return dm.DisplayMode(width=width, height=height, refresh_hz=refresh,
                          bits_per_pixel=bpp, interlaced=interlaced)


# -- the "hardware default" frequency rule -----------------------------

@pytest.mark.parametrize("frequency", [0, 1])
def test_a_frequency_of_zero_or_one_is_not_a_rate(frequency):
    assert dm.normalize_refresh(frequency) is None


@pytest.mark.parametrize("frequency", [23, 59, 60, 144, 280])
def test_a_real_frequency_survives(frequency):
    assert dm.normalize_refresh(frequency) == float(frequency)


def test_the_hardware_default_never_reaches_the_rate_list():
    modes = [mode(1920, 1080, None), mode(1920, 1080, 60.0),
             mode(1920, 1080, None)]
    assert dm.refresh_rates_from(modes, 1920, 1080) == [60.0]


def test_a_mode_with_no_known_rate_is_still_a_mode():
    """It is a resolution the display supports; only the rate is unknown."""
    modes = [mode(1920, 1080, None)]
    assert dm.resolutions_from(modes) == [(1920, 1080)]


def test_interlaced_comes_from_the_display_flags():
    assert dm.build_mode(1920, 1080, 60, 32, dm.DM_INTERLACED).interlaced is True
    assert dm.build_mode(1920, 1080, 60, 32, 0).interlaced is False


def test_build_mode_applies_the_frequency_rule():
    assert dm.build_mode(1920, 1080, 1, 32, 0).refresh_hz is None
    assert dm.build_mode(1920, 1080, 60, 32, 0).refresh_hz == 60.0


# -- filtering and picking ---------------------------------------------

MIXED = [
    mode(2560, 1440, 60.0),
    mode(2560, 1440, 144.0),
    mode(2560, 1440, 120.0),
    mode(1920, 1080, 60.0),
    mode(1920, 1080, 240.0),
    mode(1920, 1080, 280.0),
    mode(1280, 720, 60.0),
]


def test_refresh_rates_are_filtered_by_resolution():
    """280 Hz is real at 1920x1080 and does not exist at 2560x1440."""
    assert dm.refresh_rates_from(MIXED, 2560, 1440) == [60.0, 120.0, 144.0]
    assert dm.refresh_rates_from(MIXED, 1920, 1080) == [60.0, 240.0, 280.0]


def test_refresh_rates_are_ascending_and_unique():
    modes = MIXED + [mode(2560, 1440, 60.0, bpp=16)]
    assert dm.refresh_rates_from(modes, 2560, 1440) == [60.0, 120.0, 144.0]


def test_an_unsupported_resolution_has_no_rates_and_does_not_raise():
    assert dm.refresh_rates_from(MIXED, 3840, 2160) == []


def test_resolutions_are_biggest_first_and_deduplicated():
    assert dm.resolutions_from(MIXED) == [(2560, 1440), (1920, 1080), (1280, 720)]


def test_best_mode_is_the_highest_resolution_then_the_highest_refresh():
    """Not 280 Hz: that only exists at a resolution smaller than the best."""
    best = dm.best_from(MIXED)
    assert (best.width, best.height) == (2560, 1440)
    assert best.refresh_hz == 144.0


def test_best_mode_prefers_a_known_rate_over_an_unknown_one():
    modes = [mode(2560, 1440, None), mode(2560, 1440, 60.0)]
    assert dm.best_from(modes).refresh_hz == 60.0


def test_best_mode_of_nothing_is_none():
    assert dm.best_from([]) is None
    assert dm.best_from([], native=(2560, 1440)) is None


# -- "best" means the panel's own resolution, not the biggest one -------
#
# The GPU offers downsampled modes above the panel's native resolution
# (AMD VSR here). They are larger, blurrier, and top out at a lower refresh
# rate, so "largest enumerated resolution" recommends the worst of both.

DOWNSAMPLED = [
    mode(3840, 2160, 60.0),
    mode(3840, 2160, 120.0),    # the biggest mode, and not the best one
    mode(2560, 1440, 60.0),
    mode(2560, 1440, 144.0),
    mode(2560, 1440, 280.0),    # the panel's own resolution, at its own rate
    mode(1920, 1080, 240.0),
]


def test_the_native_resolution_beats_a_bigger_downsampled_one():
    best = dm.best_from(DOWNSAMPLED, native=(2560, 1440))
    assert (best.width, best.height) == (2560, 1440)
    assert best.refresh_hz == 280.0


def test_without_a_native_resolution_the_old_rule_still_applies():
    """Unknown native is not a licence to guess -- it falls back, explicitly."""
    best = dm.best_from(DOWNSAMPLED)
    assert (best.width, best.height) == (3840, 2160)
    assert best.refresh_hz == 120.0


def test_a_native_resolution_the_device_cannot_run_falls_back():
    """An EDID that disagrees with the mode list must not empty the answer."""
    best = dm.best_from(DOWNSAMPLED, native=(5120, 2880))
    assert (best.width, best.height) == (3840, 2160)


def test_the_native_rule_still_picks_the_highest_refresh_at_it():
    modes = [mode(2560, 1440, 60.0), mode(2560, 1440, 120.0),
             mode(3840, 2160, 240.0)]
    assert dm.best_from(modes, native=(2560, 1440)).refresh_hz == 120.0


def test_best_mode_passes_the_native_resolution_through(monkeypatch):
    rows = {r"\\.\DISPLAY1": [(3840, 2160, 120, 32, 0),
                              (2560, 1440, 280, 32, 0),
                              (2560, 1440, 60, 32, 0)]}
    monkeypatch.setattr(dm, "_enum_settings", _fake_enum(rows))
    plain = dm.best_mode(r"\\.\DISPLAY1")
    native = dm.best_mode(r"\\.\DISPLAY1", native=(2560, 1440))
    assert (plain.width, plain.height) == (3840, 2160)
    assert (native.width, native.height) == (2560, 1440)
    assert native.refresh_hz == 280.0


def test_a_wider_but_smaller_resolution_does_not_beat_a_bigger_one():
    modes = [mode(2560, 1080, 60.0), mode(2560, 1440, 60.0)]
    assert dm.resolutions_from(modes)[0] == (2560, 1440)


# -- the ctypes edge, stood in for --------------------------------------

def _fake_enum(rows):
    """Stand in for EnumDisplaySettingsExW over a table of DEVMODE values."""
    def enum(device_name, mode_num, devmode):
        table = rows.get(device_name, [])
        if mode_num == dm.ENUM_CURRENT_SETTINGS:
            current = rows.get(device_name + ":current")
            if not current:
                return 0
            width, height, freq, bpp, flags = current
        else:
            if mode_num >= len(table):
                return 0
            width, height, freq, bpp, flags = table[mode_num]
        devmode.dmPelsWidth = width
        devmode.dmPelsHeight = height
        devmode.dmDisplayFrequency = freq
        devmode.dmBitsPerPel = bpp
        devmode.dmDisplayFlags = flags
        return 1
    return enum


def test_modes_are_deduplicated(monkeypatch):
    rows = {r"\\.\DISPLAY1": [(1920, 1080, 60, 32, 0)] * 4
                             + [(1920, 1080, 144, 32, 0)]}
    monkeypatch.setattr(dm, "_enum_settings", _fake_enum(rows))
    assert len(dm.modes_for(r"\\.\DISPLAY1")) == 2


def test_a_device_with_no_modes_returns_empty_rather_than_raising(monkeypatch):
    monkeypatch.setattr(dm, "_enum_settings", _fake_enum({}))
    assert dm.modes_for(r"\\.\DISPLAY9") == []
    assert dm.resolutions(r"\\.\DISPLAY9") == []
    assert dm.refresh_rates_for(r"\\.\DISPLAY9", 1920, 1080) == []
    assert dm.best_mode(r"\\.\DISPLAY9") is None


def test_current_mode_of_an_unattached_device_is_none_not_a_zeroed_mode(monkeypatch):
    monkeypatch.setattr(dm, "_enum_settings", _fake_enum({}))
    assert dm.current_mode(r"\\.\DISPLAY3") is None


def test_a_zero_sized_current_mode_is_not_an_answer(monkeypatch):
    """The call can succeed and leave the DEVMODE zeroed. 0x0 is not a mode."""
    def enum(device_name, mode_num, devmode):
        return 1                       # success, buffer untouched
    monkeypatch.setattr(dm, "_enum_settings", enum)
    assert dm.current_mode(r"\\.\DISPLAY3") is None


def test_current_mode_reads_the_devmode_when_there_is_one(monkeypatch):
    rows = {r"\\.\DISPLAY1:current": (2560, 1440, 60, 32, 0)}
    monkeypatch.setattr(dm, "_enum_settings", _fake_enum(rows))
    current = dm.current_mode(r"\\.\DISPLAY1")
    assert (current.width, current.height) == (2560, 1440)
    assert current.refresh_hz == 60.0



# -- against the real machine ------------------------------------------
#
# Resolved by IDENTITY, never by `\.\DISPLAYn`.
#
# Those names are not stable, and this was measured rather than assumed:
# during the session that wrote this module the two panels swapped names
# under us. `\.\DISPLAY1` went from 226 modes and primary to 117 modes and
# not-primary, while `\.\DISPLAY2` went from 117 modes to 151 and became
# primary. The CCD view did not move at all — targets 256 and 264 kept their
# positions. Only the GDI names were reassigned.
#
# The mode LISTS move too. The Gigabyte offered 144/240/280 Hz at 1440p
# earlier in the same session and offers a 120 Hz maximum now, which is what
# a link renegotiating to less bandwidth looks like. So these tests assert
# STRUCTURE — the native resolution is offered, filtering by resolution
# actually filters, an unattached device has no current mode — and never an
# exact count or an exact rate.

import pytest  # noqa: E402  (the live block is deliberately self-contained)

from modules.monitor_control import display_config as dc  # noqa: E402
from modules.monitor_control import monitor_identity as mi  # noqa: E402


def _live_panels():
    """`{friendly_name: gdi_device_name}` for the monitors actually in use."""
    try:
        topology = dc.query()
    except Exception:                                    # noqa: BLE001
        return {}
    panels = {}
    for path in topology.active_paths():
        name = mi.target_name(path.adapter, path.target_id)
        gdi = mi.source_gdi_name(path.adapter, path.source_id)
        if name and gdi and dm.modes_for(gdi):
            panels[name.friendly_name] = gdi
    return panels


@pytest.fixture(scope="module")
def panels():
    found = _live_panels()
    if not found:
        pytest.skip("no active display with a mode list on this machine")
    return found


def test_live_every_active_panel_has_a_current_mode(panels):
    for friendly, gdi in panels.items():
        assert dm.current_mode(gdi) is not None, f"{friendly} is attached"


def test_live_the_current_mode_is_one_the_panel_offers(panels):
    """A current mode absent from the enumeration means the parser is wrong."""
    for friendly, gdi in panels.items():
        current = dm.current_mode(gdi)
        offered = {(m.width, m.height) for m in dm.modes_for(gdi)}
        assert current.resolution in offered, friendly


def test_live_rates_are_filtered_per_resolution(panels):
    """The whole point of the mode grouping.

    Asserted structurally: somewhere on some panel, one resolution offers a
    different rate set than another. Pinning WHICH rates would pin numbers
    that have already moved once today.
    """
    for gdi in panels.values():
        resolutions = dm.resolutions(gdi)
        if len(resolutions) < 2:
            continue
        rate_sets = {tuple(dm.refresh_rates_for(gdi, w, h))
                     for w, h in resolutions[:6]}
        if len(rate_sets) > 1:
            return
    pytest.skip("no panel here offers differing rates across resolutions")


def test_live_the_native_resolution_wins_over_a_bigger_downsampled_one(panels):
    """`best_mode` must prefer the glass, not the largest mode the GPU takes.

    Only meaningful on a panel whose driver offers a resolution above its
    native one; skipped otherwise rather than asserted vacuously.
    """
    for friendly, gdi in panels.items():
        native = dm.current_mode(gdi).resolution
        biggest = dm.resolutions(gdi)[0]
        if biggest == native:
            continue
        best = dm.best_mode(gdi, native=native)
        naive = dm.best_mode(gdi)
        assert best.resolution == native, friendly
        assert naive.resolution == biggest, friendly
        assert best.refresh_hz == max(dm.refresh_rates_for(gdi, *native))
        return
    pytest.skip("no panel here is offered a resolution above its native one")


def test_live_best_mode_at_native_is_the_fastest_rate_there(panels):
    for friendly, gdi in panels.items():
        native = dm.current_mode(gdi).resolution
        rates = dm.refresh_rates_for(gdi, *native)
        if not rates:
            continue
        best = dm.best_mode(gdi, native=native)
        assert best.refresh_hz == max(rates), friendly


def test_live_an_unattached_device_has_no_current_mode():
    """"Has a mode list" is not "is in use" — only the current mode says so."""
    unattached = [d for d in dm.attached_devices() if not d[2]]
    if not unattached:
        pytest.skip("every display device on this machine is attached")
    for name, _adapter, _attached, _primary in unattached:
        assert dm.current_mode(name) is None, name


def test_live_a_device_with_no_modes_is_not_an_error():
    empty = [d[0] for d in dm.attached_devices() if not dm.modes_for(d[0])]
    if not empty:
        pytest.skip("every display device on this machine reports modes")
    for name in empty:
        assert dm.resolutions(name) == []
        assert dm.best_mode(name) is None


def test_live_there_is_exactly_one_primary():
    devices = dm.attached_devices()
    if not devices:
        pytest.skip("no display devices")
    primaries = [d[0] for d in devices if d[3]]
    assert len(primaries) == 1, f"expected one primary, got {primaries}"
    assert dict((d[0], d[2]) for d in devices)[primaries[0]] is True


def test_live_the_device_list_is_well_formed():
    devices = dm.attached_devices()
    assert devices
    assert all(name.startswith(r"\\.\ ".strip()) for name, _a, _at, _p in devices)
