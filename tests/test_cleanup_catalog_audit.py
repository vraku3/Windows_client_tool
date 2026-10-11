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



def test_review_unreadable_outlook_tree_is_refused(tmp_path, monkeypatch, caplog):
    from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss
    folder = tmp_path / "cache"
    folder.mkdir()
    def unreadable(path, onerror):
        onerror(PermissionError(13, "denied", str(folder)))
        return iter(())
    monkeypatch.setattr(ss.os, "walk", unreadable)
    assert ss.holds_outlook_data(str(folder), True)
    assert str(folder) in caplog.text and caplog.records[-1].levelname == "WARNING"
    assert ss.delete_items([ScanItem(str(folder), 1, True, True, "safe")]) == (0, 1)
    assert folder.exists()


def _whatsapp_like(root):
    """A Store WebView2 app as it sits on disk: caches beside the sign-in."""
    pkg = root / "Packages" / "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"
    profile = pkg / "LocalCache" / "EBWebView" / "Default"
    for sub in ("Cache", "Code Cache", "GPUCache", "IndexedDB/https_web.whatsapp.com_0.indexeddb.leveldb",
                "Local Storage/leveldb", "Network"):
        (profile / sub).mkdir(parents=True)
    (profile / "Cache" / "data_1").write_bytes(b"x" * 2_000_000)
    (profile / "IndexedDB" / "https_web.whatsapp.com_0.indexeddb.leveldb" / "000003.log").write_bytes(b"session")
    (profile / "Network" / "Cookies").write_bytes(b"cookie")
    (pkg / "LocalState").mkdir()
    (pkg / "LocalState" / "session.db").write_bytes(b"db")
    return pkg


def test_store_app_scanners_offer_the_caches_never_the_whole_localcache(tmp_path, monkeypatch):
    # 2026-10-11: both scanners offered LocalCache whole, and WhatsApp logged out after every clean.
    from modules.cleanup.cleanup_scanner import scanners_system as ss
    pkg = _whatsapp_like(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for scan in (ss.scan_store_app_caches, ss.scan_uwp_all_apps_cache):
        paths = [item.path for item in scan().items]
        assert paths == [str(pkg / "LocalCache" / "EBWebView" / "Default" / "Cache")], scan.__name__
        assert not any(ss.holds_sign_in_data(p, True) for p in paths)


def test_delete_refuses_any_folder_holding_a_sign_in(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    pkg = _whatsapp_like(tmp_path)
    profile = pkg / "LocalCache" / "EBWebView" / "Default"
    refused = [pkg / "LocalCache", profile / "IndexedDB", profile / "Local Storage" / "leveldb"]
    items = [ScanItem(str(p), 1, True, True, "safe") for p in refused]
    items.append(ScanItem(str(profile / "Network" / "Cookies"), 1, False, True, "safe"))
    assert ss.delete_items(items) == (0, 4)
    assert all(p.exists() for p in refused) and (profile / "Network" / "Cookies").exists()
    assert ss.delete_items([ScanItem(str(profile / "Cache"), 1, True, True, "safe")]) == (1, 0)
    assert not (profile / "Cache").exists()


def test_session_storage_and_token_stores_are_refused(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    profile = tmp_path / "BrowserCaches" / "abc"
    (profile / "Session Storage").mkdir(parents=True)
    (profile / "Session Storage" / "000003.log").write_bytes(b"x")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    sso = tmp_path / "home" / ".aws" / "sso" / "cache"
    sso.mkdir(parents=True)
    (sso / "token.json").write_text("{}")
    items = [ScanItem(str(profile), 1, True, True, "safe"), ScanItem(str(sso), 1, True, True, "caution"),
             ScanItem(str(tmp_path / "home"), 1, True, True, "danger")]
    assert ss.delete_items(items) == (0, 3)
    assert (profile / "Session Storage").exists() and (sso / "token.json").exists()


def test_a_guarded_outlook_folder_is_refused_without_being_walked(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import ScanItem, scanners_system as ss
    monkeypatch.setattr(ss, "protected_system_dirs", lambda: [])
    monkeypatch.setattr(ss, "protected_user_data_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(ss.os, "walk", lambda *a, **k: (_ for _ in ()).throw(AssertionError("walked")))
    assert ss.delete_items([ScanItem(str(tmp_path), 1, True, True, "safe")]) == (0, 1)
