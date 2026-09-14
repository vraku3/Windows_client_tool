"""cleanup_scanner package facade. Re-exports all scanners and shared types
so existing imports (`from modules.cleanup import cleanup_scanner as cs`,
`from modules.cleanup.cleanup_scanner import ScanResult, ScanItem, format_size`)
keep working unchanged."""
from modules.cleanup.cleanup_scanner._common import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_apps import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_browsers import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_cloud import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_comms import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_dev import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_games import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_media import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_profiles import *  # noqa: F401,F403
from modules.cleanup.cleanup_scanner.scanners_system import *  # noqa: F401,F403

# ── Catalog-defined scanners ───────────────────────────────────────────
#
# The 41 scanners in `definitions/system.json` are data, not code: each was
# the same three lines (expand some environment variables into paths, call
# _make_item, sum the sizes) and they came to 777 lines of this package.
# `catalog.scanner_for` builds a callable with the same
# `scan_x(min_age_days=0)` signature the cleanup tabs pass around, so
# nothing downstream can tell the difference.
#
# Bound AFTER the star-imports above, deliberately: if a hand-written
# scanner of the same name ever reappears, the catalog is what wins, and
# tests/test_cleanup_catalog.py asserts every name resolves.
from modules.cleanup.cleanup_scanner.catalog import (  # noqa: E402
    ScannerSpec, all_scanners, load_catalog, run_spec, scanner_for,
)

globals().update(all_scanners())


# ── Category groups shared by Quick Cleanup's dashboard and the
# unattended "cleanup" stage (modules/updates/stage_runners.py) ──
#
# Moved here from modules/cleanup/tabs/_overview_tab.py during the
# Cleanup/Quick Cleanup merge — that file is going away, but
# run_cleanup_safe_stage's import of this exact list is not something
# this merge may break silently. Placed at the PACKAGE ROOT rather than
# in one submodule: its scanners span scanners_apps, scanners_cloud,
# scanners_media, scanners_dev and scanners_system, and by this point in
# this file every one of them is already bound above via the star-imports
# and all_scanners(), unprefixed.
_OV_GROUPS = [
    ("System Junk", [
        scan_temp_files, scan_prefetch, scan_thumbnail_cache, scan_user_crash_dumps,
    ]),
    ("Browser Caches", None),    # handled specially via bs.detect_browsers()
    ("App & Game Caches", [
        scan_app_caches, scan_d3d_shader_cache, scan_appdata_autodiscover,
        scan_steam_cache, scan_stremio_cache, scan_outlook_cache,
        scan_winget_packages, scan_store_app_caches,
        scan_discord_cache, scan_spotify_cache, scan_zoom_cache,
        scan_slack_cache, scan_discord_full_cache, scan_teams_cache,
        scan_telegram_cache, scan_brave_cache, scan_vivaldi_cache,
        scan_opera_cache, scan_edge_cache, scan_firefox_cache, scan_chrome_cache,
        scan_game_caches, scan_epic_launcher_cache, scan_ea_app_cache,
        scan_ubisoft_cache, scan_battlenet_cache, scan_gog_cache,
        scan_rockstar_cache, scan_minecraft_cache, scan_rust_game_cache,
    ]),
    ("Windows Update", [
        scan_wu_cache, scan_delivery_optimization, scan_update_cleanup,
        scan_windows_old, scan_installer_patch_cache,
    ]),
    ("Logs & Reports", [
        scan_windows_logs, scan_event_logs, scan_wer_reports,
        scan_memory_dumps, scan_panther_logs, scan_dmf_logs,
        scan_onedrive_logs, scan_defender_history, scan_diag_logs,
        scan_powershell_logs, scan_sysprep_logs, scan_msi_logs,
        scan_wmi_logs, scan_sfc_logs, scan_group_policy_logs,
    ]),
    ("Large Items", [
        scan_windows_old, scan_recycle_bin, scan_installer_patch_cache,
    ]),
    ("Dev Tools", [
        scan_dev_tool_caches, scan_vscode_cache, scan_jetbrains_cache,
        scan_npm_cache, scan_pip_cache, scan_nuget_cache,
        scan_golang_cache, scan_rust_cache, scan_java_cache,
        scan_unity_cache, scan_unreal_cache,
    ]),
    ("Cloud Storage", [
        scan_dropbox_cache, scan_google_drive_cache, scan_mega_cache,
        scan_pcloud_cache, scan_icloud_cache, scan_onedrive_full_cache,
    ]),
    ("Media Production", [
        scan_obs_cache, scan_davinci_cache, scan_premiere_cache,
        scan_blender_cache, scan_audacity_cache, scan_handbrake_cache,
    ]),
]


# What this package exports, said out loud.
#
# The eight `import *` lines above are what the scanners still look like;
# until audit #14's remaining batches turn them into data, listing every
# name by hand would be a second copy of the same 500 names to keep in
# step. `__all__` is therefore COMPUTED from what actually got bound —
# which is still a real improvement on nothing, because it makes
# `from ... import *` on THIS package deterministic and lets a reader ask
# the package what it has.
__all__ = sorted(
    name for name in dict(globals())
    if not name.startswith("_")
    and name not in {"annotations"}
)
