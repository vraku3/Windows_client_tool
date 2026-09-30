"""The shared AppX enumeration service."""

import subprocess

from core import appx_service


def _pkg(name, version="1.0.0", framework=False):
    return {
        "Name": name, "Version": version, "IsFramework": framework,
        "IsResourcePackage": False, "IsPartiallyStaged": False,
        "InstallLocation": "", "PackageFamilyName": "", "Architecture": "",
    }


def test_dedupe_by_name_keeps_newest_version():
    packages = [
        _pkg("Microsoft.WindowsCalculator", "11.2400.0.0"),
        _pkg("Microsoft.WindowsCalculator", "11.2607.0.0"),
        _pkg("SpotifyAB.SpotifyMusic", "1.0.0"),
    ]
    deduped = appx_service.dedupe_by_name(packages)
    assert len(deduped) == 2
    calc = next(p for p in deduped if p["Name"] == "Microsoft.WindowsCalculator")
    assert calc["Version"] == "11.2607.0.0"


def test_dedupe_by_name_drops_frameworks_is_not_its_job():
    """Frameworks are filtered during the fetch, not by dedupe."""
    packages = [_pkg("Microsoft.WindowsCalculator", "1.0"),
                _pkg("Microsoft.VCLibs", "1.0", framework=True)]
    deduped = appx_service.dedupe_by_name(packages)
    assert len(deduped) == 2


def test_invalidate_cache_resets(monkeypatch):
    monkeypatch.setattr(appx_service, "_enumerate", lambda: [_pkg("A")])
    assert [p["Name"] for p in appx_service.fetch_packages(use_cache=False)] == ["A"]
    appx_service.fetch_packages()  # populates the cache
    monkeypatch.setattr(appx_service, "_enumerate", lambda: [_pkg("B")])
    # Cache still serves the old list...
    assert [p["Name"] for p in appx_service.fetch_packages()] == ["A"]
    # ...until invalidated.
    appx_service.invalidate_cache()
    assert [p["Name"] for p in appx_service.fetch_packages()] == ["B"]
    appx_service.invalidate_cache()


def test_fetch_packages_or_none_preserves_a_failed_enumeration(monkeypatch):
    """`_enumerate()` returning `None` (every attempt refused/errored) must
    not be silently turned into "no packages installed" -- a caller reading
    the list as evidence (verify_uninstalled) needs to be able to tell the
    two apart."""
    monkeypatch.setattr(appx_service, "_enumerate", lambda: None)
    appx_service.invalidate_cache()
    assert appx_service.fetch_packages_or_none(use_cache=False) is None
    appx_service.invalidate_cache()


def test_fetch_packages_collapses_a_failed_enumeration_to_empty_list(
        monkeypatch):
    """Most callers only display or scan this list, so `fetch_packages()`
    keeps its old, simpler `[]`-on-failure contract; only
    `fetch_packages_or_none` exposes the distinction."""
    monkeypatch.setattr(appx_service, "_enumerate", lambda: None)
    appx_service.invalidate_cache()
    assert appx_service.fetch_packages(use_cache=False) == []
    appx_service.invalidate_cache()


class _WedgedProc:
    """Simulates a `Get-AppxPackage` process whose descendant keeps the
    stdout/stderr pipes open past the caller's timeout AND past the
    post-kill drain -- the exact condition that made the real
    `subprocess.run(..., timeout=...)` hang forever on Windows (its own
    internal post-kill `communicate()` has no timeout at all)."""

    pid = 4242

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=timeout)


