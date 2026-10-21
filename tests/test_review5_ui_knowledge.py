"""Fix brief B: current catalog targets and conservative knowledge ratings."""
import pytest

from modules.cleanup.cleanup_scanner.catalog import load_catalog, targets_of
from modules.cleanup.cleanup_scanner.knowledge import entry_for


@pytest.mark.parametrize('id_', ['teams_new_webview_cache', 'vscode_crashpad', 'zoom_cache', 'amd_comgr_cache'])
def test_diagnostics_and_uncertain_caches_require_review(id_):
    assert load_catalog()[id_].safety == 'caution'
    assert entry_for(id_).verdict == 'caution'


def test_inetcache_text_matches_only_ie_targets():
    entry = entry_for('inetcache_ietlc')
    assert 'Low\\IE' in entry.what and 'INetCache\\IE' in entry.what
    assert 'Outlook' not in entry.risk
    assert 'outlook' not in entry.catalog_concern.lower()


def test_thunderbird_text_acknowledges_local_profile_targets():
    entry = entry_for('thunderbird_cache')
    assert '%LOCALAPPDATA%' in entry.what
    assert 'not listed' not in entry.catalog_concern


def test_gradle_text_distinguishes_retention_by_cache_type():
    text = entry_for('gradle_caches_cache').used_for
    assert all(str(days) in text for days in (7, 14, 30))
    assert 'unused >30 days' not in text


def test_update_downloads_regenerate_and_pending_installs_redownload():
    entry = entry_for('wu_cache')
    assert entry.regenerates is True
    assert 'pending' in entry.risk.lower()


def test_pending_crash_reports_warn_about_uploads():
    entry = entry_for('vscode_crashpad')
    assert 'upload' in entry.risk and entry.before_cleaning


@pytest.mark.parametrize('id_,paths', [
    ('vscode_electron_cache', [r'%APPDATA%\Code\Code Cache', r'%APPDATA%\Code\GPUCache']),
    ('epic_versioned_webcache', [r'%LOCALAPPDATA%\EpicGamesLauncher\Saved\webcache_4147',
                                r'%LOCALAPPDATA%\EpicGamesLauncher\Saved\webcache_4430']),
    ('amd_comgr_cache', [r'%LOCALAPPDATA%\cache\comgr']),
])
def test_documented_cache_leaves_have_knowledge_and_app_close_note(id_, paths):
    spec = load_catalog()[id_]
    assert set(paths) <= set(spec.paths)
    assert not spec.disabled_reason
    entry = entry_for(id_)
    assert entry and entry.sources and entry.before_cleaning
    assert entry.verdict in ('safe', 'caution')


def test_firefox_local_startup_cache_expands_profile_globs(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    path = tmp_path / 'Mozilla' / 'Firefox' / 'Profiles' / 'test.default' / 'startupCache'
    path.mkdir(parents=True)
    assert str(path) in targets_of(load_catalog()['firefox_cache'])
    assert entry_for('firefox_cache').sources
