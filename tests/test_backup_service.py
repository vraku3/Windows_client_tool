# tests/test_backup_service.py
import os
import sqlite3
import winreg
from unittest.mock import patch, MagicMock

import pytest
from core.backup_service import BackupService, StepRecord, RestoreResult, RestorePointInfo


@pytest.fixture
def svc(tmp_path):
    s = BackupService(data_dir=str(tmp_path))
    yield s
    s.close()


def test_create_restore_point_returns_32char_hex(svc, tmp_path):
    rp_id = svc.create_restore_point("Test backup", "Tweaks")
    assert isinstance(rp_id, str) and len(rp_id) == 32


def test_backup_appx_records_a_store_link(svc, tmp_path):
    """#8: the removed-apps record carries a Store deep link so restore can
    point the user at a real reinstall instead of a winget miss."""
    rp_id = svc.create_restore_point("Appx", "Store Apps")
    svc.backup_appx_package(
        "Microsoft.WindowsCalculator", rp_id,
        store_link="ms-windows-store://pdp/?PFN=Microsoft.WindowsCalculator_8wekyb3d8bbwe",
    )
    path = tmp_path / "backups" / os.listdir(tmp_path / "backups")[0] / "appx" / "removed_apps.json"
    import json
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert entries == [{
        "package": "Microsoft.WindowsCalculator",
        "store_link": "ms-windows-store://pdp/?PFN=Microsoft.WindowsCalculator_8wekyb3d8bbwe",
    }]


def test_appx_revert_with_store_link_opens_the_store(monkeypatch, tmp_path):
    """A step whose revert_command is a Store link launches the page rather
    than gambling on winget knowing the package name."""
    import core.backup_service as bs

    started = []
    monkeypatch.setattr(bs.os, "startfile", lambda url: started.append(url))

    s = BackupService(data_dir=str(tmp_path))
    try:
        rp_id = s.create_restore_point("Appx link", "Store Apps")
        s.record_steps(
            "store_apps_batch",
            [StepRecord("appx", "Microsoft.WindowsCalculator",
                        "Microsoft.WindowsCalculator", None,
                        revert_command="ms-windows-store://pdp/?PFN=X")],
            rp_id,
        )
        result = s.restore_point(rp_id)
        assert result.success
        assert started == ["ms-windows-store://pdp/?PFN=X"]
    finally:
        s.close()


