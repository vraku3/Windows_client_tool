import os

import pytest

from core.procengine.snapshot import SnapshotSource
from modules.dashboard import process_view as pv


@pytest.fixture(scope="module")
def snapshot():
    src = SnapshotSource()
    src.read()
    return src.read()


def test_every_column_renders_every_real_process(snapshot):
    for info in snapshot.by_pid.values():
        for col in pv.COLUMNS:
            assert isinstance(col.text(info), str)
            col.value(info)          # must be sortable, never raise


def test_column_keys_unique_and_defaults_exist():
    keys = [c.key for c in pv.COLUMNS]
    assert len(keys) == len(set(keys))
    assert set(pv.DEFAULT_VISIBLE) <= set(keys)


def test_started_time_is_sane_for_a_real_process(snapshot):
    own = [i for i in snapshot.by_pid.values() if i.name.lower().startswith("python")]
    assert own and pv.started_at(own[0]).year >= 2024


def test_filters_cover_all_and_counts_match(snapshot):
    infos = list(snapshot.by_pid.values())
    counts = pv.filter_counts(infos)
    assert counts["all"] == len(infos)
    assert all(0 <= n <= len(infos) for n in counts.values())
    assert all(pv.passes("all", i) for i in infos)
    assert pv.passes("nonsense", infos[0])     # unknown chip falls back to All


def test_detail_text_names_the_process_and_parent(snapshot):
    info = next(i for i in snapshot.by_pid.values() if i.pid > 4 and i.raw.ppid in snapshot.by_pid)
    text = pv.detail_text(info, snapshot, services=["Foo", "Bar"])
    assert f"PID {info.pid}" in text and "Parent:" in text
    assert "Services:   Foo, Bar" in text


def test_a_refused_field_is_explained_not_blank(snapshot):
    refused = next((i for i in snapshot.by_pid.values() if i.details.path is None), None)
    if refused is None:
        pytest.skip("nothing refused on this run (elevated)")
    assert "unavailable" in pv.detail_text(refused, snapshot)


def test_aggregate_for_an_app_row(snapshot):
    infos = list(snapshot.by_pid.values())[:5]
    threads = pv.aggregate_text(pv.BY_KEY["threads"], infos)
    assert threads == f"{sum(i.raw.threads for i in infos):,}"
    assert pv.aggregate_text(pv.BY_KEY["user"], []) == ""


def test_duration_formatting():
    assert pv.format_duration(5) == "5s"
    assert pv.format_duration(125) == "2m 05s"
    assert pv.format_duration(3 * 3600 + 120) == "3h 02m"


def test_service_map_reads_svchost_hosts():
    m = pv.services_by_pid()
    assert m is None or all(isinstance(v, list) for v in m.values())


def test_is_self_matches_exactly_this_process(snapshot):
    """tmog.org's "self-monitoring capability for TMOG itself" -- this test
    IS that process (pytest's own interpreter), so a real snapshot taken
    in-process must contain exactly one row `is_self` agrees with, and it
    must be the running interpreter's own PID."""
    own_pid = os.getpid()
    matches = [i for i in snapshot.by_pid.values() if pv.is_self(i)]
    assert len(matches) == 1
    assert matches[0].pid == own_pid
    other = next(i for i in snapshot.by_pid.values() if i.pid != own_pid)
    assert not pv.is_self(other)


def test_is_self_is_flagged_as_a_badge(snapshot):
    own = next(i for i in snapshot.by_pid.values() if pv.is_self(i))
    assert "this app" in pv.badges(own)
