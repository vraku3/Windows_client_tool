r"""Counting windows per monitor, and putting them back where they were.

Three rules are defended here, and each of them is a bug that has a name:

* **Cloaked windows are not on screen.** DWM cloaking is how a suspended UWP
  app, a background `ApplicationFrameHost` shell and Explorer's hidden helper
  windows stay alive with a valid, visible, non-tool, non-zero-size window.
  `IsWindowVisible` returns True for all of them. Counting them turns "3
  windows will move to the Dell" into a number two or three times too large,
  which is the one thing this census exists to get right.

* **A maximised or snapped window is restored with `SetWindowPlacement`.**
  `MoveWindow` sets the window's *current* rect. Handed a maximised window it
  un-maximises it into the maximised rect, so the window comes back the size
  of a whole monitor with no way to restore it down. `WINDOWPLACEMENT` carries
  `showCmd` and the *restored* rect as separate fields, which is the only
  representation that can express "maximised on that monitor, and this big
  when you un-maximise it".

* **A monitor is named by its EDID identity.** HMONITOR values are handles
  that change every session and `\\.\DISPLAY2` renumbers when a cable moves.
  A layout that cannot find its monitor refuses, by name.

Everything below the census section runs on synthetic layouts, deliberately:
the restore path is never executed against this machine's real windows.
"""
from __future__ import annotations

import pytest

from modules.monitor_control import window_census as C
from modules.monitor_control import window_layout as L

# HMONITOR stand-ins. Deliberately not 1 and 2 — a handle is not an index.
MON_A = 41814615
MON_B = 105056631

KEY_A = "GBT-273C-25362F004687"
KEY_B = "DEL-D0E6-8DYM7P2"


class FakeProbe:
    """Everything `census`/`capture` asks Windows, answered from a table."""

    def __init__(self, windows, monitors=None):
        self._windows = {w["hwnd"]: w for w in windows}
        self._monitors = monitors or {}
        self.placements_written = []

    # ── enumeration ──
    def enum_windows(self):
        return list(self._windows)

    def is_visible(self, hwnd):
        return self._windows[hwnd].get("visible", True)

    def ex_style(self, hwnd):
        return self._windows[hwnd].get("ex_style", 0)

    def window_rect(self, hwnd):
        return self._windows[hwnd].get("rect", (0, 0, 800, 600))

    def is_cloaked(self, hwnd):
        return self._windows[hwnd].get("cloaked", False)

    def monitor_of(self, hwnd):
        return self._windows[hwnd].get("monitor", MON_A)

    def title(self, hwnd):
        return self._windows[hwnd].get("title", "")

    def process_id(self, hwnd):
        return self._windows[hwnd].get("pid", 1000)

    def process_name(self, hwnd):
        w = self._windows[hwnd]
        return w.get("process", "explorer.exe"), w.get("process_reason", "")

    # ── layout ──
    def placement(self, hwnd):
        return self._windows[hwnd].get("placement", _normal())

    def monitors(self):
        return dict(self._monitors)

    def set_placement(self, hwnd, placement):
        self.placements_written.append((hwnd, placement))
        return True


def _normal(rect=(100, 100, 900, 700)):
    return L.WindowPlacement(show_cmd=L.SW_SHOWNORMAL, flags=0,
                             min_position=(-1, -1), max_position=(-1, -1),
                             normal_rect=rect)


def _maximised(normal_rect=(100, 100, 900, 700)):
    return L.WindowPlacement(show_cmd=L.SW_SHOWMAXIMIZED, flags=0,
                             min_position=(-1, -1), max_position=(-8, -8),
                             normal_rect=normal_rect)


def _minimised(normal_rect=(100, 100, 900, 700)):
    return L.WindowPlacement(show_cmd=L.SW_SHOWMINIMIZED, flags=0,
                             min_position=(-32000, -32000), max_position=(-1, -1),
                             normal_rect=normal_rect)


# ══ the census ═════════════════════════════════════════════════════════

def test_an_ordinary_window_is_counted():
    probe = FakeProbe([{"hwnd": 1, "title": "Notepad", "monitor": MON_A}])
    assert C.windows_per_monitor(probe=probe) == {MON_A: 1}


