"""Research decisions are pinned without depending on the private KB."""
import json
from pathlib import Path

import pytest

DEFS = Path(__file__).resolve().parents[1] / 'src/modules/cleanup/cleanup_scanner/definitions'
CAUTION = '''d3d_shader_cache directx_shader_cache iis_logs windows_cbs_logs golang_cache
em_client_cache notezilla_cache winscp_cache electron_updater_leftovers inetcache_ietlc
chromium_service_worker_scripts fortnite_cache path_of_exile_cache steam_cache
steam_download_cache steam_full_cache steam_webhelper_cache ubisoft_cache audacity_cache
blender_full_cache illustrator_cache intel_graphics_cache nvidia_display_cache vegas_cache
box_cache google_drive_cache pcloud_cache slack_cache_full discord_full_cache'''.split()
JETBRAINS = '''jetbrains_cache intellij_cache pycharm_cache webstorm_cache phpstorm_cache
goland_cache clion_cache rider_cache datagrip_cache rubymine_cache'''.split()


def specs():
    return {s['id']: s for path in DEFS.glob('*.json')
            for s in json.loads(path.read_text(encoding='utf-8'))['scanners']}


@pytest.mark.parametrize('entry', CAUTION)
def test_research_caution_is_never_safe(entry):
    assert specs()[entry]['safety'] == 'caution'


@pytest.mark.parametrize('entry', JETBRAINS + ['android_studio_cache', 'thunderbird_cache'])
def test_system_caches_use_localappdata(entry):
    paths = specs()[entry]['paths']
    assert paths and all(p.startswith('%LOCALAPPDATA%') for p in paths)
    if entry in JETBRAINS:
        assert all(p.endswith(('\\caches', '\\log')) for p in paths)


@pytest.mark.parametrize('entry', ['steam_cache', 'steam_download_cache', 'steam_full_cache', 'steam_webhelper_cache'])
def test_steam_profile_entries_only_target_cache_leaves(entry):
    paths = specs()[entry]['paths']
    assert paths and all(p.rsplit('\\', 1)[-1] in ('Cache', 'Code Cache', 'GPUCache') for p in paths)


@pytest.mark.parametrize('entry', ['box_cache', 'google_drive_cache', 'pcloud_cache'])
def test_cloud_logs_split_from_upload_caches(entry):
    catalog = specs()
    assert not any(p.lower().endswith('\\logs') for p in catalog[entry]['paths'])
    logs = catalog[entry.removesuffix('_cache') + '_logs']
    assert logs['safety'] == 'safe'
    assert all(p.lower().endswith('\\logs') for p in logs['paths'])


def test_no_enabled_servicing_or_site_data_targets():
    for entry in specs().values():
        if entry.get('disabled_reason'):
            continue
        for path in entry['paths']:
            low = path.lower()
            assert '\\winsxs\\' not in low, entry['id']
            assert not any(t in low for t in ('\\blob_storage', '\\service worker\\scriptcache',
                                               '\\service worker\\cachestorage')), entry['id']
    mysql = specs()['mysql_logs']['paths']
    assert all(p.lower().endswith(('*.log', '*.err')) for p in mysql)


@pytest.mark.parametrize('entry,term', [
    ('telegram_cache', 'session data'), ('zoom_recordings_cache', 'recordings'),
    ('rdp_cache', 'clipboard'), ('onedrive_cache', 'sync conflict'),
    ('mega_cache', 'sync database'), ('sharepoint_cache', 'offline'),
    ('evernote_full_cache', 'databases'), ('logseq_cache', 'graph database'),
    ('obsidian_cache', 'plugins'), ('office_temp', 'unsaved'),
    ('eclipse_cache', 'Eclipse'), ('lm_studio_cache', 'model cache'),
    ('wsl_installer_cache', 'distribution'), ('postgres_logs', 'pg_xlog'),
])
def test_labels_describe_only_actual_targets(entry, term):
    spec = specs()[entry]
    assert term.lower() not in (spec['label'] + spec['description']).lower()


def test_duplicate_entries_record_the_owner():
    catalog = specs()
    for duplicate, owner in {
        'directx_shader_cache': 'd3d_shader_cache',
        'chrome_cache_full': 'chrome_cache', 'edge_cache_full': 'edge_cache',
        'brave_cache_full': 'brave_cache', 'evernote_full_cache': 'evernote_cache',
        'aftereffects_cache': 'premiere_cache', 'onedrive_logs': 'onedrive_cache',
        'sfc_logs': 'diag_logs', 'windows_cbs_logs': 'diag_logs',
        'panther_logs': 'sysprep_logs', 'nuget_global_packages': 'nuget_cache',
    }.items():
        assert catalog[duplicate]['disabled_reason'] == 'duplicate of ' + owner


