"""A partly unreadable Installer\\UserData must never produce "orphaned"
registrations: the cached installer that looks missing may simply be one we
were refused, and an orphan row offers to delete the product's Installer keys."""
from modules.app_buster.engine import desktop_apps


def test_no_ghost_registrations_when_userdata_was_not_fully_read(monkeypatch):
    packed = "00002109A10000000000000000F01FEC"
    monkeypatch.setattr(desktop_apps, "_subkeys", lambda hive, path: [packed])
    monkeypatch.setattr(desktop_apps, "_value", lambda *a, **k: "Some Suite Component")
    monkeypatch.setattr(desktop_apps, "_exists", lambda p: False)
    assert desktop_apps.ghost_registrations(set(), {}, msi_complete=False) == []
    judged = desktop_apps.ghost_registrations(set(), {}, msi_complete=True)
    assert len(judged) == 1          # the same data, fully read, is judged


def test_a_refused_products_key_makes_the_read_incomplete(monkeypatch):
    def checked(hive, path):
        if path.endswith("\\Products"):
            return [], False          # refused
        return ["S-1-5-18"], True
    monkeypatch.setattr(desktop_apps, "_subkeys_checked", checked)
    _out, complete = desktop_apps.msi_local_packages_read()
    assert complete is False
