"""Windows Update service health is read-only and never guesses."""
from modules.updates import wu_health


def status(state):
    return (0x10, state, 0, 0, 0, 0, 0)


def config(start):
    return (0x10, start, 0, "", "", 0, (), "", "")


def test_running_services_and_start_types_are_named():
    row = wu_health.read_service("wuauserv", "Windows Update",
                                 lambda _n: status(4), lambda _n: config(2))
    assert row["state"] == "Running"
    assert row["start_type"] == "Automatic"
    assert row["error"] == ""


def test_a_stopped_service_is_a_problem():
    row = wu_health.read_service("bits", "BITS",
                                 lambda _n: status(1), lambda _n: config(3))
    assert row["state"] == "Stopped"
    assert wu_health.problems([row]) == [row]
    assert "BITS: Stopped" in wu_health.summary([row])


def test_an_unreadable_service_keeps_its_reason():
    def refuse(_name):
        raise OSError("Access is denied")

    row = wu_health.read_service("dosvc", "Delivery Optimization",
                                 refuse, lambda _n: config(2))
    assert row["state"] == "Unreadable"
    assert "Access is denied" in row["error"]
    assert wu_health.problems([row]) == [row]


def test_an_unreadable_start_type_does_not_hide_a_running_state():
    def refuse(_name):
        raise OSError("config refused")

    row = wu_health.read_service("cryptsvc", "Cryptographic Services",
                                 lambda _n: status(4), refuse)
    assert row["state"] == "Running"
    assert row["start_type"].startswith("Unreadable")


def test_summary_says_all_running_when_they_are():
    rows = [{"label": "A", "state": "Running", "error": ""},
            {"label": "B", "state": "Running", "error": ""}]
    assert wu_health.summary(rows) == "All Windows Update services are running."
    assert wu_health.problems(rows) == []


def test_default_config_query_uses_service_handles_and_closes_them(monkeypatch):
    import sys
    from types import SimpleNamespace
    closed = []
    api = SimpleNamespace(SC_MANAGER_CONNECT=1, SERVICE_QUERY_CONFIG=1,
        OpenSCManager=lambda *args: "scm", OpenService=lambda *args: "service",
        QueryServiceConfig=lambda handle: config(2), CloseServiceHandle=closed.append)
    monkeypatch.setitem(sys.modules, "win32service", api)
    row = wu_health.read_service("bits", "BITS", query_status=lambda name: status(4))
    assert row["start_type"] == "Automatic"
    assert closed == ["service", "scm"]
