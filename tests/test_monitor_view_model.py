r"""What the tab says about your monitors, decided outside the widget.

The headline banner is the part of this feature that earns its place: it
turns a settings panel into something that tells you a thing you did not
know. On the machine this was written for it has something to say — both
displays run at 60 Hz on panels that offer 144 and 120.

That makes it worth testing properly, which means keeping it out of the
widget. Everything here is a pure function over view models.

The rules it has to get right are all "do not overstate":

* A monitor is only "running below its best" when a FASTER rate exists **at
  the resolution it is actually using**. Offering 144 Hz at 1080p says
  nothing about a display running 1440p.
* Silence when there is nothing to report. A banner that always says
  something is a banner nobody reads.
* Never recommend a downsampled resolution as an improvement.
"""
import pytest

from modules.monitor_control import view_model as vm


def _view(name="Panel", active=True, resolution=(2560, 1440), refresh=60.0,
          rates=(60.0, 120.0, 144.0), native=(2560, 1440), target_id=1,
          connector="HDMI"):
    return vm.MonitorView(
        target_id=target_id, name=name, connector=connector, adapter="GPU",
        active=active, resolution=resolution, position=(0, 0),
        refresh_hz=refresh, rates_at_resolution=tuple(rates),
        native_resolution=native, device_name=r"\\.\DISPLAY1",
        audio_endpoint=None, audio_is_default=False, audio_note="",
        ddc=None)


# ── running below the panel's best ─────────────────────────────────────

def test_a_display_at_60_on_a_144_panel_is_below_its_best():
    view = _view(refresh=60.0, rates=(60.0, 120.0, 144.0))
    assert vm.best_available_rate(view) == 144.0
    assert vm.is_below_best(view) is True


def test_a_display_already_at_its_fastest_is_not_flagged():
    view = _view(refresh=144.0, rates=(60.0, 120.0, 144.0))
    assert vm.is_below_best(view) is False


def test_rates_at_another_resolution_do_not_count():
    """144 Hz at 1080p says nothing about a display running 1440p."""
    view = _view(refresh=60.0, resolution=(2560, 1440), rates=(60.0,))
    assert vm.is_below_best(view) is False


def test_an_inactive_display_is_never_flagged():
    view = _view(active=False, resolution=None, refresh=0.0, rates=())
    assert vm.is_below_best(view) is False


def test_a_display_with_no_known_rates_is_not_flagged():
    """Not knowing is not the same as knowing it is fine."""
    view = _view(refresh=60.0, rates=())
    assert vm.is_below_best(view) is False
    assert vm.best_available_rate(view) is None


# ── the headline ───────────────────────────────────────────────────────

def test_the_headline_counts_active_against_connected():
    views = [_view(name="A", target_id=1), _view(name="B", target_id=2),
             _view(name="C", target_id=3, active=False, resolution=None,
                   refresh=0.0, rates=())]
    assert "2 of 3" in vm.headline(views)


def test_the_headline_names_the_worst_offender_and_its_ceiling():
    views = [
        _view(name="Fast", target_id=1, refresh=144.0,
              rates=(60.0, 144.0)),
        _view(name="Slow", target_id=2, refresh=60.0,
              rates=(60.0, 120.0, 240.0)),
    ]
    text = vm.headline(views)
    assert "Slow" in text
    assert "240" in text
    assert "Fast" not in text, "named a display that is already at its best"


def test_the_headline_is_empty_when_everything_is_at_its_best():
    views = [_view(name="A", refresh=144.0, rates=(60.0, 144.0)),
             _view(name="B", target_id=2, refresh=120.0, rates=(120.0,))]
    assert vm.headline(views) == ""


def test_the_headline_mentions_a_disconnected_monitor():
    views = [_view(name="A", refresh=144.0, rates=(144.0,)),
             _view(name="Idle", target_id=2, active=False, resolution=None,
                   refresh=0.0, rates=())]
    assert "Idle" in vm.headline(views)


def test_no_monitors_at_all_says_nothing():
    assert vm.headline([]) == ""


# ── the one-click fix ──────────────────────────────────────────────────

def test_the_fix_targets_every_display_below_its_best():
    views = [
        _view(name="A", target_id=1, refresh=60.0, rates=(60.0, 144.0)),
        _view(name="B", target_id=2, refresh=120.0, rates=(120.0,)),
        _view(name="C", target_id=3, refresh=60.0, rates=(60.0, 240.0)),
    ]
    fixes = vm.raise_refresh_plan(views)
    assert [(f.target_id, f.to_rate) for f in fixes] == [(1, 144.0), (3, 240.0)]


def test_the_fix_keeps_the_resolution_it_is_already_using():
    """Raising the rate must not also change resolution underneath someone."""
    view = _view(refresh=60.0, resolution=(2560, 1440),
                 rates=(60.0, 240.0), native=(2560, 1440))
    fix = vm.raise_refresh_plan([view])[0]
    assert fix.resolution == (2560, 1440)