def test_a_cloaked_window_is_not_counted():
    """The rule this module exists for. The window is visible, has no
    WS_EX_TOOLWINDOW, and has a real rect — every other check passes it."""
    probe = FakeProbe([
        {"hwnd": 1, "title": "Notepad", "monitor": MON_A},
        {"hwnd": 2, "title": "Settings", "monitor": MON_A, "cloaked": True},
        {"hwnd": 3, "title": "Mail", "monitor": MON_A, "cloaked": True},
    ])
    assert C.windows_per_monitor(probe=probe) == {MON_A: 1}


def test_the_cloaked_window_is_reported_with_its_reason_not_dropped_silently():
    probe = FakeProbe([{"hwnd": 2, "title": "Settings", "cloaked": True}])
    excluded = C.census(probe=probe).excluded
    assert [e.hwnd for e in excluded] == [2]
    assert excluded[0].reason == C.SKIP_CLOAKED


def test_an_invisible_window_is_not_counted():
    probe = FakeProbe([{"hwnd": 1, "visible": False}])
    assert C.windows_per_monitor(probe=probe) == {}


def test_a_tool_window_is_not_counted():
    probe = FakeProbe([{"hwnd": 1, "ex_style": C.WS_EX_TOOLWINDOW}])
    assert C.windows_per_monitor(probe=probe) == {}


def test_a_tool_window_is_detected_by_the_bit_not_by_equality():
    """Real tool windows carry other extended styles alongside it."""
    probe = FakeProbe([{"hwnd": 1, "ex_style": C.WS_EX_TOOLWINDOW | 0x00080000}])
    assert C.windows_per_monitor(probe=probe) == {}


def test_a_zero_size_window_is_not_counted():
    probe = FakeProbe([
        {"hwnd": 1, "rect": (0, 0, 0, 0)},
        {"hwnd": 2, "rect": (500, 500, 500, 900)},   # zero width
        {"hwnd": 3, "rect": (500, 500, 900, 500)},   # zero height
    ])
    assert C.windows_per_monitor(probe=probe) == {}


def test_windows_are_counted_per_monitor():
    probe = FakeProbe([
        {"hwnd": 1, "monitor": MON_A}, {"hwnd": 2, "monitor": MON_A},
        {"hwnd": 3, "monitor": MON_B},
    ])
    assert C.windows_per_monitor(probe=probe) == {MON_A: 2, MON_B: 1}


def test_a_monitor_with_no_windows_is_absent_rather_than_zero():
    """`windows_per_monitor` speaks about windows, not about monitors — the
    caller holds the monitor list and asks with `.get(handle, 0)`."""
    probe = FakeProbe([{"hwnd": 1, "monitor": MON_A}])
    assert MON_B not in C.windows_per_monitor(probe=probe)


def test_a_window_whose_cloak_state_is_unknown_is_counted_and_disclosed():
    """`DwmGetWindowAttribute` can fail. Per this project's standing rule a
    refused read is not an answer: the window is not silently dropped and it
    is not silently trusted — it counts, and the count says so."""
    probe = FakeProbe([
        {"hwnd": 1, "monitor": MON_A},
        {"hwnd": 2, "monitor": MON_A, "cloaked": None},
    ])
    result = C.census(probe=probe)
    assert result.per_monitor() == {MON_A: 2}
    assert [u.hwnd for u in result.undetermined] == [2]
    assert "cloak" in result.undetermined[0].reason.lower()


def test_a_window_on_no_monitor_is_not_invented_onto_one():
    """`MonitorFromWindow` is asked with MONITOR_DEFAULTTONEAREST, so a None
    here means the call itself failed, not that the window is off-screen."""
    probe = FakeProbe([{"hwnd": 1, "monitor": None}, {"hwnd": 2, "monitor": MON_A}])
    result = C.census(probe=probe)
    assert result.per_monitor() == {MON_A: 1}
    assert [u.hwnd for u in result.unattributed] == [1]
    assert result.unattributed[0].reason


def test_a_process_name_that_could_not_be_read_is_none_not_empty():
    probe = FakeProbe([{"hwnd": 1, "process": None,
                        "process_reason": "OpenProcess refused: access denied"}])
    window = C.census(probe=probe).windows[0]
    assert window.process_name is None
    assert "denied" in window.process_name_reason


