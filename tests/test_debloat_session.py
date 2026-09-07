from modules.debloat import debloat_session as ds


def test_first_call_creates_a_restore_point():
    created = []
    session = ds.DebloatSession(
        create_rp=lambda label: created.append(label) or "rp-1")
    rp_id = session.restore_point_id("Apps")
    assert rp_id == "rp-1" and created == ["Apps"]


def test_a_second_call_within_the_window_reuses_the_same_id():
    session = ds.DebloatSession(create_rp=lambda label: "rp-1")
    first = session.restore_point_id("Apps")
    second = session.restore_point_id("Privacy tweaks")
    assert first == second == "rp-1"


def test_a_call_after_the_window_creates_a_new_one(monkeypatch):
    times = iter([1000.0, 1000.0 + ds.SESSION_WINDOW_MINUTES * 60 + 1])
    session = ds.DebloatSession(
        create_rp=lambda label: f"rp-{label}",
        now=lambda: next(times))
    first = session.restore_point_id("Apps")
    second = session.restore_point_id("Privacy")
    assert first != second