def test_create_restore_point_creates_subfolders(svc, tmp_path):
    svc.create_restore_point("My backup", "Cleanup")
    backup_dir = tmp_path / "backups"
    dirs = [d for d in backup_dir.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    assert (dirs[0] / "manifest.json").exists()
    for sub in ("registry", "services", "appx", "files"):
        assert (dirs[0] / sub).is_dir()


def test_record_steps_counted_in_list(svc):
    rp_id = svc.create_restore_point("Test", "Tweaks")
    steps = [
        StepRecord("registry", r"HKLM\SOFTWARE\Test", None, 0),
        StepRecord("service", "DiagTrack", 2, 4),
    ]
    svc.record_steps("tweak_disable_telemetry", steps, rp_id)
    points = svc.list_restore_points()
    assert len(points) == 1
    assert points[0].step_count == 2
    assert points[0].label == "Test"
    assert points[0].module == "Tweaks"


def test_list_restore_points_newest_first(svc):
    svc.create_restore_point("First", "Tweaks")
    svc.create_restore_point("Second", "Cleanup")
    points = svc.list_restore_points()
    assert points[0].label == "Second"
    assert points[1].label == "First"


def test_restore_result_dataclass():
    r = RestoreResult(success=True, partial=False, failed_steps=[], errors=[])
    assert r.success is True
    assert r.partial is False


def test_restore_point_info_dataclass():
    info = RestorePointInfo(id="abc", label="x", created_at="2026-01-01",
                            module="Tweaks", status="active", step_count=3)
    assert info.step_count == 3


def test_command_step_revert_is_noop(svc):
    """command steps are non-revertible but return True (not an error)."""
    import sqlite3
    rp_id = svc.create_restore_point("cmd", "Tweaks")
    steps = [StepRecord("command", "sfc /scannow", None, None)]
    svc.record_steps("fix1", steps, rp_id)
    conn = sqlite3.connect(svc._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id FROM tweak_steps LIMIT 1").fetchone()
    conn.close()
    result = svc.revert_step(row["id"])
    assert result is True


def test_restore_point_all_succeed(svc):
    """restore_point with only command steps → success=True (command steps are always OK)."""
    rp_id = svc.create_restore_point("cmds", "Tweaks")
    svc.record_steps("t1", [StepRecord("command", "echo hi", None, None)], rp_id)
    result = svc.restore_point(rp_id)
    assert result.success is True
    assert result.partial is False


def _first_step_id(svc):
    conn = sqlite3.connect(svc._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id FROM tweak_steps LIMIT 1").fetchone()
    conn.close()
    return row["id"]


def test_revert_step_registry_writes_before_value_directly(svc):
    """Registry revert must write the recorded before_value straight back via SetValueEx —
    NOT shell out to `reg import` a whole-key .reg export (see revert_step's docstring
    comment: that path silently no-ops on keys that didn't exist pre-tweak, and only
    unwinds the *last* touch when one key is hit by two tweaks in the same session)."""
    rp_id = svc.create_restore_point("reg test", "Tweaks")
    svc.record_steps(
        "t1",
        [StepRecord("registry", r"HKCU\Software\Test", 0, 1,
                    value_name="Val", reg_kind=winreg.REG_DWORD)],
        rp_id,
    )
    step_id = _first_step_id(svc)

    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.CreateKeyEx", return_value=mock_key) as mock_create, \
         patch("winreg.SetValueEx") as mock_set, \
         patch("subprocess.run") as mock_run, \
         patch.object(BackupService, "_verify_registry_revert", return_value=True):
        # The write itself is mocked (no real registry change happens), so
        # read-back verification is mocked too here — it has its own
        # dedicated tests below (test_revert_step_registry_verifies_the_write_*).
        result = svc.revert_step(step_id)

    assert result is True
    mock_create.assert_called_once_with(winreg.HKEY_CURRENT_USER, "Software\\Test",
                                        access=winreg.KEY_SET_VALUE)
    mock_set.assert_called_once_with(mock_key, "Val", 0, winreg.REG_DWORD, 0)
    mock_run.assert_not_called()  # no `reg import` subprocess


def test_revert_step_registry_deletes_when_no_prior_value(svc):
    """If before_value is None, the tweak created the value from nothing — revert must
    delete it, not write some other placeholder value."""
    rp_id = svc.create_restore_point("reg test 2", "Tweaks")
    svc.record_steps(
        "t1",
        [StepRecord("registry", r"HKCU\Software\Test", None, 1,
                    value_name="NewVal", reg_kind=winreg.REG_DWORD)],
        rp_id,
    )
    step_id = _first_step_id(svc)

    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.OpenKey", return_value=mock_key), \
         patch("winreg.DeleteValue") as mock_delete, \
         patch.object(BackupService, "_verify_registry_revert", return_value=True):
        result = svc.revert_step(step_id)

    assert result is True
    mock_delete.assert_called_once_with(mock_key, "NewVal")


def test_revert_step_registry_missing_key_on_delete_is_not_a_failure(svc):
    """Reverting a delete when the key is already gone shouldn't count as an error."""
    rp_id = svc.create_restore_point("reg test 3", "Tweaks")
    svc.record_steps(
        "t1",
        [StepRecord("registry", r"HKCU\Software\Gone", None, 1, value_name="V")],
        rp_id,
    )
    step_id = _first_step_id(svc)
    with patch("winreg.OpenKey", side_effect=FileNotFoundError):
        result = svc.revert_step(step_id)
    assert result is True


def test_revert_tweak_reverts_latest_applied_steps(svc):
    """revert_tweak() targets one tweak's steps directly, without needing its
    restore_point_id — powers the per-row Disable button in the Tweaks tab."""
    rp_id = svc.create_restore_point("Single tweak: X", "Tweaks")
    svc.record_steps(
        "tweak_a",
        [StepRecord("registry", r"HKCU\Software\Test", 0, 1,
                    value_name="Val", reg_kind=winreg.REG_DWORD)],
        rp_id,
    )
    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.CreateKeyEx", return_value=mock_key), patch("winreg.SetValueEx"), \
         patch.object(BackupService, "_verify_registry_revert", return_value=True):
        result = svc.revert_tweak("tweak_a")
    assert result.success is True
    assert result.partial is False


def test_revert_tweak_no_applied_steps_is_a_failure(svc):
    """Disabling a tweak that was never applied (or already reverted) is an
    explicit failure, not a silent no-op."""
    result = svc.revert_tweak("never_applied")
    assert result.success is False
    assert result.errors


def test_revert_tweak_only_touches_most_recent_session(svc):
    """A tweak applied twice (two separate sessions, never reverted in between) —
    revert_tweak() should only unwind the latest apply, leaving the older
    session's steps untouched."""
    rp_old = svc.create_restore_point("Single tweak: X (first)", "Tweaks")
    svc.record_steps(
        "tweak_b",
        [StepRecord("registry", r"HKCU\Software\Test", 0, 1, value_name="Val")],
        rp_old,
    )
    rp_new = svc.create_restore_point("Single tweak: X (second)", "Tweaks")
    svc.record_steps(
        "tweak_b",
        [StepRecord("registry", r"HKCU\Software\Test", 1, 2, value_name="Val")],
        rp_new,
    )

    mock_key = MagicMock()
    mock_key.__enter__ = lambda s: s
    mock_key.__exit__ = MagicMock(return_value=False)
    with patch("winreg.CreateKeyEx", return_value=mock_key), patch("winreg.SetValueEx"), \
         patch.object(BackupService, "_verify_registry_revert", return_value=True):
        result = svc.revert_tweak("tweak_b")
    assert result.success is True

    conn = sqlite3.connect(svc._db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT restore_point_id, reverted_at FROM tweak_steps WHERE tweak_id='tweak_b'"
        " ORDER BY rowid"
    ).fetchall()
    conn.close()
    assert rows[0]["restore_point_id"] == rp_old and rows[0]["reverted_at"] is None
    assert rows[1]["restore_point_id"] == rp_new and rows[1]["reverted_at"] is not None


def test_record_steps_hex_encodes_binary_before_value(svc):
    """REG_BINARY before/after values come back from winreg as bytes, which json.dumps
    can't serialize — record_steps must hex-encode them instead of crashing."""
    rp_id = svc.create_restore_point("bin test", "Tweaks")
    steps = [StepRecord("registry", r"HKLM\SOFTWARE\Test", b"\x01\x02", b"\x03\x04",
                        value_name="Blob", reg_kind=winreg.REG_BINARY)]
    svc.record_steps("t1", steps, rp_id)  # must not raise
    points = svc.list_restore_points()
    assert points[0].step_count == 1


# --- a backup that did not happen must not look like one --------------------
#
# Seen in tools/_roundtrip_elevated.log, from the elevated LLMNR write:
#
#     reg export failed (rc=1) for
#     HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient
#
# ...and the apply carried straight on. `backup_registry_key` logged a warning
# and returned None, so nothing upstream could tell these two apart:
#
#   * the key does not exist yet -- nothing to export, and the way back is
#     "delete the value again". Perfectly fine.
#   * the export was REFUSED -- there is no way back at all, and we are about
#     to change the machine anyway.
#
# Measured here: `reg export` answers rc=1 with empty stdout for BOTH a
# missing key and HKLM\SECURITY (denied), so the return code cannot tell them
# apart either. Ask the registry whether the key is there.

def test_backing_up_a_key_that_does_not_exist_is_not_a_failure(svc):
    """Nothing to export, and that is a complete answer: the way back from
    creating a value is deleting it again."""
    rp_id = svc.create_restore_point("absent key", "Tweaks")
    outcome = svc.backup_registry_key(
        r"HKLM\SOFTWARE\NoSuchKeyAnywhere\Really", rp_id)
    assert outcome.ok is True
    assert outcome.exported is False
    assert "does not exist" in outcome.reason.lower()


def test_a_refused_export_is_reported_as_a_failure(svc):
    """The key IS there and the export still failed -- so there is no way back
    and it has to say so, not log a warning nobody reads."""
    rp_id = svc.create_restore_point("denied key", "Tweaks")
    with patch("core.backup_service.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout=b"", stderr=b"ERROR: Access is denied.")
        outcome = svc.backup_registry_key(
            r"HKCU\Software\Microsoft\Windows\CurrentVersion", rp_id)
    assert outcome.ok is False
    assert outcome.exported is False
    assert "access is denied" in outcome.reason.lower()


@pytest.mark.real_machine
@pytest.mark.needs_admin
def test_a_real_export_reports_the_file_it_wrote(svc):
    """A key that exists and can be read: the .reg lands, and the outcome says
    so. This is the only case that leaves a way back on disk."""
    import os
    rp_id = svc.create_restore_point("real key", "Tweaks")
    outcome = svc.backup_registry_key(
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        rp_id)
    assert outcome.ok is True
    assert outcome.exported is True
    assert os.path.exists(outcome.path), "it claimed an export with no file"
    assert os.path.getsize(outcome.path) > 0


def test_a_backup_never_raises_at_its_callers(svc):
    """Every existing caller ignores the return value, so the old behaviour --
    never raising, whatever happens -- has to hold."""
    rp_id = svc.create_restore_point("ignored", "Tweaks")
    svc.backup_registry_key(r"HKLM\SOFTWARE\NoSuchKeyAnywhere", rp_id)
    svc.backup_registry_key(r"HKLM\SECURITY", rp_id)


# --- a revert puts the KEY back too, not just the value ---------------------
#
# Measured after the elevated LLMNR round-trip:
# HKLM\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient did not exist before
# it (that is why the `reg export` failed) and exists now with 0 values and 0
# subkeys. The apply created the key, the revert removed only the value, and
# the run reported "back to exactly what it was".
#
# Inferring this at revert time is NOT safe: a key's mere existence can be the
# whole point (see gpresult/pol_parser -- this machine carries a key-only
# policy record). So whether the apply created the key is recorded when the
# apply knows it, and only a key we created, left empty, is removed.

_TEST_ROOT = r"Software\WinClientToolTest_RevertKey"


@pytest.fixture
def reg_sandbox():
    """A real HKCU subtree, removed afterwards whatever the test did."""
    import winreg

    def _delete_tree(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                subs = [winreg.EnumKey(k, i)
                        for i in range(winreg.QueryInfoKey(k)[0])]
        except FileNotFoundError:
            return
        for sub in subs:
            _delete_tree(path + "\\" + sub)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)

    _delete_tree(_TEST_ROOT)
    yield _TEST_ROOT
    _delete_tree(_TEST_ROOT)


def _key_exists(path) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path):
            return True
    except FileNotFoundError:
        return False


def _record_registry_step(svc, target, *, key_created, before=None):
    rp_id = svc.create_restore_point("revert key", "Tweaks")
    svc.record_steps(
        "t1",
        [StepRecord("registry", target, before, 1, value_name="Val",
                    reg_kind=winreg.REG_DWORD, key_created=key_created)],
        rp_id,
    )
    return _first_step_id(svc)


def test_a_key_the_apply_created_is_removed_by_the_revert(svc, reg_sandbox):
    """The whole finding: the value goes back to absent AND the key does."""
    path = reg_sandbox + r"\Created"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 1)
    step_id = _record_registry_step(svc, "HKCU\\" + path, key_created=True)

    assert svc.revert_step(step_id) is True
    assert not _key_exists(path), "the key the apply created was left behind"


def test_a_key_that_was_already_there_survives_the_revert(svc, reg_sandbox):
    """Only a key we created is ours to remove. One that predates the tweak
    stays, even once it is empty -- its existence was not ours to undo."""
    path = reg_sandbox + r"\Preexisting"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 1)
    step_id = _record_registry_step(svc, "HKCU\\" + path, key_created=False)

    assert svc.revert_step(step_id) is True
    assert _key_exists(path), "it deleted a key that was not ours"


def test_a_key_still_holding_another_value_is_never_deleted(svc, reg_sandbox):
    """Something else wrote into the key after we made it. Removing it now
    would take that with it."""
    path = reg_sandbox + r"\Shared"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "SomebodyElse", 0, winreg.REG_SZ, "keep me")
    step_id = _record_registry_step(svc, "HKCU\\" + path, key_created=True)

    assert svc.revert_step(step_id) is True
    assert _key_exists(path)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        assert winreg.QueryValueEx(k, "SomebodyElse")[0] == "keep me"


