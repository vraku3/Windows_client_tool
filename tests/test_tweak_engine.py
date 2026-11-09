# tests/test_tweak_engine.py
import pytest
import winreg
from unittest.mock import patch, MagicMock
from core.backup_service import BackupService


@pytest.fixture
def engine(tmp_path):
    from modules.tweaks.tweak_engine import TweakEngine
    svc = BackupService(data_dir=str(tmp_path))
    eng = TweakEngine(backup_service=svc)
    yield eng
    svc.close()


def _oserror(winerror):
    """winreg raises OSError subclasses carrying a Windows error code; the
    engine branches on 2 (not found) vs 5 (access denied)."""
    err = OSError()
    err.winerror = winerror
    return err


def _reg_tweak():
    return {
        "id": "test_tweak",
        "name": "Test",
        "requires_admin": True,
        "steps": [
            {"type": "registry", "key": r"HKCU\Software\Test", "value": "Val", "data": 1, "kind": "DWORD"}
        ]
    }


def _svc_tweak():
    return {
        "id": "svc_tweak",
        "name": "Service Test",
        "requires_admin": True,
        "steps": [
            {"type": "service", "name": "TestSvc", "start_type": "disabled"}
        ]
    }


def _cmd_tweak():
    return {
        "id": "cmd_tweak",
        "name": "Cmd",
        "requires_admin": True,
        "steps": [
            {"type": "command", "cmd": "echo test"}
        ]
    }


def test_apply_registry_tweak_succeeds(engine):
    errors = []
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.QueryValueEx", return_value=(0, 4)), \
         patch("winreg.CreateKeyEx", return_value=mock_key), \
         patch("winreg.SetValueEx"):
        result = engine.apply_tweak(_reg_tweak(), rp_id="rp1", on_error=errors.append)
    assert result is True
    assert errors == []


def test_apply_command_tweak_succeeds(engine):
    errors = []
    ok_proc = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=ok_proc):
        result = engine.apply_tweak(_cmd_tweak(), rp_id="rp1", on_error=errors.append)
    assert result is True


def test_detect_status_applied_registry(engine):
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.QueryValueEx", return_value=(1, 4)):
        status = engine.detect_status(_reg_tweak())
    assert status == "applied"


def test_detect_status_not_applied_registry(engine):
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.QueryValueEx", return_value=(0, 4)):
        status = engine.detect_status(_reg_tweak())
    assert status == "not_applied"


def test_missing_registry_key_reads_as_not_applied_not_unknown(engine):
    """A key that does not exist is Windows sitting at its default, which is
    exactly "not applied". Reporting it as "unknown" told the user we had no
    idea when in fact we knew perfectly well."""
    with patch("winreg.OpenKey", side_effect=_oserror(2)):
        result = engine.detect(_reg_tweak())
    assert result.status == "not_applied"
    assert "does not exist" in result.reason


def test_missing_registry_value_reads_as_not_applied(engine):
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.QueryValueEx", side_effect=_oserror(2)):
        result = engine.detect(_reg_tweak())
    assert result.status == "not_applied"
    assert "not set" in result.reason


def test_access_denied_is_the_only_registry_unknown(engine):
    """"Unknown" now means we genuinely could not look — and says so."""
    with patch("winreg.OpenKey", side_effect=_oserror(5)):
        result = engine.detect(_reg_tweak())
    assert result.status == "unknown"
    assert "permission" in result.reason.lower()


