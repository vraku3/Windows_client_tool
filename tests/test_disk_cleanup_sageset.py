"""disk_cleanup_sageset: reads which cleanmgr categories are checked in
this app's own /sageset profile, and builds the /sageset + /sagerun
commands. No PyQt6, no real registry writes -- winreg reads are exercised
against the REAL machine's VolumeCaches key (read-only, always present on
Windows), monkeypatched only for the refusal case.
"""
import winreg

from modules.cleanup import disk_cleanup_sageset as dcs


def test_sageset_command_uses_this_apps_profile_number():
    cmd = dcs.sageset_command()
    assert cmd == ["cleanmgr.exe", f"/sageset:{dcs.PROFILE_NUMBER}"]


def test_sagerun_command_uses_this_apps_profile_number():
    cmd = dcs.sagerun_command()
    assert cmd == ["cleanmgr.exe", f"/sagerun:{dcs.PROFILE_NUMBER}"]


def test_configured_categories_reads_the_real_volume_caches_key():
    """VolumeCaches always exists on a real Windows install -- this is a
    real, read-only registry read, not a mock. A freshly-picked
    PROFILE_NUMBER (42) is very unlikely to already be configured on this
    machine, so the expected answer is an empty list, not None -- None
    would mean the read itself failed, which a present, readable key must
    not produce."""
    result = dcs.configured_categories()
    assert result is not None
    assert isinstance(result, list)


def test_configured_categories_returns_none_when_the_key_cannot_be_opened(monkeypatch):
    def _raise_open_key(*args, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(winreg, "OpenKey", _raise_open_key)

    result = dcs.configured_categories()

    assert result is None, (
        "a registry refusal must not collapse into an empty list -- that "
        "reads as 'nothing configured' instead of 'could not check'")