def test_a_key_holding_a_subkey_is_never_deleted(svc, reg_sandbox):
    """An empty key with children is not empty."""
    path = reg_sandbox + r"\WithChild"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 1)
    winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path + r"\Child").Close()
    step_id = _record_registry_step(svc, "HKCU\\" + path, key_created=True)

    assert svc.revert_step(step_id) is True
    assert _key_exists(path)
    assert _key_exists(path + r"\Child")


def test_two_values_in_one_new_key_still_leave_no_key_behind(svc, reg_sandbox):
    """Found on the real machine, by checking after the fix rather than
    trusting it. "Disable Delivery Optimization" writes DODownloadMode and
    DOPerMachineMode into the same policy key:

        step 1  key absent      -> key_created=True
        step 2  key now exists  -> key_created=False

    Reverted in application order, step 1 finds the key still holding step
    2's value and cannot remove it, and step 2 has no mandate to. The key
    survived. Undo runs newest-first, so the last writer clears the way for
    the one that created the key.
    """
    path = reg_sandbox + r"\TwoValues"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "First", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "Second", 0, winreg.REG_DWORD, 1)

    rp_id = svc.create_restore_point("two values", "Tweaks")
    svc.record_steps(
        "t1",
        [StepRecord("registry", "HKCU\\" + path, None, 1, value_name="First",
                    reg_kind=winreg.REG_DWORD, key_created=True),
         StepRecord("registry", "HKCU\\" + path, None, 1, value_name="Second",
                    reg_kind=winreg.REG_DWORD, key_created=False)],
        rp_id,
    )

    outcome = svc.restore_point(rp_id)

    assert outcome.success, outcome.errors
    assert not _key_exists(path), (
        "the key the first step created outlived the revert")


