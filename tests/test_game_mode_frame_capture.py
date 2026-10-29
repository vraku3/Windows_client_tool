"""Optional PresentMon frame capture helpers."""
from types import SimpleNamespace

import pytest

from modules.game_mode.engine import frame_capture


def test_summarize_reports_average_and_percentile_lows():
    rows = ["Application,ProcessID,MsBetweenPresents"]
    rows += ["game,1,10.0"] * 99
    rows += ["game,1,50.0"]
    result = frame_capture.summarize("\n".join(rows))
    assert result["frames"] == 100
    assert result["avg_fps"] == pytest.approx(1000 / 10.4, rel=0.01)
    assert result["one_percent_low_fps"] == pytest.approx(20.0, rel=0.01)
    assert result["max_frame_ms"] == 50.0


def test_summarize_rejects_missing_or_empty_output():
    with pytest.raises(ValueError):
        frame_capture.summarize("")
    with pytest.raises(ValueError):
        frame_capture.summarize("A,B\n1,2")


def test_find_presentmon_accepts_an_explicit_path(tmp_path):
    exe = tmp_path / "PresentMon.exe"
    exe.write_bytes(b"fake")
    assert frame_capture.find_presentmon([str(exe)]) == str(exe)


def test_capture_builds_the_presentmon_command_and_requires_output(tmp_path):
    output = tmp_path / "out.csv"

    def runner(cmd, **kwargs):
        assert "--process_name" in cmd and "game.exe" in cmd
        output.write_text("Application,MsBetweenPresents\ngame,16.0\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    path = frame_capture.capture("PresentMon.exe", "game.exe", 5,
                                 str(output), runner=runner)
    assert path == str(output)
