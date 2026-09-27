"""Disk-related System log events (Get-WinEvent). No Qt."""
import subprocess

from modules.disk_health import disk_events as de

_SAMPLE = ('[{"Id":154,"Level":"Error","Time":"2026-09-20 02:23:12",'
          '"Message":"The IO operation at logical block address 0x0 for Disk 4 '
          '(PDO name: \\\\Device\\\\00000071) failed due to a hardware error."},'
          '{"Id":158,"Level":"Warning","Time":"2026-09-27 20:28:43",'
          '"Message":"Disk 5 has the same disk identifiers as one or more disks '
          'connected to the system."}]')


def test_read_disk_events_returns_none_on_refusal(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert de.read_disk_events() is None


def test_read_disk_events_returns_none_when_the_command_cannot_run(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("powershell not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert de.read_disk_events() is None


def test_read_disk_events_parses_the_real_captured_sample(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, _SAMPLE, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    events = de.read_disk_events()

    assert events is not None and len(events) == 2
    hw_error = next(e for e in events if e.event_id == 154)
    assert hw_error.disk == "4" and hw_error.is_error
    assert "hardware error" in hw_error.meaning

    dup = next(e for e in events if e.event_id == 158)
    assert dup.disk == "5" and not dup.is_error


def test_empty_result_is_a_healthy_empty_list_not_none(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert de.read_disk_events() == []


def test_group_events_collapses_repeats_and_keeps_the_latest_time():
    events = [
        de.DiskEvent(158, "Warning", "2026-09-20 08:00:00", "5", "m", "dup"),
        de.DiskEvent(158, "Warning", "2026-09-27 20:28:43", "5", "m", "dup"),
        de.DiskEvent(154, "Error", "2026-09-20 02:23:12", "4", "m", "hw error"),
    ]
    groups = de.group_events(events)

    assert len(groups) == 2
    # Errors sort first, then by descending count.
    assert groups[0].event_id == 154 and groups[0].is_error
    dup_group = next(g for g in groups if g.event_id == 158)
    assert dup_group.count == 2 and dup_group.latest == "2026-09-27 20:28:43"


def test_real_disk_events_are_readable_on_this_machine():
    events = de.read_disk_events()
    assert events is not None
    # Confirmed live 2026-09-27: this machine has real hardware-error (154)
    # and duplicate-disk-id (158) events in its System log.
    ids = {e.event_id for e in events}
    assert ids  # at least one known disk event exists in the last 14 days here