def test_steps_are_reverted_newest_first(svc, reg_sandbox):
    """Undo is LIFO. Two steps writing the same value must land back on the
    ORIGINAL, which only happens if the later one is undone first."""
    path = reg_sandbox + r"\Ordering"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 99)

    rp_id = svc.create_restore_point("ordering", "Tweaks")
    svc.record_steps(
        "t1",
        [StepRecord("registry", "HKCU\\" + path, 1, 2, value_name="Val",
                    reg_kind=winreg.REG_DWORD),
         StepRecord("registry", "HKCU\\" + path, 2, 3, value_name="Val",
                    reg_kind=winreg.REG_DWORD)],
        rp_id,
    )

    svc.restore_point(rp_id)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        assert winreg.QueryValueEx(k, "Val")[0] == 1, (
            "reverted oldest-first, so the machine kept an intermediate value")


def test_restoring_a_prior_value_never_removes_the_key(svc, reg_sandbox):
    """key_created only ever matters when the revert DELETES the value. With a
    prior value to write back, the key must obviously stay."""
    path = reg_sandbox + r"\HadAValue"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 1)
    step_id = _record_registry_step(svc, "HKCU\\" + path,
                                    key_created=True, before=7)

    assert svc.revert_step(step_id) is True
    assert _key_exists(path)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        assert winreg.QueryValueEx(k, "Val")[0] == 7


