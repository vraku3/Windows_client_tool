r"""One monitor, assembled from four engines, and what to say about it.

`display_config` knows what is on the desktop, `monitor_identity` knows what
it is called and what its glass actually is, `display_modes` knows what it
could be doing, `display_audio` and `ddc` know the rest. The UI wants one
object per monitor and a sentence at the top, so the joining and the
deciding happen here — with no Qt, so both are testable.

The headline is the part that earns the tab its place. It turns a settings
panel into something that tells you a thing you did not know: on the machine
this was written for, both displays run at 60 Hz on panels offering 144 and
120, and a third is connected and switched off.

Every rule below is a rule about not overstating:

* "Below its best" means a faster rate exists **at the resolution actually
  in use**. 144 Hz at 1080p says nothing about a display running 1440p.
* Not knowing the rates is not the same as knowing they are fine.
* Silence when there is nothing to report — a banner that always says
  something is a banner nobody reads.
* Never recommend a downsampled resolution as an improvement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorView:
    """Everything the UI needs about one monitor, from every engine."""

    target_id: int
    name: str
    connector: str
    adapter: str
    active: bool
    resolution: Optional[Tuple[int, int]]
    position: Optional[Tuple[int, int]]
    refresh_hz: float
    #: Rates offered AT `resolution` — not every rate the device can do.
    rates_at_resolution: Tuple[float, ...]
    #: From the EDID. None when it could not be read; never guessed.
    native_resolution: Optional[Tuple[int, int]]
    device_name: Optional[str]
    #: The `display_audio.AudioEndpoint` this monitor carries, or None. None
    #: covers three different things and the card has to say which: no
    #: endpoint matched, several did (two identical panels are genuinely
    #: indistinguishable by name), or the registry could not be read.
    audio_endpoint: object = None
    #: True only when this monitor's endpoint IS the current default output.
    #: False is never a claim that it is not — the default may simply not
    #: have been readable — so the UI shows a badge on True and says nothing
    #: on False, rather than labelling anything "not default".
    audio_is_default: bool = False
    #: Why there is no endpoint, when there is none. Empty means "nothing to
    #: say" — either one was found, or this monitor simply carries no audio.
    #: A non-empty note is always a case where an answer EXISTS and we could
    #: not give it: two identical panels produce two identically-named live
    #: endpoints, and picking one would be a coin flip presented as a fact.
    audio_note: str = ""
    #: Is this monitor's endpoint hidden from the sound device list? None
    #: when there is no endpoint or its state could not be read — never
    #: False, which would offer an "off" button for something we cannot see
    #: the state of. An endpoint is routinely ACTIVE *and* hidden, so this
    #: is a different question from the endpoint's state.
    audio_hidden: Optional[bool] = None
    #: A `ddc.DdcCapability` with NO live handle, or None when this monitor
    #: has no GDI device to talk to. `responded` False inside it is the
    #: different answer: present, but not speaking DDC/CI.
    ddc: object = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.connector})" if self.connector else self.name


@dataclass(frozen=True)
class RefreshFix:
    """One display that could be running faster at the resolution it is on."""

    target_id: int
    name: str
    device_name: Optional[str]
    resolution: Tuple[int, int]
    from_rate: float
    to_rate: float


def best_available_rate(view: MonitorView) -> Optional[float]:
    """The fastest rate offered at the resolution in use, or None.

    None means "we could not find out" and is deliberately distinct from a
    number — a display whose mode list could not be read must not be
    reported as running at its best.
    """
    if not view.active or not view.rates_at_resolution:
        return None
    return max(view.rates_at_resolution)


def is_below_best(view: MonitorView) -> bool:
    best = best_available_rate(view)
    if best is None or not view.refresh_hz:
        return False
    return view.refresh_hz < best


def is_downsampled(view: MonitorView) -> bool:
    """True when the desktop is larger than the panel's own pixels.

    The GPU renders high and scales down (AMD calls it Virtual Super
    Resolution). Worth surfacing: it costs performance and softens text, and
    it is why "the biggest resolution offered" is not a recommendation.
    """
    if not view.active or not view.resolution or not view.native_resolution:
        return False
    width, height = view.resolution
    native_w, native_h = view.native_resolution
    return width * height > native_w * native_h


def _format_rate(rate: float) -> str:
    """59.94 Hz is a real mode; rounding it to 60 names a different one."""
    if abs(rate - round(rate)) < 0.005:
        return str(int(round(rate)))
    return f"{rate:.2f}".rstrip("0").rstrip(".")


def describe(view: MonitorView) -> str:
    """The one line a monitor's card shows under its name."""
    if not view.active or not view.resolution:
        return "connected, not in use"
    width, height = view.resolution
    text = f"{width}x{height} @ {_format_rate(view.refresh_hz)} Hz"
    if is_downsampled(view):
        native = view.native_resolution
        text += f"  (scaled from {native[0]}x{native[1]})"
    return text


