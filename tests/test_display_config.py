r"""Reading the display topology, against this machine's real one.

The fixture is a real `QueryDisplayConfig(QDC_ALL_PATHS)` capture: 125 paths,
4 modes, three monitors of which two are on the desktop. Two things in it
would break a parser written from the documentation alone, and both are
pinned here:

* **A path's `modeInfoIdx` is only meaningful when the path is ACTIVE.**
  103 of the inactive paths carry the `0xFFFFFFFF` invalid marker, but 20 of
  them carry a perfectly valid-looking index (1 or 3) that points at a mode
  belonging to a *different* display. Resolving it without checking `active`
  attributes the Dell's 2560x1440 to a monitor that is switched off.

* **Active/inactive is a question about a TARGET, not about a path.** Every
  target appears on up to five source paths. Targets 256 and 264 each have
  both active and inactive paths; asking a single path tells you nothing.
  Aggregated per target, available {256, 264, 265} minus active {256, 264}
  leaves {265} — the LG ULTRAWIDE, connected and not on the desktop, which is
  exactly what the machine shows.

Neither shows up if you test against structures you invented, which is why
the fixture is a capture.
"""
import json
import pathlib

import pytest

from modules.monitor_control import display_config as dc

FIXTURE = (pathlib.Path(__file__).resolve().parent / "data"
           / "display_topology_all_paths.json")


@pytest.fixture
def topology():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return dc.parse_topology(raw["paths"], raw["modes"])


# ── parsing ────────────────────────────────────────────────────────────

def test_every_path_is_read(topology):
    assert len(topology.paths) == 125


def test_exactly_two_paths_are_active(topology):
    assert len(topology.active_paths()) == 2


def test_the_active_targets_are_the_two_on_the_desktop(topology):
    assert {p.target_id for p in topology.active_paths()} == {256, 264}


def test_refresh_comes_from_the_rational_not_a_rounded_int(topology):
    for path in topology.active_paths():
        assert path.refresh_hz == pytest.approx(60.0)


def test_a_zero_denominator_is_not_a_division_error():
    assert dc.refresh_hz(60, 0) == 0.0


def test_an_inactive_path_reports_no_refresh(topology):
    inactive = [p for p in topology.paths if not p.active]
    assert inactive and all(p.refresh_hz == 0.0 for p in inactive)


@pytest.mark.parametrize("target_id,expected", [
    (256, "DisplayPort"),
    (264, "HDMI"),
])
def test_the_connector_is_named(topology, target_id, expected):
    path = next(p for p in topology.active_paths() if p.target_id == target_id)
    assert expected in dc.output_technology_name(path.output_technology)


# ── the target-level state question ────────────────────────────────────

def test_a_target_is_active_if_any_of_its_paths_is(topology):
    """256 and 264 each have inactive paths too. Asking one path lies."""
    assert topology.active_target_ids() == {256, 264}


def test_available_targets_include_the_one_that_is_switched_off(topology):
    assert topology.available_target_ids() == {256, 264, 265}


def test_the_disconnected_monitor_is_identified(topology):
    """The LG ULTRAWIDE: connected, available, not on the desktop."""
    assert topology.inactive_available_target_ids() == {265}


# ── the mode-index trap ────────────────────────────────────────────────

def test_the_active_paths_resolve_to_the_real_desktop_layout(topology):
    positions = sorted(
        (topology.source_mode_for(p).position for p in topology.active_paths()))
    assert positions == [(0, 0), (2560, 0)]


def test_both_active_displays_are_2560x1440(topology):
    for path in topology.active_paths():
        mode = topology.source_mode_for(path)
        assert (mode.width, mode.height) == (2560, 1440)


def test_an_inactive_path_never_resolves_to_a_mode(topology):
    """20 of them carry a valid-looking index belonging to another display."""
    borrowed = [p for p in topology.paths
                if not p.active and p.source_mode_idx != dc.MODE_IDX_INVALID]
    assert borrowed, "fixture no longer exercises the borrowed-index case"
    assert all(topology.source_mode_for(p) is None for p in borrowed)


def test_an_invalid_mode_index_resolves_to_nothing(topology):
    invalid = [p for p in topology.paths
               if p.source_mode_idx == dc.MODE_IDX_INVALID]
    assert invalid
    assert all(topology.source_mode_for(p) is None for p in invalid)


# ── the shape the UI consumes ──────────────────────────────────────────

def test_monitors_are_reported_once_each_not_once_per_path(topology):
    """125 paths describe 3 monitors. The UI wants 3 rows."""
    monitors = topology.monitors()
    assert len(monitors) == 3
    assert {m.target_id for m in monitors} == {256, 264, 265}


def test_each_monitor_carries_its_own_state_and_mode(topology):
    by_target = {m.target_id: m for m in topology.monitors()}
    assert by_target[256].active is True
    assert by_target[264].active is True
    assert by_target[265].active is False

    assert by_target[256].refresh_hz == pytest.approx(60.0)
    assert by_target[265].refresh_hz == 0.0
    assert by_target[265].resolution is None
    assert by_target[256].resolution == (2560, 1440)


def test_monitors_are_ordered_left_to_right_with_inactive_last(topology):
    """The canvas draws them in this order, so it is the engine's job."""
    order = [m.target_id for m in topology.monitors()]
    assert order[:2] == [264, 256], "active monitors not ordered by x position"
    assert order[-1] == 265, "the inactive monitor is not last"