def test_the_census_totals_add_up():
    """Every enumerated window lands in exactly one bucket."""
    probe = FakeProbe([
        {"hwnd": 1}, {"hwnd": 2, "cloaked": True}, {"hwnd": 3, "visible": False},
        {"hwnd": 4, "monitor": None}, {"hwnd": 5, "cloaked": None},
    ])
    result = C.census(probe=probe)
    assert len(result.windows) + len(result.excluded) + len(result.unattributed) == 5


# ══ capture ════════════════════════════════════════════════════════════

GEOM_A = L.MonitorGeometry(key=KEY_A, name="MO27Q28G", device=r"\\.\DISPLAY1",
                           bounds=(0, 0, 2560, 1440), work_area=(0, 0, 2560, 1392),
                           primary=True)
GEOM_B = L.MonitorGeometry(key=KEY_B, name="S2719DGF", device=r"\\.\DISPLAY2",
                           bounds=(2560, 0, 5120, 1440),
                           work_area=(2560, 0, 5120, 1440), primary=False)


def _capture_probe(windows):
    return FakeProbe(windows, monitors={MON_A: GEOM_A, MON_B: GEOM_B})


def test_capture_records_the_monitor_by_edid_key_not_by_handle_or_device():
    probe = _capture_probe([{"hwnd": 1, "title": "Notepad", "monitor": MON_B}])
    record = L.capture(probe=probe).windows[0]
    assert record.monitor_key == KEY_B
    assert MON_B not in (record.monitor_key,)
    assert r"\\.\DISPLAY2" != record.monitor_key


def test_capture_records_the_placement_not_just_the_rect():
    probe = _capture_probe([{"hwnd": 1, "placement": _maximised((10, 20, 810, 620))}])
    record = L.capture(probe=probe).windows[0]
    assert record.placement.show_cmd == L.SW_SHOWMAXIMIZED
    assert record.placement.normal_rect == (10, 20, 810, 620)


def test_capture_records_the_monitor_geometry_it_saw():
    """The restore needs the monitor's origin *at capture time* to work out
    how far the desktop has shifted since."""
    layout = L.capture(probe=_capture_probe([{"hwnd": 1}]))
    assert layout.monitors[KEY_A].bounds == (0, 0, 2560, 1440)


def test_a_window_whose_monitor_is_unknown_carries_the_reason():
    probe = FakeProbe([{"hwnd": 1, "monitor": 999}], monitors={MON_A: GEOM_A})
    record = L.capture(probe=probe).windows[0]
    assert record.monitor_key is None
    assert record.monitor_reason


def test_the_layout_survives_a_round_trip_through_json():
    layout = L.capture(probe=_capture_probe([
        {"hwnd": 1, "title": "Notepad", "monitor": MON_A,
         "placement": _maximised()},
        {"hwnd": 2, "title": "Explorer", "monitor": MON_B},
    ]))
    assert L.WindowLayout.from_dict(layout.to_dict()) == layout


# ══ the restore rule ═══════════════════════════════════════════════════

def _layout(*records, monitors=None):
    return L.WindowLayout(
        captured_at="2026-09-03T12:00:00",
        monitors=monitors if monitors is not None else {KEY_A: GEOM_A, KEY_B: GEOM_B},
        windows=list(records))


def _record(hwnd=1, title="Notepad", key=KEY_A, placement=None, rect=None):
    placement = placement or _normal()
    return L.WindowRecord(
        hwnd=hwnd, title=title, process_name="notepad.exe", process_id=1000,
        placement=placement, rect=rect or placement.normal_rect,
        monitor_key=key, monitor_reason="")


def test_every_restore_goes_through_set_window_placement():
    layout = _layout(_record(1, placement=_normal()),
                     _record(2, placement=_maximised()),
                     _record(3, placement=_minimised()))
    plan = L.plan_restore(layout, {KEY_A: GEOM_A, KEY_B: GEOM_B})
    assert [a.method for a in plan.actions] == [L.SET_WINDOW_PLACEMENT] * 3


def test_a_maximised_window_keeps_its_maximised_state():
    layout = _layout(_record(placement=_maximised()))
    action = L.plan_restore(layout, {KEY_A: GEOM_A}).actions[0]
    assert action.placement.show_cmd == L.SW_SHOWMAXIMIZED


