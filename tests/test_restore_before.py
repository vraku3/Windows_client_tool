"""Optional System Restore point before app/Store update batches."""
from types import SimpleNamespace

import pytest

from modules.updates.restore_before import ensure_restore_point


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _app(enabled):
    return SimpleNamespace(config=Config(
        **{"updates.restore_point_before_app_updates": enabled}))


def test_off_is_a_noop_and_never_calls_windows():
    calls = []
    assert ensure_restore_point(_app(False), "app updates", lambda _l: None,
                                create=lambda label: calls.append(label) or (True, "")) is False
    assert calls == []


def test_on_creates_one_restore_point_and_logs_it():
    calls, lines = [], []
    result = ensure_restore_point(
        _app(True), "app updates", lines.append,
        create=lambda label: calls.append(label) or (True, ""))
    assert result is True
    assert calls == ["Windows Client Tool - app updates"]
    assert any("Creating" in line for line in lines)
    assert any("Restore point created" in line for line in lines)


def test_a_refused_restore_point_stops_the_batch_with_a_reason():
    lines = []
    with pytest.raises(RuntimeError) as exc:
        ensure_restore_point(_app(True), "Store app updates", lines.append,
                             create=lambda _label: (False, "refused"))
    assert "Nothing was installed" in str(exc.value)
    assert "Store app updates" in str(exc.value)
    assert not any("created." in line for line in lines)
