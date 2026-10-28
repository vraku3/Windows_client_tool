import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from core import timeline


def test_persistence_filters_search_and_corruption(tmp_path):
    timeline.record(tmp_path, "cleanup", "clean_complete", "Freed cache", {"freed_bytes": 42})
    with (tmp_path / "timeline.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('broken\n[]\n{"ts":"bad"}\n')
    timeline.record(tmp_path, "game_mode", "off", "Game Mode OFF", severity="warning")
    assert [e["source"] for e in timeline.load_timeline(tmp_path)] == ["game_mode", "cleanup"]
    assert timeline.load_timeline(tmp_path, source="cleanup")[0]["detail"] == {"freed_bytes": 42}
    assert len(timeline.search_timeline(tmp_path, "CACHE")) == 1
    assert timeline.load_timeline(tmp_path, since=datetime.now() + timedelta(days=1)) == []
    assert timeline.load_timeline(tmp_path, limit=0) == []


def test_concurrent_appends_and_retention(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: timeline.record(tmp_path, "system", "app_start", str(i)), range(80)))
    assert len(timeline.load_timeline(tmp_path, limit=100)) == 80
    path = tmp_path / "timeline.jsonl"
    line = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text((line + "\n") * 5499, encoding="utf-8")
    timeline.record(tmp_path, "system", "app_start", "Started")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5500
    timeline.record(tmp_path, "system", "app_stop", "Stopped")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5000
    assert timeline.load_timeline(tmp_path)[0]["event"] == "app_stop"


def test_failures_do_not_escape_and_payload_stays_small(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("blocked")
    timeline.record(blocked, "system", "app_start", "start")
    assert timeline.load_timeline(blocked) == []
    timeline.record(tmp_path, "system", "app_start", "x" * 10000, {"report": "x" * 50000})
    assert (tmp_path / "timeline.jsonl").stat().st_size < 12000


def test_reboot_since_stop_recorded_once_at_boot_time(tmp_path):
    stop = datetime.now() - timedelta(hours=3)
    boot = datetime.now() - timedelta(hours=2)
    event = dict(ts=stop.isoformat(), source="system", event="app_stop", summary="Stopped", detail={}, severity="info")
    (tmp_path / "timeline.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    timeline.detect_reboot(tmp_path, boot.timestamp())
    timeline.detect_reboot(tmp_path, boot.timestamp())
    events = timeline.load_timeline(tmp_path)
    assert len(events) == 2
    assert events[0]["event"] == "reboot_detected"
    assert datetime.fromisoformat(events[0]["ts"]).timestamp() == boot.timestamp()


def test_no_reboot_without_prior_stop_or_with_older_boot(tmp_path):
    timeline.detect_reboot(tmp_path, datetime.now().timestamp())
    assert timeline.load_timeline(tmp_path) == []
    timeline.record(tmp_path, "system", "app_stop", "Stopped")
    timeline.detect_reboot(tmp_path, (datetime.now() - timedelta(days=1)).timestamp())
    assert len(timeline.load_timeline(tmp_path)) == 1


def test_separate_process_writers_keep_every_event(tmp_path):
    script = "from core.timeline import record; import sys; [record(sys.argv[1], 'system', 'app_start', str(i)) for i in range(30)]"
    env = dict(os.environ, PYTHONPATH=os.path.abspath("src"))
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], env=env) for _ in range(3)]
    for process in processes:
        assert process.wait(timeout=20) == 0
    assert len(timeline.load_timeline(tmp_path, limit=200)) == 90


def test_append_after_interrupted_last_line(tmp_path):
    (tmp_path / "timeline.jsonl").write_text('{"partial":', encoding="utf-8")
    timeline.record(tmp_path, "cleanup", "clean_complete", "Completed")
    assert timeline.load_timeline(tmp_path)[0]["summary"] == "Completed"