def test_document_targets_follow_known_folder_redirection(tmp_path, monkeypatch):
    from modules.cleanup.cleanup_scanner import catalog, known_folders
    redirected = tmp_path / 'OneDrive' / 'Documents'
    monkeypatch.setattr(known_folders, 'known_folder', lambda name: str(redirected))
    assert catalog.expand(r'%DOCUMENTS%\Elder Scrolls Online\live\Logs') == str(redirected / 'Elder Scrolls Online/live/Logs')


def test_enabled_entries_do_not_offer_the_same_literal_path_twice():
    seen = {}
    for entry in specs().values():
        if entry.get('disabled_reason'):
            continue
        for path in entry['paths']:
            key = path.lower()
            assert key not in seen, (entry['id'], seen.get(key), path)
            seen[key] = entry['id']


@pytest.mark.parametrize('entry', '''acronis_cache macrium_cache veeam_cache apex_cache
blizzard_downloads_cache ck2_cache hoi4_cache stellaris_cache diablo4_cache epic_manifest_cache
eso_cache gamepass_cache itch_app_cache lol_cache minecraft_cache rockstar_cache rust_game_cache
valorant_cache amd_radeon_cache windows_backup_catalog windows_backup_logs'''.split())
def test_log_labels_do_not_promise_removing_user_data(entry):
    text = (specs()[entry]['label'] + ' ' + specs()[entry]['description']).lower()
    assert 'log' in text
    for misleading in ('shader', 'replay', 'staging', 'manifest data', 'metadata', 'database',
                       'game save', 'autosave', 'audio cache', 'differential chain'):
        assert misleading not in text, (entry, misleading)


@pytest.mark.parametrize('entry,forbidden', [
    ('bitwarden_desktop_cache', 'logs'), ('vivaldi_cache', 'blob storage'),
    ('edge_cache', 'web data'), ('thunderbird_cache', 'panacea'),
    ('firefox_cache', 'content prefs'), ('office_cache_extended', 'onenote'),
    ('dropbox_cache', 'cache.db'), ('expressvpn_cache', 'split-tunneling'),
    ('signal_cache', 'attachment'), ('skype_cache', 'shared files'),
    ('teamspeak_cache', 'query interface'), ('discord_canary_cache', 'crash'),
    ('battle_net_auth_cache', 'authentication ticket'), ('ea_desktop_full_cache', 'content staging'),
    ('epic_games_cache', 'manifest data'), ('gog_offline_cache', 'backup metadata'),
    ('unity_cache', 'library'), ('unity_hub_cache', 'editor installs'),
    ('unreal_cache', 'saved folders'), ('blender_cache', 'autosave'),
    ('ffmpeg_cache', 'FFmpeg-only'), ('illustrator_cache', 'saved recovery'),
    ('nvidia_display_cache', 'DRS'), ('nvidia_geforce_cache', 'screenshot'),
    ('obs_cache', 'replay buffer'), ('photoshop_cache', 'history'),
    ('premiere_cache', 'auto-save'), ('aftereffects_cache', 'auto-save'),
    ('windowsupdate_orch_cache', 'Orchestrator scan'), ('update_cleanup', 'orphaned patches'),
    ('npm_cache', 'pnpm'), ('java_cache', 'Gradle'),
    ('vscode_settings_sync', 'workspace storage'), ('memory_dumps', '\ufffd'),
    ('panther_logs', 'safe after setup'),
    ('inetcache_ietlc', 'DownloadHistory'),
])
def test_remaining_research_labels_do_not_claim_untargeted_data(entry, forbidden):
    entry = specs()[entry]
    assert forbidden.lower() not in (entry['label'] + entry['description']).lower()


def test_ai_model_tooltip_explains_redownload():
    assert 'downloads it again' in specs()['chrome_on_device_ai_model']['description']


def test_inetcache_entry_targets_only_the_web_cache_folders():
    paths = [p.lower() for p in specs()['inetcache_ietlc']['paths']]
    assert paths and all(p.endswith(('\\inetcache\\ie', '\\inetcache\\low\\ie')) for p in paths)


def test_sysprep_logs_did_not_grow_a_wildcard():
    # A narrowing pass must never widen: setupapi.log stays the one file it was.
    assert not any('*' in p for p in specs()['sysprep_logs']['paths'] if 'setupapi' in p.lower())


def test_no_enabled_catalog_path_is_one_the_deleter_always_refuses():
    """A target the guards refuse by name/location is dead weight at best and,
    worse, a 'refused' line every run: keep the catalog and the guards agreeing."""
    from modules.cleanup.cleanup_scanner import catalog, scanners_system as ss
    dead = []
    for spec in catalog.load_catalog(force=True).values():
        if spec.disabled_reason:
            continue
        for raw in spec.paths:
            path = catalog.expand(raw.replace('%FIXED_DRIVES%', 'C:'))
            if path and ss._state_path_protected(path.replace('*', 'x'), True, contains=True,
                                                 resolved=path.replace('*', 'x')):
                dead.append((spec.id, raw))
    assert dead == []
