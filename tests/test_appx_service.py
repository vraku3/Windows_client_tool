"""The shared AppX enumeration service."""

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