def test_readable_but_write_locked_registry_tweak_remains_not_applied(engine):
    """This PC's Communications key is readable but owned by TrustedInstaller."""
    tweak = _reg_tweak()
    tweak["steps"][0]["key"] = (
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Communications")
    key = MagicMock()
    key.__enter__ = lambda s: s
    key.__exit__ = MagicMock(return_value=False)

    def open_key(hive, sub, reserved=0, access=winreg.KEY_READ):
        if access == winreg.KEY_SET_VALUE:
            raise _oserror(5)
        return key

    with patch("winreg.OpenKey", side_effect=open_key), \
         patch("winreg.QueryValueEx", side_effect=FileNotFoundError(2, "not found")), \
         patch("winreg.CreateKeyEx") as create:
        result = engine.detect(tweak)
        assert engine.check_applicable(tweak).applicable

    assert result.status == "not_applied"
    create.assert_not_called()


def test_missing_tips_app_is_unavailable_before_selection(engine, monkeypatch):
    tweak = {"id": "disable_windows_tips_app", "name": "Disable Windows Tips App",
             "steps": [{"type": "appx", "package": "Microsoft.GetTip",
                        "action": "remove"}]}
    monkeypatch.setattr(engine._os, "appx_installed", lambda name: False)
    result = engine.detect(tweak)
    assert result.status == "not_applicable"
    assert "not installed" in result.reason


def test_absent_means_applied_flips_the_default_reading(engine):
    tweak = _reg_tweak()
    tweak["steps"][0]["absent_means"] = "applied"
    with patch("winreg.OpenKey", side_effect=_oserror(2)):
        assert engine.detect(tweak).status == "applied"


def test_reason_names_the_actual_value_found(engine):
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.QueryValueEx", return_value=(7, 4)):
        result = engine.detect(_reg_tweak())
    assert result.status == "not_applied"
    assert "is 7" in result.reason and "wants 1" in result.reason


def test_mixed_steps_report_partial(engine):
    tweak = {
        "id": "mixed", "name": "Mixed", "steps": [
            {"type": "registry", "key": r"HKCU\Software\A", "value": "V",
             "data": 1, "kind": "DWORD"},
            {"type": "registry", "key": r"HKCU\Software\B", "value": "V",
             "data": 1, "kind": "DWORD"},
        ],
    }
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.QueryValueEx", side_effect=[(1, 4), (0, 4)]):
        result = engine.detect(tweak)
    assert result.status == "partial"
    assert "1 of 2" in result.reason


def test_absent_service_is_not_applicable_not_unknown(engine):
    tweak = _svc_tweak()
    with patch.object(engine._os, "service_exists", return_value=False):
        result = engine.detect(tweak)
    assert result.status == "not_applicable"
    assert "TestSvc" in result.reason


def test_unqueryable_service_stays_unknown(engine):
    """False means "not installed"; None means "we were not allowed to look".
    Collapsing the two would tell the user a service is absent when it is
    merely unreadable."""
    with patch.object(engine._os, "service_exists", return_value=None):
        assert engine.detect(_svc_tweak()).status == "unknown"


def test_applies_to_build_gate_reports_not_applicable(engine):
    tweak = _reg_tweak()
    tweak["applies_to"] = {"min_build": 99999}
    result = engine.detect(tweak)
    assert result.status == "not_applicable"
    assert "99999" in result.reason


def test_applies_to_alias_resolves(engine):
    tweak = _reg_tweak()
    tweak["applies_to"] = {"max_build": "22H2"}  # 19045 -- a Win10 build
    result = engine.detect(tweak)
    assert result.status == "not_applicable"


def test_detect_block_overrides_command_steps(engine):
    """A command step is unknowable; a `detect` block is how such a tweak
    becomes checkable at all."""
    tweak = _cmd_tweak()
    assert engine.detect(tweak).status == "unknown"

    tweak["detect"] = {"type": "registry", "key": r"HKCU\Software\Test",
                       "value": "Val", "data": 1, "kind": "DWORD"}
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.QueryValueEx", return_value=(1, 4)):
        assert engine.detect(tweak).status == "applied"


def test_command_unknown_still_explains_itself(engine):
    result = engine.detect(_cmd_tweak())
    assert result.status == "unknown"
    assert result.reason, "an Unknown with no reason is the bug being fixed"


def test_binary_hex_string_compares_against_bytes(engine):
    tweak = {
        "id": "bin", "name": "Bin", "steps": [
            {"type": "registry", "key": r"HKCU\Software\Test", "value": "B",
             "data": "00 01 02", "kind": "BINARY"},
        ],
    }
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.QueryValueEx", return_value=(b"\x00\x01\x02", 3)):
        assert engine.detect(tweak).status == "applied"


def test_detect_status_string_api_still_works(engine):
    """The Qt signal carries a plain string; keep that contract."""
    with patch("winreg.OpenKey", side_effect=_oserror(2)):
        assert engine.detect_status(_reg_tweak()) == "not_applied"


def test_load_definitions_reads_json(tmp_path):
    from modules.tweaks.tweak_engine import TweakEngine
    import json
    data = [{"id": "t1", "name": "T1", "steps": []}]
    f = tmp_path / "test.json"
    f.write_text(json.dumps(data))
    result = TweakEngine.load_definitions(str(f))
    assert result == data


# --- the apply is the only moment that knows whether it made the key --------
#
# A revert can delete the value it wrote, but it cannot tell whether the KEY
# was there beforehand -- and a key's existence can be the whole point, so
# guessing is not allowed. The apply knows, and now records it.

def test_creating_a_key_is_recorded_so_the_revert_can_undo_it(engine, tmp_path):
    """The elevated LLMNR round-trip left an empty policy key behind because
    nothing recorded that the apply had created it."""
    import winreg

    root = r"Software\WinClientToolTest_EngineKey"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, root)
    except FileNotFoundError:
        pass

    recorded = []
    engine._backup.record_steps = (
        lambda tweak_id, steps, rp_id: recorded.extend(steps))
    rp_id = engine._backup.create_restore_point("engine key", "Tweaks")
    tweak = {"id": "t", "name": "T", "requires_admin": False,
             "steps": [{"type": "registry", "key": "HKCU\\" + root,
                        "value": "Val", "data": 1, "kind": "DWORD"}]}
    try:
        engine.apply_tweak(tweak, rp_id)
        assert recorded, "nothing was recorded at all"
        assert recorded[0].key_created is True
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, root)
        except OSError:
            pass


def test_writing_into_a_key_that_was_already_there_records_no_creation(
        engine, tmp_path):
    """Only a key we made is ours to remove later."""
    import winreg

    root = r"Software\WinClientToolTest_EngineExisting"
    winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, root).Close()

    recorded = []
    engine._backup.record_steps = (
        lambda tweak_id, steps, rp_id: recorded.extend(steps))
    rp_id = engine._backup.create_restore_point("engine existing", "Tweaks")
    tweak = {"id": "t", "name": "T", "requires_admin": False,
             "steps": [{"type": "registry", "key": "HKCU\\" + root,
                        "value": "Val", "data": 1, "kind": "DWORD"}]}
    try:
        engine.apply_tweak(tweak, rp_id)
        assert recorded, "nothing was recorded at all"
        assert recorded[0].key_created is False
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, root)
        except OSError:
            pass
