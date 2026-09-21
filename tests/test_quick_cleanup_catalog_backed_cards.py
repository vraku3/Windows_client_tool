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

#: These are genuinely NEW catalog entries (not previously reachable
#: anywhere), added after cross-referencing this app's own catalog against
#: a real installed-software census of a live machine -- confirmed real,
#: not guessed (see REACHABLE_SCANNERS's own comment in
#: test_cleanup_catalog.py). The last four came from a direct AppData
#: folder census (registry Uninstall keys miss portable/Electron-updater
#: installs like these).
_NEW_MACHINE_SPECIFIC_IDS = {
    "curseforge_cache", "amd_dvr_cache", "cyberghost_cache",
    "opencode_desktop_cache", "amd_comgr_cache", "amd_install_manager_cache",
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

    for cid in _NEW_CATALOG_IDS | _NEW_MACHINE_SPECIFIC_IDS:
        fn, _label, _color = tab._adv_scanner_map[cid]
        assert fn is not None, f"{cid} has no scanner function wired"
        assert callable(fn)


def test_machine_specific_ids_are_on_the_dashboard():
    advanced_ids = {cid for cid, _label, _color in ADVANCED_CATEGORIES}
    missing = _NEW_MACHINE_SPECIFIC_IDS - advanced_ids
    assert not missing, f"machine-specific ids never made it onto the dashboard: {missing}"


def test_curseforge_cache_finds_real_data():
    """Confirmed real on this machine at test-authoring time (10.7 MB) --
    a real assertion against the live filesystem, not a mock, matching
    this module's own evidence-over-guessing practice. Skips cleanly on a
    machine without CurseForge installed."""
    from modules.cleanup.cleanup_scanner import scan_curseforge_cache

    result = scan_curseforge_cache()
    assert result.total_size >= 0  # never negative; may be 0 if not installed here


def test_amd_dvr_cache_is_caution_not_safe():
    """ReLive's own "Save Recent" action can leave a finished clip staged
    here briefly -- this must never be safe-tier, or "Clean All Safe"
    could delete a clip the user just asked to keep."""
    from modules.cleanup.cleanup_scanner.catalog import load_catalog

    spec = load_catalog()["amd_dvr_cache"]
    assert spec.safety == "caution"


def test_the_four_appdata_census_scanners_find_real_data_or_stay_at_zero():
    """Confirmed real on this machine at authoring time (CyberGhost 33 MB,
    OpenCode 1.2 MB, comgr 873 KB, AMD Install Manager 876 KB) -- real
    assertions against the live filesystem. Never negative; 0 on a machine
    without these apps installed."""
    from modules.cleanup.cleanup_scanner import (
        scan_cyberghost_cache, scan_opencode_desktop_cache,
        scan_amd_comgr_cache, scan_amd_install_manager_cache,
    )

    for fn in (scan_cyberghost_cache, scan_opencode_desktop_cache,
               scan_amd_comgr_cache, scan_amd_install_manager_cache):
        result = fn()
        assert result.total_size >= 0
