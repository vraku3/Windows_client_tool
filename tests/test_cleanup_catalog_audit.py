"""The Cleanup catalog must not sign anyone out or delete their data.

Audited 2026-10-10: 289 entries marked "safe" pointed at an app's whole
data folder, a sign-in store or user data -- the hosts file, Telegram's
tdata, Signal's database, Zoom recordings, ShareX screenshots, Notepad++
unsaved tabs, saved Wi-Fi, Outlook's .ost/.pst, VPN keys, installed games --
and "caution"/"danger" entries (which the Thorough and Aggressive presets
select) held VMs, databases, WSL distros and WinSxS\\Manifests. Every such
path was removed or its entry disabled with the reason; a new path must be a
recognised cache/temp/log folder or be reviewed into the allowlist.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


def test_no_enabled_entry_points_at_anything_but_reviewed_junk():
    from cleanup_catalog_audit import unreviewed
    found = unreviewed()
    assert found == [], "\n".join(f"{stem}/{spec['id']} [{spec['safety']}]: {[p for p, _r in hits]}"
                                  for stem, spec, hits in found)


def test_sign_in_stores_are_never_targets():
    from modules.cleanup.cleanup_scanner import catalog
    banned = ("cookies", "local storage", "indexeddb", "session storage", "login data",
              "\\network", "tdata", "\\config", "steamapps", "\\outlook")
    for spec in catalog.load_catalog(force=True).values():
        if spec.disabled_reason:
            continue
        for path in spec.paths:
            low = path.lower()
            assert not any(b in low + "\\" for b in banned if not b.startswith("\\")) and \
                not any(low.endswith(b) or (b + "\\") in low for b in banned if b.startswith("\\")), \
                f"{spec.id}: {path}"


def test_outlook_data_is_refused_by_the_deleter(tmp_path, monkeypatch):
    from modules.cleanup import cleanup_history
    from modules.cleanup.cleanup_scanner import ScanItem
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    monkeypatch.setattr(cleanup_history, "record_deleted", lambda *a, **k: None)
    folder = tmp_path / "SomeCache"
    folder.mkdir()
    (folder / "archive.pst").write_bytes(b"x")
    ost = tmp_path / "mailbox.ost"
    ost.write_bytes(b"x")
    items = [ScanItem(path=str(folder), size=1, is_dir=True, selected=True, safety="safe"),
             ScanItem(path=str(ost), size=1, is_dir=False, selected=True, safety="safe")]
    deleted, errors = ss.delete_items(items)
    assert (deleted, errors) == (0, 2)
    assert (folder / "archive.pst").exists() and ost.exists()


def test_the_outlook_folders_and_their_parents_are_refused(monkeypatch):
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    guarded = ss.protected_user_data_dirs()
    assert any(p.lower().endswith(r"microsoft\outlook") for p in guarded)
