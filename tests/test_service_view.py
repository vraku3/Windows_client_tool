import subprocess

from modules.dashboard import service_view as sv


def svc(**kw):
    base = {"Name": "Foo", "Display Name": "Foo Service", "Status": "Running",
            "Start Type": "Auto", "PID": "12", "Description": "Does foo things",
            "Impact": "Low", "Path": r"C:\Program Files\Foo\foo.exe",
            "StartName": "LocalSystem"}
    base.update(kw)
    return base


def test_auto_but_stopped_needs_both_conditions():
    assert sv.auto_but_stopped(svc(Status="Stopped"))
    assert not sv.auto_but_stopped(svc(Status="Running"))
    assert not sv.auto_but_stopped(svc(Status="Stopped", **{"Start Type": "Manual"}))


def test_custom_account_ignores_the_builtin_ones():
    assert not sv.custom_account(svc(StartName="LocalSystem"))
    assert not sv.custom_account(svc(StartName="NT AUTHORITY\\LocalService"))
    assert not sv.custom_account(svc(StartName=""))          # unknown is not custom
    assert sv.custom_account(svc(StartName="ACME\\backup_svc"))


def test_third_party_is_by_binary_location():
    assert sv.is_third_party(svc())
    assert not sv.is_third_party(svc(Path=r"C:\WINDOWS\system32\svchost.exe -k netsvcs"))
    assert not sv.is_third_party(svc(Path=""))


def test_search_covers_description_and_account():
    assert sv.matches(svc(), "foo things")
    assert sv.matches(svc(StartName="ACME\\alice"), "alice")
    assert not sv.matches(svc(), "zzz")


def test_counts_and_visible_agree():
    rows = [svc(), svc(Name="B", Status="Stopped"), svc(Name="C", **{"Start Type": "Disabled"})]
    counts = sv.filter_counts(rows)
    assert counts["all"] == 3 and counts["stopped"] == 1 and counts["disabled"] == 1
    assert len(sv.visible(rows, "stopped", "")) == counts["stopped"]
    assert len(sv.visible(rows, "nonsense", "")) == 3


def test_detail_text_flags_and_dependents():
    text = sv.detail_text(svc(Status="Stopped", StartName="ACME\\alice"), ["A", "B"])
    assert "Automatic but not running" in text and "a real account" in text
    assert "Needed by:  A, B" in text
    assert "nothing else" in sv.detail_text(svc(), [])
    assert "Needed by" not in sv.detail_text(svc(), None)


def test_set_start_type_trusts_success_line_not_exit_code(monkeypatch):
    class R:
        def __init__(self, rc, out): self.returncode, self.stdout, self.stderr = rc, out, ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R(0, "[SC] ChangeServiceConfig SUCCESS"))
    assert sv.set_start_type("Foo", "Manual")[0] is True
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R(0, "[SC] OpenService FAILED 5:\nAccess is denied."))
    ok, msg = sv.set_start_type("Foo", "Manual")
    assert not ok and "denied" in msg


def test_set_start_type_refuses_bad_input():
    assert not sv.set_start_type("Foo", "Sideways")[0]
    assert not sv.set_start_type("Foo & calc", "Manual")[0]