def test_run_ps_bounded_returns_none_instead_of_hanging_on_a_wedged_child(
        monkeypatch):
    """Regression test for the Debloat Apps tab hanging on "Scanning..."
    for 20+ minutes: a child process that never releases its pipes must
    make `_run_ps_bounded` give up within its own bounded drain, not block
    forever, and must still attempt to kill the whole process tree (not
    just the immediate PID) via `taskkill /T`."""
    taskkill_calls = []

    def fake_popen(*args, **kwargs):
        return _WedgedProc()

    def fake_run(cmd, **kwargs):
        taskkill_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(appx_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(appx_service.subprocess, "run", fake_run)

    result = appx_service._run_ps_bounded("Get-AppxPackage", timeout=0.01)

    assert result is None
    assert len(taskkill_calls) == 1
    cmd = taskkill_calls[0]
    assert cmd[0] == "taskkill"
    assert "/T" in cmd and "/F" in cmd
    assert str(_WedgedProc.pid) in cmd


def test_enumerate_treats_a_wedged_child_as_a_failed_attempt(monkeypatch):
    """`_enumerate()` must not propagate a hang from `_run_ps_bounded` --
    a wedged first attempt (elevated -AllUsers) still lets an unelevated
    caller fall through to the per-user fallback attempt cleanly."""
    monkeypatch.setattr(appx_service, "is_admin", lambda: False, raising=False)
    monkeypatch.setattr(
        "core.admin_utils.is_admin", lambda: False)
    monkeypatch.setattr(
        appx_service, "_run_ps_bounded", lambda cmd, timeout=60: None)

    assert appx_service._enumerate() is None


def test_clean_drops_frameworks_and_resources():
    framework = _pkg("Microsoft.VCLibs", "1.0", framework=True)
    resource = _pkg("Microsoft.X", "1.0")
    resource["IsResourcePackage"] = True
    partial = _pkg("Microsoft.Y", "1.0")
    partial["IsPartiallyStaged"] = True
    normal = _pkg("Microsoft.WindowsCalculator", "1.0")
    names = [p["Name"] for p in appx_service._clean(
        [framework, resource, partial, normal])]
    assert names == ["Microsoft.WindowsCalculator"]


def test_architecture_is_requested_as_a_name_not_an_enum_number():
    """ConvertTo-Json writes the ProcessorArchitecture enum as a number, and
    QTableWidgetItem(9) is the item-TYPE overload, so the Store Apps
    Architecture column was blank on every row."""
    from core import appx_service
    assert "[string]$_.Architecture" in appx_service._SELECT


import pytest


@pytest.mark.real_machine
def test_real_machine_architectures_are_names():
    from core import appx_service
    packages = appx_service.fetch_packages(use_cache=False)
    assert packages
    assert all(isinstance(p["Architecture"], str) and not p["Architecture"].isdigit()
               for p in packages), {p["Architecture"] for p in packages}


# ----------------------------------------------------------------------
# fetch_provisioned_packages_or_none / provisioned_package_name_map_or_none
# / remove_provisioned_package -- Store Apps sysadmin pass, 2026-09-30.
#
# Confirmed live, unelevated, on this real machine:
#   Get-AppxProvisionedPackage -Online -> COMException "The requested
#   operation requires elevation", exit code 1, empty stdout. So the read
#   side must short-circuit to None unelevated rather than attempt (and
#   parse) a query guaranteed to be refused.
# ----------------------------------------------------------------------


def test_fetch_provisioned_packages_short_circuits_when_unelevated(monkeypatch):
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: False)
    calls = []
    monkeypatch.setattr(appx_service, "_run_ps_bounded",
                        lambda cmd, timeout=60: calls.append(cmd))
    assert appx_service.fetch_provisioned_packages_or_none(use_cache=False) is None
    assert calls == []  # never even spawned the guaranteed-refused query


def test_fetch_provisioned_packages_parses_real_shaped_output(monkeypatch):
    """Shape matches the real DisplayName/PackageName pairs captured live
    2026-09-30 (Clipchamp.Clipchamp among the provisioned-but-not-installed
    set on this machine)."""
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    monkeypatch.setattr(
        appx_service, "_run_ps_bounded",
        lambda cmd, timeout=60: (0,
            '[{"DisplayName":"Clipchamp.Clipchamp",'
            '"PackageName":"Clipchamp.Clipchamp_4.6.10320.0_neutral_~_yxz26nhyzhsrt"},'
            '{"DisplayName":"Microsoft.WindowsCalculator",'
            '"PackageName":"Microsoft.WindowsCalculator_2021.2607.0.0_neutral_~_8wekyb3d8bbwe"}]',
            ""))
    result = appx_service.fetch_provisioned_packages_or_none(use_cache=False)
    assert result is not None
    assert {p["DisplayName"] for p in result} == {
        "Clipchamp.Clipchamp", "Microsoft.WindowsCalculator"}


def test_fetch_provisioned_packages_handles_a_single_object_not_a_list(monkeypatch):
    """`ConvertTo-Json` emits a bare object, not a one-element array, when
    only one package is provisioned -- the same normalization `_clean()`
    already needs for `Get-AppxPackage`."""
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    monkeypatch.setattr(
        appx_service, "_run_ps_bounded",
        lambda cmd, timeout=60: (0, '{"DisplayName":"Solo.App","PackageName":"Solo.App_1.0_x64__abc"}', ""))
    result = appx_service.fetch_provisioned_packages_or_none(use_cache=False)
    assert result == [{"DisplayName": "Solo.App", "PackageName": "Solo.App_1.0_x64__abc"}]


