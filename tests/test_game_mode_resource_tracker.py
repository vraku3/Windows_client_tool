"""Tests for core.resource_tracker and its integration with Game Mode."""
from __future__ import annotations

import json
from unittest.mock import MagicMock


from core.resource_tracker import ResourceTracker, get_resource_tracker
from modules.game_mode.engine import session
from modules.game_mode.engine.journal import Journal


def test_resource_tracker_basic_registration_and_cleanup(tmp_path):
    tracker = ResourceTracker(base_dir=str(tmp_path))
    assert tracker.count == 0

    order = []

    # 1. Custom callback
    tracker.track_custom(lambda: order.append("custom1"), "custom1")
    # 2. Worker
    mock_worker = MagicMock()
    tracker.track_worker(mock_worker)
    # 3. Timer
    mock_timer = MagicMock()
    tracker.track_timer(mock_timer, "timer1")
    # 4. Another custom
    tracker.track_custom(lambda: order.append("custom2"), "custom2")

    assert tracker.count == 4
    snapshot = tracker.get_snapshot()
    assert len(snapshot) == 4
    assert snapshot[0]["type"] == "custom"
    assert snapshot[1]["type"] == "worker"
    assert snapshot[2]["type"] == "timer"

    # Cleanup with wave_id=1
    tracker.cleanup(wave_id=1)

    assert tracker.count == 0
    # Cleaned in reverse order: custom2 before custom1
    assert order == ["custom2", "custom1"]
    mock_timer.restore.assert_called_once()
    mock_worker.cancel.assert_called_once()

    # Verify state snapshot JSON written
    snapshot_file = tmp_path / "state_snapshot_1.json"
    assert snapshot_file.exists()
    data = json.loads(snapshot_file.read_text(encoding="utf-8"))
    assert len(data) == 4


def test_resource_tracker_isolated_failures(tmp_path):
    tracker = ResourceTracker(base_dir=str(tmp_path))
    order = []

    def failing_action():
        raise RuntimeError("boom")

    tracker.track_custom(lambda: order.append("first"), "first")
    tracker.track_custom(failing_action, "failing")
    tracker.track_custom(lambda: order.append("last"), "last")

    # Cleanup should run all actions despite failure in failing_action
    tracker.cleanup()
    assert order == ["last", "first"]
    assert tracker.count == 0


def test_resource_tracker_timer_callable_and_cancel(tmp_path):
    tracker = ResourceTracker(base_dir=str(tmp_path))
    called = []
    tracker.track_timer(lambda: called.append("lambda_timer"))

    mock_cancel_timer = MagicMock(spec=["cancel"])
    tracker.track_timer(mock_cancel_timer)

    tracker.cleanup()
    assert called == ["lambda_timer"]
    mock_cancel_timer.cancel.assert_called_once()


def test_resource_tracker_registry_and_file(tmp_path):
    tracker = ResourceTracker(base_dir=str(tmp_path))
    mock_reg = MagicMock()
    mock_file = MagicMock()

    tracker.track_registry_key(mock_reg)
    tracker.track_file(mock_file)

    tracker.cleanup()
    mock_file.close.assert_called_once()
    mock_reg.Close.assert_called_once()


def test_resource_tracker_global_instance():
    t1 = get_resource_tracker()
    t2 = get_resource_tracker()
    assert t1 is t2


def test_session_turn_on_and_off_with_tracker(tmp_path):
    class FakeSys:
        def __init__(self):
            self.timer_cancelled = False
        def is_admin(self): return False
        def processes(self): return []
        def fullscreen_pids(self): return set()
        def details(self, _pid): return ("", "", False)
        def plans(self): return {}
        def active_plan(self): return None
        def cpu_name(self): return "AMD Ryzen"
        def service(self, _s): return None
        def set_timer_resolution(self, _p): return None
        def cancel_timer_resolution(self, _p):
            self.timer_cancelled = True
            return None
        def reg_get(self, *a): return (False, None, 0)
        def reg_set(self, *a): return None

    sys_ = FakeSys()
    journal = Journal(str(tmp_path))
    tracker = ResourceTracker(base_dir=str(tmp_path))

    choices = session.Choices(
        high_resolution_timer=True,
        suppress_game_dvr=False,
        low_latency_network=False,
        max_mmcss=False,
        prefer_discrete_gpu=False,
        lower=False,
        lower_apps_io_priority=False,
        trim_background_memory=False,
        maintenance=False,
        tasks=False,
        plan=False,
        gamebar=False,
        vcache=False,
        services=set(),
        close=set(),
    )

    # Turn on
    session.turn_on(sys_, choices, journal, lambda *a: None, own_pid=1, session=1,
                    resource_tracker=tracker)
    assert tracker.count >= 1  # high_resolution_timer registered

    # Turn off with tracker
    session.turn_off(sys_, journal, lambda *a: None, settle=0.1, resource_tracker=tracker)
    assert tracker.count == 0
    assert sys_.timer_cancelled is True


def test_resource_tracker_untrack_worker(tmp_path):
    tracker = ResourceTracker(base_dir=str(tmp_path))
    worker = MagicMock()
    worker.id = "worker_123"

    tracker.track_worker(worker)
    assert tracker.count == 1
    assert len(tracker.get_snapshot()) == 1

    tracker.untrack_worker(worker)
    assert tracker.count == 0
    assert len(tracker.get_snapshot()) == 0


def test_resource_tracker_invalid_handle_ignored(tmp_path):
    tracker = ResourceTracker(base_dir=str(tmp_path))
    # Handles 0, -1, 0xFFFFFFFF should not crash or call CloseHandle
    tracker.track_handle(0)
    tracker.track_handle(-1)
    tracker.track_handle(0xFFFFFFFF)
    assert tracker.count == 3
    tracker.cleanup()
    assert tracker.count == 0
