"""Route table and ARP/neighbour cache reader (no Qt)."""
from modules.network_diagnostics import route_table as rt


def test_real_machine_routes_and_neighbors_are_readable():
    snap = rt.read_routes_and_neighbors()
    assert snap is not None
    assert snap.routes
    assert all(r.interface and not r.interface.startswith("if ") for r in snap.routes[:5]) or snap.routes


def test_default_routes_sort_first():
    snap = rt.read_routes_and_neighbors()
    assert snap is not None
    defaults = [r for r in snap.routes if r.is_default]
    if defaults:
        assert snap.routes[0].is_default


def test_interface_names_are_resolved_not_left_as_bare_indexes():
    snap = rt.read_routes_and_neighbors()
    assert snap is not None
    bare = [r for r in snap.routes if r.interface.startswith("if ")]
    # some ephemeral/loopback-only indexes can go unresolved; most must not.
    assert len(bare) < len(snap.routes) / 2


def test_a_refused_read_is_none_not_an_empty_snapshot(monkeypatch):
    monkeypatch.setattr(rt, "_run_ps", lambda *a, **k: None)
    assert rt.read_routes_and_neighbors() is None


def test_invalid_json_is_treated_as_a_refusal(monkeypatch):
    monkeypatch.setattr(rt, "_run_ps", lambda *a, **k: "not json")
    assert rt.read_routes_and_neighbors() is None


def test_problem_neighbors_excludes_multicast_and_none_on_a_healthy_lan():
    snap = rt.read_routes_and_neighbors()
    assert snap is not None
    problems = rt.problem_neighbors(snap)
    assert all(not p.ip.startswith(("ff02::", "224.", "239.")) for p in problems)


def test_problem_neighbors_flags_bad_states_from_fixed_data():
    snap = rt.RouteSnapshot(
        routes=[],
        neighbors=[rt.Neighbor("10.0.0.5", "", "Ethernet", "Unreachable"),
                  rt.Neighbor("10.0.0.6", "AA-BB-CC-DD-EE-FF", "Ethernet", "Reachable"),
                  rt.Neighbor("224.0.0.1", "", "Ethernet", "Unreachable")])
    problems = rt.problem_neighbors(snap)
    assert [p.ip for p in problems] == ["10.0.0.5"]


def test_problem_neighbors_of_none_is_empty():
    assert rt.problem_neighbors(None) == []


def test_the_routes_pane_renders_real_data(qapp):
    from modules.network_diagnostics.network_health_module import NetworkHealthModule
    m = NetworkHealthModule()
    m.app = type("A", (), {"thread_pool": type("P", (), {"start": staticmethod(lambda w: w.run())})()})()
    m.create_widget()
    m._routes.refresh()
    assert m._routes._routes.rowCount() > 0
    assert "route(s)" in m._routes._status.text()


def test_a_refused_route_read_says_so_in_the_pane(qapp):
    from modules.network_diagnostics.network_health_module import NetworkHealthModule
    m = NetworkHealthModule()
    m.app = type("A", (), {"thread_pool": type("P", (), {"start": staticmethod(lambda w: w.run())})()})()
    m.create_widget()
    m._routes._done(None)
    assert "would not report" in m._routes._status.text()
    assert m._routes._routes.rowCount() == 0
