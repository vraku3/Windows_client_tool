"""Duplicate safety checks operate only on scratch files and faked shell COM."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from modules.cleanup.cleanup_scanner import exclusions
from modules.cleanup.duplicates import actions
from modules.cleanup.duplicates.enumerate import ScanOptions
from modules.cleanup.duplicates.finder import find_duplicates
from modules.game_mode.engine import app_usage
from test_duplicates_actions import groups_at


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(actions, "system_folders", lambda: [])


@pytest.mark.parametrize("mode", ["recycle", "delete", "move"])
def test_named_stream_refuses_duplicate_actions(tmp_path, mode):
    groups = groups_at(tmp_path)
    target = groups[0].files[1]
    with open(target.path + ":extra", "wb") as stream:
        stream.write(b"unique metadata")
    groups = find_duplicates(ScanOptions(roots=[str(tmp_path)], min_size=1,
        skip_system_folders=False, use_cache=False)).groups
    target = groups[0].files[1]
    dest = tmp_path / "destination"
    dest.mkdir()
    called = []
    result = actions.perform_actions(groups, {target.path}, mode=mode, destination=str(dest),
                                     action=called.append, record=False)
    assert not called
    assert result.outcomes[0].status == "skipped"
    assert "stream" in result.outcomes[0].reason.lower()
    assert os.path.exists(target.path)


def test_zone_identifier_stream_is_allowed(tmp_path):
    groups = groups_at(tmp_path)
    with open(groups[0].files[1].path + ":Zone.Identifier", "w") as stream:
        stream.write("[ZoneTransfer]\nZoneId=3\n")
    groups = find_duplicates(ScanOptions(roots=[str(tmp_path)], min_size=1,
        skip_system_folders=False, use_cache=False)).groups
    assert actions.verify_pair(*groups[0].files) is None


def test_stream_enumeration_failure_refuses_action(tmp_path, monkeypatch):
    import ctypes
    real = ctypes.WinDLL
    fake = Mock()
    fake.FindFirstStreamW.return_value = ctypes.c_void_p(-1).value
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: fake if name == "kernel32" else real(name, **kw))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    groups = groups_at(tmp_path)
    reason = actions.verify_pair(*groups[0].files)
    assert reason and "stream" in reason.lower()


@pytest.mark.parametrize("payload", ["{broken", "{}", '[123]', '[""]'])
def test_broken_exclusions_refuse_every_path(tmp_path, payload):
    exclusions._config_path().write_text(payload, encoding="utf-8")
    assert actions.always_refused(str(tmp_path / "ordinary.bin")) == "Cleanup exclusions could not be read"


def test_exclusion_read_error_refuses_but_missing_is_empty(tmp_path, monkeypatch):
    assert actions.always_refused(str(tmp_path / "ordinary.bin")) is None
    monkeypatch.setattr(exclusions, "_config_path", lambda: (_ for _ in ()).throw(PermissionError("denied")))
    assert actions.always_refused(str(tmp_path / "ordinary.bin")) == "Cleanup exclusions could not be read"


# Move-journal tests moved to test_review5_dup_safety.py (the move is MoveFileExW now).


def test_recycle_does_not_take_connected_html_files(tmp_path, monkeypatch):
    import pythoncom
    from win32com.shell import shell
    op = Mock()
    op.GetAnyOperationsAborted.return_value = False
    monkeypatch.setattr(pythoncom, "CoCreateInstance", lambda *_args: op)
    monkeypatch.setattr(shell, "SHCreateItemFromParsingName", lambda *_args: object())
    actions.recycle_file(str(tmp_path / "report.html"))
    assert op.SetOperationFlags.call_args.args[0] & 0x2000


@pytest.mark.parametrize("vendor", ["Bitdefender", "Defender", "Malwarebytes", "ESET", "Kaspersky", "Norton",
    "Avast", "AVG", "McAfee", "Sophos", "Trend Micro", "Webroot", "CrowdStrike", "SentinelOne", "Sysmon",
    "F-Secure", "G Data", "Panda", "Comodo", "Avira"])
def test_security_vendors_share_removal_protection(tmp_path, vendor):
    assert actions.always_refused(str(tmp_path / vendor / "component.bin")) is not None
    assert app_usage.protected(vendor)


def test_stale_webroot_secureanywhere_never_suggested():
    now = datetime.now(timezone.utc)
    evidence = [app_usage.Evidence("exe:wrsa.exe", now - timedelta(days=150), "UserAssist")]
    report = app_usage.analyse(evidence, [{"name": "Webroot SecureAnywhere",
        "display_icon": r"C:\Tools\wrsa.exe"}], [], now=now)
    assert not report.suggestions
    assert report.protected == 1
