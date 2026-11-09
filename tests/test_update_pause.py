"""Windows Update pause/resume through the UX settings values."""
from datetime import datetime, timezone

from modules.updates import update_pause


class FakeReg:
    HKEY_LOCAL_MACHINE = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.exists = False

    def OpenKey(self, root, path, reserved, access):
        if not self.exists:
            raise FileNotFoundError(path)
        return "key"

    def CreateKeyEx(self, root, path, reserved, access):
        self.exists = True
        return "key"

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, key):
        pass


def test_pause_values_are_utc_and_clamped_to_windows_limit():
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    values = update_pause.pause_values(7, now)
    assert values["PauseUpdatesExpiryTime"] == "2026-11-05T12:00:00Z"
    assert values["PauseFeatureUpdatesStartTime"] == "2026-10-29T12:00:00Z"
    assert update_pause.pause_values(999, now)["PauseUpdatesExpiryTime"] == "2026-12-03T12:00:00Z"


def test_read_reports_not_paused_before_any_write():
    assert update_pause.read_pause(FakeReg()) == (None, "")


def test_pause_writes_all_five_values_and_reads_back():
    reg = FakeReg()
    ok, message = update_pause.pause_updates(
        7, datetime(2026, 10, 29, tzinfo=timezone.utc), reg)
    assert ok and "paused until" in message
    assert set(reg.values) == set(update_pause.VALUE_NAMES)
    expiry, reason = update_pause.read_pause(reg)
    assert expiry == "2026-11-05T00:00:00Z" and reason == ""


def test_resume_removes_every_pause_value():
    reg = FakeReg()
    update_pause.pause_updates(7, datetime(2026, 10, 29, tzinfo=timezone.utc), reg)
    ok, message = update_pause.resume_updates(reg)
    assert ok and "Removed 5" in message
    assert reg.values == {}


def test_resume_without_a_pause_is_a_noop():
    ok, message = update_pause.resume_updates(FakeReg())
    assert ok and "No Windows Update pause" in message


def test_pause_uses_windows_settings_registry_path():
    assert update_pause.UX_KEY == r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"


def test_pause_activity_requires_a_valid_future_timestamp():
    for expiry in (None, "", "bad", "2000-01-01T00:00:00Z",
                   "2099-01-01T00:00:00", 42):
        assert not update_pause.is_pause_active(expiry)
    assert update_pause.is_pause_active("2099-01-01T00:00:00Z")
    assert update_pause.is_pause_active("2099-01-01T00:00:00+02:00")