def test_nothing_to_fix_is_an_empty_plan():
    assert vm.raise_refresh_plan([_view(refresh=144.0, rates=(144.0,))]) == []


# ── how a monitor is described in its card ─────────────────────────────

def test_an_active_monitor_reads_as_its_mode():
    view = _view(name="S2719DGF", refresh=60.0)
    assert vm.describe(view) == "2560x1440 @ 60 Hz"


def test_a_fractional_rate_keeps_its_decimal():
    """59.94 Hz is a real mode and rounding it to 60 names a different one."""
    view = _view(refresh=59.94, rates=(59.94,))
    assert "59.94" in vm.describe(view)


def test_an_inactive_monitor_says_so_rather_than_showing_zeroes():
    view = _view(active=False, resolution=None, refresh=0.0, rates=())
    assert vm.describe(view) == "connected, not in use"


def test_a_downsampled_resolution_is_called_out():
    """Running above native means the GPU is scaling — worth knowing."""
    view = _view(resolution=(3840, 2160), native=(2560, 1440), refresh=120.0)
    assert vm.is_downsampled(view) is True
    assert "scaled" in vm.describe(view).lower()


def test_running_at_native_is_not_called_downsampled():
    assert vm.is_downsampled(_view(resolution=(2560, 1440),
                                   native=(2560, 1440))) is False


def test_an_unknown_native_resolution_makes_no_claim():
    assert vm.is_downsampled(_view(native=None)) is False


# ── the hardware fields: audio and DDC ─────────────────────────────────
#
# `build_views` joins five engines now. The rule that governs all of it is
# the one the module already lives by: ONE unreadable field must not cost
# the list. A machine with no DDC at all, or an unreadable audio registry,
# still has to produce a full set of monitors.

class _FakeEndpoint:
    def __init__(self, endpoint_id, name):
        self.endpoint_id = endpoint_id
        self.friendly_name = name
        self.label = name


class _FakeCap:
    def __init__(self, responded=True, reason="DDC/CI responding"):
        self.responded = responded
        self.reason = reason


def _two_monitors(monkeypatch, *, audio=None, ddc_by_device=None,
                  default_id=None, audio_raises=None, ddc_raises=None,
                  ambiguous=None):
    """Stand `build_views` up over fakes for every engine it calls."""
    from modules.monitor_control import display_audio as da
    from modules.monitor_control import ddc as ddc_mod
    from modules.monitor_control import display_config as dc
    from modules.monitor_control import display_modes as dm
    from modules.monitor_control import monitor_identity as mi

    class _Mon:
        def __init__(self, target_id, active, device):
            self.target_id = target_id
            self.adapter = ("LUID", 1)
            self.connector = "HDMI"
            self.active = active
            self.resolution = (2560, 1440) if active else None
            self.position = (0, 0)
            self.refresh_hz = 144.0 if active else 0.0
            self.device = device

    class _Topology:
        def monitors(self):
            return [_Mon(520, True, r"\\.\DISPLAY2"),
                    _Mon(521, False, None)]

        def active_paths(self):
            return []

    names = {520: "MO27Q28G", 521: "LG ULTRAWIDE"}

    class _Record:
        def __init__(self, target_id):
            self.friendly_name = names[target_id]
            self.device_path = None

    monkeypatch.setattr(dc, "query", lambda: _Topology())
    monkeypatch.setattr(mi, "target_name",
                        lambda adapter, target_id: _Record(target_id))
    monkeypatch.setattr(mi, "source_gdi_name",
                        lambda adapter, source_id: r"\\.\DISPLAY2")
    monkeypatch.setattr(mi, "adapter_name", lambda adapter: "GPU")
    monkeypatch.setattr(dm, "refresh_rates_for",
                        lambda device, w, h: (60.0, 144.0))

    # The GDI device name is what pairs a view to a physical monitor, and
    # `build_views` gets it from the topology's active paths — which the
    # fake leaves empty, so set it directly on the monitors instead.
    monkeypatch.setattr(
        vm, "_gdi_names_by_target",
        lambda topology: {520: r"\\.\DISPLAY2"})

    def _list_endpoints():
        if audio_raises:
            raise audio_raises
        return list(audio or [])

    def _endpoint_for(endpoints, monitor_name):
        if (ambiguous or {}).get(monitor_name):
            return None
        for endpoint in endpoints:
            if endpoint.friendly_name.endswith(monitor_name):
                return endpoint
        return None

    monkeypatch.setattr(da, "list_render_endpoints", _list_endpoints)
    monkeypatch.setattr(da, "endpoint_for_monitor", _endpoint_for)
    monkeypatch.setattr(
        da, "ambiguous_matches",
        lambda endpoints, monitor_name: (ambiguous or {}).get(monitor_name, []))
    monkeypatch.setattr(da, "endpoint_guid",
                        lambda value: value.rsplit(".", 1)[-1])
    monkeypatch.setattr(
        da, "default_render_endpoint_detail",
        lambda: type("R", (), {"endpoint_id": default_id,
                               "determined": default_id is not None,
                               "reason": "fake"})())

    def _probe_device(device_name, api=None):
        if ddc_raises:
            raise ddc_raises
        return (ddc_by_device or {}).get(device_name)

    monkeypatch.setattr(ddc_mod, "probe_device", _probe_device)


