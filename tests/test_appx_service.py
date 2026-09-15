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