# --- a revert that writes without raising is not proof it landed ------------
#
# TweakEngine.detect() and Security Dashboard's "verified by reading the
# control back" both exist because a Windows write API not raising is not
# evidence the machine actually moved. `revert_step` had never applied that
# same rule to its OWN writes -- it reported "reverted" the instant
# SetValueEx/DeleteValue returned, never reading the value back. The bug
# class this closes is exactly the one recorded above this comment block in
# this same file: the elevated LLMNR round-trip that reported "back to
# exactly what it was" while leaving an empty key behind. That one was
# caught by a person manually re-checking the registry after the fact; a
# read-back here would have caught it the moment it happened, and guards
# against any future regression of the same shape (wrong hive/sub, a
# before/after swap, a write silently landing somewhere the read doesn't
# agree with) without needing another manual round-trip session to notice.

def test_verify_registry_revert_matches_written_value(reg_sandbox):
    path = reg_sandbox + r"\VerifyMatch"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 5)
    result = BackupService._verify_registry_revert(
        winreg.HKEY_CURRENT_USER, path, "Val", 5)
    assert result is True


def test_verify_registry_revert_flags_a_real_mismatch(reg_sandbox):
    """The registry really does hold something other than what we expected —
    a genuine failure, not a false alarm."""
    path = reg_sandbox + r"\VerifyMismatch"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 999)
    result = BackupService._verify_registry_revert(
        winreg.HKEY_CURRENT_USER, path, "Val", 5)
    assert result is False