def test_fetch_provisioned_packages_returns_none_on_a_refused_query(monkeypatch):
    """Elevated but still refused for some other reason (e.g. a corporate
    policy) -- must not be read as "nothing is provisioned"."""
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    monkeypatch.setattr(
        appx_service, "_run_ps_bounded",
        lambda cmd, timeout=60: (1, "", "Some DISM failure"))
    assert appx_service.fetch_provisioned_packages_or_none(use_cache=False) is None


def test_fetch_provisioned_packages_returns_none_on_unparseable_output(monkeypatch):
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    monkeypatch.setattr(
        appx_service, "_run_ps_bounded",
        lambda cmd, timeout=60: (0, "not json at all", ""))
    assert appx_service.fetch_provisioned_packages_or_none(use_cache=False) is None


def test_provisioned_package_name_map_or_none_builds_displayname_to_packagename(
        monkeypatch):
    monkeypatch.setattr(
        appx_service, "fetch_provisioned_packages_or_none",
        lambda use_cache=True: [
            {"DisplayName": "Microsoft.BingWeather",
             "PackageName": "Microsoft.BingWeather_4.54.63045.0_neutral_~_8wekyb3d8bbwe"}])
    result = appx_service.provisioned_package_name_map_or_none(use_cache=False)
    assert result == {
        "Microsoft.BingWeather": "Microsoft.BingWeather_4.54.63045.0_neutral_~_8wekyb3d8bbwe"}


def test_provisioned_package_name_map_or_none_preserves_none(monkeypatch):
    """Never collapsed to `{}` -- that would read as "nothing provisioned"
    rather than "we could not check"."""
    monkeypatch.setattr(
        appx_service, "fetch_provisioned_packages_or_none",
        lambda use_cache=True: None)
    assert appx_service.provisioned_package_name_map_or_none(use_cache=False) is None


def test_remove_provisioned_package_refuses_unelevated_without_running_anything(
        monkeypatch):
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: False)
    calls = []
    monkeypatch.setattr(appx_service, "_run_ps_bounded",
                        lambda cmd, timeout=60: calls.append(cmd))
    ok, reason = appx_service.remove_provisioned_package("Some.Package_1.0_x64__abc")
    assert ok is False
    assert "administrator" in reason.lower()
    assert calls == []


def test_remove_provisioned_package_reads_success_from_stdout_not_returncode_alone(
        monkeypatch):
    """DISM-backed cmdlets in this app refuse while exiting 0 elsewhere
    (CLAUDE.md's netsh/dism note); this call must key success off the
    explicit marker text, not the return code by itself."""
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    monkeypatch.setattr(
        appx_service, "_run_ps_bounded",
        lambda cmd, timeout=60: (0, "DEPROVISIONED\r\n", ""))
    ok, reason = appx_service.remove_provisioned_package("Some.Package_1.0_x64__abc")
    assert ok is True
    assert reason == ""


def test_remove_provisioned_package_reports_a_distinguishable_failure(monkeypatch):
    """Mirrors the real failure text captured live against a nonexistent
    package name: exit 1, no success marker, a real error message."""
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    monkeypatch.setattr(
        appx_service, "_run_ps_bounded",
        lambda cmd, timeout=60: (1, "", "The parameter is incorrect."))
    ok, reason = appx_service.remove_provisioned_package("Definitely.Not.Real_1.0_x64__abc")
    assert ok is False
    assert "parameter is incorrect" in reason.lower()


def test_remove_provisioned_package_quotes_the_package_name(monkeypatch):
    """A package name containing a single quote must not break out of the
    PowerShell string literal -- same discipline as every other ps_quote
    call site in this app."""
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    seen = {}
    def fake(cmd, timeout=60):
        seen["cmd"] = cmd
        return 0, "DEPROVISIONED", ""
    monkeypatch.setattr(appx_service, "_run_ps_bounded", fake)
    appx_service.remove_provisioned_package("Weird'Name_1.0_x64__abc")
    assert "Weird''Name" in seen["cmd"]


@pytest.mark.real_machine
def test_real_machine_provisioned_query_is_none_unelevated_or_a_real_list():
    """Confirms the live shape this whole feature is built against: either
    the query is refused outright (this session is unelevated) or it comes
    back as a real list of DisplayName/PackageName dicts -- never `[]` (an
    elevated DISM query with zero provisioned packages has not been
    observed on any real Windows install)."""
    from core.admin_utils import is_admin
    result = appx_service.fetch_provisioned_packages_or_none(use_cache=False)
    if not is_admin():
        assert result is None
    else:
        assert result is not None
        assert len(result) > 0
        assert all("DisplayName" in p and "PackageName" in p for p in result)