def headline(views: Sequence[MonitorView]) -> str:
    """One sentence about the whole setup, or nothing.

    Nothing is the common case on a machine that is already set up right,
    and it is the point: the banner is hidden when it has no news.
    """
    if not views:
        return ""

    active = [v for v in views if v.active]
    idle = [v for v in views if not v.active]
    behind = [v for v in active if is_below_best(v)]
    if not behind and not idle:
        return ""

    parts = [f"{len(active)} of {len(views)} monitors active"]
    if behind:
        worst = max(behind, key=lambda v: (best_available_rate(v) or 0)
                    - v.refresh_hz)
        parts.append(
            f"{worst.name} at {_format_rate(worst.refresh_hz)} Hz of "
            f"{_format_rate(best_available_rate(worst))} Hz available")
    if idle:
        names = ", ".join(v.name for v in idle)
        parts.append(f"{names} connected but not in use")
    return " · ".join(parts)


def raise_refresh_plan(views: Sequence[MonitorView]) -> List[RefreshFix]:
    """Every display that could go faster, at the resolution it already has.

    The resolution is carried through deliberately: raising a refresh rate
    must not quietly change resolution underneath someone, and the fastest
    mode on the device may well be at a resolution they did not ask for.
    """
    fixes: List[RefreshFix] = []
    for view in views:
        if not is_below_best(view) or not view.resolution:
            continue
        fixes.append(RefreshFix(
            target_id=view.target_id, name=view.name,
            device_name=view.device_name, resolution=view.resolution,
            from_rate=view.refresh_hz, to_rate=best_available_rate(view)))
    return fixes


def build_views(topology=None) -> List[MonitorView]:
    """Assemble a view per monitor from the engines. Read-only.

    Every engine call is guarded: this runs on a live machine where a
    monitor can vanish between two calls, and one unreadable field must not
    cost the whole list.
    """
    from modules.monitor_control import display_config as dc
    from modules.monitor_control import display_modes as dm
    from modules.monitor_control import monitor_identity as mi

    topology = topology if topology is not None else dc.query()
    gdi_by_target = _gdi_names_by_target(topology)

    views: List[MonitorView] = []
    for monitor in topology.monitors():
        record = None
        try:
            record = mi.target_name(monitor.adapter, monitor.target_id)
        except Exception:                                # noqa: BLE001
            logger.debug("No target name for %s", monitor.target_id,
                         exc_info=True)

        device = gdi_by_target.get(monitor.target_id)
        rates: Tuple[float, ...] = ()
        if device and monitor.resolution:
            try:
                rates = tuple(dm.refresh_rates_for(device, *monitor.resolution))
            except Exception:                            # noqa: BLE001
                logger.debug("No rate list for %s", device, exc_info=True)

        native = None
        if record is not None and getattr(record, "device_path", None):
            try:
                native = mi.native_resolution(record.device_path)
            except Exception:                            # noqa: BLE001
                logger.debug("No EDID native resolution for %s",
                             record.device_path, exc_info=True)

        adapter_label = ""
        try:
            adapter_label = mi.adapter_name(monitor.adapter) or ""
        except Exception:                                # noqa: BLE001
            logger.debug("No adapter name", exc_info=True)

        views.append(MonitorView(
            target_id=monitor.target_id,
            name=(record.friendly_name if record and record.friendly_name
                  else f"Display {monitor.target_id}"),
            connector=monitor.connector,
            adapter=adapter_label,
            active=monitor.active,
            resolution=monitor.resolution,
            position=monitor.position,
            refresh_hz=monitor.refresh_hz,
            rates_at_resolution=rates,
            native_resolution=native,
            device_name=device,
        ))
    return _with_hardware(views)


def _gdi_names_by_target(topology) -> Dict[int, str]:
    r"""`{target_id: "\\.\DISPLAY2"}` for every active path.

    Its own function because it is also what pairs a monitor with its DDC
    handle, and a fake topology in a test has to be able to stand in for it.
    """
    from modules.monitor_control import monitor_identity as mi

    names: Dict[int, str] = {}
    for path in topology.active_paths():
        try:
            names[path.target_id] = mi.source_gdi_name(path.adapter,
                                                       path.source_id)
        except Exception:                                # noqa: BLE001
            logger.debug("No GDI name for target %s", path.target_id,
                         exc_info=True)
    return names


