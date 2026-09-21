"""35 catalog-backed categories were added to Quick Cleanup's dashboard --
real, already-tested scanners (catalog.py's scanner_for(id)) that were
previously reachable only via System Junk/App & Game Caches' own bulk
category inclusion, now individually featured as their own card (see
ADVANCED_CATEGORIES's own comment in quick_cleanup_tab.py).

Two catalog entries this batch was drawn from are DELIBERATELY absent:
"driver_store" and "application_manifest_cache" both carry their own
catalog labels reading "DANGER, system-critical" / "can break hardware" --
this pins that exclusion so it can't be silently undone by a later,
unrelated edit.
"""
from modules.cleanup.cleanup_scanner.catalog import load_catalog
from modules.cleanup.components.quick_cleanup_tab import ADVANCED_CATEGORIES

_NEW_CATALOG_IDS = {
    "complus_cache", "office_cache_extended", "onedrive_cache",
    "onedrive_commercial_cache", "onedrive_full_cache", "claude_cli_cache",
    "npp_cache", "vscode_cached_data", "vscode_settings_sync",
    "xbox_app_cache", "amd_radeon_cache", "browser_caches",
    "d3d_shader_cache", "diag_logs", "directx_shader_cache", "dxgi_cache",
    "event_logs", "font_cache", "language_packs", "memory_dumps",
    "minidump", "package_cache", "per_drive_recycle_bin", "per_drive_temp",
    "sfc_logs", "sysprep_logs", "thumbnail_cache_central", "update_cleanup",
    "wer_reports", "windows_backup_catalog", "windows_backup_logs",
    "windows_setup_diags", "windows_tweaker_logs",
    "windowsupdate_orch_cache", "wu_history_cache",
}


def test_every_new_id_is_on_the_dashboard():
    advanced_ids = {cid for cid, _label, _color in ADVANCED_CATEGORIES}
    missing = _NEW_CATALOG_IDS - advanced_ids
    assert not missing, f"new catalog ids never made it onto the dashboard: {missing}"


def test_driver_store_and_application_manifest_are_deliberately_excluded():
    advanced_ids = {cid for cid, _label, _color in ADVANCED_CATEGORIES}
    assert "driver_store" not in advanced_ids
    assert "application_manifest_cache" not in advanced_ids


def test_every_new_id_is_a_real_catalog_entry():
    catalog = load_catalog()
    missing = _NEW_CATALOG_IDS - set(catalog.keys())
    assert not missing, f"these ids do not exist in the catalog at all: {missing}"


def test_every_new_id_resolves_to_a_real_scanner_function(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab(on_category_clicked=lambda _cid: None)
    tab.build(advanced_categories=ADVANCED_CATEGORIES)

    for cid in _NEW_CATALOG_IDS:
        fn, _label, _color = tab._adv_scanner_map[cid]
        assert fn is not None, f"{cid} has no scanner function wired"
        assert callable(fn)
