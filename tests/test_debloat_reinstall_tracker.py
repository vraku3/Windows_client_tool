from modules.debloat import debloat_reinstall_tracker as rt
from modules.debloat import debloat_scanner as ds


def _patched(tmp_path, monkeypatch):
    path = tmp_path / "debloat_removed.json"
    monkeypatch.setattr(rt, "_store_path", lambda: str(path))
    return path


def test_never_removed_package_is_not_reported(tmp_path, monkeypatch):
    _patched(tmp_path, monkeypatch)
    assert rt.check_reinstalled(["Microsoft.WindowsCalculator"]) == []


def test_removed_then_still_absent_is_not_reported(tmp_path, monkeypatch):
    _patched(tmp_path, monkeypatch)
    rt.record_removed(["Microsoft.GamingApp"])
    assert rt.check_reinstalled(["Microsoft.WindowsCalculator"]) == []


def test_removed_then_seen_again_is_flagged(tmp_path, monkeypatch):
    _patched(tmp_path, monkeypatch)
    rt.record_removed(["Microsoft.GamingApp"])
    result = rt.check_reinstalled(["Microsoft.GamingApp", "Microsoft.Paint"])
    assert len(result) == 1
    assert result[0]["package"] == "Microsoft.GamingApp"
    assert result[0]["times_reinstalled"] == 1
    assert result[0]["last_removed_at"]


def test_repeated_scans_without_a_new_removal_do_not_double_count(
        tmp_path, monkeypatch):
    _patched(tmp_path, monkeypatch)
    rt.record_removed(["Microsoft.GamingApp"])
    rt.check_reinstalled(["Microsoft.GamingApp"])
    second = rt.check_reinstalled(["Microsoft.GamingApp"])
    assert second[0]["times_reinstalled"] == 1


def test_removed_again_after_a_reinstall_bumps_the_count_on_next_return(
        tmp_path, monkeypatch):
    _patched(tmp_path, monkeypatch)
    # Two distinct timestamps a real second apart can't be guaranteed inside
    # one fast test, so the clock seam is driven explicitly here.
    ticks = iter(["2026-01-01T00:00:00", "2026-01-02T00:00:00"])
    monkeypatch.setattr(rt, "_now_iso", lambda: next(ticks))
    rt.record_removed(["Microsoft.GamingApp"])
    rt.check_reinstalled(["Microsoft.GamingApp"])   # flags reinstall #1
    rt.record_removed(["Microsoft.GamingApp"])       # removed again
    result = rt.check_reinstalled(["Microsoft.GamingApp"])  # back again
    assert result[0]["times_reinstalled"] == 2


def test_record_removed_ignores_empty_input(tmp_path, monkeypatch):
    path = _patched(tmp_path, monkeypatch)
    rt.record_removed([])
    assert not path.exists()


def test_corrupted_store_is_treated_as_empty_not_raised(tmp_path, monkeypatch):
    path = _patched(tmp_path, monkeypatch)
    path.write_text("{not valid json", encoding="utf-8")
    assert rt.check_reinstalled(["Microsoft.GamingApp"]) == []


def test_real_machine_installed_packages_feed_the_comparison(
        tmp_path, monkeypatch):
    """Real-machine check: whatever `debloat_scanner` genuinely reports as
    installed right now, recording one of those real package ids as
    "removed" and then re-scanning must flag it -- proving the comparison
    works against this app's own real installed-package data, not just
    hand-picked strings."""
    _patched(tmp_path, monkeypatch)
    installed = ds.get_installed_packages()
    if not installed:
        return  # nothing installed from the known-bloatware set right now
    real_pkg = next(iter(installed))
    rt.record_removed([real_pkg])
    result = rt.check_reinstalled(list(installed.keys()))
    assert any(r["package"] == real_pkg for r in result)