def test_build_views_fills_the_audio_endpoint_for_the_monitor_it_belongs_to(
        monkeypatch):
    endpoint = _FakeEndpoint("{guid-a}", "2 - MO27Q28G")
    _two_monitors(monkeypatch, audio=[endpoint])
    views = {v.target_id: v for v in vm.build_views()}
    assert views[520].audio_endpoint is endpoint
    assert views[521].audio_endpoint is None


def test_the_default_output_is_matched_on_the_trailing_guid(monkeypatch):
    """The registry key is the bare guid; `IMMDevice::GetId` returns the
    full `{0.0.0.00000000}.{guid}` form. Comparing them whole never
    matches, and every monitor would read as "not the default"."""
    endpoint = _FakeEndpoint("{guid-a}", "2 - MO27Q28G")
    _two_monitors(monkeypatch, audio=[endpoint],
                  default_id="{0.0.0.00000000}.{guid-a}")
    views = {v.target_id: v for v in vm.build_views()}
    assert views[520].audio_is_default is True


def test_a_monitor_that_is_not_the_default_output_says_so(monkeypatch):
    endpoint = _FakeEndpoint("{guid-a}", "2 - MO27Q28G")
    _two_monitors(monkeypatch, audio=[endpoint],
                  default_id="{0.0.0.00000000}.{guid-b}")
    views = {v.target_id: v for v in vm.build_views()}
    assert views[520].audio_is_default is False


def test_an_unreadable_audio_registry_costs_no_monitors(monkeypatch):
    _two_monitors(monkeypatch,
                  audio_raises=OSError("access denied reading MMDevices"))
    views = vm.build_views()
    assert len(views) == 2
    assert all(v.audio_endpoint is None for v in views)


def test_build_views_probes_ddc_for_the_device_the_monitor_draws_to(
        monkeypatch):
    cap = _FakeCap()
    _two_monitors(monkeypatch, ddc_by_device={r"\\.\DISPLAY2": cap})
    views = {v.target_id: v for v in vm.build_views()}
    assert views[520].ddc is cap


def test_a_monitor_with_no_gdi_device_is_never_probed(monkeypatch):
    """The LG here is connected and switched off: no device, no HMONITOR,
    nothing to talk DDC/CI to. Probing anyway would pair it with whichever
    monitor answered first."""
    cap = _FakeCap()
    _two_monitors(monkeypatch, ddc_by_device={r"\\.\DISPLAY2": cap})
    views = {v.target_id: v for v in vm.build_views()}
    assert views[521].ddc is None


def test_a_machine_with_no_ddc_at_all_still_lists_its_monitors(monkeypatch):
    _two_monitors(monkeypatch, ddc_raises=OSError("Dxva2.dll is not here"))
    views = vm.build_views()
    assert len(views) == 2
    assert all(v.ddc is None for v in views)


# ── "we could not tell" is not "there is none" ─────────────────────────

def test_two_endpoints_answering_to_one_name_say_so_rather_than_guessing(
        monkeypatch):
    """Two identical monitors produce two identically-named live endpoints
    and nothing in the registry separates them. Picking one would be a coin
    flip shown as a fact."""
    rivals = [_FakeEndpoint("{guid-a}", "2 - MO27Q28G"),
              _FakeEndpoint("{guid-b}", "2 - MO27Q28G")]
    _two_monitors(monkeypatch, audio=rivals, ambiguous={"MO27Q28G": rivals})
    views = {v.target_id: v for v in vm.build_views()}
    assert views[520].audio_endpoint is None
    assert "2 audio endpoints" in views[520].audio_note


def test_a_monitor_that_simply_carries_no_audio_makes_no_excuse(monkeypatch):
    """Absence with nothing to explain gets no note — a card that apologises
    for every monitor without speakers is noise."""
    _two_monitors(monkeypatch, audio=[])
    views = {v.target_id: v for v in vm.build_views()}
    assert views[520].audio_endpoint is None
    assert views[520].audio_note == ""


def test_an_unreadable_endpoint_list_is_a_note_not_a_silent_absence(
        monkeypatch):
    _two_monitors(monkeypatch,
                  audio_raises=OSError("access denied reading MMDevices"))
    for view in vm.build_views():
        assert view.audio_endpoint is None
        assert "could not be read" in view.audio_note