def test_verify_registry_revert_absent_matches_expected_none(reg_sandbox):
    """Nothing there at all, and we expected absence — a clean match, the
    common case for a step that only ever created a value."""
    path = reg_sandbox + r"\VerifyAbsent"
    result = BackupService._verify_registry_revert(
        winreg.HKEY_CURRENT_USER, path, "Val", None)
    assert result is True


def test_verify_registry_revert_flags_a_value_still_present_when_expected_gone(reg_sandbox):
    path = reg_sandbox + r"\VerifyStillThere"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 1)
    result = BackupService._verify_registry_revert(
        winreg.HKEY_CURRENT_USER, path, "Val", None)
    assert result is False


def test_verify_registry_revert_access_denied_is_unknown_not_a_failure():
    """A refused read-back is never collapsed into pass or fail — the same
    distinction `_registry_key_exists` and `TweakEngine.detect()` already
    draw for every other registry read in this app."""
    denied = OSError(5, "Access is denied")
    denied.winerror = 5  # winreg's own OSErrors carry this; a bare errno does not
    with patch("winreg.OpenKey", side_effect=denied):
        result = BackupService._verify_registry_revert(
            winreg.HKEY_LOCAL_MACHINE, "SECURITY", "Val", 1)
    assert result is None


def test_revert_step_treats_a_refused_verification_as_success_not_failure(svc, reg_sandbox):
    """The write itself did not raise; the read-back to confirm it was
    refused. That is unknown, not a mismatch, so the revert still counts as
    done -- matching how `detect()` never turns a refusal into a false
    negative on the apply side."""
    path = reg_sandbox + r"\VerifyRefused"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 1)
    step_id = _record_registry_step(svc, "HKCU\\" + path, key_created=False, before=7)

    with patch.object(BackupService, "_verify_registry_revert", return_value=None):
        result = svc.revert_step(step_id)

    assert result is True
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        assert winreg.QueryValueEx(k, "Val")[0] == 7  # the real write still happened


def test_revert_step_fails_when_the_actual_write_does_not_match_what_was_recorded(svc, reg_sandbox):
    """If the write path ever regresses (wrong hive/sub, a before/after swap,
    any future refactor bug) so the registry ends up NOT holding the
    recorded before_value, revert_step must say so rather than reporting
    success just because SetValueEx did not raise. Simulated by making the
    real SetValueEx write the wrong value while everything else — the
    sandbox, the read-back — runs for real."""
    path = reg_sandbox + r"\WriteRegresses"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path,
                            access=winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Val", 0, winreg.REG_DWORD, 999)  # the "applied" value
    step_id = _record_registry_step(svc, "HKCU\\" + path, key_created=False, before=7)

    real_set_value_ex = winreg.SetValueEx

    def _wrong_write(key, name, reserved, kind, _data):
        real_set_value_ex(key, name, reserved, kind, 12345)  # NOT the recorded before_value

    with patch("winreg.SetValueEx", side_effect=_wrong_write):
        result = svc.revert_step(step_id)

    assert result is False
    row = svc._conn.execute(
        "SELECT revert_error, reverted_at FROM tweak_steps WHERE id=?", (step_id,)
    ).fetchone()
    assert row["reverted_at"] is None, "a failed revert must not be marked reverted"
    assert "read" in row["revert_error"].lower()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        assert winreg.QueryValueEx(k, "Val")[0] == 12345  # proves this was a real write, not a mock
