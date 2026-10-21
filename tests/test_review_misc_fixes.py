"""Focused regressions for cleanup accounting, worker lifetime and task reporting."""
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot, SnapshotRow
from modules.game_mode.engine import catalog, session, space, system


@pytest.mark.parametrize("terminal", ["result", "error", "cancelled"])
def test_recommendation_worker_is_held_until_finished(qapp, terminal):
    from modules.cleanup.components.recommendations_panel import RecommendationsPanel
    queue = []

    class Pool:
        def start(self, worker):
            queue.append(worker)

    panel = RecommendationsPanel(thread_pool=Pool())
    try:
        panel.refresh(ScanSnapshot())
        worker = queue.pop()
        signal = getattr(worker.signals, terminal)
        if terminal == "result":
            signal.emit([])
        elif terminal == "error":
            signal.emit("test failure")
        else:
            signal.emit()
        assert worker in panel._workers
        assert not panel._busy
        worker.signals.finished.emit()
        assert worker not in panel._workers
    finally:
        panel.cancel()
        panel.close()


@pytest.mark.parametrize("reverse", [False, True])
def test_scan_snapshot_drops_nested_paths_with_case_and_separator_boundaries(reverse):
    parent = SnapshotRow("logs", "Logs", "system", "system", "caution", "caution",
                         r"C:\Windows\Logs\CBS", 100, False)
    child = replace(parent, path="c:/WINDOWS/logs/cbs/CBS.log", size=40, is_dir=False)
    sibling = replace(parent, path=r"C:\Windows\Logs\CBS-old", size=20)
    rows = [parent, child, sibling]
    snapshot = ScanSnapshot(tuple(reversed(rows)) if reverse else tuple(rows))
    assert snapshot.total_size == 120
    assert {r.path for r in snapshot.unique_rows} == {parent.path, sibling.path}
    # A file row must never swallow a path below its name.
    snapshot = ScanSnapshot((replace(parent, is_dir=False), child))
    assert snapshot.total_size == 140


def test_game_cache_help_describes_only_named_leaves():
    from modules.cleanup.cleanup_scanner import knowledge
    raw = json.loads((Path(knowledge.__file__).parent / "definitions" / "knowledge" /
                      "handwritten.json").read_text(encoding="utf-8"))["game_caches"]
    assert raw["verdict"] == "caution"
    assert "DANGEROUS" not in raw.get("catalog_concern", "")
    assert "Cache" in raw["what"] and "GPUCache" in raw["what"]
    assert "stutter" in raw["risk"].lower()
    assert "close" in raw["before_cleaning"].lower()
    assert "steamapps" not in raw["what"].lower()


@pytest.mark.parametrize("states,expected", [([4, 4, 4, 4], "the current run is still running"),
                                           ([4, 3], None)])
def test_task_stop_reads_back_state_even_after_s_false(monkeypatch, states, expected):
    class Task:
        def __init__(self):
            self.states = iter(states)
            self.last = states[-1]
            self.reads = 0

        @property
        def State(self):
            self.reads += 1
            return next(self.states, self.last)

        def Stop(self, flags):
            return 1  # S_FALSE: success HRESULT alone is not proof it stopped.

    task = Task()
    native = system.WinSystem.__new__(system.WinSystem)
    monkeypatch.setattr(native, "_task", lambda _path: task)
    ticks = iter(range(100))
    monkeypatch.setattr(system.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(system.time, "sleep", lambda _seconds: None)
    assert native.stop_task(r"\Task") == expected
    assert task.reads >= 2


def test_task_stop_readback_exception_does_not_raise(monkeypatch):
    class Task:
        def Stop(self, flags):
            return None

        @property
        def State(self):
            raise PermissionError("state refused")

    native = system.WinSystem.__new__(system.WinSystem)
    monkeypatch.setattr(native, "_task", lambda _path: Task())
    assert "state refused" in native.stop_task(r"\Task")


def test_task_still_running_is_reported_without_losing_restore(tmp_path, monkeypatch):
    from modules.game_mode.engine.journal import Journal
    from test_game_mode_tasks import TaskSystem
    s = TaskSystem()
    path = r"\ReviewTask"
    s.task_state = {path: True}
    s.running_tasks = {path}
    s.stop_task = lambda _path: "the current run is still running"
    monkeypatch.setattr(catalog, "TASKS", (catalog.TaskSpec(path, "Review task", catalog.MAX),))
    journal = Journal(str(tmp_path))
    log = Mock()
    session._On(s, journal, log).tasks(True, session.preset(catalog.MAX, []))
    messages = "\n".join(call.args[1] for call in log.call_args_list)
    assert "paused for future triggers; the current run is still running" in messages
    assert "stopped a run in progress" not in messages
    assert s.task_state[path] is False
    assert journal.actions[0]["done"]


def test_one_way_space_help_distinguishes_diagnostics_from_rebuilt_caches():
    assert "removed for good" in space.ONE_WAY_NOTE.lower()
    assert "past crash" in space.ONE_WAY_NOTE.lower()
    assert "rebuild these files" not in space.ONE_WAY_NOTE.lower()
    for key in ("wer", "dumps", "logs"):
        assert "removed for good" in space.BY_ID[key].why.lower()