def _audio_by_target(views: Sequence[MonitorView]
                     ) -> Dict[int, Tuple[object, bool, str]]:
    """`{target_id: (endpoint, is_default, note)}` per monitor.

    The three cases are kept apart because they are different answers. An
    endpoint with an empty note is a fact. No endpoint with a note is "there
    IS an answer and we could not give it" — two identical panels produce
    two identically-named live endpoints and nothing in the registry
    separates them, so `endpoint_for_monitor` refuses rather than guessing.
    Absent from the map is "this monitor carries no audio", which needs no
    apology.

    The endpoint list is read ONCE for all monitors: it is a walk of every
    render endpoint the machine remembers, ghosts included.
    """
    from modules.monitor_control import display_audio as da

    try:
        endpoints = da.list_render_endpoints()
    except Exception as exc:                             # noqa: BLE001
        logger.debug("Could not read the audio endpoints", exc_info=True)
        note = "the audio endpoint list could not be read (%s)" % exc
        return {view.target_id: (None, False, note) for view in views}

    # The registry subkey is a bare `{guid}` and IMMDevice::GetId returns
    # `{0.0.0.00000000}.{guid}`. Comparing them whole never matches, so
    # every monitor would read as "not the default output".
    default_guid: Optional[str] = None
    try:
        detail = da.default_render_endpoint_detail()
        if detail.endpoint_id:
            default_guid = da.endpoint_guid(detail.endpoint_id)
    except Exception:                                    # noqa: BLE001
        logger.debug("Could not read the default render endpoint",
                     exc_info=True)

    found: Dict[int, Tuple[object, bool, str]] = {}
    for view in views:
        try:
            endpoint = da.endpoint_for_monitor(endpoints, view.name)
        except Exception:                                # noqa: BLE001
            logger.debug("No audio endpoint for %s", view.name, exc_info=True)
            continue
        if endpoint is None:
            # None is "nothing matched" OR "several did". Only the second is
            # worth reporting, and only `ambiguous_matches` can tell them
            # apart -- showing "no audio" for two identical monitors that
            # both have one would be wrong in the more confusing direction.
            rivals = []
            try:
                rivals = da.ambiguous_matches(endpoints, view.name)
            except Exception:                            # noqa: BLE001
                logger.debug("Could not check ambiguity for %s", view.name,
                             exc_info=True)
            if rivals:
                found[view.target_id] = (None, False, (
                    "%d audio endpoints answer to this name and nothing "
                    "tells them apart" % len(rivals)))
            continue
        is_default = False
        if default_guid and endpoint.endpoint_id:
            try:
                is_default = da.endpoint_guid(
                    endpoint.endpoint_id) == default_guid
            except Exception:                            # noqa: BLE001
                logger.debug("Could not compare %s against the default",
                             endpoint.endpoint_id, exc_info=True)
        found[view.target_id] = (endpoint, is_default, "")
    return found


def _ddc_by_target(views: Sequence[MonitorView]) -> Dict[int, object]:
    """`{target_id: DdcCapability}` for the monitors that have a GDI device.

    A monitor with no device name is not probed at all: it is not on the
    desktop, so it has no HMONITOR and there is nothing to talk to.

    Each probe opens and closes its own physical monitor handles, so
    nothing here outlives the call — a handle kept across a refresh is a
    handle `DestroyPhysicalMonitors` has already taken back.
    """
    from modules.monitor_control import ddc

    found: Dict[int, object] = {}
    for view in views:
        if not view.active or not view.device_name:
            continue
        try:
            capability = ddc.probe_device(view.device_name)
        except Exception:                                # noqa: BLE001
            # No Dxva2.dll, no session, no display: a fact about the
            # machine, and not one that costs the monitor list.
            logger.debug("Could not probe DDC/CI for %s", view.device_name,
                         exc_info=True)
            continue
        if capability is not None:
            found[view.target_id] = capability
    return found


def _with_hardware(views: Sequence[MonitorView]) -> List[MonitorView]:
    """Fill in what audio and DDC know, guarded the same way as the rest."""
    audio = _audio_by_target(views)
    capabilities = _ddc_by_target(views)
    filled: List[MonitorView] = []
    for view in views:
        endpoint, is_default, note = audio.get(view.target_id,
                                               (None, False, ""))
        filled.append(replace(view, audio_endpoint=endpoint,
                              audio_is_default=is_default,
                              audio_note=note,
                              audio_hidden=_hidden_state(endpoint),
                              ddc=capabilities.get(view.target_id)))
    return filled


def _hidden_state(endpoint) -> Optional[bool]:
    """Is this endpoint hidden, or don't we know? Never a guess."""
    if endpoint is None:
        return None
    from modules.monitor_control import display_audio as da

    try:
        return da.is_hidden(getattr(endpoint, "raw_state", None))
    except Exception:                                    # noqa: BLE001
        logger.debug("Could not read the hidden flag", exc_info=True)
        return None
