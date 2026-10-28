"""Post-reboot reconciliation for updates that asked for a restart."""
from types import SimpleNamespace

from modules.updates import reboot_reconcile as rr


def offer(wid, installed, available, name=None):
    return SimpleNamespace(winget_id=wid, installed_version=installed,
                           available_version=available, name=name or wid)


def test_version_advanced_only_for_comparable_versions():
    assert rr.version_advanced("2.0", "1.9")
    assert rr.version_advanced("1.2.10", "1.2.9")
    assert not rr.version_advanced("1.2.9", "1.2.10")
    assert not rr.version_advanced("1.0", "1.0")
    assert not rr.version_advanced("unknown", "1.0")
    assert not rr.version_advanced("2.0", None)


def test_pending_items_merge_by_source_and_id(tmp_path):
    assert rr.record(tmp_path, [{"source": "winget", "id": "Acme.App", "name": "Acme"}]) == 1
    rr.record(tmp_path, [{"source": "winget", "id": "acme.app", "name": "Acme 2", "version_before": "1.0"}])
    rows = rr.load(tmp_path)
    assert len(rows) == 1 and rows[0]["name"] == "Acme 2" and rows[0]["version_before"] == "1.0"
    rr.record(tmp_path, [{"source": "wu", "id": "KB1", "name": "Update"}])
    assert len(rr.load(tmp_path)) == 2


def test_winget_confirmed_by_version_or_disappearance(tmp_path):
    pending = [{"source": "winget", "id": "Acme.App", "version_before": "1.0"},
               {"source": "winget", "id": "Other.App", "version_before": "3.0"}]
    results = rr.reconcile(pending, [offer("acme.app", "2.0", "3.0")], False)
    assert results[0]["status"] == rr.CONFIRMED and "2.0" in results[0]["detail"]
    assert results[1]["status"] == rr.CONFIRMED and "No longer offered" in results[1]["detail"]
    assert set(rr.resolved(results)) == {"winget:acme.app", "winget:other.app"}


def test_still_offered_at_the_same_version_stays_pending():
    pending = [{"source": "winget", "id": "Acme.App", "version_before": "1.0"}]
    results = rr.reconcile(pending, [offer("Acme.App", "1.0", "2.0")], False)
    assert results[0]["status"] == rr.STILL_PENDING
    assert rr.resolved(results) == []


def test_a_failed_scan_is_unknown_never_confirmed():
    pending = [{"source": "winget", "id": "Acme.App", "version_before": "1.0"}]
    results = rr.reconcile(pending, None, False)
    assert results[0]["status"] == rr.UNKNOWN
    assert rr.resolved(results) == []


def test_windows_update_is_never_confirmed_from_a_cleared_reboot_flag():
    pending = [{"source": "wu", "id": "KB5129195", "name": "Security Update"}]
    cleared = rr.reconcile(pending, [], False)
    assert cleared[0]["status"] == rr.UNVERIFIED
    assert rr.resolved(cleared) == ["wu:kb5129195"]
    still = rr.reconcile(pending, [], True)
    assert still[0]["status"] == rr.STILL_PENDING
    unreadable = rr.reconcile(pending, [], None)
    assert unreadable[0]["status"] == rr.UNKNOWN


def test_forget_keeps_only_unresolved_items(tmp_path):
    rr.record(tmp_path, [{"source": "winget", "id": "A", "name": "A"},
                         {"source": "winget", "id": "B", "name": "B"}])
    rr.forget(tmp_path, ["winget:a"])
    assert [row["id"] for row in rr.load(tmp_path)] == ["B"]
    rr.forget(tmp_path, ["winget:b"])
    assert rr.load(tmp_path) == []
