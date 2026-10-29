"""Per-app Store updates through winget, plus the Store tab selection gate."""
from types import SimpleNamespace

from modules.updates.store_update import update_apps
from modules.updates.winget_updater import AppUpdate, UpdateOutcome


def _update(package_id="Acme.Store.App", name="Store App"):
    return AppUpdate(name, package_id, "1.0", "2.0", "msstore")


def test_update_apps_installs_each_offer_and_records_it(monkeypatch):
    calls = []
    recorded = []

    def install(winget_id, **kwargs):
        calls.append((winget_id, kwargs))
        return UpdateOutcome(winget_id, "UPDATED", "Now 2.0", version_after="2.0")

    monkeypatch.setattr("modules.updates.winget_updater.install_update", install)
    monkeypatch.setattr("modules.updates.activity_ledger.record_winget_activity",
                        lambda app, update, outcome, context, log: recorded.append(
                            (update.winget_id, outcome.state, context)))
    lines = []
    results = update_apps(SimpleNamespace(), [_update()], log=lines.append)
    assert calls[0][0] == "Acme.Store.App"
    assert calls[0][1]["target_version"] == "2.0"
    assert calls[0][1]["before_version"] == "1.0"
    assert recorded == [("Acme.Store.App", "UPDATED", "store")]
    assert results[0][1].state == "UPDATED"
    assert "UPDATED" in " ".join(lines)


def test_an_install_exception_is_a_failed_outcome_not_a_lost_batch(monkeypatch):
    def install(*_args, **_kwargs):
        raise RuntimeError("winget refused")

    recorded = []
    monkeypatch.setattr("modules.updates.winget_updater.install_update", install)
    monkeypatch.setattr("modules.updates.activity_ledger.record_winget_activity",
                        lambda app, update, outcome, context, log: recorded.append(outcome))

    results = update_apps(SimpleNamespace(), [_update()])
    assert len(results) == 1
    assert results[0][1].state == "FAILED"
    assert "winget refused" in results[0][1].detail
    assert recorded and recorded[0].state == "FAILED"


def test_cancellation_stops_before_the_next_package(monkeypatch):
    calls = []
    monkeypatch.setattr("modules.updates.winget_updater.install_update",
                        lambda *a, **k: calls.append(a[0]) or UpdateOutcome(a[0], "UPDATED", "ok"))
    monkeypatch.setattr("modules.updates.activity_ledger.record_winget_activity",
                        lambda *a, **k: None)
    checks = iter([False, True])
    results = update_apps(SimpleNamespace(), [_update("A"), _update("B")],
                          is_cancelled=lambda: next(checks))
    assert calls == ["A"] and len(results) == 1


def test_store_tab_only_lists_msstore_rows_and_gates_update(qapp):
    from modules.updates.store_updates_tab import _StoreUpdatesTab
    class Config:
        def get(self, key, default=None):
            return default
    tab = _StoreUpdatesTab()
    tab.set_app(SimpleNamespace(config=Config()))
    tab.set_updates([AppUpdate("Store App", "Acme.Store.App", "1", "2", "msstore"),
                     AppUpdate("Winget App", "Acme.Winget.App", "1", "2", "winget")])
    assert [u.winget_id for u in tab._updates] == ["Acme.Store.App"]
    assert not tab._update_btn.isEnabled()
    tab._table.selectRow(0)
    tab._sync_buttons()
    assert tab._update_btn.isEnabled()
    assert [u.winget_id for u in tab._selected_updates()] == ["Acme.Store.App"]