def test_a_maximised_window_is_restored_with_its_restored_rect_not_its_screen_rect():
    """This is the whole reason `MoveWindow` is wrong. On screen the window
    occupies the entire monitor; its restored size is 800x600. `MoveWindow`
    can only carry one rect, so it would make 2560x1440 the restored size and
    the un-maximise would do nothing visible."""
    on_screen = (0, 0, 2560, 1392)
    restored = (100, 100, 900, 700)
    layout = _layout(_record(placement=_maximised(restored), rect=on_screen))
    action = L.plan_restore(layout, {KEY_A: GEOM_A}).actions[0]
    assert action.placement.normal_rect == restored
    assert action.placement.normal_rect != on_screen


def test_a_snapped_window_is_recognised_by_its_rect_disagreeing_with_placement():
    """Windows does not record 'snapped' anywhere. A snapped window reports
    SW_SHOWNORMAL with an on-screen rect that is not its restored rect —
    that disagreement is the only signal there is."""
    snapped = _record(placement=_normal((100, 100, 900, 700)),
                      rect=(0, 0, 1280, 1392))
    assert snapped.is_snapped is True
    assert _record(placement=_normal((100, 100, 900, 700)),
                   rect=(100, 100, 900, 700)).is_snapped is False


def test_a_snapped_window_is_restored_through_placement_too():
    """Its restored rect is what has to survive; the snap itself is re-applied
    by the user, and `MoveWindow` to the snapped rect would leave a window
    that merely looks snapped and un-snaps to the wrong size."""
    layout = _layout(_record(placement=_normal((100, 100, 900, 700)),
                             rect=(0, 0, 1280, 1392)))
    action = L.plan_restore(layout, {KEY_A: GEOM_A}).actions[0]
    assert action.method == L.SET_WINDOW_PLACEMENT
    assert action.placement.normal_rect == (100, 100, 900, 700)


def test_a_minimised_window_stays_minimised():
    layout = _layout(_record(placement=_minimised()))
    action = L.plan_restore(layout, {KEY_A: GEOM_A}).actions[0]
    assert action.placement.show_cmd == L.SW_SHOWMINIMIZED


# ── the refusal ────────────────────────────────────────────────────────

def test_a_window_whose_monitor_is_gone_is_refused_by_name():
    layout = _layout(_record(1, "Notepad", KEY_A), _record(2, "Slack", KEY_B))
    plan = L.plan_restore(layout, {KEY_A: GEOM_A})
    assert [a.hwnd for a in plan.actions] == [1]
    assert [r.hwnd for r in plan.refusals] == [2]
    assert KEY_B in plan.refusals[0].reason
    assert "S2719DGF" in plan.refusals[0].reason


def test_a_window_that_was_never_attributed_to_a_monitor_is_refused():
    record = L.WindowRecord(hwnd=1, title="Notepad", process_name="notepad.exe",
                            process_id=1, placement=_normal(),
                            rect=(0, 0, 800, 600), monitor_key=None,
                            monitor_reason="MonitorFromWindow failed")
    plan = L.plan_restore(_layout(record), {KEY_A: GEOM_A})
    assert plan.refusals[0].reason


def test_a_refused_window_is_never_silently_dropped():
    layout = _layout(_record(1, key=KEY_B))
    plan = L.plan_restore(layout, {KEY_A: GEOM_A})
    assert len(plan.actions) + len(plan.refusals) == 1


# ── the topology moved under us ────────────────────────────────────────

def test_a_monitor_that_has_not_moved_needs_no_translation():
    layout = _layout(_record(key=KEY_B, placement=_normal((2600, 100, 3400, 700))))
    action = L.plan_restore(layout, {KEY_B: GEOM_B}).actions[0]
    assert action.offset == (0, 0)
    assert action.placement.normal_rect == (2600, 100, 3400, 700)


