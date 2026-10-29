"""Store update verification: snapshot, bounded poll, honest outcome."""
from modules.updates.store_verify import (
    changed_apps, snapshot, verify_store_update,
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _pkgs(*rows):
    return [{"Name": name, "Version": version} for name, version in rows]


def test_snapshot_keeps_the_newest_version_per_name():
    rows = snapshot(_pkgs(("App", "1.0.0.0"), ("App", "2.0.0.0"), ("Other", "3.1")))
    assert rows == {"App": "2.0.0.0", "Other": "3.1"}


def test_snapshot_skips_rows_without_a_name():
    assert snapshot([{"Name": "", "Version": "1"}]) == {}


def test_changed_apps_reports_old_to_new_only_for_moves():
    rows = changed_apps({"A": "1.0", "B": "2.0"}, {"A": "1.1", "B": "2.0"})
    assert rows == ["A: 1.0 -> 1.1"]


def test_a_moved_version_is_reported_and_polling_stops_early():
    clock = Clock()
    calls = []
    responses = [ _pkgs(("App", "1.0")), _pkgs(("App", "1.1")) ]

    def fetch(*, use_cache=True):
        calls.append(use_cache)
        return responses.pop(0)

    result = verify_store_update(
        fetch=fetch, trigger=lambda log: 2, sleep=clock.sleep, clock=clock,
        budget_s=60, interval_s=10)
    assert result.changed == ["App: 1.0 -> 1.1"]
    assert result.triggered == 2
    assert "may still be updating" in result.message
    assert calls == [False, False]


def test_a_quiet_window_is_not_reported_as_up_to_date():
    clock = Clock()
    packages = _pkgs(("App", "1.0"))

    def fetch(*, use_cache=True):
        return packages

    result = verify_store_update(
        fetch=fetch, trigger=lambda log: 1, sleep=clock.sleep, clock=clock,
        budget_s=30, interval_s=10)
    assert result.changed == []
    assert "No installed version changed" in result.message
    assert "may still be updating" in result.message


def test_a_refused_snapshot_is_nothing_verified():
    result = verify_store_update(
        fetch=lambda *, use_cache=True: None, trigger=lambda log: 1,
        sleep=lambda _s: None, clock=lambda: 0.0)
    assert result.read_refused
    assert "nothing was verified" in result.message


def test_versions_that_can_never_be_reread_are_not_called_unchanged():
    clock = Clock()
    responses = [_pkgs(("App", "1.0")), None, None, None]

    def fetch(*, use_cache=True):
        return responses.pop(0) if responses else None

    result = verify_store_update(
        fetch=fetch, trigger=lambda log: 1, sleep=clock.sleep, clock=clock,
        budget_s=30, interval_s=10)
    assert result.read_refused
    assert "nothing is verified" in result.message