def test_a_monitor_that_moved_takes_its_windows_with_it():
    r"""The Dell sits at x=2560 in the captured layout. Unplug the Gigabyte
    and the Dell becomes the primary at x=0 — same monitor, same EDID, 2560
    pixels to the left. A restore that replayed the absolute rect would put
    the window on empty desktop."""
    moved = L.MonitorGeometry(key=KEY_B, name="S2719DGF", device=r"\\.\DISPLAY1",
                              bounds=(0, 0, 2560, 1440),
                              work_area=(0, 0, 2560, 1440), primary=True)
    layout = _layout(_record(key=KEY_B, placement=_normal((2600, 100, 3400, 700))))
    action = L.plan_restore(layout, {KEY_B: moved}).actions[0]
    assert action.offset == (-2560, 0)
    assert action.placement.normal_rect == (40, 100, 840, 700)


def test_translation_moves_the_window_without_resizing_it():
    moved = L.MonitorGeometry(key=KEY_B, name="S2719DGF", device="x",
                              bounds=(0, 300, 2560, 1740),
                              work_area=(0, 300, 2560, 1740), primary=False)
    original = (2600, 100, 3400, 700)
    layout = _layout(_record(key=KEY_B, placement=_normal(original)))
    rect = L.plan_restore(layout, {KEY_B: moved}).actions[0].placement.normal_rect
    assert rect[2] - rect[0] == original[2] - original[0]
    assert rect[3] - rect[1] == original[3] - original[1]


def test_a_maximised_windows_max_position_is_translated_too():
    moved = L.MonitorGeometry(key=KEY_B, name="S2719DGF", device="x",
                              bounds=(0, 0, 2560, 1440),
                              work_area=(0, 0, 2560, 1440), primary=True)
    placement = L.WindowPlacement(show_cmd=L.SW_SHOWMAXIMIZED, flags=0,
                                  min_position=(-1, -1), max_position=(2552, -8),
                                  normal_rect=(2600, 100, 3400, 700))
    layout = _layout(_record(key=KEY_B, placement=placement))
    action = L.plan_restore(layout, {KEY_B: moved}).actions[0]
    assert action.placement.max_position == (-8, -8)


# ── executing it ───────────────────────────────────────────────────────

def test_restore_does_not_write_unless_it_is_told_to():
    """A dry run is the default. Nothing in this test suite has permission to
    rearrange the machine it runs on."""
    probe = FakeProbe([], monitors={KEY_A: GEOM_A})
    plan = L.restore(_layout(_record()), present={KEY_A: GEOM_A}, probe=probe)
    assert probe.placements_written == []
    assert len(plan.actions) == 1
    assert plan.applied is False


def test_restore_when_applied_calls_set_window_placement_per_action():
    probe = FakeProbe([], monitors={KEY_A: GEOM_A})
    layout = _layout(_record(1, placement=_maximised()), _record(2, key=KEY_B))
    plan = L.restore(layout, present={KEY_A: GEOM_A}, probe=probe, apply=True)
    assert [hwnd for hwnd, _ in probe.placements_written] == [1]
    assert probe.placements_written[0][1].show_cmd == L.SW_SHOWMAXIMIZED
    assert plan.applied is True
    assert len(plan.refusals) == 1


# ══ against the real machine, read-only ════════════════════════════════

@pytest.fixture
def live_census():
    try:
        return C.census()
    except OSError as exc:
        pytest.skip(f"cannot enumerate windows: {exc}")


def test_live_census_finds_windows(live_census):
    assert live_census.windows, "no top-level windows on this desktop"


def test_live_census_excludes_more_than_it_keeps(live_census):
    """On a real Windows 11 desktop the majority of top-level windows are
    cloaked, invisible or tool windows. If the census ever keeps more than it
    drops, the filtering has stopped working."""
    assert len(live_census.excluded) > len(live_census.windows)


def test_live_census_actually_meets_cloaked_windows(live_census):
    cloaked = [e for e in live_census.excluded if e.reason == C.SKIP_CLOAKED]
    assert cloaked, "no cloaked windows seen — is DwmGetWindowAttribute working?"


def test_live_census_attributes_every_kept_window_to_a_monitor(live_census):
    assert sum(live_census.per_monitor().values()) == len(live_census.windows)


def test_live_capture_names_every_monitor_by_edid_or_says_why_not():
    try:
        layout = L.capture()
    except OSError as exc:
        pytest.skip(f"cannot capture layout: {exc}")
    for record in layout.windows:
        assert record.monitor_key or record.monitor_reason
    for key, geometry in layout.monitors.items():
        assert key == geometry.key
        assert "DISPLAY" not in key
